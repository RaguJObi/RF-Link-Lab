from dataclasses import replace

import numpy as np
import pytest

from rf_link_lab.simulation import SimulationConfig, run_simulation


def test_healthy_burst_is_deterministic_and_crc_valid():
    first = run_simulation(SimulationConfig())
    second = run_simulation(SimulationConfig())
    np.testing.assert_array_equal(first.rx_iq, second.rx_iq)
    assert first.metrics == second.metrics
    assert first.metrics["ber"] == 0
    assert first.metrics["packets_ok"] == 12
    assert first.metrics["evm_pct"] < 12
    assert [row["sequence"] for row in first.telemetry] == list(range(12))
    assert all(row["crc_ok"] for row in first.telemetry)


@pytest.mark.parametrize("delay,phase,cfo", [(13, -125, -2100), (80, 170, 3500), (1, 0, 800)])
def test_acquisition_and_measured_cfo_compensation(delay, phase, cfo):
    config = SimulationConfig(delay_samples=delay, phase_deg=phase, cfo_hz=cfo, snr_db=32)
    compensated = run_simulation(config)
    uncompensated = run_simulation(replace(config, correct_cfo=False))
    assert abs(compensated.metrics["cfo_est_hz"] - cfo) < 12
    assert compensated.metrics["packet_success_rate"] == 1
    assert compensated.metrics["ber"] == 0
    assert uncompensated.metrics["ber"] > .15
    assert compensated.features["cfo_abs_hz"] == uncompensated.features["cfo_abs_hz"]


def test_noise_and_clipping_degrade_real_decisions():
    healthy = run_simulation(SimulationConfig(snr_db=32))
    noisy = run_simulation(SimulationConfig(snr_db=-2))
    clipped = run_simulation(SimulationConfig(snr_db=32, clip_level=.22))
    assert noisy.metrics["ber"] > healthy.metrics["ber"]
    assert noisy.metrics["packet_success_rate"] < 1
    assert noisy.features["snr_est_db"] < healthy.features["snr_est_db"] - 10
    assert clipped.features["clipping_fraction"] > .5
    assert clipped.metrics["evm_pct"] > healthy.metrics["evm_pct"]
    assert healthy.features["clipping_fraction"] == 0


def test_measurement_features_are_finite():
    result = run_simulation(SimulationConfig(frames=2))
    assert set(result.features) == {"cfo_abs_hz", "evm_pct", "clipping_fraction",
                                    "snr_est_db", "amplitude_cv", "spectral_flatness"}
    assert all(np.isfinite(value) for value in result.features.values())
    assert len(result.spectrum_frequency_hz) == len(result.spectrum_dbfs)


@pytest.mark.parametrize("changes", [{"frames": 1.5}, {"seed": -1},
                                      {"samples_per_symbol": True},
                                      {"delay_samples": -3}, {"snr_db": 150}])
def test_invalid_physical_and_integer_settings_fail_clearly(changes):
    with pytest.raises(ValueError):
        run_simulation(replace(SimulationConfig(), **changes))

