"""No board is opened: process and serial interactions are mocked."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from rf_link_lab.hardware import HardwareError, capture_rx, load_sc16q11, probe_bladerf


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class HardwareTests(unittest.TestCase):
    @patch("rf_link_lab.hardware.subprocess.run", side_effect=FileNotFoundError)
    def test_probe_missing_tool_is_distinct(self, run):
        result = probe_bladerf()
        self.assertFalse(result["available"])
        self.assertEqual(result["error_kind"], "missing_tool")

    @patch("rf_link_lab.hardware.subprocess.run", return_value=completed(1, stderr="No bladeRF device(s) available."))
    def test_probe_no_device(self, run):
        self.assertEqual(probe_bladerf()["error_kind"], "no_device")
        self.assertEqual(run.call_count, 1)

    @patch("rf_link_lab.hardware.subprocess.run", side_effect=subprocess.TimeoutExpired("bladeRF-cli", 10))
    def test_probe_timeout(self, run):
        self.assertEqual(probe_bladerf()["error_kind"], "timeout")

    @patch("rf_link_lab.hardware.subprocess.run", side_effect=[completed(stdout="Serial: demo"), completed(stdout="FPGA loaded: yes\nVersion: demo")])
    def test_probe_identity_and_version_only(self, run):
        result = probe_bladerf()
        self.assertTrue(result["available"])
        self.assertEqual(run.call_args_list[0].args[0], ["bladeRF-cli", "-p"])
        self.assertEqual(run.call_args_list[1].args[0], ["bladeRF-cli", "-e", "info", "-e", "version"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_capture_is_finite_rx_only_and_path_cannot_become_command(self):
        def simulate(args, **kwargs):
            Path(kwargs["cwd"], "capture.sc16q11").write_bytes(b"\x00" * 400)
            return completed(stdout="RX1 Sample rate: 2000000\nState: Idle\nLast error: None")

        with tempfile.TemporaryDirectory() as directory:
            # Semicolons are valid filenames on both supported desktop systems.
            output = Path(directory) / "capture;tx start.sc16q11"
            with patch("rf_link_lab.hardware.subprocess.run", side_effect=simulate) as run:
                result = capture_rx(output, 100_000_000, 2_000_000, 100, 10)
            args = run.call_args.args[0]
            commands = args[2::2]
            self.assertFalse(any("tx " in value for value in commands))
            self.assertNotIn(str(output), args)
            self.assertIn("rx wait", commands)
            self.assertIn("rx config file=capture.sc16q11 format=bin channel=1 n=100", commands)
            self.assertIn("set loopback none", commands)
            self.assertIn("set agc rx1 off", commands)
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertGreater(run.call_args.kwargs["timeout"], 0)
            self.assertEqual(output.stat().st_size, 400)
            metadata = json.loads(output.with_suffix(output.suffix + ".json").read_text())
            self.assertEqual(metadata["source"], "hardware_rx")
            self.assertEqual(len(metadata["sha256"]), 64)
            self.assertEqual(result["requested"]["sample_rate"], 2_000_000)

    @patch("rf_link_lab.hardware.subprocess.run")
    def test_invalid_settings_cannot_reach_cli(self, run):
        invalid = [
            ("100000000; tx start", 2_000_000, 100, 10),
            (100_000_000, True, 100, 10),
            (100_000_000, 2_000_000, 0, 10),
            (100_000_000, 2_000_000, 100, 999),
        ]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                capture_rx(Path("unused.sc16q11"), *settings)
        run.assert_not_called()

    def test_existing_capture_preserved_before_hardware_access(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "keep.sc16q11"
            output.write_bytes(b"keep")
            with patch("rf_link_lab.hardware.subprocess.run") as run, self.assertRaises(FileExistsError):
                capture_rx(output, 100_000_000, 2_000_000, 100, 10)
            run.assert_not_called()
            self.assertEqual(output.read_bytes(), b"keep")

    def test_short_recording_rejected(self):
        def simulate(args, **kwargs):
            Path(kwargs["cwd"], "capture.sc16q11").write_bytes(b"\x00" * 396)
            return completed()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "short.sc16q11"
            with patch("rf_link_lab.hardware.subprocess.run", side_effect=simulate), self.assertRaises(HardwareError) as raised:
                capture_rx(output, 100_000_000, 2_000_000, 100, 10)
            self.assertEqual(raised.exception.kind, "incomplete_capture")
            self.assertFalse(output.exists())

    def test_capture_timeout_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeout.sc16q11"
            with patch("rf_link_lab.hardware.subprocess.run", side_effect=subprocess.TimeoutExpired("bladeRF-cli", 20)), self.assertRaises(HardwareError) as raised:
                capture_rx(output, 100_000_000, 2_000_000, 100, 10)
            self.assertEqual(raised.exception.kind, "timeout")
            self.assertFalse(output.exists())

    def test_transfer_error_rejected_even_if_process_exits_zero(self):
        def simulate(args, **kwargs):
            Path(kwargs["cwd"], "capture.sc16q11").write_bytes(b"\x00" * 400)
            return completed(stdout="State: Idle\nLast error: Operation timed out")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed.sc16q11"
            with patch("rf_link_lab.hardware.subprocess.run", side_effect=simulate), self.assertRaises(HardwareError) as raised:
                capture_rx(output, 100_000_000, 2_000_000, 100, 10)
            self.assertEqual(raised.exception.kind, "device_error")
            self.assertFalse(output.exists())

    def test_raw_replay_little_endian_scaling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "known.sc16q11"
            np.array([-2048, 1024, 2047, 0], dtype="<i2").tofile(path)
            samples = load_sc16q11(path)
            np.testing.assert_allclose(samples, [-1 + 0.5j, 2047 / 2048 + 0j])
            self.assertEqual(samples.dtype, np.complex64)

    def test_raw_replay_rejects_bad_length_and_out_of_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sc16q11"
            for contents in [b"", b"123", np.array([3000, 0], dtype="<i2").tobytes()]:
                path.write_bytes(contents)
                with self.subTest(contents=contents), self.assertRaises(ValueError):
                    load_sc16q11(path)

    def test_raw_replay_selected_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.sc16q11"
            path.write_bytes(b"\x00" * 8)
            with self.assertRaises(ValueError):
                load_sc16q11(path, maximum_samples=1)


if __name__ == "__main__":
    unittest.main()
