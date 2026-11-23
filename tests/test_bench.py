"""Bench reports preserve evidence and provenance without opening hardware."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from rf_link_lab.bench import BenchSession, demo_bench_session, verify_bench_session


def test_bundled_offline_example_verifies_and_replays():
    from rf_link_lab.paths import resource_root
    from rf_link_lab.serial_logging import replay_uart_recording
    folder=resource_root()/'artifacts/reference/bench-offline'
    assert verify_bench_session(folder)['evidence_count']==2
    assert replay_uart_recording(folder/'uart')['matches']


def _saved_iq(folder: Path, *, origin="synthetic") -> Path:
    folder.mkdir()
    # This attachment layer inspects hashes/JSON only; it must not deserialize.
    capture = folder / "capture.npz"
    capture.write_bytes(b"opaque capture bytes; never deserialize or regenerate")
    manifest = {"schema": "rf-link-lab-run-v2", "provenance": origin,
                "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
                "config": {"sample_rate": 200000}, "metadata": {"provenance": origin},
                "metrics": {"packets_ok": 3}}
    (folder / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder / "run.json"


def _uart(folder: Path):
    from rf_link_lab.serial_link import SerialTelemetrySession, demo_serial_session
    session = SerialTelemetrySession()
    session.start_recording(folder)
    demo_serial_session(session=session)
    session.stop_recording()
    return folder


def test_empty_session_is_explicitly_unattempted_and_exclusive(tmp_path):
    bench = BenchSession.create(tmp_path / "empty", environment={"bench": "operator label"})
    draft = json.loads((bench.folder / "bench.json").read_text())
    assert not draft["finalized"]
    assert all(check["status"] == "not_attempted" for check in draft["checks"])
    with pytest.raises(ValueError, match="not finalized"):
        verify_bench_session(bench.folder)
    with pytest.raises(FileExistsError):
        BenchSession.create(bench.folder)
    path = bench.finalize()
    verified = verify_bench_session(path)
    assert verified["assets_verified"] == 2
    assert verified["evidence_count"] == 0
    assert not verified["source_authenticated"]
    assert bench.finalize() == path
    with pytest.raises(ValueError, match="finalized"):
        bench.add_note("too late")


def test_mixed_evidence_is_portable_after_originals_removed(tmp_path):
    uart = _uart(tmp_path / "original-uart")
    iq = _saved_iq(tmp_path / "original-iq")
    bench = BenchSession.create(tmp_path / "bench")
    bench.add_uart_recording(uart)
    bench.add_iq_recording(iq)
    bench.record_probe({"available": False, "error": "offline example"})
    bench.record_check("uart_button_response", "not_attempted", "No physical board used")
    bench.add_note("Move the complete folder to another computer")
    bench.finalize()
    shutil.rmtree(uart)
    shutil.rmtree(iq.parent)
    shutil.copytree(bench.folder, tmp_path / "portable")
    verified = verify_bench_session(tmp_path / "portable")
    assert verified["evidence_count"] == 2
    records = verified["manifest"]["evidence"]
    assert records[0]["summary"]["packets_ok"] == 6
    assert records[0]["summary"]["crc_errors"] == 1
    assert records[1]["summary"]["declared_origin"] == "synthetic"
    assert not any(Path(name).is_absolute() for item in records for name in item["files"])
    report = (tmp_path / "portable" / "report.html").read_text(encoding="utf-8")
    assert "offline_feed" in report
    assert "not authenticated" in report
    assert "physical button" in report


def test_already_internal_recording_is_registered_without_duplicate(tmp_path):
    bench = BenchSession.create(tmp_path / "bench")
    iq = _saved_iq(bench.folder / "iq")
    bench.add_iq_recording(iq)
    result = verify_bench_session(bench.finalize())
    assert result["manifest"]["evidence"][0]["manifest"] == "iq/run.json"
    assert not (bench.folder / "evidence").exists()


@pytest.mark.parametrize("target", ["report.html", "events.jsonl", "capture.npz", "run.json"])
def test_changed_assets_are_rejected(tmp_path, target):
    bench = BenchSession.create(tmp_path / "bench")
    iq = _saved_iq(bench.folder / "iq")
    bench.add_iq_recording(iq)
    path = bench.finalize()
    asset = bench.folder / target if target in ("report.html", "events.jsonl") else iq.parent / target
    with asset.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="hash or size mismatch"):
        verify_bench_session(path)


def test_changed_evidence_before_finalization_is_rejected(tmp_path):
    bench = BenchSession.create(tmp_path / "bench")
    iq = _saved_iq(bench.folder / "iq")
    bench.add_iq_recording(iq)
    (iq.parent / "capture.npz").write_bytes(b"changed while session open")
    with pytest.raises(ValueError, match="hash or size mismatch"):
        bench.finalize()
    assert not json.loads((bench.folder / "bench.json").read_text())["finalized"]


@pytest.mark.parametrize("unsafe", ["../outside", "/tmp/outside", "C:/outside", "a\\b", "a/../b", "./report.html", "a//b", "file:outside"])
def test_verification_rejects_unsafe_asset_paths(tmp_path, unsafe):
    bench = BenchSession.create(tmp_path / "bench")
    path = bench.finalize()
    manifest = json.loads(path.read_text())
    manifest["assets"][unsafe] = {"sha256": "0" * 64, "size_bytes": 0}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe asset path"):
        verify_bench_session(path)


def test_symlink_asset_is_rejected(tmp_path):
    bench = BenchSession.create(tmp_path / "bench")
    path = bench.finalize()
    report = bench.folder / "report.html"
    outside = tmp_path / "outside.html"
    outside.write_bytes(report.read_bytes())
    report.unlink()
    try:
        report.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege is not enabled")
    with pytest.raises(ValueError, match="Symlink or junction"):
        verify_bench_session(path)


def test_report_escapes_operator_and_imported_content(tmp_path):
    attack = '<script>alert("unsafe")</script>'
    bench = BenchSession.create(tmp_path / "bench", title=attack, notes=attack, environment={"label": attack})
    bench.record_probe({"info": attack})
    bench.record_check(attack, "failed", attack)
    bench.add_iq_recording(_saved_iq(tmp_path / "iq", origin=attack))
    bench.finalize()
    report = (bench.folder / "report.html").read_text(encoding="utf-8")
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    verified = verify_bench_session(bench.folder)
    check = verified["manifest"]["checks"][-1]
    assert check["origin"] == "operator_entered"
    assert check["status"] == "failed"


def test_raw_sidecar_hash_and_safe_filename_are_required(tmp_path):
    raw = tmp_path / 'capture space#&λ.sc16q11'
    raw.write_bytes(b"\0" * 16)
    metadata = {"file": raw.name, "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "bytes": 16, "source": "hardware_rx", "requested": {"sample_rate": 1000000}}
    sidecar = raw.with_suffix(raw.suffix + ".json")
    sidecar.write_text(json.dumps(metadata))
    bench = BenchSession.create(tmp_path / "bench")
    bench.add_iq_recording(raw)
    bench.finalize()
    report = (bench.folder / "report.html").read_text(encoding="utf-8")
    assert "%23%26%CE%BB.sc16q11" in report
    assert "hardware_rx" in report
    metadata["file"] = "../another.sc16q11"
    sidecar.write_text(json.dumps(metadata))
    fresh = BenchSession.create(tmp_path / "new")
    with pytest.raises(ValueError, match="match the adjacent"):
        fresh.add_iq_recording(raw)
    metadata["file"] = raw.name
    metadata["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="hash mismatch"):
        fresh.add_iq_recording(raw)


def test_record_check_validates_status_and_keeps_assertion_origin(tmp_path):
    bench = BenchSession.create(tmp_path / "bench")
    with pytest.raises(ValueError, match="status"):
        bench.record_check("button", "probably")
    with pytest.raises(ValueError, match="nonempty"):
        bench.add_note(" ")
    bench.record_check("nucleo_identity", "passed", "Operator read the board label")
    manifest = verify_bench_session(bench.finalize())["manifest"]
    check = next(item for item in manifest["checks"] if item["name"] == "nucleo_identity")
    assert check["origin"] == "operator_entered"
    assert len([item for item in manifest["checks"] if item["name"] == "nucleo_identity"]) == 1
    assert any(item["status"] == "not_attempted" for item in manifest["checks"])


def test_active_uart_is_rejected_and_incomplete_is_explicit(tmp_path):
    from rf_link_lab.serial_link import SerialTelemetrySession, demo_serial_session
    from rf_link_lab.serial_logging import verify_uart_recording
    session = SerialTelemetrySession()
    session.start_recording(tmp_path / "uart", max_raw_bytes=20)
    demo_serial_session(session=session)
    bench = BenchSession.create(tmp_path / "bench")
    with pytest.raises(ValueError):
        bench.add_uart_recording(tmp_path / "uart")
    session.stop_recording()
    manifest_path = tmp_path / "uart" / "uart.json"
    assert not verify_uart_recording(manifest_path)["complete"]
    bench.add_uart_recording(manifest_path)
    verified = verify_bench_session(bench.finalize())
    assert not verified["manifest"]["evidence"][0]["complete"]
    assert "byte limit reached" in verified["manifest"]["evidence"][0]["error"]
    assert "INCOMPLETE" in (bench.folder / "report.html").read_text()
    assert "byte limit reached" in (bench.folder / "report.html").read_text()


def test_uart_open_failure_remains_visible_when_log_files_complete(tmp_path):
    from rf_link_lab.serial_link import SerialLinkError, SerialTelemetrySession
    def failed_connection(**settings):
        raise OSError("USB cable unavailable in fake transport")
    uart = SerialTelemetrySession(serial_factory=failed_connection)
    uart.start_recording(tmp_path / "uart")
    with pytest.raises(SerialLinkError):
        uart.start("fake-port-for-test")
    original = uart.stop_recording()
    assert original["complete"]
    bench = BenchSession.create(tmp_path / "bench")
    bench.add_uart_recording(tmp_path / "uart")
    manifest = verify_bench_session(bench.finalize())["manifest"]
    # File completeness and successful hardware receipt are separate facts.
    assert manifest["evidence"][0]["complete"]
    assert manifest["evidence"][0]["summary"]["packets_ok"] == 0
    assert manifest["evidence"][0]["connection_attempt_count"] == 1
    assert manifest["evidence"][0]["connection_attempts"][0]["port"] == "fake-port-for-test"
    assert manifest["evidence"][0]["stream_error_count"] == 1
    assert all(check["status"] == "not_attempted" for check in manifest["checks"])
    report = (bench.folder / "report.html").read_text()
    assert "USB cable unavailable" in report


def test_manifest_report_drift_is_rejected(tmp_path):
    bench = BenchSession.create(tmp_path / "bench", title="Original title")
    path = bench.finalize()
    manifest = json.loads(path.read_text())
    manifest["title"] = "Accidental later edit"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest/report drift"):
        verify_bench_session(path)


@pytest.mark.parametrize("failure", ["report", "manifest"])
def test_finalization_can_retry_after_transient_write_failure(tmp_path, monkeypatch, failure):
    bench = BenchSession.create(tmp_path / "bench")
    original_write = Path.write_text
    def fail_report_once(path, *args, **kwargs):
        if path == bench.folder / "report.html.tmp":
            raise OSError("transient report disk failure")
        return original_write(path, *args, **kwargs)
    original_save = bench._save
    def fail_manifest_once():
        if bench._manifest["finalized"]:
            raise OSError("transient manifest disk failure")
        return original_save()
    with monkeypatch.context() as patch:
        if failure == "report":
            patch.setattr(Path, "write_text", fail_report_once)
        else:
            patch.setattr(bench, "_save", fail_manifest_once)
        with pytest.raises(OSError, match="transient"):
            bench.finalize()
    assert not bench._manifest["finalized"]
    bench.add_note("Retried after transient disk failure")
    assert verify_bench_session(bench.finalize())["status"] == "verified"


def test_demo_creates_real_recording_apis_and_no_hardware_claims(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An offline bench demo must never open serial or RF hardware")
    import serial
    import rf_link_lab.hardware as hardware
    monkeypatch.setattr(serial, "Serial", forbidden)
    monkeypatch.setattr(hardware, "probe_bladerf", forbidden)
    monkeypatch.setattr(hardware, "capture_rx", forbidden)
    path = demo_bench_session(tmp_path / "demo")
    result = verify_bench_session(path)
    manifest = result["manifest"]
    assert result["evidence_count"] == 2
    assert not manifest["probes"]
    assert all(item["status"] == "not_attempted" for item in manifest["checks"] if item["name"] != "offline_parser_and_bundle_example")
    uart = manifest["evidence"][0]
    assert uart["summary"]["packets_ok"] == 6
    assert uart["summary"]["crc_errors"] == 1
    assert manifest["evidence"][1]["summary"]["declared_origin"] == "synthetic"
    events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "session_created"
    assert events[-1]["event"] == "session_finalized"
