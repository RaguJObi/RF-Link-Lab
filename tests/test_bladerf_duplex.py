"""The fake CLI below never imports or opens a physical device."""

from dataclasses import replace
import json
from pathlib import Path
import queue
import subprocess
import threading

import numpy as np
import pytest

from rf_link_lab import bladerf_duplex as duplex
from rf_link_lab.hardware import HardwareError


SERIAL = "0123456789abcdef0123456789abcdef"


def valid_plan():
    return {
        "schema": "rf-link-lab.conducted-plan.v1", "device_serial": SERIAL,
        "connection": "TX1_ATTENUATORS_RX1", "operator_checked_connections": True,
        "unused_ports_correctly_loaded": True, "active_antenna_connected": False,
        "cable_loss_db": 0.0,
        "attenuators": [{"label": "Mock-only 40 dB", "attenuation_db": 40.0,
                          "frequency_min_hz": 1_000_000, "frequency_max_hz": 6_000_000_000,
                          "power_rating_w": 1.0}],
        "tx_power_bound": {"upper_dbm": -10.0, "frequency_hz": 915_000_000,
                           "tx_gain_db": -20, "source_note": "Test fixture bound, never a real hardware authorization"},
        "rx_working_ceiling_dbm": -20.0,
    }


class FakeOutput:
    def __init__(self):
        self.queue = queue.Queue()

    def put(self, line):
        self.queue.put(line + "\n" if line is not None else None)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.queue.get()
        if line is None:
            raise StopIteration
        return line

    def close(self):
        pass


class FakeInput:
    def __init__(self, process):
        self.process = process

    def write(self, value):
        for line in value.splitlines():
            self.process.command(line)
        return len(value)

    def flush(self):
        pass

    def close(self):
        pass


class FakeProcess:
    def __init__(self, argv, *, mode="normal", cancel_event=None, **kwargs):
        self.argv, self.kwargs, self.mode = argv, kwargs, mode
        self.cwd = Path(kwargs["cwd"])
        self.stdout, self.stdin = FakeOutput(), FakeInput(self)
        self.returncode = None
        self.commands = []
        self.settings = {"frequency": {}, "samplerate": {}, "bandwidth": {}, "gain": {}}
        self.rx_n, self.rx_state, self.tx_state = 0, "Idle", "Idle"
        self.cancel_event, self.killed = cancel_event, False
        self.rx_status_count = 0
        self.rx_running_observed = False

    def emit(self, line):
        self.stdout.put(line)

    def command(self, line):
        self.commands.append(line)
        parts = line.split()
        if not parts:
            return
        if parts[0] == "echo":
            # Some stdin frontends echo INPUT text. That is not acknowledgement.
            self.emit("bladeRF> " + line)
            if self.mode != "input_echo_only":
                self.emit("bladeRF> " + parts[1])
        elif line == "info":
            self.emit("  Board: bladeRF 2.0")
            self.emit("  Serial #: " + ("f" * 32 if self.mode == "wrong_serial" else SERIAL))
        elif line == "version":
            versions = {"bladeRF-cli": "1.8.0-0.2021.10-2", "libbladeRF": "2.4.1-0.2021.10-2",
                        "Firmware": "2.4.0-git-a3d5c55f", "FPGA": "0.14.0 (configured from SPI flash)"}
            if self.mode in ("modern_versions", "mixed_firmware_fpga", "old_host_modern_pair", "mixed_fpga_firmware"):
                versions.update({"bladeRF-cli": "1.10.0", "libbladeRF": "2.6.0",
                                 "Firmware": "2.6.0-git-09c82087", "FPGA": "0.16.0 (configured from USB host)"})
            if self.mode == "mixed_firmware_fpga":
                versions["FPGA"] = "0.14.0 (configured from SPI flash)"
            if self.mode == "old_host_modern_pair":
                versions["bladeRF-cli"], versions["libbladeRF"] = "1.8.0", "2.4.1"
            if self.mode == "mixed_fpga_firmware":
                versions["Firmware"] = "2.4.0"
            if self.mode == "malformed_version":
                versions["libbladeRF"] = "unidentified"
            for label, value in versions.items():
                if not (self.mode == "missing_version" and label == "libbladeRF"):
                    self.emit(f"  {label} version: {value}")
            if self.mode == "ambiguous_version":
                self.emit("  libbladeRF version: 2.6.0")
        elif line == "set bitmode 16":
            # Ubuntu 22.04's CLI 1.8.0 has SC16 Q11 binary support but no
            # bitmode parameter. Do not let the fake hide this incompatibility.
            self.emit("set: Invalid parameter (bitmode)")
        elif parts[0] == "set" and parts[1] in self.settings:
            self.settings[parts[1]][parts[2]] = int(parts[3])
            if self.mode == "error_exit_zero" and parts[1] == "gain" and parts[2] == "tx1":
                self.emit("Error: Invalid gain setting for TX1")
            elif parts[1] == "gain" and parts[2] == "rx1" and self.mode == "delayed_rx_gain":
                self.emit("Note: This change will not be visible until the channel is enabled.")
        elif parts[0] == "print":
            kind, channel = parts[1:3]
            number = self.settings[kind][channel]
            if kind == "frequency" and self.mode.startswith("frequency_offset_"):
                number += int(self.mode.removeprefix("frequency_offset_"))
            if self.mode == "wrong_tx_frequency" and kind == "frequency" and channel == "tx1":
                number += 11
            if self.mode == "wrong_rate" and kind == "samplerate" and channel == "tx1":
                number += 1
            if self.mode == "wrong_bandwidth" and kind == "bandwidth" and channel == "tx1":
                number += 1
            if self.mode == "wrong_tx_gain" and kind == "gain" and channel == "tx1":
                number += 1
            if kind == "gain":
                if channel == "rx1" and self.mode in ("delayed_rx_gain", "wrong_enabled_rx_gain"):
                    # A realistic disabled-channel readback would still be 60,
                    # regardless of the queued request. Enabled reads follow
                    # initial data receipt and an explicit Running observation.
                    if self.rx_state != "Running":
                        number = 60
                    else:
                        assert self.rx_running_observed
                        assert (self.cwd / "rx.sc16q11").stat().st_size >= duplex._READY_BYTES
                        if self.mode == "wrong_enabled_rx_gain":
                            number += 1
                limits = "[-23.75, 66]" if channel == "tx1" else "[-15, 60]"
                self.emit(f"  Gain {channel.upper():<4}overall: {number} dB (Range: {limits})")
            elif kind == "samplerate":
                if self.mode == "fractional_rate":
                    self.emit(f"  {channel.upper()} sample rate: {number} 1/2 (Range: [521000, 61440000])")
                elif self.mode == "missing_readback":
                    self.emit("No parseable sample rate text")
                else:
                    self.emit(f"  {channel.upper()} sample rate: {number} 0/1 (Range: [521000, 61440000])")
            else:
                self.emit(f"  {channel.upper()} {kind.title()}: {number} Hz (Range: [1, 6000000000])")
        elif line in ("rx config", "tx config"):
            direction = parts[0]
            if self.mode == "missing_format" and direction == "rx":
                self.emit("No sample format readback")
            else:
                actual = "SC16 Q11, Binary"
                if self.mode == "wrong_" + direction + "_format":
                    actual = "SC8 Q7, Binary"
                if self.mode == "csv_format" and direction == "tx":
                    actual = "SC16 Q11, CSV"
                self.emit("  File format: " + actual)
                if self.mode == "ambiguous_format" and direction == "rx":
                    self.emit("  File format: SC16 Q11, Binary")
        elif line.startswith("rx config "):
            self.rx_n = int(next(x[2:] for x in parts if x.startswith("n=")))
        elif line == "rx start":
            self.rx_state = "Running"
            if self.mode != "no_rx_bytes":
                samples = self.rx_n if self.mode == "window_exhausted" else duplex._READY_BYTES // 4
                (self.cwd / "rx.sc16q11").write_bytes(b"\x00" * (samples * 4))
            if self.mode == "cancel_ready":
                self.cancel_event.set()
        elif line == "tx start":
            assert (self.cwd / "rx.sc16q11").stat().st_size >= duplex._READY_BYTES
            self.tx_state = "Running"
            if self.mode == "cancel_transmit":
                self.cancel_event.set()
            elif self.mode == "keyboard_interrupt":
                raise KeyboardInterrupt
            elif self.mode == "unexpected_after_tx":
                raise RuntimeError("unexpected driver-wrapper failure")
        elif line == "rx wait 100ms":
            # Timed wait may return SUCCESS while streams remain Running. Test
            # the controller actually polls state and keeps waiting in that case.
            self.rx_status_count += 1
            if self.mode not in ("never_complete", "cancel_transmit") and self.rx_status_count >= 2:
                self.tx_state, self.rx_state = "Idle", "Idle"
                samples = self.rx_n - 1 if self.mode == "short_capture" else self.rx_n
                (self.cwd / "rx.sc16q11").write_bytes(b"\x00" * (samples * 4))
        elif line == "rx stop":
            if self.mode != "cleanup_stuck":
                self.rx_state = "Idle"
        elif line == "tx stop":
            if self.mode != "cleanup_stuck":
                self.tx_state = "Idle"
        elif line in ("tx", "rx"):
            state = self.tx_state if line == "tx" else self.rx_state
            if line == "rx" and state == "Running":
                self.rx_running_observed = True
            if self.mode == "cleanup_stuck" and "tx stop" in self.commands:
                state = "Running"
            self.emit("  State: " + state)
            error = "I/O failure" if self.mode == "stream_error" and line == "tx" and "tx start" in self.commands else "None"
            self.emit("  Last error: " + error)
        elif line == "quit":
            if self.mode == "cleanup_stuck":
                return
            self.returncode = 0
            self.stdout.put(None)

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-bladerf", timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9
        self.stdout.put(None)


@pytest.fixture
def tx_file(tmp_path):
    path = tmp_path / "input ; weird waveform.sc16q11"
    values = np.zeros(20_000, dtype="<i2")
    values[::2], values[1::2] = 100, -100
    path.write_bytes(values.tobytes())
    return path


@pytest.fixture(autouse=True)
def short_watchdogs(monkeypatch):
    monkeypatch.delenv("RF_LINK_LAB_SELECTED_SERIAL", raising=False)
    monkeypatch.setattr(duplex, "_COMMAND_TIMEOUT_S", 0.15)
    monkeypatch.setattr(duplex, "_READY_TIMEOUT_S", 0.06)
    monkeypatch.setattr(duplex, "_CLEANUP_TIMEOUT_S", 0.08)
    monkeypatch.setattr(duplex, "_POLL_S", 0.002)


def run(tx_file, tmp_path, mode="normal", *, settings=None, cancel=None):
    processes = []
    def factory(argv, **kwargs):
        process = FakeProcess(argv, mode=mode, cancel_event=cancel, **kwargs)
        processes.append(process)
        return process
    settings = settings or duplex.DuplexSettings(SERIAL)
    plan = valid_plan()
    plan["tx_power_bound"]["tx_gain_db"] = settings.tx_gain_db
    result = duplex.run_finite_duplex(
        tx_file, tmp_path / "capture ; quoted dir", settings,
        connection_plan=plan, process_factory=factory, cancel_event=cancel,
    )
    return result, processes[0]


def test_finite_selected_device_and_verified_capture(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path)
    assert result["status"] == "complete"
    assert result["capture_completed"] and result["cleanup_confirmed"]
    assert result["rf_success"] == "unassessed"
    assert result["tx_start_requested"]
    assert process.kwargs["shell"] is False
    assert process.argv[-3:] == ["-d", "*:serial=" + SERIAL, "-i"]
    commands = result["commands"]
    assert commands.index("rx start") < commands.index("tx start")
    assert commands.index("print gain tx1") < commands.index("tx start")
    assert commands.index("rx start") < commands.index("print gain rx1") < commands.index("tx start")
    assert sum(command.startswith("tx config file=") for command in commands) == 1
    assert "repeat=1" in next(c for c in commands if c.startswith("tx config"))
    assert not any("repeat=0" in c or "n=0" in c for c in commands)
    assert commands.count("rx wait 100ms") == 2
    assert result["rx_bytes"] == 12_000_000
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text())
    assert manifest == result
    sidecar = json.loads(Path(result["paths"]["rx_sidecar"]).read_text())
    assert sidecar["source"] == "hardware_rx" and sidecar["sha256"] == result["rx_sha256"]
    assert sidecar["sample_rate"] == 1_000_000
    assert "input ; weird" not in "\n".join(commands)
    assert "quoted dir" not in "\n".join(commands)
    assert "tx.sc16q11" in next(c for c in commands if c.startswith("tx config"))


def test_legacy_cli_binary_format_is_verified_without_bitmode_command(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path)
    assert result["status"] == "complete"
    assert not any("bitmode" in command for command in process.commands)
    assert result["readback"]["versions"]["libbladerf"] == "2.4.1-0.2021.10-2"
    for direction in ("rx", "tx"):
        readback = result["readback"][direction + "1"]
        assert readback["format"] == "SC16Q11_LE"
        assert readback["cli_file_format"] == "SC16 Q11, Binary"
        assert readback["format_verification"] == "configured_task_readback"
        assert process.commands.index(direction + " config") < process.commands.index("rx start")


def test_larger_tx_drain_buffer_preserves_rx_readiness_and_waveform(tx_file, tmp_path):
    # This checks the isolated bench correction, not physical RF completion:
    # increase TX drain chunks without changing RX timing or waveform bytes.
    original = tx_file.read_bytes()
    result, process = run(tx_file, tmp_path, "modern_versions")
    assert result["status"] == "complete"
    assert next(c for c in process.commands if c.startswith("rx config file=")) == (
        "rx config file=rx.sc16q11 format=bin channel=1 n=3000000 "
        "samples=8192 buffers=16 xfers=8 timeout=1000"
    )
    assert next(c for c in process.commands if c.startswith("tx config file=")) == (
        "tx config file=tx.sc16q11 format=bin channel=1 repeat=1 delay=0 "
        "samples=32768 buffers=16 xfers=8 timeout=1000"
    )
    assert duplex._READY_BYTES == 65_536
    assert result["rx_readiness"]["observed_bytes"] == 65_536
    assert Path(result["paths"]["tx"]).read_bytes() == original
    assert tx_file.read_bytes() == original
    assert result["tx_samples"] == len(original) // 4
    assert result["requested"]["tx_gain_db"] == -20
    assert result["requested"]["rx_gain_db"] == 0
    geometry = {
        "rx": {"samples_per_buffer": 8192, "buffers": 16, "transfers": 8, "timeout_ms": 1000},
        "tx": {"samples_per_buffer": 32768, "buffers": 16, "transfers": 8, "timeout_ms": 1000},
    }
    assert result["requested_stream_geometry"] == geometry
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text())
    assert manifest["requested_stream_geometry"] == geometry


def test_matched_modern_versions_are_recorded_before_streaming(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path, "modern_versions")
    assert result["status"] == "complete" and result["tx_start_requested"]
    assert result["readback"]["versions"] == {
        "cli": "1.10.0", "libbladerf": "2.6.0", "firmware": "2.6.0-git-09c82087",
        "fpga": "0.16.0 (configured from USB host)",
    }
    assert result["readback"]["version_compatibility"] == "passed_project_known_buffer_version_checks"
    assert process.commands.index("version") < process.commands.index("rx config")


@pytest.mark.parametrize("mode,detail", [
    ("mixed_firmware_fpga", "Firmware >=2.5.0 requires FPGA >=0.16.0"),
    ("old_host_modern_pair", "requires libbladeRF >=2.6.0"),
    ("mixed_fpga_firmware", "FPGA >=0.16.0 requires firmware >=2.5.0"),
])
def test_known_incompatible_versions_block_all_stream_setup(tx_file, tmp_path, mode, detail):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == "version_incompatible"
    assert detail in result["error"]
    assert not result["tx_start_requested"]
    assert not any(command.startswith(("rx config", "tx config", "set ")) for command in process.commands)
    assert "rx start" not in process.commands and "tx start" not in process.commands
    assert result["cleanup_confirmed"]
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text())
    assert manifest["readback"]["versions"] == result["readback"]["versions"]
    assert "version_compatibility" not in result["readback"]


@pytest.mark.parametrize("mode", ["missing_version", "malformed_version", "ambiguous_version"])
def test_unknown_or_ambiguous_versions_cannot_authorize_streaming(tx_file, tmp_path, mode):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == "readback_unavailable"
    assert not result["tx_start_requested"]
    assert "version_output" in result["readback"]
    assert "rx start" not in process.commands and "tx start" not in process.commands
    assert not any(command.startswith(("rx config", "tx config")) for command in process.commands)


@pytest.mark.parametrize("mode,error", [
    ("wrong_rx_format", "readback_mismatch"), ("wrong_tx_format", "readback_mismatch"),
    ("csv_format", "readback_mismatch"), ("missing_format", "readback_unavailable"),
    ("ambiguous_format", "readback_unavailable"),
])
def test_wrong_or_unavailable_sample_format_refuses_streaming(tx_file, tmp_path, mode, error):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == error
    assert not result["tx_start_requested"]
    assert "rx start" not in process.commands and "tx start" not in process.commands
    assert result["cleanup_confirmed"]
    assert Path(result["paths"]["manifest"]).is_file()


def test_rx_gain_deferred_until_enabled_channel_is_receiving(tx_file, tmp_path):
    settings = replace(duplex.DuplexSettings(SERIAL), rx_gain_db=40, tx_gain_db=50)
    result, process = run(tx_file, tmp_path, "delayed_rx_gain", settings=settings)
    assert result["status"] == "complete"
    assert result["readback"]["rx1"]["gain_db"] == 40
    assert result["readback"]["rx1"]["gain_verification"] == "after_running_rx_and_initial_bytes"
    assert result["readback"]["tx1"]["gain_db"] == 50
    commands = result["commands"]
    assert commands.index("rx start") < commands.index("rx") < commands.index("print gain rx1")
    assert commands.index("print gain rx1") < commands.index("tx start")
    assert process.rx_running_observed


@pytest.mark.parametrize("offset", [-10, -2, 10])
def test_tiny_frequency_quantization_keeps_actual_readback(tx_file, tmp_path, offset):
    result, _ = run(tx_file, tmp_path, f"frequency_offset_{offset}")
    assert result["status"] == "complete" and result["tx_start_requested"]
    for channel in ("rx1", "tx1"):
        readback = result["readback"][channel]
        assert readback["frequency_hz"] == 915_000_000 + offset
        assert readback["requested_frequency_hz"] == 915_000_000
        assert readback["frequency_delta_hz"] == offset
        assert readback["frequency_tolerance_hz"] == 10
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text())
    assert manifest["readback"] == result["readback"]
    sidecar = json.loads(Path(result["paths"]["rx_sidecar"]).read_text())
    assert sidecar["frequency_hz"] == 915_000_000 + offset
    assert sidecar["requested"]["frequency_hz"] == 915_000_000


@pytest.mark.parametrize("mode", ["frequency_offset_-11", "frequency_offset_11", "wrong_tx_frequency"])
def test_frequency_outside_project_tolerance_refuses_tx(tx_file, tmp_path, mode):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == "readback_mismatch"
    assert "exceeds project tolerance 10 Hz" in result["error"]
    assert not result["tx_start_requested"] and "tx start" not in process.commands
    assert result["cleanup_confirmed"]
    assert Path(result["paths"]["manifest"]).is_file()


def test_wrong_enabled_rx_gain_stops_before_transmitting(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path, "wrong_enabled_rx_gain",
                          settings=replace(duplex.DuplexSettings(SERIAL), rx_gain_db=40))
    assert result["status"] == "failed" and result["error_kind"] == "readback_mismatch"
    assert "rx start" in process.commands and "print gain rx1" in process.commands
    assert "tx start" not in process.commands and not result["tx_start_requested"]
    assert result["cleanup_confirmed"]
    assert result["rx_bytes"] == duplex._READY_BYTES
    assert "gain_db" not in result["readback"]["rx1"]


@pytest.mark.parametrize("gain", [1, 50, 60, 66])
def test_supported_positive_overall_tx_gain_requires_matching_plan_and_readback(tx_file, tmp_path, gain):
    result, _ = run(tx_file, tmp_path, settings=replace(duplex.DuplexSettings(SERIAL), tx_gain_db=gain))
    assert result["status"] == "complete"
    assert result["plan_validation"]["requested"]["tx_gain_db"] == gain
    assert result["readback"]["tx1"]["gain_db"] == gain


@pytest.mark.parametrize("mode,error", [
    ("wrong_serial", "identity_mismatch"), ("wrong_rate", "readback_mismatch"),
    ("wrong_bandwidth", "readback_mismatch"),
    ("wrong_tx_gain", "readback_mismatch"),
    ("fractional_rate", "readback_mismatch"), ("missing_readback", "readback_unavailable"),
    ("error_exit_zero", "device_error"), ("no_rx_bytes", "rx_not_ready"),
    ("window_exhausted", "rx_window_exhausted"),
])
def test_fails_closed_before_tx(tx_file, tmp_path, mode, error):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == error
    assert not result["tx_start_requested"] and "tx start" not in process.commands
    assert Path(result["paths"]["manifest"]).is_file()
    assert result["cleanup_confirmed"]


@pytest.mark.parametrize("mode,error", [("short_capture", "incomplete_capture"), ("stream_error", "device_error")])
def test_failures_preserve_raw_after_tx(tx_file, tmp_path, mode, error):
    result, _ = run(tx_file, tmp_path, mode)
    assert result["status"] == "failed" and result["error_kind"] == error
    assert result["tx_start_requested"] and result["rx_bytes"] > 0
    assert Path(result["paths"]["rx"]).is_file()
    assert Path(result["paths"]["log"]).is_file()
    if mode == "stream_error":
        assert result["cleanup_confirmed"] is False


@pytest.mark.parametrize("mode,expect_tx", [("cancel_ready", False), ("cancel_transmit", True)])
def test_cancel_stops_both_paths_and_preserves_partial_capture(tx_file, tmp_path, mode, expect_tx):
    event = threading.Event()
    result, process = run(tx_file, tmp_path, mode, cancel=event)
    assert result["status"] == "cancelled" and result["tx_start_requested"] is expect_tx
    assert "tx stop" in process.commands and "rx stop" in process.commands
    assert result["cleanup_confirmed"]
    assert result["rx_bytes"] == duplex._READY_BYTES


def test_outer_deadline_is_bounded_and_never_automatically_retries(tx_file, tmp_path, monkeypatch):
    monkeypatch.setattr(duplex, "_OUTER_BASE_TIMEOUT_S", -2.8)
    result, process = run(tx_file, tmp_path, "never_complete")
    assert result["status"] == "failed" and result["error_kind"] == "timeout"
    assert process.commands.count("tx start") == 1
    assert result["cleanup_confirmed"]


def test_input_echo_is_not_command_acknowledgement(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path, "input_echo_only")
    assert result["status"] == "failed" and result["error_kind"] == "timeout"
    assert "tx start" not in process.commands


def test_hard_kill_never_claims_rf_cleanup(tx_file, tmp_path):
    result, process = run(tx_file, tmp_path, "cleanup_stuck")
    assert process.killed and not result["cleanup_confirmed"]
    assert result["status"] == "failed" and result["error_kind"] == "cleanup_unconfirmed"
    assert result["capture_completed"] is False


@pytest.mark.parametrize("changes", [
    {"device_serial": "automatic"}, {"device_serial": SERIAL + ";tx start"},
    {"frequency_hz": True}, {"frequency_hz": 60_000_000}, {"sample_rate": 0},
    {"rx_samples": 0}, {"tx_gain_db": 67}, {"tx_gain_db": -30}, {"rx_gain_db": 100}, {"bandwidth_hz": 0},
])
def test_invalid_settings_never_launch_a_process(tx_file, tmp_path, changes):
    called = []
    with pytest.raises(HardwareError, match="."):
        duplex.run_finite_duplex(
            tx_file, tmp_path / "forbidden", replace(duplex.DuplexSettings(SERIAL), **changes),
            connection_plan=valid_plan(), process_factory=lambda *a, **k: called.append(True),
        )
    assert not called and not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("selected", ["f" * 32, "invalid", ""])
def test_launcher_serial_binding_rejects_before_process_or_output(tx_file, tmp_path, monkeypatch, selected):
    monkeypatch.setenv("RF_LINK_LAB_SELECTED_SERIAL", selected)
    with pytest.raises(HardwareError, match="serial") as raised:
        duplex.run_finite_duplex(
            tx_file, tmp_path / "forbidden", duplex.DuplexSettings(SERIAL),
            connection_plan=valid_plan(), process_factory=lambda *a, **kw: pytest.fail("opened a radio"),
        )
    assert raised.value.kind == "preflight"
    assert not (tmp_path / "forbidden").exists()


def test_matching_launcher_serial_binding_is_case_insensitive(tx_file, tmp_path, monkeypatch):
    monkeypatch.setenv("RF_LINK_LAB_SELECTED_SERIAL", SERIAL.upper())
    result, process = run(tx_file, tmp_path)
    assert result["status"] == "complete"
    assert process.argv[-3:] == ["-d", "*:serial=" + SERIAL, "-i"]


@pytest.mark.parametrize("bad", [None, {}, {"schema": "rf-link-lab.conducted-plan.v1"}])
def test_missing_or_incomplete_plan_never_launches(tx_file, tmp_path, bad):
    with pytest.raises(HardwareError):
        duplex.run_finite_duplex(tx_file, tmp_path / "forbidden", duplex.DuplexSettings(SERIAL),
                                 connection_plan=bad, process_factory=lambda *a, **k: pytest.fail("launched"))
    assert not (tmp_path / "forbidden").exists()


def test_existing_evidence_is_not_overwritten(tx_file, tmp_path):
    output = tmp_path / "already"
    output.mkdir()
    evidence = output / "tx.sc16q11"
    evidence.write_bytes(b"preserve")
    with pytest.raises(HardwareError) as raised:
        duplex.run_finite_duplex(tx_file, output, duplex.DuplexSettings(SERIAL),
                                 connection_plan=valid_plan(), process_factory=lambda *a, **k: pytest.fail("launched"))
    assert raised.value.kind == "output_exists" and evidence.read_bytes() == b"preserve"


@pytest.mark.parametrize("data", [b"", b"bad", np.array([2048, 0], dtype="<i2").tobytes(), b"\x00" * 2_000_004],
                         ids=["empty", "misaligned", "range", "duration"])
def test_malformed_or_excessive_tx_never_launches(tx_file, tmp_path, data):
    tx_file.write_bytes(data)
    with pytest.raises(HardwareError):
        duplex.run_finite_duplex(tx_file, tmp_path / "forbidden", duplex.DuplexSettings(SERIAL),
                                 connection_plan=valid_plan(), process_factory=lambda *a, **k: pytest.fail("launched"))


def test_plan_is_revalidated_at_launch_and_actual_tx_boundary(tx_file, tmp_path, monkeypatch):
    from rf_link_lab import conducted_plan
    validator = conducted_plan.validate_connection_plan
    calls = []
    def checked(*args, **kwargs):
        calls.append(kwargs)
        return validator(*args, **kwargs)
    monkeypatch.setattr(conducted_plan, "validate_connection_plan", checked)
    result, _ = run(tx_file, tmp_path)
    assert result["status"] == "complete" and len(calls) == 3


def test_cancel_before_device_open_is_side_effect_free(tx_file, tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(HardwareError) as raised:
        duplex.run_finite_duplex(tx_file, tmp_path / "forbidden", duplex.DuplexSettings(SERIAL),
                                 connection_plan=valid_plan(), cancel_event=event,
                                 process_factory=lambda *a, **k: pytest.fail("launched"))
    assert raised.value.kind == "cancelled" and not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("mode,status,error", [
    ("keyboard_interrupt", "cancelled", "cancelled"),
    ("unexpected_after_tx", "failed", "unexpected_error"),
])
def test_interrupt_or_unexpected_exception_preserves_manifest(tx_file, tmp_path, mode, status, error):
    result, process = run(tx_file, tmp_path, mode)
    assert result["status"] == status and result["error_kind"] == error
    assert result["tx_start_requested"] and result["cleanup_confirmed"]
    assert "tx stop" in process.commands and "rx stop" in process.commands
    assert Path(result["paths"]["rx"]).is_file()
    assert json.loads(Path(result["paths"]["manifest"]).read_text())["status"] == status


def test_unexpected_process_launch_error_is_reported(tx_file, tmp_path):
    def failed_factory(*args, **kwargs):
        raise RuntimeError("test launch failure")
    result = duplex.run_finite_duplex(tx_file, tmp_path / "launch-failed", duplex.DuplexSettings(SERIAL),
                                      connection_plan=valid_plan(), process_factory=failed_factory)
    assert result["status"] == "failed" and result["error_kind"] == "unexpected_error"
    assert not result["tx_start_requested"] and not result["cleanup_confirmed"]
    assert Path(result["paths"]["manifest"]).is_file()


def test_passive_defaults_do_not_validate_or_open_hardware():
    settings = duplex.DuplexSettings()
    assert settings.device_serial == "" and settings.rx_samples == 3_000_000
    assert settings.tx_gain_db == -20 and -23.75 <= settings.tx_gain_db <= 66


@pytest.mark.parametrize("message", ["Usage: set gain channel number", "Unknown command: unsupported-command", "Error: USB failure"])
def test_command_errors_are_never_inferred_from_exit_status_alone(message):
    with pytest.raises(HardwareError):
        duplex._check_errors(message)
