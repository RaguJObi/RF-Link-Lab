"""Known-payload bursts are software evidence; no test opens a device."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from rf_link_lab.conducted_waveform import (
    decode_known_capture, load_tx_reference, prepare_tx_waveform,
)
from rf_link_lab.protocol import Telemetry


MESSAGES = [Telemetry(517, 190123, False), Telemetry(520, 190223, True),
            Telemetry(0xFFFFFFFF, 0xFFFFFFFF, False)]


def _capture(folder, samples, sample_rate=1_000_000, *, origin="synthetic_conducted_rehearsal"):
    path = folder / "capture.sc16q11"
    components = np.column_stack((samples.real, samples.imag))
    words = np.rint(components * 2048)
    assert np.all(words >= -2048) and np.all(words <= 2047)
    path.write_bytes(words.astype("<i2").tobytes())
    path.with_suffix(".sc16q11.json").write_text(json.dumps({
        "sample_rate": sample_rate, "symbol_rate": 50_000, "samples": len(samples),
        "source": origin, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return path


def _rewrite(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_quantized_guarded_roundtrip_retains_exact_actual_tx_and_payload(tmp_path, monkeypatch):
    path = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    reference = load_tx_reference(path)
    manifest = reference["manifest"]
    assert manifest["sample_rate"] == 1_000_000
    assert manifest["symbol_rate"] == 50_000
    assert manifest["samples_per_symbol"] == 20
    assert manifest["guard_samples"] == 10_000
    assert manifest["samples"] == manifest["active_samples"] + 20_000
    assert manifest["finite"] is True and manifest["repeat_count"] == 1
    assert manifest["provenance"] == "prepared_tx_waveform_no_rf"
    assert manifest["quantization"]["quantized_component_peak"] == .25
    assert manifest["quantization"]["positive_rail_headroom_codes"] > 0
    components = np.fromfile(path.with_name("tx.sc16q11"), dtype="<i2").reshape(-1, 2)
    actual = components[:, 0] / 2048 + 1j * components[:, 1] / 2048
    np.testing.assert_array_equal(actual, reference["tx_iq"])
    assert not np.any(actual[:10_000]) and not np.any(actual[-10_000:])
    capture = _capture(tmp_path, actual)
    import rf_link_lab.conducted_waveform as module
    monkeypatch.setattr(module, "generate_waveform", lambda *a, **kw: pytest.fail("decoder regenerated TX"))
    result = decode_known_capture(capture, path)
    assert result.metrics["packets_ok"] == 3
    assert result.metrics["known_payload_frames_matched"] == 3
    assert result.metrics["known_payload_frames_missing"] == 0
    assert result.metrics["known_payload_complete"] is True
    assert result.metrics["ber"] == 0
    assert 0.6 <= result.metrics["sync_score"] <= 1
    # Actual RX decisions now pass through a carrier loop; the immutable TX
    # reference retains the legacy, untracked normalization.
    assert result.metrics["reference_evm_pct"] < .1
    assert result.metrics["carrier_tracking_applied"]
    assert reference["metadata"]["carrier_tracking_enabled"] is False
    assert result.metrics["bits_compared"] == 3 * 17 * 8
    assert result.metrics["packet_success_rate"] == 1
    assert result.metadata["original_origin"] == "synthetic_conducted_rehearsal"
    assert result.metadata["sha256_verified"] is True
    np.testing.assert_array_equal(result.tx_iq, actual)


def test_independent_noisy_phase_cfo_timing_and_gain_capture(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    reference = load_tx_reference(tx)
    rng = np.random.default_rng(7382)
    samples = np.pad(reference["tx_iq"], (173, 597))
    samples = .7 * samples * np.exp(1j * (.89 + 2 * np.pi * 1500 * np.arange(len(samples)) / 1_000_000))
    samples += .0005 * (rng.standard_normal(len(samples)) + 1j * rng.standard_normal(len(samples)))
    result = decode_known_capture(_capture(tmp_path, samples), tx)
    assert result.metrics["known_payload_frames_matched"] == 3
    assert result.metrics["ber"] == 0
    assert abs(result.metrics["cfo_est_hz"] - 1500) < 5
    # 173 input samples become a fractional 34.6-sample shift after decimation;
    # integer timing at four samples/symbol leaves measurable timing error.
    assert result.metrics["reference_evm_pct"] < 15


@pytest.mark.parametrize("phase_difference,accepted", [(3e-9, True), (1e-4, False)])
def test_saved_reference_verification_tolerates_only_tiny_platform_roundoff(tmp_path, phase_difference, accepted):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    target = tx.with_name("tx-reference.npz")
    with np.load(target, allow_pickle=False) as saved:
        arrays = {name: saved[name].copy() for name in saved.files}
    arrays["reference_symbols"] *= np.exp(1j * phase_difference)
    np.savez_compressed(target, **arrays)
    # Rehash deliberately so the independent constellation verification, rather
    # than the file-integrity check, must distinguish roundoff from alteration.
    _rewrite(tx, lambda m: m["files"]["reference"].update(sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    if accepted:
        assert load_tx_reference(tx)["manifest"]["frames_expected"] == len(MESSAGES)
    else:
        with pytest.raises(ValueError, match="constellation differs"):
            load_tx_reference(tx)


def test_truncated_capture_preserves_missing_frames_and_never_full_ber_zero(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared", guard_samples=1000)
    reference = load_tx_reference(tx)
    # Full acquisition preamble and first frame; no trailing zero guard.
    samples = reference["tx_iq"][:1000 + (160 + 68 + 20) * 20]
    result = decode_known_capture(_capture(tmp_path, samples), tx)
    assert result.metrics["acquisition_valid"] is True
    assert result.metrics["known_payload_frames_missing"] >= 1
    assert result.metrics["packet_success_rate"] < 1
    assert result.metrics["known_payload_complete"] is False
    assert result.metrics["ber"] is None
    assert result.metrics["bits_erased"] > 0


def test_unrelated_crc_valid_payload_does_not_count_as_expected_success(tmp_path):
    expected = prepare_tx_waveform(MESSAGES, tmp_path / "expected")
    other = prepare_tx_waveform([Telemetry(1, 10, True)], tmp_path / "other")
    actual = load_tx_reference(other)["tx_iq"]
    result = decode_known_capture(_capture(tmp_path, actual), expected)
    assert result.metrics["packets_ok"] == 1
    assert result.metrics["known_payload_frames_matched"] == 0
    assert result.metrics["known_payload_frames_missing"] == 3
    assert result.metrics["known_payload_frames_unexpected"] == 1
    assert result.metrics["packet_success_rate"] == 0
    assert result.metrics["ber"] != 0


def test_repeated_expected_payload_uses_multiset_matching(tmp_path):
    messages = [MESSAGES[0], MESSAGES[0], MESSAGES[1]]
    tx = prepare_tx_waveform(messages, tmp_path / "expected")
    actual = load_tx_reference(tx)["tx_iq"]
    result = decode_known_capture(_capture(tmp_path, actual), tx)
    assert result.metrics["known_payload_frames_matched"] == 3
    assert result.metrics["known_payload_duplicate_frames"] == 0


@pytest.mark.parametrize("member", ["tx.sc16q11", "tx-reference.npz"])
def test_tampered_tx_files_are_rejected(tmp_path, member):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    target = tx.with_name(member)
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 1
    target.write_bytes(data)
    with pytest.raises(ValueError, match="hash mismatch"):
        load_tx_reference(tx)


def test_tampered_rx_hash_rejected_before_decode(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    capture = _capture(tmp_path, load_tx_reference(tx)["tx_iq"])
    capture.write_bytes(capture.read_bytes() + bytes(4))
    # Retain count here so the hash, rather than count check, detects mutation.
    _rewrite(capture.with_suffix(".sc16q11.json"), lambda m: m.update(samples=capture.stat().st_size // 4))
    with pytest.raises(ValueError, match="hash mismatch"):
        decode_known_capture(capture, tx)


@pytest.mark.parametrize("field,value", [("samples_per_symbol", 4), ("duration_s", 99),
    ("frames_expected", 90), ("finite", False), ("normalization_divisor", 32768),
    ("guard_samples", 3), ("sample_rate", True)])
def test_contradictory_tx_metadata_rejected(tmp_path, field, value):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    _rewrite(tx, lambda m: m.update({field: value}))
    with pytest.raises(ValueError):
        load_tx_reference(tx)


def test_bundle_references_cannot_escape_directory(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    _rewrite(tx, lambda m: m["files"]["waveform"].update(file="../tx.sc16q11"))
    with pytest.raises(ValueError, match="basenames"):
        load_tx_reference(tx)


def test_payload_declaration_tamper_rejected(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    _rewrite(tx, lambda m: m["payload"][0].update(sequence=88))
    with pytest.raises(ValueError, match="payload bytes"):
        load_tx_reference(tx)


def test_rx_rate_and_size_metadata_contradictions_rejected(tmp_path):
    tx = prepare_tx_waveform(MESSAGES, tmp_path / "prepared")
    capture = _capture(tmp_path, load_tx_reference(tx)["tx_iq"])
    with pytest.raises(ValueError, match="contradicts"):
        decode_known_capture(capture, tx, sample_rate=2_000_000)
    _rewrite(capture.with_suffix(".sc16q11.json"), lambda m: m.update(samples=1))
    with pytest.raises(ValueError, match="sample count"):
        decode_known_capture(capture, tx)


@pytest.mark.parametrize("settings", [{"sample_rate": 200000}, {"sample_rate": True},
    {"sample_rate": 1000001}, {"symbol_rate": 0}, {"symbol_rate": 300000},
    {"tx_scale": 0}, {"tx_scale": 1}, {"tx_scale": np.nan}, {"tx_scale": np.inf},
    {"tx_scale": 1e-20}, {"guard_samples": -1}, {"guard_samples": 1.5}])
def test_invalid_rates_scaling_guards_rejected_before_bundle_creation(tmp_path, settings):
    with pytest.raises(ValueError):
        prepare_tx_waveform(MESSAGES, tmp_path / "bad", **settings)
    assert not (tmp_path / "bad").exists()


def test_explicit_noninteger_upsample_ratio_and_alternate_symbol_rate(tmp_path):
    path = prepare_tx_waveform(MESSAGES, tmp_path / "prepared", sample_rate=1_250_000)
    data = load_tx_reference(path)
    assert data["manifest"]["resampling"]["up"] == 25
    assert data["manifest"]["resampling"]["down"] == 4
    assert data["manifest"]["samples_per_symbol"] == 25
    path = prepare_tx_waveform(MESSAGES, tmp_path / "alternate", sample_rate=1_000_000, symbol_rate=100_000)
    assert load_tx_reference(path)["manifest"]["samples_per_symbol"] == 10


def test_output_directory_is_exclusive(tmp_path):
    output = tmp_path / "kept"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        prepare_tx_waveform(MESSAGES, output)
    assert sentinel.read_text() == "keep"


def test_empty_and_unbounded_payload_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        prepare_tx_waveform([], tmp_path / "empty")
    with pytest.raises(ValueError, match="at most"):
        prepare_tx_waveform((Telemetry(i, i, False) for i in range(2049)), tmp_path / "too-many")
