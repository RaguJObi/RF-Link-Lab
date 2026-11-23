#!/usr/bin/env bash
# Targeted Ubuntu preparation, firmware backup, explicit F446 programming and UART.
# This procedure never starts bladeRF TX or RX and never updates bladeRF firmware.
set -Eeuo pipefail
umask 077
PROJECT=/home/rfll/SDR/bladeRF/RF-Link-Lab
VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PROJECT/PROJECT_CONTEXT.json")
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid project version.' >&2; exit 2; }
APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v${VERSION}-Ubuntu22-x86_64"
[[ $(id -un) == rfll && $EUID -ne 0 && $(hostname) == ubuntu-bench ]] || {
    echo 'Run as rfll on ubuntu-bench, without sudo.' >&2; exit 2;
}
[[ -x "$APP/runtime/python/bin/python3.12" && -s "$APP/firmware/rf_link_lab_f446re.bin" ]]
phase=starting
write_status() {
    python3 - "$SESSION" "$phase" "${1:-0}" <<'PY'
import datetime, json, pathlib, sys
path, phase, code = sys.argv[1:]
result = dict(schema='rf-link-lab-physical-start-v1', session=path, phase=phase,
              exit_code=int(code), updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              rf_started=False)
pathlib.Path(path, 'status.json').write_text(json.dumps(result, indent=2)+'\n')
PY
}
mark() { phase=$1; write_status; printf '\nPHASE=%s\n' "$phase"; }
if [[ ${1:-} == --measure ]]; then
    SESSION=${2:?Missing session}
    [[ "$SESSION" == "$PROJECT"/artifacts/bench/physical-* && -d "$SESSION" ]]
else
    [[ $# -eq 0 ]] || { echo 'No arguments, or internal --measure SESSION only.' >&2; exit 2; }
    mkdir -p "$PROJECT/artifacts/bench"
    SESSION=$(mktemp -d "$PROJECT/artifacts/bench/physical-$(date +%Y%m%d-%H%M%S)-XXXXXX")
    cp -- "$0" "$SESSION/procedure.sh"
    exec > >(tee -a "$SESSION/console.log") 2>&1
    printf 'SESSION=%s\n' "$SESSION"
    trap 'code=$?; write_status "$code"' EXIT
    mark preparing_tools
    echo 'Enter your Ubuntu sudo password only at its terminal prompt.'
    # A newline keeps the prompt visible through the Windows line-based logger.
    sudo -v -p $'[sudo] Ubuntu password for %u (type here):\n'
    if ! command -v st-info >/dev/null || ! command -v st-flash >/dev/null; then
        sudo apt-get update
        sudo apt-get install -y stlink-tools
    fi
    if ! id -nG rfll | tr ' ' '\n' | grep -qx dialout; then
        sudo usermod -a -G dialout rfll
    fi
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=usb --action=add
    echo 'Starting the measurement procedure with refreshed normal-user groups.'
    exec sudo -u rfll bash "$SESSION/procedure.sh" --measure "$SESSION"
fi
trap 'code=$?; write_status "$code"; printf "\nSESSION=%s\nEXIT_CODE=%s\n" "$SESSION" "$code"' EXIT
mark inspecting_usb
id
date --iso-8601=seconds
lsusb
lsusb -t
echo 'Keep Nucleo ST-LINK CN1 and bladeRF connected to Ubuntu using USB data cables.'
echo 'Waiting up to 60 seconds for one identifiable STM32F446 target.'
for attempt in {1..12}; do
    st-info --probe > "$SESSION/stlink-probe.txt" 2>&1 || true
    python3 - "$SESSION" <<'PY'
import pathlib, re, sys
p=pathlib.Path(sys.argv[1]); text=(p/'stlink-probe.txt').read_text()
parts=re.split(r'(?m)^\s*serial:\s*', text)[1:]
matches=[]
for part in parts:
    serial=part.split()[0]
    chip=re.search(r'(?m)^\s*chipid:\s*(0x[0-9a-fA-F]+)', part)
    if re.fullmatch(r'[0-9a-fA-F]{12,64}', serial) and chip and int(chip[1],16)==0x421:
        matches.append(serial)
(p/'selected-stlink.txt').write_text(matches[0]+'\n' if len(matches)==1 else '')
if len(matches)>1:
    print('Multiple F446 targets: automatic selection refused.'); sys.exit(3)
PY
    [[ -s "$SESSION/selected-stlink.txt" ]] && break
    sleep 5
done
cat "$SESSION/stlink-probe.txt"
bash "$APP/START-RF-LINK-LAB.sh" serial-ports | tee "$SESSION/serial-ports.json"
bash "$APP/START-RF-LINK-LAB.sh" probe | tee "$SESSION/bladerf-probe.json"
if [[ ! -s "$SESSION/selected-stlink.txt" ]]; then
    mark waiting_for_nucleo
    echo 'No single F446 target identified. No board was programmed. Inspect USB cable and CN1.' >&2
    exit 10
fi
STLINK_SERIAL=$(cat "$SESSION/selected-stlink.txt")
mark backing_up_stm32
echo "Reading the existing 512 KiB F446 flash through ST-LINK $STLINK_SERIAL."
st-flash --serial "$STLINK_SERIAL" read "$SESSION/previous-stm32-flash.bin" 0x08000000 0x80000
[[ $(stat -c %s "$SESSION/previous-stm32-flash.bin") == 524288 ]] || {
    echo 'Flash backup has an unexpected size; programming refused.' >&2; exit 11;
}
sha256sum "$SESSION/previous-stm32-flash.bin" | tee "$SESSION/previous-stm32-flash.sha256"
sha256sum "$APP/firmware/rf_link_lab_f446re.bin" | tee "$SESSION/programmed-image.sha256"
mark programming_f446
bash "$APP/FLASH-STM32.sh" --serial "$STLINK_SERIAL" --board NUCLEO-F446RE
mark locating_uart
for attempt in {1..10}; do
    python3 - "$STLINK_SERIAL" "$SESSION" <<'PY'
import pathlib, subprocess, sys
serial, dest=sys.argv[1:]; matches=[]
for port in pathlib.Path('/dev/serial/by-id').glob('*'):
    result=subprocess.run(['udevadm','info','--query=property','--name',str(port)], capture_output=True,text=True)
    properties=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if properties.get('ID_SERIAL_SHORT','').lower()==serial.lower(): matches.append(str(port))
pathlib.Path(dest,'selected-uart.txt').write_text(matches[0]+'\n' if len(matches)==1 else '')
PY
    [[ -s "$SESSION/selected-uart.txt" ]] && break
    sleep 2
done
if [[ ! -s "$SESSION/selected-uart.txt" ]]; then
    mark waiting_for_uart
    echo 'No unique stable UART endpoint matched this ST-LINK. Replug CN1 and inspect /dev/serial/by-id.' >&2
    exit 12
fi
UART_PORT=$(cat "$SESSION/selected-uart.txt")
mark recording_uart
printf '\nUART=%s\nPRESS AND RELEASE THE BLUE B1 BUTTON SEVERAL TIMES during the next 30 seconds.\n' "$UART_PORT"
sleep 3
bash "$APP/START-RF-LINK-LAB.sh" serial-record --port "$UART_PORT" --baudrate 115200 --duration 30 --output "$SESSION/uart" | tee "$SESSION/serial-record-result.json"
python3 - "$SESSION" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1]); manifest=json.loads((p/'uart/uart.json').read_text())
packets=[json.loads(line) for line in (p/'uart/packets.jsonl').read_text().splitlines() if line]
states=sorted({row['button'] for row in packets})
summary=dict(packet_rows=len(packets),button_states=states,uart_summary=manifest.get('summary'),
             interpretation='Physical UART rows; RF transmission has not been attempted.')
(p/'uart-observation.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
if not packets: sys.exit('No CRC-verified telemetry rows. UART is not validated.')
PY
mark preparing_physical_payload
bash "$APP/START-RF-LINK-LAB.sh" link-prepare --uart-recording "$SESSION/uart" --output "$SESSION/tx" | tee "$SESSION/link-prepare-result.json"
mark uart_recorded_rf_pending
echo 'UART recording and physical payload preparation finished. Inspect counters and button states.'
echo 'No RF capture or transmission was started. RF wiring confirmation and the connection plan remain required.'
