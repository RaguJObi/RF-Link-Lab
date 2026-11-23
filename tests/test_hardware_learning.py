"""Structural unit fixtures exercise software; they are not hardware models."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from rf_link_lab import hardware_cases as cases
from rf_link_lab import hardware_learning as learning
from test_hardware_cases import add_structural, structural_review


def structural_dataset(sessions=12):
    records = []
    for session in range(sessions):
        for label in ("healthy", "frequency_offset"):
            identity = f"{session}-{label}"
            import hashlib
            sha = hashlib.sha256(identity.encode()).hexdigest()
            records.append({"case_id": identity, "raw_rx_sha256": sha, "review_sha256": sha,
                            "session_id": f"session-{session:02}", "label": label,
                            "features": {"cfo_abs_hz": 2. + session if label == "healthy" else 1400. + session,
                                         "pilot_evm_pct": 10. + session / 10, "snr_est_db": 28. + session / 10,
                                         "clipping_fraction": 0., "amplitude_cv": .2, "spectral_flatness": .05}})
    return {"schema": cases.DATASET_SCHEMA, "status": "verified", "records": records, "excluded": [],
            "counts": {"eligible_cases": len(records), "independent_captures": len(records),
                       "independent_sessions": sessions, "rf_classes": 2},
            "fixture_warning": "Fabricated structural unit-test rows; no physical model is established."}


@pytest.fixture(scope="module")
def structural_trained(tmp_path_factory):
    output = tmp_path_factory.mktemp("structural-model-only")
    from unittest.mock import patch
    with patch.object(learning, "dataset_records", return_value=structural_dataset()):
        result = learning.train_hardware_model("structural-casebook", output)
    return output, result


def test_single_real_session_group_cannot_train_even_with_many_captures(tmp_path, monkeypatch):
    book = tmp_path / "book"
    cases.create_casebook(book)
    for index in range(12):
        label = "healthy" if index % 2 else "frequency_offset"
        review = structural_review(tmp_path, str(index))
        add_structural(monkeypatch, book, review, session="physical-20261020", label=label,
                       notes="Structural test only; not operator hardware ground truth", include_for_training=True)
    readiness = learning.training_readiness(book)
    assert readiness["status"] == "blocked"
    assert readiness["counts"]["independent_sessions"] == 1
    output = tmp_path / "blocked"
    result = learning.train_hardware_model(book, output)
    assert result["status"] == "blocked"
    assert {path.name for path in output.iterdir()} == {"readiness.json"}


def test_group_split_has_no_capture_or_session_leakage_and_is_deterministic(monkeypatch):
    dataset = structural_dataset()
    monkeypatch.setattr(learning, "dataset_records", lambda path: dataset)
    first = learning.training_readiness("fixture")
    second = learning.training_readiness("fixture")
    assert first == second
    assert first["status"] == "ready"
    groups = list(map(set, first["split_sessions"].values()))
    assert sorted(map(len, groups)) == [3, 3, 6]
    assert all(groups[i].isdisjoint(groups[j]) for i in range(3) for j in range(i + 1, 3))
    for sessions in groups:
        for label in ("healthy", "frequency_offset"):
            assert len({row["session_id"] for row in dataset["records"] if row["label"] == label and row["session_id"] in sessions}) >= 2


def test_rare_class_blocks_even_if_total_sessions_is_large(monkeypatch):
    dataset = structural_dataset(20)
    dataset["records"] = [row for row in dataset["records"] if row["label"] == "healthy" or row["session_id"] == "session-00"]
    monkeypatch.setattr(learning, "dataset_records", lambda path: dataset)
    result = learning.training_readiness("fixture")
    assert result["status"] == "blocked"
    assert any("frequency_offset" in reason for reason in result["reasons"])


def test_disjoint_class_sessions_can_use_balanced_whole_session_split(monkeypatch):
    dataset = structural_dataset(24)
    dataset["records"] = [row for row in dataset["records"] if (row["label"] == "healthy") == (int(row["session_id"][-2:]) < 12)]
    monkeypatch.setattr(learning, "dataset_records", lambda path: dataset)
    result = learning.training_readiness("fixture")
    assert result["status"] == "ready"
    for split, sessions in result["split_sessions"].items():
        for label in ("healthy", "frequency_offset"):
            assert len({row["session_id"] for row in dataset["records"] if row["session_id"] in sessions and row["label"] == label}) >= 2


def test_constrained_split_can_depart_from_target_sizes_when_required(monkeypatch):
    dataset = structural_dataset(12)
    dataset["records"] = [row for row in dataset["records"] if (row["label"] == "healthy") == (int(row["session_id"][-2:]) < 6)]
    monkeypatch.setattr(learning, "dataset_records", lambda path: dataset)
    result = learning.training_readiness("fixture")
    assert result["status"] == "ready"
    assert sorted(map(len, result["split_sessions"].values())) == [4, 4, 4]


def test_duplicate_raw_capture_rows_are_never_counted_as_independent(monkeypatch):
    dataset = structural_dataset()
    dataset["records"][1]["raw_rx_sha256"] = dataset["records"][0]["raw_rx_sha256"]
    monkeypatch.setattr(learning, "dataset_records", lambda path: dataset)
    result = learning.training_readiness("fixture")
    assert result["status"] == "blocked"
    assert any("Duplicate original raw" in reason for reason in result["reasons"])


def test_model_is_distinct_data_only_and_portable_inference_matches(structural_trained):
    output, result = structural_trained
    assert result["status"] == "trained"
    evaluation = result["evaluation"]
    assert evaluation["portable_prediction_max_abs_difference"] < 1e-12
    assert evaluation["support_is_calibrated_probability"] is False
    assert 0 <= evaluation["coverage"] <= 1
    model = learning.load_hardware_model(output / "model.json")
    assert model.payload["schema"] == "rf-link-lab.hardware-forest.v1"
    assert model.payload["feature_names"] == list(cases.HARDWARE_FEATURE_NAMES)
    assert model.payload["hyperparameters"] == learning.MODEL_PARAMETERS
    assert {path.name for path in output.iterdir()} == {"model.json", "evaluation.json", "readiness.json", "dataset.json", "split-manifest.json"}


def test_test_sessions_are_not_used_in_validation_selection(tmp_path, monkeypatch):
    dataset = structural_dataset()
    monkeypatch.setattr(learning, "dataset_records", lambda path: copy.deepcopy(dataset))
    first = learning.train_hardware_model("fixture", tmp_path / "first")
    test_sessions = set(first["readiness"]["split_sessions"]["test"])
    for row in dataset["records"]:
        if row["session_id"] in test_sessions:
            row["features"]["cfo_abs_hz"] += 1e6
    second = learning.train_hardware_model("fixture", tmp_path / "second")
    first_model = json.loads(Path(first["model_path"]).read_text())
    second_model = json.loads(Path(second["model_path"]).read_text())
    # The full exported dataset hash must reflect changed held-out evidence,
    # while every learned/selected value remains independent of those rows.
    assert first_model["qualification"]["dataset_sha256"] != second_model["qualification"]["dataset_sha256"]
    first_model["qualification"].pop("dataset_sha256")
    second_model["qualification"].pop("dataset_sha256")
    assert first_model == second_model
    assert first["evaluation"]["validation_selection"] == second["evaluation"]["validation_selection"]
    assert second["evaluation"]["coverage"] == 0


def test_diagnosis_missing_mixed_outside_untrained_and_reference_leakage(tmp_path, structural_trained):
    review = structural_review(tmp_path)
    model = structural_trained[0] / "model.json"
    baseline = learning.diagnose_hardware(review, model)
    changed = copy.deepcopy(review)
    changed["receiver_metrics"].update(ber=1, known_payload_complete=False, reference_evm_pct=999, packets_ok=0)
    changed["source"].update(application_version="leak", experiment_path="leak")
    changed["blind_features"].update(label="clipping", session_id="leak")
    assert learning.diagnose_hardware(changed, model) == baseline
    missing = copy.deepcopy(review)
    missing["blind_features"]["snr_est_db"] = None
    assert learning.diagnose_hardware(missing, model)["label"] == "unknown"
    mixed = copy.deepcopy(review)
    mixed["blind_features"].update(cfo_abs_hz=1500, clipping_fraction=.5)
    assert "mixed_impairment_signatures" in learning.diagnose_hardware(mixed, model)["unknown_reasons"]
    outside = copy.deepcopy(review)
    outside["blind_features"]["cfo_abs_hz"] = 1e9
    assert any("envelope" in reason for reason in learning.diagnose_hardware(outside, model)["unknown_reasons"])
    untrained = copy.deepcopy(review)
    untrained["blind_features"]["snr_est_db"] = 2
    assert "fixed_rule_signature_is_an_untrained_class:low_snr" in learning.diagnose_hardware(untrained, model)["unknown_reasons"]


def test_no_model_is_explicitly_rules_only(tmp_path):
    result = learning.diagnose_hardware(structural_review(tmp_path))
    assert result["label"] == "unknown"
    assert result["support"] is None
    assert result["rule_baseline"] == "healthy"
    assert "hardware_model_not_supplied" in result["unknown_reasons"]


@pytest.mark.parametrize("mutation", ["missing", "overlap", "few_sessions", "duplicate_raw", "missing_class_coverage", "missing_hash"])
def test_unqualified_external_model_is_rejected(tmp_path, structural_trained, mutation):
    value = json.loads((structural_trained[0] / "model.json").read_text())
    if mutation == "missing":
        value.pop("split_sessions")
    elif mutation == "overlap":
        value["split_sessions"]["validation"][0] = value["split_sessions"]["train"][0]
    elif mutation == "few_sessions":
        value["split_sessions"]["train"] = value["split_sessions"]["train"][:2]
    elif mutation == "duplicate_raw":
        value["qualification"]["records"][1]["raw_rx_sha256"] = value["qualification"]["records"][0]["raw_rx_sha256"]
    elif mutation == "missing_class_coverage":
        value["qualification"]["records"] = [row for row in value["qualification"]["records"] if row["split"] != "validation" or row["label"] == "healthy"]
    else:
        value["qualification"].pop("dataset_sha256")
    path = tmp_path / "unqualified.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        learning.load_hardware_model(path)


def test_changed_export_sidecar_is_rejected(tmp_path, structural_trained):
    source = structural_trained[0] / "model.json"
    model = tmp_path / "model.json"
    model.write_bytes(source.read_bytes())
    (tmp_path / "dataset.json").write_text("{}")
    with pytest.raises(ValueError, match="sidecar hash"):
        learning.load_hardware_model(model)


@pytest.mark.parametrize("mutation", ["synthetic", "cycle", "out_of_bounds", "unreachable", "wrong_features", "nan", "bad_leaf", "wrong_source"])
def test_malformed_model_is_rejected_without_traversal(tmp_path, structural_trained, mutation):
    value = json.loads((structural_trained[0] / "model.json").read_text())
    if mutation == "synthetic":
        value["schema"] = "rf-link-lab-forest-v1"
    elif mutation == "wrong_features":
        value["feature_names"][0] = "ber"
    elif mutation == "wrong_source":
        value["source"] = "synthetic_simulator_only"
    elif mutation == "nan":
        value["acceptance"]["feature_bounds"]["snr_est_db"][0] = float("nan")
    elif mutation == "bad_leaf":
        value["trees"][0]["leaf_support"][0] = [-1, 2]
    elif mutation == "cycle":
        value["trees"][0]["children_left"][0] = 0
    elif mutation == "out_of_bounds":
        value["trees"][0]["children_right"][0] = 99999
    else:
        tree = value["trees"][0]
        for key in ("children_left", "children_right", "feature", "threshold", "leaf_support"):
            tree[key].append(tree[key][-1])
    path = tmp_path / "untrusted.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        learning.load_hardware_model(path)
