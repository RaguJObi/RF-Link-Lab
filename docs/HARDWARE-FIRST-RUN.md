# First physical hardware session

We are building a measured embedded-to-radio telemetry link:

```text
NUCLEO-F446RE -- ST-LINK USB --> Ubuntu -- USB 3 --> bladeRF xA9
                                                   TX1
                                                    |
                                          reviewed attenuator chain
                                                    |
                                                   RX1
                                                    |
                                    decoded telemetry + saved report
```

The Nucleo sends its sequence number, uptime and actual button state. Ubuntu
records those messages, prepares a finite QPSK radio burst from up to 12 verified
messages, and compares the real received payload with the transmitted payload.
Physical UART, finite TX/RX transport and complete decoded recovery from a saved
real capture are now recorded. The next goal is a fresh whole-path run of the
new package with that receiver. Preserve the
recordings and failures so each result can be reproduced. AI evaluation follows
collection of labelled hardware measurements.

This version sends a saved snapshot, not a continuous button-to-radio stream.
Both RF channels share one bladeRF board. We will use the physical hardware
commands below and skip software loopbacks, rehearsal and synthetic tests.

Current availability, 21 October 2026: the hardware is disconnected and Ubuntu
is unavailable. Development, packaging and documentation continue locally.
No SSH, new UART acquisition or RF operation is attempted while access is absent.
Use [Ready for hardware](READY-FOR-HARDWARE.md) for the current update kit and
reconnection procedure when the desktop returns.

Historical physical session, 20 October 2026: the project and patched v0.3.1 runtime ran on
Ubuntu 22.04.5 x86_64 at `/home/rfll/SDR/bladeRF/RF-Link-Lab`. The Nucleo's full
512 KiB previous flash was backed up, the supplied image written/verified, and
a 30-second 115200-baud UART recording preserved **322 packets / 5,474 bytes**,
both button states and zero recorded CRC/framing/sequence/stream errors.

The initial system stack (CLI 1.8 / libbladeRF 2.4.1 / SPI FPGA 0.14 with firmware
2.6) identified the board but produced held-sample corruption. The current
private stack is **CLI 1.10 / libbladeRF 2.6 / FPGA 0.16 in volatile RAM**, using
the existing firmware 2.6 and SuperSpeed USB. The matching FPGA is loaded on
explicit hardware launch; neither FX3 firmware nor persistent FPGA storage is
flashed. The earlier failed/incomplete captures remain preserved.

Matched-stack attempt 3 recovered 5/12 CRC-valid frames before its received
waveform ended. Attempt 4 used a 32768-sample TX buffer and retained the full
waveform. Its **original receiver result** was 4/12 frames, 150 errors / 1632
compared bits and reference EVM about 60.7%. A separately saved, blind
decision-directed type-II QPSK phase tracker now reprocesses that real capture
to **12/12 exact frames, 0/1632 compared bit errors**. Reference-free decoding
recovers the same telemetry. The coarse CFO estimate remains about +8.546 Hz;
the loop tracks residual phase. Reference EVM is about **35.03%**, a normalized
receiver metric rather than a calibrated RF-quality qualification. Reprocessing
opened no radio and did not transmit again.

The [new receiver report](../artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4-carrier-tracking-reprocess/receiver/report.html)
and [reprocessing record](../artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4-carrier-tracking-reprocess/reprocessing.json) distinguish the unchanged original from the
corrected saved-I/Q result. **Ubuntu v0.4.0 is prepared and packaged locally;
its target execution and a fresh daily UART-to-RF run remain pending.** The user confirmed the complete attenuated
chain, both unused-port loads and no antenna/external amplifier. All completed
finite transport attempts have confirmed cleanup.

Sections 1–6 below retain the original transfer/setup/measurement procedure.
The old v0.3.1 first-install procedure is retained for history; use the
[prepared update guide](READY-FOR-HARDWARE.md) for this already installed Ubuntu
project. Daily operation uses the helper next; it does not repeat STM32 flashing.
Original physical evidence is at
`/home/rfll/SDR/bladeRF/RF-Link-Lab/artifacts/bench/physical-20261020-121721-P68MXp`;
the local console copy is `build/hardware-start-20261020-064706/ubuntu-console.txt`.
The imported UART and backup hashes, all 322 raw frame CRCs and saved fields
were independently checked on Windows. The [UART-only report](../artifacts/bench/physical-uart-20261020-report/report.html)
contains copied UART evidence and the saved identity probe; its check entries
are Codex reviews of saved logs, not direct physical observations by the report
generator. It includes no I/Q or RF performance claim.
The RF originals are preserved in their separate imported attempt directories;
attempt 4 is `artifacts/bench/physical-rf-20261020-buffer32768-imported/rf-run-4`.
LED appearance, native GUI, latency and real-hardware AI remain
unverified. Source-host and Ubuntu clock readings differ by about 5 h 30 min;
cross-host UTC alignment has not been qualified.

## Daily run after installing the current Ubuntu package

When Ubuntu and the boards are available, inspect and reconnect the confirmed
RF chain and both USB data cables as described below. First install the complete
v0.4.0 application into `runtime-ubuntu` using the
[existing-project update guide](READY-FOR-HARDWARE.md), then run this in a normal
Ubuntu terminal:

```bash
PROJECT=/home/rfll/SDR/bladeRF/RF-Link-Lab
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v0.4.0-Ubuntu22-x86_64"
PLAN="$PROJECT/artifacts/bench/physical-20261020-121721-P68MXp/connection-plan.json"
SESSION="$PROJECT/artifacts/bench/connected-$(date +%Y%m%d-%H%M%S)-$$"
bash "$APP/RUN-CONNECTED-BENCH.sh" \
  --serial YOUR_BLADERF_SERIAL \
  --port /dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_YOUR_STLINK_ID-if02 \
  --plan "$PLAN" --output "$SESSION"
```

The source-checkout equivalent uses
`bash "$PROJECT/deployment/hardware/RUN-CONNECTED-BENCH.sh"` with the same options.
The helper records **10 seconds of fresh UART**, verifies the retained serial
bytes, prepares up to 12 valid messages, selects the pinned host stack, loads
the xA9 FPGA into RAM and sends **one finite burst**. Press/release B1 during the
UART interval. It does not reflash the Nucleo or persistent radio storage.

The new directory contains UART/tx evidence, the copied plan, phase/error status
and `$SESSION/rf/report.html`. Inspect `known_payload_complete`, matched/missing
frames, actual compared-bit count and `cleanup_confirmed`; a successful process
or full-size I/Q file alone does not establish packet recovery. The daily helper
and newly packaged receiver still require their first combined target run.
Keep the new package's corresponding-source companion and license notices when
redistributing it.

## 1. Establish SSH connectivity

The numbered sections retain the original v0.3.1 session procedure and its
observed identifiers. Use the current daily helper above for repeat operation.

The supplied destination is `rfll@ubuntu-bench.local`, hostname
`ubuntu-bench`. SSH initially timed out, then became reachable after
the user enabled the service on 20 October 2026. This laptop has no SSH login
key, so login needs the Ubuntu password entered directly in an interactive
terminal. No password should be posted in chat or stored in a script. If
connectivity changes, confirm Ubuntu's address in its local terminal:

```bash
hostname
ip -br address
ip route
```

If SSH is not installed or running, enable it on Ubuntu:

```bash
sudo apt-get update
sudo apt-get install openssh-server
sudo systemctl enable --now ssh
systemctl status ssh --no-pager
```

In Windows PowerShell, test the confirmed address:

```powershell
Test-NetConnection ubuntu-bench.local -Port 22
ssh rfll@ubuntu-bench.local
```

Use the actual Ubuntu address in subsequent commands if it differs. Check the
SSH host fingerprint against the desktop before accepting a new host identity.
Enter any requested login password directly in your terminal; do not post it in
chat. An SSH timeout must be resolved before SCP can transfer the project.

## 2. Copy and install the complete project kit

The transfer kit contains a source archive, Git history bundle, the Ubuntu
portable application, checksums and an installer. This avoids transferring
Windows dependencies and build caches as if they were Ubuntu software.

The kit's `TRANSFER-TO-UBUNTU.ps1` performs the SSH check, SCP transfer,
installation and read-only host/device inspection. It requests login through
OpenSSH's password prompt. It never programs either board or starts RF TX.
Before running the full system setup, inspect those observations: existing
Nuand tools may already be installed and should not be replaced unnecessarily.

In Windows PowerShell, once SSH works:

```powershell
ssh rfll@ubuntu-bench.local "mkdir -p /home/rfll/SDR/bladeRF"
scp -r "C:\path\to\RF-Link-Lab\release\transfer-v0.3.1-20261020" "rfll@ubuntu-bench.local:/home/rfll/SDR/bladeRF/RF-Link-Lab-transfer-v0.3.1-20261020"
```

The destination transfer folder should be new. If a previous transfer is
present, inspect it before repeating the copy so folders are not nested or
silently mixed. In an Ubuntu terminal:

```bash
KIT="$HOME/SDR/bladeRF/RF-Link-Lab-transfer-v0.3.1-20261020"
bash "$KIT/INSTALL-RF-LINK-LAB.sh"

PROJECT="$HOME/SDR/bladeRF/RF-Link-Lab"
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v0.3.1-Ubuntu22-x86_64"
```

The installer verifies its inputs and installs into the new `RF-Link-Lab`
directory. It refuses an existing project directory. It does not replace the
surrounding bladeRF vendor checkout, flash a board or start RF transmission.
Project source and history are retained inside `PROJECT`; the portable runtime
and ready-to-use launchers are inside `APP`.

## 3. Install the missing tools and connect USB

The physical-bench helper completed the backup, verified flash and UART
observation described above. It starts no RF operation. Running it again repeats
programming after a fresh backup; use the current status above to resume
diagnosis without repeating setup or programming.
For a deliberate new backup/program/UART attempt, run this from an interactive
PowerShell terminal in the Windows project:

```powershell
& 'C:\path\to\RF-Link-Lab\deployment\hardware\RUN-PHYSICAL-BENCH.ps1'
```

The helper installs only missing ST-LINK tools, refreshes serial access,
requires exactly one identified F446 target, saves and checks a 512 KiB flash
backup, then programs the telemetry image and records UART. It prepares a
payload from the recorded packets and starts no RF capture or transmission.
Enter SSH/sudo passwords only at terminal prompts and press/release B1 when
prompted. Each attempt preserves a unique evidence directory under
`/home/rfll/SDR/bladeRF/RF-Link-Lab/artifacts/bench/physical-*`; inspect its status,
counters and button states before claiming a hardware pass. On Ubuntu, the
already-started attempt keeps the exact executed helper as `procedure.sh` in
the printed session directory. To start a new attempt locally as `rfll`, set
`SESSION` to that observed directory and run:

```bash
bash "$SESSION/procedure.sh"
```

The updated source also provides the Linux helper at the fixed project path
below. Copy that updated file into the installed Ubuntu project before using
this command; the initial transfer predates this helper. It runs locally as
`rfll` without the Windows laptop:

```bash
bash /home/rfll/SDR/bladeRF/RF-Link-Lab/deployment/hardware/START-PHYSICAL-BENCH.sh
```

The remaining steps document the same operations individually.

The initial host inspection already found Nuand's `bladeRF-cli` and `stdbuf`.
Install the missing ST-LINK tools and give the normal desktop user serial access:

```bash
sudo apt-get update
sudo apt-get install -y stlink-tools
sudo usermod -a -G dialout rfll
sudo udevadm control --reload-rules
```

Sign out of Ubuntu and sign back in, or start a fresh SSH login, to load the
group change. Reconnect the boards after installing their access rules. Preserve
the existing Nuand installation; the full `SETUP-UBUNTU.sh` remains available if
subsequent observations establish a missing dependency. Run the application as
the normal user. Re-establish the variables in your new terminal:

```bash
PROJECT="$HOME/SDR/bladeRF/RF-Link-Lab"
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v0.3.1-Ubuntu22-x86_64"
SESSION="$PROJECT/artifacts/bench/physical-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SESSION"

lsusb
lsusb -t
```

Connect the Nucleo's **ST-LINK Mini-B USB** socket using a data cable. Connect
bladeRF's **USB 3** socket directly to an Ubuntu USB 3 port using its data cable.
The boards need no direct digital wires between them. Confirm the bladeRF's USB
connection in the observed USB tree. Inspect the RF connectors before any RX
operation; an initial RX input can use a suitable 50-ohm termination or an
appropriate receive antenna after checking its connections and environment.
Keep TX unconnected to an antenna and do not connect TX directly to RX.

The user's corrected attenuator markings are 30 dB, 20 W, DC-18 GHz
(`2211 MCL BW S30W20+`, model `BW-S30W20+`) and 40 dB, 20 W, DC-18 GHz
(`2205 MCL BW S40W20+ 20W`, model `BW-S40W20+`). SMA cables are available.
The earlier 20/50 dB values are superseded. With bladeRF powered off, the
intended conducted chain is TX1 -> SMA cable -> 30 dB pad -> 40 dB pad ->
SMA cable -> RX1. Join pads directly when their connectors mate, otherwise
use an extra SMA cable. Follow any IN/OUT markings. Nominal total attenuation
is 70 dB. The user confirmed two separate 50-ohm terminators; fit one on TX2 and one
on RX2. Both pad models and this fitted chain/port loading are now user-confirmed,
with no antenna or external amplifier. Connect both boards by USB to Ubuntu;
no GPIO/UART jumper wires run between the boards. The saved plan is a set of
operator assertions, including an unmeasured power planning envelope.

## 4. Program and measure the real Nucleo

This step is complete for the current board. Its previous flash backup SHA256 is
`529758c1050b6191383de61f585e317faf784bc7b24c3546cfd8d2d5a97ca86d`;
the programmed image SHA256 is
`636d701ff7140d075b9d69d265504bf9cf352a393bb4b4b8d8e8a8bb92e0cdf2`.
Observed ST-LINK serial: `YOUR_STLINK_ID`, chip ID `0x0421`.
The stable UART endpoint was
`/dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_YOUR_STLINK_ID-if02`.
The commands below document programming/re-recording; skip reprogramming when
continuing the existing session. For another manual flash attempt, create a new
`SESSION` directory first so the original pre-project flash backup is preserved.

Close serial terminals and debugging programs, then identify the actual probe:

```bash
st-info --probe
```

Confirm the observed target is the F446 (chip ID `0x421` / `0x0421`), and set
the probe serial from that output. If multiple probes are present, select the
intended Nucleo explicitly. Save its existing 512 KiB flash before replacing
the STM32 application:

```bash
STLINK_SERIAL='REPLACE_WITH_THE_OBSERVED_STLINK_SERIAL'
st-flash --serial "$STLINK_SERIAL" read \
  "$SESSION/previous-stm32-flash.bin" 0x08000000 0x80000
test "$(stat -c '%s' "$SESSION/previous-stm32-flash.bin")" -eq 524288
sha256sum "$SESSION/previous-stm32-flash.bin" \
  > "$SESSION/previous-stm32-flash.bin.sha256"
```

Continue only after a successful complete backup. A failed/protected read needs
inspection; do not bypass protection or mass-erase to create the backup. Then
program the selected board using the image- and target-checking launcher:

```bash
bash "$APP/FLASH-STM32.sh" --serial "$STLINK_SERIAL" --board NUCLEO-F446RE
bash "$APP/START-RF-LINK-LAB.sh" serial-ports
ls -l /dev/serial/by-id/
```

Select the observed ST-LINK virtual COM endpoint, preferably its stable
`/dev/serial/by-id/...` path. Do not assume that `/dev/ttyACM0` belongs to this
board when other serial devices are connected:

```bash
UART_PORT='/dev/serial/by-id/REPLACE_WITH_THE_OBSERVED_STLINK_ENDPOINT'
bash "$APP/START-RF-LINK-LAB.sh" serial-record \
  --port "$UART_PORT" --baudrate 115200 --duration 30 \
  --output "$SESSION/uart"

cat "$SESSION/uart/uart.json"
tail -n 20 "$SESSION/uart/packets.jsonl"
```

During the recording, press and release the blue user button several times.
Expected telemetry is approximately one frame every 100 ms, plus debounced
button transitions. Confirm `summary.packets_ok` advances, sequence and uptime
advance, and `button` records both states. Inspect CRC errors, sequence gaps and
stream errors rather than treating a successful command exit as packet proof.
The green LED should show a heartbeat and remain on while the button is held;
decoded button fields provide the host-side evidence. Keep raw bytes and all
sidecars together.

## 5. Identify bladeRF and record a finite real RX file

This step completed. The RX-only recording contains 1,000,000 complex samples /
4,000,000 bytes with a matching sidecar/hash and no logged RX error. Requested
gain 0 dB was not verified after active RX; pre-stream readback was stale at
60 dB. Bandwidth was logged at 18 MHz, not selected. This earlier recording
does not establish either setting or decoded payload success.

To inspect the existing session in a normal Ubuntu terminal, set:

```bash
PROJECT=/home/rfll/SDR/bladeRF/RF-Link-Lab
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v0.3.1-Ubuntu22-x86_64"
SESSION="$PROJECT/artifacts/bench/physical-20261020-121721-P68MXp"
lsusb
lsusb -t
```

The saved identity is available, with SuperSpeed USB and FPGA loaded. The
following command is available for a fresh identity check; it opens no stream:

```bash
bash "$APP/START-RF-LINK-LAB.sh" probe | tee "$SESSION/bladerf-probe-before-rx.json"
```

Inspect `available`, `devices`, `info` and `error` in the JSON. The current probe
command can exit successfully while reporting `available: false`; that result
must be fixed before continuing. Record the actual bladeRF serial, firmware and
FPGA versions. Do not update FX3 firmware merely because this is the first run.
The newer-firmware compatibility-table warning is recorded. Keep the existing
stack for the first bounded RX check; inspect any actual streaming error before
deciding whether a software update is needed.

For a deliberate later RX-only measurement, choose a new filename and capture
one second of real RX1 samples at 915 MHz. Keep the completed original:

```bash
bash "$APP/START-RF-LINK-LAB.sh" capture-rx \
  "$SESSION/rx-smoke-next.sc16q11" \
  --frequency 915000000 --sample-rate 1000000 --samples 1000000 --gain 0

stat -c '%s bytes' "$SESSION/rx-smoke-next.sc16q11"
cat "$SESSION/rx-smoke-next.sc16q11.json"
```

The file must contain **4,000,000 bytes** and have its metadata/hash sidecar.
This step proves a bounded USB/RX recording when it succeeds. Noise or unrelated
received energy is not proof of RF Link Lab packet reception.

## 6. Prepare real telemetry, then complete the physical RF plan

The current session already contains the prepared 12-frame physical UART
payload at `$SESSION/tx`; reuse it for the first RF trial. For a different UART
recording, prepare into a new output directory using:

```bash
bash "$APP/START-RF-LINK-LAB.sh" link-prepare \
  --uart-recording "$SESSION/uart" --output "$SESSION/tx-new"
```

This uses the last up to 12 verified physical UART messages and opens no radio.
The fitted chain and unused-port loads are user-confirmed, and the saved plan
selects the actual bladeRF serial. The first completed transport used overall
TX gain 50 and RX gain 40 at 915 MHz through the 70 dB nominal chain. Its
calculation uses 67.5 dB minimum loss and an explicitly unmeasured +30 dBm
engineering planning envelope, not calibrated output or a guaranteed vendor
maximum. TX gain in dB does not establish output power in dBm.

After the plan and actual connections have been reviewed, save the completed
plan as `$SESSION/connection-plan.json`, using the schema in
[CONDUCTED-PLAN.md](CONDUCTED-PLAN.md). After diagnosis and a targeted correction,
the command for a reviewed later trial must use a new output directory:

```bash
bash "$APP/START-RF-LINK-LAB.sh" link-live "$SESSION/tx" \
  --plan "$SESSION/connection-plan.json" --rx-gain 40 --output "$SESSION/rf-run-next"
```

It requests one finite TX1 burst while RX1 records, compares the received
payload, and saves its transport log, raw capture, receiver measurements and
`report.html`. Missing/incomplete plans are rejected. Use a new output folder
for every attempt. Ctrl+C requests cancellation and cleanup; inspect the saved
cleanup status before another physical attempt.

## Optional native display on the Ubuntu desktop

Run this in a terminal **on the Ubuntu graphical desktop**, not an ordinary
headless SSH terminal:

```bash
APP=/home/rfll/SDR/bladeRF/RF-Link-Lab/runtime-ubuntu/RF-Link-Lab-v0.4.0-Ubuntu22-x86_64
bash "$APP/START-HARDWARE.sh" --serial YOUR_BLADERF_SERIAL
```

Use **Hardware day** for real identity, serial and finite TX/RX controls. Choose
**Current physical Nucleo serial packets** when preparing in the GUI. Close CLI
serial recordings before opening the same endpoint in the GUI. The existing AI
model has not been qualified on this hardware link and remains disabled for
these conducted measurements.

The portable launcher changes its working directory to writable app state.
All commands above therefore use absolute paths. Ubuntu CLI execution is now
confirmed by the saved host preflight, and the finite physical UART/button-state
observation is preserved. Matched-stack physical streaming and full saved-I/Q
recovery are recorded separately. The fresh new-package daily run, native GUI,
visual LED behavior and timing qualification still require their own results.

## Review a saved measurement and prepare hardware AI

In the source app or v0.4.0 Ubuntu package, open **Hardware day → 6 · Evidence & AI**. Select a finalized physical experiment; optionally select a later receiver result for the same raw capture. Save a review, curate explicit operator labels and check training readiness. This does not start RF or use the synthetic classifier. Follow [Hardware evidence & AI](HARDWARE-AI.md) for exact commands, measurement definitions and dataset requirements.
