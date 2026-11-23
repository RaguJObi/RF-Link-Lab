"""Explicit, finite conducted TX1/RX1 through one selected bladeRF CLI process.

No process, device query, or RF transmission occurs on import. The streaming
boundary is mock-tested; physical timing and RF operation need bench evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from .hardware import HardwareError, _integer


_COMMAND_TIMEOUT_S = 4.0
_READY_TIMEOUT_S = 3.0
_CLEANUP_TIMEOUT_S = 4.0
_OUTER_BASE_TIMEOUT_S = 20.0
_POLL_S = 0.02
_RX_STREAM_BUFFER_SAMPLES = 8192
# The matched 2025.10 stack captured only the first part of a short TX burst
# with 8192-sample TX buffers. The CLI flushes a fixed count of zero buffers
# before disabling TX; use its default 32768-sample TX buffer to increase that
# drain while keeping the waveform and RX readiness unchanged. Physical tail
# recovery must still be verified from the captured samples and decoded frames.
_TX_STREAM_BUFFER_SAMPLES = 32768
_READY_BYTES = _RX_STREAM_BUFFER_SAMPLES * 4 * 2
_CAPTURE_MARGIN_S = 0.35
# Allow tiny synthesizer quantization in frequency readback only. This is a
# project working restriction, not a vendor accuracy or tuning specification.
_FREQUENCY_TOLERANCE_HZ = 10
_ERROR = re.compile(
    r"\b(?:error|failed|failure|invalid|unsupported|unrecognized|unknown command|not supported|"
    r"no device|no bladerf|not found|not open|not configured)\b|\busage\s*:", re.I,
)


@dataclass(frozen=True)
class DuplexSettings:
    device_serial: str = ""
    frequency_hz: int = 915_000_000
    sample_rate: int = 1_000_000
    rx_samples: int = 3_000_000
    rx_gain_db: int = 0
    tx_gain_db: int = -20
    bandwidth_hz: int = 200_000


class _Cancelled(Exception):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate(settings: DuplexSettings, tx_path: Path) -> int:
    if not isinstance(settings, DuplexSettings):
        raise ValueError("settings must be DuplexSettings.")
    if not isinstance(settings.device_serial, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", settings.device_serial):
        raise ValueError("Select the exact 32-hex-digit bladeRF serial; automatic selection is disabled.")
    selected_serial = os.environ.get("RF_LINK_LAB_SELECTED_SERIAL")
    if selected_serial is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{32}", selected_serial):
            raise ValueError("The hardware launcher's selected bladeRF serial is invalid; start a new hardware session.")
        if selected_serial.lower() != settings.device_serial.lower():
            raise ValueError("The requested bladeRF serial differs from the radio selected by the hardware launcher.")
    _integer("frequency_hz", settings.frequency_hz, 70_000_000, 6_000_000_000)
    _integer("sample_rate", settings.sample_rate, 1_000_000, 5_000_000)
    _integer("rx_samples", settings.rx_samples, 1_000_000, 5_000_000)
    _integer("rx_gain_db", settings.rx_gain_db, -5, 50)
    # `set gain tx1` selects overall gain, not the separate DSA stage. Nuand's
    # overall TX range is [-23.75, 66]; integer requests use [-23, 66]. The
    # separate connection-plan power bound must cover the selected setting.
    _integer("tx_gain_db", settings.tx_gain_db, -23, 66)
    _integer("bandwidth_hz", settings.bandwidth_hz, 200_000, settings.sample_rate)
    if not 1 <= settings.rx_samples / settings.sample_rate <= 5:
        raise ValueError("The project RX capture duration must be 1 through 5 seconds.")
    if not tx_path.is_file() or tx_path.is_symlink():
        raise ValueError("TX input must be an existing regular, nonsymlink SC16Q11 file.")
    size = tx_path.stat().st_size
    if size < 4 or size % 4 or size / 4 > settings.sample_rate * 0.5:
        raise ValueError("TX must contain whole SC16Q11 samples and last at most 0.5 seconds including guards.")
    # Stream the validation so a malformed waveform never reaches the CLI DAC.
    import numpy as np
    with tx_path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            values = np.frombuffer(block, dtype="<i2")
            if np.any(values < -2048) or np.any(values > 2047):
                raise ValueError("TX components must be in the SC16Q11 range [-2048, 2047].")
    return size // 4


def _clean(line: str) -> str:
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
    # fgets prompts need not have their own newline under a pipe. Only an exact
    # echo RESULT is accepted, never an echoed `echo MARKER` input command.
    return re.sub(r"^(?:bladeRF>\s*)+", "", line, flags=re.I).strip()


def _check_errors(output: str) -> None:
    for line in output.splitlines():
        clean = _clean(line)
        if re.search(r"Last error:\s*None\s*$", clean, re.I):
            continue
        if _ERROR.search(clean):
            raise HardwareError("device_error", "bladeRF CLI reported: " + clean)


class _Cli:
    def __init__(self, process, log, cancel_event: threading.Event, deadline: float):
        self.process, self.log = process, log
        self.cancel_event, self.deadline = cancel_event, deadline
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.commands: list[str] = []
        self._log_lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True, name="bladerf-cli-output")
        self.reader.start()

    def record(self, kind: str, line: str) -> None:
        with self._log_lock:
            self.log.write(f"{_utc()} {kind} {line.rstrip()}\n")
            self.log.flush()

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                self.record("CLI", line)
                self.lines.put(line)
        except (OSError, ValueError) as exc:
            self.lines.put(f"output reader failed: {exc}\n")
        finally:
            self.lines.put(None)

    def guard(self) -> None:
        if self.cancel_event.is_set():
            raise _Cancelled("Operator cancelled the finite run.")
        if time.monotonic() >= self.deadline:
            raise HardwareError("timeout", "The finite run exceeded its outer process deadline.")

    def command(self, command: str, *, timeout: float | None = None,
                cleanup: bool = False, allow_errors: bool = False) -> str:
        timeout = _COMMAND_TIMEOUT_S if timeout is None else timeout
        if not cleanup:
            self.guard()
        marker = "RFLL_ACK_" + uuid.uuid4().hex
        self.commands.append(command)
        self.record("CMD", command)
        try:
            self.process.stdin.write(command + "\necho " + marker + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise HardwareError("process_error", f"Could not send CLI command: {exc}") from exc
        deadline = time.monotonic() + timeout
        if not cleanup:
            deadline = min(deadline, self.deadline)
        output = []
        while time.monotonic() < deadline:
            if not cleanup:
                self.guard()
            try:
                line = self.lines.get(timeout=min(_POLL_S, max(0.001, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if line is None:
                raise HardwareError("process_error", "bladeRF CLI exited before acknowledging the command.")
            if _clean(line) == marker:
                result = "".join(output)
                if not allow_errors:
                    _check_errors(result)
                return result
            output.append(line)
        raise HardwareError("timeout", "No bladeRF CLI acknowledgement for: " + command)

    def status(self, direction: str, *, cleanup: bool = False, timeout: float | None = None) -> tuple[str, str]:
        output = self.command(direction, cleanup=cleanup, timeout=timeout, allow_errors=cleanup)
        states = re.findall(r"State:\s*([^\r\n]+)", output, re.I)
        errors = re.findall(r"Last error:\s*([^\r\n]+)", output, re.I)
        if len(states) != 1 or len(errors) != 1:
            raise HardwareError("status_unavailable", "Cannot verify " + direction.upper() + " task state and error.")
        return states[0].strip().lower(), errors[0].strip().lower()


def _gain_readback(cli: _Cli, channel: str, expected: int) -> int:
    output = cli.command(f"print gain {channel}")
    matches = re.findall(rf"Gain\s+{channel}\s+overall:\s*(-?\d+)\s+dB", output, re.I)
    if len(matches) != 1:
        raise HardwareError("readback_unavailable", f"Cannot parse actual {channel} gain.")
    actual = int(matches[0])
    if actual != expected:
        raise HardwareError("readback_mismatch", f"Actual {channel} gain is {actual}; requested {expected}.")
    return actual


def _version_readback(output: str) -> dict[str, str]:
    versions = {}
    for label, name in (("bladeRF-cli", "cli"), ("libbladeRF", "libbladerf"),
                        ("Firmware", "firmware"), ("FPGA", "fpga")):
        matches = re.findall(rf"(?mi)^\s*{re.escape(label)}\s+version:\s*([^\r\n]+)", output)
        if len(matches) != 1 or not re.match(r"^v?\d+\.\d+\.\d+(?:$|[-+\s(])", matches[0].strip()):
            raise HardwareError("readback_unavailable", f"Cannot verify an unambiguous {label} version.")
        versions[name] = matches[0].strip()
    return versions


def _check_version_compatibility(versions: dict[str, str]) -> None:
    numeric = {name: tuple(map(int, re.match(r"^v?(\d+)\.(\d+)\.(\d+)", value).groups()))
               for name, value in versions.items()}
    # Nuand 2025.10 compatibility.c and backend/usb/usb.h define the larger
    # GPIF buffers: firmware >=2.5 requires FPGA >=0.16, and conversely.
    if numeric["firmware"] >= (2, 5, 0) and numeric["fpga"] < (0, 16, 0):
        raise HardwareError("version_incompatible", "Firmware >=2.5.0 requires FPGA >=0.16.0 for compatible GPIF buffers.")
    if numeric["fpga"] >= (0, 16, 0) and numeric["firmware"] < (2, 5, 0):
        raise HardwareError("version_incompatible", "FPGA >=0.16.0 requires firmware >=2.5.0 for compatible GPIF buffers.")
    # Project minimum for the installed 2.6 firmware generation, based on
    # Nuand's matched 2025.10 release. This is not a general vendor guarantee.
    if numeric["firmware"] >= (2, 6, 0) and numeric["libbladerf"] < (2, 6, 0):
        raise HardwareError("version_incompatible", "Project policy requires libbladeRF >=2.6.0 with firmware >=2.6.0.")


def _readback(cli: _Cli, settings: DuplexSettings, result: dict) -> dict:
    identity = cli.command("info")
    serial = re.search(r"Serial\s*#?\s*:\s*([0-9a-fA-F]{32})\b", identity, re.I)
    if not serial or serial.group(1).lower() != settings.device_serial.lower():
        raise HardwareError("identity_mismatch", "Selected device serial could not be verified in CLI readback.")
    result.update(device_serial=serial.group(1).lower(), identity=identity)
    result["version_output"] = cli.command("version")
    result["versions"] = _version_readback(result["version_output"])
    _check_version_compatibility(result["versions"])
    result["version_compatibility"] = "passed_project_known_buffer_version_checks"
    cli.command("set loopback none")
    for channel in ("rx1", "tx1"):
        cli.command(f"set biastee {channel} off")
        cli.command(f"set frequency {channel} {settings.frequency_hz}")
        cli.command(f"set samplerate {channel} {settings.sample_rate}")
        cli.command(f"set bandwidth {channel} {settings.bandwidth_hz}")
    cli.command("set agc rx1 off")
    cli.command(f"set gain rx1 {settings.rx_gain_db}")
    cli.command(f"set gain tx1 {settings.tx_gain_db}")
    for channel in ("rx1", "tx1"):
        values: dict[str, Any] = {}
        for field, name, expected in (
            ("frequency", "frequency_hz", settings.frequency_hz),
            ("samplerate", "sample_rate", settings.sample_rate),
            ("bandwidth", "bandwidth_hz", settings.bandwidth_hz),
        ):
            output = cli.command(f"print {field} {channel}")
            if field == "samplerate":
                matches = re.findall(rf"{channel}\s+sample\s+rate:\s*(\d+)\s+(\d+)/(\d+)", output, re.I)
                if len(matches) != 1 or int(matches[0][2]) == 0:
                    raise HardwareError("readback_unavailable", f"Cannot parse actual {channel} sample rate.")
                integer, numerator, denominator = map(int, matches[0])
                if integer * denominator + numerator != expected * denominator:
                    raise HardwareError("readback_mismatch", f"Actual {channel} sample rate differs from the request.")
                actual = integer
            else:
                pattern = rf"{channel}\s+{field}:\s*(\d+)\s+Hz"
                matches = re.findall(pattern, output, re.I)
                if len(matches) != 1:
                    raise HardwareError("readback_unavailable", f"Cannot parse actual {channel} {field}.")
                actual = int(matches[0])
                if field == "frequency":
                    delta = actual - expected
                    values["requested_frequency_hz"] = expected
                    values["frequency_delta_hz"] = delta
                    values["frequency_tolerance_hz"] = _FREQUENCY_TOLERANCE_HZ
                    if abs(delta) > _FREQUENCY_TOLERANCE_HZ:
                        raise HardwareError(
                            "readback_mismatch",
                            f"Actual {channel} frequency is {actual}; requested {expected}; "
                            f"difference {delta} Hz exceeds project tolerance "
                            f"{_FREQUENCY_TOLERANCE_HZ} Hz.",
                        )
                elif actual != expected:
                    raise HardwareError("readback_mismatch", f"Actual {channel} {field} is {actual}; requested {expected}.")
            values[name] = actual
        if channel == "tx1":
            values["gain_db"] = _gain_readback(cli, channel, settings.tx_gain_db)
        else:
            # bladeRF 2.0 micro can defer the requested RX gain until channel
            # enable. An idle readback can still show the previous gain. Do not
            # record that stale value as the actual gain or start TX before the
            # enabled channel has been checked below.
            values["gain_verification"] = "pending_channel_enable"
        result[channel] = values
    return result


def _format_readback(cli: _Cli, direction: str) -> dict[str, str]:
    # Nuand's 2021.10 CLI maps format=bin to SC16 Q11 directly and has no
    # bitmode command. Require the configured task's format explicitly, so
    # newer CLIs with other bit widths cannot silently reinterpret our files.
    output = cli.command(direction + " config")
    matches = re.findall(r"File\s+format:\s*([^\r\n]+)", output, re.I)
    if len(matches) != 1:
        raise HardwareError("readback_unavailable", f"Cannot parse actual {direction} sample file format.")
    actual = matches[0].strip()
    if not re.fullmatch(r"SC16\s+Q11,\s*Binary", actual, re.I):
        raise HardwareError("readback_mismatch", f"Actual {direction} file format is {actual}; required SC16 Q11, Binary.")
    return {"format": "SC16Q11_LE", "cli_file_format": actual,
            "format_verification": "configured_task_readback"}


def _cleanup(cli: _Cli) -> tuple[bool, dict]:
    """Best-effort bounded graceful stop; never equate kill with RF disable."""
    deadline = time.monotonic() + _CLEANUP_TIMEOUT_S
    states = {}
    for direction in ("tx", "rx"):
        try:
            # Stop on Idle is an expected vendor state error. We still query the
            # subsequent state, rather than trusting the stop command itself.
            cli.command(direction + " stop", cleanup=True, allow_errors=True,
                        timeout=max(0.01, deadline-time.monotonic()))
        except HardwareError:
            pass
    while time.monotonic() < deadline:
        for direction in ("tx", "rx"):
            try:
                state, error = cli.status(direction, cleanup=True,
                                          timeout=max(0.01, deadline-time.monotonic()))
                states[direction] = {"state": state, "last_error": error}
            except HardwareError:
                states[direction] = {"state": "unconfirmed", "last_error": "unconfirmed"}
        # An error may concern the disable operation itself. Require both Idle
        # and no reported error before claiming graceful cleanup was confirmed.
        if all(states.get(d) == {"state": "idle", "last_error": "none"} for d in ("tx", "rx")):
            return True, states
        time.sleep(_POLL_S)
    return False, states


def run_finite_duplex(
    tx_path: Path, output_dir: Path, settings: DuplexSettings, *, connection_plan: dict,
    cancel_event: threading.Event | None = None, process_factory=None,
) -> dict[str, Any]:
    """Run once, preserving raw/log evidence; no automatic retry or RF verdict.

    Preflight errors raise HardwareError without starting a process. Once a
    process starts, failures and cancellations return a durable JSON-safe result.
    ``output_dir`` must not exist. Real streaming is currently Linux-only;
    ``process_factory`` injects a Popen-compatible fake for device-free tests.
    """
    from .conducted_plan import validate_connection_plan

    try:
        tx_path = Path(tx_path).expanduser().absolute()
        tx_samples = _validate(settings, tx_path)
        validation = validate_connection_plan(connection_plan, frequency_hz=settings.frequency_hz,
                                              tx_gain_db=settings.tx_gain_db, device_serial=settings.device_serial)
    except (ValueError, TypeError, OSError) as exc:
        raise HardwareError("preflight", str(exc)) from exc
    output_dir = Path(output_dir).expanduser().absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise HardwareError("output_exists", "Use a new exclusive output directory; existing evidence is preserved.")
    cancel = cancel_event if cancel_event is not None else threading.Event()
    if cancel.is_set():
        raise HardwareError("cancelled", "The run was cancelled before opening a device.")
    if process_factory is None:
        if not sys.platform.startswith("linux"):
            raise HardwareError("unsupported_host", "Live finite duplex currently runs on the Ubuntu host; use offline mode here.")
        cli_path, stdbuf = shutil.which("bladeRF-cli"), shutil.which("stdbuf")
        if not cli_path or not stdbuf:
            raise HardwareError("missing_tool", "Live duplex needs bladeRF-cli and coreutils stdbuf on Ubuntu.")
        factory = subprocess.Popen
    else:
        cli_path, stdbuf, factory = "bladeRF-cli", "stdbuf", process_factory
    argv = [stdbuf, "-oL", "-eL", cli_path, "-d", "*:serial=" + settings.device_serial.lower(), "-i"]
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = {"tx": output_dir / "tx.sc16q11", "rx": output_dir / "rx.sc16q11",
             "log": output_dir / "session.log", "manifest": output_dir / "duplex.json",
             "rx_sidecar": output_dir / "rx.sc16q11.json"}
    # Fixed names are the only paths passed into the CLI parser. Directory names
    # and all user descriptions remain OS arguments or JSON, never CLI commands.
    with tx_path.open("rb") as source, paths["tx"].open("xb") as destination:
        shutil.copyfileobj(source, destination)
    try:
        tx_samples = _validate(settings, paths["tx"])
    except (ValueError, OSError) as exc:
        raise HardwareError("preflight", "Copied TX snapshot failed validation: " + str(exc)) from exc
    result: dict[str, Any] = {
        "schema": "rf-link-lab.bladerf-duplex.v1", "status": "failed", "capture_completed": False,
        "rf_success": "unassessed", "tx_start_requested": False, "cleanup_confirmed": False,
        "error": None, "error_kind": None, "started_utc": _utc(), "requested": asdict(settings),
        "plan_validation": validation, "readback": {}, "argv": argv, "commands": [],
        "paths": {name: str(path) for name, path in paths.items()}, "tx_samples": tx_samples,
        "tx_sha256": _hash(paths["tx"]), "adapter_validation": "mock-tested; physical bench validation pending",
        "requested_stream_geometry": {
            "rx": {"samples_per_buffer": _RX_STREAM_BUFFER_SAMPLES, "buffers": 16,
                   "transfers": 8, "timeout_ms": 1000},
            "tx": {"samples_per_buffer": _TX_STREAM_BUFFER_SAMPLES, "buffers": 16,
                   "transfers": 8, "timeout_ms": 1000},
        },
    }
    process, cli = None, None
    with paths["log"].open("x", encoding="utf-8", buffering=1) as log:
        try:
            # Repeat policy validation at the private, immediate launch boundary;
            # root callers cannot bypass it through another workflow or UI path.
            result["plan_validation"] = validate_connection_plan(
                connection_plan, frequency_hz=settings.frequency_hz, tx_gain_db=settings.tx_gain_db,
                device_serial=settings.device_serial)
            if cancel.is_set():
                raise _Cancelled("Cancelled before device opening.")
            process = factory(argv, cwd=output_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                              bufsize=1, shell=False)
            cli = _Cli(process, log, cancel, time.monotonic() + _OUTER_BASE_TIMEOUT_S + settings.rx_samples/settings.sample_rate)
            _readback(cli, settings, result["readback"])
            cli.command(f"rx config file=rx.sc16q11 format=bin channel=1 n={settings.rx_samples} "
                        f"samples={_RX_STREAM_BUFFER_SAMPLES} buffers=16 xfers=8 timeout=1000")
            cli.command(f"tx config file=tx.sc16q11 format=bin channel=1 repeat=1 delay=0 "
                        f"samples={_TX_STREAM_BUFFER_SAMPLES} buffers=16 xfers=8 timeout=1000")
            for direction in ("rx", "tx"):
                result["readback"][direction + "1"].update(_format_readback(cli, direction))
            rx_started = time.monotonic()
            cli.command("rx start")
            ready_deadline = min(cli.deadline, time.monotonic() + _READY_TIMEOUT_S)
            while True:
                cli.guard()
                observed = paths["rx"].stat().st_size if paths["rx"].is_file() else 0
                if observed >= _READY_BYTES:
                    break
                if time.monotonic() >= ready_deadline:
                    raise HardwareError("rx_not_ready", "No initial RX bytes arrived; TX was not requested.")
                if process.poll() is not None:
                    raise HardwareError("process_error", "CLI exited while waiting for initial RX bytes.")
                time.sleep(_POLL_S)
            state, error = cli.status("rx")
            if state != "running" or error != "none":
                raise HardwareError("rx_not_ready", "RX is not running without error; TX was not requested.")
            result["readback"]["rx1"]["gain_db"] = _gain_readback(cli, "rx1", settings.rx_gain_db)
            result["readback"]["rx1"]["gain_verification"] = "after_running_rx_and_initial_bytes"
            # Use both elapsed time (stream may outrun disk flushes) and observed
            # bytes. Neither check establishes timing synchronization on hardware.
            remaining_wall = settings.rx_samples/settings.sample_rate - (time.monotonic()-rx_started)
            remaining_file = (settings.rx_samples-observed/4)/settings.sample_rate
            needed = tx_samples/settings.sample_rate + _CAPTURE_MARGIN_S
            result["rx_readiness"] = {"observed_bytes": observed, "remaining_wall_budget_s": remaining_wall,
                                      "remaining_file_budget_s": remaining_file, "required_budget_s": needed}
            if min(remaining_wall, remaining_file) < needed:
                raise HardwareError("rx_window_exhausted", "Finite RX window has insufficient TX budget; TX was not requested.")
            # Again check the operator plan immediately before the actual TX
            # request, after settings have passed readback checks (frequency
            # alone permits the explicit tiny project quantization tolerance).
            result["plan_validation"] = validate_connection_plan(
                connection_plan, frequency_hz=settings.frequency_hz, tx_gain_db=settings.tx_gain_db,
                device_serial=settings.device_serial)
            cli.guard()
            result["tx_start_requested"] = True
            cli.command("tx start")
            while True:
                cli.command("tx wait 100ms")
                cli.command("rx wait 100ms")
                tx_state, tx_error = cli.status("tx")
                rx_state, rx_error = cli.status("rx")
                if tx_error != "none" or rx_error != "none":
                    raise HardwareError("stream_error", f"TX error={tx_error}; RX error={rx_error}.")
                if tx_state == rx_state == "idle":
                    break
                if tx_state not in ("running", "idle", "stopping") or rx_state not in ("running", "idle", "stopping"):
                    raise HardwareError("stream_error", "Unexpected CLI streaming state.")
            observed = paths["rx"].stat().st_size if paths["rx"].is_file() else 0
            if observed != settings.rx_samples * 4:
                raise HardwareError("incomplete_capture", f"Expected {settings.rx_samples*4} RX bytes; found {observed}.")
            result.update(status="complete", capture_completed=True)
        except _Cancelled as exc:
            result.update(status="cancelled", error_kind="cancelled", error=str(exc))
        except KeyboardInterrupt:
            cancel.set()
            result.update(status="cancelled", error_kind="cancelled", error="Operator interrupted the finite run.")
        except (HardwareError, OSError, ValueError) as exc:
            result.update(status="failed", error_kind=getattr(exc, "kind", "process_error"), error=str(exc))
        except Exception as exc:
            result.update(status="failed", error_kind="unexpected_error", error=f"{type(exc).__name__}: {exc}")
        finally:
            if cli is not None:
                confirmed, states = _cleanup(cli)
                result["cleanup_confirmed"], result["cleanup_status"] = confirmed, states
                try:
                    cli.record("CMD", "quit")
                    cli.commands.append("quit")
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    process.wait(timeout=1.0)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    result["cleanup_confirmed"] = False
                    # SIGTERM is intercepted by some CLI builds and can merely
                    # release a wait. Use a bounded final hard-kill fallback and
                    # explicitly retain unconfirmed hardware cleanup status.
                    try:
                        process.kill()
                        process.wait(timeout=1.0)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                result["commands"] = cli.commands
                result["process_returncode"] = process.poll()
                if (process.poll() not in (None, 0) and result["status"] == "complete"
                        and result["cleanup_confirmed"]):
                    result.update(status="failed", capture_completed=False, error_kind="process_error",
                                  error="CLI exited with a nonzero status after capture.")
                cli.reader.join(timeout=0.25)
                streams = (process.stdin, process.stdout) if not cli.reader.is_alive() else (process.stdin,)
                for stream in streams:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
            if result["status"] == "complete" and not result["cleanup_confirmed"]:
                result.update(status="failed", capture_completed=False, error_kind="cleanup_unconfirmed",
                              error="Captured bytes exist, but shutdown could not be confirmed. Check board state before another run.")
            result["finished_utc"] = _utc()
            result["rx_bytes"] = paths["rx"].stat().st_size if paths["rx"].is_file() else 0
            result["rx_sha256"] = _hash(paths["rx"])
            sidecar = {
                "schema": "rf-link-lab.bladerf-rx.v1", "source": "hardware_rx",
                "file": "rx.sc16q11", "format": "SC16Q11_LE", "channel": "RX1",
                "sample_rate": result["readback"].get("rx1", {}).get("sample_rate"),
                "frequency_hz": result["readback"].get("rx1", {}).get("frequency_hz"),
                "requested": {"sample_rate": settings.sample_rate, "frequency_hz": settings.frequency_hz,
                              "gain_db": settings.rx_gain_db},
                "bytes": result["rx_bytes"], "samples": result["rx_bytes"]//4,
                "sha256": result["rx_sha256"], "capture_status": result["status"],
                "duplex_manifest": "duplex.json", "tx_start_requested": result["tx_start_requested"],
                "cleanup_confirmed": result["cleanup_confirmed"],
                "adapter_validation": result["adapter_validation"],
            }
            _json(paths["rx_sidecar"], sidecar)
            _json(paths["manifest"], result)
    return result
