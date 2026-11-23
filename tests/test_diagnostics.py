"""Tests of evidence boundaries, grouped evaluation and portable inference."""
import copy
import json

import numpy as np
import pytest

from rf_link_lab.dataset import CLASS_NAMES, FEATURE_NAMES, make_dataset
from rf_link_lab.diagnostics import (diagnose, load_model, propose_followup,
                                     rules_diagnose, train_demo_model)
from rf_link_lab.simulation import SimulationConfig, run_simulation


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    directory = tmp_path_factory.mktemp("synthetic_model")
    report = train_demo_model(directory, sessions=8, windows_per_class=1, seed=2026)
    return directory, report, load_model(directory / "model.json")


def test_dataset_is_reproducible_and_session_disjoint():
    first, manifest = make_dataset(sessions=8, windows_per_class=1, seed=11)
    repeated, repeated_manifest = make_dataset(sessions=8, windows_per_class=1, seed=11)
    assert first == repeated
    assert manifest == repeated_manifest
    groups = [set(value) for value in manifest["split_sessions"].values()]
    assert all(groups[i].isdisjoint(groups[j]) for i in range(3) for j in range(i+1, 3))
    assert sum(map(len, groups)) == 8
    for group in groups:
        for session in group:
            rows = [record for record in first if record["session_id"] == session]
            assert {record["label"] for record in rows} == set(CLASS_NAMES)
            assert len({record["split"] for record in rows}) == 1
            for key in ["gain", "phase_deg", "delay_samples", "correct_cfo"]:
                assert len({record["audit_config"][key] for record in rows}) == 1
    # Deliberately separated audit fields never appear in the feature vector.
    assert set(FEATURE_NAMES).isdisjoint({"seed", "label", "snr_db", "cfo_hz", "session_id", "audit_config"})


def test_portable_model_matches_training_implementation(trained):
    directory, report, model = trained
    assert report["portable_prediction_max_abs_difference"] < 1e-12
    assert report["test_count"] == 8
    assert 0 <= report["model_macro_f1"] <= 1
    assert 0 <= report["rules_macro_f1"] <= 1
    assert len(report["model"]["confusion_matrix"]) == 4
    assert (directory / "dataset.csv").is_file()
    assert (directory / "split-manifest.json").is_file()
    assert json.loads((directory / "evaluation.json").read_text())["source"] == "synthetic_simulator_only"
    values = run_simulation(SimulationConfig(seed=505, frames=6, clip_level=4)).features
    probability = model.predict_proba([[values[name] for name in FEATURE_NAMES]])[0]
    assert np.isclose(probability.sum(), 1)
    assert np.all(probability >= 0)


def test_diagnosis_ignores_labels_and_ground_truth_metadata(trained):
    model = trained[2]
    measured = run_simulation(SimulationConfig(seed=506, frames=6, clip_level=4)).features
    spoofed = {**measured, "label": "clipping", "snr_db": -999, "cfo_hz": 999999,
               "audit_config": {"label": "low_snr"}}
    assert diagnose(measured, model) == diagnose(spoofed, model)


def test_outside_envelope_and_mixed_signatures_abstain(trained):
    model = trained[2]
    measured = run_simulation(SimulationConfig(seed=507, frames=6, clip_level=4)).features
    outside = diagnose({**measured, "cfo_abs_hz": 1e9}, model)
    assert outside["label"] == "unknown"
    assert "envelope" in outside["unknown_reason"]
    mixed = diagnose({**measured, "cfo_abs_hz": 1800, "clipping_fraction": .9,
                      "snr_est_db": 24}, model)
    assert mixed["label"] == "unknown"
    assert mixed["unknown_reason"]
    assert len(mixed["ranked_signatures"]) == 4


def test_low_tree_support_abstains(trained):
    model = copy.deepcopy(trained[2])
    model.payload["acceptance"]["minimum_support"] = 1.01
    measured = run_simulation(SimulationConfig(seed=508, frames=6, clip_level=4)).features
    result = diagnose(measured, model)
    assert result["label"] == "unknown"
    assert "support" in result["unknown_reason"]


def test_rules_and_frequency_estimator_reliability():
    clean = dict(cfo_abs_hz=2, snr_est_db=28, clipping_fraction=0)
    assert rules_diagnose(clean) == "healthy"
    assert rules_diagnose({**clean, "cfo_abs_hz": 1800}) == "frequency_offset"
    assert rules_diagnose({**clean, "snr_est_db": 4}) == "low_snr"
    assert rules_diagnose({**clean, "clipping_fraction": .9}) == "clipping"
    # A noisy pilot's frequency estimate alone is not a second-fault proof.
    assert rules_diagnose({**clean, "snr_est_db": 4, "cfo_abs_hz": 800}) == "low_snr"


@pytest.mark.parametrize("label,field", [("frequency_offset", "cfo_hz"),
                                         ("low_snr", "snr_db"), ("clipping", "gain")])
def test_followup_changes_exactly_one_control(label, field):
    original = SimulationConfig(cfo_hz=1800, snr_db=4, gain=.65, clip_level=.23)
    result = propose_followup(original, label)
    assert result["simulation_only"] is True
    assert set(result["changes"]) == {field}
    assert result["expected_observation"]
    assert getattr(original, field) != result["config"][field]


def test_rejects_missing_or_nonfinite_measurements(trained):
    with pytest.raises(ValueError, match="six numeric"):
        diagnose({}, trained[2])
    values = dict.fromkeys(FEATURE_NAMES, 0.0)
    values["snr_est_db"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        diagnose(values, trained[2])


def test_malformed_model_rejected_without_execution(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"format": "pickle"}))
    with pytest.raises(ValueError, match="Unsupported"):
        load_model(path)


def test_acquisition_failures_are_audited_not_filled(monkeypatch):
    import rf_link_lab.dataset as dataset
    original=dataset.run_simulation
    def fail_one(config):
        result=original(config)
        if config.seed==11:
            result.features['evm_pct']=None
            result.metrics['status']='acquisition_failed'
        return result
    monkeypatch.setattr(dataset,'run_simulation',fail_one)
    records,manifest=dataset.make_dataset(sessions=8,windows_per_class=1,seed=11)
    assert manifest['attempted_record_count']==32
    assert manifest['excluded_measurement_count']==1
    assert len(records)==31
    assert manifest['excluded_measurements'][0]['receiver_status']=='acquisition_failed'
    assert manifest['measurement_coverage']==31/32
