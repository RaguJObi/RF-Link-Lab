# Connect and run on your Ubuntu desktop

Use the **Ubuntu desktop for both boards**. The Windows laptop is optional. The Ubuntu release includes the application, its Python runtime and libraries, and a compiled NUCLEO-F446RE firmware image. You do not need to build the project or install Python packages.

**Current bench, 21 October 2026:** hardware is disconnected and Ubuntu is unavailable. Follow [Ready for hardware](READY-FOR-HARDWARE.md) to update the existing installation and resume with one fresh UART/RF run. The Nucleo was already backed up, programmed and UART-tested; do not repeat that first-time flash for daily operation.

**Validation boundary:** prior patched v0.3.1 ran on Ubuntu and produced real UART/RF evidence; the updated receiver recovers 12/12 messages from a saved physical capture. New v0.3.2 package execution remains pending. The steps below are the generic first-install reference. Physical RF operation must use the matching-stack hardware launcher or daily helper described in the current guide.

## 1. Prepare the Ubuntu desktop once

Copy `RF-Link-Lab-v0.3.2-Ubuntu22-x86_64.tar.gz` to the Ubuntu 22.04 desktop and extract it using the file manager. Keep all the extracted files together. Open a terminal inside the extracted release folder. Existing installations should use the update kit instead of repeating setup.

Run:

```bash
./SETUP-UBUNTU.sh
```

This explicit setup step needs internet and asks for your normal sudo password. It installs native graphics libraries, the ST-LINK utilities, Nuand's bladeRF host tools and xA9 FPGA image, and USB permission groups. It does not compile software, change MCU firmware, flash bladeRF FX3 firmware, or disable ModemManager.

**Sign out of Ubuntu and sign back in** after setup so your account receives the new device permissions. Reconnect USB devices after signing in. This setup is normally needed only once per Ubuntu installation. Package installation can fail if the network or upstream package server is unavailable; resolve its displayed error before proceeding.

The bundle targets **Ubuntu 22.04, x86_64**. The setup checks both rather than attempting changes on an unsupported computer. If a file manager removes executable permission during extraction, run `bash ./SETUP-UBUNTU.sh` and likewise prefix the other launcher commands with `bash`.

## 2. Start with the STM32 board

Make this connection with a USB data cable:

```text
NUCLEO-F446RE ST-LINK USB connector  ──────  Ubuntu desktop USB
```

Use the board's ST-LINK connector. No external UART adapter or wires to the bladeRF are needed for this step. The default NUCLEO USART2 route carries PA2/PA3 through ST-LINK's virtual COM interface; a board with modified solder bridges needs the routing check in [the firmware guide](../firmware/README.md).

Flash the supplied program once. Close RF Link Lab, other serial terminals, and debug programs. First list the attached ST-LINK probes:

```bash
st-info --probe
```

Find the `serial:` value belonging to your NUCLEO-F446RE, then insert that exact value in this command:

```bash
./FLASH-STM32.sh --serial YOUR_STLINK_SERIAL --board NUCLEO-F446RE
```

This explicitly replaces the selected STM32's current program. It requires the selected probe to report an STM32F446 chip ID before calling the flashing tool. It does not choose a mounted disk or touch the bladeRF. If the probe cannot see the MCU, check the cable, board power, and SWD jumpers; do not bypass the target check.

After the flashing tool reports success, start the application:

```bash
./START-RF-LINK-LAB.sh
```

Before connecting serial, open **Hardware day → 4 · Bench session**, enter a session title and choose **Start bench session** with a new folder name. This arms persistent raw-byte and packet logging. You can add notes and physical observations there during the experiment.

Then open **Hardware day → 2 · Nucleo serial**:

1. Choose **Refresh ports**.
2. Select the ST-LINK endpoint, normally `/dev/ttyACM0`, at **115200 baud**.
3. Choose **Connect selected port**.
4. Check that valid packet count, sequence number, and uptime advance.
5. Press and release the blue user button. Check that the received button field follows both actions.
6. Use **Decode these packets through software modem** to pass the CRC-verified STM32 telemetry through the software waveform and independent receiver.

Expected firmware behavior is periodic telemetry every 100 milliseconds plus each debounced button transition. The green LED normally gives a one-second heartbeat and stays lit while the user button is held. The actual decoded button field is the evidence for the embedded path.

The final software-modem step demonstrates **physical STM32 → USB serial → software radio → independent decoding**. The RF samples in that step are still generated in software. This is the first useful integration result; save it before adding another device.

If the cable is removed, the app reports the read failure and stops. Restore the cable, refresh the endpoint if its number changed, and reconnect explicitly. See [serial measurement meanings and recovery](SERIAL.md) for counter interpretation and sequence resets.

## 3. Connect the bladeRF for receive-only checks

Make the second USB connection:

```text
NUCLEO-F446RE ST-LINK USB  ──────  Ubuntu desktop USB
bladeRF 2.0 micro xA9 USB 3  ────  Ubuntu desktop USB 3
```

Use a direct USB 3 connection for the bladeRF when possible. For this initial stage no TX-to-RX RF cable is required. Tab 3's capture controls remain receive-only. The separate tab 5 contains the new explicit finite-link workflow; preparation and software rehearsal open no radio.

In **Hardware day → 1 · Setup & identity**, explicitly run the device identity check. Confirm the attached device and host tools are visible. If the FPGA is loaded, Nuand's host tools use the installed hosted xA9 image. A device identity result proves communication with the board, not a working RF link.

In **Hardware day → 3 · RX & recording replay**, choose a new output recording name and use **Record RX to a new file**. Keep the metadata file with the recording. Reopen it with **Import and decode an I/Q file**.

A real receive recording can contain noise or unrelated signals. A result of **no valid RF Link Lab packet found** is expected when no compatible telemetry transmitter is present. A spectrum plot or successful file capture alone is not evidence of packet reception. Initial real captures verify USB streaming, finite capture, file integrity, and replay; the cabled transmitter remains a later hardware milestone.

## 4. Prepare and rehearse the finite radio experiment

Open **Hardware day → 5 · Finite TX / RX**. Prepare a finite burst from the offline example or an explicit snapshot of verified serial packets, then run the software rehearsal into a new folder. This applies a labelled software channel to the saved quantized transmit samples and independently decodes the result. No radio or serial endpoint is opened by rehearsal. Inspect exact known-frame matches, missing/unexpected frames and the report before a physical trial.

For the later hardware path, record your actual SMA cables, attenuator values, frequency ranges and applicable power ratings, then establish the TX output-power upper-bound basis. The tab's connection/power plan requires those declarations and the selected bladeRF serial. They are not known yet, so no physical connection values are assumed here. Only an explicit live start on Ubuntu can request TX; **Stop** cancels the finite experiment and preserves its outcome. Each run offers one bounded burst, with no automatic repeat or retry.

See [the finite-link guide](CONDUCTED-LINK.md) for the full procedure and [the connection-plan guide](CONDUCTED-PLAN.md) for required declarations. Physical duplex needs the installed `bladeRF-cli` and `stdbuf` from GNU coreutils; the Ubuntu setup includes them. Windows can prepare, rehearse and inspect reports. A saved serial snapshot demonstrates chosen payload delivery, not continuous button-to-radio streaming or latency.

## 5. Run without either board

Disconnect the devices or leave them unused. The normal simulation and imported software recordings remain available. In the serial tab, **Exercise serial parser without a board** uses correctly encoded packets plus deliberate corruption and reordering.

You can also run the offline serial demonstration directly:

```bash
./START-RF-LINK-LAB.sh serial-demo
./START-RF-LINK-LAB.sh serial-ports
```

The first command opens no port. The second lists endpoints without opening them. The offline parser demonstration should report **6 valid packets, 1 CRC error, 1 observed sequence gap, 1 duplicate, and 1 out-of-order packet**. Its source remains explicitly offline.

## Your first bench record

Return to **4 · Bench session** after the experiment, add your observed button/connection results and choose **Finish & save report**. This disconnects serial, closes the UART log and creates a portable HTML report. Device identity queries and RX recordings made in the app while the session was active are included automatically. Previously saved I/Q can be attached explicitly. The report includes local firmware/model identities, app/OS details, raw UART, packet/CRC/sequence events and copied I/Q files. Physical observations are labelled as operator-entered. Keep the complete folder together; see [the bench guide](BENCH-SESSIONS.md).

Note each physical check as passed, failed, or not attempted. Hardware evidence begins with those actual results. The **Create offline bench example** action provides a separate software-only demonstration; it does not validate either board.

The launcher stores writable app state under `${XDG_DATA_HOME:-$HOME/.local/share}/rf-link-lab` by default. Release files and saved experiments stay separate, so extracting a future release does not require overwriting your evidence. You can set `RF_LINK_LAB_HOME` to choose another writable location.

Setup follows [Nuand's Linux installation guide](https://github.com/Nuand/bladeRF/wiki/Getting-Started%3A-Linux). The STM32 utilities are available as [Ubuntu 22.04's stlink-tools package](https://packages.ubuntu.com/jammy/electronics/stlink-tools); the explicit serial selection follows [the upstream st-flash command reference](https://github.com/stlink-org/stlink/blob/v1.7.0/doc/man/st-flash.md).
