"""Saved-evidence commands must stay outside every device entry point."""
import json
import sys
import types

import pytest

from rf_link_lab.cli import main


@pytest.fixture(autouse=True)
def no_device(monkeypatch):
    import rf_link_lab.hardware as hardware
    import serial
    def forbidden(*args, **kwargs):
        pytest.fail("An evidence command tried to access a device")
    monkeypatch.setattr(hardware, "probe_bladerf", forbidden)
    monkeypatch.setattr(hardware, "capture_rx", forbidden)
    monkeypatch.setattr(serial, "Serial", forbidden)


def stub(monkeypatch, name, **functions):
    module = types.ModuleType("rf_link_lab." + name)
    for key, function in functions.items():
        setattr(module, key, function)
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_review_passes_explicit_selected_receiver_and_preserves_original_outcome(tmp_path, monkeypatch, capsys):
    calls = []
    review = {"outcome": {"recorded_known_payload_complete": False, "selected_known_payload_complete": True}}
    def inspect(path, *, receiver_run):
        calls.append((path, receiver_run))
        return review
    def export(value, output, *, diagnosis):
        assert value is review and diagnosis is None
        return {"report": str(output / "report.html")}
    stub(monkeypatch, "hardware_review", review_experiment=inspect)
    stub(monkeypatch, "hardware_reports", save_hardware_review=export)
    original, selected, output = (tmp_path / name for name in ("physical", "corrected", "report"))
    assert main(["hardware-review", str(original), "--receiver-run", str(selected), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [(original, selected)]
    assert result["review"]["outcome"] == review["outcome"]
    assert "report.html" in result["export"]["report"]


def test_case_add_starts_unknown_and_not_selected_for_training(tmp_path, monkeypatch, capsys):
    captured = {}
    def add(path, experiment, **kwargs):
        captured.update(kwargs)
        return {"case_id": "new-case", "label": kwargs["label"]}
    stub(monkeypatch, "hardware_cases", add_case=add)
    assert main(["casebook-add", str(tmp_path / "book"), str(tmp_path / "experiment"),
                 "--session-id", "physical-20261020"]) == 0
    assert json.loads(capsys.readouterr().out)["label"] == "unknown"
    assert captured == {"session_id": "physical-20261020", "receiver_run": None,
                        "label": "unknown", "notes": "", "include_for_training": False}


@pytest.mark.parametrize("command", ["hardware-readiness", "hardware-train"])
def test_insufficient_data_has_explicit_non_success_exit_and_no_device_access(tmp_path, monkeypatch, capsys, command):
    blocked = {"status": "blocked", "reasons": ["Only one independent physical session."]}
    stub(monkeypatch, "hardware_learning", training_readiness=lambda *a, **k: blocked,
         train_hardware_model=lambda *a, **k: blocked)
    args = [command, str(tmp_path / "book")]
    if command == "hardware-train":
        args += ["--output", str(tmp_path / "model")]
    assert main(args) == 3
    assert json.loads(capsys.readouterr().out) == blocked
    assert not (tmp_path / "model/model.json").exists()


def test_prediction_reverifies_source_and_unknown_does_not_mean_healthy(tmp_path, monkeypatch, capsys):
    order = []
    def inspect(path, **kwargs):
        order.append("verified")
        return {"source": {"raw_rx_sha256": "a" * 64}}
    def diagnose(review, model):
        assert order == ["verified"]
        order.append("prediction")
        return {"label": "unknown", "reason": "Outside the qualified hardware envelope"}
    stub(monkeypatch, "hardware_review", review_experiment=inspect)
    stub(monkeypatch, "hardware_learning", diagnose_hardware=diagnose)
    assert main(["hardware-diagnose", str(tmp_path / "physical"), "--model", str(tmp_path / "model.json")]) == 0
    assert order == ["verified", "prediction"]
    assert json.loads(capsys.readouterr().out)["diagnosis"]["label"] == "unknown"


def test_tampered_input_is_a_cli_error_and_never_saved_as_success(tmp_path, monkeypatch):
    def inspect(*args, **kwargs):
        raise ValueError("Original capture hash mismatch")
    stub(monkeypatch, "hardware_review", review_experiment=inspect)
    with pytest.raises(SystemExit) as result:
        main(["hardware-review", str(tmp_path / "bad"), "--output", str(tmp_path / "review")])
    assert result.value.code == 2
    assert not (tmp_path / "review").exists()
