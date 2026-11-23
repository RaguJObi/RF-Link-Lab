#!/usr/bin/env bash
# Record fresh physical UART, then send exactly one plan-gated RF snapshot.
set -Eeuo pipefail
umask 077
serial= port= plan= output=
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || { echo 'Every option requires a value.' >&2; exit 2; }
    case "$1" in
        --serial) serial=$2 ;;
        --port) port=$2 ;;
        --plan) plan=$2 ;;
        --output) output=$2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift 2
done
if [[ ! $serial =~ ^[[:xdigit:]]{32}$ || -z $port || -z $plan || -z $output || $EUID -eq 0 ]]; then
    echo 'Usage: bash RUN-CONNECTED-BENCH.sh --serial XA9_SERIAL --port /dev/serial/by-id/PORT --plan /absolute/connection-plan.json --output /absolute/new-session' >&2
    exit 2
fi
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ -f "$script_dir/PROJECT_CONTEXT.json" ]]; then PROJECT="$script_dir";
else PROJECT=$(cd -- "$script_dir/../.." && pwd); fi
if [[ -f "$PROJECT/START-RF-LINK-LAB.sh" ]]; then APP="$PROJECT";
else
    app_version=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PROJECT/PROJECT_CONTEXT.json")
    [[ $app_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
    APP="$PROJECT/runtime-ubuntu/RF-Link-Lab-v$app_version-Ubuntu22-x86_64"
fi
app_override=$(printenv RF_LINK_LAB_APP || true)
[[ -z $app_override ]] || APP="$app_override"
[[ -f "$APP/START-RF-LINK-LAB.sh" && $port == /* && -c $port && -f $plan ]] || {
    echo 'Application, selected UART endpoint or connection plan is missing.' >&2; exit 3;
}
[[ $output == /* && ! -e $output && ! -L $output ]] || {
    echo 'Choose an absolute new output folder; existing evidence is preserved.' >&2; exit 3;
}
plan=$(realpath -- "$plan")
python3 - "$plan" "$serial" <<'PY'
import json,sys
plan=json.load(open(sys.argv[1]))
if plan.get('device_serial','').lower()!=sys.argv[2].lower():
    sys.exit('The plan must select the same bladeRF serial as this launcher.')
PY
mkdir -p -- "$(dirname -- "$output")"
mkdir -- "$output"
output=$(realpath -- "$output")
cp -- "$plan" "$output/connection-plan.json"
plan_basis="$(dirname -- "$plan")/plan-basis.json"
if [[ -f "$plan_basis" && ! -L "$plan_basis" ]]; then
    cp -- "$plan_basis" "$output/plan-basis.json"
fi
phase=starting
write_status() {
    python3 - "$output" "$phase" "$1" "$serial" "$port" <<'PY'
import datetime,json,pathlib,sys
folder,phase,code,serial,port=sys.argv[1:]
p=pathlib.Path(folder)
x={'schema':'rf-link-lab.connected-bench.v1','phase':phase,'exit_code':int(code),
   'updated_host_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
   'serial':serial.lower(),'uart_port':port,'uart_duration_s':10,
   'scope':'Fresh recorded UART snapshot followed by one finite RF burst; no board flashing.',
   'experiment':'rf/experiment.json','report':'rf/report.html'}
if (p/'rf/experiment.json').is_file():
    rf=json.loads((p/'rf/experiment.json').read_text())
    x.update(rf_status=rf['status'],cleanup_confirmed=rf['cleanup_confirmed'],metrics=rf['metrics'])
temporary=p/'connected-session.json.tmp'
temporary.write_text(json.dumps(x,indent=2)+'\n');temporary.replace(p/'connected-session.json')
PY
}
trap 'code=$?; write_status "$code"' EXIT
echo "SESSION=$output"
echo 'Recording the Nucleo for 10 seconds. You may press and release its blue B1 button.'
phase=recording_uart
bash "$APP/START-RF-LINK-LAB.sh" serial-record --port "$port" --baudrate 115200 --duration 10 --output "$output/uart" | tee "$output/serial-result.json"
bash "$APP/START-RF-LINK-LAB.sh" uart-replay "$output/uart" > "$output/uart-verification.json"
phase=preparing_snapshot
bash "$APP/START-RF-LINK-LAB.sh" link-prepare --uart-recording "$output/uart" --output "$output/tx" > "$output/tx-result.json"
if [[ -f "$PROJECT/START-HARDWARE.sh" ]]; then hardware="$PROJECT/START-HARDWARE.sh";
else hardware="$PROJECT/deployment/hardware/START-HARDWARE.sh"; fi
phase=finite_rf_attempt
set +e
RF_LINK_LAB_APP="$APP" bash "$hardware" --serial "$serial" link-live "$output/tx" --plan "$output/connection-plan.json" --rx-gain 40 --output "$output/rf" | tee "$output/rf-console.log"
run_code=$?
set -e
if [[ -f "$output/rf/experiment.json" ]]; then
    bash "$APP/START-RF-LINK-LAB.sh" link-verify "$output/rf" > "$output/rf-verification.json"
    bash "$APP/START-RF-LINK-LAB.sh" bench-verify "$output/rf" > "$output/bench-verification.json"
fi
if [[ $run_code == 0 ]]; then phase=completed; else phase=rf_attempt_incomplete; fi
echo "REPORT=$output/rf/report.html"
echo 'Inspect known-payload completion and cleanup status in the saved report.'
exit "$run_code"
