# Live STM32 telemetry

The serial receiver is a persistent, receive-only connection. Select the NUCLEO's USB virtual COM port, connect at **115200 baud, 8 data bits, no parity, 1 stop bit**, and the application checks telemetry packets continuously. The app does not flash a board or send commands over its UART.

The board must run this project's F446RE firmware. A board running the original demonstration firmware, another program, or a text-printing UART example will not produce valid RF Link Lab telemetry. A serial port being listed is evidence of a USB endpoint, not evidence of the correct firmware or a working radio link.

## Physical setup

Use the NUCLEO's **ST-LINK USB connector** and a USB cable that carries data. The firmware and board routing instructions are in [the firmware guide](../firmware/README.md); follow the instructions for the NUCLEO-F446RE board revision. The ST-LINK virtual COM interface carries USART2 telemetry to the computer.

For this initial embedded check, the bladeRF is not needed. The demonstration's telemetry fields are a sequence number, uptime in milliseconds, and the actual button state. Firmware sends one packet every 100 milliseconds and another after each button transition passes its 30-millisecond debounce interval. Sequence and timing therefore remain observable while the button is released.

On Ubuntu, the endpoint normally resembles `/dev/ttyACM0`. A second attached USB serial device can change that number. On Windows it normally resembles `COM5`. Refresh the port list after connecting; an entry labelled ST-LINK or STMicroelectronics is a suggestion that still requires selecting the correct device.

If Ubuntu reports permission denied, add your account to the serial-device group once, then sign out of the desktop session and sign back in:

```bash
sudo usermod -a -G dialout "$USER"
```

Close other serial monitors before connecting. Only one program should read the endpoint. If a cable is removed, the application records the read error and stops; reconnect explicitly after fixing the connection. There is no unattended reconnect loop.

## What the measurements mean

- **Valid packets:** packets with the correct header, supported version, legal payload values, and matching CRC32. This includes valid duplicates and out-of-order packets.
- **CRC errors:** complete candidates whose CRC does not match. A damaged candidate is rejected; the parser searches onward for the next valid packet.
- **Framing errors:** candidates with the telemetry magic but an unsupported header or illegal payload values. Ordinary unrelated bytes contribute to discarded bytes, rather than generating one framing error per byte.
- **Sequence gaps:** missing sequence numbers inferred when a new forward sequence arrives. This is a cumulative count of observed jumps; a delayed packet does not erase an earlier gap.
- **Duplicates:** sequences already seen within the last 1,024 distinct sequence numbers by default. Older repeats can be classified as out of order because the memory is deliberately bounded.
- **Out of order:** a previously unseen sequence behind the latest forward sequence. Sequence comparison handles uint32 wrap from 4,294,967,295 to zero.
- **Bytes received:** all bytes read, including rejected and incomplete data. More bytes alone do not prove valid telemetry.

Counters and retained packet history survive disconnect and explicit reconnect. The partial packet buffer and sequence baseline reset so fragments from two sessions cannot be combined. The first valid packet after reconnect establishes a new baseline. Use the explicit reset action when you want a fresh set of totals. A board reset while the port remains connected can appear as out-of-order telemetry; disconnect/reconnect to establish the new sequence baseline.

History retains the most recent 100 valid packets by default. Packet entries carry UTC receipt time, their input source, and a session identifier that advances on every explicit connection attempt. Receipt time describes the host, and uptime describes the MCU; neither is a precision measurement of radio latency. Use the source and session identifier to distinguish current board packets from retained demonstrations or earlier connections.

## Exercise the same parser without the board

The offline serial demonstration creates real protocol frames, splits them across arbitrary reads, corrupts one CRC, repeats one packet, and delivers a packet late. Its result should show **6 valid packets, 1 CRC error, 1 observed sequence gap, 1 duplicate, and 1 out-of-order packet**. It is explicitly labelled offline input and does not open hardware.

From the installed source environment:

```bash
python -m rf_link_lab serial-demo
```

Live serial input and offline demonstration bytes cannot be mixed in the same active connection. Stop the live connection before using the demonstration. Validated recent frames can then be used as the input to the software radio simulation; that demonstrates STM32-to-software integration, while transmission over the bladeRF RF path remains a separate bench milestone.

## Python integration

```python
from rf_link_lab.serial_link import SerialTelemetrySession, list_ports

print(list_ports())  # Lists endpoint metadata; opens nothing.
session = SerialTelemetrySession()
try:
    session.start("/dev/ttyACM0", baudrate=115200)
    # A GUI timer can poll this without blocking the USB receiver.
    print(session.snapshot())
    # Keep the application alive while the independent reader runs.
finally:
    session.stop()
```

`start()` and `stop()` are explicit. Port open failures raise `SerialLinkError` and leave actionable `last_error` evidence in `snapshot()`. The background reader performs bounded reads. `stop()` cancels reading, closes the endpoint, and waits up to one second by default for the worker. A driver that ignores timeout/cancellation produces a visible error and prevents a second reader from starting. `recent_frames()` returns only CRC-verified retained telemetry, encoded in the canonical 17-byte wire format. For current hardware evidence use `recent_frames(current_session_only=True, source="serial")`; it excludes packets retained from earlier boards or offline demonstrations.

Current validation uses injected serial endpoints and real protocol bytes. Live firmware, ST-LINK routing, USB driver behavior, and board button behavior require the first physical bench check.

## Persistent recordings and faithful replay

A recording saves the bytes actually received, including noise, corrupted CRCs,
unsupported headers and incomplete packets. This is separate from the bounded
recent-packet list shown by the GUI. Every valid packet is written to disk even
after it has scrolled out of that list.

Start a recording **while disconnected**, using a new output directory. Starting
clears earlier totals, retained packets and partial parser bytes, and establishes
a new source/session boundary. Then connect the selected endpoint explicitly.
Disconnecting, a cable/read error or reconnecting keeps the recording open; each
transition records a parser boundary. Finalize it explicitly when the experiment
ends. Statistics cannot be reset while a recording remains open.

```python
from pathlib import Path
from rf_link_lab.serial_link import SerialTelemetrySession
from rf_link_lab.serial_logging import (
    verify_uart_recording, replay_uart_recording,
)

session = SerialTelemetrySession()
session.start_recording(Path("runs/uart-button-check"))  # Must not exist.
try:
    session.start("/dev/ttyACM0")
    # Keep the application alive; press/release the actual board button.
    # A GUI timer may inspect session.snapshot()["recording"].
finally:
    try:
        session.stop()
    finally:
        manifest = session.stop_recording()

verification = verify_uart_recording("runs/uart-button-check")
result = replay_uart_recording("runs/uart-button-check/uart.json")
print(verification["complete"], result["matches"])
```

The recording directory contains:

- `raw.bin`: exact received bytes, in receipt order.
- `chunks.jsonl`: byte offsets/lengths, UTC receipt timestamps, elapsed monotonic
  time, source/session identity and lifecycle/parser boundaries. No bytes across
  a disconnect or source change are joined into a packet during replay.
- `packets.jsonl`: every CRC-verified telemetry packet, including duplicates and
  out-of-order packets, receipt time, sequence classification and provenance.
- `uart.json`: schema `rf-link-lab-uart-v1`, parser configuration, limits,
  source-segment totals, final counters, file sizes and SHA-256 hashes.

`stream_error_count` and `stream_errors` record port-open, read and stop failures
separately from the recorder's `last_error`. A complete byte log may therefore
contain a failed connection or cable removal; completion does not mean the
device worked. The manifest retains the latest 100 stream-error summaries and
sets `stream_errors_truncated` when older errors are omitted from that summary;
the chunk log retains every error within the configured event limit.
`connection_attempt_count` and `connection_attempts` likewise retain the latest
100 selected port/baud-rate attempts, host timestamps and session identities.
`connection_attempts_truncated` marks omitted older summaries. These are labelled
attempts because an endpoint selection may fail to open; it does not establish a
successful board connection. The chunk log preserves the full attempt history.

The recorder defaults to **64 MiB of raw bytes** and **1,000,000 total JSONL
events** across the chunk and packet logs. Both are explicit bounded limits;
`start_recording(..., max_raw_bytes=..., max_events=...)` can lower them for an
experiment. Accepted maximums are 1 GiB raw and 1,000,000 events. A limit or disk
write failure sets a visible recording error and produces an **incomplete**
manifest when finalization is possible. The receiver continues counting input,
so received totals may exceed saved bytes. The application never silently calls
that a successful capture. If the filesystem cannot even save the final
manifest, the in-memory error remains visible; a leftover `active` manifest
does not declare completion.

`stop_recording()` returns the final manifest plus `output_dir`, or `None` when
there is no open recording. `snapshot()["recording"]` is `None` before the first
recording; afterward it reports `output_dir`, `status`, bytes/packets/events
written and `last_error`. Status is `active`, `error`, `complete` or `incomplete`.
A disconnected UART and an active recording are independent states. Finalized
recording information stays visible after capture ends.

Verification rejects changed hashes, missing files, unsafe file references,
noncontiguous chunk ranges and invalid source boundaries. SHA-256 establishes
file consistency, not device authentication: someone able to replace the whole
recording could also replace its manifest. UTC timestamps are host observations;
elapsed time uses the host's monotonic clock and is not RF latency evidence.

Replay opens no port and uses the same incremental parser, duplicate window and
sequence rules as live reception. It checks every saved packet independently of
the small GUI history, then compares all counters and source-segment totals.
Its result contains `manifest`, `recorded_summary`, `replayed_summary`,
`verification`, `packets_match` and `matches`. `matches` is true only for a
complete recording with matching packets and counters. Recorded `serial` source
labels remain historical receipt provenance; the replay action is explicitly
`offline_replay`. Neither UART capture nor replay proves a bladeRF radio link.

For a hardware-free exercise, start a recording and call
`demo_serial_session(session=session)` before finalizing. The files are labelled
`offline_feed`, and replay should recover the existing demonstration's six valid
packets and its CRC/sequence errors without opening a USB endpoint.
