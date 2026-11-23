"""Measured-feature diagnosis and one-experiment recommendations.

This small model is a synthetic teaching aid. Its tree vote fractions are model
support, not calibrated probabilities or proof of a physical root cause.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .dataset import CLASS_NAMES, FEATURE_NAMES, make_dataset, save_dataset

RULE_THRESHOLDS = {"cfo_abs_hz": 500.0, "snr_est_db": 12.0,
                   "clipping_fraction": .035}


def _signature_flags(features: Mapping[str, float]) -> dict[str, bool]:
    # Pilot frequency estimation can be unreliable in noise. A large estimate
    # alone is not evidence of a second fault when the pilot fit has poor SNR.
    return {"frequency_offset": (float(features["cfo_abs_hz"]) > RULE_THRESHOLDS["cfo_abs_hz"]
                                 and float(features["snr_est_db"]) >= RULE_THRESHOLDS["snr_est_db"]),
            "low_snr": float(features["snr_est_db"]) < RULE_THRESHOLDS["snr_est_db"],
            "clipping": float(features["clipping_fraction"]) > RULE_THRESHOLDS["clipping_fraction"]}


def rules_diagnose(features: Mapping[str, float]) -> str:
    """Explicit fixed-threshold baseline, with a documented priority order."""
    flags = _signature_flags(features)
    # The baseline makes one label even on mixed cases. The AI assistant below
    # can instead abstain; compare classification scores before abstention.
    return next((name for name in ["clipping", "frequency_offset", "low_snr"]
                 if flags[name]), "healthy")


@dataclass
class DiagnosticModel:
    payload: dict

    def predict_proba(self, samples: np.ndarray) -> np.ndarray:
        samples = np.atleast_2d(np.asarray(samples, dtype=float))
        probabilities = np.zeros((samples.shape[0], len(CLASS_NAMES)), dtype=float)
        for tree in self.payload["trees"]:
            for row_index, row in enumerate(samples):
                node = 0
                while tree["children_left"][node] != -1:
                    feature = tree["feature"][node]
                    node = (tree["children_left"][node] if row[feature] <= tree["threshold"][node]
                            else tree["children_right"][node])
                probabilities[row_index] += np.asarray(tree["leaf_support"][node])
        return probabilities / len(self.payload["trees"])


def load_model(path: Path | str) -> DiagnosticModel:
    """Load data-only JSON. No executable pickle/joblib is loaded."""
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("format") != "rf-link-lab-forest-v1":
        raise ValueError("Unsupported diagnostic model format.")
    if payload.get("feature_names") != FEATURE_NAMES or payload.get("class_names") != CLASS_NAMES:
        raise ValueError("Model feature/class order does not match this receiver.")
    if not payload.get("trees"):
        raise ValueError("Model has no learned trees.")
    for tree in payload["trees"]:
        length = len(tree["children_left"])
        if length == 0 or any(len(tree[key]) != length for key in
                              ["children_right", "feature", "threshold", "leaf_support"]):
            raise ValueError("Invalid learned tree arrays.")
        for index in range(length):
            left, right = tree["children_left"][index], tree["children_right"][index]
            if left == -1:
                support = np.asarray(tree["leaf_support"][index], dtype=float)
                if support.shape != (len(CLASS_NAMES),) or not np.all(np.isfinite(support)) or np.any(support < 0) or not np.isclose(support.sum(), 1):
                    raise ValueError("Invalid learned leaf support.")
            elif not (index < left < length and index < right < length and
                      0 <= tree["feature"][index] < len(FEATURE_NAMES) and
                      np.isfinite(tree["threshold"][index])):
                raise ValueError("Invalid learned tree branch.")
    return DiagnosticModel(payload)


def _measurement_vector(features: Mapping[str, float]) -> np.ndarray:
    try:
        values = np.array([features[name] for name in FEATURE_NAMES], dtype=float)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Provide all six numeric measured receiver features.") from error
    if not np.all(np.isfinite(values)):
        raise ValueError("Receiver features must all be finite.")
    return values


def diagnose(features: Mapping[str, float], model: DiagnosticModel) -> dict:
    values = _measurement_vector(features)
    support_vector = model.predict_proba(values)[0]
    predicted = CLASS_NAMES[int(np.argmax(support_vector))]
    support = float(np.max(support_vector))
    flags = _signature_flags(features)
    active_signatures = [name for name, present in flags.items() if present]
    evidence = [f"Measured absolute frequency offset: {features['cfo_abs_hz']:.1f} Hz.",
                f"Pilot residual SNR estimate: {features['snr_est_db']:.1f} dB.",
                f"Samples at an ADC rail: {100*features['clipping_fraction']:.2f}%.",
                f"Received-symbol EVM: {features['evm_pct']:.1f}%."]
    reason = None
    bounds = model.payload["acceptance"]["feature_bounds"]
    outside = [name for index, name in enumerate(FEATURE_NAMES)
               if not bounds[name][0] <= values[index] <= bounds[name][1]]
    if outside:
        reason = "Measurements outside the synthetic training/validation envelope: " + ", ".join(outside) + "."
    elif len(active_signatures) > 1:
        reason = "Multiple impairment signatures are present; the training labels describe single faults."
    elif support < model.payload["acceptance"]["minimum_support"]:
        reason = "Tree support is below the acceptance threshold selected on validation sessions."
    next_measurement = {
        "healthy": "Repeat the capture with the same settings and a fresh recording; verify packet CRC and measured values remain stable.",
        "frequency_offset": "Set only the injected simulation frequency offset to 0 Hz, then repeat with gain and noise unchanged; compare measured offset, EVM and packet CRC.",
        "low_snr": "Increase the simulated signal-to-noise ratio only, then repeat the capture; compare residual SNR, EVM and packet CRC.",
        "clipping": "Reduce receiver input level or gain only, then repeat the capture; compare rail-hit fraction, EVM and packet CRC.",
        "unknown": "Repeat a fresh recording with exactly the same controls; check whether the measured signatures are stable before selecting one impairment to isolate.",
    }
    label = "unknown" if reason else predicted
    return {"label": label, "suggested_label": predicted, "support": support,
            "support_meaning": "Mean learned-tree class support; not calibrated confidence.",
            "probabilities": {name: float(value) for name, value in zip(CLASS_NAMES, support_vector)},
            "ranked_signatures": [{"label": CLASS_NAMES[index], "support": float(support_vector[index])}
                                  for index in np.argsort(-support_vector)],
            "evidence": evidence, "next_measurement": next_measurement[label],
            "unknown_reason": reason, "evidence_source": "synthetic_receiver_measurements_only",
            "limitation": "This model has not been validated on bladeRF captures or physical hardware."}


def propose_followup(config: Any, diagnosis: Mapping | str) -> dict:
    """One synthetic intervention to demonstrate a testable hypothesis.

    Changes a simulator setting with known ground truth. It is not autonomous
    hardware control and does not prove that a proposed real-world cause is true.
    """
    settings = asdict(config) if is_dataclass(config) else dict(config)
    label = diagnosis if isinstance(diagnosis, str) else diagnosis["label"]
    if label == "frequency_offset":
        changes = {"cfo_hz": 0.0}
        rationale = "Remove only the injected frequency offset in this synthetic channel."
        expected = "Measured offset falls; EVM and valid packet count should improve when offset was limiting."
    elif label == "low_snr":
        changes = {"snr_db": 30.0}
        rationale = "Reduce only the injected noise power in this synthetic channel."
        expected = "Residual SNR rises; EVM and valid packet count should improve when noise was limiting."
    elif label == "clipping":
        # Gain is unit-scale, and the normalized pulse shape can overshoot.
        changes = {"gain": float(min(settings.get("gain", .65), .35 * settings.get("clip_level", 1.0)))}
        rationale = "Lower only the simulated receiver input gain relative to its ADC rails."
        expected = "ADC rail-hit fraction should fall; if another impairment remains, investigate it separately."
    else:
        changes = {"seed": int(settings.get("seed", 42)) + 1}
        rationale = "Repeat the synthetic observation with independent noise before changing a physical control."
        expected = "A repeated capture tests stability; an unknown result requires a controlled follow-up, not a forced label."
    return {"config": {**settings, **changes}, "changes": changes,
            "rationale": rationale, "expected_observation": expected,
            "simulation_only": True}


def _scores(truth: list[str], predicted: list[str]) -> dict:
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    return {"accuracy": float(accuracy_score(truth, predicted)),
            "macro_f1": float(f1_score(truth, predicted, labels=CLASS_NAMES, average="macro", zero_division=0)),
            "confusion_matrix": confusion_matrix(truth, predicted, labels=CLASS_NAMES).tolist(),
            "class_order": CLASS_NAMES,
            "per_class": classification_report(truth, predicted, labels=CLASS_NAMES,
                                                 output_dict=True, zero_division=0)}


def train_demo_model(output_dir: Path, sessions: int = 20,
                     windows_per_class: int = 3, seed: int = 2026) -> dict:
    """Train, tune abstention on validation only, then evaluate heldout sessions."""
    from sklearn.ensemble import RandomForestClassifier
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records, manifest = make_dataset(sessions, windows_per_class, seed)
    save_dataset(records, manifest, output_dir)
    splits = {split: [record for record in records if record["split"] == split]
              for split in ["train", "validation", "test"]}
    x = {split: np.array([[r[name] for name in FEATURE_NAMES] for r in rows])
         for split, rows in splits.items()}
    y = {split: [r["label"] for r in rows] for split, rows in splits.items()}
    forest = RandomForestClassifier(n_estimators=80, max_depth=9, min_samples_leaf=2,
                                    random_state=seed, n_jobs=1, class_weight="balanced")
    forest.fit(x["train"], y["train"])
    class_columns = [forest.classes_.tolist().index(name) for name in CLASS_NAMES]
    trees = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :][:, class_columns]
        normalized = values / values.sum(axis=1, keepdims=True)
        trees.append({"children_left": tree.children_left.tolist(),
                      "children_right": tree.children_right.tolist(),
                      "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(),
                      "leaf_support": normalized.tolist()})
    validation_support = forest.predict_proba(x["validation"])[:, class_columns]
    validation_correct = np.array([CLASS_NAMES[index] == expected for index, expected in
                                   zip(np.argmax(validation_support, axis=1), y["validation"])])
    minimum_support = .85
    selection = []
    for threshold in np.arange(.55, .951, .05):
        accepted = validation_support.max(axis=1) >= threshold
        coverage = float(accepted.mean())
        accuracy = float(validation_correct[accepted].mean()) if accepted.any() else 0.0
        selection.append({"threshold": round(float(threshold), 2), "coverage": coverage,
                          "accepted_accuracy": accuracy})
        if coverage >= .5 and accuracy >= .95:
            minimum_support = float(threshold)
            break
    # Bounds are selected from train + validation, never heldout test data.
    # Slack avoids interpreting observed synthetic extrema as exact physics.
    development = np.vstack([x["train"], x["validation"]])
    bounds = {}
    for column, name in enumerate(FEATURE_NAMES):
        low, high = float(development[:, column].min()), float(development[:, column].max())
        slack = max(.25 * (high - low), 1e-6)
        bounds[name] = [low - slack, high + slack]
    payload = {"format": "rf-link-lab-forest-v1", "source": "synthetic_simulator_only",
               "created_utc": datetime.now(timezone.utc).isoformat(),
               "feature_names": FEATURE_NAMES, "class_names": CLASS_NAMES,
               "trees": trees, "training_seed": seed,
               "training_sessions": manifest["split_sessions"]["train"],
               "acceptance": {"minimum_support": minimum_support, "feature_bounds": bounds,
                              "selection": selection,
                              "policy": "Lowest candidate support >=.55 achieving >=.95 accepted accuracy at >=.50 coverage on validation; fallback .85. Bounds use train+validation extrema with25percent slack. Multiple fixed-rule signatures abstain.",
                              "guarantee": "None; these heuristic guards are not validated hardware OOD detection."},
               "feature_importance": {name: float(value) for name, value in zip(FEATURE_NAMES, forest.feature_importances_)}}
    model_path = output_dir / "model.json"
    model_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    portable = load_model(model_path)
    predicted = [CLASS_NAMES[index] for index in np.argmax(portable.predict_proba(x["test"]), axis=1)]
    rules = [rules_diagnose(record) for record in splits["test"]]
    model_scores, rule_scores = _scores(y["test"], predicted), _scores(y["test"], rules)
    accepted_diagnoses = [diagnose(record, portable) for record in splits["test"]]
    accepted = [index for index, result in enumerate(accepted_diagnoses) if result["label"] != "unknown"]
    parity = float(np.max(np.abs(portable.predict_proba(x["test"]) -
                                 forest.predict_proba(x["test"])[:, class_columns])))
    from .simulation import SimulationConfig, run_simulation
    mixed_results = []
    for offset in range(12):
        config = SimulationConfig(seed=seed + 900000 + offset, frames=6, cfo_hz=1800,
                                  snr_db=4, gain=.65, clip_level=.23,
                                  phase_deg=offset * 23, delay_samples=19 + offset,
                                  correct_cfo=False)
        measured=run_simulation(config)
        unavailable=any(value is None for value in measured.features.values())
        mixed=({'label':'unknown','unknown_reason':'Receiver acquisition failed; no complete feature vector.'} if unavailable else diagnose(measured.features,portable))
        mixed_results.append({"seed": config.seed, "label": mixed["label"],
                              "rejection_stage":"receiver" if unavailable else "diagnostic_guards",
                              "unknown_reason": mixed["unknown_reason"]})
    report = {"schema_version": 1, "source": "synthetic_simulator_only",
              "model_macro_f1": model_scores["macro_f1"], "rules_macro_f1": rule_scores["macro_f1"],
              "model_accuracy": model_scores["accuracy"], "rules_accuracy": rule_scores["accuracy"],
              "test_count": len(splits["test"]), "model": model_scores, "rules": rule_scores,
              "excluded_test_count":manifest.get('split_excluded_counts',{}).get('test',0),
              "receiver_measurement_coverage":manifest.get('measurement_coverage',1),
              "score_scope":"Classifier scores are conditional on complete receiver measurements. Failed acquisitions are logged separately and are not assigned fabricated features.",
              "abstention": {"minimum_support": minimum_support,
                             "test_coverage": len(accepted) / len(splits["test"]),
                             "accepted_test_accuracy": float(np.mean([accepted_diagnoses[i]["label"] == y["test"][i]
                                                                      for i in accepted])) if accepted else None,
                             "unknown_test_count": len(splits["test"]) - len(accepted)},
              "mixed_fault_probe": {"count": len(mixed_results),
                                    "rejection_rate": sum(r["label"] == "unknown" for r in mixed_results) / len(mixed_results),
                                    "results": mixed_results,
                                    "limitation": "Small synthetic demonstration of simultaneous faults, not an OOD benchmark."},
              "portable_prediction_max_abs_difference": parity,
              "rule_thresholds": RULE_THRESHOLDS, "rule_priority": ["clipping", "frequency_offset", "low_snr", "healthy"],
              "split_manifest": manifest,
              "model_path": str(model_path.resolve()),
              "evaluation_path": str((output_dir / "evaluation.json").resolve()),
              "feature_importance": payload["feature_importance"],
              "interpretation": "Both scores classify the same heldout virtual sessions before abstention. Similar or better rules performance is a valid outcome; no AI advantage is assumed.",
              "limits": ["One simple synthetic channel; no real hardware captures.",
                         "Four controlled single-impairment classes; not universal root-cause diagnosis.",
                         "Random forest support is not calibrated confidence.",
                         "Do not tune the model from these test results; create a new independent test set for later iterations."]}
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report
