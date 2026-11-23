#!/usr/bin/env bash
# Explicit board programming only; the normal app launcher never invokes this.
set -Eeuo pipefail
usage() {
    printf '%s\n' \
        'Usage: bash FLASH-STM32.sh --serial STLINK_SERIAL --board NUCLEO-F446RE' \
        'Find the intended ST-LINK serial with: st-info --probe' \
        'This explicitly replaces the selected STM32 application with RF Link Lab firmware.' \
        'It never flashes bladeRF firmware and never chooses a mounted disk.'
}
if [[ "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi
selected_serial=''
selected_board=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --serial|--board)
            if [[ $# -lt 2 || "$2" == --* ]]; then usage >&2; exit 2; fi
            if [[ "$1" == --serial ]]; then selected_serial="$2"; else selected_board="$2"; fi
            shift 2
            ;;
        *) usage >&2; exit 2 ;;
    esac
done
if [[ "$selected_board" != NUCLEO-F446RE || ! "$selected_serial" =~ ^[[:xdigit:]]{12,64}$ ]]; then
    usage >&2
    exit 2
fi
bundle_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
firmware_image="$bundle_root/firmware/rf_link_lab_f446re.bin"
if [[ ! -s "$firmware_image" ]]; then
    printf 'Firmware binary is missing: %s\nExtract the complete Ubuntu bundle.\n' "$firmware_image" >&2
    exit 3
fi
if ! command -v st-info >/dev/null || ! command -v st-flash >/dev/null; then
    printf '%s\n' 'ST-LINK tools are missing. Run SETUP-UBUNTU.sh first.' >&2
    exit 3
fi
printf '%s\n' 'Close RF Link Lab and other serial/debug tools before programming.'
probe_output=$(st-info --probe)
printf '%s\n' "$probe_output"
# Require a selected probe and F446 target. Chip ID 0x421 identifies STM32F446.
chip_id=$(printf '%s\n' "$probe_output" | awk -v serial="$selected_serial" '
    $1 == "serial:" { selected = (tolower($2) == tolower(serial)) }
    selected && $1 == "chipid:" { print tolower($2); exit }
')
if [[ "$chip_id" != 0x0421 && "$chip_id" != 0x421 ]]; then
    printf '%s\n' \
        'The selected ST-LINK was not found with an STM32F446 target (chip ID 0x421).' \
        'No programming command was run. Check the board model, USB cable, serial, and SWD jumpers.' >&2
    exit 4
fi
printf 'Programming selected %s through ST-LINK %s.\n' "$selected_board" "$selected_serial"
st-flash --serial "$selected_serial" --reset write "$firmware_image" 0x08000000
printf '%s\n' \
    'The flashing tool reported success. This is not yet a live telemetry validation.' \
    'Reconnect the ST-LINK USB cable if needed, then launch the app and connect at 115200 baud.' \
    'Verify sequence/uptime and the button state before recording the board as tested.'
