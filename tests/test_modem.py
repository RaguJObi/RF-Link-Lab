import numpy as np

from rf_link_lab.modem import (bits_to_bytes, bytes_to_bits, matched_filter,
                               qpsk_demodulate, qpsk_modulate, rrc_taps,
                               shape_symbols)


def test_qpsk_gray_mapping_and_byte_order():
    bits = np.array([0, 0, 0, 1, 1, 0, 1, 1], dtype=np.uint8)
    expected = np.array([1+1j, 1-1j, -1+1j, -1-1j]) / np.sqrt(2)
    np.testing.assert_allclose(qpsk_modulate(bits), expected)
    np.testing.assert_array_equal(qpsk_demodulate(expected), bits)
    assert bits_to_bytes(bits) == b"\x1b"
    np.testing.assert_array_equal(bytes_to_bits(b"\x1b"), bits)


def test_rrc_matched_pair_recovers_random_symbols():
    rng = np.random.default_rng(2)
    symbols = qpsk_modulate(rng.integers(0, 2, 1200))
    taps = rrc_taps(4)
    assert abs(np.sum(taps ** 2) - 1) < 1e-12
    np.testing.assert_allclose(taps, taps[::-1])
    waveform = matched_filter(shape_symbols(symbols, 4), 4)
    decisions = waveform[len(taps)-1::4][:len(symbols)]
    assert np.sqrt(np.mean(np.abs(decisions-symbols)**2)) < .015
    np.testing.assert_array_equal(qpsk_demodulate(decisions), qpsk_demodulate(symbols))

