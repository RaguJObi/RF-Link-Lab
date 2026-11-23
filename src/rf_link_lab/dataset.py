"""Deterministic, session-grouped *synthetic* experiments for the RF tutor.

Ground-truth settings exist only in the audit metadata. The classifier consumes
the six measurements returned by the receiver, never a simulator setting.
"""
from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np

from .simulation import SimulationConfig, run_simulation

CLASS_NAMES = ["healthy", "frequency_offset", "low_snr", "clipping"]
FEATURE_NAMES = [
    "cfo_abs_hz", "evm_pct", "clipping_fraction", "snr_est_db",
    "amplitude_cv", "spectral_flatness",
]


def make_dataset(sessions: int = 20, windows_per_class: int = 3,
                 seed: int = 2026) -> tuple[list[dict], dict]:
    """Generate all classes in each independent virtual recording session.

    A session shares a nuisance realization (phase, timing and gain). Entire
    sessions go into one split. A session is a synthetic grouping, not a real
    device/session measurement, and does not validate hardware generalization.
    """
    if sessions < 8:
        raise ValueError("At least 8 sessions are required for grouped splits.")
    if windows_per_class < 1:
        raise ValueError("windows_per_class must be positive.")
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(sessions).tolist()
    n_train = max(4, int(sessions * .6))
    n_validation = max(2, int(sessions * .2))
    if n_train + n_validation > sessions - 2:
        n_train = sessions - n_validation - 2
    groups = {
        "train": sorted(shuffled[:n_train]),
        "validation": sorted(shuffled[n_train:n_train+n_validation]),
        "test": sorted(shuffled[n_train+n_validation:]),
    }
    split_for_session = {session: split for split, group in groups.items()
                         for session in group}
    records: list[dict] = []
    excluded: list[dict] = []
    for session in range(sessions):
        session_id = f"synthetic-session-{session:03d}"
        session_rng = np.random.default_rng(seed + 1009 * (session + 1))
        nuisance = {
            "gain": float(session_rng.uniform(.45, .85)),
            "phase_deg": float(session_rng.uniform(-180, 180)),
            "delay_samples": int(session_rng.integers(13, 81)),
        }
        # These nuisance settings have exactly the same values for all classes
        # in a session. The impairment is the only class-specific intervention.
        for window in range(windows_per_class):
            nominal_snr = float(session_rng.uniform(24, 34))
            nominal_cfo = float(session_rng.uniform(-2, 2))
            for class_index, label in enumerate(CLASS_NAMES):
                sample_seed = int(seed + session * 10000 + window * 100 + class_index)
                settings = dict(seed=sample_seed, frames=6,
                                snr_db=nominal_snr, cfo_hz=nominal_cfo,
                                clip_level=4.0, sample_rate=200000,
                                samples_per_symbol=4, correct_cfo=False, **nuisance)
                if label == "frequency_offset":
                    settings["cfo_hz"] = float(session_rng.choice([-1, 1]) *
                                                session_rng.uniform(800, 3800))
                elif label == "low_snr":
                    settings["snr_db"] = float(session_rng.uniform(0, 9))
                elif label == "clipping":
                    settings["clip_level"] = float(session_rng.uniform(.12, .30))
                result = run_simulation(SimulationConfig(**settings))
                values=[result.features.get(name) for name in FEATURE_NAMES]
                if any(value is None for value in values) or not np.all(np.isfinite(values)):
                    excluded.append({'sample_id':f'{session_id}-window-{window:02d}-{label}',
                                     'session_id':session_id,'split':split_for_session[session],'label':label,
                                     'reason':'receiver did not provide a complete finite measurement vector',
                                     'receiver_status':result.metrics.get('status'),'audit_config':settings})
                    continue
                measured = {name: float(result.features[name]) for name in FEATURE_NAMES}
                records.append({
                    "sample_id": f"{session_id}-window-{window:02d}-{label}",
                    "session_id": session_id,
                    "split": split_for_session[session], "label": label,
                    **measured,
                    "audit_config": asdict(SimulationConfig(**settings)),
                    "audit_metrics": {key: value for key, value in result.metrics.items()
                                      if isinstance(value, (str, int, float, bool)) or value is None},
                })
    manifest = {
        "schema_version": 1, "source": "synthetic_simulator_only",
        "seed": seed, "session_count": sessions,
        "windows_per_class_per_session": windows_per_class,
        "record_count": len(records), "feature_names": FEATURE_NAMES,
        "attempted_record_count": sessions*windows_per_class*len(CLASS_NAMES),
        "excluded_measurement_count":len(excluded),"excluded_measurements":excluded,
        "measurement_coverage":len(records)/(sessions*windows_per_class*len(CLASS_NAMES)),
        "class_names": CLASS_NAMES,
        "split_sessions": {split: [f"synthetic-session-{s:03d}" for s in group]
                           for split, group in groups.items()},
        "split_counts": {split: sum(r["split"] == split for r in records)
                         for split in groups},
        "split_excluded_counts":{split:sum(r['split']==split for r in excluded) for split in groups},
        "split_policy": "Entire virtual recording sessions; train60/validation20/test20 percent (minimum two validation/test sessions).",
        "nuisance_policy": "Gain .45-.85, phase -180..180deg and delay13..80samples are drawn once per session and shared by every class; SNR24..34dB and nominal CFO+-2Hz shared per window. Nominal ADC rail4 provides headroom even for the low-SNR experiment.",
        "impairments": {"healthy": "nominal", "frequency_offset": "abs(CFO)800..3800Hz",
                        "low_snr": "SNR0..9dB", "clipping": "ADC rail .12.. .30"},
        "feature_boundary": "Only FEATURE_NAMES, measured from received samples, enter the model. Labels/settings/session IDs/seeds/metrics are excluded.",
        "limitation": "Classifier scores condition on acquired captures with complete measured features. Failed acquisitions are recorded separately, never filled with fabricated features. Synthetic sessions share one simulator; performance is not hardware accuracy.",
    }
    return records, manifest


def save_dataset(records: list[dict], manifest: dict, output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["sample_id", "session_id", "split", "label", *FEATURE_NAMES,
               "audit_config_json", "audit_metrics_json"]
    with (output_dir / "dataset.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow({**{key: record[key] for key in columns[:-2]},
                             "audit_config_json": json.dumps(record["audit_config"], sort_keys=True),
                             "audit_metrics_json": json.dumps(record["audit_metrics"], sort_keys=True)})
    (output_dir / "split-manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
