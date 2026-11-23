"""Review the retained 20 October hardware cases without opening any device.

Original captures are intentionally external to Git. This helper requires the
locally retained evidence and writes only a new output directory. It does not
decode, relabel, transmit, simulate or invent independent bench sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rf_link_lab.hardware_cases import add_case, audit_casebook, create_casebook, export_dataset
from rf_link_lab.hardware_learning import diagnose_hardware, train_hardware_model
from rf_link_lab.hardware_reports import save_hardware_review, verify_hardware_review_report
from rf_link_lab.hardware_review import review_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New output folder; originals must be retained locally")
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists():
        parser.error("Choose a new output directory.")
    bench = ROOT / "artifacts/bench"
    original = bench / "physical-rf-20261020-buffer32768-imported/rf-run-4"
    selected = bench / "physical-rf-20261020-buffer32768-imported/rf-run-4-carrier-tracking-reprocess/receiver"
    cases = [
        (bench / "physical-rf-20261020-attempt1-imported/rf-run", None),
        (bench / "physical-rf-20261020-attempt2-imported/rf-run-2", None),
        (bench / "physical-rf-20261020-matched-imported/rf-run-3", None),
        (original, None), (original, selected),
    ]
    for experiment, receiver in cases:
        if not (experiment / "experiment.json").is_file() or (receiver is not None and not receiver.is_dir()):
            parser.error("The original saved evidence is required: " + str(experiment))
    # Verify all sources before creating any new case/report files.
    reviews = [review_experiment(experiment, receiver_run=receiver) for experiment, receiver in cases]
    before = {item["path"]: item["sha256"] for review in reviews for item in review["source"]["files"]}
    output.mkdir(parents=True, exist_ok=False)
    casebook = output / "casebook"
    create_casebook(casebook)
    for experiment, receiver in cases:
        add_case(casebook, experiment, session_id="physical-20261020", receiver_run=receiver,
                 label="unknown", notes="Retained historical evidence; no operator training label assigned.",
                 include_for_training=False)
    review = reviews[-1]
    diagnosis = diagnose_hardware(review)
    report = save_hardware_review(review, output / "selected-review-final", diagnosis=diagnosis)
    verified_report = verify_hardware_review_report(output / "selected-review-final")
    audit = audit_casebook(casebook)
    export = export_dataset(casebook, output / "dataset.json")
    training = train_hardware_model(casebook, output / "training-blocked")
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert unchanged and audit["status"] == "verified"
    assert audit["counts"]["cases"] == 5
    assert audit["counts"]["independent_captures"] == 3
    assert audit["counts"]["declared_bench_sessions"] == 1
    assert audit["counts"]["training_eligible"] == 0
    assert training["status"] == "blocked" and not (output / "training-blocked/model.json").exists()
    assert review["outcome"]["recorded_known_payload_complete"] is False
    assert review["outcome"]["selected_known_payload_complete"] is True
    assert review["raw_adc_rail_measurement"]["sample_count"] == 3_000_000
    assert review["raw_adc_rail_measurement"]["rail_hit_samples"] == 0
    result = {
        "schema": "rf-link-lab.saved-hardware-ai-check.v1",
        "client_local_date": "2026-10-21", "source_files_unchanged": unchanged,
        "source_files_verified": len(before), "casebook": str(casebook),
        "audit": audit, "dataset": export, "review_report": report,
        "review_export_verification": verified_report, "training": training,
        "hardware_operations": 0, "new_rf_transmissions": 0,
        "scope": "Saved real evidence review only; no physical model or generalization score produced.",
    }
    (output / "saved-evidence-check.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "counts": audit["counts"], "source_files_unchanged": unchanged,
                      "report": report["report"], "training_status": training["status"]}, indent=2))


if __name__ == "__main__":
    main()
