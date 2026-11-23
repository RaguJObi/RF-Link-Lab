"""Explicit, receive-only hardware boundaries; importing this module opens nothing.

The adapter has been tested using mocked processes, not a connected bladeRF.
Nuand CLI reference: https://github.com/Nuand/bladeRF/wiki/bladeRF-CLI-Tips-and-Tricks
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

import numpy as np


class HardwareError(RuntimeError):
    """An adapter failure with a stable category for a UI or command line."""

    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


def _run(args: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            check=False, shell=False, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise HardwareError("missing_tool", "bladeRF-cli was not found. Install Nuand's host tools on the hardware computer.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HardwareError("timeout", f"bladeRF-cli did not finish within {timeout:g} seconds; no successful capture is recorded.") from exc
    except OSError as exc:
        raise HardwareError("process_error", f"Could not start bladeRF-cli: {exc}") from exc


def _output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())


def _failure(result: subprocess.CompletedProcess) -> HardwareError:
    message = _output(result) or f"bladeRF-cli exited with status {result.returncode}."
    lowered = message.lower()
    no_device = any(text in lowered for text in ("no bladerf", "no devices", "no device(s)", "no device available"))
    return HardwareError("no_device" if no_device else "device_error", message)


def probe_bladerf() -> dict[str, Any]:
    """Read device identity/version on explicit request; return actionable errors.

    ``available`` means the CLI enumerated and opened a device. It does not prove
    streaming, RF reception, calibration, or that the attached board is an xA9.
    """
    status: dict[str, Any] = {"available": False, "devices": "", "info": "", "error": None, "error_kind": None}
    try:
        devices = _run(["bladeRF-cli", "-p"], timeout=10)
        status["devices"] = _output(devices)
        if devices.returncode:
            raise _failure(devices)
        info = _run(["bladeRF-cli", "-e", "info", "-e", "version"], timeout=15)
        status["info"] = _output(info)
        if info.returncode:
            raise _failure(info)
        status["available"] = True
    except HardwareError as exc:
        status["error"] = str(exc)
        status["error_kind"] = exc.kind
    return status


def _integer(name: str, value: int, minimum: int, maximum: int) -> int:
    # bool is an int subclass but is never a valid physical setting here.
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def capture_rx(
    output_path: Path, frequency_hz: int, sample_rate: int, samples: int, gain_db: int,
) -> dict[str, Any]:
    """Capture one finite RX1 SC16Q11 file and provenance sidecar, never transmit.

    These are project limits for short initial recordings, not the complete
    device specification. Actual supported ranges/settings come from the CLI.
    Existing recordings and metadata are never overwritten. No hardware probe
    or capture happens until this function is called explicitly.
    """
    frequency_hz = _integer("frequency_hz", frequency_hz, 47_000_000, 6_000_000_000)
    sample_rate = _integer("sample_rate", sample_rate, 1_000_000, 10_000_000)
    samples = _integer("samples", samples, 1, 20_000_000)
    gain_db = _integer("gain_db", gain_db, -5, 50)
    output_path = Path(output_path).expanduser().resolve()
    metadata_path = output_path.with_suffix(output_path.suffix + ".json")
    if output_path.exists() or metadata_path.exists():
        raise FileExistsError("Choose a new recording name; existing captures are preserved.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # The user-controlled output path is deliberately not passed through the
    # CLI's own command parser. A fixed local filename also handles spaces and
    # punctuation in directory names without any shell/CLI quoting tricks.
    commands = [
        "info", "version", "set loopback none",
        f"set frequency rx1 {frequency_hz}",
        f"set samplerate rx1 {sample_rate}",
        "set agc rx1 off", f"set gain rx1 {gain_db}",
        "print frequency rx1", "print samplerate rx1", "print bandwidth rx1",
        "print gain rx1", "print agc rx1", "print loopback",
        f"rx config file=capture.sc16q11 format=bin channel=1 n={samples}",
        "rx start", "rx wait", "rx",
    ]
    args = ["bladeRF-cli"]
    for command in commands:
        args.extend(["-e", command])
    # CLI wait completes a finite n capture; the process watchdog also bounds
    # disconnected/stalled transfers. No unbounded recording is accepted.
    timeout_s = 20.0 + samples / sample_rate * 2.0
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="rf_link_rx_", dir=output_path.parent) as directory:
        temporary = Path(directory)
        result = _run(args, timeout=timeout_s, cwd=temporary)
        if result.returncode:
            raise _failure(result)
        last_errors = re.findall(r"Last error:\s*([^\r\n]+)", _output(result), flags=re.IGNORECASE)
        if any(error.strip().lower() != "none" for error in last_errors):
            raise HardwareError("device_error", "RX status reported an error; recording was not accepted.\n" + _output(result))
        capture = temporary / "capture.sc16q11"
        expected_bytes = samples * 4  # one int16 I and one int16 Q per sample
        if not capture.is_file() or capture.stat().st_size != expected_bytes:
            observed = capture.stat().st_size if capture.is_file() else 0
            raise HardwareError("incomplete_capture", f"Expected {expected_bytes} bytes but received {observed}; recording was not accepted.")
        metadata: dict[str, Any] = {
            "schema": "rf-link-lab.bladerf-rx.v1", "source": "hardware_rx",
            "adapter_validation": "mock-tested; live hardware validation pending",
            "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
            "file": output_path.name, "format": "SC16Q11_LE", "channel": "RX1",
            "samples": samples, "bytes": expected_bytes,
            "normalization_divisor": 2048.0,
            "requested": {"frequency_hz": frequency_hz, "sample_rate": sample_rate, "gain_db": gain_db},
            "nominal_duration_s": samples / sample_rate,
            "settings_readback_and_log": _output(result),
            "settings_note": "Requested values are separate from actual CLI readback; analog bandwidth is logged, not selected by this adapter.",
            "commands": commands, "argv": args, "process_timeout_s": timeout_s,
            "sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        }
        with capture.open("rb") as source, output_path.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        with metadata_path.open("x", encoding="utf-8") as destination:
            json.dump(metadata, destination, indent=2)
            destination.write("\n")
    return metadata


def load_sc16q11(path: Path, *, maximum_samples: int = 20_000_000) -> np.ndarray:
    """Replay a raw, single-channel bladeRF recording as normalized complex64.

    The file contains little-endian int16 I,Q pairs with 11 fractional bits.
    This loader does not infer frequency, sample rate, provenance, modulation,
    or hardware health from the raw bytes. Preserve and read the sidecar.
    """
    _integer("maximum_samples", maximum_samples, 1, 200_000_000)
    path = Path(path)
    byte_count = path.stat().st_size
    if not byte_count or byte_count % 4:
        raise ValueError("SC16Q11 must contain a nonempty whole number of 4-byte I/Q samples.")
    if byte_count // 4 > maximum_samples:
        raise ValueError(f"Recording exceeds the selected {maximum_samples} sample replay limit.")
    components = np.fromfile(path, dtype="<i2")
    if components.size != byte_count // 2:
        raise ValueError("Recording changed or was truncated while being read.")
    if np.any(components < -2048) or np.any(components > 2047):
        raise ValueError("Components are outside SC16Q11 range; check the format and channel layout.")
    pairs = components.reshape(-1, 2).astype(np.float32) / 2048.0
    return (pairs[:, 0] + 1j * pairs[:, 1]).astype(np.complex64)


def read_serial_bytes(port: str, *, baudrate: int = 115200, max_bytes: int = 4096, timeout_s: float = 1.0) -> bytes:
    """Explicit optional UART read boundary; no port scan, firmware flash or writes.

    Opening a serial port can affect its modem-control lines. Verify the board's
    virtual COM configuration before attaching. Bytes still need packet/CRC
    validation by the telemetry parser.
    """
    if not isinstance(port, str) or not port.strip():
        raise ValueError("port must name an explicit serial device.")
    _integer("baudrate", baudrate, 1200, 3_000_000)
    _integer("max_bytes", max_bytes, 1, 65_536)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 10:
        raise ValueError("timeout_s must be greater than zero and at most 10 seconds.")
    try:
        import serial
    except ImportError as exc:
        raise HardwareError("missing_serial_dependency", "Install the optional pyserial dependency to read STM32 telemetry.") from exc
    try:
        with serial.Serial(port=port, baudrate=baudrate, timeout=timeout_s) as connection:
            return connection.read(max_bytes)
    except (serial.SerialException, OSError) as exc:
        raise HardwareError("serial_error", f"Could not read {port}: {exc}") from exc
