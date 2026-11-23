"""External recordings exercise the receiver without calling the simulator."""

import numpy as np
import pytest

from rf_link_lab.modem import qpsk_demodulate
from rf_link_lab.protocol import Telemetry
from rf_link_lab.receiver import decode_iq, resample_iq
from rf_link_lab import receiver
from rf_link_lab.waveform import generate_waveform


METADATA = {"sample_rate": 200000, "samples_per_symbol": 4}


def capture(messages, *, prefix=773, suffix=523, cfo=1700, sample_rate=200000, sps=4):
    waveform = generate_waveform(messages, sample_rate=sample_rate,
                                  samples_per_symbol=sps)
    rng = np.random.default_rng(8701)
    observed = np.pad(waveform.tx_iq, (prefix, suffix))
    observed = .48 * observed * np.exp(1j * (.82 + 2 * np.pi * cfo *
                                            np.arange(len(observed)) / sample_rate))
    observed += .003 * (rng.standard_normal(len(observed)) +
                       1j * rng.standard_normal(len(observed)))
    return observed, waveform


def test_external_saved_iq_decodes_actual_arbitrary_telemetry_without_tx_reference(tmp_path):
    messages = [Telemetry(583, 9172, False), Telemetry(926, 48439, True),
                Telemetry(0xFFFFFFFF, 0xFFFFFFFF, False)]
    observed, _ = capture(messages)
    saved = tmp_path / "external-complex64.npy"
    np.save(saved, observed.astype(np.complex64), allow_pickle=False)
    received = decode_iq(np.load(saved, allow_pickle=False), METADATA)
    assert received.metrics["status"] == "decoded"
    assert received.metrics["acquisition_valid"] is True
    assert abs(received.metrics["cfo_est_hz"] - 1700) < 5
    assert received.telemetry == [dict(sequence=m.sequence, uptime_ms=m.uptime_ms,
                                        button=m.button, crc_ok=True) for m in messages]
    assert received.metrics["reference_available"] is False
    for key in ("ber", "bit_errors", "bits_total", "bits_compared", "bits_erased",
                "evm_pct", "reference_evm_pct", "packets_total", "frames_expected",
                "frames_lost", "packet_success_rate"):
        assert received.metrics[key] is None
    assert received.features["evm_pct"] is None
    assert received.metrics["pilot_evm_pct"] < 5
    assert received.reference_symbols.size == 0


def test_reference_is_benchmark_only_and_never_controls_frame_count_or_decoding():
    messages = [Telemetry(n + 500, n * 333, bool(n % 2)) for n in range(4)]
    observed, waveform = capture(messages)
    one_message_reference = generate_waveform([Telemetry(1, 2, True)])
    unreferenced = decode_iq(observed, METADATA)
    measured = decode_iq(observed, METADATA, reference=waveform)
    deliberately_wrong = decode_iq(observed, METADATA, reference=one_message_reference)
    assert unreferenced.telemetry == measured.telemetry == deliberately_wrong.telemetry
    assert measured.metrics["ber"] == 0
    assert measured.metrics["evm_pct"] < 5
    assert deliberately_wrong.metrics["ber"] > 0
    assert deliberately_wrong.metrics["bits_total"] == 136
    assert deliberately_wrong.metrics["packets_ok"] == 4
    assert deliberately_wrong.metrics["packets_total"] is None
    assert len(measured.constellation) == len(waveform.reference_symbols)
    np.testing.assert_array_equal(qpsk_demodulate(measured.constellation), waveform.reference_bits)


def test_explicit_expected_frames_is_the_only_packet_denominator():
    observed, _ = capture([Telemetry(1, 52, True), Telemetry(3, 102, False)])
    received = decode_iq(observed, {**METADATA, "expected_frames": 5})
    assert received.metrics["packets_total"] == 5
    assert received.metrics["frames_lost"] == 3
    assert received.metrics["packet_success_rate"] == .4


@pytest.mark.parametrize("cfo,ramp_hz_per_s", [(1700, 2000), (-1700, -2000)])
def test_carrier_loop_tracks_frequency_ramp_after_public_preamble(cfo, ramp_hz_per_s):
    messages = [Telemetry(900 + n, 10000 + n * 123, bool(n % 2)) for n in range(20)]
    observed, waveform = capture(messages, cfo=cfo)
    # The public preamble has a fixed CFO. Independent oscillator drift begins
    # during the payload; no reference array participates in receiver tracking.
    payload_time = np.maximum(0, (np.arange(len(observed)) - 773 - 160 * 4 - 40) / 200000)
    observed *= np.exp(1j * np.pi * ramp_hz_per_s * payload_time ** 2)
    tracked = decode_iq(observed, METADATA, reference=waveform)
    untracked = decode_iq(observed, {**METADATA, "carrier_tracking_enabled": False})
    assert [row["sequence"] for row in tracked.telemetry] == [m.sequence for m in messages]
    assert tracked.metrics["ber"] == 0
    assert tracked.metrics["carrier_tracking_enabled"] and tracked.metrics["carrier_tracking_applied"]
    assert tracked.metadata["carrier_tracking"]["method"] == "decision_directed_qpsk_second_order_pll"
    assert tracked.metadata["carrier_tracking"]["phase_gain"] > 0
    assert tracked.metadata["carrier_tracking"]["frequency_gain"] > 0
    assert untracked.metrics["packets_ok"] < len(messages)


@pytest.mark.parametrize("bias_hz", [-12, 12])
def test_carrier_loop_recovers_small_preamble_cfo_bias_without_reference_leakage(monkeypatch, bias_hz):
    messages = [Telemetry(600 + n, 1000 + n * 17, bool(n % 2)) for n in range(12)]
    observed, waveform = capture(messages)
    refine = receiver._refine_cfo
    monkeypatch.setattr(receiver, "_refine_cfo", lambda *args, **kwargs: refine(*args, **kwargs) + bias_hz)
    blind = decode_iq(observed, METADATA)
    benchmark = decode_iq(observed, {**METADATA, "expected_frames": 1}, reference=waveform)
    wrong_reference = generate_waveform([Telemetry(1, 2, False)])
    wrong = decode_iq(observed, METADATA, reference=wrong_reference)
    assert blind.telemetry == benchmark.telemetry == wrong.telemetry
    assert [row["sequence"] for row in blind.telemetry] == [m.sequence for m in messages]
    assert benchmark.metrics["ber"] == 0
    assert wrong.metrics["ber"] > 0
    np.testing.assert_array_equal(blind.constellation[:len(benchmark.constellation)], benchmark.constellation)
    np.testing.assert_array_equal(blind.constellation[:len(wrong.constellation)], wrong.constellation)


def test_disabling_cfo_correction_also_disables_carrier_tracking():
    observed, waveform = capture([Telemetry(31, 500, True), Telemetry(32, 600, False)], cfo=1700)
    received = decode_iq(observed, {**METADATA, "correct_cfo": False}, reference=waveform)
    assert not received.metrics["cfo_correction_enabled"]
    assert not received.metrics["carrier_tracking_enabled"]
    assert not received.metrics["carrier_tracking_applied"]
    assert not received.metadata["carrier_tracking"]["enabled"]
    assert received.metrics["ber"] > 0


@pytest.mark.parametrize("prefix,suffix", [(0, 0), (1, 2097), (3503, 17003)])
def test_arbitrary_prefix_and_trailing_noise_do_not_set_payload_length(prefix, suffix):
    observed, _ = capture([Telemetry(19, 502, True)], prefix=prefix, suffix=suffix)
    received = decode_iq(observed, METADATA)
    assert received.metrics["packets_ok"] == 1
    assert received.telemetry[0]["sequence"] == 19


def test_truncated_payload_does_not_create_crc_valid_packets():
    waveform = generate_waveform([Telemetry(19, 502, True)])
    # Keep the full published preamble but only half of the payload.
    observed = np.pad(waveform.tx_iq[:(160 + 34) * 4 + 40], (111, 0))
    received = decode_iq(observed, METADATA)
    assert received.metrics["acquisition_valid"] is True
    assert received.metrics["status"] == "synchronized_no_valid_frames"
    assert received.telemetry == []
    assert received.metrics["frames_lost"] is None


@pytest.mark.parametrize("kind,status", [("empty", "empty_capture"),
                                         ("short", "capture_too_short"),
                                         ("zero", "no_signal"),
                                         ("tone", "acquisition_failed")])
def test_explicit_failure_statuses_and_no_fabricated_packets(kind, status):
    values = {"empty": np.empty(0, dtype=complex),
              "short": np.ones(100, dtype=complex),
              "zero": np.zeros(2000, dtype=complex),
              "tone": np.exp(2j * np.pi * .004 * np.arange(8000))}[kind]
    received = decode_iq(values, METADATA)
    assert received.metrics["status"] == status
    assert received.metrics["acquisition_valid"] is False
    assert received.metrics["packets_ok"] == 0
    assert received.metrics["ber"] is None
    assert received.telemetry == []


@pytest.mark.parametrize("seed", range(10))
def test_random_noise_is_not_accepted_as_an_acquired_burst(seed):
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(16000) + 1j * rng.standard_normal(16000)
    received = decode_iq(noise, METADATA)
    assert received.metrics["acquisition_valid"] is False
    assert received.telemetry == []
    assert received.metrics["ber"] is None


def test_one_msps_capture_is_explicitly_resampled_to_baseband_rate():
    observed, _ = capture([Telemetry(901, 123456, True), Telemetry(902, 123556, False)],
                           sample_rate=1000000, sps=20, prefix=1005, suffix=605)
    source = {"sample_rate": 1000000, "symbol_rate": 50000,
              "samples_per_symbol": 20, "source": "external test recording"}
    native = decode_iq(observed, source)
    adapted, metadata = resample_iq(observed, source)
    received = decode_iq(adapted, metadata)
    assert native.telemetry == received.telemetry
    assert received.metrics["packets_ok"] == 2
    assert abs(received.metrics["cfo_est_hz"] - 1700) < 5
    assert metadata["sample_rate"] == 200000
    assert metadata["capture_sample_rate"] == 1000000
    assert metadata["samples_per_symbol"] == 4
    assert metadata["symbol_rate"] == 50000
    assert metadata["resampling"]["down"] == 5
    assert metadata["resampling"]["up"] == 1
    assert source["sample_rate"] == 1000000


def test_mismatched_actual_symbol_rate_does_not_decode():
    observed, _ = capture([Telemetry(9, 22, True)])
    received = decode_iq(observed, {"sample_rate": 1000000, "samples_per_symbol": 20})
    assert received.metrics["acquisition_valid"] is False
    assert received.telemetry == []


@pytest.mark.parametrize("metadata", [{}, {"sample_rate": True, "samples_per_symbol": 4},
                                       {"sample_rate": 200000, "samples_per_symbol": True},
                                       {"sample_rate": 1000000, "samples_per_symbol": 4,
                                        "symbol_rate": 50000},
                                       {**METADATA, "expected_frames": -1},
                                       {**METADATA, "correct_cfo": "yes"},
                                       {**METADATA, "carrier_tracking_enabled": "yes"},
                                       {**METADATA, "waveform_format": "arbitrary QPSK"}])
def test_invalid_or_inconsistent_capture_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        decode_iq(np.ones(2000, dtype=complex), metadata)


@pytest.mark.parametrize("samples", [np.ones((2, 40), dtype=complex), np.ones(2000),
                                      np.array([complex(np.nan, 0)])])
def test_invalid_samples_are_rejected(samples):
    with pytest.raises(ValueError):
        decode_iq(samples, METADATA)


def test_resampling_rejects_noninteger_symbol_timing():
    with pytest.raises(ValueError, match="integer samples_per_symbol"):
        resample_iq(np.ones(2000, dtype=complex), METADATA, target_sample_rate=220000)


def test_waveform_generation_requires_actual_telemetry_and_valid_rates():
    with pytest.raises(ValueError, match="at least one"):
        generate_waveform([])
    with pytest.raises(ValueError, match="integer"):
        generate_waveform([Telemetry(1, 2, False)], samples_per_symbol=4.5)
