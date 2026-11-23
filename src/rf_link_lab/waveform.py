"""Public burst format and telemetry waveform construction.

There is one published acquisition preamble per burst, followed by complete
17-byte telemetry frames. There is no hidden frame-count field. Receivers
scan the acquired sample stream and validate each recovered frame's CRC.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray

from .modem import bytes_to_bits, qpsk_modulate, shape_symbols
from .protocol import Telemetry, encode_telemetry


PILOT_SYMBOLS = 96
SYNC_SYMBOLS = qpsk_modulate(np.random.default_rng(1701).integers(
    0, 2, 128, dtype=np.uint8))
SYNC_SYMBOLS.setflags(write=False)
PREAMBLE_SYMBOLS = np.concatenate((
    np.full(PILOT_SYMBOLS, (1 + 1j) / np.sqrt(2)), SYNC_SYMBOLS))
PREAMBLE_SYMBOLS.setflags(write=False)
WAVEFORM_FORMAT = "rf-link-lab-qpsk-telemetry-v1"


@dataclass
class WaveformResult:
    tx_iq: NDArray[np.complex128]
    reference_bits: NDArray[np.uint8]
    reference_symbols: NDArray[np.complex128]
    metadata: dict


def generate_waveform(telemetry: Iterable[Telemetry], sample_rate: int = 200000,
                      samples_per_symbol: int = 4) -> WaveformResult:
    """Encode actual telemetry messages; return optional benchmark references.

    This function never opens hardware or transmits. Reference arrays are for
    software benchmark metrics only; they are not required for reception.
    """
    for name, value in (("sample_rate", sample_rate),
                        ("samples_per_symbol", samples_per_symbol)):
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise ValueError(f"{name} must be an integer")
    if sample_rate <= 0 or samples_per_symbol < 2:
        raise ValueError("sample_rate must be positive and samples_per_symbol >=2")
    messages = list(telemetry)
    if not messages:
        raise ValueError("at least one telemetry message is required")
    encoded = b"".join(encode_telemetry(message) for message in messages)
    bits = bytes_to_bits(encoded)
    payload = qpsk_modulate(bits)
    tx_iq = shape_symbols(np.concatenate((PREAMBLE_SYMBOLS, payload)),
                          samples_per_symbol)
    return WaveformResult(tx_iq, bits, payload, {
        "waveform_format": WAVEFORM_FORMAT,
        "sample_rate": int(sample_rate),
        "samples_per_symbol": int(samples_per_symbol),
        "symbol_rate": sample_rate / samples_per_symbol,
        "expected_frames": len(messages),
        "source": "generated telemetry waveform",
    })
