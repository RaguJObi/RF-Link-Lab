"""Finite, reproducible SC16Q11 bursts and known-payload offline measurement.

This module opens neither RF hardware nor serial ports. A prepared burst is
software evidence of the bytes offered to a transmitter, not proof of RF.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from fractions import Fraction
import hashlib
import json
from numbers import Integral, Real
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from . import __version__
from .hardware import load_sc16q11
from .modem import bytes_to_bits, qpsk_demodulate
from .protocol import Telemetry, encode_telemetry
from .receiver import ReceiverResult, decode_iq, resample_iq
from .recordings import decode_recording
from .waveform import WAVEFORM_FORMAT, generate_waveform


SCHEMA = "rf-link-lab-tx-waveform-v1"
MAX_SAMPLES = 20_000_000
MAX_DURATION_S = 10.0
MAX_FRAMES = 2048


def _integer(name: str, value, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return int(value)


def _rates(sample_rate, symbol_rate) -> tuple[int, int]:
    fs = _integer("sample_rate", sample_rate, 1_000_000, 10_000_000)
    symbol_rate = _integer("symbol_rate", symbol_rate, 10_000, 250_000)
    if fs % symbol_rate or fs // symbol_rate < 4:
        raise ValueError("sample_rate / symbol_rate must be an integer >=4")
    return fs, symbol_rate


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member(folder: Path, name) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        raise ValueError("TX file references must be local basenames")
    path = folder / name
    if path.is_symlink() or path.resolve().parent != folder.resolve() or not path.is_file():
        raise ValueError("TX file reference is missing or escapes its bundle")
    return path


def _payload(messages: Iterable[Telemetry]) -> list[dict]:
    rows = []
    for message in messages:
        if len(rows) >= MAX_FRAMES:
            raise ValueError(f"at most {MAX_FRAMES} telemetry frames per finite burst")
        if not isinstance(message, Telemetry):
            raise ValueError("messages must contain Telemetry values")
        _integer("sequence", message.sequence, 0, 0xFFFFFFFF)
        _integer("uptime_ms", message.uptime_ms, 0, 0xFFFFFFFF)
        if not isinstance(message.button, (bool, np.bool_)):
            raise ValueError("Telemetry button must be boolean")
        raw = encode_telemetry(message)
        rows.append({"sequence": int(message.sequence), "uptime_ms": int(message.uptime_ms), "button": bool(message.button),
                     "frame_hex": raw.hex(), "frame_sha256": hashlib.sha256(raw).hexdigest()})
    if not rows:
        raise ValueError("at least one telemetry message is required")
    return rows


def _quantize(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Round once; explicitly reject overflow instead of clipping or wrapping."""
    if samples.ndim != 1 or not np.iscomplexobj(samples) or not np.all(np.isfinite(samples)):
        raise ValueError("TX I/Q must be a finite one-dimensional complex array")
    components = np.column_stack((samples.real, samples.imag))
    rounded = np.rint(components * 2048.0)
    if np.any(rounded < -2048) or np.any(rounded > 2047):
        raise ValueError("TX scaling overflows SC16Q11; reduce tx_scale")
    words = rounded.astype("<i2")
    actual = words[:, 0].astype(float) / 2048 + 1j * words[:, 1].astype(float) / 2048
    if not np.any(words):
        raise ValueError("tx_scale is too small: quantization erased the waveform")
    return words, actual


def prepare_tx_waveform(messages: Iterable[Telemetry], output_dir: Path, *,
                        sample_rate: int = 1_000_000, symbol_rate: int = 50_000,
                        tx_scale: float = 0.25, guard_samples: int | None = None) -> Path:
    """Create an exclusive bundle containing one finite, never-repeated burst.

    ``tx_scale`` is the maximum absolute I or Q component *before* rounding,
    following explicit peak normalization; it is not calibrated RF power.
    Default leading and trailing zero guards each last 10 ms. Both rates,
    resampling ratio, actual quantized samples and payload bytes are retained.
    """
    fs, symbols_s = _rates(sample_rate, symbol_rate)
    if (isinstance(tx_scale, bool) or not isinstance(tx_scale, Real)
            or not np.isfinite(tx_scale) or not 0 < tx_scale < 1):
        raise ValueError("tx_scale must be finite and greater than zero and less than one")
    guard = _integer("guard_samples", fs // 100 if guard_samples is None else guard_samples, 0, fs)
    rows = _payload(messages)
    values = [Telemetry(row["sequence"], row["uptime_ms"], row["button"]) for row in rows]
    base_rate = symbols_s * 4
    waveform = generate_waveform(values, sample_rate=base_rate, samples_per_symbol=4)
    ratio = Fraction(fs, base_rate)
    if max(ratio.numerator, ratio.denominator) > 4096:
        raise ValueError("choose a simpler rational sample-rate ratio")
    projected = (len(waveform.tx_iq) * ratio.numerator + ratio.denominator - 1) // ratio.denominator + 2 * guard
    if projected > MAX_SAMPLES or projected / fs > MAX_DURATION_S:
        raise ValueError("finite burst exceeds 20 million samples or 10 seconds")
    active = resample_poly(waveform.tx_iq, ratio.numerator, ratio.denominator)
    peak = float(max(np.max(np.abs(active.real)), np.max(np.abs(active.imag))))
    normalized_scale = float(tx_scale) / peak
    words, actual = _quantize(np.pad(active * normalized_scale, (guard, guard)))
    if len(actual) > MAX_SAMPLES or len(actual) / fs > MAX_DURATION_S:
        raise ValueError("finite burst exceeds 20 million samples or 10 seconds")
    metadata = {**waveform.metadata, "sample_rate": fs, "symbol_rate": symbols_s,
                "samples_per_symbol": fs // symbols_s,
                "provenance": "prepared_tx_waveform_no_rf", "source_provenance": "prepared_tx_waveform_no_rf",
                "expected_frames": len(rows), "carrier_tracking_enabled": False}
    # The EVM reference comes from the exact quantized, resampled TX words,
    # rather than an ideal waveform regenerated later by a receiver.
    adapted, adapted_meta = resample_iq(actual, metadata, base_rate)
    measured = decode_iq(adapted, adapted_meta, reference=waveform)
    if (measured.metrics["packets_ok"] != len(rows)
            or measured.metrics["bits_compared"] != len(waveform.reference_bits)
            or measured.metrics["ber"] != 0):
        raise ValueError("prepared waveform failed independent quantized-payload verification")
    reference_symbols = measured.constellation.copy()
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    tx_path = destination / "tx.sc16q11"
    reference_path = destination / "tx-reference.npz"
    with tx_path.open("xb") as handle:
        handle.write(words.tobytes(order="C"))
    with reference_path.open("xb") as handle:
        np.savez_compressed(handle, tx_iq=actual, reference_bits=waveform.reference_bits,
                            reference_symbols=reference_symbols)
    manifest = {
        "schema": SCHEMA, "version": __version__, "provenance": "prepared_tx_waveform_no_rf",
        "waveform_format": WAVEFORM_FORMAT, "format": "SC16Q11_LE", "normalization_divisor": 2048,
        "sample_rate": fs, "symbol_rate": symbols_s, "samples_per_symbol": fs // symbols_s,
        "samples": len(actual), "active_samples": len(active), "guard_samples": guard,
        "prefix_guard_samples": guard, "suffix_guard_samples": guard,
        "duration_s": len(actual) / fs, "frames_expected": len(rows), "payload": rows,
        "finite": True, "repeat_count": 1,
        "resampling": {"method": "scipy.signal.resample_poly", "source_sample_rate": base_rate,
                       "up": ratio.numerator, "down": ratio.denominator},
        "quantization": {"rounding": "numpy.rint; nearest integer, ties to even", "clipping": "rejected",
                         "tx_scale": float(tx_scale), "unscaled_component_peak": peak,
                         "normalization_multiplier": normalized_scale,
                         "quantized_component_peak": float(max(np.max(np.abs(actual.real)), np.max(np.abs(actual.imag)))),
                         "positive_rail_headroom_codes": int(2047 - np.max(words)),
                         "negative_rail_headroom_codes": int(np.min(words) + 2048)},
        "reference_note": "TX I/Q is the exact quantized stream. Payload symbols are its independently acquired normalized decisions; bits are the encoded expected payload.",
        "files": {"waveform": {"file": tx_path.name, "sha256": _sha(tx_path)},
                  "reference": {"file": reference_path.name, "sha256": _sha(reference_path)}},
    }
    path = destination / "tx.json"
    path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def load_tx_reference(path: Path) -> dict:
    """Verify a bundle, rates, payload and actual bytes before using references."""
    path = Path(path)
    if path.is_dir():
        path = path / "tx.json"
    if path.is_symlink():
        raise ValueError("TX manifest must not be a symbolic link")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("waveform_format") != WAVEFORM_FORMAT:
        raise ValueError("unsupported TX waveform manifest")
    if manifest.get("format") != "SC16Q11_LE" or manifest.get("normalization_divisor") != 2048:
        raise ValueError("unsupported TX quantization format")
    fs, symbols_s = _rates(manifest.get("sample_rate"), manifest.get("symbol_rate"))
    if manifest.get("samples_per_symbol") != fs // symbols_s:
        raise ValueError("TX sample and symbol rates disagree")
    total = _integer("samples", manifest.get("samples"), 1, MAX_SAMPLES)
    active = _integer("active_samples", manifest.get("active_samples"), 1, MAX_SAMPLES)
    guard = _integer("guard_samples", manifest.get("guard_samples"), 0, fs)
    if (manifest.get("prefix_guard_samples") != guard or manifest.get("suffix_guard_samples") != guard
            or active + 2 * guard != total or total / fs > MAX_DURATION_S
            or manifest.get("duration_s") != total / fs
            or manifest.get("finite") is not True or manifest.get("repeat_count") != 1):
        raise ValueError("TX guard, duration or finite-burst constraints disagree")
    files = manifest.get("files", {})
    paths = {}
    for key in ("waveform", "reference"):
        info = files.get(key, {})
        member = _member(path.parent, info.get("file"))
        if member.stat().st_size > MAX_SAMPLES * 32:
            raise ValueError("TX bundle member exceeds the bounded size limit")
        if _sha(member) != info.get("sha256"):
            raise ValueError(f"TX {key} hash mismatch")
        paths[key] = member
    actual = load_sc16q11(paths["waveform"]).astype(np.complex128)
    if len(actual) != total or np.any(actual[:guard]) or (guard and np.any(actual[-guard:])):
        raise ValueError("TX sample count or zero guards disagree with the manifest")
    payload = manifest.get("payload")
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_FRAMES or manifest.get("frames_expected") != len(payload):
        raise ValueError("invalid TX expected payload count")
    try:
        checked_rows = _payload(Telemetry(row["sequence"], row["uptime_ms"], row["button"]) for row in payload)
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid TX payload declaration") from exc
    if checked_rows != payload:
        raise ValueError("TX payload bytes or payload hashes disagree")
    expected_bits = bytes_to_bits(b"".join(bytes.fromhex(row["frame_hex"]) for row in payload))
    with np.load(paths["reference"], allow_pickle=False) as data:
        saved_iq = data["tx_iq"]
        bits = data["reference_bits"]
        symbols = data["reference_symbols"]
    if (saved_iq.shape != actual.shape or not np.iscomplexobj(saved_iq)
            or not np.array_equal(saved_iq, actual)):
        raise ValueError("TX reference I/Q differs from the actual quantized stream")
    if not np.array_equal(bits, expected_bits) or bits.ndim != 1:
        raise ValueError("TX reference bits differ from declared payload")
    if (symbols.ndim != 1 or not np.iscomplexobj(symbols) or len(symbols) * 2 != len(bits)
            or not np.all(np.isfinite(symbols)) or not np.array_equal(qpsk_demodulate(symbols), bits)):
        raise ValueError("TX reference constellation is inconsistent with its payload")
    quantization = manifest.get("quantization", {})
    scale = quantization.get("tx_scale")
    peak = float(max(np.max(np.abs(actual.real)), np.max(np.abs(actual.imag))))
    if (isinstance(scale, bool) or not isinstance(scale, Real) or not 0 < scale < 1
            or abs(peak - scale) > .5 / 2048 + 1e-12
            or quantization.get("quantized_component_peak") != peak):
        raise ValueError("TX scaling metadata disagrees with quantized samples")
    ratio = Fraction(fs, symbols_s * 4)
    resampling = manifest.get("resampling", {})
    if resampling != {"method": "scipy.signal.resample_poly", "source_sample_rate": symbols_s * 4,
                      "up": ratio.numerator, "down": ratio.denominator}:
        raise ValueError("TX resampling metadata disagrees with sample and symbol rates")
    source_peak = quantization.get("unscaled_component_peak")
    multiplier = quantization.get("normalization_multiplier")
    if (not isinstance(source_peak, Real) or not np.isfinite(source_peak) or source_peak <= 0
            or not isinstance(multiplier, Real) or not np.isfinite(multiplier) or multiplier <= 0
            or not np.isclose(source_peak * multiplier, scale, rtol=1e-12, atol=1e-15)):
        raise ValueError("TX peak-normalization metadata is inconsistent")
    metadata = {"sample_rate": fs, "symbol_rate": symbols_s, "samples_per_symbol": fs // symbols_s,
                "waveform_format": WAVEFORM_FORMAT, "expected_frames": len(payload),
                "carrier_tracking_enabled": False,
                "provenance": manifest.get("provenance"), "tx_manifest_path": str(path.resolve()),
                "tx_sha256": files["waveform"]["sha256"], "tx_reference_sha256": files["reference"]["sha256"]}
    # Verify references against the saved transmitted stream, never by building
    # a replacement waveform from its configuration or payload declarations.
    adapted, adapted_meta = resample_iq(actual, metadata, symbols_s * 4)
    verification = decode_iq(adapted, adapted_meta, reference={"reference_bits": bits,
                                                             "reference_symbols": symbols})
    if (verification.metrics["ber"] != 0 or verification.metrics["packets_ok"] != len(payload)
            or verification.constellation.shape != symbols.shape
            # Preamble CFO optimization can differ by a few nanounits across
            # SciPy/platform builds. File hashes, bits and decoded payload are
            # still checked; this tolerance covers numerical roundoff only.
            or not np.allclose(verification.constellation, symbols, rtol=1e-8, atol=1e-8)):
        raise ValueError("TX reference constellation differs from the actual quantized stream")
    return {"manifest": manifest, "tx_iq": actual, "reference_bits": bits.astype(np.uint8),
            "reference_symbols": symbols.astype(np.complex128), "metadata": metadata}


def decode_known_capture(capture_path: Path, tx_reference_path: Path, *,
                          sample_rate: int | None = None) -> ReceiverResult:
    """Independently decode saved RX samples and compare the exact known payload.

    Raw sidecar hashes and declared rates are checked before decoding. Missing
    frames, extra frames and CRC-valid but unexpected payloads remain separate.
    Partial-payload BER is labeled separately; a full-payload BER of zero is
    never reported when expected frames are missing.
    """
    reference = load_tx_reference(tx_reference_path)
    manifest = reference["manifest"]
    capture_path = Path(capture_path)
    if sample_rate is not None:
        _integer("sample_rate", sample_rate, 1_000_000, 10_000_000)
    # Reject contradictions rather than silently overriding a rate declared by
    # the capture producer. The existing reader verifies the RX data hash.
    sidecar = capture_path.with_suffix(capture_path.suffix + ".json")
    if sidecar.exists() and capture_path.suffix.lower() in (".sc16q11", ".bin", ".iq"):
        saved = json.loads(sidecar.read_text(encoding="utf-8"))
        declared_rate = saved.get("sample_rate", saved.get("requested", {}).get("sample_rate"))
        if sample_rate is not None and declared_rate is not None and sample_rate != declared_rate:
            raise ValueError("provided RX sample rate contradicts the capture sidecar")
        declared_symbol_rate = saved.get("symbol_rate")
        if declared_symbol_rate is not None and declared_symbol_rate != manifest["symbol_rate"]:
            raise ValueError("RX and TX symbol rates disagree")
        if "samples" in saved and saved["samples"] != capture_path.stat().st_size // 4:
            raise ValueError("RX sample count disagrees with its sidecar")
    measured = decode_recording(capture_path, sample_rate=sample_rate,
                                 symbol_rate=manifest["symbol_rate"],
                                 resample_to=manifest["symbol_rate"] * 4)
    metadata = {**measured.metadata, "expected_frames": manifest["frames_expected"],
                "known_payload_comparison": True, "tx_manifest_path": reference["metadata"]["tx_manifest_path"],
                "tx_sha256": reference["metadata"]["tx_sha256"],
                "reference_note": manifest["reference_note"]}
    result = decode_iq(measured.rx_iq, metadata, reference=reference)
    result.tx_iq = reference["tx_iq"].copy()
    expected = Counter(row["frame_hex"] for row in manifest["payload"])
    received = Counter(encode_telemetry(Telemetry(row["sequence"], row["uptime_ms"], row["button"])).hex()
                       for row in result.telemetry)
    matched = sum((expected & received).values())
    missing = sum((expected - received).values())
    unexpected = sum((received - expected).values())
    duplicates = sum(max(0, count - expected.get(frame, 0)) for frame, count in received.items() if frame in expected)
    metrics = result.metrics
    metrics.update({"known_payload_frames_matched": matched,
                    "known_payload_frames_missing": missing,
                    "known_payload_frames_unexpected": unexpected,
                    "known_payload_duplicate_frames": duplicates,
                    "known_payload_complete": missing == 0,
                    "frames_lost": missing,
                    "packet_success_rate": matched / len(manifest["payload"]),
                    "compared_bit_error_rate": metrics["ber"],
                    "ber_scope": "complete_aligned_known_payload"})
    if metrics["bits_erased"] or (missing and metrics["ber"] == 0):
        metrics["ber"] = None
        metrics["ber_scope"] = "unavailable_incomplete_known_payload; compared_bit_error_rate_is_partial"
    result.metadata["known_payload_missing_frames"] = list((expected - received).elements())
    result.metadata["known_payload_unexpected_frames"] = list((received - expected).elements())
    result.metadata["provenance"] = "known_payload_offline_decode"
    result.config = result.metadata.copy()
    result.reference_bits = reference["reference_bits"].copy()
    return result
