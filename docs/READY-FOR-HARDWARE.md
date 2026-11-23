# Ready for the next hardware session

Status on **21 October 2026**: development continues locally. The hardware is
disconnected and Ubuntu is unavailable. Package checks and saved-capture
decoding do not establish a fresh hardware run.

## Verified results and package

- Nucleo backup, programming and write verification completed. The 30-second
  UART recording contains 322 valid messages, both button states and no
  recorded CRC, framing, sequence or stream errors.
- Matched Nuand CLI 1.10 / libbladeRF 2.6 and FPGA 0.16 in RAM eliminated the
  earlier held-sample corruption. Existing FX3 firmware remains 2.6.
- The updated, reference-independent QPSK tracker reprocesses a real capture
  to **12/12 exact messages, zero errors across 1,632 compared bits**.
  Blind decoding agrees. Original 4/12 evidence remains unchanged.
- Reference EVM is about 35.03%; calibrated modulation-quality qualification
  remains open. This reprocessing made no new RF transmission.

The **v0.3.2 Ubuntu 22.04 x86_64** package includes Linux Python/dependencies,
private matched radio tools, xA9 FPGA image, licenses and three hardware
helpers. A corresponding Nuand source archive accompanies it. Local checks
passed: 350 tests, 114 subtests, one platform-dependent skip.
**The new package has not yet executed on Ubuntu.**

## Copy and update when Ubuntu is available

Open **release/transfer-v0.3.2-20261021/** on this laptop and run
**UPDATE-UBUNTU.cmd**. Its destination is the existing desktop
**rfll@ubuntu-bench.local**, project **/home/rfll/SDR/bladeRF/RF-Link-Lab**.
Enter passwords only at terminal prompts. Edit the destination settings in
the command file if they change.

The updater verifies checksums and source revision, requires a clean Git
checkout and a fast-forward source update, and preserves older runtimes and
all evidence. It refuses to replace an existing v0.3.2 runtime. It starts no
RF, flashes no board and installs no system packages.

Alternatively, run in a Windows terminal:

~~~powershell
scp -r "C:\path\to\RF-Link-Lab\release\transfer-v0.3.2-20261021" rfll@ubuntu-bench.local:/home/rfll/SDR/bladeRF/RF-Link-Lab-update-v0.3.2-20261021
ssh rfll@ubuntu-bench.local "bash /home/rfll/SDR/bladeRF/RF-Link-Lab-update-v0.3.2-20261021/UPDATE-RF-LINK-LAB.sh --project /home/rfll/SDR/bladeRF/RF-Link-Lab"
~~~

Use a new staging name if that remote folder already exists. The included
INSTALL-RF-LINK-LAB.sh is for a **new** project folder and deliberately refuses
to replace this installation. The optional Windows executable remains v0.3.0.

## Reconnect, then make one physical measurement

With the boards powered down, refit the previously reviewed chain:

~~~text
bladeRF TX1 → SMA → BW-S30W20+ (30 dB) → BW-S40W20+ (40 dB) → SMA → RX1
bladeRF TX2 → separate 50 Ω terminator
bladeRF RX2 → separate 50 Ω terminator
Nucleo CN1 ST-LINK Mini-B USB → Ubuntu USB
bladeRF USB 3 → Ubuntu USB 3
~~~

No GPIO/UART jumper wires connect the boards. Use no antenna or external
amplifier for this test. The saved plan describes the **previously confirmed**
chain/settings; check that the refitted connections and loads still match
before using it. Changed equipment/settings require a new
[plan review](CONDUCTED-PLAN.md).

Run on Ubuntu:

~~~bash
PROJECT=/home/rfll/SDR/bladeRF/RF-Link-Lab
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v0.3.2-Ubuntu22-x86_64"
PLAN="$PROJECT/artifacts/bench/physical-20261020-121721-P68MXp/connection-plan.json"
SESSION="$PROJECT/artifacts/bench/connected-$(date +%Y%m%d-%H%M%S)-$$"
bash "$APP/RUN-CONNECTED-BENCH.sh" \
  --serial YOUR_BLADERF_SERIAL \
  --port /dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_YOUR_STLINK_ID-if02 \
  --plan "$PLAN" --output "$SESSION"
~~~

Press/release B1 during the 10-second UART recording to record new button
transitions. The helper verifies real UART bytes, prepares the last up to 12
valid messages, loads the matching FPGA into **RAM**, and requests one finite
RF burst. It never reflashes the Nucleo or FX3. A bladeRF power cycle restores
the older SPI image, so the RAM load occurs on explicit hardware launch.

Read **$SESSION/connected-session.json** and open **$SESSION/rf/report.html**.
Accept a run only with real verified UART packets, complete matching payload,
no missing/unexpected messages, all offered bits compared, and confirmed
cleanup. Record the measured bit-error count and actual hardware readbacks.
Failed runs also preserve their files; inspect cleanup/errors before retrying.
Use a new session folder each time.

This sends a recorded UART snapshot through one board's TX/RX cable path.
Continuous telemetry, independent radios and end-to-end latency remain
separate development steps.

## AI and the next development checkpoint

1. Execute this new package and daily helper on Ubuntu; preserve fresh
   whole-path evidence.
2. Check repeatability across separate power cycles/sessions.
3. Collect operator-labelled hardware cases: healthy, truncated waveform,
   acquisition failure, phase/frequency drift, low SNR and clipping. Group
   every reprocessing of the same raw-capture hash together to prevent
   training/test leakage.
4. Compare a hardware model with fixed diagnostic rules on held-out sessions,
   retaining an unknown outcome. The current synthetic classifier remains
   disabled for these hardware-link results.

No test faults or RF experiments run automatically. Each transmission remains
an explicit finite run under its reviewed connection/power plan.
