"""Session-separated learning from explicitly labelled physical bench evidence.

No simulator, synthetic model, executable model format or device interface is
used. With insufficient independent sessions, training writes readiness only.
Tree support is an uncalibrated decision statistic, never a fault probability.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np

from .hardware_cases import (CLIPPING_DEFINITION_ID, HARDWARE_FEATURE_NAMES, RF_CLASS_NAMES, _json_bytes,
                             dataset_records, review_exclusion_reasons)

MODEL_SCHEMA = "rf-link-lab.hardware-forest.v1"
MODEL_PARAMETERS = {"n_estimators": 64, "max_depth": 5, "min_samples_leaf": 2,
                    "class_weight": "balanced", "n_jobs": 1}
# Initial engineering screen for an entire finite capture, including guards
# and idle samples. This is deliberately distinct from the synthetic model's
# extrema proxy and has not been qualified as a physical-fault threshold.
RULE_THRESHOLDS = {"cfo_abs_hz": 500.0, "snr_est_db": 12.0, "clipping_fraction": 1e-4}
_SPLITS = ("train", "validation", "test")
_SHA = re.compile(r"[0-9a-f]{64}")


def _signatures(features):
    return {"frequency_offset": features["cfo_abs_hz"] > RULE_THRESHOLDS["cfo_abs_hz"] and features["snr_est_db"] >= RULE_THRESHOLDS["snr_est_db"],
            "low_snr": features["snr_est_db"] < RULE_THRESHOLDS["snr_est_db"],
            "clipping": features["clipping_fraction"] > RULE_THRESHOLDS["clipping_fraction"]}


def hardware_rules_diagnose(features):
    """Fixed hardware baseline using only the same reference-independent inputs."""
    flags = _signatures(features)
    return next((name for name in ("clipping", "frequency_offset", "low_snr") if flags[name]), "healthy")


def _split_sessions(records, seed):
    """Assign whole groups with >=2 sessions per class in each split.

    A small binary assignment problem avoids pretending random window splits
    are independent. Deviations from 50/25/25 are allowed where class coverage
    requires them. Seeded costs make repeated runs choose the same allocation.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp
    sessions = sorted({row["session_id"] for row in records})
    classes = sorted({row["label"] for row in records})
    members = {name: {row["session_id"] for row in records if row["label"] == name} for name in classes}
    n = len(sessions)
    # Most bench collections permit an ordinary shuffled group allocation.
    # Try deterministic candidates first, reserving the constrained solver for
    # sparse class coverage rather than paying a solver cost on every audit.
    rng = np.random.default_rng(seed)
    train_end, validation_end = n // 2, n // 2 + n // 4
    for _ in range(256):
        shuffled = rng.permutation(sessions).tolist()
        candidate = {"train": sorted(shuffled[:train_end]),
                     "validation": sorted(shuffled[train_end:validation_end]),
                     "test": sorted(shuffled[validation_end:])}
        if all(len(set(candidate[split]) & members[name]) >= 2 for split in _SPLITS for name in classes):
            return candidate
    dimensions = 3 * n + 6
    objective = np.zeros(dimensions)
    objective[:3 * n] = np.random.default_rng(seed).uniform(0, .001, 3 * n)
    objective[3 * n:] = 100.0
    integrality = np.zeros(dimensions)
    integrality[:3 * n] = 1
    lower = np.zeros(dimensions)
    upper = np.full(dimensions, np.inf)
    upper[:3 * n] = 1
    rows, lows, highs = [], [], []
    for index in range(n):
        row = np.zeros(dimensions)
        row[[index + split * n for split in range(3)]] = 1
        rows.append(row); lows.append(1); highs.append(1)
    targets = (n * .5, n * .25, n * .25)
    for split in range(3):
        row = np.zeros(dimensions)
        row[split * n:(split + 1) * n] = 1
        row[3 * n + 2 * split] = -1
        row[3 * n + 2 * split + 1] = 1
        rows.append(row); lows.append(targets[split]); highs.append(targets[split])
        for name in classes:
            row = np.zeros(dimensions)
            for index, session in enumerate(sessions):
                if session in members[name]:
                    row[split * n + index] = 1
            rows.append(row); lows.append(2); highs.append(np.inf)
    solution = milp(objective, integrality=integrality, bounds=Bounds(lower, upper),
                    constraints=LinearConstraint(np.asarray(rows), lows, highs),
                    options={"time_limit": 5.0, "mip_rel_gap": 0.0})
    if solution.x is None:
        return None
    assignment = {name: [] for name in _SPLITS}
    for index, session in enumerate(sessions):
        selected = [split for split in range(3) if solution.x[split * n + index] > .5]
        if len(selected) != 1:
            return None
        assignment[_SPLITS[selected[0]]].append(session)
    if any(len(set(assignment[split]) & members[name]) < 2 for split in _SPLITS for name in classes):
        return None
    return assignment


def _readiness(dataset, seed):
    records = dataset["records"]
    reasons = []
    if dataset["status"] != "verified":
        reasons.append("Original evidence integrity failed; restore the hash-bound files first.")
    counts = {"eligible_cases": len(records), "excluded_cases": len(dataset.get("excluded", [])),
              "independent_captures": len({row["raw_rx_sha256"] for row in records}),
              "independent_sessions": len({row["session_id"] for row in records}),
              "rf_classes": len({row["label"] for row in records})}
    if counts["independent_captures"] != len(records):
        reasons.append("Duplicate original raw RX captures cannot be independent training rows.")
    if counts["independent_sessions"] < 12:
        reasons.append("At least 12 independent operator bench sessions are required; multiple captures or reprocessing in one session do not add sessions.")
    if counts["rf_classes"] < 2:
        reasons.append("At least two explicitly labelled RF classes are required.")
    class_sessions = {label: sorted({row["session_id"] for row in records if row["label"] == label})
                      for label in RF_CLASS_NAMES if any(row["label"] == label for row in records)}
    for label, sessions in class_sessions.items():
        if len(sessions) < 6:
            reasons.append(f"Class {label} needs at least six distinct bench sessions to supply two in each split.")
    split = None
    if not reasons:
        split = _split_sessions(records, seed)
        if split is None:
            reasons.append("No whole-session split satisfying two class-bearing sessions per class in every split was found.")
    return {"schema": "rf-link-lab.hardware-readiness.v1", "status": "blocked" if reasons else "ready",
            "reasons": reasons, "counts": counts, "class_sessions": class_sessions,
            "split_sessions": split, "seed": seed, "excluded": dataset["excluded"],
            "policy": {"minimum_independent_sessions": 12, "minimum_rf_classes": 2,
                       "minimum_class_sessions_per_split": 2, "split_unit": "operator_bench_session",
                       "raw_capture_deduplication": "one explicitly selected processing variant per SHA256"},
            "limitation": "Meeting this minimum permits an initial evaluation; it does not establish broad hardware generalization or calibrated diagnosis."}


def training_readiness(casebook, *, seed=2026):
    return _readiness(dataset_records(casebook), seed)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class HardwareModel:
    payload: dict

    def predict_support(self, samples):
        samples = np.atleast_2d(np.asarray(samples, dtype=float))
        if samples.ndim != 2 or samples.shape[1] != len(HARDWARE_FEATURE_NAMES) or not np.all(np.isfinite(samples)):
            raise ValueError("Hardware inference requires six finite measured features.")
        result = np.zeros((len(samples), len(self.payload["class_names"])))
        for tree in self.payload["trees"]:
            for index, sample in enumerate(samples):
                node = 0
                while tree["children_left"][node] != -1:
                    feature = tree["feature"][node]
                    node = tree["children_left"][node] if sample[feature] <= tree["threshold"][node] else tree["children_right"][node]
                result[index] += tree["leaf_support"][node]
        return result / len(self.payload["trees"])


def load_hardware_model(path):
    """Load bounded data-only JSON, validating all branches before traversal."""
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Hardware model JSON is too large.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != MODEL_SCHEMA:
        raise ValueError("Unsupported hardware model schema; synthetic models are not accepted.")
    if payload.get("source") != "operator_labelled_physical_bench_cases" or payload.get("feature_names") != list(HARDWARE_FEATURE_NAMES):
        raise ValueError("Hardware model source or feature whitelist mismatch.")
    if payload.get("feature_definition_ids") != {"clipping_fraction": CLIPPING_DEFINITION_ID}:
        raise ValueError("Hardware model clipping feature definition is incompatible.")
    if payload.get("rule_thresholds") != RULE_THRESHOLDS:
        raise ValueError("Hardware model fixed-baseline thresholds are incompatible.")
    classes = payload.get("class_names")
    if not isinstance(classes, list) or len(classes) < 2 or classes != [name for name in RF_CLASS_NAMES if name in classes]:
        raise ValueError("Invalid hardware RF class order.")
    groups = payload.get("split_sessions")
    qualification = payload.get("qualification")
    if not isinstance(groups, dict) or set(groups) != set(_SPLITS) or not isinstance(qualification, dict):
        raise ValueError("Hardware model is missing independent-session qualification metadata.")
    all_sessions = set()
    for split in _SPLITS:
        sessions = groups[split]
        if (not isinstance(sessions, list) or len(sessions) < 2
                or any(not isinstance(value, str) or not value.strip() for value in sessions)
                or len(set(sessions)) != len(sessions) or all_sessions.intersection(sessions)):
            raise ValueError("Hardware model session groups must be distinct and disjoint.")
        all_sessions.update(sessions)
    if len(all_sessions) < 12:
        raise ValueError("Hardware model qualification requires at least 12 independent bench sessions.")
    for key in ("dataset_sha256", "split_manifest_sha256"):
        if not isinstance(qualification.get(key), str) or not _SHA.fullmatch(qualification[key]):
            raise ValueError("Hardware model lacks hash-bound training export provenance.")
    records = qualification.get("records")
    if not isinstance(records, list) or not records or len(records) > 1_000_000:
        raise ValueError("Hardware model lacks per-case session qualification.")
    seen_raw, seen_cases, covered_sessions = set(), set(), set()
    class_groups = {split: {name: set() for name in classes} for split in _SPLITS}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid hardware qualification record.")
        raw, case_id = record.get("raw_rx_sha256"), record.get("case_id")
        split, session, label = record.get("split"), record.get("session_id"), record.get("label")
        if (not isinstance(raw, str) or not _SHA.fullmatch(raw) or raw in seen_raw
                or not isinstance(case_id, str) or not case_id or case_id in seen_cases
                or split not in _SPLITS or session not in groups[split] or label not in classes
                or not isinstance(record.get("review_sha256"), str) or not _SHA.fullmatch(record["review_sha256"])):
            raise ValueError("Hardware qualification has duplicated captures or invalid group assignments.")
        seen_raw.add(raw); seen_cases.add(case_id); covered_sessions.add(session)
        class_groups[split][label].add(session)
    if covered_sessions != all_sessions or any(len(class_groups[split][label]) < 2 for split in _SPLITS for label in classes):
        raise ValueError("Hardware qualification needs two class-bearing sessions per class in every split.")
    # Sidecars can accompany a portable model; when present they must match.
    # A standalone JSON retains unauthenticated provenance declarations.
    for name, key in (("dataset.json", "dataset_sha256"), ("split-manifest.json", "split_manifest_sha256")):
        sidecar = path.parent / name
        if sidecar.exists() and hashlib.sha256(sidecar.read_bytes()).hexdigest() != qualification[key]:
            raise ValueError("Hardware model training export sidecar hash mismatch.")
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict) or type(acceptance.get("enabled")) is not bool or not _finite(acceptance.get("minimum_support")) or not .5 <= acceptance["minimum_support"] <= 1.01:
        raise ValueError("Invalid hardware abstention policy.")
    bounds = acceptance.get("feature_bounds")
    if not isinstance(bounds, dict) or set(bounds) != set(HARDWARE_FEATURE_NAMES):
        raise ValueError("Invalid hardware feature envelope.")
    for span in bounds.values():
        if not isinstance(span, list) or len(span) != 2 or not all(_finite(v) for v in span) or span[0] > span[1]:
            raise ValueError("Invalid hardware feature envelope bounds.")
    trees = payload.get("trees")
    if not isinstance(trees, list) or not 1 <= len(trees) <= 128:
        raise ValueError("Invalid hardware forest size.")
    for tree in trees:
        keys = ("children_left", "children_right", "feature", "threshold", "leaf_support")
        if not isinstance(tree, dict) or any(not isinstance(tree.get(key), list) for key in keys):
            raise ValueError("Invalid hardware tree arrays.")
        length = len(tree["children_left"])
        if not 1 <= length <= 4096 or any(len(tree[key]) != length for key in keys):
            raise ValueError("Invalid hardware tree array lengths.")
        parents = [0] * length
        for index in range(length):
            left, right = tree["children_left"][index], tree["children_right"][index]
            support = tree["leaf_support"][index]
            if not isinstance(support, list) or len(support) != len(classes) or any(not _finite(v) or v < 0 for v in support) or not math.isclose(sum(support), 1, abs_tol=1e-8):
                raise ValueError("Invalid hardware tree support.")
            if type(left) is not int or type(right) is not int:
                raise ValueError("Hardware tree children must be integer indices.")
            if left == right == -1:
                continue
            feature = tree["feature"][index]
            if not (index < left < length and index < right < length and left != right and type(feature) is int and 0 <= feature < len(HARDWARE_FEATURE_NAMES) and _finite(tree["threshold"][index])):
                raise ValueError("Invalid or cyclic hardware tree branch.")
            parents[left] += 1
            parents[right] += 1
        if parents[0] != 0 or any(count != 1 for count in parents[1:]):
            raise ValueError("Hardware tree has unreachable or shared nodes.")
    return HardwareModel(payload)


def _diagnose_features(features, model):
    reasons = []
    classes = model.payload["class_names"]
    support = model.predict_support([[features[name] for name in HARDWARE_FEATURE_NAMES]])[0]
    predicted = classes[int(np.argmax(support))]
    rule = hardware_rules_diagnose(features)
    bounds = model.payload["acceptance"]["feature_bounds"]
    outside = [name for name in HARDWARE_FEATURE_NAMES if not bounds[name][0] <= features[name] <= bounds[name][1]]
    if outside:
        reasons.append("outside_observed_development_envelope:" + ",".join(outside))
    if sum(_signatures(features).values()) > 1:
        reasons.append("mixed_impairment_signatures")
    if rule not in classes:
        reasons.append("fixed_rule_signature_is_an_untrained_class:" + rule)
    if not model.payload["acceptance"]["enabled"]:
        reasons.append("validation_did_not_support_an_acceptance_threshold")
    elif float(support.max()) < model.payload["acceptance"]["minimum_support"]:
        reasons.append("tree_support_below_validation_threshold")
    return {"label": "unknown" if reasons else predicted, "suggested_label": predicted,
            "support": float(support.max()), "unknown_reasons": reasons,
            "ranked_signatures": [{"label": classes[int(index)], "support": float(support[index])}
                                  for index in np.argsort(-support)], "rule_baseline": rule}


def diagnose_hardware(review, model_path=None):
    """Inspect a verified hardware review using a distinct hardware-only model."""
    reasons = review_exclusion_reasons(review)
    result = {"label": "unknown", "suggested_label": None, "support": None,
              "unknown_reasons": reasons, "ranked_signatures": [], "rule_baseline": None}
    if model_path is None:
        result["unknown_reasons"] = [*reasons, "hardware_model_not_supplied"]
        if not reasons:
            result["rule_baseline"] = hardware_rules_diagnose(review["blind_features"])
    else:
        model = load_hardware_model(model_path)
        if not reasons:
            result = _diagnose_features(review["blind_features"], model)
    return {"schema": "rf-link-lab.hardware-diagnosis.v1", **result,
            "support_meaning": "Mean learned-tree class support; uncalibrated and not a fault probability.",
            "new_rf_transmission": False,
            "limitations": ["A model label is a hypothesis about measured signatures, not a hardware component diagnosis.",
                            "Training labels and physical origin are operator declarations. Generalization beyond held-out bench sessions is unproven.",
                            "Only six reference-independent features enter the model. Known-payload scores and metadata are excluded."]}


def _scores(truth, predicted, classes):
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
    if not truth:
        return {"accuracy": None, "macro_f1": None, "confusion_matrix": None, "class_order": classes, "count": 0}
    matrix_classes = classes + [name for name in RF_CLASS_NAMES if name in predicted and name not in classes]
    return {"accuracy": float(accuracy_score(truth, predicted)),
            "macro_f1": float(f1_score(truth, predicted, labels=classes, average="macro", zero_division=0)),
            "confusion_matrix": confusion_matrix(truth, predicted, labels=matrix_classes).tolist(),
            "class_order": matrix_classes, "evaluated_class_order": classes, "count": len(truth)}


def train_hardware_model(casebook, output, *, seed=2026):
    """Fit fixed RF forest, select acceptance on validation, evaluate test once."""
    dataset = dataset_records(casebook)
    readiness = _readiness(dataset, seed)
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("model.json", "evaluation.json", "readiness.json", "dataset.json", "split-manifest.json")):
        raise ValueError("Training output already contains results; choose a new output directory.")
    readiness["readiness_path"] = str(output / "readiness.json")
    (output / "readiness.json").write_bytes(_json_bytes(readiness))
    if readiness["status"] != "ready":
        return readiness
    from sklearn.ensemble import RandomForestClassifier
    classes = [name for name in RF_CLASS_NAMES if name in readiness["class_sessions"]]
    rows = {split: [row for row in dataset["records"] if row["session_id"] in readiness["split_sessions"][split]] for split in _SPLITS}
    qualification_records = [{"case_id": row["case_id"], "raw_rx_sha256": row["raw_rx_sha256"],
                              "review_sha256": row["review_sha256"], "session_id": row["session_id"],
                              "label": row["label"], "split": split} for split in _SPLITS for row in rows[split]]
    split_manifest = {"schema": "rf-link-lab.hardware-split.v1", "seed": seed,
                      "split_sessions": readiness["split_sessions"], "records": qualification_records}
    dataset_bytes, split_bytes = _json_bytes(dataset), _json_bytes(split_manifest)
    x = {split: np.array([[row["features"][name] for name in HARDWARE_FEATURE_NAMES] for row in values]) for split, values in rows.items()}
    y = {split: [row["label"] for row in values] for split, values in rows.items()}
    forest = RandomForestClassifier(**MODEL_PARAMETERS, random_state=seed)
    forest.fit(x["train"], y["train"])
    order = [forest.classes_.tolist().index(name) for name in classes]
    trees = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        support = tree.value[:, 0, :][:, order]
        support = support / support.sum(axis=1, keepdims=True)
        trees.append({"children_left": tree.children_left.tolist(), "children_right": tree.children_right.tolist(),
                      "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(), "leaf_support": support.tolist()})
    development = np.vstack((x["train"], x["validation"]))
    bounds = {}
    for index, name in enumerate(HARDWARE_FEATURE_NAMES):
        low, high = float(development[:, index].min()), float(development[:, index].max())
        slack = max((high - low) * .1, 1e-6)
        bounds[name] = [low - slack, high + slack]
    payload = {"schema": MODEL_SCHEMA, "source": "operator_labelled_physical_bench_cases",
               "feature_names": list(HARDWARE_FEATURE_NAMES), "class_names": classes,
               "feature_definition_ids": {"clipping_fraction": CLIPPING_DEFINITION_ID},
               "rule_thresholds": RULE_THRESHOLDS,
               "rule_threshold_limitation": "Initial engineering thresholds have not been qualified against physical fault labels. Full-capture ADC rail fraction includes idle intervals and depends on recording length.",
               "training_seed": seed, "hyperparameters": MODEL_PARAMETERS, "trees": trees,
               "split_sessions": readiness["split_sessions"],
               "qualification": {"records": qualification_records,
                                  "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                                  "split_manifest_sha256": hashlib.sha256(split_bytes).hexdigest(),
                                  "meaning": "Operator-declared physical origin and labels; hashes bind exports, not source authenticity."},
               "acceptance": {"enabled": True, "minimum_support": .5, "feature_bounds": bounds,
                              "policy": "Fixed support candidates .55/.65/.75/.85/.95; greatest validation coverage with accepted accuracy >=.90 and coverage >=.50. No passing threshold disables acceptance. Envelope uses train+validation only; mixed or untrained rule signatures abstain."},
               "feature_importance": {name: float(value) for name, value in zip(HARDWARE_FEATURE_NAMES, forest.feature_importances_)}}
    model = HardwareModel(payload)
    validation = [_diagnose_features(row["features"], model) for row in rows["validation"]]
    selection, passing = [], []
    for threshold in (.55, .65, .75, .85, .95):
        accepted = [index for index, item in enumerate(validation) if not item["unknown_reasons"] and item["support"] >= threshold]
        accuracy = float(np.mean([validation[index]["suggested_label"] == y["validation"][index] for index in accepted])) if accepted else None
        coverage = len(accepted) / len(validation)
        selection.append({"threshold": threshold, "coverage": coverage, "accepted_accuracy": accuracy})
        if accuracy is not None and accuracy >= .9 and coverage >= .5:
            passing.append((coverage, -threshold, threshold))
    payload["acceptance"].update(enabled=bool(passing), minimum_support=max(passing)[2] if passing else 1.01, selection=selection)
    model_path = output / "model.json"
    model_path.write_bytes(_json_bytes(payload))
    portable = load_hardware_model(model_path)
    test_support = portable.predict_support(x["test"])
    sklearn_support = forest.predict_proba(x["test"])[:, order]
    predicted = [classes[int(index)] for index in np.argmax(test_support, axis=1)]
    rules = [hardware_rules_diagnose(row["features"]) for row in rows["test"]]
    diagnoses = [_diagnose_features(row["features"], portable) for row in rows["test"]]
    accepted = [index for index, item in enumerate(diagnoses) if item["label"] != "unknown"]
    evaluation = {"schema": "rf-link-lab.hardware-evaluation.v1", "source": payload["source"],
                  "model": _scores(y["test"], predicted, classes), "rules": _scores(y["test"], rules, classes),
                  "accepted": _scores([y["test"][index] for index in accepted], [predicted[index] for index in accepted], classes),
                  "coverage": len(accepted) / len(y["test"]), "test_count": len(y["test"]),
                  "abstained_count": len(y["test"]) - len(accepted),
                  "portable_prediction_max_abs_difference": float(np.max(np.abs(test_support - sklearn_support))),
                  "split_sessions": readiness["split_sessions"], "validation_selection": selection,
                  "rules_thresholds": RULE_THRESHOLDS, "support_is_calibrated_probability": False,
                  "selection_scope": "Hyperparameters and rule thresholds fixed before fitting. Acceptance threshold selected on validation only; test used only for final reporting.",
                  "limitations": [readiness["limitation"],
                                  "Amplitude variation and spectral flatness depend on the selected receiver processing and capture duration. Preserve source reviews/configuration and compare like acquisition windows; no physical root cause is established."]}
    (output / "dataset.json").write_bytes(dataset_bytes)
    (output / "split-manifest.json").write_bytes(split_bytes)
    (output / "evaluation.json").write_bytes(_json_bytes(evaluation))
    return {"schema": "rf-link-lab.hardware-training.v1", "status": "trained", "readiness": readiness,
            "model_path": str(model_path), "evaluation_path": str(output / "evaluation.json"),
            "dataset_path": str(output / "dataset.json"), "split_manifest_path": str(output / "split-manifest.json"),
            "readiness_path": str(output / "readiness.json"), "evaluation": evaluation}
