"""Small, documented telemetry wire protocol shared by simulation and firmware.

All integers are little endian. A frame is magic(2), version(1), length(1),
sequence(u32), uptime_ms(u32), button(u8), CRC32(4). CRC covers the header
and payload. This is corruption detection, not authentication.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib


MAGIC = b"\xb5\x62"
VERSION = 1
PAYLOAD_LENGTH = 9
FRAME_SIZE = 17
_PAYLOAD = struct.Struct("<IIB")


@dataclass(frozen=True)
class Telemetry:
    sequence: int
    uptime_ms: int
    button: bool


def encode_telemetry(message: Telemetry) -> bytes:
    """Encode one telemetry message; reject values the MCU cannot represent."""
    if not 0 <= message.sequence <= 0xFFFFFFFF:
        raise ValueError("sequence must fit an unsigned 32-bit integer")
    if not 0 <= message.uptime_ms <= 0xFFFFFFFF:
        raise ValueError("uptime_ms must fit an unsigned 32-bit integer")
    if message.button not in (False, True, 0, 1):
        raise ValueError("button must be a boolean")
    body = MAGIC + bytes((VERSION, PAYLOAD_LENGTH)) + _PAYLOAD.pack(
        message.sequence, message.uptime_ms, int(message.button)
    )
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_telemetry(frame: bytes) -> Telemetry:
    """Decode a complete frame only when framing, CRC and values are valid."""
    if len(frame) != FRAME_SIZE:
        raise ValueError(f"expected {FRAME_SIZE} bytes")
    if frame[:4] != MAGIC + bytes((VERSION, PAYLOAD_LENGTH)):
        raise ValueError("invalid telemetry header")
    expected_crc = struct.unpack("<I", frame[-4:])[0]
    if zlib.crc32(frame[:-4]) & 0xFFFFFFFF != expected_crc:
        raise ValueError("telemetry CRC mismatch")
    sequence, uptime_ms, button = _PAYLOAD.unpack(frame[4:-4])
    if button not in (0, 1):
        raise ValueError("invalid button value")
    return Telemetry(sequence, uptime_ms, bool(button))


class TelemetryParser:
    """Incremental parser that recovers after junk, truncation and bad CRCs.

    A malformed candidate discards one byte, so a valid magic sequence inside
    a damaged candidate is not skipped. At most one partial frame is retained.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.rejected_frames = 0
        self.crc_errors = 0
        self.framing_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Telemetry]:
        self._buffer.extend(data)
        messages: list[Telemetry] = []
        while self._buffer:
            start = self._buffer.find(MAGIC)
            if start < 0:
                retained = self._buffer[-1:] if self._buffer[-1:] == MAGIC[:1] else b""
                self.discarded_bytes += len(self._buffer) - len(retained)
                self._buffer[:] = retained
                break
            self.discarded_bytes += start
            del self._buffer[:start]
            if len(self._buffer) < 4:
                break
            if self._buffer[2:4] != bytes((VERSION, PAYLOAD_LENGTH)):
                self.rejected_frames += 1
                self.framing_errors += 1
                self.discarded_bytes += 1
                del self._buffer[0]
                continue
            if len(self._buffer) < FRAME_SIZE:
                break
            try:
                message = decode_telemetry(bytes(self._buffer[:FRAME_SIZE]))
            except ValueError as exc:
                self.rejected_frames += 1
                if "CRC mismatch" in str(exc):
                    self.crc_errors += 1
                else:
                    self.framing_errors += 1
                self.discarded_bytes += 1
                del self._buffer[0]
                continue
            messages.append(message)
            del self._buffer[:FRAME_SIZE]
        return messages

