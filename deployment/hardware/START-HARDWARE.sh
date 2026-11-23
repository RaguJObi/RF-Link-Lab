#!/usr/bin/env bash
# Explicit xA9 hardware entry: matched private host tools + volatile FPGA + app.
set -Eeuo pipefail
if [[ $# -lt 2 || $1 != --serial || ! $2 =~ ^[[:xdigit:]]{32}$ || $EUID -eq 0 ]]; then
    echo 'Usage: bash START-HARDWARE.sh --serial EXACT_XA9_SERIAL [RF_LINK_LAB_COMMAND ...]' >&2
    echo 'With no application command, this opens the native GUI after preparing the selected radio.' >&2
    exit 2
fi
serial=${2,,}
export RF_LINK_LAB_SELECTED_SERIAL="$serial"
shift 2
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$script_dir/PROJECT_CONTEXT.json" ]]; then PROJECT="$script_dir";
else PROJECT=$(cd -- "$script_dir/../.." && pwd); fi
if [[ -f "$PROJECT/START-RF-LINK-LAB.sh" ]]; then default_app="$PROJECT";
else
    app_version=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$PROJECT/PROJECT_CONTEXT.json")
    [[ $app_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
    default_app="$PROJECT/runtime-ubuntu/RF-Link-Lab-v$app_version-Ubuntu22-x86_64"
fi
APP=${RF_LINK_LAB_APP:-$default_app}
[[ -f "$APP/START-RF-LINK-LAB.sh" ]] || { echo 'The installed Ubuntu application is missing.' >&2; exit 3; }
mkdir -p "$PROJECT/build"
exec 9>"$PROJECT/build/rf-link-lab-hardware.lock"
flock -n 9 || { echo 'Another hardware launcher is running. Close it before reconfiguring the FPGA.' >&2; exit 4; }
echo 'Close other bladeRF programs before preparing this selected radio.'
if [[ -f "$PROJECT/PREPARE-MATCHED-BLADERF.sh" ]]; then prepare="$PROJECT/PREPARE-MATCHED-BLADERF.sh";
else prepare="$PROJECT/deployment/hardware/PREPARE-MATCHED-BLADERF.sh"; fi
if [[ -d "$PROJECT/hardware-tools/nuand-2025.10" ]]; then default_tools="$PROJECT/hardware-tools/nuand-2025.10";
else default_tools="$PROJECT/build/nuand-2025.10"; fi
tools=$(realpath -m -- "${RF_LINK_LAB_TOOLS:-$default_tools}")
export RF_LINK_LAB_TOOLS="$tools"
RF_LINK_LAB_PROJECT="$PROJECT" bash "$prepare" --serial "$serial"
prefix="$tools/install"
export PATH="$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec bash "$APP/START-RF-LINK-LAB.sh" "$@"
