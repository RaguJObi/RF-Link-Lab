"""QPSK and root-raised-cosine signal processing, without hardware dependencies."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import signal


def bytes_to_bits(data: bytes) -> NDArray[np.uint8]:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")


def bits_to_bytes(bits: NDArray) -> bytes:
    values = np.asarray(bits, dtype=np.uint8).ravel()
    if len(values) % 8:
        raise ValueError("byte conversion requires a multiple of eight bits")
    if np.any(values > 1):
        raise ValueError("bits must be zero or one")
    return np.packbits(values, bitorder="big").tobytes()


def qpsk_modulate(bits: NDArray) -> NDArray[np.complex128]:
    """Gray QPSK: 00=(+I,+Q), 01=(+I,-Q), 10=(-I,+Q), 11=(-I,-Q)."""
    values = np.asarray(bits, dtype=np.int8).ravel()
    if len(values) % 2 or np.any((values < 0) | (values > 1)):
        raise ValueError("QPSK requires an even number of binary bits")
    pairs = values.reshape(-1, 2)
    return ((1 - 2 * pairs[:, 0]) + 1j * (1 - 2 * pairs[:, 1])) / np.sqrt(2)


def qpsk_demodulate(symbols: NDArray) -> NDArray[np.uint8]:
    values = np.asarray(symbols)
    return np.column_stack((values.real < 0, values.imag < 0)).astype(np.uint8).ravel()


def rrc_taps(samples_per_symbol: int = 4, span_symbols: int = 10,
             rolloff: float = 0.35) -> NDArray[np.float64]:
    """Unit-energy RRC pulse; span is even so group delay is integral."""
    if samples_per_symbol < 2 or span_symbols < 2 or span_symbols % 2:
        raise ValueError("use samples_per_symbol >=2 and an even pulse span >=2")
    if not 0 < rolloff <= 1:
        raise ValueError("rolloff must be in (0, 1]")
    t = np.arange(-span_symbols * samples_per_symbol // 2,
                  span_symbols * samples_per_symbol // 2 + 1) / samples_per_symbol
    pulse = np.empty_like(t, dtype=float)
    for index, value in enumerate(t):
        if np.isclose(value, 0):
            pulse[index] = 1 + rolloff * (4 / np.pi - 1)
        elif np.isclose(abs(value), 1 / (4 * rolloff)):
            pulse[index] = rolloff / np.sqrt(2) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * rolloff))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * rolloff))
            )
        else:
            pulse[index] = (
                np.sin(np.pi * value * (1 - rolloff))
                + 4 * rolloff * value * np.cos(np.pi * value * (1 + rolloff))
            ) / (np.pi * value * (1 - (4 * rolloff * value) ** 2))
    return pulse / np.linalg.norm(pulse)


def shape_symbols(symbols: NDArray, samples_per_symbol: int = 4) -> NDArray:
    """TX scaling makes random QPSK's sample-domain mean power about one."""
    return signal.upfirdn(rrc_taps(samples_per_symbol) * np.sqrt(samples_per_symbol),
                          symbols, up=samples_per_symbol)


def matched_filter(samples: NDArray, samples_per_symbol: int = 4) -> NDArray:
    """RX scaling restores unit-amplitude decisions after the TX/RX pair."""
    return signal.fftconvolve(samples, rrc_taps(samples_per_symbol) /
                               np.sqrt(samples_per_symbol), mode="full")

