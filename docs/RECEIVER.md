# Independent I/Q receiver

The receiver can process a saved recording without regenerating its
transmission. `run_simulation()` now calls this same receiver after it creates
the channel. Frame parsing does not use the simulator's original messages or
its payload length.

## The published burst format

A burst has 96 repeated QPSK pilot symbols, a fixed 64-symbol synchronization
word, and a stream of 17-byte telemetry frames. The symbols and format name
are published in `src/rf_link_lab/waveform.py`. There is one acquisition
preamble per burst; this version acquires one burst per recording.

The modulation is Gray-coded QPSK with root-raised-cosine filtering, roll-off
0.35 and a ten-symbol pulse span. The default symbol rate is 50 ksymbols/s:
200,000 complex samples/s with four samples/symbol. The symbol rate is a
declared property of our link; it is not inferred from arbitrary RF signals.

The format does not transmit a hidden frame count. The decoder scans the
available recovered bytes for the public telemetry header and verifies CRC32
before returning each message. Partial frames and noise do not become
successful messages. `src/rf_link_lab/protocol.py` defines the telemetry wire
format shared with the firmware encoder.

## Receive a recording

```python
import numpy as np
from rf_link_lab.receiver import decode_iq

samples = np.load("recording.npy", allow_pickle=False)  # 1-D complex I/Q
result = decode_iq(samples, {
    "sample_rate": 200000,
    "samples_per_symbol": 4,
    "source": "external saved recording",
})
print(result.metrics["status"], result.telemetry)
```

The caller must provide the actual sample rate. `symbol_rate` may replace
`samples_per_symbol` when their ratio is an integer. If both are supplied,
they must agree. A sample-rate label alone cannot be checked against elapsed
wall-clock time from an uncalibrated recording; use the device's actual rate
readback and the known transmitted symbol rate.

Optional metadata includes `expected_frames` when the experiment truly knows
how many messages were sent, and `correct_cfo` to disable carrier-frequency
correction for a controlled experiment. This decoder does not receive the
injected channel CFO, timing delay, gain, noise, or phase.

Returned statuses are `decoded`, `synchronized_no_valid_frames`,
`empty_capture`, `capture_too_short`, `no_signal`, and `acquisition_failed`.
Use `acquisition_valid` and CRC-validated `packets_ok` together: detecting the
sync word is not evidence that a payload was successfully received.

## Explicit bladeRF-rate adapter

A recording at 1,000,000 samples/s and a transmitted symbol rate of 50,000
symbols/s has 20 samples/symbol. The receiver accepts that ratio directly, or
the caller can explicitly resample to 200,000 samples/s:

```python
from rf_link_lab.receiver import decode_iq, resample_iq

baseband, metadata = resample_iq(raw_capture, {
    "sample_rate": 1000000,
    "samples_per_symbol": 20,
    "symbol_rate": 50000,
    "source": "bladeRF receive capture",
}, target_sample_rate=200000)
result = decode_iq(baseband, metadata)
```

The adapter uses an anti-aliasing polyphase filter; it does not merely relabel
the original samples. Returned metadata records the capture and output rates,
sample counts, method, and up/down ratio. This test validates the software
rate conversion, not a real board's configured rate or RF behavior.

## What can and cannot be measured

Without a transmitted payload reference, payload BER and reference EVM are
unavailable: `ber`, `evm_pct`, and `reference_evm_pct` are `None`, and
`reference_available` is false. Unknown frame denominators also remain
`None`. Receiving two packets does not establish that only two were sent.

`pilot_evm_pct` is a different, explicitly named measurement against the
public sync word after acquisition and channel normalization. Pilot-derived
SNR, estimated CFO, the spectrum, and measured rail occupancy are available
when acquisition permits them. None are calibrated receiver sensitivity or
RF input power. The legacy `spectrum_dbfs` field contains Welch bin power
relative to unit complex mean power, not calibrated dBm or density per Hz.

For a software benchmark only, pass a `WaveformResult` as `reference` or a
mapping with `reference_bits` and `reference_symbols`. These arrays enter
only the final metric comparison. A shorter or deliberately incorrect
reference cannot change the decoded frame count or repair a CRC. BER/EVM
remain unavailable if acquisition fails; `benchmark_alignment_valid` records
whether a comparison was possible. Erased bits are reported separately.

The current classifier retains the payload-reference EVM feature contract. An unreferenced hardware
capture is outside that feature contract. Do not substitute pilot EVM for
payload EVM or present that classifier's result as hardware validation.

## Generate from actual telemetry

```python
from rf_link_lab.protocol import Telemetry
from rf_link_lab.waveform import generate_waveform

waveform = generate_waveform([
    Telemetry(sequence=57, uptime_ms=4812, button=True),
    Telemetry(sequence=58, uptime_ms=4912, button=False),
], sample_rate=1000000, samples_per_symbol=20)
```

This creates samples and optional benchmark references from the actual
supplied messages. It does not open a device or transmit. A later transmit
adapter must apply deliberate amplitude scaling and approved RF connections.

## Receiver assumptions and verification

Acquisition estimates CFO from the repeated pilot, matches the published sync
at every integer sample timing phase, and refines CFO against the shaped
public preamble. A bounded nearby-frequency search handles a failed local
fit. A normalized sync score below 0.6 is rejected before frame parsing.
That threshold is a conservative software gate, not a measured false-alarm
specification.

The current receiver assumes an integer samples/symbol ratio, burst sampling
without sample-clock drift, one stable complex channel coefficient, a CFO
within half the symbol rate, and our exact published format. It does not yet
include a fractional timing loop, continuous burst reacquisition, multipath
equalization, DC/IQ correction, or independent-oscillator tracking. Those
limitations matter when moving from generated samples to hardware.

Tests cover saved complex64 I/Q containing arbitrary telemetry, unknown
payload references and expected frame counts, deliberately wrong reference
data, arbitrary leading/trailing noise, truncated payloads, zero/short/tone
captures, random-noise false acquisition, inconsistent rates, native 1 Msps
decoding, and explicit 1 Msps to 200 ksps resampling. Hardware reception is
still pending a bench session.
