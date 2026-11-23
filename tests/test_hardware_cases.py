"""Structural fixtures verify bookkeeping; none are physical training evidence."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from rf_link_lab import hardware_cases as cases


def structural_review(tmp_path, identity="one", *, raw_identity=None, features=None):
    """Mock the already-verified review boundary using small structural files."""
    directory = tmp_path / identity
    directory.mkdir(exist_ok=True)
    files = []
    for name, content in (("experiment.json", identity), ("raw.sc16q11", raw_identity or identity),
                          ("run.json", identity + "receiver"), ("capture.npz", identity + "npz")):
        path = directory / name
        path.write_bytes(content.encode())
        files.append({"path": str(path.absolute()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    return {"schema": "rf-link-lab.hardware-review.v1", "source": {
        "experiment_path": files[0]["path"], "experiment_sha256": files[0]["sha256"],
        "raw_rx_sha256": files[1]["sha256"], "receiver_manifest_path": files[2]["path"],
        "receiver_manifest_sha256": files[2]["sha256"], "receiver_npz_sha256": files[3]["sha256"],
        "application_version": "structural-test-fixture", "reprocessed": False, "files": files},
        "blind_features": features or {"cfo_abs_hz": 2., "pilot_evm_pct": 10., "snr_est_db": 28.,
                                      "clipping_fraction": 0., "amplitude_cv": .2, "spectral_flatness": .05},
        "outcome": {"experiment_status": "passed", "receiver_status": "decoded", "physical_tx_requested": True},
        "feature_basis": {"clipping_fraction": {"definition_id": cases.CLIPPING_DEFINITION_ID}},
        "transport": {}, "receiver_metrics": {}, "findings": [], "limitations": ["Structural test fixture only."]}


def add_structural(monkeypatch, book, review, session="session-one", **kwargs):
    monkeypatch.setattr(cases, "review_experiment", lambda *args, **kw: copy.deepcopy(review))
    return cases.add_case(book, "structural-only", session_id=session, **kwargs)


def test_casebook_annotations_are_explicit_and_review_immutable(tmp_path, monkeypatch):
    book = tmp_path / "book"
    assert cases.create_casebook(book)["revision"] == 0
    review = structural_review(tmp_path)
    case = add_structural(monkeypatch, book, review)
    review_path = book / case["review_file"]
    original = review_path.read_bytes()
    assert case["annotation"]["label"] == "unknown"
    updated = cases.annotate_case(book, case["case_id"], label="healthy", notes="Operator reason in a structural fixture.", include_for_training=True)
    assert len(updated["annotations"]) == 2
    assert updated["annotation"]["revision"] == 2
    assert review_path.read_bytes() == original
    audit = cases.audit_casebook(book)
    assert audit["status"] == "verified"
    assert audit["cases"][0]["training_eligible"]


@pytest.mark.parametrize("kwargs,match", [({"session_id": ""}, "session"),
    ({"label": "healthy"}, "reason"), ({"label": "fake"}, "label"),
    ({"include_for_training": True}, "unknown"), ({"include_for_training": 1}, "boolean")])
def test_operator_constraints(tmp_path, monkeypatch, kwargs, match):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    monkeypatch.setattr(cases, "review_experiment", lambda *args, **kw: review)
    values = {"session_id": "one", **kwargs}
    with pytest.raises(ValueError, match=match):
        cases.add_case(book, "fixture", **values)
    assert not list((book / "reviews").iterdir())


def test_duplicate_capture_processing_is_preserved_but_not_independent(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    first = add_structural(monkeypatch, book, structural_review(tmp_path, "original", raw_identity="same"),
                           label="healthy", notes="Fixture annotation", include_for_training=True)
    second_review = structural_review(tmp_path, "reprocessed", raw_identity="same")
    with pytest.raises(ValueError, match="different bench sessions"):
        add_structural(monkeypatch, book, second_review, session="another")
    with pytest.raises(ValueError, match="Deselect"):
        add_structural(monkeypatch, book, second_review, label="healthy", notes="Fixture annotation", include_for_training=True)
    second = add_structural(monkeypatch, book, second_review)
    with pytest.raises(ValueError, match="Deselect"):
        cases.annotate_case(book, second["case_id"], label="healthy", notes="Fixture", include_for_training=True)
    cases.annotate_case(book, first["case_id"], label="healthy", notes="Prefer newer processing", include_for_training=False)
    cases.annotate_case(book, second["case_id"], label="healthy", notes="Explicitly selected newer processing", include_for_training=True)
    records = cases.dataset_records(book)
    assert records["counts"]["independent_captures"] == 1
    assert len(records["records"]) == 1
    assert len(records["excluded"]) == 1


def test_duplicate_evidence_id_is_deterministic(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    add_structural(monkeypatch, book, review)
    with pytest.raises(ValueError, match="already recorded"):
        add_structural(monkeypatch, book, review)


@pytest.mark.parametrize("kind", ["original", "review", "missing"])
def test_tampered_or_missing_originals_stop_export(tmp_path, monkeypatch, kind):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    case = add_structural(monkeypatch, book, review, label="healthy", notes="Fixture", include_for_training=True)
    if kind == "review":
        (book / case["review_file"]).write_text("{}")
    elif kind == "original":
        Path(review["source"]["files"][1]["path"]).write_bytes(b"changed")
    else:
        Path(review["source"]["files"][1]["path"]).unlink()
    assert cases.audit_casebook(book)["status"] == "integrity_failed"
    with pytest.raises(ValueError, match="integrity failed"):
        cases.export_dataset(book, tmp_path / "dataset.json")
    assert not (tmp_path / "dataset.json").exists()


def test_unknown_pipeline_and_missing_features_remain_excluded_without_imputation(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    missing = structural_review(tmp_path, "missing")
    missing["blind_features"]["snr_est_db"] = None
    add_structural(monkeypatch, book, missing, label="low_snr", notes="Fixture", include_for_training=True)
    add_structural(monkeypatch, book, structural_review(tmp_path, "pipeline"), label="transport_fault", notes="Recorded transport error fixture", include_for_training=True)
    add_structural(monkeypatch, book, structural_review(tmp_path, "unknown"))
    exported = cases.export_dataset(book, tmp_path / "export")
    assert exported["records"] == []
    assert len(exported["excluded"]) == 3
    assert "missing_blind_features:snr_est_db" in exported["excluded"][0]["reasons"]
    assert exported["excluded"][0]["features"]["snr_est_db"] is None
    assert cases.list_cases(book)["cases"][0]["blind_features"]["snr_est_db"] is None


def test_pipeline_findings_and_invalid_ranges_are_excluded(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    review["findings"] = [{"code": "held_sample_padding"}]
    review["blind_features"]["clipping_fraction"] = 2
    add_structural(monkeypatch, book, review, label="clipping", notes="Fixture", include_for_training=True)
    reasons = cases.audit_casebook(book)["cases"][0]["exclusion_reasons"]
    assert "pipeline_integrity:held_sample_padding" in reasons
    assert "invalid_blind_feature_range:clipping_fraction" in reasons


def test_public_preamble_acquisition_does_not_require_valid_payload_crc(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path, "synchronized")
    review["outcome"]["receiver_status"] = "synchronized_no_valid_frames"
    review["receiver_metrics"] = {"status": "synchronized_no_valid_frames", "packets_ok": 0,
                                  "known_payload_complete": False, "acquisition_valid": True,
                                  "synchronization_valid": True}
    add_structural(monkeypatch, book, review, label="low_snr", notes="Explicit structural fixture label", include_for_training=True)
    assert cases.audit_casebook(book)["cases"][0]["training_eligible"] is True
    failed = structural_review(tmp_path, "acquisition-failed")
    failed["outcome"]["receiver_status"] = "acquisition_failed"
    add_structural(monkeypatch, book, failed, label="low_snr", notes="Fixture", include_for_training=True)
    assert cases.audit_casebook(book)["cases"][1]["training_eligible"] is False


def test_legacy_extrema_proxy_cannot_enter_new_hardware_dataset(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    review.pop("feature_basis")
    add_structural(monkeypatch, book, review, label="healthy", notes="Fixture", include_for_training=True)
    assert "incompatible_clipping_feature_definition" in cases.audit_casebook(book)["cases"][0]["exclusion_reasons"]


def test_export_is_exact_six_feature_whitelist(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    review = structural_review(tmp_path)
    review["blind_features"].update(ber=0, reference_evm_pct=0, label="healthy", session_id="leak", version=99)
    add_structural(monkeypatch, book, review, label="healthy", notes="Fixture", include_for_training=True)
    result = cases.export_dataset(book, tmp_path / "dataset.json")
    assert list(result["records"][0]["features"]) == list(cases.HARDWARE_FEATURE_NAMES)
    assert json.loads(Path(result["dataset_path"]).read_text())["counts"]["eligible_cases"] == 1


def test_synthetic_rejection_from_review_is_not_bypassed(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    def reject(*args, **kwargs):
        raise ValueError("Hardware review rejects rehearsal or synthetic experiments.")
    monkeypatch.setattr(cases, "review_experiment", reject)
    with pytest.raises(ValueError, match="synthetic"):
        cases.add_case(book, "synthetic", session_id="explicit")
    assert cases.audit_casebook(book)["counts"]["cases"] == 0
