"""Persistent receive-only STM32 telemetry, with explicit hardware boundaries.

Importing this module, making a session, and feeding test bytes open no ports.
``start`` opens only the selected port. There is no flashing, UART transmission,
automatic reconnect, or automatic choice of a discovered device.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Callable

from .protocol import Telemetry, TelemetryParser, encode_telemetry
from .serial_logging import DEFAULT_MAX_EVENTS, DEFAULT_MAX_RAW_BYTES, UARTRecorder


class SerialLinkError(RuntimeError):
    """A serial session could not open, close, or stop within its time limit."""


def demo_serial_session(*, history_limit: int = 100, session: SerialTelemetrySession | None = None) -> dict[str, Any]:
    """Exercise framing, CRC rejection and recovery without opening a device.

    The returned source is explicitly ``offline_feed``. Sequence 12 is damaged;
    13 arrives before 12's replacement, then is repeated. UART fragmentation
    deliberately splits packets at arbitrary boundaries.
    """
    session = session if session is not None else SerialTelemetrySession(history_limit=history_limit)
    messages = [Telemetry(number, number * 100, bool(number % 2)) for number in (10, 11, 12, 13, 12, 13, 14)]
    frames = [encode_telemetry(message) for message in messages]
    damaged = bytearray(frames[2])
    damaged[-1] ^= 1
    stream = b"diagnostic junk" + b"".join(frames[:2]) + bytes(damaged) + b"".join(frames[3:])
    for offset in range(0, len(stream), 7):
        session.feed_bytes(stream[offset:offset + 7])
    return session.snapshot()


def list_ports(*, port_lister: Callable[[], Any] | None = None) -> list[dict[str, Any]]:
    """List endpoints without opening them; ST-LINK descriptions are suggestions.

    Suggestions are based only on USB metadata and do not establish which board
    is attached, whether its firmware matches, or whether its UART is routed.
    ``port_lister`` makes discovery testable without an installed USB driver.
    """
    if port_lister is None:
        try:
            from serial.tools.list_ports import comports
        except ImportError as exc:
            raise SerialLinkError("pyserial is missing. Reinstall RF Link Lab's dependencies.") from exc
        port_lister = comports
    try:
        ports = []
        for port in port_lister():
            description = str(getattr(port, "description", "") or "")
            manufacturer = str(getattr(port, "manufacturer", "") or "")
            product = str(getattr(port, "product", "") or "")
            label = " ".join((description, manufacturer, product)).lower()
            ports.append({
                "device": str(port.device), "description": description,
                "hwid": str(getattr(port, "hwid", "") or ""),
                "suggested": any(marker in label for marker in ("stlink", "st-link", "stmicroelectronics")),
            })
        return sorted(ports, key=lambda item: (not item["suggested"], item["device"]))
    except (OSError, RuntimeError) as exc:
        raise SerialLinkError(f"Could not list serial ports: {exc}") from exc


class SerialTelemetrySession:
    """One thread-safe serial receiver and bounded, CRC-verified telemetry history.

    ``snapshot`` is safe to call on a GUI timer. Counts persist across explicit
    reconnects; parser fragments and the sequence baseline reset on every start
    and stop. A new connection therefore never joins fragments from two devices.
    ``reset_statistics`` clears totals and retained history only while stopped.

    Sequence gaps are cumulative observations of forward jumps, not a count of
    permanently lost packets: later arrivals do not undo an observed gap. The
    half-range uint32 rule handles wraparound. Duplicates are recognized within
    a bounded window; older repeats are classified as out of order.
    """

    def __init__(
        self, *, serial_factory: Callable[..., Any] | None = None,
        history_limit: int = 100, sequence_window: int = 1024,
        read_timeout_s: float = 0.1, read_size: int = 4096,
    ) -> None:
        for name, value, maximum in (
            ("history_limit", history_limit, 10_000),
            ("sequence_window", sequence_window, 100_000),
            ("read_size", read_size, 65_536),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer from 1 to {maximum}.")
        if isinstance(read_timeout_s, bool) or not isinstance(read_timeout_s, (int, float)) or not 0 < read_timeout_s <= 1:
            raise ValueError("read_timeout_s must be greater than zero and at most 1 second.")
        self._serial_factory = serial_factory
        self._read_timeout = read_timeout_s
        self._read_size = read_size
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._connection: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._parser = TelemetryParser()
        self._recent: deque[dict[str, Any]] = deque(maxlen=history_limit)
        self._seen_order: deque[int] = deque(maxlen=sequence_window)
        self._seen: set[int] = set()
        self._last_sequence: int | None = None
        self._connected = False
        self._status = "disconnected"
        self._port: str | None = None
        self._baudrate = 115200
        self._last_error: str | None = None
        self._input_source: str | None = None
        self._session_id = 0
        self._recorder: UARTRecorder | None = None
        self._recording_active = False
        self._recording_segment: tuple[str, int] | None = None
        self._packet_observer: Callable[[dict[str, Any]], None] | None = None
        self._clear_counts()

    def _clear_counts(self) -> None:
        self._counts = dict.fromkeys((
            "packets_ok", "crc_errors", "framing_errors", "discarded_bytes",
            "sequence_gaps", "duplicates", "out_of_order", "bytes_received",
        ), 0)

    def _reset_stream(self) -> None:
        self._parser = TelemetryParser()
        self._last_sequence = None
        self._seen_order.clear()
        self._seen.clear()

    def _record_event_locked(self, kind: str, *, boundary: bool = False, **details: Any) -> None:
        if boundary:
            self._recording_segment = None
        if self._recording_active and self._recorder is not None:
            self._recorder.event(kind, source=self._input_source, session_id=self._session_id,
                                 boundary=boundary, **details)

    def _activate_source_locked(self, source: str) -> None:
        if self._input_source != source:
            self._reset_stream()
            if source == "offline_feed":
                self._session_id += 1
            self._input_source = source
            self._recording_segment = None
        identity = (source, self._session_id)
        if self._recording_active and self._recorder is not None and self._recording_segment != identity:
            self._recorder.segment(source=source, session_id=self._session_id)
            self._recording_segment = identity

    def start_recording(
        self, output_dir: Path, *, max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> dict[str, Any]:
        """Start a fresh durable capture while disconnected; open no device.

        The output directory must not exist. Counts/history and parser fragments
        reset only after the writer is successfully created. Explicit reconnects
        keep this recording open, with separate parser and sequence boundaries.
        """
        with self._lifecycle_lock, self._lock:
            if self._status == "opening" or self._connected or (self._thread is not None and self._thread.is_alive()):
                raise SerialLinkError("Stop the serial connection before starting a recording.")
            if self._recording_active:
                raise SerialLinkError("Finalize the current UART recording before starting another.")
            try:
                recorder = UARTRecorder(
                    output_dir, parser_config={"read_size": self._read_size, "sequence_window": self._seen_order.maxlen},
                    max_raw_bytes=max_raw_bytes, max_events=max_events,
                )
            except OSError as exc:
                raise SerialLinkError(f"Could not start UART recording: {exc}") from exc
            self._clear_counts()
            self._recent.clear()
            self._reset_stream()
            self._session_id += 1
            self._input_source = None
            self._last_error = None
            self._status = "disconnected"
            self._recorder = recorder
            self._recording_active = True
            self._recording_segment = None
            self._record_event_locked("recording_start", boundary=True)
            return recorder.snapshot()

    def stop_recording(self) -> dict[str, Any] | None:
        """Finalize a capture explicitly without stopping or opening a port."""
        with self._lifecycle_lock, self._lock:
            if self._recorder is None or not self._recording_active:
                return None
            result = self._recorder.finish(self._counts, source=self._input_source, session_id=self._session_id)
            self._recording_active = False
            self._recording_segment = None
            return result

    def start(self, port: str, baudrate: int = 115200) -> dict[str, Any]:
        """Open one selected port and start a reader; replaces an existing session.

        Opening a UART may affect USB bridge modem-control signals. This method
        never sends telemetry or flashing commands, and never retries on its own.
        """
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Choose an explicit serial port.")
        if isinstance(baudrate, bool) or not isinstance(baudrate, int) or not 1200 <= baudrate <= 3_000_000:
            raise ValueError("baudrate must be an integer from 1200 to 3000000.")
        with self._lifecycle_lock:
            self.stop()
            with self._lock:
                self._reset_stream()
                self._session_id += 1
                self._input_source = None
                self._port = port.strip()
                self._baudrate = baudrate
                self._last_error = None
                self._status = "opening"
                self._record_event_locked("opening", boundary=True, port=self._port, baudrate=baudrate)
            try:
                factory = self._serial_factory
                if factory is None:
                    import serial
                    factory = serial.Serial
                connection = factory(
                    port=port.strip(), baudrate=baudrate,
                    timeout=self._read_timeout, write_timeout=1.0,
                )
            except Exception as exc:
                with self._lock:
                    self._status = "error"
                    self._last_error = f"Could not open {port.strip()}: {exc}"
                    self._record_event_locked("open_error", boundary=True, error=self._last_error)
                raise SerialLinkError(self._last_error) from exc
            with self._lock:
                event = threading.Event()
                self._connection = connection
                self._stop_event = event
                self._connected = True
                self._status = "connected"
                self._activate_source_locked("serial")
                self._thread = threading.Thread(
                    target=self._read_loop, args=(connection, event),
                    daemon=True, name="rf-link-serial-reader",
                )
                self._thread.start()
            return self.snapshot()

    def stop(self, *, timeout_s: float = 1.0) -> dict[str, Any]:
        """Request cancellation, close the endpoint, and wait a bounded interval.

        If a broken driver ignores both timeout and cancellation, ``stop`` raises
        and retains that worker reference. ``start`` will not open a second port
        while that reader remains alive. No late bytes from it are accepted.
        """
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 5:
            raise ValueError("timeout_s must be greater than zero and at most 5 seconds.")
        with self._lifecycle_lock:
            with self._lock:
                event, connection, thread = self._stop_event, self._connection, self._thread
                if event is not None:
                    event.set()
                self._connected = False
                self._connection = None
                self._reset_stream()
                self._record_event_locked("stop", boundary=True)
            close_error = None
            if connection is not None:
                cancel = getattr(connection, "cancel_read", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:
                        # Closing below is the fallback; some OS drivers do not
                        # implement cancellation even when pyserial exposes it.
                        pass
                try:
                    connection.close()
                except Exception as exc:
                    close_error = f"Could not close serial port: {exc}"
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=timeout_s)
            with self._lock:
                if thread is not None and thread.is_alive():
                    self._status = "error"
                    self._last_error = "Serial reader did not stop in time. Resolve the device or driver before reconnecting."
                    self._record_event_locked("stop_error", boundary=True, error=self._last_error)
                    raise SerialLinkError(self._last_error)
                self._thread = None
                self._stop_event = None
                self._status = "disconnected" if close_error is None else "error"
                if close_error is not None:
                    self._last_error = close_error
                    self._record_event_locked("stop_error", boundary=True, error=self._last_error)
                    raise SerialLinkError(close_error)
            return self.snapshot()

    def _read_loop(self, connection: Any, event: threading.Event) -> None:
        try:
            while not event.is_set():
                data = connection.read(self._read_size)
                with self._lock:
                    if event.is_set() or self._connection is not connection:
                        break
                    if data:
                        self._feed_locked(data)
        except Exception as exc:
            with self._lock:
                if not event.is_set() and self._connection is connection:
                    self._connected = False
                    self._status = "error"
                    self._last_error = f"Serial read failed on {self._port}: {exc}. Reconnect explicitly after checking the cable."
                    self._reset_stream()
                    self._record_event_locked("read_error", boundary=True, error=self._last_error)
        finally:
            try:
                connection.close()
            except Exception:
                pass
            with self._lock:
                if self._connection is connection:
                    self._connection = None
                    self._connected = False

    def feed_bytes(self, data: bytes) -> list[Telemetry]:
        """Feed an offline test source through the same parser and counters.

        This does not imply board evidence. Mixing offline bytes into a live
        serial connection is rejected to preserve the capture's provenance.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes, bytearray, or memoryview.")
        with self._lock:
            if self._status == "opening" or self._connected or (self._thread is not None and self._thread.is_alive()):
                raise SerialLinkError("Stop the serial connection before feeding offline demonstration bytes.")
            self._activate_source_locked("offline_feed")
            return self._feed_locked(data)

    def _feed_locked(self, data: bytes) -> list[Telemetry]:
        self._counts["bytes_received"] += len(data)
        observed = []
        # Bound the parser's temporary input buffer as well as retained history.
        for offset in range(0, len(data), self._read_size):
            chunk = bytes(data[offset:offset + self._read_size])
            if self._recording_active and self._recorder is not None:
                self._recorder.data(chunk, source=self._input_source, session_id=self._session_id)
            before_crc = self._parser.crc_errors
            before_framing = self._parser.framing_errors
            before_discarded = self._parser.discarded_bytes
            messages = self._parser.feed(chunk)
            self._counts["crc_errors"] += self._parser.crc_errors - before_crc
            self._counts["framing_errors"] += self._parser.framing_errors - before_framing
            self._counts["discarded_bytes"] += self._parser.discarded_bytes - before_discarded
            for message in messages:
                self._counts["packets_ok"] += 1
                row = {
                    "sequence": message.sequence, "uptime_ms": message.uptime_ms,
                    "button": message.button,
                    "sequence_status": self._classify_sequence(message.sequence),
                    "received_utc": datetime.now(timezone.utc).isoformat(),
                    "source": self._input_source,
                    "session_id": self._session_id,
                }
                self._recent.append(row)
                if self._recording_active and self._recorder is not None:
                    self._recorder.packet(row)
                if self._packet_observer is not None:
                    self._packet_observer(dict(row))
            observed.extend(messages)
        return observed

    def _classify_sequence(self, sequence: int) -> str:
        if sequence in self._seen:
            self._counts["duplicates"] += 1
            return "duplicate"
        if len(self._seen_order) == self._seen_order.maxlen:
            self._seen.discard(self._seen_order[0])
        self._seen_order.append(sequence)
        self._seen.add(sequence)
        if self._last_sequence is None:
            self._last_sequence = sequence
            return "first"
        forward = (sequence - self._last_sequence) & 0xFFFFFFFF
        if 0 < forward < 0x80000000:
            self._last_sequence = sequence
            if forward > 1:
                self._counts["sequence_gaps"] += forward - 1
                return "gap"
            return "in_order"
        self._counts["out_of_order"] += 1
        return "out_of_order"

    def snapshot(self) -> dict[str, Any]:
        """Return copied, JSON-serializable state; callers cannot mutate history."""
        with self._lock:
            recent = [dict(item) for item in self._recent]
            return {
                "connected": self._connected, "status": self._status,
                "port": self._port, "baudrate": self._baudrate,
                **self._counts,
                "latest": dict(recent[-1]) if recent else None,
                "recent": recent, "last_error": self._last_error,
                "input_source": self._input_source,
                "session_id": self._session_id,
                "history_limit": self._recent.maxlen,
                "sequence_window": self._seen_order.maxlen,
                "recording": self._recorder.snapshot() if self._recorder is not None else None,
            }

    def recent_frames(self, *, current_session_only: bool = False, source: str | None = None) -> list[bytes]:
        """Return CRC-verified frames, optionally restricted to this connection.

        For hardware evidence use ``current_session_only=True, source="serial"``
        so retained packets from demonstrations or earlier devices are excluded.
        """
        if source not in (None, "serial", "offline_feed"):
            raise ValueError("source must be serial, offline_feed, or None.")
        with self._lock:
            return [
                encode_telemetry(Telemetry(item["sequence"], item["uptime_ms"], item["button"]))
                for item in self._recent
                if (not current_session_only or item["session_id"] == self._session_id)
                and (source is None or item["source"] == source)
            ]

    def reset_statistics(self) -> dict[str, Any]:
        """Explicitly clear totals/history only while stopped; never reconnect."""
        with self._lock:
            if self._status == "opening" or self._connected or (self._thread is not None and self._thread.is_alive()):
                raise SerialLinkError("Stop the serial connection before clearing statistics.")
            if self._recording_active:
                raise SerialLinkError("Finalize the UART recording before clearing statistics.")
            self._clear_counts()
            self._recent.clear()
            self._reset_stream()
            return self.snapshot()

    def __enter__(self) -> "SerialTelemetrySession":
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            self.stop()
        finally:
            self.stop_recording()
