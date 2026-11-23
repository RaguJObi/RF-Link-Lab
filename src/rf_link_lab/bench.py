"""Portable bench evidence: source declarations and operator checks stay explicit.

Creating or verifying a session never opens a device, runs a receiver, executes a
recording, or authenticates its origin. Hashes detect changes after attachment;
they are not signatures and do not establish where samples were acquired.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import tempfile
from typing import Any
from urllib.parse import quote
import uuid

from . import __version__
from .paths import resource_root


SCHEMA = "rf-link-lab-bench-v1"
DEFAULT_CHECKS = (
    "ubuntu_execution", "nucleo_identity", "firmware_flash", "uart_button_response",
    "bladerf_identification", "bladerf_rx_capture", "conducted_rf_link",
)
LIMITS = (
    "Source declarations are not authenticated. Operator checks are assertions, "
    "not automatic measurements. Offline UART and synthetic I/Q do not prove "
    "physical button operation, device identity, RF transmission, or RF performance. "
    "Attachment verifies and preserves existing files; no receiver or hardware runs."
)
UART_CONTEXT_FIELDS = (
    "stream_error_count", "stream_errors", "stream_errors_truncated",
    "connection_attempt_count", "connection_attempts", "connection_attempts_truncated",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> Any:
    """Take a detached, finite, non-executable JSON snapshot."""
    return json.loads(json.dumps(value, allow_nan=False))


def _digest(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _asset(folder: Path, relative: str) -> Path:
    """Resolve only regular files through plain relative, non-link components."""
    if _linked(folder):
        raise ValueError("Symlink or junction evidence directories are not allowed.")
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or PurePosixPath(relative).is_absolute()
            or PureWindowsPath(relative).is_absolute()
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ValueError(f"Unsafe asset path: {relative!r}")
    candidate = folder
    for part in relative.split("/"):
        candidate = candidate / part
        if _linked(candidate):
            raise ValueError(f"Symlink or junction asset is not allowed: {relative}")
    if not candidate.is_file() or not candidate.resolve().is_relative_to(folder.resolve()):
        raise ValueError(f"Missing or unsafe asset: {relative}")
    return candidate


def _folder(path: Path, manifest_name: str) -> tuple[Path, Path]:
    path = Path(path).expanduser().absolute()
    if _linked(path):
        raise ValueError("Symlink or junction evidence paths are not allowed.")
    manifest = path / manifest_name if path.is_dir() else path
    folder = manifest.parent
    if _linked(folder) or manifest.name != manifest_name:
        raise ValueError(f"Choose a directory or its {manifest_name} manifest.")
    _asset(folder, manifest_name)
    return folder, manifest


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("A recording manifest must be a JSON object.")
    return value


def _iq_evidence(path: Path) -> tuple[Path, list[str], dict[str, Any]]:
    """Inspect hashes and declared metadata only; never load an NPZ or decode."""
    path = Path(path).expanduser().absolute()
    if path.is_dir() or path.name == "run.json":
        folder, manifest_path = _folder(path, "run.json")
        manifest = _read(manifest_path)
        if manifest.get("schema") not in ("rf-link-lab-run-v1", "rf-link-lab-run-v2"):
            raise ValueError("Unsupported I/Q run manifest.")
        capture = _asset(folder, "capture.npz")
        if _digest(capture)["sha256"] != manifest.get("capture_sha256"):
            raise ValueError("I/Q capture hash mismatch.")
        metadata = manifest.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("I/Q metadata must be an object.")
        origin = metadata.get("original_origin", metadata.get("source_provenance", manifest.get("provenance", "unknown")))
        summary = {"format": "saved_run", "declared_origin": origin,
                   "processing": manifest.get("provenance", "unknown"), "metadata": metadata,
                   "config": manifest.get("config", {}), "recorded_metrics": manifest.get("metrics", {}),
                   "capture_sha256": manifest["capture_sha256"]}
        return folder, ["run.json", "capture.npz"], summary
    if path.suffix.lower() not in (".sc16q11", ".bin", ".iq"):
        raise ValueError("Choose run.json or a raw SC16Q11 recording with its hash sidecar.")
    folder = path.parent
    capture = _asset(folder, path.name)
    sidecar_name = path.name + ".json"
    sidecar = _read(_asset(folder, sidecar_name))
    if sidecar.get("file") is not None and sidecar["file"] != path.name:
        raise ValueError("Raw I/Q sidecar file must match the adjacent recording name.")
    details = _digest(capture)
    if details["sha256"] != sidecar.get("sha256"):
        raise ValueError("Raw I/Q capture hash mismatch or missing sidecar SHA256.")
    if sidecar.get("bytes") is not None and sidecar["bytes"] != details["size_bytes"]:
        raise ValueError("Raw I/Q byte count differs from its sidecar.")
    if not details["size_bytes"] or details["size_bytes"] % 4:
        raise ValueError("SC16Q11 capture must contain whole, nonempty I/Q samples.")
    return folder, [path.name, sidecar_name], {
        "format": "raw_sc16q11", "declared_origin": sidecar.get("provenance", sidecar.get("source", "unknown")),
        "metadata": sidecar, "capture_sha256": details["sha256"],
    }


def _identities() -> dict[str, Any]:
    root = resource_root()
    identities: dict[str, Any] = {}
    for name, candidates in {
        "firmware": ("firmware/rf_link_lab_f446re.bin", "artifacts/firmware/rf_link_lab_f446re.bin"),
        "model": ("artifacts/reference/model/model.json",),
    }.items():
        existing = next((root / candidate for candidate in candidates if (root / candidate).is_file()), None)
        identities[name] = {"available": bool(existing), "scope": "local resource identity; use on a board/run is not established"}
        if existing:
            identities[name].update(_digest(existing))
            identities[name]["filename"] = existing.name
    return identities


class BenchSession:
    """An explicit, append-recorded session with a portable finalized report."""

    def __init__(self, folder: Path, manifest: dict[str, Any]):
        self.folder = folder
        self._manifest = manifest

    @classmethod
    def create(cls, output_dir: Path, *, title: str = "Bench session", notes: str = "",
               environment: dict | None = None) -> BenchSession:
        if not isinstance(title, str) or not isinstance(notes, str):
            raise ValueError("Title and notes must be text.")
        if environment is not None and not isinstance(environment, dict):
            raise ValueError("Environment details must be a dictionary.")
        operator_environment = _json(environment or {})
        folder = Path(output_dir).expanduser().absolute()
        if _linked(folder):
            raise ValueError("A bench folder cannot be a symlink or junction.")
        folder.mkdir(parents=True, exist_ok=False)
        manifest = {
            "schema": SCHEMA, "session_id": str(uuid.uuid4()), "title": title,
            "application_version": __version__, "created_utc": _utc(), "finalized_utc": None,
            "status": "in_progress", "finalized": False,
            "environment": {"os": platform.platform(), "architecture": platform.machine(),
                            "python": platform.python_version(), "operator_environment": operator_environment},
            "local_resource_identity": _identities(),
            "notes": [], "checks": [{"name": name, "status": "not_attempted", "details": None,
                                      "origin": "default_not_attempted", "timestamp_utc": None} for name in DEFAULT_CHECKS],
            "probes": [], "evidence": [], "assets": {}, "limits": LIMITS,
        }
        session = cls(folder, manifest)
        session._event("session_created", {"title": title})
        if notes:
            session.add_note(notes)
        return session

    def _assert_open(self) -> None:
        if self._manifest["finalized"]:
            raise ValueError("This bench session is finalized; create a new session for additional evidence.")

    def _save(self) -> None:
        # Replacement is limited to this session's own evolving JSON manifest.
        temporary = self.folder / "bench.json.tmp"
        temporary.write_text(json.dumps(self._manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.folder / "bench.json")

    def _event(self, kind: str, details: dict) -> None:
        with (self.folder / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"timestamp_utc": _utc(), "event": kind, "details": _json(details)}, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._save()

    def add_note(self, text: str) -> None:
        self._assert_open()
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Enter a nonempty operator note.")
        note = {"text": text, "origin": "operator_entered", "timestamp_utc": _utc()}
        self._manifest["notes"].append(note)
        self._event("note_added", note)

    def record_check(self, name: str, status: str, details: Any = None) -> None:
        self._assert_open()
        if not isinstance(name, str) or not name.strip() or status not in ("passed", "failed", "not_attempted"):
            raise ValueError("Check requires a name and status passed, failed, or not_attempted.")
        check = {"name": name, "status": status, "details": _json(details),
                 "origin": "operator_entered", "timestamp_utc": _utc()}
        self._manifest["checks"] = [old for old in self._manifest["checks"] if old["name"] != name] + [check]
        self._event("operator_check_recorded", check)

    def record_probe(self, result: dict) -> None:
        self._assert_open()
        if not isinstance(result, dict):
            raise ValueError("Probe result must be a dictionary.")
        probe = {"timestamp_utc": _utc(), "origin": "submitted_probe_result",
                 "scope": "Result supplied by caller; device identity and streaming are not independently authenticated here.",
                 "result": _json(result)}
        self._manifest["probes"].append(probe)
        self._event("probe_result_recorded", probe)

    def _preserve(self, folder: Path, files: list[str], kind: str, verifier) -> tuple[list[str], Path]:
        before = {name: _digest(_asset(folder, name)) for name in files}
        if folder.resolve().is_relative_to(self.folder.resolve()):
            preserved = folder
        else:
            destination = self.folder / "evidence" / f"{kind}-{uuid.uuid4().hex[:12]}"
            destination.parent.mkdir(exist_ok=True)
            # Stage outside the session, inspect again, then install atomically.
            with tempfile.TemporaryDirectory(prefix="rf_bench_stage_") as temporary:
                stage = Path(temporary)
                for name in files:
                    source = _asset(folder, name)
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open("rb") as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                verifier(stage)
                after = {name: _digest(_asset(stage, name)) for name in files}
                if before != after or before != {name: _digest(_asset(folder, name)) for name in files}:
                    raise ValueError("Recording changed while copying; no evidence was attached.")
                shutil.copytree(stage, destination)
            preserved = destination
        verifier(preserved)
        if before != {name: _digest(_asset(preserved, name)) for name in files}:
            raise ValueError("Recording changed while attaching evidence.")
        relative = [str((preserved / name).relative_to(self.folder)).replace("\\", "/") for name in files]
        for name in relative:
            self._manifest["assets"][name] = _digest(_asset(self.folder, name))
        return relative, preserved

    def add_uart_recording(self, path: Path) -> None:
        self._assert_open()
        from .serial_logging import verify_uart_recording
        folder, manifest_path = _folder(path, "uart.json")
        verified = verify_uart_recording(manifest_path)
        manifest = _read(manifest_path)
        if manifest.get("status") == "active" or manifest.get("status") not in ("complete", "incomplete"):
            raise ValueError("Stop UART recording before attaching it; active logs are not finalized evidence.")
        files = ["uart.json"] + [item["path"] for item in manifest["files"].values()]
        relative, _ = self._preserve(folder, files, "uart", verify_uart_recording)
        record = {"kind": "uart", "manifest": relative[0], "files": relative,
                  "status": manifest["status"], "complete": bool(verified.get("complete")),
                  "summary": _json(manifest.get("summary", {})),
                  "error": manifest.get("last_error", manifest.get("error")),
                  "stream_errors": _json(manifest.get("stream_errors", [])),
                  "started_utc": manifest.get("started_utc"), "ended_utc": manifest.get("ended_utc"),
                  "parser_config": _json(manifest.get("parser_config", {})),
                  "scope": "Recorded UART source labels are declarations; parser success does not verify board identity or physical button operation.",
                  "integrity": "hash_verified_at_attachment", "source_authenticated": False}
        for key in UART_CONTEXT_FIELDS:
            record[key] = _json(manifest.get(key))
        self._manifest["evidence"].append(record)
        self._event("uart_attached", record)

    def add_iq_recording(self, path: Path) -> None:
        self._assert_open()
        folder, files, summary = _iq_evidence(path)
        selector = files[0]
        def verify(directory):
            return _iq_evidence(directory / selector)
        relative, _ = self._preserve(folder, files, "iq", verify)
        record = {"kind": "iq", "manifest": relative[0], "files": relative,
                  "summary": _json(summary), "integrity": "hash_verified_at_attachment",
                  "source_authenticated": False,
                  "scope": "Stored metadata and measurements only; attaching does not rerun the receiver or establish physical RF acquisition."}
        self._manifest["evidence"].append(record)
        self._event("iq_attached", record)

    def add_experiment_recording(self,path: Path) -> None:
        """Preserve a finalized finite-link experiment, including failed trials."""
        self._assert_open()
        from .conducted import verify_conducted_experiment
        folder,manifest_path=_folder(path,'experiment.json')
        verified=verify_conducted_experiment(manifest_path)
        files=['experiment.json']+list(verified['manifest']['assets'])
        relative,_=self._preserve(folder,files,'conducted',verify_conducted_experiment)
        record={'kind':'experiment','manifest':relative[0],'files':relative,
                'summary':_json(verified['summary']),'integrity':'hash_verified_at_attachment',
                'source_authenticated':False,
                'scope':'Finite-link experiment outcome; offline rehearsal, transport completion and known-payload decoding remain separate.'}
        self._manifest['evidence'].append(record)
        self._event('conducted_experiment_attached',record)

    def finalize(self) -> Path:
        if self._manifest["finalized"]:
            verify_bench_session(self.folder)
            return self.folder / "bench.json"
        # Recheck every registered byte; logs may have changed since attachment.
        _verify_assets(self.folder, self._manifest["assets"])
        _verify_evidence(self.folder, self._manifest["evidence"])
        self._event("session_finalized", {"evidence_count": len(self._manifest["evidence"])})
        draft = self._manifest
        candidate = _json(draft)
        candidate.update(finalized_utc=_utc(), status="finalized", finalized=True)
        report_temporary = self.folder / "report.html.tmp"
        report_temporary.write_text(_report(candidate), encoding="utf-8")
        os.replace(report_temporary, self.folder / "report.html")
        for name in ("events.jsonl", "report.html"):
            candidate["assets"][name] = _digest(_asset(self.folder, name))
        self._manifest = candidate
        try:
            self._save()
        except Exception:
            self._manifest = draft
            raise
        verify_bench_session(self.folder)
        return self.folder / "bench.json"


def _verify_assets(folder: Path, assets: dict) -> None:
    if not isinstance(assets, dict):
        raise ValueError("Bench asset inventory must be an object.")
    for relative, expected in assets.items():
        observed = _digest(_asset(folder, relative))
        if observed != expected:
            raise ValueError(f"Bench asset hash or size mismatch: {relative}")


def _verify_evidence(folder: Path, evidence: list) -> None:
    from .serial_logging import verify_uart_recording
    if not isinstance(evidence, list):
        raise ValueError("Bench evidence inventory must be a list.")
    for item in evidence:
        manifest = _asset(folder, item["manifest"])
        if item["kind"] == "uart":
            verified = verify_uart_recording(manifest)
            if verified.get("status") == "active" or verified.get("complete") != item.get("complete"):
                raise ValueError("UART completion status changed or remains active.")
            source = _read(manifest)
            if source.get("summary", {}) != item.get("summary") or source.get("status") != item.get("status"):
                raise ValueError("UART summary differs from attached evidence.")
            if (source.get("last_error", source.get("error")) != item.get("error")
                    or any(source.get(key) != item.get(key) for key in UART_CONTEXT_FIELDS)):
                raise ValueError("UART errors differ from attached evidence.")
        elif item["kind"] == "iq":
            _, _, summary = _iq_evidence(manifest)
            if summary != item.get("summary"):
                raise ValueError("I/Q metadata differs from attached evidence.")
        elif item['kind']=='experiment':
            from .conducted import verify_conducted_experiment
            verified=verify_conducted_experiment(manifest)
            if verified['summary']!=item.get('summary'):raise ValueError('Conducted experiment metadata differs from attached evidence.')
        else:
            raise ValueError("Unsupported bench evidence kind.")


def verify_bench_session(path: Path) -> dict[str, Any]:
    """Verify a finalized portable bundle, without opening devices or decoding."""
    folder, manifest_path = _folder(path, "bench.json")
    manifest = _read(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unsupported bench session schema.")
    if not manifest.get("finalized") or manifest.get("status") != "finalized":
        raise ValueError("Bench session is not finalized; preserve its files and finish the session first.")
    assets = manifest.get("assets")
    _verify_assets(folder, assets)
    if not {"events.jsonl", "report.html"} <= assets.keys():
        raise ValueError("Finalized bench requires hashed event log and report.")
    for item in manifest.get("evidence", []):
        if not isinstance(item.get("files"), list) or item["manifest"] not in item["files"]:
            raise ValueError("Evidence requires an inventoried manifest.")
        for name in item["files"]:
            if name not in assets:
                raise ValueError("Evidence file is absent from the hash inventory.")
            _asset(folder, name)
    _verify_evidence(folder, manifest.get("evidence", []))
    if _asset(folder, "report.html").read_text(encoding="utf-8") != _report(manifest):
        raise ValueError("Bench manifest/report drift: readable report differs from session metadata.")
    return {"schema": SCHEMA, "path": str(manifest_path), "status": "verified",
            "finalized": True, "assets_verified": len(assets), "evidence_count": len(manifest.get("evidence", [])),
            "source_authenticated": False, "hardware_readiness": "not established by hash verification",
            "manifest": manifest}


def _report(manifest: dict) -> str:
    e = html.escape
    def pretty(value):
        return "<pre>" + e(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)) + "</pre>"
    def metrics(values):
        return '<div class="metrics">'+''.join(f'<div><span>{e(label)}</span><strong>{e(str(value) if value is not None else "Unavailable")}</strong></div>' for label,value in values)+'</div>'
    checks = "".join(f"<tr><td>{e(item['name'].replace('_',' '))}</td><td>{e(item['status'].upper())}</td><td>{e(item['origin'])}</td><td>{e(str(item['details'] or '—'))}</td></tr>" for item in manifest["checks"])
    notes = "".join(f"<p><time>{e(item['timestamp_utc'])}</time> — {e(item['text'])}</p>" for item in manifest["notes"]) or "<p>No operator notes.</p>"
    evidence = []
    for item in manifest["evidence"]:
        links = "".join(f'<li><a href="{e(quote(name, safe="/"), quote=True)}">{e(name)}</a><br><small>{manifest["assets"][name]["size_bytes"]} bytes · SHA256 {e(manifest["assets"][name]["sha256"])}</small></li>' for name in item["files"])
        status = item.get("status", "stored recording")
        warning = "<p class=warning>INCOMPLETE — no successful UART acquisition is established. Preserved bytes remain available for fault investigation.</p>" if item.get("complete") is False else ""
        uart_context = ""
        highlights = ""
        if item["kind"] == "uart":
            summary=item['summary']
            highlights='<p>Input source: <strong>'+e(', '.join(summary.get('sources',[])) or 'no received bytes')+'</strong> · File retention: '+e(status)+'</p>'
            highlights+=metrics([(label,summary.get(key,0)) for label,key in [('Valid packets','packets_ok'),('CRC rejected','crc_errors'),('Observed gaps','sequence_gaps'),('Observed input bytes','bytes_received')]])
            highlights+=f"<p>Duplicates: {summary.get('duplicates',0)} · Out of order: {summary.get('out_of_order',0)} · Framing errors: {summary.get('framing_errors',0)} · UART stream failures: {item.get('stream_error_count',0)}</p>"
            if item.get('stream_error_count'):highlights+='<p class="warning">UART stream failures were recorded:</p>'+pretty(item.get('stream_errors',[]))
            uart_context = (f"<p>Recording UTC: {e(str(item.get('started_utc')))} → {e(str(item.get('ended_utc')))}</p>"
                            f"<p>Recording writer error: {e(str(item.get('error')))}</p>"
                            "<p>Connection attempts and stream errors (separate from file completeness; an attempted endpoint is not a verified board):</p>"
                            + pretty({key: item.get(key) for key in UART_CONTEXT_FIELDS})
                            + "<p>Parser configuration:</p>" + pretty(item.get("parser_config", {})))
        elif item['kind']=='experiment':
            values=item['summary']
            highlights='<p>Mode: <strong>'+e(str(values.get('mode')))+'</strong> · Outcome: '+e(str(values.get('experiment_status')))+'</p>'
            highlights+='<p>Declared origin: '+e(str(values.get('declared_origin')))+' · TX requested: '+e(str(values.get('tx_start_requested')))+' · Transport cleanup confirmed: '+e(str(values.get('cleanup_confirmed')))+'</p>'
            if values.get('error'):highlights+='<p class="warning">'+e(str(values['error']))+'</p>'
        else:
            highlights='<p>Declared I/Q origin: <strong>'+e(str(item['summary'].get('declared_origin','unknown')))+'</strong></p>'
            recorded=item['summary'].get('recorded_metrics',{})
            if recorded:
                highlights+=metrics([(label,recorded.get(key,'Unavailable')) for label,key in [('Recorded valid packets','packets_ok'),('Recorded BER','ber'),('Recorded reference EVM (%)','evm_pct')]])
        evidence.append(f"<article><h3>{e(item['kind'].upper())} recording</h3>{highlights}{warning}<p>{e(item['scope'])}</p><details><summary>Full recorded metadata and connection history</summary>{pretty(item['summary'])}{uart_context}</details><details><summary>Evidence files and SHA256 hashes</summary><ul>{links}</ul></details></article>")
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{e(manifest['title'])} — RF Link Lab</title>
<style>body{{margin:0;background:#0b1320;color:#e5edf6;font:16px/1.6 system-ui}}main{{max-width:1100px;margin:auto;padding:36px}}h1{{font-size:32px}}h2{{margin-top:36px}}a{{color:#45d5bd}}pre,article{{background:#101d2d;padding:18px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;font-size:13px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;border-bottom:1px solid #33465b;padding:10px}}.tablewrap{{overflow-x:auto}}.warning{{color:#ffca7a}}time,.metrics span{{color:#9aabc0}}article{{margin-bottom:16px}}.metrics{{display:flex;flex-wrap:wrap;gap:28px;margin:20px 0}}.metrics div{{min-width:130px}}.metrics span,.metrics strong{{display:block}}.metrics strong{{font-size:25px}}details{{margin:16px 0}}summary{{color:#45d5bd;cursor:pointer}}@media(max-width:600px){{main{{padding:18px}}h1{{font-size:26px}}}}</style>
<main><p>RF LINK LAB · BENCH SESSION · {e(manifest['application_version'])}</p><h1>{e(manifest['title'])}</h1>
<p>Created UTC: {e(manifest['created_utc'])}<br>Finalized UTC: {e(str(manifest['finalized_utc']))}</p>
<h2>Evidence boundary</h2><p>{e(LIMITS)}</p><p>A finalized session means its report and evidence inventory are sealed for change detection. It does not mean hardware checks passed. The JSON manifest itself is not signed.</p>
<h2>Preserved recordings</h2>{''.join(evidence) or '<p>No recordings attached.</p>'}
<h2>Operator checks</h2><div class="tablewrap"><table><tr><th>Check</th><th>Status</th><th>Origin</th><th>Details</th></tr>{checks}</table></div>
<h2>Operator notes</h2>{notes}<details><summary>Computer environment</summary>{pretty(manifest['environment'])}</details><details><summary>Local firmware and model identity</summary>{pretty(manifest['local_resource_identity'])}</details>
<details><summary>Submitted device probe results</summary>{pretty(manifest['probes'])}</details>
<h2>Integrity and portability</h2><p>The adjacent bench.json lists SHA256 and byte counts for every linked evidence file, this report, and <a href="events.jsonl">events.jsonl</a>. Copy the complete session folder. Verification checks stored bytes and metadata without replaying the receiver or opening devices.</p></main></html>'''


def demo_bench_session(output_dir: Path) -> Path:
    """Make an honest offline UART + synthetic I/Q example using real APIs."""
    from .serial_link import SerialTelemetrySession, demo_serial_session
    from .simulation import SimulationConfig, run_simulation
    from .storage import save_run
    bench = BenchSession.create(output_dir, title="Offline bench-session example",
                                notes="Software-only demonstration. No serial device, USB probe or RF acquisition was used.")
    uart = SerialTelemetrySession()
    uart.start_recording(bench.folder / "uart")
    demo_serial_session(session=uart)
    uart.stop_recording()
    bench.add_uart_recording(bench.folder / "uart")
    run = save_run(run_simulation(SimulationConfig(seed=42, frames=3)), bench.folder / "synthetic-iq")
    bench.add_iq_recording(run)
    bench.record_check("offline_parser_and_bundle_example", "passed", "Software example created; hardware checks remain NOT_ATTEMPTED.")
    return bench.finalize()
