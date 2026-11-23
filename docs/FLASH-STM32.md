# Flash the NUCLEO-F446RE and verify telemetry

The supplied `rf_link_lab_f446re.bin` is an actual Cortex-M4 Flash image. It has passed compilation, native protocol/debounce checks and binary/vector inspection. **No physical NUCLEO-F446RE was available during the build: first-board verification remains required.**

## Connections

1. Check that the board label says **NUCLEO-F446RE**, with MCU **STM32F446RE**. This image is not for other Nucleo models.
2. Keep the two CN2 jumpers installed to connect the on-board ST-LINK debugger to the target STM32. Use the factory USB power configuration (JP5 U5V, JP6/IDD installed); leave BOOT0 in its normal low setting. Disconnect external shields while doing the first check.
3. Connect a **USB data cable** from the board's **CN1 Mini-B connector on the ST-LINK end** to the Ubuntu desktop. This cable supplies power, programming and the virtual serial connection.
4. There are no required jumper wires between STM32 and bladeRF. Each board connects to the desktop by its own USB cable.

On the factory board, SB13/SB14 connect target PA2/PA3 to the ST-LINK virtual COM bridge, and SB62/SB63 are open. If your board was previously modified or the ST-LINK section was detached, inspect its routing against its board revision before changing solder bridges. The image uses HSI internally, so no MCO/crystal bridge change is required. These routes and the flashing interface are described in ST's [UM1724, sections 7.4, 7.5 and 7.10](https://www.st.com/resource/en/user_manual/um1724-stm32-nucleo64-boards-mb1136-stmicroelectronics.pdf).

## Flash once using the USB drive

This is the easiest method if the ST-LINK mass-storage interface is enabled.

1. Open the board's removable drive in the file manager. Identify it by its board information (`DETAILS.TXT`/board name); do not select another removable drive.
2. Copy **only** `rf_link_lab_f446re.bin` from the release's `firmware` folder into that drive. In a source checkout the file is in `artifacts/firmware`.
3. Wait for the programmer activity to finish. The ST-LINK interface programs the MCU; this drive is not ordinary persistent storage, so the copied file need not remain visible.
4. Inspect for `FAIL.TXT` or another error file. A file-copy completion alone is not verification of the application. Press the black RESET button once if needed.
5. LD2 (green, near Arduino D13/PA5) should blink at 1 Hz; holding blue B1 should make it stay on. Confirm decoded telemetry as described below.

Flashing replaces the target's existing application. ST-LINK firmware is separate and must not be replaced with this image. If the USB drive is unavailable, use the explicit Ubuntu flashing launcher shipped with the release or ST's [STM32CubeProgrammer](https://www.st.com/en/development-tools/stm32cubeprog.html), select the correct ST-LINK device and STM32F446RE target, and program the `.bin` at **`0x08000000`** with verification enabled. The `.hex` already contains its addresses. This repository never flashes automatically.

## Confirm the serial stream

Close ordinary serial terminals that may already own the board port. Start the app with `./START-RF-LINK-LAB.sh`, open **Hardware day → Nucleo serial**, refresh ports, select the ST-LINK virtual COM device (usually `/dev/ttyACM0` on Ubuntu), and connect at **115200 / 8N1 / no flow control**.

Do not expect readable text from a terminal: this is a binary packet stream. The host parser should show:

- A sequence number increasing continuously, normally about 10 frames/second plus button events.
- Uptime increasing in milliseconds, restarting after reset.
- Button false when blue B1 is released and true after it has been held for at least 30 ms. Release also produces an event.
- Zero CRC errors during an undisturbed test. Count any observed failures rather than treating this expectation as a result.

Start a recording in **Hardware day → 4 · Bench session** before connecting serial. Press and release B1 five times and record your observations there. The log retains exact UART bytes and every CRC-valid packet beyond the serial page's bounded recent history. Unplug once, explicitly disconnect/reconnect in the app, and confirm a new serial segment in the same bench recording. Press RESET while monitoring and record the observed sequence/uptime restart and classifications; disconnect/reconnect afterward to establish a fresh sequence baseline. Finish the bench session to save its report, packet events and raw UART capture. Use **Exercise serial parser without a board** after disconnecting for a separately labelled damaged-frame recovery demonstration. A software-modem export remains separate from both raw UART and physical RF evidence; see [bench recording and replay](BENCH-SESSIONS.md).

If no port appears, confirm the USB cable carries data and check the ST-LINK driver/USB enumeration. If access is denied on Ubuntu, use the release's one-time setup instructions and sign out/in after joining `dialout`. If a port exists but no valid frames arrive, check the selected port/baud, reflash the exact board image, confirm LD2 behavior and then inspect SB13/SB14. Never connect a 5 V TTL or RS-232 adapter to MCU pins. External UART hardware is unnecessary for the default configuration.

## Firmware behavior and limits

The MCU uses its internal factory-trimmed nominal 16 MHz HSI clock. USART2's integer divider is 139, giving about 115107.9 baud at the ideal HSI frequency (−0.08% divider error). Actual baud and millisecond timing also include oscillator tolerance; UART behavior must be checked on the board. ST's [STM32F446RE datasheet](https://www.st.com/resource/en/datasheet/stm32f446re.pdf) specifies the device memory and oscillator.

The blue B1 switch on PC13 is active low: the MB1136 schematic provides a pull-up and the switch pulls the input toward ground. LD2 on PA5 is active high. See ST's [MB1136 C.4 schematic, MCU page](https://www.st.com/resource/en/schematic_pack/mb1136-default-c04_schematic.pdf). Register addresses and bit fields were checked against ST's [STM32F446 device definitions](https://github.com/STMicroelectronics/cmsis-device-f4/blob/master/Include/stm32f446xx.h); their operation is documented in the [RM0390 reference manual](https://www.st.com/resource/en/reference_manual/rm0390-stm32f446xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf). The startup vector positions were checked against ST's [STM32F446 startup template](https://github.com/STMicroelectronics/cmsis-device-f4/blob/master/Source/Templates/gcc/startup_stm32f446xx.s); the project does not copy or require that template.

Periodic transmission uses a bounded, checked UART send. Bytes are CRC-protected but unauthenticated. Firmware does not process host commands. Nothing in this image transmits RF: the Linux modem and bladeRF form a separate data path.

## Rebuild and verify files

See [firmware source/build instructions](../firmware/README.md). `artifacts/firmware/manifest.json` records the source and output SHA-256 values, compiler, Flash/RAM sizes, vector addresses and status `BUILT_NOT_HARDWARE_TESTED`. Compare the release image's SHA-256 to its supplied manifest before flashing. Save actual board results separately; do not edit build metadata into a claim of hardware testing.
