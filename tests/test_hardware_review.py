"""Evidence review contracts using inert file fixtures; no RF or software link."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from rf_link_lab.bench import BenchSession
from rf_link_lab.hardware_review import ADC_RAIL_FEATURE_DEFINITION, BLIND_FEATURE_NAMES, review_experiment


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _receiver(folder, raw_sha, *, packets=4, complete=False, tracking=False):
    folder.mkdir(parents=True, exist_ok=True)
    np.savez(folder / "capture.npz", rx_iq=np.array([1 + 1j]))
    metadata = {"experiment_mode": "live", "capture_sha256": raw_sha,
                "capture_sidecar": {"sha256": raw_sha}, "sample_rate": 200000,
                "symbol_rate": 50000, "tx_sha256": "3" * 64,
                "carrier_tracking_enabled": tracking}
    record = {"schema": "rf-link-lab-run-v2", "provenance": "known_payload_offline_decode",
              "application_version": "test-fixture", "capture_sha256": _hash(folder / "capture.npz"),
              "metadata": metadata, "config": metadata,
              "metrics": {"status": "decoded", "known_payload_complete": complete,
                          "packets_ok": packets, "cfo_est_hz": -8.5, "pilot_evm_pct": 29.4,
                          "ber": .09, "reference_evm_pct": 60.6, "evm_pct": 60.6},
              "features": {"cfo_abs_hz": 8.5, "evm_pct": 60.6, "snr_est_db": 22.3,
                           "clipping_fraction": 0, "amplitude_cv": 1.5, "spectral_flatness": .61}}
    _json(folder / "run.json", record)
    return folder / "run.json"


def _experiment(tmp_path, *, receiver=True, cleanup=True, mode="live", held=False, versions=None, raw_bytes=None):
    folder = tmp_path / "experiment"
    session = BenchSession.create(folder, environment={})
    transport = {"status": "complete" if receiver else "failed", "capture_completed": receiver,
                 "tx_start_requested": receiver and mode == "live", "cleanup_confirmed": cleanup,
                 "readback": {"versions": versions or {"firmware": "2.6.0", "fpga": "0.16.0", "libbladerf": "2.6.0"}}}
    metrics = {}
    if receiver:
        (folder / "hardware").mkdir()
        pairs = np.zeros((2048, 2), dtype="<i2")
        pairs[:512, 0] = np.arange(512)
        if held:
            pairs[512:] = pairs[511]
        else:
            pairs[512:, 0] = np.arange(1536)
        (folder / "hardware/rx.sc16q11").write_bytes(pairs.tobytes() if raw_bytes is None else raw_bytes)
        raw_sha = _hash(folder / "hardware/rx.sc16q11")
        receiver_path = _receiver(folder / "receiver", raw_sha)
        metrics = json.loads(receiver_path.read_text())["metrics"]
        (folder / "tx").mkdir()
        (folder / "tx/tx.sc16q11").write_bytes(b"inert fixture TX bytes")
        np.savez(folder / "tx/tx-reference.npz", fixture=np.array([1]))
        tx_sha = _hash(folder / "tx/tx.sc16q11")
        _json(folder / "tx/tx.json", {"files": {"waveform": {"sha256": tx_sha}}})
        (folder / "hardware/tx.sc16q11").write_bytes((folder / "tx/tx.sc16q11").read_bytes())
        transport["tx_sha256"] = tx_sha
    files = [item for item in folder.rglob("*") if item.is_file() and item.name not in ("bench.json", "events.jsonl")]
    record = {"schema": "rf-link-lab-conducted-experiment-v1", "finalized": True,
              "application_version": "test-fixture", "mode": mode,
              "origin": "hardware_duplex_rx" if mode == "live" else "synthetic_duplex_rehearsal",
              "status": "capture_without_complete_payload" if receiver else "failed",
              "metrics": metrics, "transport": transport, "payload_source": {"source": "inert_fixture"},
              "tx_start_requested": receiver and mode == "live", "cleanup_confirmed": cleanup,
              "error": None if receiver else "Inert pre-TX failure fixture",
              "assets": {item.relative_to(folder).as_posix(): {"size_bytes": item.stat().st_size, "sha256": _hash(item)} for item in files}}
    _json(folder / "experiment.json", record)
    session.add_experiment_recording(folder / "experiment.json")
    session.finalize()
    return folder


def test_review_uses_strict_blind_whitelist_and_preserves_all_files(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("Review launched a hardware process"))
    folder = _experiment(tmp_path)
    before = {str(path): _hash(path) for path in folder.rglob("*") if path.is_file()}
    review = review_experiment(folder / "experiment.json")
    assert review["schema"] == "rf-link-lab.hardware-review.v1"
    assert tuple(review["blind_features"]) == BLIND_FEATURE_NAMES
    assert review["blind_features"]["pilot_evm_pct"] == 29.4
    assert "evm_pct" not in review["blind_features"] and "ber" not in review["blind_features"]
    assert review["outcome"]["new_rf_transmission"] is False
    assert review["outcome"]["selected_known_payload_complete"] is False
    assert review["source"]["raw_rx_sha256"] == _hash(folder / "hardware/rx.sc16q11")
    assert review["source"]["receiver_npz_sha256"] == _hash(folder / "receiver/capture.npz")
    assert all(set(row) == {"path", "sha256", "bytes"} for row in review["source"]["files"])
    assert before == {str(path): _hash(path) for path in folder.rglob("*") if path.is_file()}
    assert not any("label" in key for key in review)


def test_failed_before_tx_has_explicit_unavailable_features_and_no_success(tmp_path):
    review = review_experiment(_experiment(tmp_path, receiver=False, cleanup=False))
    assert set(review["blind_features"].values()) == {None}
    assert review["receiver_metrics"] == {}
    assert review["source"]["raw_rx_sha256"] is None
    assert review["outcome"]["selected_known_payload_complete"] is None
    assert review["outcome"]["physical_tx_requested"] is False
    assert {row["code"] for row in review["findings"]} >= {"cleanup_unconfirmed", "transport_not_completed", "incomplete_blind_measurements"}


def test_rehearsal_and_tampered_original_are_rejected(tmp_path):
    folder = _experiment(tmp_path, receiver=False, mode="rehearsal")
    with pytest.raises(ValueError, match="rejects rehearsal"):
        review_experiment(folder)
    (folder / "experiment.json").write_text("{}")
    with pytest.raises(ValueError):
        review_experiment(folder)


def test_raw_capture_tamper_is_rejected(tmp_path):
    folder = _experiment(tmp_path)
    with (folder / "hardware/rx.sc16q11").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="hash or size mismatch"):
        review_experiment(folder)


def test_same_raw_reprocessing_keeps_original_outcome_and_tracks_hypothesis(tmp_path):
    folder = _experiment(tmp_path)
    selected = _receiver(tmp_path / "reprocessed", _hash(folder / "hardware/rx.sc16q11"), packets=12, complete=True, tracking=True)
    review = review_experiment(folder, receiver_run=selected)
    assert review["source"]["reprocessed"] is True
    assert review["outcome"]["recorded_known_payload_complete"] is False
    assert review["outcome"]["selected_known_payload_complete"] is True
    improved = next(row for row in review["findings"] if row["code"] == "carrier_tracking_associated_improvement")
    assert improved["kind"] == "hypothesis"
    assert improved["evidence"]["new_rf_transmission"] is False
    assert review["source"]["raw_rx_sha256"] != review["source"]["receiver_npz_sha256"]


@pytest.mark.parametrize("damage", ["raw", "npz", "missing_raw", "conflicting_sidecar"])
def test_optional_receiver_requires_matching_original_raw_and_npz(tmp_path, damage):
    folder = _experiment(tmp_path)
    selected = _receiver(tmp_path / "reprocessed", _hash(folder / "hardware/rx.sc16q11"), packets=12, complete=True)
    record = json.loads(selected.read_text())
    if damage == "raw":
        record["metadata"]["capture_sha256"] = "0" * 64
    elif damage == "npz":
        (selected.parent / "capture.npz").write_bytes(b"changed")
    elif damage == "missing_raw":
        record["metadata"].pop("capture_sha256")
        record["metadata"].pop("capture_sidecar")
    else:
        record["metadata"]["capture_sidecar"]["sha256"] = "1" * 64
    _json(selected, record)
    with pytest.raises(ValueError):
        review_experiment(folder, receiver_run=selected)


def test_inconsistent_reprocessing_settings_do_not_imply_carrier_improvement(tmp_path):
    folder = _experiment(tmp_path)
    selected = _receiver(tmp_path / "reprocessed", _hash(folder / "hardware/rx.sc16q11"), packets=12, complete=True, tracking=True)
    record = json.loads(selected.read_text())
    record["metadata"]["symbol_rate"] = 25000
    _json(selected, record)
    result = review_experiment(folder, receiver_run=selected)
    assert "carrier_tracking_associated_improvement" not in {row["code"] for row in result["findings"]}


def test_known_gpif_versions_and_held_padding_are_separate_observations(tmp_path):
    folder = _experiment(tmp_path, held=True, versions={"firmware": "2.6.0", "fpga": "0.14.0", "libbladerf": "2.4.1"})
    review = review_experiment(folder)
    assert review["capture_integrity"]["held_padding_fraction"] == 1
    codes = {row["code"] for row in review["findings"]}
    assert {"recorded_stack_mismatch", "held_sample_padding"} <= codes
    assert all(row["kind"] == "observation" for row in review["findings"])


def test_nonfinite_or_boolean_features_are_unavailable_and_never_imputed(tmp_path):
    folder = _experiment(tmp_path)
    selected = _receiver(tmp_path / "reprocessed", _hash(folder / "hardware/rx.sc16q11"))
    record = json.loads(selected.read_text())
    record["features"].update(snr_est_db=float("nan"), amplitude_cv=True)
    record["metrics"]["pilot_evm_pct"] = float("inf")
    _json(selected, record)
    review = review_experiment(folder, receiver_run=selected)
    assert all(review["blind_features"][name] is None for name in ("snr_est_db", "amplitude_cv", "pilot_evm_pct"))
    json.dumps(review, allow_nan=False)


def test_adc_rail_fraction_counts_original_complex_samples_once(tmp_path):
    raw = np.array([[-2048, 0], [0, 2047], [-2048, 2047], [2046, -2047], [511, 511], [511, 511]], dtype="<i2")
    folder = _experiment(tmp_path, raw_bytes=raw.tobytes())
    review = review_experiment(folder)
    assert review["blind_features"]["clipping_fraction"] == .5
    measurement = review["raw_adc_rail_measurement"]
    assert measurement["sample_count"] == 6
    assert measurement["rail_hit_samples"] == 3
    assert measurement["rail_hit_components"] == 4
    assert measurement["format_valid"] is True
    assert measurement["rail_codes"] == [-2048, 2047]
    assert measurement["bytes_examined"] == raw.nbytes
    basis = review["feature_basis"]["clipping_fraction"]
    assert basis["definition_id"] == ADC_RAIL_FEATURE_DEFINITION
    assert basis["source"] == "original_raw_sc16q11"
    assert basis["raw_rx_sha256"] == review["source"]["raw_rx_sha256"]


def test_repeated_small_extrema_and_legacy_receiver_proxy_do_not_become_adc_rail_hits(tmp_path):
    raw = np.tile(np.array([[8, -9]], dtype="<i2"), (9, 1))
    folder = _experiment(tmp_path, raw_bytes=raw.tobytes())
    selected = _receiver(tmp_path / "reprocessed", _hash(folder / "hardware/rx.sc16q11"))
    record = json.loads(selected.read_text())
    record["features"]["clipping_fraction"] = .99
    record["metrics"]["clipping_fraction"] = .88
    _json(selected, record)
    # The selected NPZ also contains unrelated normalized values. Only the
    # original SC16 codes, not either old proxy or NPZ extrema, enter this input.
    review = review_experiment(folder, receiver_run=selected)
    assert review["blind_features"]["clipping_fraction"] == 0
    assert review["raw_adc_rail_measurement"]["rail_hit_samples"] == 0
    assert review["receiver_metrics"]["clipping_fraction"] == .88
    assert json.loads(selected.read_text())["features"]["clipping_fraction"] == .99


def test_adc_rail_scan_covers_samples_beyond_its_bounded_chunk(tmp_path):
    samples = 1024 * 1024 // 4 + 3
    raw = np.zeros((samples, 2), dtype="<i2")
    raw[-1, 1] = -2048
    review = review_experiment(_experiment(tmp_path, raw_bytes=raw.tobytes()))
    assert review["raw_adc_rail_measurement"]["sample_count"] == samples
    assert review["raw_adc_rail_measurement"]["rail_hit_samples"] == 1
    assert review["blind_features"]["clipping_fraction"] == 1 / samples


@pytest.mark.parametrize("component,trailing", [(2048, b""), (-2049, b""), (0, b"\x01")])
def test_invalid_sc16_words_or_partial_samples_make_raw_rail_feature_unavailable(tmp_path, component, trailing):
    raw = np.array([[component, 0], [-2048, 2047]], dtype="<i2").tobytes() + trailing
    review = review_experiment(_experiment(tmp_path, raw_bytes=raw))
    assert review["blind_features"]["clipping_fraction"] is None
    assert review["raw_adc_rail_measurement"]["format_valid"] is False
    assert "invalid_sc16q11_format" in {row["code"] for row in review["findings"]}
    assert ("partial_sc16_sample" in {row["code"] for row in review["findings"]}) is bool(trailing)


def test_absent_raw_recording_has_no_adc_rail_measurement(tmp_path):
    review = review_experiment(_experiment(tmp_path, receiver=False))
    assert review["blind_features"]["clipping_fraction"] is None
    assert review["raw_adc_rail_measurement"]["available"] is False
    assert review["raw_adc_rail_measurement"]["fraction"] is None
