"""Build and inspect real STM32F446RE images; never connects to a board.

Requires Arm GNU GCC or Zig 0.15.2 (pip install ziglang==0.15.2).
ELF conversion and Intel HEX generation use the Python standard library.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FLASH_BASE, FLASH_SIZE = 0x08000000, 512 * 1024
RAM_BASE, RAM_SIZE = 0x20000000, 128 * 1024
STEM = "rf_link_lab_f446re"


def run(command: list[str], cwd: Path = ROOT, timeout: int = 240) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command!r}\n"
                           f"{result.stdout}\n{result.stderr}")
    return (result.stdout + result.stderr).strip()


def elf_flash_image(path: Path) -> tuple[bytes, dict]:
    """Extract PT_LOAD physical Flash addresses from a 32-bit ARM ELF."""
    data = path.read_bytes()
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("Expected ELF32, little endian, current ELF version")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    _, elf_type, machine, _, entry, phoff, _, _, _, phsize, phcount, *_ = header
    if machine != 40 or elf_type != 2 or phsize < 32:
        raise ValueError("Expected ARM executable with ELF32 program headers")
    chunks: list[tuple[int, bytes]] = []
    ram_end = RAM_BASE
    for index in range(phcount):
        segment = struct.unpack_from("<IIIIIIII", data, phoff + index * phsize)
        kind, offset, vaddr, paddr, filesz, memsz, _, _ = segment
        if kind != 1:
            continue
        if filesz > memsz or offset + filesz > len(data):
            raise ValueError("Invalid ELF load-segment bounds")
        if RAM_BASE <= vaddr < RAM_BASE + RAM_SIZE:
            if vaddr + memsz > RAM_BASE + RAM_SIZE:
                raise ValueError("RAM segment exceeds STM32F446RE system SRAM")
            ram_end = max(ram_end, vaddr + memsz)
        elif not FLASH_BASE <= vaddr < FLASH_BASE + FLASH_SIZE:
            raise ValueError(f"Unexpected load virtual address 0x{vaddr:08x}")
        if not filesz:
            continue
        if not FLASH_BASE <= paddr < paddr + filesz <= FLASH_BASE + FLASH_SIZE:
            raise ValueError(f"Load content outside program Flash: 0x{paddr:08x}")
        chunks.append((paddr, data[offset:offset + filesz]))
    if not chunks or min(address for address, _ in chunks) != FLASH_BASE:
        raise ValueError("Image does not start at program Flash base")
    end = max(address + len(chunk) for address, chunk in chunks)
    image = bytearray(b"\xff" * (end - FLASH_BASE))
    for address, chunk in sorted(chunks):
        image[address - FLASH_BASE:address - FLASH_BASE + len(chunk)] = chunk
    if len(image) < 452:
        raise ValueError("Missing complete STM32F446 vector table")
    stack, reset = struct.unpack_from("<II", image)
    systick, = struct.unpack_from("<I", image, 15 * 4)
    if stack != RAM_BASE + RAM_SIZE or stack % 8:
        raise ValueError("Initial stack must be aligned to top of 128 KiB SRAM")
    for name, vector in (("reset", reset), ("SysTick", systick)):
        if not vector & 1 or not FLASH_BASE <= (vector & ~1) < FLASH_BASE + len(image):
            raise ValueError(f"Invalid {name} Thumb vector 0x{vector:08x}")
    if entry != reset:
        raise ValueError("ELF entry point and reset vector differ")
    return bytes(image), {
        "flash_origin": f"0x{FLASH_BASE:08x}",
        "flash_capacity_bytes": FLASH_SIZE,
        "image_bytes": len(image),
        "ram_origin": f"0x{RAM_BASE:08x}",
        "ram_capacity_bytes": RAM_SIZE,
        "ram_segment_bytes_including_stack_reserve": ram_end - RAM_BASE,
        "initial_stack_pointer": f"0x{stack:08x}",
        "reset_vector": f"0x{reset:08x}",
        "systick_vector": f"0x{systick:08x}",
        "vector_count": 113,
    }


def intel_hex(image: bytes) -> str:
    def record(address: int, kind: int, payload: bytes) -> str:
        body = bytes((len(payload), address >> 8, address & 255, kind)) + payload
        return ":" + (body + bytes((-sum(body) & 255,))).hex().upper()
    lines: list[str] = []
    upper = None
    for offset in range(0, len(image), 16):
        address = FLASH_BASE + offset
        if address >> 16 != upper:
            upper = address >> 16
            lines.append(record(0, 4, upper.to_bytes(2, "big")))
        lines.append(record(address & 0xffff, 0, image[offset:offset + 16]))
    lines.append(record(0, 1, b""))
    return "\n".join(lines) + "\n"


def elf_section_symbol_map(path: Path) -> str:
    """Readable post-link memory map when Zig's cc driver has no -Map option."""
    data = path.read_bytes()
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    shoff, shsize, shcount, shstrings = header[6], header[11], header[12], header[13]
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shsize)
                for i in range(shcount)]
    names_header = sections[shstrings]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    def string(table: bytes, offset: int) -> str:
        return table[offset:table.find(b"\0", offset)].decode("utf-8", errors="replace")
    lines = ["RF Link Lab ELF section/symbol memory map (post-link, not linker -Map output)",
             "Address     Size       Section"]
    for section in sections:
        name, kind, flags, address, offset, size, link, _, _, entsize = section
        if flags & 2:
            lines.append(f"0x{address:08x} 0x{size:08x} {string(names, name)}")
    lines += ["", "Address     Size       Symbol"]
    symbols = []
    for section in sections:
        _, kind, _, _, offset, size, link, _, _, entsize = section
        if kind != 2 or entsize != 16:
            continue
        linked = sections[link]
        strings = data[linked[4]:linked[4] + linked[5]]
        for where in range(offset, offset + size, entsize):
            name, value, length, info, _, index = struct.unpack_from("<IIIBBH", data, where)
            if name and index and info & 15 != 4:
                symbols.append((value, length, string(strings, name)))
    for value, length, name in sorted(symbols):
        lines.append(f"0x{value:08x} 0x{length:08x} {name}")
    return "\n".join(lines) + "\n"


def toolchain(args: argparse.Namespace) -> tuple[list[str], str]:
    gcc = args.gcc or shutil.which("arm-none-eabi-gcc")
    if gcc and not args.zig:
        return [gcc], "gcc"
    zig = args.zig or os.environ.get("ZIG") or shutil.which("zig")
    if zig:
        return [zig], "zig"
    if importlib.util.find_spec("ziglang"):
        return [sys.executable, "-m", "ziglang"], "zig"
    raise RuntimeError("Install arm-none-eabi-gcc, or pip install ziglang==0.15.2; "
                       "then rerun. --zig /path/to/zig also works.")


def build(args: argparse.Namespace) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    objects_dir = output / "objects"
    objects_dir.mkdir(exist_ok=True)
    compiler, kind = toolchain(args)
    version = run(compiler + (["version"] if kind == "zig" else ["--version"]))
    cc = compiler + (["cc"] if kind == "zig" else [])
    target = (["-target", "thumb-freestanding-eabi", "-mcpu=cortex_m4"] if kind == "zig"
              else ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft"])
    common = target + ["-Os", "-g", "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                       "-ffunction-sections", "-fdata-sections", "-fno-unwind-tables",
                       "-fno-asynchronous-unwind-tables", "-I", str(ROOT / "firmware")]
    sources = [ROOT / "firmware" / "stm32f446re" / "startup.S",
               ROOT / "firmware" / "stm32f446re" / "main.c",
               ROOT / "firmware" / "rf_button.c", ROOT / "firmware" / "rf_telemetry.c"]
    objects: list[Path] = []
    commands = []
    for source in sources:
        obj = objects_dir / (source.stem + ".o")
        command = cc + common
        if source.suffix == ".c":
            command += ["-std=c99", "-Wall", "-Wextra", "-Werror", "-Wconversion",
                        "-Wshadow", "-Wstrict-prototypes"]
        command += ["-c", str(source), "-o", str(obj)]
        run(command)
        commands.append(command)
        objects.append(obj)
    elf = output / (STEM + ".elf")
    map_path = output / (STEM + ".map")
    command = cc + target + ["-nostdlib", "-Wl,--gc-sections", "-Wl,--build-id=none",
                              "-Wl,-e,Reset_Handler",
                              "-Wl,-T," + str(ROOT / "firmware" / "stm32f446re" / "memory.ld")]
    if kind == "gcc":
        command += ["-Wl,-Map=" + str(map_path)]
    command += [str(obj) for obj in objects] + ["-o", str(elf)]
    run(command)
    commands.append(command)
    if kind == "zig":
        map_path.write_text(elf_section_symbol_map(elf), encoding="utf-8", newline="\n")
    image, inspection = elf_flash_image(elf)
    binary = output / (STEM + ".bin")
    hex_path = output / (STEM + ".hex")
    binary.write_bytes(image)
    hex_path.write_text(intel_hex(image), encoding="ascii", newline="\n")
    host_results = []
    if not args.skip_host_tests:
        host_cc = cc if kind == "zig" else [shutil.which("cc") or "cc"]
        for test, module in (("test_codec", "rf_telemetry"), ("test_button", "rf_button")):
            executable = objects_dir / (test + (".exe" if os.name == "nt" else ""))
            cmd = host_cc + ["-O2", "-std=c99", "-Wall", "-Wextra", "-Werror",
                             str(ROOT / "firmware" / (module + ".c")),
                             str(ROOT / "firmware" / (test + ".c")), "-o", str(executable)]
            run(cmd)
            commands.append(cmd)
            host_results.append(run([str(executable)]))
    source_paths = sources + [ROOT / "firmware" / "stm32f446re" / "memory.ld",
                             ROOT / "firmware" / "rf_telemetry.h",
                             ROOT / "firmware" / "rf_button.h", Path(__file__).resolve()]
    manifest = {
        "schema_version": 1, "project_version": "0.2.0", "board": "NUCLEO-F446RE (MB1136)",
        "mcu": "STM32F446RE", "status": "BUILT_NOT_HARDWARE_TESTED",
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "build_host": platform.platform(), "compiler_kind": kind, "compiler_version": version,
        "cpu_clock": "Internal factory-trimmed HSI, nominal 16 MHz; no HSE/PLL dependency",
        "uart": {"peripheral": "USART2", "tx": "PA2 / AF7", "rx_reserved": "PA3 / AF7",
                 "baud": 115200, "data_bits": 8, "parity": "none", "stop_bits": 1,
                 "flow_control": "none", "brr": 139, "nominal_actual_baud": 16000000 / 139,
                 "host_commands": False},
        "telemetry": {"frame_bytes": 17, "period_ms": 100, "additional_frames": "debounced button changes",
                      "sequence_at_reset": 0, "uptime_at_reset_ms": 0},
        "button": {"pin": "PC13", "active": "low", "debounce_ms": 30},
        "led": {"pin": "PA5", "normal": "1 Hz heartbeat", "button_held": "solid",
                "uart_timeout": "fast blink until next successful transmission"},
        "image_inspection": inspection, "host_test_results": host_results,
        "host_tests_skipped": args.skip_host_tests,
        "artifacts": {p.name: {"bytes": p.stat().st_size,
                               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in (elf, binary, hex_path, map_path)},
        "source_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"):
                           hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "build_commands": commands,
        "limitations": ["No hardware attached during build; boot, UART levels, LED and button require bench verification.",
                        "HSI oscillator tolerance affects UART baud and millisecond timing.",
                        "This image emits binary telemetry only and does not control an RF transmitter."]
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "firmware")
    parser.add_argument("--zig", help="Zig executable, optional when python ziglang package is installed")
    parser.add_argument("--gcc", help="arm-none-eabi-gcc executable")
    parser.add_argument("--skip-host-tests", action="store_true",
                        help="Cross-build only; manifest explicitly records missing host tests")
    args = parser.parse_args()
    try:
        manifest = build(args)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Built {STEM}: {manifest['image_inspection']['image_bytes']} bytes of Flash image")
    for result in manifest["host_test_results"]:
        print(result)
    print("ELF/vector/RAM inspection passed; status: BUILT_NOT_HARDWARE_TESTED")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
