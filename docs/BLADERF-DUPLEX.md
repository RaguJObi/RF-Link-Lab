# Finite conducted bladeRF TX1/RX1 controller

The adapter is implemented and tested with a fake interactive process. No board
was opened, no transmission occurred, and Ubuntu timing and RF recovery remain
unverified. A completed capture is a streaming result; decoded telemetry and
known-payload comparison provide separate RF evidence in the experiment runner.

## Host and prerequisites

Live duplex currently runs on Linux, using Nuand's `bladeRF-cli` and GNU
coreutils `stdbuf`. The Windows application can prepare and analyse offline
experiments; it does not execute this live controller. `stdbuf -oL -eL` gives
newline-delimited acknowledgements promptly under pipes. Without line buffering,
the CLI's normal buffered output could conceal an acknowledgement until much
later. This dependency is checked before a device process starts. See the
[GNU stdbuf reference](https://www.gnu.org/software/coreutils/manual/html_node/stdbuf-invocation.html).

The operator supplies a complete conducted connection plan, including the exact
32-digit bladeRF serial, checked TX1 → rated attenuation → RX1 connection,
component frequency/power ratings, and a documented TX power upper bound at the
chosen frequency and gain. No plan or unknown values means no live process.
The project working ceiling is an operating policy, not a manufacturer absolute
maximum input rating. Software cannot inspect the cable, attenuator markings,
or their physical condition. See [the connection-plan policy](CONDUCTED-PLAN.md).

## Public boundary

`DuplexSettings` is a passive dataclass. Its defaults are an empty serial,
915 MHz, 1 million complex samples/s, 3 million RX samples, 0 dB RX gain,
-20 dB overall TX gain and 200 kHz bandwidth. Empty serial is useful for an offline
experiment but is rejected for live use. Defaults never authorize transmission.

`run_finite_duplex(tx_path, output_dir, settings, *, connection_plan,
cancel_event=None, process_factory=None)` accepts a prepared single-channel
little-endian SC16Q11 waveform. `output_dir` must not exist. Input and copied
waveform samples are checked before opening the device. Values outside
[-2048, 2047], partial I/Q samples or a duration above 0.5 seconds are rejected.

The project limits short bench runs to 70 MHz–6 GHz, 1–5 million samples/s,
1–5 million RX samples, a 1–5 second RX window, -5 through 50 dB RX gain and
-23 through 66 dB overall TX gain. Bandwidth must be at least 200 kHz and no greater
than the requested sample rate. These are project bounds. Nuand's installed
driver remains the authority on supported settings; the controller requires
exact actual readback rather than assuming the request succeeded.

`set gain tx1` addresses **overall** gain. Nuand's overall TX range is
[-23.75, 66] dB; the separate `dsa` stage spans [-89.75, 0] dB. The controller
uses the integer overall subset [-23, 66], with default -20. Positive overall
gain is required for useful signals through a large attenuation chain; the
independent connection-plan power bound must cover the chosen gain. Increasing
this control does not change or bypass the project's RX working ceiling.
It does not reinterpret a DSA value as an overall gain. Neither gain scale is an
independent measurement of output power in dBm. See the
[official gain-range definitions](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/fpga_common/include/bladerf2_common.h).

## Staged lifecycle

1. Validate configuration, finite waveform and the operator connection plan.
2. Create an exclusive evidence directory and copy the waveform to fixed
   `tx.sc16q11`. Validate the plan again immediately before process launch.
3. Start one `bladeRF-cli -d '*:serial=EXACT_SERIAL' -i` process under `stdbuf`.
   Commands are passed through pipes with `shell=False`. User file names never
   enter the CLI command parser; only fixed local names are used.
4. Verify the opened serial. Record versions, disable loopback, choose 16-bit
   samples, disable RX1/TX1 bias tees and RX AGC, and configure both directions.
   Read back frequency, rational sample rate and bandwidth separately for RX1
   and TX1, and verify TX1 gain before streaming. RX1 gain is marked pending
   until the channel is enabled: the micro can return its previous RX gain
   while disabled. Any missing, differing or unparseable verified value stops
   the run.
5. Configure finite RX `n=...`, and TX `repeat=1 delay=0`. Infinite `n=0` and
   `repeat=0` are never generated. Both use 8192 samples/buffer, 16 buffers,
   8 transfers and a 1000 ms data-transfer timeout.
6. Start RX first. Wait for at least 65,536 bytes on disk, then verify RX still
   reports Running with no last error. Read back and verify the now-enabled RX1
   gain before requesting any TX. A mismatch preserves the partial RX and stops
   both tasks without a TX request. Check both elapsed wall time and observed
   file size leave the complete TX duration plus a 350 ms margin. If not, stop
   before TX. This proves host receipt of initial samples; it does not prove
   hardware synchronization or guarantee all later transmitted samples arrive.
7. Revalidate the connection plan and check cancellation immediately before the
   one finite `tx start` request. Poll 100 ms waits and explicit TX/RX status.
   Require both Idle and Last error None, then an exact RX byte count.
8. Request bounded TX/RX stops, verify final Idle/no-error status and quit.
   A stop on an already idle task may return the vendor's expected state error;
   final task status is evaluated separately. No automatic retry occurs.

Every command is followed by a unique `echo` marker. An echoed INPUT line such
as `echo RFLL_ACK_...` is not accepted as a command result. Each acknowledgement
has a deadline, and the full process has an outer 20 seconds plus capture-duration
watchdog. The application also accepts a cooperative cancellation event and
handles a CLI KeyboardInterrupt by recording cancellation and attempting cleanup.

## Cancellation, shutdown and evidence

The adapter preserves partial data and reports `complete`, `cancelled`, or
`failed`. The directory contains `tx.sc16q11`, any received `rx.sc16q11`,
`session.log`, `duplex.json` and an RX sidecar. The manifest records requested
settings separately from actual readback, hashes, command history, selected
serial, plan validation, TX-request status and cleanup status. It never marks RF
decoding successful on the strength of a process exit status.

Graceful cleanup has a separate bounded window. If status cannot be verified or
the process fails to exit, a hard kill is the final fallback. Such a run has
`cleanup_confirmed=false`; process death is not evidence that the RF hardware
was disabled. Inspect board state before another manual run. A failed transfer
can also leave the last-error field set, so cleanup is conservatively unconfirmed
even if the CLI reports Idle. An exception in the started workflow preserves a
failure manifest and raw files instead of discarding them.

## Why the lifecycle is explicit

The audit was checked against Nuand commit
[`35174c30b009cdb335800c411f3c14d45283f12b`](https://github.com/Nuand/bladeRF/tree/35174c30b009cdb335800c411f3c14d45283f12b).
Installed releases can differ. A changed CLI format fails closed and needs an
adapter update; this source review is not validation of the installed driver.

- The [CLI command help](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/doc/interactive-help.md)
  defines finite RX sample counts, TX repetitions, channel selection and waits.
- [RX startup](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/rx.c#L371-L404)
  sets the task Running after stream configuration, before channel enable and
  the receive loop. The [start command](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/rx.c#L461-L472)
  waits for that state. The extra file-growth check therefore matters.
- [Wait and stop implementation](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/rxtx.c#L722-L860)
  shows that a timed wait can return success while streaming continues, and
  stop submits an asynchronous request. Neither command alone proves completion.
- [TX task cleanup](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/tx.c#L429-L453)
  disables the selected module before stopping. The corresponding RX path does
  the same. Stopping tasks close their files; normal CLI shutdown joins them.
- [CLI signal handling and destruction](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/common.c#L38-L90)
  shows SIGINT/SIGTERM may release a wait rather than terminate immediately.
  [Normal destruction](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/common.c#L149-L170)
  requests task shutdown and closes the device.
- [bladeRF2 close](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/libraries/libbladeRF/src/board/bladerf2/bladerf2.c#L517-L568)
  deinitializes streams and puts the RFIC into a deinitialized or standby state
  on a normal driver close. A killed process may skip that normal code path.
- [Readback formatting](https://github.com/Nuand/bladeRF/blob/35174c30b009cdb335800c411f3c14d45283f12b/host/utilities/bladeRF-cli/src/cmd/printset_impl.c#L480-L504)
  and the same file's gain and rational-rate functions define the strict parsers.
- Nuand's [RX gain example](https://github.com/Nuand/bladeRF/wiki/Getting-Started:-Verifying-Basic-Device-Operation#rx-gain)
  documents that a requested gain may not appear in readback until the RX
  channel is enabled. The adapter therefore verifies RX gain after observing
  received bytes and Running state, before requesting TX.

The CLI reuses the installed Nuand tooling and keeps process failure isolated
from the GUI. A libbladeRF binding could later offer structured API return codes,
metadata timestamps and tighter synchronization. It would still need bounded
stream workers, explicit module disable/close, cancellation and bench validation;
switching interfaces would not by itself establish those properties.

## Software checks

`tests/test_bladerf_duplex.py` supplies an in-memory Popen-compatible process.
It exercises finite limits, no launch on invalid plans, staged serial/settings
readback, RX readiness, exhausted capture budgets, input echoes, waits that
return before completion, cancellation, stream errors despite exit status zero,
partial capture preservation, Ctrl-C, unexpected exceptions and unconfirmed
cleanup. A delayed-gain fake models the micro's disabled-channel behavior, and
a wrong enabled RX gain must prevent TX. Positive overall gain, up to 66, still
requires matching plan declarations and exact readback. These tests do not
execute Nuand tools or open any device.
