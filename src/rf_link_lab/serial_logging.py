"""Durable receive-only UART evidence and parser replay.

The log describes host receipt, not RF transmission or authenticated hardware.
Every recording is a fresh directory. SHA-256 detects file changes; it does not
authenticate the device or protect against replacement of the whole recording.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Iterator


SCHEMA = "rf-link-lab-uart-v1"
DEFAULT_MAX_RAW_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_EVENTS = 1_000_000
COUNTERS = (
    "bytes_received", "packets_ok", "crc_errors", "framing_errors",
    "discarded_bytes", "sequence_gaps", "duplicates", "out_of_order",
)
_FILES = {"raw": "raw.bin", "chunks": "chunks.jsonl", "packets": "packets.jsonl"}
_PACKET_FIELDS = ("sequence", "uptime_ms", "button", "sequence_status", "source", "session_id")
_STREAM_ERROR_FIELDS = ("kind", "error", "utc", "elapsed_s", "source", "session_id")
_CONNECTION_ATTEMPT_FIELDS = ("port", "baudrate", "utc", "elapsed_s", "session_id")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_line(item: dict[str, Any]) -> bytes:
    return (json.dumps(item, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _stream_error_summary(event: dict[str, Any]) -> dict[str, Any]:
    result = {key: event[key] for key in _STREAM_ERROR_FIELDS}
    result["error"] = result["error"][:2000]
    return result


def _connection_attempt_summary(event: dict[str, Any]) -> dict[str, Any]:
    result = {key: event[key] for key in _CONNECTION_ATTEMPT_FIELDS}
    result["port"] = result["port"][:1000]
    return result


def _positive_limit(name: str, value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}.")
    return value


class UARTRecorder:
    """Internal writer; the owning session holds its lock around every call."""

    def __init__(self, output_dir: Path, *, parser_config: dict[str, int],
                 max_raw_bytes: int = DEFAULT_MAX_RAW_BYTES,
                 max_events: int = DEFAULT_MAX_EVENTS) -> None:
        self.max_raw_bytes = _positive_limit("max_raw_bytes", max_raw_bytes, 1024 ** 3)
        self.max_events = _positive_limit("max_events", max_events, 1_000_000)
        self.output_dir = Path(output_dir).expanduser().absolute()
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.started_utc = _utc()
        self.started_monotonic = time.monotonic()
        self.parser_config = dict(parser_config)
        self.status = "active"
        self.last_error: str | None = None
        self.bytes_written = 0
        self.packets_written = 0
        self.events_written = 0
        self.segment_count = 0
        self.segments_by_source: dict[str, int] = {}
        self.sources: set[str] = set()
        self.stream_error_count = 0
        self.stream_errors: deque[dict[str, Any]] = deque(maxlen=100)
        self.connection_attempt_count = 0
        self.connection_attempts: deque[dict[str, Any]] = deque(maxlen=100)
        self._streams: dict[str, Any] = {}
        self._manifest: dict[str, Any] | None = None
        try:
            for key, name in _FILES.items():
                self._streams[key] = (self.output_dir / name).open("xb")
            self._write_manifest(self._make_manifest({}, ended_utc=None))
        except Exception as exc:
            self.fail(f"Could not create UART recording: {exc}")
            self._close()
            raise OSError(self.last_error) from exc

    def snapshot(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir), "status": self.status,
            "bytes_written": self.bytes_written, "packets_written": self.packets_written,
            "events_written": self.events_written, "last_error": self.last_error,
        }

    def fail(self, message: str) -> None:
        if self.last_error is None:
            self.last_error = str(message)
        self.status = "error"
        # Best effort: the UI still retains the error if the disk cannot accept
        # even a manifest. An old 'active' manifest never declares completion.
        try:
            self._write_manifest(self._make_manifest({}, ended_utc=None))
        except OSError:
            pass

    def _stamp(self, item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "utc": _utc(), "elapsed_s": time.monotonic() - self.started_monotonic}

    def _write_event(self, stream_name: str, item: dict[str, Any]) -> bool:
        if self.status != "active":
            return False
        if self.events_written >= self.max_events:
            self.fail(f"UART recording event limit reached ({self.max_events}); capture is incomplete.")
            return False
        try:
            stream = self._streams[stream_name]
            encoded = _json_line(item)
            if len(encoded) > 65_536:
                raise OSError("UART event exceeds the 65536-byte line limit")
            if stream.write(encoded) != len(encoded):
                raise OSError("short write while saving a UART event")
            stream.flush()
            self.events_written += 1
            return True
        except (OSError, ValueError) as exc:
            self.fail(f"UART recording write failed: {exc}")
            return False

    def event(self, kind: str, *, source: str | None, session_id: int,
              boundary: bool = False, **details: Any) -> None:
        event = self._stamp({
            "kind": kind, "source": source, "session_id": session_id,
            "boundary": boundary, **details,
        })
        if self._write_event("chunks", event):
            if kind in ("open_error", "read_error", "stop_error"):
                self.stream_error_count += 1
                self.stream_errors.append(_stream_error_summary(event))
            elif kind == "opening":
                self.connection_attempt_count += 1
                self.connection_attempts.append(_connection_attempt_summary(event))

    def segment(self, *, source: str, session_id: int) -> None:
        if self.status != "active":
            return
        self.segment_count += 1
        self.sources.add(source)
        self.segments_by_source[source] = self.segments_by_source.get(source, 0) + 1
        self.event("segment_start", source=source, session_id=session_id, boundary=True)

    def data(self, data: bytes, *, source: str, session_id: int) -> None:
        if self.status != "active" or not data:
            return
        if self.bytes_written + len(data) > self.max_raw_bytes:
            self.fail(f"UART raw byte limit reached ({self.max_raw_bytes}); capture is incomplete.")
            return
        if self.events_written >= self.max_events:
            self.fail(f"UART recording event limit reached ({self.max_events}); capture is incomplete.")
            return
        offset = self.bytes_written
        try:
            written = self._streams["raw"].write(data)
            self._streams["raw"].flush()
            self.bytes_written += written
            if written != len(data):
                raise OSError("short write while saving raw UART bytes")
        except (OSError, ValueError) as exc:
            self.fail(f"UART raw write failed: {exc}")
            return
        self._write_event("chunks", self._stamp({
            "kind": "data", "offset": offset, "length": len(data),
            "source": source, "session_id": session_id, "boundary": False,
        }))

    def packet(self, row: dict[str, Any]) -> None:
        if self._write_event("packets", dict(row)):
            self.packets_written += 1

    def _make_manifest(self, summary: dict[str, Any], *, ended_utc: str | None) -> dict[str, Any]:
        files = {}
        for key, name in _FILES.items():
            path = self.output_dir / name
            try:
                if path.is_file():
                    files[key] = {"path": name, "size_bytes": path.stat().st_size, "sha256": _hash(path)}
            except OSError as exc:
                self.last_error = self.last_error or f"Could not verify saved UART file {name}: {exc}"
                self.status = "incomplete" if ended_utc is not None else "error"
                files[key] = {"path": name, "error": str(exc)}
        return {
            "schema": SCHEMA, "status": self.status,
            "complete": self.status == "complete", "started_utc": self.started_utc,
            "ended_utc": ended_utc, "parser_config": self.parser_config,
            "limits": {"max_raw_bytes": self.max_raw_bytes, "max_events": self.max_events},
            "summary": {**{key: int(summary.get(key, 0)) for key in COUNTERS},
                        "sources": sorted(self.sources), "segment_count": self.segment_count,
                        "segments_by_source": dict(sorted(self.segments_by_source.items()))},
            "bytes_written": self.bytes_written, "packets_written": self.packets_written,
            "events_written": self.events_written, "last_error": self.last_error,
            "stream_error_count": self.stream_error_count,
            "stream_errors": list(self.stream_errors),
            "stream_errors_truncated": self.stream_error_count > len(self.stream_errors),
            "connection_attempt_count": self.connection_attempt_count,
            "connection_attempts": list(self.connection_attempts),
            "connection_attempts_truncated": self.connection_attempt_count > len(self.connection_attempts),
            "files": files,
            "evidence": "Host UART receipt; source labels are provenance, not device authentication or RF proof.",
        }

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        # The directory was created exclusively, so only this writer owns it.
        temporary = self.output_dir / "uart.json.tmp"
        with temporary.open("wb") as stream:
            encoded = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
            if stream.write(encoded) != len(encoded):
                raise OSError("short write while saving the UART manifest")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.output_dir / "uart.json")

    def _close(self) -> None:
        for stream in self._streams.values():
            try:
                flush = getattr(stream, "flush", None)
                if callable(flush):
                    flush()
                fileno = getattr(stream, "fileno", None)
                if callable(fileno):
                    os.fsync(fileno())
                stream.close()
            except OSError as exc:
                self.fail(f"UART recording close failed: {exc}")
                try:
                    stream.close()
                except OSError:
                    pass

    def finish(self, summary: dict[str, Any], *, source: str | None, session_id: int) -> dict[str, Any]:
        if self._manifest is not None:
            return {**self._manifest, "output_dir": str(self.output_dir)}
        self.event("recording_stop", source=source, session_id=session_id, boundary=False)
        self._close()
        self.status = "incomplete" if self.last_error else "complete"
        manifest = self._make_manifest(summary, ended_utc=_utc())
        try:
            self._write_manifest(manifest)
        except OSError as exc:
            self.status = "incomplete"
            self.last_error = f"UART manifest finalization failed: {exc}"
            manifest = self._make_manifest(summary, ended_utc=_utc())
            try:
                self._write_manifest(manifest)
            except OSError:
                pass
        self._manifest = manifest
        return {**manifest, "output_dir": str(self.output_dir)}


def _location(path: Path | str) -> tuple[Path, Path]:
    selected = Path(path).expanduser().absolute()
    manifest_path = selected / "uart.json" if selected.is_dir() else selected
    if manifest_path.name != "uart.json" or not manifest_path.is_file():
        raise ValueError("Choose a UART recording directory or its uart.json file.")
    if manifest_path.is_symlink():
        raise ValueError("UART manifest must not be a symbolic link.")
    return manifest_path.parent.resolve(), manifest_path


def _read_manifest(path: Path | str) -> tuple[Path, dict[str, Any]]:
    root, manifest_path = _location(path)
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("UART manifest exceeds the safety limit.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read UART manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("Unsupported UART recording schema.")
    return root, manifest


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as stream:
        while True:
            line = stream.readline(65_537)
            if not line:
                return
            if len(line) > 65_536 or not line.endswith(b"\n"):
                raise ValueError(f"UART event line is oversized or truncated: {path.name}")
            try:
                value = json.loads(line)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid UART JSON line: {path.name}") from exc
            if not isinstance(value, dict):
                raise ValueError("UART event must be a JSON object.")
            yield value


def _nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def verify_uart_recording(path: Path | str) -> dict[str, Any]:
    """Verify hashes, relative paths and bounded event structure; open no device.

    Incomplete captures may still be inspectable. Callers must check ``complete``
    before treating a capture as a fully finalized experiment.
    """
    root, manifest = _read_manifest(path)
    status = manifest.get("status")
    if status not in ("active", "error", "complete", "incomplete"):
        raise ValueError("Invalid UART recording status.")
    if manifest.get("complete") is not (status == "complete"):
        raise ValueError("UART completion flag disagrees with recording status.")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(_FILES):
        raise ValueError("UART recording must declare raw, chunks and packets files.")
    for key, expected in _FILES.items():
        item = files[key]
        if not isinstance(item, dict) or item.get("path") != expected:
            raise ValueError("Unsafe or unsupported UART file path.")
        asset = root / expected
        if asset.is_symlink() or not asset.is_file() or asset.resolve().parent != root:
            raise ValueError("UART file must be an ordinary file inside its recording directory.")
        limit = 1024 ** 3 if key == "raw" else 512 * 1024 * 1024
        size = asset.stat().st_size
        if size > limit or item.get("size_bytes") != size:
            raise ValueError(f"UART {expected} size mismatch or safety limit exceeded.")
        if not isinstance(item.get("sha256"), str) or _hash(asset) != item["sha256"]:
            raise ValueError(f"UART {expected} SHA-256 mismatch.")
    config = manifest.get("parser_config", {})
    if not isinstance(config, dict):
        raise ValueError("Invalid UART parser configuration.")
    _positive_limit("read_size", config.get("read_size"), 65_536)
    _positive_limit("sequence_window", config.get("sequence_window"), 100_000)
    summary = manifest.get("summary", {})
    if not isinstance(summary, dict) or any(not _nonnegative_integer(summary.get(key)) for key in COUNTERS):
        raise ValueError("Invalid UART summary counters.")
    offset = event_count = segments = 0
    sources: set[str] = set()
    by_source: dict[str, int] = {}
    previous_elapsed = -1.0
    source = None
    session_id = None
    stream_error_count = 0
    stream_errors: deque[dict[str, Any]] = deque(maxlen=100)
    connection_attempt_count = 0
    connection_attempts: deque[dict[str, Any]] = deque(maxlen=100)
    for event in _lines(root / "chunks.jsonl"):
        event_count += 1
        if event_count > 1_000_000:
            raise ValueError("UART event count exceeds the safety limit.")
        elapsed = event.get("elapsed_s")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < previous_elapsed:
            raise ValueError("UART chunk monotonic timestamps are invalid.")
        previous_elapsed = elapsed
        try:
            stamp = datetime.fromisoformat(event["utc"])
            if stamp.utcoffset() is None:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("UART UTC timestamp is invalid.") from exc
        if event.get("source") not in (None, "serial", "offline_feed") or not _nonnegative_integer(event.get("session_id")):
            raise ValueError("Invalid UART event provenance.")
        if not isinstance(event.get("boundary"), bool):
            raise ValueError("UART boundary marker is missing.")
        if event.get("kind") in ("open_error", "read_error", "stop_error"):
            if not isinstance(event.get("error"), str):
                raise ValueError("UART stream failure lacks an error description.")
            stream_error_count += 1
            stream_errors.append(_stream_error_summary(event))
        elif event.get("kind") == "opening":
            if (not isinstance(event.get("port"), str) or not event["port"]
                    or not _nonnegative_integer(event.get("baudrate"))
                    or not 1200 <= event["baudrate"] <= 3_000_000):
                raise ValueError("UART connection attempt lacks valid endpoint settings.")
            connection_attempt_count += 1
            connection_attempts.append(_connection_attempt_summary(event))
        if event["boundary"]:
            source, session_id = None, None
        if event.get("kind") == "segment_start":
            if event["source"] is None or not event["boundary"]:
                raise ValueError("Invalid UART source segment.")
            source, session_id = event["source"], event["session_id"]
            sources.add(source)
            segments += 1
            by_source[source] = by_source.get(source, 0) + 1
        elif event.get("kind") == "data":
            length = event.get("length")
            if event.get("offset") != offset or not _nonnegative_integer(length) or not 1 <= length <= config["read_size"]:
                raise ValueError("UART chunk ranges are invalid or noncontiguous.")
            if event["boundary"] or (event["source"], event["session_id"]) != (source, session_id) or source is None:
                raise ValueError("UART bytes do not belong to an active source segment.")
            offset += length
        elif event.get("kind") not in ("recording_start", "recording_stop", "opening", "open_error", "stop", "read_error", "stop_error"):
            raise ValueError("Unknown UART lifecycle event.")
    packet_count = 0
    for row in _lines(root / "packets.jsonl"):
        packet_count += 1
        if packet_count + event_count > 1_000_000:
            raise ValueError("UART event count exceeds the safety limit.")
        if not all(key in row for key in _PACKET_FIELDS):
            raise ValueError("UART packet record lacks provenance or telemetry fields.")
    if manifest["complete"]:
        if offset != files["raw"]["size_bytes"] or summary["bytes_received"] != offset:
            raise ValueError("Complete UART recording has unaccounted raw bytes.")
        if packet_count != summary["packets_ok"] or packet_count != manifest.get("packets_written"):
            raise ValueError("Complete UART recording has inconsistent packet counts.")
        if summary.get("sources") != sorted(sources) or summary.get("segment_count") != segments or summary.get("segments_by_source") != by_source:
            raise ValueError("Complete UART recording source summary disagrees with event log.")
        if event_count + packet_count != manifest.get("events_written"):
            raise ValueError("Complete UART recording event count mismatch.")
        if (manifest.get("stream_error_count") != stream_error_count
                or manifest.get("stream_errors") != list(stream_errors)
                or manifest.get("stream_errors_truncated") is not (stream_error_count > len(stream_errors))):
            raise ValueError("Complete UART recording stream errors disagree with event log.")
        if (manifest.get("connection_attempt_count") != connection_attempt_count
                or manifest.get("connection_attempts") != list(connection_attempts)
                or manifest.get("connection_attempts_truncated") is not (connection_attempt_count > len(connection_attempts))):
            raise ValueError("Complete UART recording connection attempts disagree with event log.")
    elif offset > files["raw"]["size_bytes"]:
        raise ValueError("UART event references missing raw bytes.")
    return {
        "schema": SCHEMA, "path": str(root / "uart.json"), "status": status,
        "complete": manifest["complete"], "files_verified": True,
        "summary": summary, "chunk_bytes": offset, "packet_records": packet_count,
        "stream_error_count": stream_error_count, "stream_errors": list(stream_errors),
        "connection_attempt_count": connection_attempt_count,
        "connection_attempts": list(connection_attempts),
    }


def replay_uart_recording(path: Path | str) -> dict[str, Any]:
    """Feed saved chunks and exact lifecycle boundaries through the live parser.

    No port is discovered or opened. A serial-labelled segment remains historical
    receipt evidence; replay is explicitly an offline action.
    """
    verification = verify_uart_recording(path)
    root, manifest = _read_manifest(path)
    from .serial_link import SerialTelemetrySession

    session = SerialTelemetrySession(history_limit=1, **manifest["parser_config"])
    recorded_packets = iter(_lines(root / "packets.jsonl"))
    packet_matches = True
    segment_count = 0
    sources: set[str] = set()
    by_source: dict[str, int] = {}
    # A callback checks every packet, independently of the bounded UI history.
    def observe(row: dict[str, Any]) -> None:
        nonlocal packet_matches
        expected = next(recorded_packets, None)
        if expected is None or any(row.get(key) != expected.get(key) for key in _PACKET_FIELDS):
            packet_matches = False

    session._packet_observer = observe
    with (root / "raw.bin").open("rb") as raw:
        for event in _lines(root / "chunks.jsonl"):
            with session._lock:
                if event["boundary"]:
                    session._reset_stream()
                session._session_id = event["session_id"]
                session._input_source = event["source"]
                if event["kind"] == "segment_start":
                    segment_count += 1
                    source = event["source"]
                    sources.add(source)
                    by_source[source] = by_source.get(source, 0) + 1
                elif event["kind"] == "data":
                    raw.seek(event["offset"])
                    session._feed_locked(raw.read(event["length"]))
    if next(recorded_packets, None) is not None:
        packet_matches = False
    snapshot = session.snapshot()
    summary = {**{key: snapshot[key] for key in COUNTERS}, "sources": sorted(sources),
               "segment_count": segment_count, "segments_by_source": by_source}
    return {
        "manifest": manifest, "recorded_summary": manifest["summary"],
        "replayed_summary": summary,
        "matches": bool(manifest["complete"] and summary == manifest["summary"] and packet_matches),
        "packets_match": packet_matches, "verification": verification,
        "replay_source": "offline_replay",
    }
