"""Readable exports of saved hardware evidence; never open a board or train a model."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path

from . import __version__


SCHEMA = "rf-link-lab.hardware-review-export.v1"
_FEATURE_LABELS = {
    "cfo_abs_hz": "Absolute preamble frequency offset (Hz)",
    "pilot_evm_pct": "Public-preamble EVM (%)",
    "snr_est_db": "Pilot residual SNR estimate (dB)",
    "clipping_fraction": "Samples at an ADC rail (fraction)",
    "amplitude_cv": "Amplitude coefficient of variation",
    "spectral_flatness": "Spectral flatness",
}


def _snapshot(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _value(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render_hardware_review(review: dict, *, diagnosis=None, application_version=None) -> str:
    """Render observations and hypotheses without promoting them to labels."""
    review = _snapshot(review)
    diagnosis = _snapshot(diagnosis)
    version = __version__ if application_version is None else application_version
    if review.get("schema") != "rf-link-lab.hardware-review.v1":
        raise ValueError("Choose a saved hardware-evidence review.")
    e = lambda value: html.escape(_value(value), quote=True)
    outcome = review.get("outcome", {})
    metrics = review.get("receiver_metrics", {})
    rows = "".join(
        f"<tr><th>{e(label)}</th><td>{e(review.get('blind_features', {}).get(name))}</td></tr>"
        for name, label in _FEATURE_LABELS.items()
    )
    findings = []
    for item in review.get("findings", []):
        findings.append(
            f"<article><div class='tag'>{e(item.get('kind', 'observation'))} · "
            f"{e(item.get('severity', 'info'))}</div><h3>{e(item.get('title'))}</h3>"
            f"<pre>{e(item.get('evidence', []))}</pre>"
            f"<p><strong>Next measurement:</strong> {e(item.get('next_measurement'))}</p></article>"
        )
    if not findings:
        findings.append("<p>No supported diagnostic finding was recorded.</p>")
    original = outcome.get("recorded_known_payload_complete")
    selected = outcome.get("selected_known_payload_complete")
    known = "".join(
        f"<tr><th>{e(key)}</th><td>{e(metrics.get(key))}</td></tr>"
        for key in ("known_payload_frames_matched", "known_payload_frames_missing",
                    "known_payload_frames_unexpected", "bits_compared", "bit_errors",
                    "reference_evm_pct", "pilot_evm_pct")
    )
    model = (
        "<p>No learned hardware-model inference requested. Findings above use explicit "
        "evidence checks and diagnostic rules.</p>"
        if diagnosis is None else f"<pre>{e(diagnosis)}</pre>"
    )
    limits = "".join(f"<li>{e(item)}</li>" for item in review.get("limitations", []))
    rails = review.get("raw_adc_rail_measurement", {})
    rail_rows = "".join(
        f"<tr><th>{e(key)}</th><td>{e(rails.get(key))}</td></tr>"
        for key in ("definition_id", "available", "format", "rail_codes", "sample_count",
                    "rail_hit_samples", "out_of_range_samples", "trailing_bytes",
                    "format_valid", "fraction", "scan_scope")
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RF Link Lab — hardware evidence review</title><style>
body{{margin:0;background:#0b1320;color:#e5edf6;font:15px/1.6 system-ui}}
main{{max-width:1120px;margin:auto;padding:36px 24px}}h1{{font-size:32px;line-height:1.2}}
h2{{font-size:22px;margin-top:36px}}h3{{margin:6px 0 12px;font-size:19px}}
.tag{{color:#45d5bd;font-size:12px;letter-spacing:1.4px;text-transform:uppercase}}
.muted{{color:#a6b8c9}}.cards{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.card,article{{background:#101d2d;border:1px solid #243447;border-radius:10px;padding:18px;margin:12px 0}}
.card strong{{display:block;font-size:24px}}table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:10px;border-bottom:1px solid #243447;vertical-align:top}}
th{{font-weight:400;color:#a6b8c9;width:58%}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}}
summary{{cursor:pointer}}@media(max-width:650px){{.cards{{grid-template-columns:1fr}}main{{padding:22px 16px}}}}
</style></head><body><main><div class="tag">Saved hardware evidence · v{e(version)}</div>
<h1>RF Link Lab — hardware review</h1>
<p class="muted">This report reads existing files. It starts no hardware operation, RF transmission or model training.
File hashes preserve evidence integrity; they do not authenticate its physical origin.</p>
<div class="cards"><div class="card">Original payload complete<strong>{e(original)}</strong></div>
<div class="card">Selected decode complete<strong>{e(selected)}</strong></div></div>
<p>Experiment status: <strong>{e(outcome.get('experiment_status'))}</strong>.
Selected receiver status: <strong>{e(outcome.get('receiver_status'))}</strong>.</p>
<h2>Supported findings</h2>{''.join(findings)}
<h2>Blind receiver measurements</h2><p class="muted">These six measurements may enter the hardware classifier.
Missing measurements stay unavailable. Known-payload BER, reference EVM and packet counts never enter its feature vector.</p>
<table>{rows}</table>
<h2>Raw ADC endpoint check</h2>
<p class="muted">The clipping input counts complex samples with either I or Q exactly at
−2048 or +2047 in the original SC16 Q11 file. The denominator is the entire capture,
including quiet samples. It is an endpoint indicator, not proof of an analog fault.
Historical receiver values based on repeated observed peaks remain unchanged.</p>
<table>{rail_rows}</table>
<details><summary>Definitions and sources of the six inputs</summary><pre>{e(review.get('feature_basis', {}))}</pre></details>
<h2>Known-payload comparison</h2>
<p class="muted">Comparison evidence only; normalized EVM is not a calibrated RF-quality qualification.</p><table>{known}</table>
<h2>Learned hardware model</h2>{model}<p class="muted">Tree support is uncalibrated. A supported finding is not an operator label or a learned prediction.</p>
<h2>Evidence boundary</h2><ul>{limits}</ul>
<details><summary>Preserved source identities and transport evidence</summary><pre>{e(review.get('source', {}))}</pre>
<pre>{e(review.get('transport', {}))}</pre></details>
</main></body></html>"""


def save_hardware_review(review: dict, output_dir, *, diagnosis=None) -> dict:
    """Write one new export; never alter the original experiment or old reports."""
    review, diagnosis = _snapshot(review), _snapshot(diagnosis)
    content = render_hardware_review(review, diagnosis=diagnosis)
    folder = Path(output_dir).expanduser().absolute()
    if folder.exists() or folder.is_symlink():
        raise FileExistsError("Choose a new hardware-review output folder.")
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "review.json").write_text(json.dumps(review, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (folder / "report.html").write_text(content, encoding="utf-8")
    manifest = {
        "schema": SCHEMA, "application_version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnosis": diagnosis,
        "files": {name: {"sha256": _sha(folder / name), "bytes": (folder / name).stat().st_size}
                  for name in ("review.json", "report.html")},
        "scope": "Saved hardware evidence only. No device access, training or new RF transmission.",
    }
    (folder / "export.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {"status": "saved", "review_manifest": str(folder / "review.json"),
            "report": str(folder / "report.html"), "export_manifest": str(folder / "export.json")}


def verify_hardware_review_report(path) -> dict:
    folder = Path(path)
    folder = folder if folder.is_dir() else folder.parent
    if folder.is_symlink():
        raise ValueError("Review export folders must not be symbolic links.")
    manifest = json.loads((folder / "export.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or set(manifest.get("files", {})) != {"review.json", "report.html"}:
        raise ValueError("Unsupported review export.")
    for name, expected in manifest["files"].items():
        member = folder / name
        if member.is_symlink() or not member.is_file():
            raise ValueError("A preserved review file is missing or linked.")
        if expected != {"sha256": _sha(member), "bytes": member.stat().st_size}:
            raise ValueError("Review export file hash or size mismatch: " + name)
    review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    expected_html = render_hardware_review(review, diagnosis=manifest.get("diagnosis"),
                                          application_version=manifest.get("application_version"))
    if (folder / "report.html").read_text(encoding="utf-8") != expected_html:
        raise ValueError("The report differs from its saved review and diagnosis.")
    return {"status": "verified", "files_verified": 2,
            "scope": "Export bytes and rendering contract checked; original source files are checked separately."}
