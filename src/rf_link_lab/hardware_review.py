"""Read-only review of preserved conducted-hardware evidence.

This module verifies existing files and describes recorded observations. It
does not open a device, decode new samples, assign training labels or invoke a
learned classifier. Hashes bind a review to files; they do not authenticate RF.
"""
from __future__ import annotations

import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re

import numpy as np

from .bench import _asset, _folder, verify_bench_session
from .conducted import verify_conducted_experiment
from .storage import json_value


SCHEMA = "rf-link-lab.hardware-review.v1"
BLIND_FEATURE_NAMES = (
    "cfo_abs_hz", "pilot_evm_pct", "snr_est_db", "clipping_fraction",
    "amplitude_cv", "spectral_flatness",
)
ADC_RAIL_FEATURE_DEFINITION = "raw_sc16q11_adc_rail_fraction_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _digest(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path.absolute()), "sha256": digest.hexdigest(), "bytes": size}


def _read(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Evidence manifest must be a JSON object.")
    return result


def _number(value):
    if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _receiver(path: Path, raw_sha256: str | None, *, require_raw: bool) -> tuple[dict, list[Path]]:
    folder, manifest_path = _folder(path, "run.json")
    record = _read(manifest_path)
    if record.get("schema") not in ("rf-link-lab-run-v1", "rf-link-lab-run-v2"):
        raise ValueError("Unsupported receiver manifest schema.")
    if record.get("provenance") not in ("known_payload_offline_decode", "independent_offline_decode"):
        raise ValueError("Hardware review requires a stored independent receiver result.")
    archive = _asset(folder, "capture.npz")
    if _digest(archive)["sha256"] != record.get("capture_sha256"):
        raise ValueError("Receiver NPZ capture hash mismatch.")
    metadata = record.get("metadata", {})
    if not isinstance(metadata, dict) or not isinstance(record.get("metrics", {}), dict):
        raise ValueError("Receiver metadata and measurements must be objects.")
    sidecar = metadata.get("capture_sidecar", {})
    if not isinstance(sidecar, dict):
        raise ValueError("Receiver capture sidecar must be an object.")
    links = [value for value in (metadata.get("capture_sha256"), sidecar.get("sha256")) if value is not None]
    if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in links):
        raise ValueError("Receiver original raw RX hash is invalid.")
    if links and (raw_sha256 is None or any(value != raw_sha256 for value in links)):
        raise ValueError("Receiver result refers to a different original raw RX capture.")
    if require_raw and (raw_sha256 is None or not links):
        raise ValueError("A separate receiver result must identify the original raw RX SHA256.")
    return record, [manifest_path, archive]


def _padding(path: Path | None) -> dict:
    """Inspect at most 512 spread blocks (4 MiB), never infer a sample rate.

    The observed legacy mismatch filled the final 1536 pairs of each 2048-pair
    block with pair 511. Requiring a varying first 512 pairs excludes all-zero
    recordings and naturally constant blocks from that particular signature.
    """
    result = {"method": "held_pair_511_in_2048_complex_sample_blocks",
              "block_samples": 2048, "maximum_blocks": 512,
              "blocks_examined": 0, "held_padding_blocks": 0,
              "held_padding_fraction": None, "trailing_incomplete_sample_bytes": 0}
    if path is None:
        return result
    size = path.stat().st_size
    result["trailing_incomplete_sample_bytes"] = size % 4
    blocks = size // (2048 * 4)
    indices = np.unique(np.linspace(0, max(0, blocks - 1), min(blocks, 512), dtype=np.int64))
    with path.open("rb") as stream:
        for index in indices:
            stream.seek(int(index) * 2048 * 4)
            values = np.frombuffer(stream.read(2048 * 4), dtype="<i2").reshape(-1, 2)
            if len(values) != 2048:
                raise ValueError("Raw RX recording changed during bounded integrity inspection.")
            held = bool(np.all(values[512:] == values[511]) and np.any(values[:511] != values[511]))
            result["blocks_examined"] += 1
            result["held_padding_blocks"] += int(held)
    if result["blocks_examined"]:
        result["held_padding_fraction"] = result["held_padding_blocks"] / result["blocks_examined"]
    return result


def _raw_adc_rails(path: Path | None) -> dict:
    """Count exact endpoint codes over the original file with 1 MiB reads.

    SC16 Q11 stores signed 12-bit I/Q component codes in little-endian int16
    words. One complex sample contributes one hit if either component equals
    -2048 or 2047. No resampling, extrema estimation or receiver result is used.
    """
    result = {"definition_id": ADC_RAIL_FEATURE_DEFINITION,
              "source": "original_raw_sc16q11", "available": path is not None,
              "source_file": str(path.absolute()) if path is not None else None,
              "format": "SC16Q11_LE", "rail_codes": [-2048, 2047],
              "sample_count": 0, "rail_hit_samples": 0,
              "rail_hit_components": 0, "out_of_range_samples": 0,
              "out_of_range_components": 0, "trailing_bytes": 0,
              "bytes_examined": 0, "format_valid": None, "fraction": None,
              "scan_scope": "entire original raw file; at most 1 MiB per read",
              "definition": "Complex samples with either original I or Q code exactly -2048 or 2047, divided by all original complex samples; both rails in one sample count once."}
    if path is None:
        return result
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result["bytes_examined"] += len(block)
            complete = len(block) // 4 * 4
            result["trailing_bytes"] += len(block) - complete
            values = np.frombuffer(block[:complete], dtype="<i2").reshape(-1, 2)
            rails = (values == -2048) | (values == 2047)
            invalid = (values < -2048) | (values > 2047)
            result["sample_count"] += len(values)
            result["rail_hit_samples"] += int(np.count_nonzero(np.any(rails, axis=1)))
            result["rail_hit_components"] += int(np.count_nonzero(rails))
            result["out_of_range_samples"] += int(np.count_nonzero(np.any(invalid, axis=1)))
            result["out_of_range_components"] += int(np.count_nonzero(invalid))
    result["format_valid"] = not (result["out_of_range_components"] or result["trailing_bytes"])
    if result["format_valid"] and result["sample_count"]:
        result["fraction"] = result["rail_hit_samples"] / result["sample_count"]
    return result


def _versions(transport: dict, log: Path | None) -> dict:
    readback = transport.get("readback") or {}
    if not isinstance(readback, dict) or not isinstance(readback.get("versions", {}), dict):
        raise ValueError("Recorded version readback must be an object.")
    versions = dict(readback.get("versions") or {})
    text = str(readback.get("version_output") or "")
    if log is not None:
        with log.open("rb") as stream:
            text += "\n" + stream.read(1024 * 1024).decode("utf-8", errors="replace")
    for key, label in (("firmware", "Firmware"), ("fpga", "FPGA"), ("libbladerf", "libbladeRF"), ("cli", "bladeRF-cli")):
        if key not in versions:
            hits = re.findall(r"\b" + re.escape(label) + r" version:\s*([^\r\n]+)", text)
            if hits:
                versions[key] = hits[-1].strip()
    return versions


def _version(value):
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(map(int, match.groups())) if match else None


def review_experiment(path, *, receiver_run=None) -> dict:
    """Verify and review one finalized, declared-live experiment without writes.

    ``receiver_run`` may select a separately saved reprocessing of the same raw
    capture. Its raw-capture linkage and NPZ hash must both verify. Original
    experiment status and measurements remain separate from selected results.
    """
    folder, experiment_path = _folder(Path(path), "experiment.json")
    initial_experiment = _digest(experiment_path)
    initial_bench = _digest(_asset(folder, "bench.json"))
    verified = verify_conducted_experiment(experiment_path)
    record = verified["manifest"]
    if record.get("mode") != "live" or record.get("origin") != "hardware_duplex_rx":
        raise ValueError("Hardware review rejects rehearsal or synthetic experiments.")
    bench = verify_bench_session(folder)
    if "experiment.json" not in bench["manifest"]["assets"]:
        raise ValueError("The finalized bench must preserve its original experiment manifest.")
    paths = {experiment_path, _asset(folder, "bench.json")}
    paths.update(_asset(folder, name) for name in record["assets"])
    paths.update(_asset(folder, name) for name in bench["manifest"]["assets"])
    raw = _asset(folder, "hardware/rx.sc16q11") if "hardware/rx.sc16q11" in record["assets"] else None
    raw_sha = _digest(raw)["sha256"] if raw is not None else None
    original = None
    original_paths = []
    if "receiver/run.json" in record["assets"]:
        original, original_paths = _receiver(folder / "receiver/run.json", raw_sha, require_raw=False)
        paths.update(original_paths)
    selected = original
    selected_paths = original_paths
    reprocessed = False
    if receiver_run is not None:
        selected, selected_paths = _receiver(Path(receiver_run), raw_sha, require_raw=True)
        paths.update(selected_paths)
        reprocessed = not original_paths or selected_paths[0].resolve() != original_paths[0].resolve()
    before = {str(item.absolute()): _digest(item) for item in sorted(paths)}
    for inventory in (record["assets"], bench["manifest"]["assets"]):
        for name, expected in inventory.items():
            observed = before[str((folder / name).absolute())]
            if observed["sha256"] != expected["sha256"] or observed["bytes"] != expected["size_bytes"]:
                raise ValueError("Source evidence changed after contract verification.")
    transport = record.get("transport") or {}
    if not isinstance(transport, dict):
        raise ValueError("Recorded transport must be an object.")
    metrics = (selected or {}).get("metrics", {})
    measured = (selected or {}).get("features", {})
    if not isinstance(measured, dict):
        raise ValueError("Stored receiver features must be an object.")
    blind = {name: _number(metrics.get(name) if name == "pilot_evm_pct" else measured.get(name))
             for name in BLIND_FEATURE_NAMES}
    adc_rails = _raw_adc_rails(raw)
    # The legacy receiver feature counts repeated observed extrema after
    # processing. It must never stand in for original SC16 ADC endpoint codes.
    blind["clipping_fraction"] = adc_rails["fraction"]
    feature_basis = {
        "cfo_abs_hz": {"source": "selected_receiver", "definition": "Absolute frequency offset estimated using the public acquisition sequence; no payload reference."},
        "pilot_evm_pct": {"source": "selected_receiver.metrics.pilot_evm_pct", "definition": "Normalized error against the public synchronization symbols; no private payload reference."},
        "snr_est_db": {"source": "selected_receiver.features.snr_est_db", "definition": "Public-pilot mean-to-residual estimate, not a calibrated RF SNR measurement."},
        "clipping_fraction": {"definition_id": ADC_RAIL_FEATURE_DEFINITION,
                              "source": "original_raw_sc16q11", "rail_codes": [-2048, 2047],
                              "definition": adc_rails["definition"], "sample_count": adc_rails["sample_count"],
                              "rail_hit_samples": adc_rails["rail_hit_samples"], "raw_rx_sha256": raw_sha,
                              "scan_scope": adc_rails["scan_scope"]},
        "amplitude_cv": {"source": "selected_receiver.features.amplitude_cv", "definition": "Amplitude standard deviation divided by mean, over the recorded post-synchronization window including its trailing samples."},
        "spectral_flatness": {"source": "selected_receiver.features.spectral_flatness", "definition": "Geometric-to-arithmetic mean of selected baseband Welch-bin powers over the recorded capture window."},
    }
    # cfo_abs_hz is ordinarily a measured feature; older manifests may carry
    # only the signed, independently estimated offset among their metrics.
    if blind["cfo_abs_hz"] is None and _number(metrics.get("cfo_est_hz")) is not None:
        blind["cfo_abs_hz"] = abs(float(metrics["cfo_est_hz"]))
    findings = []

    def finding(code, title, severity, evidence, next_measurement, kind="observation"):
        findings.append({"code": code, "title": title, "severity": severity, "kind": kind,
                         "evidence": json_value(evidence), "next_measurement": next_measurement})

    if record.get("cleanup_confirmed") is not True:
        finding("cleanup_unconfirmed", "Transport shutdown is unconfirmed", "error",
                {"cleanup_confirmed": record.get("cleanup_confirmed"), "error": record.get("error")},
                "When the bench returns, inspect device stream state and confirm shutdown before another finite run.")
    if record.get("status") in ("failed", "cancelled"):
        finding("transport_not_completed", "The recorded experiment did not complete", "warning",
                {"status": record.get("status"), "error": record.get("error"), "tx_start_requested": record.get("tx_start_requested")},
                "Inspect the preserved transport log, correct the recorded error, then make one new bounded capture.")
    log = _asset(folder, "hardware/session.log") if "hardware/session.log" in record["assets"] else None
    versions = _versions(transport, log)
    firmware, fpga, library = (_version(versions.get(key)) for key in ("firmware", "fpga", "libbladerf"))
    mismatch = []
    if firmware and fpga and ((firmware >= (2, 5, 0) and fpga < (0, 16, 0)) or (fpga >= (0, 16, 0) and firmware < (2, 5, 0))):
        mismatch.append("Firmware/FPGA pair crosses the known GPIF buffer geometry boundary.")
    if firmware and library and firmware >= (2, 6, 0) and library < (2, 6, 0):
        mismatch.append("Firmware 2.6 with a pre-2.6 host library fails the project matched-stack policy.")
    if mismatch:
        finding("recorded_stack_mismatch", "Recorded radio stack fails known buffer checks", "error",
                {"versions": versions, "checks": mismatch},
                "Use the packaged matching host tools and volatile FPGA image, then compare a fresh receive recording.")
    integrity = _padding(raw)
    if integrity["held_padding_blocks"]:
        finding("held_sample_padding", "Raw I/Q contains the legacy held-pair padding signature", "error",
                integrity, "Compare a fresh receive-only recording using the matched stack; do not interpret held pairs as ADC signal.")
    if integrity["trailing_incomplete_sample_bytes"]:
        finding("partial_sc16_sample", "Raw recording ends within an I/Q sample", "warning", integrity,
                "Inspect transport errors and preserve this partial capture before recording a fresh complete sample stream.")
    if adc_rails["format_valid"] is False:
        finding("invalid_sc16q11_format", "Original raw samples violate the SC16 Q11 code or sample format", "error",
                adc_rails, "Inspect the capture format, word scaling and transport log before recording a fresh valid SC16 Q11 stream; do not interpret out-of-range words as clipping.")
    if metrics.get("known_payload_complete") is False:
        finding("known_payload_incomplete", "Selected receiver did not recover the complete known payload", "warning",
                {key: metrics.get(key) for key in ("known_payload_frames_matched", "known_payload_frames_missing", "known_payload_frames_unexpected", "packets_ok")},
                "Inspect the received burst and transport log before selecting a single controlled follow-up; missing frames alone do not identify a cause.")
    elif metrics.get("known_payload_complete") is True:
        finding("known_payload_recovered", "Selected receiver recovered the saved known payload", "info",
                {"known_payload_complete": True, "packets_ok": metrics.get("packets_ok"), "reprocessed": reprocessed},
                "Repeat with a fresh physical capture when available; one saved capture does not establish repeatability.")
    missing = [name for name, value in blind.items() if value is None]
    if missing:
        finding("incomplete_blind_measurements", "Some reference-independent measurements are unavailable", "info",
                {"missing_features": missing, "receiver_status": metrics.get("status")},
                "Retain unavailable values; inspect acquisition or transport evidence rather than inventing measurements.")
    if reprocessed and original is not None:
        original_metadata, selected_metadata = original.get("metadata", {}), selected.get("metadata", {})
        original_metrics = original.get("metrics", {})
        comparable = all(original_metadata.get(key) is not None and original_metadata.get(key) == selected_metadata.get(key)
                         for key in ("sample_rate", "symbol_rate", "capture_sha256"))
        comparable = comparable and original_metadata.get("tx_sha256") == selected_metadata.get("tx_sha256")
        increased = (_number(metrics.get("packets_ok")) is not None and _number(original_metrics.get("packets_ok")) is not None
                     and metrics["packets_ok"] > original_metrics["packets_ok"])
        tracking_added = selected_metadata.get("carrier_tracking_enabled") is True and original_metadata.get("carrier_tracking_enabled") is not True
        if comparable and increased and tracking_added:
            finding("carrier_tracking_associated_improvement", "Carrier tracking accompanied improved recovery of the same capture", "info",
                    {"original_packets_ok": original_metrics.get("packets_ok"), "selected_packets_ok": metrics.get("packets_ok"),
                     "raw_rx_sha256": raw_sha, "new_rf_transmission": False},
                    "Use the updated receiver on a fresh independent capture; this comparison supports a receiver-processing hypothesis, not a hardware component diagnosis.", kind="hypothesis")
    if before != {str(item.absolute()): _digest(item) for item in sorted(paths)}:
        raise ValueError("Source evidence changed during hardware review.")
    if initial_experiment != _digest(experiment_path) or initial_bench != _digest(_asset(folder, "bench.json")):
        raise ValueError("Original manifests changed during hardware review.")
    if selected is not None:
        current, _ = _receiver(selected_paths[0], raw_sha, require_raw=reprocessed)
        if json.dumps(current, sort_keys=True) != json.dumps(selected, sort_keys=True):
            raise ValueError("Selected receiver manifest changed during hardware review.")
    selected_manifest = before[str(selected_paths[0].absolute())] if selected_paths else None
    selected_npz = before[str(selected_paths[1].absolute())] if selected_paths else None
    return {"schema": SCHEMA, "source": {
                "experiment_path": str(experiment_path), "experiment_sha256": before[str(experiment_path.absolute())]["sha256"],
                "raw_rx_sha256": raw_sha, "receiver_manifest_path": selected_manifest["path"] if selected_manifest else None,
                "receiver_manifest_sha256": selected_manifest["sha256"] if selected_manifest else None,
                "receiver_npz_sha256": selected_npz["sha256"] if selected_npz else None,
                "application_version": (selected or record).get("application_version"), "reprocessed": reprocessed,
                "files": list(before.values())},
            "transport": json_value(transport), "receiver_metrics": json_value(metrics),
            "blind_features": blind, "feature_basis": feature_basis,
            "raw_adc_rail_measurement": adc_rails, "capture_integrity": integrity, "recorded_versions": versions,
            "findings": findings, "outcome": {
                "experiment_status": record.get("status"), "receiver_status": metrics.get("status"),
                "recorded_known_payload_complete": record.get("metrics", {}).get("known_payload_complete"),
                "selected_known_payload_complete": metrics.get("known_payload_complete"),
                "physical_tx_requested": record.get("tx_start_requested"), "new_rf_transmission": False},
            "limitations": [
                "Source origin and physical connections remain declarations; hashes detect file changes, not source authenticity.",
                "This read-only review performs no hardware access, new RF transmission, receiver execution or automatic training label assignment.",
                "Findings are recorded observations or explicit hypotheses, not learned AI diagnoses or calibrated RF measurements.",
                "Reprocessing the same raw capture is not a new independent hardware example or bench session.",
                "The blind clipping fraction counts exact endpoint codes in the entire original raw SC16 Q11 file. Endpoint occupancy alone does not establish an analog clipping cause.",
                "Historical receiver_metrics clipping_fraction remains unchanged; that older processed-sample extrema proxy is not the blind raw-code rail fraction.",
                "Amplitude variation and spectral flatness depend on the capture window and receiver processing; they do not isolate a physical fault.",
                "Known-payload BER, reference EVM and frame-coverage metrics are review evidence only and never enter blind_features.",
            ]}
