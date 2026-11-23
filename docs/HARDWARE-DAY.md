# First hardware session

**Current access, 21 October 2026: equipment is disconnected and Ubuntu is unavailable.** Previous sessions verified Nucleo flash/UART and finite bladeRF captures on Ubuntu 22.04.5 x86_64. The reviewed chain uses BW-S30W20+ and BW-S40W20+ pads plus two 50-ohm loads. The matching radio stack is CLI 1.10 / libbladeRF 2.6 / FPGA 0.16 in RAM with firmware 2.6.

Start with [Ready for hardware](READY-FOR-HARDWARE.md) for the v0.3.2 existing-project update and daily command. Saved physical I/Q reprocessing recovers 12/12 exact messages; new-package execution and a fresh whole-path run remain pending. The procedure below retains general bring-up and evidence-collection guidance.

## 1. Establish identity before streaming

On the Ubuntu machine with the board attached through a suitable USB 3 connection, record the host OS release, USB connection, board label/revision, cable, and installed software versions. Use Nuand's [getting-started guide](https://github.com/Nuand/bladeRF/wiki/Getting-Started) for installation and matching firmware/FPGA guidance rather than assuming a package is current.

From this project run:

```text
python -m rf_link_lab probe
```

The underlying read-only queries are:

```text
bladeRF-cli -p
bladeRF-cli -e info -e version
```

Save their full output in a new session folder. Verify model, serial number, FPGA-loaded state, version compatibility, and USB speed. A detected serial number proves enumeration, not reception. If no device appears, establish permissions, cable/USB state, and whether another application owns it before changing firmware.

## 2. Make one short receive-only recording

The first receive-only connection will be selected after inspecting the available 50-ohm terminator/antenna and attenuation. Keep external signal levels within the documented input limits. Use the separate RX tab; do not move to the explicit finite-TX tab until the serial, RX and physical-plan checks are complete.

This explicit Python example records 0.1 seconds at 2 million complex samples/s; select the actual observation frequency and RX gain for the inspected setup before running it:

```python
from pathlib import Path
from rf_link_lab.hardware import capture_rx

metadata = capture_rx(
    Path("artifacts/hardware/session-001/rx.sc16q11"),
    frequency_hz=100_000_000,  # example receive tuning only
    sample_rate=2_000_000,
    samples=200_000,
    gain_db=10,
)
print(metadata["settings_readback_and_log"])
```

The adapter follows Nuand's `rx config → rx start → rx wait` sequence and accepts only the expected number of bytes. Requested values can differ from driver readback; inspect the actual output. [CLI recording reference](https://github.com/Nuand/bladeRF/wiki/bladeRF-CLI-Tips-and-Tricks#receiving-samples).

For SC16Q11, 200,000 complex samples occupy 800,000 raw bytes. Use a new filename for each run; the adapter rejects overwriting. The returned duration is nominal duration from the requested rate, not a timing calibration.

## 3. Replay the exact recording

```python
from pathlib import Path
from rf_link_lab.hardware import load_sc16q11

iq = load_sc16q11(Path("artifacts/hardware/session-001/rx.sc16q11"))
print(iq.shape, iq.dtype)
```

This verifies file interpretation and permits waveform/spectrum inspection. Preserve the JSON sidecar so the frequency axis can use the actual sampling context. Do not feed arbitrary ambient RF to the reference QPSK receiver and interpret its output as validated packet recovery. A spectrum peak does not identify its emitter or prove calibration.

Repeat with a changed RX gain and compare clipping/sample magnitude with the original. Record exactly one controlled change. Save disconnect/stalled-transfer behavior as well as the successful path. Keep the status "RX streaming validated" separate from "reference modem decoded."

## 4. Bring up the STM32 separately

Check the actual board revision, debugger, USB connector, and solder-bridge routing against ST's [UM1724 manual](https://www.st.com/resource/en/user_manual/um1724-stm32-nucleo64-boards-mb1136-stmicroelectronics.pdf), especially USART communication and board configuration. Do not infer routing solely from the board family name.

The complete [firmware](../firmware/README.md) includes a compiled `rf_link_lab_f446re.bin` with startup, clock setup, USART2 telemetry, button debounce and LED behavior. It has passed build and image checks; it has not booted on a physical board here. Follow [Flash the STM32](FLASH-STM32.md) to identify and explicitly program the exact NUCLEO-F446RE. No CubeIDE project generation or target compilation is required. Use **Hardware day → Nucleo serial** to observe real sequence, uptime and button messages. Record button transitions, CRC counts and reconnect/reset behavior. The offline parser demonstration separately exercises damaged-frame recovery without claiming physical UART evidence.

Only then connect serial telemetry to the modem's payload input. The NUCLEO is the data source; it does not drive the bladeRF FPGA pins or directly generate the RF waveform in this design.

## 5. Plan the conducted RF test after RX and UART evidence

Record each attenuator's dB value, operating frequency range, power rating, connector type, cable losses, and any DC/bias-tee considerations. Calculate expected RX power from a supported TX setting and compare it with the receiver limit and the attenuators' ratings. There is no universal attenuation value assumed here. Nuand requires appropriate loading and attenuation between TX and RX. [Connection notes](https://github.com/Nuand/bladeRF/wiki/Getting-Started:-Verifying-Basic-Device-Operation#important-notes).

The v0.3 finite TX/RX feature is implemented and mock-tested, with hardware qualification still pending. First use **Hardware day → 5 · Finite TX / RX** to prepare one burst and run the offline rehearsal. Its known-payload report is software evidence. Then review a plan using the selected bladeRF serial and actual component/power information before an explicit live start on Ubuntu. Unknown attenuation or ratings leave the physical plan incomplete. [Finite-link procedure](CONDUCTED-LINK.md) · [Connection/power plan](CONDUCTED-PLAN.md).

The live controller records RX before requesting one finite TX burst and retains settings, readbacks, logs, samples, exact-payload outcomes and cleanup status. Use **Stop** to request cancellation; unconfirmed cleanup must remain recorded as unconfirmed. A file with the expected number of bytes proves capture completion, not decoded telemetry. A complete reference result requires exact expected-frame matches. Snapshot serial payloads do not measure continuous streaming or button-to-radio latency.

A same-board cabled link can validate a useful portion of the signal path, but shared clocks and limited propagation conditions differ from two independent radios. `rfic_bist` is digital loopback and has a narrower evidence boundary. Keep these experiments labelled separately. The existing synthetic AI classifier is disabled for the new known-payload results pending separate evaluation.

## Session closeout

Save the configuration/readback, serial/firmware/FPGA identity, physical connection photo, raw samples, hashes, plots, packet logs, test outcome, and the next unresolved measurement. Publish only captures you may share and avoid committing serial numbers unintentionally. Mark each completed check only after its real output exists.
