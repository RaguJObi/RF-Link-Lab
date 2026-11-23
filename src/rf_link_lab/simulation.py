"""Deterministic, sampled-baseband telemetry experiment.

Channel order: integer delay -> gain and carrier rotation -> fixed-reference
AWGN -> separate I/Q ADC rails. AWGN power is 10**(-snr_db/10), referred to
unit mean input power; changing gain therefore also changes received SNR.

The receiver knows sample rate, symbol rate and the published preamble. It
does not consume injected delay, gain, phase, CFO, noise or clipping settings.
Known payload is used only for benchmark BER/EVM, never to repair decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral

import numpy as np
from numpy.typing import NDArray
from .protocol import Telemetry
from .receiver import decode_iq
from .waveform import generate_waveform, PILOT_SYMBOLS, SYNC_SYMBOLS


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 42
    frames: int = 12
    snr_db: float = 24
    cfo_hz: float = 0
    gain: float = 0.65
    clip_level: float = 1.0
    sample_rate: int = 200000
    samples_per_symbol: int = 4
    phase_deg: float = 15
    delay_samples: int = 37
    correct_cfo: bool = True


@dataclass
class SimulationResult:
    config: SimulationConfig
    metrics: dict[str, float | int | bool | str | None]
    features: dict[str, float | None]
    tx_iq: NDArray[np.complex128]
    rx_iq: NDArray[np.complex128]
    constellation: NDArray[np.complex128]
    spectrum_frequency_hz: NDArray[np.float64]
    spectrum_dbfs: NDArray[np.float64]
    telemetry: list[dict]
    reference_symbols: NDArray[np.complex128]
    reference_bits: NDArray[np.uint8]
    metadata: dict


def _validate(config: SimulationConfig) -> None:
    for name in ("seed", "frames", "sample_rate", "samples_per_symbol", "delay_samples"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise ValueError(f"{name} must be an integer")
    if config.seed < 0:
        raise ValueError("seed must be nonnegative")
    numeric = (config.snr_db, config.cfo_hz, config.gain, config.clip_level,
               config.phase_deg)
    if not np.all(np.isfinite(numeric)):
        raise ValueError("simulation settings must be finite")
    if not -60 <= config.snr_db <= 120:
        raise ValueError("noise reference SNR must be between -60 and 120 dB")
    if not 1 <= config.frames <= 1000:
        raise ValueError("frames must be between 1 and 1000")
    if config.samples_per_symbol < 2 or config.sample_rate <= 0:
        raise ValueError("sample_rate must be positive and samples_per_symbol >=2")
    if config.delay_samples < 0 or config.delay_samples > 100000:
        raise ValueError("delay_samples must be between 0 and 100000")
    if config.gain <= 0 or config.clip_level <= 0:
        raise ValueError("gain and clip_level must be positive")
    if abs(config.cfo_hz) >= config.sample_rate / (2 * config.samples_per_symbol):
        raise ValueError("CFO must lie within half the symbol rate for pilot acquisition")



def run_simulation(config: SimulationConfig | None = None) -> SimulationResult:
    """Generate a reproducible channel, then call the independent receiver.

    The receiver gets waveform rates/public format plus optional benchmark
    references, never the injected channel controls or original messages.
    """
    config = config or SimulationConfig()
    _validate(config)
    rng = np.random.default_rng(config.seed)
    messages = [Telemetry(sequence=index, uptime_ms=index * 100,
                          button=(index % 5 == 0)) for index in range(config.frames)]
    waveform = generate_waveform(messages, config.sample_rate, config.samples_per_symbol)
    tx_iq = waveform.tx_iq
    # Padding belongs to the channel; decoding length comes from received I/Q.
    delayed = np.pad(tx_iq, (config.delay_samples, 12 * config.samples_per_symbol))
    sample_indices = np.arange(len(delayed))
    carrier = np.exp(1j * (np.deg2rad(config.phase_deg)
                         + 2 * np.pi * config.cfo_hz * sample_indices / config.sample_rate))
    noise_sigma = np.sqrt(10 ** (-config.snr_db / 10) / 2)
    analog = config.gain * delayed * carrier + noise_sigma * (
        rng.standard_normal(len(delayed)) + 1j * rng.standard_normal(len(delayed)))
    rx_iq = (np.clip(analog.real, -config.clip_level, config.clip_level)
             + 1j * np.clip(analog.imag, -config.clip_level, config.clip_level))
    metadata = {**waveform.metadata, "correct_cfo": config.correct_cfo,
                "provenance": "synthetic"}
    received = decode_iq(rx_iq, metadata, reference=waveform)
    return SimulationResult(config, received.metrics, received.features, tx_iq, rx_iq,
                            received.constellation, received.spectrum_frequency_hz,
                            received.spectrum_dbfs, received.telemetry,
                            waveform.reference_symbols, waveform.reference_bits, metadata)
