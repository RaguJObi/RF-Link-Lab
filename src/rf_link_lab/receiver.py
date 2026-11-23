"""Independent QPSK receiver for saved or externally captured complex I/Q.

Only samples, declared rates, and the public preamble enter acquisition and
decoding. Transmitted payload references are optional and used *after*
decoding exclusively for aligned benchmark BER and payload-reference EVM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from fractions import Fraction
import math
from numbers import Integral, Real

import numpy as np
from numpy.typing import NDArray
from scipy import signal
from scipy.optimize import minimize_scalar

from .modem import bits_to_bytes, matched_filter, qpsk_demodulate, rrc_taps, shape_symbols
from .protocol import TelemetryParser
from .waveform import PILOT_SYMBOLS, PREAMBLE_SYMBOLS, SYNC_SYMBOLS, WAVEFORM_FORMAT


Metric = float | int | bool | str | None


@dataclass
class ReceiverResult:
    config: dict
    metadata: dict
    metrics: dict[str, Metric]
    features: dict[str, float | None]
    rx_iq: NDArray[np.complex128]
    constellation: NDArray[np.complex128]
    spectrum_frequency_hz: NDArray[np.float64]
    spectrum_dbfs: NDArray[np.float64]
    telemetry: list[dict]
    reference_symbols: NDArray[np.complex128]
    tx_iq: NDArray[np.complex128]


def _rates(metadata: Mapping) -> tuple[int, int]:
    fs = metadata.get("sample_rate")
    if isinstance(fs, bool) or not isinstance(fs, Integral) or fs <= 0:
        raise ValueError("metadata sample_rate must be a positive integer in Hz")
    sps = metadata.get("samples_per_symbol")
    symbol_rate = metadata.get("symbol_rate")
    if sps is None and isinstance(symbol_rate, Real) and not isinstance(symbol_rate, bool):
        if not np.isfinite(symbol_rate) or symbol_rate <= 0:
            raise ValueError("symbol_rate must be positive and finite")
        ratio = fs / symbol_rate
        if not np.isclose(ratio, round(ratio), atol=1e-9, rtol=1e-9):
            raise ValueError("sample_rate / symbol_rate must be an integer; resample first")
        sps = int(round(ratio))
    if isinstance(sps, bool) or not isinstance(sps, Integral) or sps < 2:
        raise ValueError("metadata samples_per_symbol must be an integer >=2")
    if symbol_rate is not None:
        if (isinstance(symbol_rate, bool) or not isinstance(symbol_rate, Real)
                or not np.isfinite(symbol_rate) or symbol_rate <= 0
                or not np.isclose(fs / sps, symbol_rate, rtol=1e-9, atol=1e-9)):
            raise ValueError("sample_rate, samples_per_symbol and symbol_rate disagree")
    if metadata.get("waveform_format", WAVEFORM_FORMAT) != WAVEFORM_FORMAT:
        raise ValueError("unsupported waveform_format; this receiver requires RF Link Lab QPSK telemetry v1")
    return int(fs), int(sps)


def _samples(samples) -> NDArray[np.complex128]:
    values = np.asarray(samples)
    if values.ndim != 1 or (values.size and not np.iscomplexobj(values)):
        raise ValueError("I/Q samples must be a one-dimensional complex array")
    if values.size > 20_000_000:
        raise ValueError("capture exceeds 20 million samples; process a shorter capture")
    values = values.astype(np.complex128, copy=False)
    if not np.all(np.isfinite(values)):
        raise ValueError("I/Q samples must be finite")
    return values


def resample_iq(samples, metadata: Mapping, target_sample_rate: int = 200000
                ) -> tuple[NDArray[np.complex128], dict]:
    """Explicit anti-aliasing adapter, e.g. bladeRF 1 Msps → baseband 200 ksps.

    Symbol rate is preserved. The resampled rate, integer samples/symbol,
    original capture rate, and polyphase ratio remain in returned metadata.
    Frequencies in the output must be interpreted at its *output* rate.
    """
    fs, sps = _rates(metadata)
    if (isinstance(target_sample_rate, bool)
            or not isinstance(target_sample_rate, Integral) or target_sample_rate <= 0):
        raise ValueError("target_sample_rate must be a positive integer")
    symbol_rate = fs / sps
    target_sps = target_sample_rate / symbol_rate
    if target_sps < 2 or not np.isclose(target_sps, round(target_sps), atol=1e-9):
        raise ValueError("target sample rate must give an integer samples_per_symbol >=2")
    values = _samples(samples)
    ratio = Fraction(int(target_sample_rate), fs)
    if max(ratio.numerator, ratio.denominator) > 4096:
        raise ValueError("resampling ratio is too large; choose a simpler integer rate ratio")
    output = (signal.resample_poly(values, ratio.numerator, ratio.denominator)
              if values.size and fs != target_sample_rate else values.copy())
    output_metadata = dict(metadata)
    output_metadata.update({
        "capture_sample_rate": metadata.get("capture_sample_rate", fs),
        "source_sample_rate": fs,
        "sample_rate": int(target_sample_rate),
        "symbol_rate": symbol_rate,
        "samples_per_symbol": int(round(target_sps)),
        "resampling": {"method": "scipy.signal.resample_poly",
                       "up": ratio.numerator, "down": ratio.denominator,
                       "input_samples": len(values), "output_samples": len(output)},
    })
    return output, output_metadata


def _estimate_cfo(samples: NDArray, sample_rate: int, sps: int) -> tuple[float, int]:
    """Repeated-pilot autocorrelation; unambiguous within half symbol rate."""
    lag, width = sps, 64 * sps
    products = samples[lag:] * np.conj(samples[:-lag])
    kernel = np.ones(width)
    correlation = signal.fftconvolve(products, kernel, mode="valid")
    energy_a = signal.fftconvolve(np.abs(samples[lag:]) ** 2, kernel, mode="valid")
    energy_b = signal.fftconvolve(np.abs(samples[:-lag]) ** 2, kernel, mode="valid")
    # FFT convolution has small roundoff even in exact zero guards. Exclude
    # empty windows before division so they cannot acquire an artificial peak.
    floor = max(float(max(np.max(energy_a), np.max(energy_b))) * 1e-12, 1e-24)
    valid = (energy_a > floor) & (energy_b > floor)
    score = np.zeros_like(energy_a)
    score[valid] = np.minimum(1.0, np.abs(correlation[valid]) /
                             np.sqrt(energy_a[valid] * energy_b[valid]))
    index = int(np.argmax(score))
    estimate = float(np.angle(correlation[index]) * sample_rate / (2 * np.pi * lag))
    return estimate, index


def _acquire(filtered: NDArray, sps: int) -> tuple[int, float, complex]:
    """Find public sync across every integer sample timing phase."""
    best_score, best_sample, best_h = -1.0, 0, 1 + 0j
    length = len(SYNC_SYMBOLS)
    for phase in range(sps):
        candidates = filtered[phase::sps]
        if len(candidates) < length:
            continue
        corr = signal.correlate(candidates, SYNC_SYMBOLS, mode="valid", method="fft")
        energy = signal.fftconvolve(np.abs(candidates) ** 2, np.ones(length), mode="valid")
        floor = max(float(np.max(energy)) * 1e-12, 1e-24)
        valid = energy > floor
        scores = np.zeros_like(energy)
        scores[valid] = np.minimum(1.0, np.abs(corr[valid]) /
                                  np.sqrt(length * energy[valid]))
        index = int(np.argmax(scores))
        if scores[index] > best_score:
            best_score = float(scores[index])
            best_sample = phase + index * sps
            best_h = complex(corr[index] / length)
    return best_sample, best_score, best_h


def _refine_cfo(samples: NDArray, coarse_hz: float, sync_sample: int,
                sample_rate: int, sps: int) -> float:
    """Fit CFO against the shaped public preamble; never against payload."""
    delay = sync_sample - (len(rrc_taps(sps)) - 1) - PILOT_SYMBOLS * sps
    template = shape_symbols(PREAMBLE_SYMBOLS, sps)[:(len(PREAMBLE_SYMBOLS) - 5) * sps]
    if delay < 0 or delay + len(template) > len(samples):
        return coarse_hz
    observed = samples[delay:delay + len(template)]
    time = np.arange(len(template)) / sample_rate
    mixed = observed * np.conj(template)

    def cost(offset: float) -> float:
        return -float(abs(np.sum(mixed * np.exp(-2j * np.pi * offset * time))) ** 2)

    radius = sample_rate / (128 * sps)
    fit = minimize_scalar(cost, bounds=(coarse_hz - radius, coarse_hz + radius),
                          method="bounded", options={"xatol": 1e-7})
    return float(fit.x) if fit.success else coarse_hz


def _reference_arrays(reference) -> tuple[NDArray, NDArray]:
    if reference is None:
        return np.empty(0, dtype=np.uint8), np.empty(0, dtype=np.complex128)
    if isinstance(reference, Mapping):
        bits, symbols = reference.get("reference_bits"), reference.get("reference_symbols")
    else:
        bits = getattr(reference, "reference_bits", None)
        symbols = getattr(reference, "reference_symbols", None)
    if bits is None or symbols is None:
        raise ValueError("reference requires reference_bits and reference_symbols")
    bits = np.asarray(bits)
    symbols = np.asarray(symbols, dtype=np.complex128)
    if (bits.ndim != 1 or symbols.ndim != 1 or not len(symbols)
            or len(bits) != 2 * len(symbols) or np.any((bits != 0) & (bits != 1))
            or not np.all(np.isfinite(symbols))):
        raise ValueError("invalid reference bit/symbol arrays")
    return bits.astype(np.uint8), symbols


def _carrier_tracking_parameters(symbol_rate: float, enabled: bool) -> dict:
    bandwidth, damping = .01, 1 / math.sqrt(2)
    denominator = 1 + 2 * damping * bandwidth + bandwidth ** 2
    return {
        "enabled": enabled, "method": "decision_directed_qpsk_second_order_pll",
        "loop_bandwidth_rad_per_symbol": bandwidth, "damping": damping,
        "phase_gain": 4 * damping * bandwidth / denominator,
        "frequency_gain": 4 * bandwidth ** 2 / denominator,
        "maximum_residual_frequency_hz": symbol_rate * .01,
        "minimum_normalized_symbol_magnitude": .2,
        "initial_residual_phase_rad": 0.0, "initial_residual_frequency_hz": 0.0,
        "input_basis": "Public-preamble channel estimate and received symbol decisions; no payload reference or expected frame count.",
    }


def _track_carrier(symbols: NDArray, symbol_rate: float, parameters: dict) -> NDArray:
    """Track residual phase/frequency using only nearest-QPSK decisions.

    The acquisition estimate removes the large CFO and resolves the quadrant.
    This type-II loop follows the remaining drift across a burst. Magnitude
    gating avoids teaching the loop from near-zero guards or weak noise.
    """
    corrected = np.empty_like(symbols)
    phase = frequency = 0.0
    alpha, beta = parameters["phase_gain"], parameters["frequency_gain"]
    limit = 2 * math.pi * parameters["maximum_residual_frequency_hz"] / symbol_rate
    floor_squared = parameters["minimum_normalized_symbol_magnitude"] ** 2
    for index, sample in enumerate(symbols):
        observed = complex(sample) * complex(math.cos(phase), -math.sin(phase))
        corrected[index] = observed
        error = 0.0
        if observed.real ** 2 + observed.imag ** 2 >= floor_squared:
            decision_i = -1.0 if observed.real < 0 else 1.0
            decision_q = -1.0 if observed.imag < 0 else 1.0
            error = math.atan2(decision_i * observed.imag - decision_q * observed.real,
                               decision_i * observed.real + decision_q * observed.imag)
            frequency = max(-limit, min(limit, frequency + beta * error))
        phase = (phase + frequency + alpha * error + math.pi) % (2 * math.pi) - math.pi
    return corrected


def decode_iq(samples, metadata: Mapping, reference=None) -> ReceiverResult:
    """Acquire and decode a single RF Link Lab burst from external I/Q.

    Required metadata: integer ``sample_rate`` and ``samples_per_symbol``
    (or ``symbol_rate``). Optional: ``expected_frames`` and ``correct_cfo``.
    Unknown denominators and unavailable payload-reference BER/EVM are None.
    This burst receiver assumes integer symbol timing and no sample-clock
    drift; it is not an arbitrary-radio decoder or calibrated RF instrument.
    """
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping containing the actual sample rate")
    fs, sps = _rates(metadata)
    values = _samples(samples)
    expected = metadata.get("expected_frames")
    if expected is not None and (isinstance(expected, bool)
                                 or not isinstance(expected, Integral) or expected < 0):
        raise ValueError("expected_frames must be a nonnegative integer or absent")
    correct_cfo = metadata.get("correct_cfo", True)
    if not isinstance(correct_cfo, (bool, np.bool_)):
        raise ValueError("correct_cfo must be boolean")
    tracking_requested = metadata.get("carrier_tracking_enabled", True)
    if not isinstance(tracking_requested, (bool, np.bool_)):
        raise ValueError("carrier_tracking_enabled must be boolean")
    tracking_enabled = bool(correct_cfo and tracking_requested)
    tracking_parameters = _carrier_tracking_parameters(fs / sps, tracking_enabled)
    tx_bits, payload_reference = _reference_arrays(reference)
    ref_available = reference is not None
    result_metadata = dict(metadata)
    result_metadata.update({"sample_rate": fs, "samples_per_symbol": sps,
                            "symbol_rate": fs / sps, "waveform_format": WAVEFORM_FORMAT,
                            "carrier_tracking_enabled": tracking_enabled,
                            "carrier_tracking": tracking_parameters})
    frequency = np.empty(0, dtype=float)
    spectrum = np.empty(0, dtype=float)
    flatness = None
    clipping_fraction = 0.0
    if len(values):
        frequency, psd = signal.welch(values, fs=fs, nperseg=min(1024, len(values)),
                                      return_onesided=False, scaling="spectrum")
        frequency, psd = np.fft.fftshift(frequency), np.fft.fftshift(np.real(psd))
        # Legacy spectrum_dbfs name: unit complex mean power per Welch bin,
        # not calibrated dBm, nor the ADC clipping rail, nor density per Hz.
        spectrum = 10 * np.log10(np.maximum(psd, 1e-16))
        selected_psd = np.maximum(psd[np.abs(frequency) < (fs / sps) * .675], 1e-16)
        if len(selected_psd):
            flatness = float(np.exp(np.mean(np.log(selected_psd))) / np.mean(selected_psd))
        rail_i, rail_q = float(np.max(np.abs(values.real))), float(np.max(np.abs(values.imag)))
        rail_hits = ((np.abs(values.real) >= rail_i - max(1e-12, rail_i * 1e-8))
                     | (np.abs(values.imag) >= rail_q - max(1e-12, rail_q * 1e-8)))
        if np.any(np.abs(values) > 0):
            clipping_fraction = float(max(0, np.count_nonzero(rail_hits) - 2) / len(values))
    metrics: dict[str, Metric] = {
        "status": "empty_capture", "acquisition_valid": False,
        "synchronization_valid": False, "reference_available": ref_available,
        "benchmark_alignment_valid": False,
        "ber": None, "bit_errors": None, "bits_total": len(tx_bits) if ref_available else None,
        "bits_compared": 0 if ref_available else None,
        "bits_erased": len(tx_bits) if ref_available else None,
        "frames_lost": int(expected) if expected is not None else None,
        "frames_expected": int(expected) if expected is not None else None,
        "evm_pct": None, "reference_evm_pct": None, "pilot_evm_pct": None,
        "packet_success_rate": 0.0 if expected else None,
        "packets_ok": 0, "packets_total": int(expected) if expected is not None else None,
        "crc_rejected_candidates": 0, "decoded_bytes": 0,
        "cfo_est_hz": None, "clipping_fraction": clipping_fraction,
        "snr_est_db": None, "sync_score": None,
        "cfo_correction_enabled": bool(correct_cfo), "sample_count": len(values),
        "carrier_tracking_enabled": tracking_enabled, "carrier_tracking_applied": False,
    }
    features = {"cfo_abs_hz": None, "evm_pct": None,
                "clipping_fraction": clipping_fraction, "snr_est_db": None,
                "amplitude_cv": None, "spectral_flatness": flatness}

    def result(constellation=None, received=None):
        return ReceiverResult(result_metadata.copy(), result_metadata, metrics, features,
                              values, np.empty(0, dtype=complex) if constellation is None else constellation,
                              frequency, spectrum, received or [], payload_reference,
                              np.empty(0, dtype=complex))

    if not len(values):
        return result()
    if len(values) < (PILOT_SYMBOLS + len(SYNC_SYMBOLS)) * sps:
        metrics["status"] = "capture_too_short"
        return result()
    if float(np.mean(np.abs(values) ** 2)) < 1e-18:
        metrics["status"] = "no_signal"
        return result()

    sample_indices = np.arange(len(values))
    cfo, _ = _estimate_cfo(values, fs, sps)
    coarse_cfo = cfo
    corrected = values * np.exp(-2j * np.pi * cfo * sample_indices / fs)
    acquisition_filter = matched_filter(corrected, sps)
    sync_sample, score, channel_h = _acquire(acquisition_filter, sps)
    cfo = _refine_cfo(values, cfo, sync_sample, fs, sps)
    corrected = values * np.exp(-2j * np.pi * cfo * sample_indices / fs)
    acquisition_filter = matched_filter(corrected, sps)
    sync_sample, score, channel_h = _acquire(acquisition_filter, sps)
    if score < .6:
        # A noisy lag estimate or a local preamble-fit minimum can miss the
        # narrow sync peak. Test nearby CFO hypotheses with the public sync.
        # Neither expected payload length nor reference payload participates.
        radius = fs / (128 * sps)
        for offset in np.linspace(-3 * radius, 3 * radius, 13):
            candidate_cfo = coarse_cfo + float(offset)
            if abs(candidate_cfo) >= fs / (2 * sps):
                continue
            candidate = values * np.exp(-2j * np.pi * candidate_cfo * sample_indices / fs)
            filtered = matched_filter(candidate, sps)
            candidate_sample, candidate_score, candidate_h = _acquire(filtered, sps)
            if candidate_score > score:
                cfo, sync_sample, score, channel_h = (candidate_cfo, candidate_sample,
                                                     candidate_score, candidate_h)
                corrected, acquisition_filter = candidate, filtered
        refined_cfo = _refine_cfo(values, cfo, sync_sample, fs, sps)
        candidate = values * np.exp(-2j * np.pi * refined_cfo * sample_indices / fs)
        filtered = matched_filter(candidate, sps)
        candidate_sample, candidate_score, candidate_h = _acquire(filtered, sps)
        if candidate_score > score:
            cfo, sync_sample, score, channel_h = (refined_cfo, candidate_sample,
                                                 candidate_score, candidate_h)
            corrected, acquisition_filter = candidate, filtered
    metrics.update({"cfo_est_hz": cfo, "sync_score": score})
    features["cfo_abs_hz"] = abs(cfo)
    # Reject unacquired noise before interpreting any sample as a frame.
    valid = bool(score >= .6 and abs(channel_h) >= 1e-10)
    metrics.update({"acquisition_valid": valid, "synchronization_valid": valid})
    if not valid:
        metrics["status"] = "acquisition_failed"
        return result()

    group_delay = (len(rrc_taps(sps)) - 1) // 2
    sync_positions = sync_sample + np.arange(len(SYNC_SYMBOLS)) * sps
    sync_observed = acquisition_filter[sync_positions] / channel_h
    pilot_evm = float(100 * np.sqrt(np.mean(np.abs(sync_observed - SYNC_SYMBOLS) ** 2)))
    metrics["pilot_evm_pct"] = pilot_evm
    pilot_end = sync_sample - group_delay - 16 * sps
    pilot_start = pilot_end - 64 * sps
    pilot = corrected[max(0, pilot_start):max(0, pilot_end)]
    if len(pilot) >= 8:
        pilot_mean = np.mean(pilot)
        noise_power = float(np.mean(np.abs(pilot - pilot_mean) ** 2))
        signal_power = float(abs(pilot_mean) ** 2)
        snr_estimate = float(10 * np.log10(max(signal_power, 1e-12) / max(noise_power, 1e-12)))
        metrics["snr_est_db"] = features["snr_est_db"] = snr_estimate
    active = values[max(0, sync_sample - group_delay):]
    if active.size:
        amplitudes = np.abs(active)
        features["amplitude_cv"] = float(np.std(amplitudes) / max(np.mean(amplitudes), 1e-12))
    payload_start = sync_sample + len(SYNC_SYMBOLS) * sps
    if correct_cfo:
        decision_filter, decision_h = acquisition_filter, channel_h
    else:
        decision_filter = matched_filter(values, sps)
        midpoint = sync_sample + (len(SYNC_SYMBOLS) - 1) * sps / 2
        decision_h = channel_h * np.exp(2j * np.pi * cfo *
                                       (midpoint - group_delay) / fs)
    # Input capture length supplies the decision boundary. It is deliberately
    # independent of the transmitted frame count or optional reference length.
    # Full-convolution filter tails cannot create samples beyond this boundary.
    positions = np.arange(payload_start, len(values), sps, dtype=int)
    constellation = decision_filter[positions] / decision_h
    if tracking_enabled and len(constellation):
        constellation = _track_carrier(constellation, fs / sps, tracking_parameters)
        metrics["carrier_tracking_applied"] = True
    recovered_bits = qpsk_demodulate(constellation)
    whole_byte_bits = len(recovered_bits) // 8 * 8
    recovered_bytes = bits_to_bytes(recovered_bits[:whole_byte_bits])
    parser = TelemetryParser()
    decoded = parser.feed(recovered_bytes)
    received = [dict(asdict(message), crc_ok=True) for message in decoded]
    metrics.update({"status": "decoded" if decoded else "synchronized_no_valid_frames",
                    "packets_ok": len(decoded), "decoded_bytes": len(recovered_bytes),
                    "crc_rejected_candidates": parser.rejected_frames})
    if expected is not None:
        metrics["frames_lost"] = max(0, int(expected) - len(decoded))
        metrics["packet_success_rate"] = min(1.0, len(decoded) / expected) if expected else None
    if ref_available:
        count = min(len(constellation), len(payload_reference))
        bits_compared = count * 2
        bit_errors = int(np.count_nonzero(recovered_bits[:bits_compared] != tx_bits[:bits_compared]))
        evm = (float(100 * np.sqrt(np.mean(np.abs(constellation[:count] - payload_reference[:count]) ** 2)))
               if count else None)
        metrics.update({"ber": bit_errors / bits_compared if bits_compared else None,
                        "bit_errors": bit_errors, "bits_compared": bits_compared,
                        "bits_erased": len(tx_bits) - bits_compared,
                        "evm_pct": evm, "reference_evm_pct": evm,
                        "benchmark_alignment_valid": bool(count)})
        features["evm_pct"] = evm
        # Keep known-payload plots backward compatible; this restriction takes
        # place after parsing, and therefore cannot repair or constrain frames.
        constellation = constellation[:count]
    return result(constellation, received)
