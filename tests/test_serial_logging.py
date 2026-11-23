import hashlib
import json
from queue import Empty, Queue
import time

import pytest

from rf_link_lab.protocol import Telemetry, encode_telemetry
from rf_link_lab.serial_link import SerialLinkError, SerialTelemetrySession, demo_serial_session
from rf_link_lab.serial_logging import replay_uart_recording, verify_uart_recording


def frame(sequence):
    return encode_telemetry(Telemetry(sequence, 1234, bool(sequence % 2)))


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.002)
    assert predicate()


class FakeSerial:
    def __init__(self, **settings):
        self.input = Queue()

    def read(self, _size):
        try:
            data = self.input.get(timeout=.01)
        except Empty:
            return b""
        if isinstance(data, Exception):
            raise data
        return data

    def cancel_read(self):
        self.input.put(b"")

    def close(self):
        pass


def serial_session(**options):
    connections = []

    def factory(**settings):
        connection = FakeSerial(**settings)
        connections.append(connection)
        return connection

    return SerialTelemetrySession(serial_factory=factory, **options), connections


def rewrite_asset_manifest(root, key):
    """Change declared hashes to exercise structural checks beyond hashing."""
    path = root / "uart.json"
    manifest = json.loads(path.read_text())
    asset = root / manifest["files"][key]["path"]
    manifest["files"][key]["size_bytes"] = asset.stat().st_size
    manifest["files"][key]["sha256"] = hashlib.sha256(asset.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))


def test_recording_preserves_exact_junk_corruption_and_fragmentation(tmp_path):
    root = tmp_path / "capture"
    session = SerialTelemetrySession(history_limit=2, read_size=5)
    session.start_recording(root)
    bad = bytearray(frame(1))
    bad[-1] ^= 1
    stream = b"junk\xb5\x62\x02\x09" + frame(0) + bytes(bad) + frame(2)
    for offset in range(0, len(stream), 7):
        session.feed_bytes(stream[offset:offset + 7])
    manifest = session.stop_recording()
    assert manifest["status"] == "complete"
    assert (root / "raw.bin").read_bytes() == stream
    assert manifest["summary"]["packets_ok"] == 2
    assert manifest["summary"]["crc_errors"] == 1
    assert manifest["summary"]["framing_errors"] == 1
    assert manifest["summary"]["sources"] == ["offline_feed"]
    assert manifest["summary"]["sequence_gaps"] == 1
    assert verify_uart_recording(root / "uart.json")["complete"]
    replay = replay_uart_recording(root)
    assert replay["matches"]
    assert replay["replayed_summary"] == manifest["summary"]
    assert replay["replay_source"] == "offline_replay"
    assert session.snapshot()["recording"]["status"] == "complete"


def test_all_valid_packets_are_persistent_beyond_bounded_history(tmp_path):
    root = tmp_path / "many"
    session = SerialTelemetrySession(history_limit=2)
    session.start_recording(root)
    numbers = [0xFFFFFFFE, 0xFFFFFFFF, 0, 2, 2, 1, 3] + list(range(4, 200))
    session.feed_bytes(b"".join(frame(number) for number in numbers))
    session.stop_recording()
    assert len(session.snapshot()["recent"]) == 2
    packets = [json.loads(line) for line in (root / "packets.jsonl").read_text().splitlines()]
    assert len(packets) == len(numbers)
    assert [row["sequence"] for row in packets] == numbers
    assert packets[3]["sequence_status"] == "gap"
    assert packets[4]["sequence_status"] == "duplicate"
    assert packets[5]["sequence_status"] == "out_of_order"
    assert replay_uart_recording(root)["matches"]


def test_recording_clears_prior_offline_history_and_partial_frame(tmp_path):
    session = SerialTelemetrySession()
    session.feed_bytes(frame(100) + frame(101)[:10])
    previous_id = session.snapshot()["session_id"]
    session.start_recording(tmp_path / "fresh")
    snapshot = session.snapshot()
    assert snapshot["packets_ok"] == snapshot["bytes_received"] == 0
    assert snapshot["recent"] == []
    assert snapshot["input_source"] is None
    assert snapshot["session_id"] > previous_id
    session.feed_bytes(frame(101)[10:] + frame(0))
    manifest = session.stop_recording()
    assert manifest["summary"]["packets_ok"] == 1
    assert session.snapshot()["latest"]["sequence_status"] == "first"
    assert replay_uart_recording(tmp_path / "fresh")["matches"]


def test_reconnect_and_offline_transition_preserve_source_boundaries(tmp_path):
    root = tmp_path / "reconnect"
    session, connections = serial_session(read_size=7)
    session.start_recording(root)
    session.start("first")
    connections[0].input.put(frame(90) + frame(91)[:10])
    eventually(lambda: session.snapshot()["bytes_received"] == 27)
    session.stop()
    assert session.snapshot()["recording"]["status"] == "active"
    session.start("second")
    connections[1].input.put(frame(91)[10:] + frame(0))
    eventually(lambda: session.snapshot()["packets_ok"] == 2)
    session.stop()
    session.feed_bytes(frame(5))
    manifest = session.stop_recording()
    assert manifest["summary"]["segment_count"] == 3
    assert manifest["summary"]["segments_by_source"] == {"serial": 2, "offline_feed": 1}
    assert manifest["summary"]["sources"] == ["offline_feed", "serial"]
    packets = [json.loads(line) for line in (root / "packets.jsonl").read_text().splitlines()]
    assert [row["sequence"] for row in packets] == [90, 0, 5]
    assert [row["sequence_status"] for row in packets] == ["first"] * 3
    assert len({row["session_id"] for row in packets}) == 3
    assert replay_uart_recording(root)["matches"]


def test_offline_stop_boundary_prevents_joining_parser_fragments(tmp_path):
    session = SerialTelemetrySession()
    root = tmp_path / "boundary"
    session.start_recording(root)
    session.feed_bytes(frame(1)[:10])
    session.stop()
    session.feed_bytes(frame(1)[10:] + frame(2))
    manifest = session.stop_recording()
    assert manifest["summary"]["packets_ok"] == 1
    assert replay_uart_recording(root)["matches"]


def test_lifecycle_open_failure_read_error_and_reconnect_are_recorded(tmp_path):
    root = tmp_path / "errors"
    session, connections = serial_session()
    session.start_recording(root)
    session.start("first")
    connections[0].input.put(frame(1)[:5])
    connections[0].input.put(OSError("cable removed"))
    eventually(lambda: session.snapshot()["status"] == "error")
    session.start("second")
    connections[1].input.put(frame(1)[5:] + frame(9))
    eventually(lambda: session.snapshot()["packets_ok"] == 1)
    session.stop()
    session.stop_recording()
    verified = verify_uart_recording(root)
    assert verified["complete"]
    assert verified["stream_error_count"] == 1
    assert "cable removed" in verified["stream_errors"][0]["error"]
    saved = json.loads((root / "uart.json").read_text())
    assert saved["last_error"] is None  # Recorder success is independent of the stream failure.
    assert saved["connection_attempt_count"] == 2
    assert [item["port"] for item in saved["connection_attempts"]] == ["first", "second"]
    assert all(item["baudrate"] == 115200 for item in saved["connection_attempts"])
    events = [json.loads(line) for line in (root / "chunks.jsonl").read_text().splitlines()]
    assert any(row["kind"] == "read_error" and "cable removed" in row["error"] for row in events)
    assert replay_uart_recording(root)["matches"]

    def fail(**_settings):
        raise OSError("access denied")

    other = SerialTelemetrySession(serial_factory=fail)
    other.start_recording(tmp_path / "open-error")
    with pytest.raises(SerialLinkError):
        other.start("bad")
    other.stop_recording()
    events = (tmp_path / "open-error" / "chunks.jsonl").read_text()
    assert '"kind": "open_error"' in events
    assert replay_uart_recording(tmp_path / "open-error")["matches"]
    assert verify_uart_recording(tmp_path / "open-error")["stream_error_count"] == 1
    assert verify_uart_recording(tmp_path / "open-error")["connection_attempts"][0]["port"] == "bad"


def test_recording_refuses_live_start_overwrite_and_counter_reset(tmp_path):
    session, _connections = serial_session()
    session.start("fake")
    try:
        with pytest.raises(SerialLinkError, match="Stop"):
            session.start_recording(tmp_path / "live")
        assert not (tmp_path / "live").exists()
    finally:
        session.stop()
    root = tmp_path / "capture"
    session.start_recording(root)
    with pytest.raises(SerialLinkError, match="Finalize"):
        session.reset_statistics()
    with pytest.raises(SerialLinkError, match="Finalize"):
        session.start_recording(tmp_path / "another")
    session.feed_bytes(frame(1))
    session.stop_recording()
    before = (root / "raw.bin").read_bytes()
    with pytest.raises(SerialLinkError, match="start UART"):
        session.start_recording(root)
    assert (root / "raw.bin").read_bytes() == before
    assert session.stop_recording() is None


def test_raw_limit_and_event_limit_fail_visibly_without_stopping_parser(tmp_path):
    session = SerialTelemetrySession()
    root = tmp_path / "limited"
    session.start_recording(root, max_raw_bytes=17)
    session.feed_bytes(frame(1))
    session.feed_bytes(frame(2))
    snapshot = session.snapshot()
    assert snapshot["packets_ok"] == 2
    assert snapshot["recording"]["status"] == "error"
    assert "byte limit" in snapshot["recording"]["last_error"]
    manifest = session.stop_recording()
    assert manifest["status"] == "incomplete"
    assert manifest["summary"]["bytes_received"] == 34
    assert manifest["bytes_written"] == 17
    assert not replay_uart_recording(root)["matches"]
    assert verify_uart_recording(root)["files_verified"]
    other = SerialTelemetrySession()
    other.start_recording(tmp_path / "events", max_events=2)
    other.feed_bytes(frame(1))
    assert "event limit" in other.snapshot()["recording"]["last_error"]
    assert other.stop_recording()["status"] == "incomplete"


def test_write_failure_is_visible_and_finalized_as_incomplete(tmp_path):
    root = tmp_path / "disk-full"
    session = SerialTelemetrySession()
    session.start_recording(root)
    original = session._recorder._streams["raw"]

    class FullDisk:
        def write(self, _data):
            raise OSError("disk full")

        def close(self):
            original.close()

    session._recorder._streams["raw"] = FullDisk()
    session.feed_bytes(frame(1))
    assert session.snapshot()["packets_ok"] == 1
    assert "disk full" in session.snapshot()["recording"]["last_error"]
    manifest = session.stop_recording()
    assert not manifest["complete"]
    assert manifest["status"] == "incomplete"
    assert verify_uart_recording(root)["complete"] is False


def test_manifest_write_failure_remains_visible_without_false_success(tmp_path, monkeypatch):
    session = SerialTelemetrySession()
    root = tmp_path / "manifest-full"
    session.start_recording(root)
    session.feed_bytes(frame(1))

    def fail(_manifest):
        raise OSError("manifest filesystem is full")

    monkeypatch.setattr(session._recorder, "_write_manifest", fail)
    manifest = session.stop_recording()
    assert manifest["status"] == "incomplete"
    assert "manifest filesystem is full" in manifest["last_error"]
    assert session.snapshot()["recording"]["status"] == "incomplete"
    # A file that could not be replaced retains 'active', never 'complete'.
    assert json.loads((root / "uart.json").read_text())["complete"] is False


def test_short_json_event_write_cannot_be_reported_complete(tmp_path):
    session = SerialTelemetrySession()
    root = tmp_path / "short-event"
    session.start_recording(root)
    original = session._recorder._streams["chunks"]

    class ShortWriter:
        def write(self, data):
            return original.write(data[:5])

        def flush(self):
            original.flush()

        def close(self):
            original.close()

    session._recorder._streams["chunks"] = ShortWriter()
    session.feed_bytes(frame(1))
    assert "short write" in session.snapshot()["recording"]["last_error"]
    assert session.stop_recording()["status"] == "incomplete"
    assert not json.loads((root / "uart.json").read_text())["complete"]


def test_tampered_hashes_and_path_traversal_are_rejected(tmp_path):
    root = tmp_path / "tamper"
    session = SerialTelemetrySession()
    session.start_recording(root)
    demo_serial_session(session=session)
    session.stop_recording()
    raw = root / "raw.bin"
    original = raw.read_bytes()
    raw.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(ValueError, match="SHA-256"):
        verify_uart_recording(root)
    raw.write_bytes(original)
    path = root / "uart.json"
    manifest = json.loads(path.read_text())
    manifest["files"]["raw"]["path"] = "../outside.bin"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe"):
        verify_uart_recording(root)


def test_replay_checks_packet_content_and_event_source_not_only_file_hashes(tmp_path):
    root = tmp_path / "content"
    session = SerialTelemetrySession()
    session.start_recording(root)
    session.feed_bytes(frame(1))
    session.stop_recording()
    packets = root / "packets.jsonl"
    value = json.loads(packets.read_text())
    value["sequence"] = 400
    packets.write_text(json.dumps(value) + "\n")
    rewrite_asset_manifest(root, "packets")
    assert verify_uart_recording(root)["files_verified"]
    assert not replay_uart_recording(root)["matches"]
    chunks = root / "chunks.jsonl"
    events = [json.loads(line) for line in chunks.read_text().splitlines()]
    next(item for item in events if item["kind"] == "data")["source"] = "serial"
    chunks.write_text("".join(json.dumps(item) + "\n" for item in events))
    rewrite_asset_manifest(root, "chunks")
    with pytest.raises(ValueError, match="source segment"):
        verify_uart_recording(root)


def test_replay_never_opens_a_serial_endpoint(tmp_path, monkeypatch):
    root = tmp_path / "offline"
    session = SerialTelemetrySession()
    session.start_recording(root)
    demo_serial_session(session=session)
    session.stop_recording()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Replay must not open or enumerate a device")

    import serial
    import serial.tools.list_ports
    monkeypatch.setattr(serial, "Serial", forbidden)
    monkeypatch.setattr(serial.tools.list_ports, "comports", forbidden)
    assert replay_uart_recording(root)["matches"]
