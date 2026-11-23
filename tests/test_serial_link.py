from queue import Empty, Queue
import threading
import time
from types import SimpleNamespace

import pytest

from rf_link_lab.protocol import Telemetry, TelemetryParser, encode_telemetry
from rf_link_lab.serial_link import (
    SerialLinkError, SerialTelemetrySession, demo_serial_session, list_ports,
)


def frame(sequence, *, button=False):
    return encode_telemetry(Telemetry(sequence, 1234, button))


def eventually(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    assert predicate(), "Reader did not reach the expected state before the test timeout"


class FakeSerial:
    def __init__(self, **kwargs):
        self.settings = kwargs
        self.input = Queue()
        self.closed = False
        self.cancelled = False

    def read(self, count):
        try:
            item = self.input.get(timeout=0.01)
        except Empty:
            return b""
        if isinstance(item, Exception):
            raise item
        return item

    def cancel_read(self):
        self.cancelled = True
        self.input.put(b"")

    def close(self):
        self.closed = True


def make_session(**kwargs):
    connections = []

    def factory(**settings):
        connection = FakeSerial(**settings)
        connections.append(connection)
        return connection

    return SerialTelemetrySession(serial_factory=factory, **kwargs), connections


def test_construction_and_port_discovery_do_not_open_hardware():
    calls = []
    session = SerialTelemetrySession(serial_factory=lambda **settings: calls.append(settings))
    endpoints = list_ports(port_lister=lambda: [
        SimpleNamespace(device="COM2", description="USB serial", hwid="generic"),
        SimpleNamespace(device="COM9", description="STMicroelectronics STLink Virtual COM Port", hwid="usb"),
    ])
    assert endpoints[0]["device"] == "COM9"
    assert endpoints[0]["suggested"]
    assert not endpoints[1]["suggested"]
    assert calls == []
    assert session.snapshot()["status"] == "disconnected"


def test_arbitrary_fragmentation_and_corruption_recover_valid_packets():
    session = SerialTelemetrySession(read_size=5)
    damaged = bytearray(frame(2))
    damaged[-1] ^= 2
    # Invalid version is a framing candidate; ordinary junk is discarded bytes.
    stream = b"noise\xb5\x62\x05\x09" + frame(1) + bytes(damaged) + frame(3, button=True)
    observed = []
    for value in stream:
        observed.extend(session.feed_bytes(bytes([value])))
    snapshot = session.snapshot()
    assert [message.sequence for message in observed] == [1, 3]
    assert snapshot["packets_ok"] == 2
    assert snapshot["crc_errors"] == 1
    assert snapshot["framing_errors"] == 1
    assert snapshot["sequence_gaps"] == 1
    assert snapshot["bytes_received"] == len(stream)
    assert snapshot["discarded_bytes"] == len(stream) - 2 * 17
    assert snapshot["input_source"] == "offline_feed"
    assert snapshot["connected"] is False
    assert snapshot["latest"]["button"] is True


def test_uint32_wrap_forward_gaps_duplicate_and_late_arrival():
    session = SerialTelemetrySession()
    for sequence in (0xFFFFFFFE, 0xFFFFFFFF, 0, 2, 2, 1, 3):
        session.feed_bytes(frame(sequence))
    snapshot = session.snapshot()
    assert [item["sequence_status"] for item in snapshot["recent"]] == [
        "first", "in_order", "in_order", "gap", "duplicate", "out_of_order", "in_order",
    ]
    assert snapshot["sequence_gaps"] == 1
    assert snapshot["duplicates"] == 1
    assert snapshot["out_of_order"] == 1


def test_history_and_duplicate_tracking_are_bounded_and_copied():
    session = SerialTelemetrySession(history_limit=2, sequence_window=3)
    session.feed_bytes(b"".join(frame(number) for number in range(100)))
    snapshot = session.snapshot()
    assert snapshot["packets_ok"] == 100
    assert [item["sequence"] for item in snapshot["recent"]] == [98, 99]
    assert len(session._seen) == len(session._seen_order) == 3
    snapshot["recent"][-1]["sequence"] = 600
    snapshot["latest"]["sequence"] = 700
    assert session.snapshot()["latest"]["sequence"] == 99
    parser = TelemetryParser()
    assert [message.sequence for message in parser.feed(b"".join(session.recent_frames()))] == [98, 99]
    session.feed_bytes(frame(0))
    assert session.snapshot()["out_of_order"] == 1
    assert session.snapshot()["duplicates"] == 0


def test_worker_reads_fragments_persistently_then_releases_port():
    session, connections = make_session()
    assert session.start("COM7")["connected"]
    connection = connections[0]
    assert connection.settings == {"port": "COM7", "baudrate": 115200, "timeout": 0.1, "write_timeout": 1.0}
    connection.input.put(frame(8)[:6])
    connection.input.put(frame(8)[6:] + frame(9)[:3])
    connection.input.put(frame(9)[3:])
    eventually(lambda: session.snapshot()["packets_ok"] == 2)
    with pytest.raises(SerialLinkError, match="Stop"):
        session.feed_bytes(frame(10))
    snapshot = session.stop()
    assert not snapshot["connected"]
    assert snapshot["status"] == "disconnected"
    assert connection.closed and connection.cancelled
    assert snapshot["packets_ok"] == 2


def test_disconnect_records_error_and_never_reopens_automatically():
    session, connections = make_session()
    session.start("COM5")
    connection = connections[0]
    connection.input.put(frame(1))
    connection.input.put(OSError("USB cable removed"))
    eventually(lambda: session.snapshot()["status"] == "error")
    assert session.snapshot()["packets_ok"] == 1
    assert "USB cable removed" in session.snapshot()["last_error"]
    assert not session.snapshot()["connected"]
    eventually(lambda: connection.closed)
    assert len(connections) == 1
    session.start("COM5")
    assert len(connections) == 2
    assert session.snapshot()["last_error"] is None
    session.stop()


def test_port_change_discards_parser_fragment_and_resets_sequence_baseline():
    session, connections = make_session()
    session.start("COM1")
    connections[0].input.put(frame(100) + frame(101)[:10])
    eventually(lambda: session.snapshot()["bytes_received"] == 27)
    session.start("COM2", 57600)
    assert connections[0].closed
    connections[1].input.put(frame(101)[10:] + frame(0))
    eventually(lambda: session.snapshot()["packets_ok"] == 2)
    snapshot = session.stop()
    assert snapshot["latest"]["sequence"] == 0
    assert snapshot["latest"]["sequence_status"] == "first"
    assert snapshot["sequence_gaps"] == 0
    assert snapshot["baudrate"] == 57600
    assert snapshot["port"] == "COM2"


def test_reconnect_preserves_totals_and_explicit_reset_clears_them():
    session, connections = make_session()
    session.feed_bytes(frame(10) + frame(12))
    session.start("COM1")
    with pytest.raises(SerialLinkError, match="Stop"):
        session.reset_statistics()
    connections[0].input.put(frame(0))
    eventually(lambda: session.snapshot()["packets_ok"] == 3)
    session.stop()
    assert session.snapshot()["sequence_gaps"] == 1
    assert session.snapshot()["latest"]["sequence_status"] == "first"
    snapshot = session.reset_statistics()
    assert snapshot["packets_ok"] == snapshot["bytes_received"] == 0
    assert snapshot["recent"] == []
    assert snapshot["latest"] is None


def test_hardware_frames_can_exclude_retained_offline_and_previous_connection_data():
    session, connections = make_session()
    session.feed_bytes(frame(100))
    session.start("COM1")
    connections[0].input.put(frame(0))
    eventually(lambda: session.snapshot()["packets_ok"] == 2)
    session.start("COM2")
    connections[1].input.put(frame(5))
    eventually(lambda: session.snapshot()["packets_ok"] == 3)
    session.stop()
    parser = TelemetryParser()
    selected = session.recent_frames(current_session_only=True, source="serial")
    assert [message.sequence for message in parser.feed(b"".join(selected))] == [5]
    assert len(session.recent_frames()) == 3
    assert session.snapshot()["recent"][-1]["session_id"] == session.snapshot()["session_id"]


def test_open_failure_is_actionable_without_connected_state():
    def fail(**settings):
        raise OSError("permission denied")

    session = SerialTelemetrySession(serial_factory=fail)
    with pytest.raises(SerialLinkError, match="permission denied"):
        session.start("/dev/ttyACM0")
    snapshot = session.snapshot()
    assert not snapshot["connected"]
    assert snapshot["status"] == "error"
    assert snapshot["port"] == "/dev/ttyACM0"
    session.stop()


def test_stuck_driver_has_bounded_stop_and_blocks_second_open():
    release = threading.Event()
    entered = threading.Event()

    class StalledSerial(FakeSerial):
        def read(self, count):
            entered.set()
            release.wait(timeout=2)
            return frame(99)

    connections = []

    def factory(**settings):
        connection = StalledSerial(**settings)
        connections.append(connection)
        return connection

    session = SerialTelemetrySession(serial_factory=factory)
    try:
        session.start("COM1")
        assert entered.wait(timeout=1)
        start = time.monotonic()
        with pytest.raises(SerialLinkError, match="did not stop"):
            session.stop(timeout_s=0.01)
        assert time.monotonic() - start < 0.5
        assert not session.snapshot()["connected"]
        with pytest.raises(SerialLinkError, match="did not stop"):
            session.start("COM2")
        assert len(connections) == 1
    finally:
        release.set()
        eventually(lambda: not session._thread.is_alive())
        session.stop()
    assert session.snapshot()["packets_ok"] == 0


def test_demonstration_uses_protocol_and_reports_offline_evidence():
    snapshot = demo_serial_session()
    assert snapshot["packets_ok"] == 6
    assert snapshot["crc_errors"] == 1
    assert snapshot["sequence_gaps"] == 1
    assert snapshot["out_of_order"] == 1
    assert snapshot["duplicates"] == 1
    assert snapshot["input_source"] == "offline_feed"
    assert not snapshot["connected"]
    assert snapshot["latest"]["sequence"] == 14


def test_demonstration_can_populate_gui_session_without_changing_pipeline():
    session = SerialTelemetrySession()
    snapshot = demo_serial_session(session=session)
    assert session.snapshot() == snapshot
    assert len(session.recent_frames(current_session_only=True, source="offline_feed")) == 6


@pytest.mark.parametrize("kwargs", [
    {"history_limit": 0}, {"sequence_window": False}, {"read_size": 100000},
    {"read_timeout_s": 0}, {"read_timeout_s": float("nan")},
])
def test_session_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        SerialTelemetrySession(**kwargs)
