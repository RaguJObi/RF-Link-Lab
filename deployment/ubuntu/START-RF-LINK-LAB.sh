#!/usr/bin/env bash
# This file is copied to the root of the self-contained Ubuntu bundle.
set -Eeuo pipefail
bundle_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime_python="$bundle_root/runtime/python/bin/python3.12"
if [[ ! -x "$runtime_python" || ! -d "$bundle_root/app/rf_link_lab" || ! -d "$bundle_root/runtime/site-packages" ]]; then
    printf '%s\n' \
        'The portable runtime or application files are missing or not executable.' \
        'Extract the complete Ubuntu tar.gz bundle to a local directory and run this launcher there.' \
        'Keep app/, runtime/, firmware/, and the launcher together.' >&2
    exit 2
fi
if [[ "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'This runtime requires an x86_64 Linux computer.' >&2
    exit 3
fi
if [[ $EUID -eq 0 ]]; then
    printf '%s\n' 'Run the application as your desktop user, without sudo.' >&2
    exit 3
fi
export PYTHONPATH="$bundle_root/app:$bundle_root/runtime/site-packages"
unset PYTHONHOME
export RF_LINK_LAB_RESOURCES="$bundle_root"
export RF_LINK_LAB_HOME="${RF_LINK_LAB_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/rf-link-lab}"
mkdir -p -- "$RF_LINK_LAB_HOME"
cd -- "$RF_LINK_LAB_HOME"
if [[ $# -eq 0 ]]; then
    set -- gui
fi
exec "$runtime_python" -m rf_link_lab "$@"
