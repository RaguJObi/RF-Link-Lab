# Finite TX waveform and known-payload comparison

This is the hardware-independent foundation for the conducted-link controller.
Preparing or inspecting a waveform opens no serial port, probes no board, and
does not transmit. `prepared_tx_waveform_no_rf` records software preparation;
the receiver preserves an RX file's original declared origin separately.

## Preparing the bytes offered to a transmitter

```python
from pathlib import Path
from rf_link_lab.protocol import Telemetry
from rf_link_lab.conducted_waveform import prepare_tx_waveform

manifest = prepare_tx_waveform(
    [Telemetry(101, 1000, False), Telemetry(102, 1100, True)],
    Path("new-exclusive-tx-folder"),
    sample_rate=1_000_000, symbol_rate=50_000, tx_scale=0.25,
)
```

The output directory must be new. It contains:

- `tx.sc16q11`: exact little-endian signed int16 I,Q pairs offered to TX.
- `tx-reference.npz`: the actual quantized TX samples, expected payload bits,
  and normalized payload constellation measured from those quantized samples.
- `tx.json`: file hashes, complete frame bytes and individual frame hashes,
  telemetry values, rates, guards, normalization, quantization and duration.

The existing public QPSK format has one acquisition preamble followed by the
actual 17-byte telemetry frames. At the default 50 ksymbol/s, generation starts
at 200 kcomplex samples/s and four samples/symbol. An explicit rational
`scipy.signal.resample_poly` conversion produces the hardware sample rate.
Other supported symbol rates retain a four-sample/symbol generation baseline.

Project limits are 1–10 Mcomplex samples/s, 10–250 ksymbol/s, integer hardware
samples/symbol of at least four, at most 2,048 frames, 20 million samples and
10 seconds per burst. These are application limits, not device capabilities.
Default leading and trailing guards each contain 10 ms of exact zero samples;
`guard_samples` changes both lengths explicitly. There is one burst and one
repeat only. Guards help streaming alignment; they do not establish RF timing.

The resampled waveform is divided by its peak absolute I or Q component,
then multiplied by `tx_scale`. Thus the default 0.25 means 512 positive SC16Q11
codes at the largest component before rounding. Components are rounded once
with `numpy.rint` and divided by 2048 when loaded. Overflow raises an error:
there is no silent clipping, wrapping or automatic scale adjustment. Scales
whose rounding erases the waveform are rejected. The manifest records both
rail headrooms. `tx_scale` is a digital amplitude setting, not dBm or a
calibration of TX connector power.

## Verifying and decoding a captured file

```python
from rf_link_lab.conducted_waveform import load_tx_reference, decode_known_capture

reference = load_tx_reference(manifest)
result = decode_known_capture(Path("capture.sc16q11"), manifest)
```

`load_tx_reference` checks local file paths, hashes, finite-burst constraints,
rates, payload bytes, zero guards, quantization and array consistency. It
independently decodes the **saved quantized stream** to verify its stored
constellation. It never regenerates a transmitter from stored configuration.
NumPy archives are loaded with `allow_pickle=False`. Hashes detect changes
relative to the manifest; they are not signatures or authentication.

`decode_known_capture` uses the existing raw SC16Q11 reader and sidecar hash
checks, rejects contradictory declared rates and counts, explicitly resamples
to four samples/symbol, and calls the independent receiver. Payload references
are applied only after acquisition and frame parsing; they cannot repair CRCs
or force an expected number of frames into a recording. A sidecar declaration
of a hardware origin is preserved, but is not independent proof of RF.

The result distinguishes:

- `packets_ok`: all CRC-valid received telemetry frames.
- `known_payload_frames_matched`: exact full-frame matches, including CRC,
  limited to the multiplicity of each expected frame.
- `known_payload_frames_missing`: expected full frames not recovered.
- `known_payload_frames_unexpected`: CRC-valid frames beyond the expected
  payload, including excess duplicates.
- `known_payload_duplicate_frames`: excess copies of an expected frame.
- `packet_success_rate`: exact matches / expected frames.
- `bits_compared` and `bits_erased`: the aligned benchmark's observed and
  unobserved portions of the known payload.

Full-payload BER is unavailable for a truncated benchmark. The separate
`compared_bit_error_rate` retains the comparison over observed decisions and
`ber_scope` explains the scope. An apparent zero comparison with missing
expected frames is never promoted to a complete-payload BER of zero. Missing
frames remain missing even if unrelated frames have valid CRCs.

Reference EVM compares the independently acquired RX payload decisions against
the stored constellation measured from actual TX words. It is conditional on
acquisition and available aligned decisions; it is not a calibrated instrument
measurement. The current receiver resolves integer sample timing at four
samples/symbol, so a fractional post-resampling timing offset can produce
visible EVM while every bit and CRC still decodes correctly. Carrier-offset
correction uses the public preamble, never the payload reference.

## What the software tests establish

Tests cover exact quantized round trips, zero guards, arbitrary timing and
phase shifts, gain, CFO, noise, truncated captures, unrelated CRC-valid frames,
repeated expected frames, tampered files, inconsistent metadata and exclusive
output directories. The zero-guard regression also ensures normalized sync
scores remain between zero and one: FFT roundoff in empty windows cannot win
acquisition. These tests establish software behavior only. They do not prove
Ubuntu execution, bladeRF streaming, RF performance, cable loss or safe RF
levels. Physical connection and streaming qualification remain separate work.
