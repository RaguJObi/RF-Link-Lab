# Persistent bench evidence

A bench session preserves what happened during one experiment: received UART
bytes and parser results, stored I/Q, device probe output, the computer
environment, local firmware/model identities, and operator notes. Its portable
HTML report puts these records together without turning software demonstrations
into hardware claims.

This is useful at work when a fault must be reproduced after the instrument has
been disconnected or the recording has moved to another computer. Raw UART
preserves malformed packets as well as successful telemetry. Sequence gaps,
duplicates, out-of-order packets, framing errors and CRC errors remain visible.
Those records can later support a diagnostic dataset; they do not label the
physical cause of a fault automatically.

## What the report proves

`finalized` means that the session has a report and a SHA256 inventory. It does
not mean that the experiment succeeded. Hardware checks initially say
`NOT_ATTEMPTED`. A check changed by the operator is explicitly labelled
`operator_entered`; a submitted probe result remains separate from those checks.

Offline UART packets have the `offline_feed` source label. A serial reader uses
`serial`. These are source declarations, not authenticated device identities.
Receiving a CRC-valid packet alone does not prove a Nucleo's model, flashed
firmware identity or physical button operation. Firmware/model hashes in the
environment section identify resources available to the application, not which
firmware was flashed or which model produced every imported measurement.

Synthetic I/Q remains synthetic. A raw bladeRF sidecar may declare `hardware_rx`;
the bench report preserves that declaration, requested settings and CLI log
without authenticating the source or claiming calibrated RF performance. Adding
I/Q copies existing evidence; it does not transmit, regenerate a waveform or
rerun the receiver. Recorded receiver metrics remain recorded metrics.

## Python interface

```python
from pathlib import Path
from rf_link_lab.bench import BenchSession, verify_bench_session
from rf_link_lab.serial_link import SerialTelemetrySession

bench = BenchSession.create(
    Path("sessions/first-uart-session"),
    title="First Nucleo UART experiment",
    notes="Describe the physical connections and expected button behaviour.",
    environment={"operator": "Your name", "connection": "ST-LINK USB to Ubuntu"},
)

uart = SerialTelemetrySession()
# Start recording before connecting so the complete parser stream is preserved.
uart.start_recording(bench.folder / "uart")
# Explicit physical-device action, performed only when that port is confirmed:
# uart.start("/dev/ttyACM0")
# Perform the experiment, then disconnect and stop the recording:
# uart.stop()
uart.stop_recording()
bench.add_uart_recording(bench.folder / "uart")

# These are explicit operator assertions, not automatic hardware qualification.
bench.record_check("uart_button_response", "not_attempted", "Board not yet connected")
bench.add_note("No physical button result recorded in this example.")
# Optional existing evidence; neither call opens hardware:
# bench.record_probe(previous_probe_result)
# bench.add_iq_recording(Path("previous-capture/run.json"))
# bench.add_iq_recording(Path("previous-capture/receive.sc16q11"))

manifest_path = bench.finalize()
verification = verify_bench_session(manifest_path)
print(verification["status"], manifest_path.parent / "report.html")
```

Use a new session directory every time. Creation refuses an existing directory;
input evidence is never overwritten. Files already within the session are
registered in place. External UART/I/Q files are copied into a unique directory
under `evidence/`, so the original files can later be moved or removed. Keep the
complete session directory together when sharing it.

An active UART recording cannot be attached. Stop it first. A finalized UART log
with `complete: false` can be attached only when its preserved file hashes verify;
the report labels it `INCOMPLETE` and makes no successful-acquisition claim.
This preserves troubleshooting evidence after a recording failure. If its
manifest or hashes are invalid, attachment fails; retain the files and record
the failure as an operator note instead of presenting them as verified evidence.
UART stream failures such as a failed port open, read error or close error are
reported separately from the recording writer's `last_error`. A file-complete
recording can contain a failed device connection and zero packets; file
completeness never changes a hardware check into `passed`.

## Offline example

```python
from pathlib import Path
from rf_link_lab.bench import demo_bench_session, verify_bench_session

path = demo_bench_session(Path("sessions/offline-bench-example"))
result = verify_bench_session(path)
```

The example uses the same UART recorder/parser as the hardware path. It records
fragmented bytes containing six valid packets, one damaged CRC, a forward
sequence gap, a duplicate and an out-of-order arrival. It also saves a short
synthetic QPSK receiver experiment. No serial port, device probe, RF capture or
RF transmitter is opened. Every default hardware check remains `NOT_ATTEMPTED`.

## On-disk contract and verification

`bench.json` uses schema `rf-link-lab-bench-v1`. It records session/version/UTC
timestamps, environment, local identities, operator checks/notes, submitted
probes, evidence records and an `assets` mapping. Each asset uses a relative
forward-slash path and stores `size_bytes` plus `sha256`. Source declarations
are explicitly unauthenticated.

`events.jsonl` is append-only during a session. Each operation writes a UTC event
and flushes it to disk. The evolving `bench.json` says `in_progress` until final
verification succeeds. `finalize()` rechecks attached assets, adds the final
event, creates `report.html`, and hashes the report/event log as well as the
recordings. A finalized session rejects further edits. Repeating `finalize()`
verifies its existing output rather than rewriting it.

UART evidence contains `uart.json`, `raw.bin`, `chunks.jsonl`, `packets.jsonl`
and the recorder's declared parser configuration and statistics. I/Q evidence
contains either `run.json` plus `capture.npz`, or a raw SC16Q11 file plus its
adjacent `.json` hash sidecar. The attachment layer never deserializes NPZ
objects. Raw files must contain a whole, nonempty number of four-byte I/Q
samples, and any sidecar `file`/`bytes` fields must agree with the recording.

`verify_bench_session(path)` accepts a directory or its `bench.json`, checks
finalization, relative path safety, regular-file/link safety, byte counts and
hashes, and rechecks the underlying UART/I/Q metadata. It raises `ValueError`
for unsafe, changed, incomplete-session or unsupported evidence. It returns
`status: verified`, an asset count, evidence count and the manifest on success.
It does not open devices, transmit, replay the receiver or authenticate origin.
It also compares the readable report with the manifest content, so an accidental
later edit to a title, note or check cannot silently diverge from the report.

Absolute paths, traversal paths, symlinks and junctions are rejected for linked
evidence. Operator and imported text is escaped in the HTML report, and file
links are encoded. Hashes detect changes relative to the inventory; the JSON
manifest is not signed and deliberate coordinated rewriting is outside that
integrity guarantee. Keep the original session or an independently stored hash
when stronger chain-of-custody evidence is required.

The v0.3 finite link tab saves an experiment report for every started rehearsal
or physical attempt. If a bench session is active, its finalized experiment and
copied TX/raw RX/receiver/log assets are attached automatically. An experiment
records transport status separately from exact known-payload recovery. Cancelled
and failed attempts keep partial files; neither becomes a passed RF observation.
See [the conducted-link workflow](CONDUCTED-LINK.md). Finish the active link
worker before finishing the parent bench session.
