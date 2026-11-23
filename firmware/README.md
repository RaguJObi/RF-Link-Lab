# NUCLEO-F446RE telemetry firmware

This is a complete, original, bare-metal C application for the **NUCLEO-F446RE (MB1136)**, with a reset vector, linker script and actual ARM `.elf`, `.bin` and Intel `.hex` outputs. The image has been flashed to a physical NUCLEO-F446RE and verified: a 30-second recording produced 322 CRC-valid frames with both button states (see [VALIDATION.md](../VALIDATION.md)). See [flashing and first-board checks](../docs/FLASH-STM32.md).

Connect the Nucleo's ST-LINK USB Mini-B connector to the Ubuntu desktop. A factory-configured board routes USART2 to the ST-LINK virtual COM port; no external UART wires are normally needed. The MCU supplies telemetry over USB to the host modem. It is not connected to the bladeRF expansion connector.

The application uses the internal nominal 16 MHz HSI oscillator, so it does not depend on an external crystal or ST-LINK MCO. USART2 PA2/AF7 transmits at 115200, 8 data bits, no parity, one stop bit and no flow control. PA3/AF7 is reserved for the USART2 receive route, but this firmware implements no host commands. The 17-byte protocol remains identical to `rf_link_lab.protocol`: sequence, uptime and actual debounced B1 button state, with reflected IEEE CRC32. It emits a frame at boot, every 100 ms, and after each 30 ms stable button transition. Sequence and uptime restart at zero on reset; sequence wraps modulo 2^32.

LD2 on PA5 gives a 1 Hz heartbeat, stays on while B1 is held, and blinks quickly if a UART transmission times out. A UART successful transfer means bytes left the peripheral; it does **not** prove that the host received them. Host-side CRC and sequence counters provide that evidence.

## Build from source

Use Python 3.10+ and either Arm GNU GCC in PATH or Zig 0.15.2. No STM32CubeIDE, vendor HAL, downloaded board package or firmware network access is required.

```sh
python3 -m pip install ziglang==0.15.2
python3 scripts/build_firmware.py
```

On Windows, use the project's Python environment:

```powershell
.venv\Scripts\python.exe scripts\build_firmware.py
```

The builder writes `artifacts/firmware/rf_link_lab_f446re.{elf,bin,hex,map}` and `manifest.json`. It cross-compiles the board application, compiles and runs native C golden-vector and button-debounce tests, validates ARM ELF memory ranges and reset/SysTick vectors, and records compiler/source/output hashes. Zig's C driver does not expose linker `-Map`; its `.map` is explicitly labelled as a post-link section/symbol map. GNU GCC produces its native linker map. The `objects` folder contains intermediate files, not release inputs. The script never connects to or flashes a board.

## Source boundaries

- `rf_telemetry.c/.h`: allocation-free portable serialization and CRC; also used in a native C golden-vector check.
- `rf_button.c/.h`: portable debounce logic tested for bounce, held state, release and millisecond-counter wrap.
- `stm32f446re/main.c`: board clock, GPIO, UART and SysTick, bounded transmit and the telemetry scheduler.
- `stm32f446re/startup.S`: initial stack pointer, exception vectors, `.data` copy and `.bss` clear.
- `stm32f446re/memory.ld`: 512 KiB Flash at `0x08000000` and 128 KiB system SRAM at `0x20000000`, with a minimum 4 KiB stack reserve.

Keeping protocol and input processing independent of peripheral registers makes them testable on a development computer. At this load (170 serial bytes/second plus button transitions), bounded polling is sufficient. DMA/interrupt queues, watchdog policy, board-command framing and an RTOS are later engineering extensions when requirements justify them. There is no RF transmission or AI inference on this MCU image.

## Hardware evidence

The first board session confirmed boot, a stable 115200-baud UART stream, sequence and uptime progression, and both press and release states. Recovery after unplug/reset and host rejection of deliberately damaged frames are the next checks.
