"""Operator-labelled, hash-bound cases from preserved physical experiments.

Review JSON is immutable. Operator annotations are revisions, never labels
inferred from receiver results. Original evidence stays outside the casebook
and is verified again before every export or training-readiness assessment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import re

from .hardware_review import BLIND_FEATURE_NAMES, review_experiment

CASEBOOK_SCHEMA = "rf-link-lab.hardware-casebook.v1"
DATASET_SCHEMA = "rf-link-lab.hardware-dataset.v1"
LABELS = ("unknown", "healthy", "frequency_offset", "low_snr", "clipping",
          "transport_fault", "receiver_processing", "other")
RF_CLASS_NAMES = ("healthy", "frequency_offset", "low_snr", "clipping")
HARDWARE_FEATURE_NAMES = tuple(BLIND_FEATURE_NAMES)
CLIPPING_DEFINITION_ID = "raw_sc16q11_adc_rail_fraction_v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_CASE = re.compile(r"case-[0-9a-f]{24}")
_PIPELINE_CODES = frozenset(("cleanup_unconfirmed", "transport_not_completed",
                            "recorded_stack_mismatch", "held_sample_padding", "partial_sc16_sample",
                            "invalid_sc16q11_format"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")


def _digest(path):
    sha = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


def _root(path):
    path = Path(path).absolute()
    return path.parent if path.name == "casebook.json" else path


def _write_index(root, value):
    temporary = root / ".casebook.pending.json"
    temporary.write_bytes(_json_bytes(value))
    temporary.replace(root / "casebook.json")


def _read_index(path):
    root = _root(path)
    value = json.loads((root / "casebook.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != CASEBOOK_SCHEMA:
        raise ValueError("Unsupported hardware casebook schema.")
    if not isinstance(value.get("cases"), list) or type(value.get("revision")) is not int:
        raise ValueError("Invalid hardware casebook index.")
    seen, raw_sessions, selected = set(), {}, set()
    for case in value["cases"]:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not _CASE.fullmatch(case_id) or case_id in seen:
            raise ValueError("Duplicate or invalid hardware case ID.")
        seen.add(case_id)
        if case.get("review_file") != f"reviews/{case_id}.json" or not _SHA.fullmatch(str(case.get("review_sha256", ""))):
            raise ValueError("Invalid immutable review reference.")
        session = _session(case.get("session_id"))
        if session != case["session_id"]:
            raise ValueError("Stored bench session IDs must use their canonical trimmed form.")
        annotations = case.get("annotations")
        if not isinstance(annotations, list) or not annotations:
            raise ValueError("Hardware case has no operator annotation history.")
        previous = 0
        for annotation in annotations:
            _annotation(annotation.get("label"), annotation.get("notes"), annotation.get("include_for_training"))
            revision = annotation.get("revision")
            if type(revision) is not int or revision <= previous or revision > value["revision"]:
                raise ValueError("Invalid case annotation revision history.")
            previous = revision
        raw = case.get("raw_rx_sha256")
        if raw is not None:
            if not isinstance(raw, str) or not _SHA.fullmatch(raw):
                raise ValueError("Invalid original raw RX SHA256.")
            if raw in raw_sessions and raw_sessions[raw] != session:
                raise ValueError("The same raw RX capture cannot belong to different bench sessions.")
            raw_sessions[raw] = session
            if annotations[-1]["include_for_training"]:
                if raw in selected:
                    raise ValueError("Only one processing variant per raw RX capture may be selected for training.")
                selected.add(raw)
    return root, value


def _session(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128 or not value.isprintable():
        raise ValueError("Provide an explicit operator bench session ID (1 to 128 printable characters).")
    return value.strip()


def _annotation(label, notes, include):
    if label not in LABELS:
        raise ValueError("Unknown hardware case label.")
    if not isinstance(notes, str) or type(include) is not bool:
        raise ValueError("Annotation notes must be text and training selection must be a boolean.")
    if label != "unknown" and not notes.strip():
        raise ValueError("A known operator label requires a reason in notes.")
    if include and label == "unknown":
        raise ValueError("An unknown case cannot be selected for training.")
    return {"label": label, "notes": notes.strip(), "include_for_training": include}


def _case_result(case):
    return {**case, "annotation": dict(case["annotations"][-1])}


def create_casebook(path):
    """Create a casebook directory; an existing valid casebook is reused."""
    root = _root(path)
    if (root / "casebook.json").exists():
        return _read_index(root)[1]
    root.mkdir(parents=True, exist_ok=True)
    (root / "reviews").mkdir(exist_ok=True)
    value = {"schema": CASEBOOK_SCHEMA, "created_utc": _now(), "revision": 0,
             "evidence_policy": "Immutable reviews reference external original files. Preserve originals; copied metadata alone is insufficient for training.",
             "cases": []}
    _write_index(root, value)
    return value


def add_case(casebook, experiment, *, session_id, receiver_run=None, label="unknown",
             notes="", include_for_training=False):
    """Verify physical evidence, then preserve one immutable processing review."""
    root, index = _read_index(casebook)
    session = _session(session_id)
    annotation = _annotation(label, notes, include_for_training)
    review = review_experiment(experiment, receiver_run=receiver_run)
    source = review["source"]
    identity = {key: source.get(key) for key in ("experiment_sha256", "raw_rx_sha256",
                                                "receiver_manifest_sha256", "receiver_npz_sha256")}
    case_id = "case-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    for case in index["cases"]:
        if case["case_id"] == case_id:
            raise ValueError("This evidence variant is already recorded; use annotate_case to revise its annotation.")
        raw = source.get("raw_rx_sha256")
        if raw is not None and case.get("raw_rx_sha256") == raw:
            if case["session_id"] != session:
                raise ValueError("The same raw RX capture cannot belong to different bench sessions.")
            if include_for_training and case["annotations"][-1]["include_for_training"]:
                raise ValueError("Deselect the existing processing variant before selecting this raw capture for training.")
    data = _json_bytes(review)
    review_path = root / "reviews" / f"{case_id}.json"
    # Exclusive creation prevents accidental replacement of an immutable review.
    with review_path.open("xb") as stream:
        stream.write(data)
    index["revision"] += 1
    annotation.update(revision=index["revision"], created_utc=_now())
    case = {"case_id": case_id, "session_id": session,
            "review_file": f"reviews/{case_id}.json", "review_sha256": hashlib.sha256(data).hexdigest(),
            "raw_rx_sha256": source.get("raw_rx_sha256"), "annotations": [annotation]}
    index["cases"].append(case)
    _write_index(root, index)
    return _case_result(case)


def annotate_case(casebook, case_id, *, label, notes="", include_for_training=False):
    """Append an operator revision without editing the preserved review."""
    root, index = _read_index(casebook)
    annotation = _annotation(label, notes, include_for_training)
    selected = next((case for case in index["cases"] if case["case_id"] == case_id), None)
    if selected is None:
        raise ValueError("Hardware case ID was not found.")
    if include_for_training and selected.get("raw_rx_sha256") is not None:
        for case in index["cases"]:
            if case is not selected and case.get("raw_rx_sha256") == selected["raw_rx_sha256"] and case["annotations"][-1]["include_for_training"]:
                raise ValueError("Deselect the existing processing variant before selecting this raw capture for training.")
    index["revision"] += 1
    annotation.update(revision=index["revision"], created_utc=_now())
    selected["annotations"].append(annotation)
    _write_index(root, index)
    return _case_result(selected)


def review_exclusion_reasons(review):
    """Pipeline integrity gates; reference-dependent payload scores are unused."""
    reasons = []
    if review.get("schema") != "rf-link-lab.hardware-review.v1":
        return ["unsupported_review_schema"]
    source = review.get("source") or {}
    if not _SHA.fullmatch(str(source.get("raw_rx_sha256", ""))):
        reasons.append("missing_original_raw_rx")
    outcome = review.get("outcome") or {}
    if outcome.get("physical_tx_requested") is not True:
        reasons.append("no_recorded_physical_transmission")
    if outcome.get("experiment_status") in ("failed", "cancelled"):
        reasons.append("transport_did_not_complete")
    if outcome.get("receiver_status") not in ("decoded", "synchronized_no_valid_frames"):
        reasons.append("receiver_acquisition_incomplete")
    metrics = review.get("receiver_metrics") or {}
    for flag in ("acquisition_valid", "synchronization_valid"):
        if ((flag in metrics and metrics[flag] is not True)
                or (flag in outcome and outcome[flag] is not True)):
            reasons.append("receiver_" + flag + "_not_valid")
    definition = ((review.get("feature_basis") or {}).get("clipping_fraction") or {}).get("definition_id")
    if definition != CLIPPING_DEFINITION_ID:
        reasons.append("incompatible_clipping_feature_definition")
    for finding in review.get("findings", []):
        if finding.get("code") in _PIPELINE_CODES:
            reasons.append("pipeline_integrity:" + finding["code"])
    features = review.get("blind_features") or {}
    missing = [name for name in HARDWARE_FEATURE_NAMES
               if not isinstance(features.get(name), Real) or isinstance(features.get(name), bool)
               or not math.isfinite(features[name])]
    if missing:
        reasons.append("missing_blind_features:" + ",".join(missing))
    else:
        invalid = [name for name in ("cfo_abs_hz", "pilot_evm_pct", "amplitude_cv") if features[name] < 0]
        invalid.extend(name for name in ("clipping_fraction", "spectral_flatness") if not 0 <= features[name] <= 1)
        if invalid:
            reasons.append("invalid_blind_feature_range:" + ",".join(invalid))
    return reasons


def _verify_case(root, case):
    problems, review = [], None
    path = root / case["review_file"]
    try:
        sha, _ = _digest(path)
        if sha != case["review_sha256"]:
            raise ValueError("immutable review SHA256 mismatch")
        review = json.loads(path.read_text(encoding="utf-8"))
        if review.get("schema") != "rf-link-lab.hardware-review.v1":
            raise ValueError("unsupported review schema")
        source = review.get("source") or {}
        if source.get("raw_rx_sha256") != case.get("raw_rx_sha256"):
            raise ValueError("review/index raw capture identity mismatch")
        files = source.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("missing original source file inventory")
        verified = {}
        for item in files:
            original = Path(item["path"])
            if not original.is_absolute():
                raise ValueError("original source paths must be absolute")
            expected = item.get("sha256")
            if not isinstance(expected, str) or not _SHA.fullmatch(expected) or type(item.get("bytes")) is not int or item["bytes"] < 0:
                raise ValueError("invalid original source inventory entry")
            actual, size = _digest(original)
            if actual != expected or size != item["bytes"]:
                problems.append("Original source changed: " + str(original))
            verified[str(original)] = expected
        for key, hash_key in (("experiment_path", "experiment_sha256"),
                              ("receiver_manifest_path", "receiver_manifest_sha256")):
            if source.get(key) is not None and verified.get(str(Path(source[key]))) != source.get(hash_key):
                problems.append("Source identity is absent from inventory: " + key)
        raw = source.get("raw_rx_sha256")
        if raw is not None and raw not in verified.values():
            problems.append("Original raw RX identity is absent from inventory.")
        npz = source.get("receiver_npz_sha256")
        if npz is not None and npz not in verified.values():
            problems.append("Receiver NPZ identity is absent from inventory.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        problems.append(str(error))
    return review, problems


def audit_casebook(casebook):
    """Verify immutable reviews and all external originals, without writes."""
    root, index = _read_index(casebook)
    cases = []
    for case in index["cases"]:
        review, problems = _verify_case(root, case)
        annotation = case["annotations"][-1]
        excluded = []
        if problems:
            excluded.append("evidence_integrity_failed")
        if not annotation["include_for_training"]:
            excluded.append("not_explicitly_selected")
        if annotation["label"] not in RF_CLASS_NAMES:
            excluded.append("unknown_label" if annotation["label"] == "unknown" else "pipeline_or_other_label_excluded_from_rf_model")
        if review is not None:
            excluded.extend(review_exclusion_reasons(review))
        else:
            excluded.append("unreadable_review")
        cases.append({**_case_result(case), "integrity_ok": not problems, "problems": problems,
                      "training_eligible": not excluded, "exclusion_reasons": excluded,
                      "blind_features": (review or {}).get("blind_features", {}),
                      "outcome": (review or {}).get("outcome", {})})
    return {"schema": "rf-link-lab.hardware-casebook-audit.v1", "status": "verified" if all(row["integrity_ok"] for row in cases) else "integrity_failed",
            "casebook_path": str(root), "revision": index["revision"], "cases": cases,
            "counts": {"cases": len(cases), "integrity_failed": sum(not row["integrity_ok"] for row in cases),
                       "training_eligible": sum(row["training_eligible"] for row in cases),
                       "independent_captures": len({row["raw_rx_sha256"] for row in cases if row["raw_rx_sha256"] is not None}),
                       "declared_bench_sessions": len({row["session_id"] for row in cases})}}


def list_cases(casebook):
    return audit_casebook(casebook)


def dataset_records(casebook):
    """Return verified selected records and explicit exclusions; never impute."""
    audit = audit_casebook(casebook)
    records, excluded = [], []
    for case in audit["cases"]:
        if not case["training_eligible"]:
            excluded.append({"case_id": case["case_id"], "session_id": case["session_id"],
                             "raw_rx_sha256": case["raw_rx_sha256"], "label": case["annotation"]["label"],
                             "features": {name: (float(case["blind_features"][name])
                                                  if isinstance(case["blind_features"].get(name), Real)
                                                  and not isinstance(case["blind_features"].get(name), bool)
                                                  and math.isfinite(case["blind_features"][name]) else None)
                                          for name in HARDWARE_FEATURE_NAMES},
                             "reasons": case["exclusion_reasons"]})
            continue
        records.append({"case_id": case["case_id"], "session_id": case["session_id"],
                        "raw_rx_sha256": case["raw_rx_sha256"], "review_sha256": case["review_sha256"],
                        "label": case["annotation"]["label"],
                        "features": {name: float(case["blind_features"][name]) for name in HARDWARE_FEATURE_NAMES}})
    return {"schema": DATASET_SCHEMA, "status": "verified" if audit["status"] == "verified" else "integrity_failed",
            "casebook_path": audit["casebook_path"], "casebook_revision": audit["revision"],
            "feature_names": list(HARDWARE_FEATURE_NAMES), "records": records, "excluded": excluded,
            "feature_definition_ids": {"clipping_fraction": CLIPPING_DEFINITION_ID},
            "counts": {"eligible_cases": len(records), "excluded_cases": len(excluded),
                       "independent_captures": len({row["raw_rx_sha256"] for row in records}),
                       "independent_sessions": len({row["session_id"] for row in records}),
                       "rf_classes": len({row["label"] for row in records})},
            "limitations": ["Labels and physical origin are operator declarations, not authenticated ground truth.",
                            "Original source files must still verify before training. Processing variants are not independent captures."]}


def export_dataset(casebook, output):
    """Export JSON features plus explicit excluded cases; integrity failure stops export."""
    result = dataset_records(casebook)
    if result["status"] != "verified":
        raise ValueError("Casebook evidence integrity failed; restore external originals before exporting.")
    output = Path(output)
    path = output if output.suffix.lower() == ".json" else output / "dataset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("Dataset output already exists; choose a new export path.")
    result["dataset_path"] = str(path.absolute())
    path.write_bytes(_json_bytes(result))
    return result
