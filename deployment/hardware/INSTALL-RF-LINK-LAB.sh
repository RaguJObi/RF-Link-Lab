#!/usr/bin/env bash
# Install the source and portable Ubuntu runtime into a NEW project folder.
set -Eeuo pipefail
kit_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_parent="${1:-$HOME/SDR/bladeRF}"
project_root="$project_parent/RF-Link-Lab"
runtime_archive=$(python3 - "$kit_root" <<'PY'
import pathlib,sys
archives=list(pathlib.Path(sys.argv[1]).glob('RF-Link-Lab-v*-Ubuntu22-x86_64.tar.gz'))
if len(archives)!=1:sys.exit('The transfer kit must contain exactly one Ubuntu runtime archive.')
print(archives[0].name)
PY
)
runtime_dir=$(basename -- "$runtime_archive" .tar.gz)
if [[ $# -gt 1 ]]; then
    printf '%s\n' 'Usage: bash INSTALL-RF-LINK-LAB.sh [parent-directory]' >&2
    exit 2
fi
if [[ $EUID -eq 0 ]]; then
    printf '%s\n' 'Run this installer as your normal desktop user, without sudo.' >&2
    exit 2
fi
if [[ -e "$project_root" || -L "$project_root" ]]; then
    printf 'Existing project preserved: %s\nChoose a different parent directory or inspect the existing installation.\n' "$project_root" >&2
    exit 2
fi
cd -- "$kit_root"
sha256sum -c SHA256SUMS
mkdir -p -- "$project_parent"
if command -v git >/dev/null; then
    git clone --branch main --origin transfer "$kit_root/RF-Link-Lab-history.bundle" "$project_root"
    printf '\n/runtime-ubuntu/\n' >> "$project_root/.git/info/exclude"
else
    tar -xzf "$kit_root/RF-Link-Lab-source.tar.gz" -C "$project_parent"
fi
mkdir -p -- "$project_root/release" "$project_root/runtime-ubuntu"
cp -- "$kit_root/$runtime_archive" "$project_root/release/$runtime_archive"
cp -- "$kit_root/$runtime_archive.sha256" "$project_root/release/$runtime_archive.sha256"
tar -xzf "$kit_root/$runtime_archive" -C "$project_root/runtime-ubuntu"
printf '%s\n' "$project_root" > "$kit_root/INSTALLED-PROJECT.txt"
printf '\nInstalled project: %s\nPortable app: %s/runtime-ubuntu/%s\n' "$project_root" "$project_root" "$runtime_dir"
printf '%s\n' 'No board was flashed and no RF transmission was started.' \
    'Read docs/HARDWARE-FIRST-RUN.md. Check existing host tools before running the optional system setup.'
