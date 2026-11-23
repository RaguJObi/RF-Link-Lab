"""Portable, hash-checked run bundles and reports. Never deserialize executable objects."""
from __future__ import annotations

import dataclasses
import hashlib
import html
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from . import __version__

ARRAY_NAMES = ("tx_iq", "rx_iq", "constellation", "spectrum_frequency_hz", "spectrum_dbfs", "reference_symbols", "reference_bits")


def json_value(value):
    if dataclasses.is_dataclass(value):
        return {k: json_value(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def save_run(result, output_dir: Path, diagnosis: dict | None = None) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    arrays = {name: np.asarray(getattr(result, name)) for name in ARRAY_NAMES if hasattr(result, name)}
    capture = output_dir / "capture.npz"
    np.savez_compressed(capture, **arrays)
    metadata=json_value(getattr(result,'metadata',{}))
    provenance=metadata.get('provenance','synthetic')
    manifest = {
        "schema": "rf-link-lab-run-v2", "provenance": provenance,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "application_version": __version__, "python_version": platform.python_version(),
        "config": json_value(result.config), "metadata":metadata,"metrics": json_value(result.metrics),
        "features": json_value(result.features), "telemetry": json_value(result.telemetry),
        "diagnosis": json_value(diagnosis), "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "replay_scope": "Verify and display stored waveform and recorded metrics; receiver metrics are not recomputed on load.",
        "limits": "Origin is declared by source metadata. No calibrated sensitivity, independent radios or standards conformance is established."
    }
    (output_dir / "run.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    write_report(result, output_dir / "report.html", diagnosis)
    return output_dir / "run.json"


def load_run(path: Path):
    path = Path(path)
    manifest_path = path / "run.json" if path.is_dir() else path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") not in ("rf-link-lab-run-v1","rf-link-lab-run-v2"):
        raise ValueError("Unsupported run bundle. Import raw SC16 Q11 separately.")
    if manifest.get('provenance') not in ('synthetic','independent_offline_decode','serial_waveform_software_loopback','known_payload_offline_decode'):
        raise ValueError('Unsupported provenance declaration in saved run.')
    capture = manifest_path.parent / "capture.npz"
    if hashlib.sha256(capture.read_bytes()).hexdigest() != manifest.get("capture_sha256"):
        raise ValueError("Capture hash mismatch: the saved I/Q file has changed.")
    with np.load(capture, allow_pickle=False) as raw:
        arrays = {name: raw[name].copy() for name in ARRAY_NAMES if name in raw}
    if not {"tx_iq", "rx_iq", "constellation", "spectrum_frequency_hz", "spectrum_dbfs"} <= arrays.keys():
        raise ValueError("Incomplete capture bundle.")
    return SimpleNamespace(config=manifest["config"], metrics=manifest["metrics"], features=manifest["features"],
                           telemetry=manifest["telemetry"], saved_diagnosis=manifest.get("diagnosis"),
                           metadata=manifest.get('metadata',{}),provenance=manifest['provenance']+' replay', **arrays)


def _svg_plot(x, y, title: str, xlabel: str, ylabel: str, scatter: bool = False) -> str:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if not x.size:
        return ""
    if scatter:
        lo_x, hi_x, lo_y, hi_y = -1.6, 1.6, -1.6, 1.6
    else:
        lo_x, hi_x = float(x.min()), float(x.max())
        lo_y, hi_y = min(-100., float(y.min())), max(0., float(y.max()))
    hi_x = max(hi_x, lo_x + 1e-9)
    hi_y = max(hi_y, lo_y + 1e-9)
    stride = max(1, x.size // 1400)
    px = 60 + (x[::stride] - lo_x) / (hi_x - lo_x) * 510
    py = 250 - (y[::stride] - lo_y) / (hi_y - lo_y) * 200
    marks = "".join(f'<circle cx="{a:.2f}" cy="{b:.2f}" r="1.4" fill="#45d5bd"/>' for a, b in zip(px, py)) if scatter else '<polyline points="' + " ".join(f"{a:.2f},{b:.2f}" for a,b in zip(px,py)) + '" fill="none" stroke="#45d5bd" stroke-width="1.4"/>'
    grid = "".join(f'<line x1="60" y1="{50+i*50}" x2="570" y2="{50+i*50}" stroke="#243447"/><text x="50" y="{54+i*50}" text-anchor="end" fill="#91a6bc" font-size="10">{hi_y-(hi_y-lo_y)*i/4:.1f}</text>' for i in range(5))
    ticks = "".join(f'<text x="{60+i*127.5}" y="269" text-anchor="middle" fill="#91a6bc" font-size="10">{lo_x+(hi_x-lo_x)*i/4:.1f}</text>' for i in range(5))
    return f'<svg viewBox="0 0 620 310" role="img" aria-label="{html.escape(title)}"><text x="60" y="25" fill="#e5edf6" font-size="16">{html.escape(title)}</text>{grid}{ticks}{marks}<text x="310" y="297" text-anchor="middle" fill="#91a6bc">{html.escape(xlabel)}</text><text x="15" y="150" transform="rotate(-90 15 150)" text-anchor="middle" fill="#91a6bc">{html.escape(ylabel)}</text></svg>'


def write_report(result, path: Path, diagnosis: dict | None = None) -> None:
    e = html.escape
    config = json_value(result.config)
    rows = "".join(f"<tr><td>{e(str(k))}</td><td>{e(str(v))}</td></tr>" for k,v in json_value(result.metrics).items())
    cfg = "".join(f"<tr><td>{e(str(k))}</td><td>{e(str(v))}</td></tr>" for k,v in config.items())
    plots = _svg_plot(np.real(result.constellation), np.imag(result.constellation), "Recovered QPSK symbols", "In-phase (normalized)", "Quadrature (normalized)", True)
    plots += _svg_plot(np.asarray(result.spectrum_frequency_hz)/1000, result.spectrum_dbfs, "Received baseband spectrum", "Frequency offset (kHz)", "Bin power (dB re unit complex power)")
    diag = f"<pre>{e(json.dumps(json_value(diagnosis),indent=2))}</pre>" if diagnosis else "<p>No compatible model inference saved for this run. A missing payload reference also makes benchmark BER/reference EVM unavailable.</p>"
    metadata=getattr(result,'metadata',{})
    origin=e(str(metadata.get('original_origin',metadata.get('source_provenance',metadata.get('provenance','synthetic')))))
    processing=e(str(metadata.get('provenance','synthetic')))
    source_note=('This run uses simulated data.' if origin=='synthetic' else 'Source origin is declared by recording metadata; this report does not independently authenticate the source.')
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>RF Link Lab — experiment report</title>
<style>body{{margin:0;background:#0b1320;color:#e5edf6;font:15px/1.6 system-ui}}main{{max-width:1100px;margin:auto;padding:40px}}h1{{font-size:30px}}h2{{font-size:20px;margin-top:32px}}.tag{{color:#45d5bd;font-size:12px;letter-spacing:2px}}.plots{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}svg{{width:100%;background:#101d2d}}table{{width:100%;border-collapse:collapse}}td{{padding:8px 12px;border-bottom:1px solid #243447}}td:first-child{{color:#91a6bc}}pre{{white-space:pre-wrap;background:#101d2d;padding:20px}}@media(max-width:700px){{.plots{{grid-template-columns:1fr}}}}</style>
<main><div class="tag">ORIGIN: {origin} · V{__version__}</div><h1>RF Link Lab</h1>
<p>Processing: {processing}. {source_note} No calibrated RF performance is established.</p>
<div class="plots">{plots}</div><h2>Receiver measurements</h2><table>{rows}</table>
<h2>Recording and experiment settings</h2><table>{cfg}</table><h2>Diagnostic inference</h2>{diag}
<h2>Evidence boundary</h2><p>Benchmark BER and reference EVM require an aligned known payload reference. Missing reference metrics remain unavailable. Pilot EVM uses the public acquisition sequence. Model support is not calibrated probability. Opening a saved run shows recorded metrics; the independent I/Q decode action processes the samples again. Serial-to-modem software loopback does not transmit RF.</p></main></html>'''
    Path(path).write_text(content, encoding="utf-8")
