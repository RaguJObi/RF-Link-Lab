"""Reports preserve saved evidence and escape user-authored labels/notes."""
import hashlib
import json

import pytest

from rf_link_lab.hardware_reports import save_hardware_review, verify_hardware_review_report


def review():
    return {
        "schema": "rf-link-lab.hardware-review.v1",
        "source": {"experiment_path": '<script>alert("source")</script>'},
        "transport": {"cleanup_confirmed": True},
        "outcome": {"recorded_known_payload_complete": False, "selected_known_payload_complete": True,
                    "experiment_status": "capture_without_complete_payload", "receiver_status": "decoded"},
        "receiver_metrics": {"known_payload_frames_matched": 12, "bit_errors": 0},
        "blind_features": {"pilot_evm_pct": 29.46, "snr_est_db": None, "clipping_fraction": 0.0},
        "raw_adc_rail_measurement": {"definition_id": "raw_sc16q11_adc_rail_fraction_v1",
                                     "sample_count": 3000000, "rail_hit_samples": 0},
        "findings": [{"title": "<img src=x onerror=alert(1)>", "kind": "hypothesis",
                      "evidence": ["Both processing variants use the same raw SHA."],
                      "next_measurement": "Repeat with identical RF settings."}],
        "limitations": ["No calibrated RF performance established."],
    }


def test_review_export_preserves_partial_original_and_complete_selected_decode(tmp_path, monkeypatch):
    import rf_link_lab.hardware_reports as module
    original = review()
    result = save_hardware_review(original, tmp_path / "review")
    folder = tmp_path / "review"
    assert json.loads((folder / "review.json").read_text()) == original
    text = (folder / "report.html").read_text()
    assert "Original payload complete<strong>No</strong>" in text
    assert "Selected decode complete<strong>Yes</strong>" in text
    assert "Unavailable" in text and "29.46" in text
    assert "raw_sc16q11_adc_rail_fraction_v1" in text and "3000000" in text
    assert "<script>" not in text and "<img src=x" not in text
    assert "&lt;img" in text and "&lt;script&gt;" in text
    assert result["report"] == str(folder / "report.html")
    assert verify_hardware_review_report(folder)["status"] == "verified"
    monkeypatch.setattr(module, "__version__", "99.0.0")
    assert verify_hardware_review_report(folder)["status"] == "verified"
    with pytest.raises(FileExistsError):
        save_hardware_review(original, folder)


def test_modified_report_is_rejected_even_if_its_inventory_hash_is_rewritten(tmp_path):
    folder = tmp_path / "review"
    save_hardware_review(review(), folder)
    report = folder / "report.html"
    report.write_text(report.read_text().replace("Unavailable", "0.0"))
    manifest_path = folder / "export.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["report.html"] = {
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(), "bytes": report.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="differs"):
        verify_hardware_review_report(folder)


def test_report_byte_change_fails_integrity_and_synthetic_schema_is_not_accepted(tmp_path):
    folder = tmp_path / "review"
    save_hardware_review(review(), folder, diagnosis={"label": "unknown", "reason": "No qualified model"})
    with (folder / "review.json").open("a") as out:
        out.write(" ")
    with pytest.raises(ValueError, match="hash or size"):
        verify_hardware_review_report(folder)
    wrong = review()
    wrong["schema"] = "rf-link-lab-run-v2"
    with pytest.raises(ValueError):
        save_hardware_review(wrong, tmp_path / "wrong")
    assert not (tmp_path / "wrong").exists()
