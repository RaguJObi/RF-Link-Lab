#!/usr/bin/env bash
# Build a pinned, isolated host stack and load a matching xA9 FPGA into RAM.
# Lowercase -l loads volatile FPGA memory. This script never calls -L or -f.
set -Eeuo pipefail
if [[ $# -ne 2 || $1 != --serial || ! $2 =~ ^[[:xdigit:]]{32}$ || $EUID -eq 0 ]]; then
    echo 'Usage as normal user: bash PREPARE-MATCHED-BLADERF.sh --serial EXACT_XA9_SERIAL' >&2
    exit 2
fi
serial=${2,,}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -f "$script_dir/PROJECT_CONTEXT.json" ]]; then default_project="$script_dir";
else default_project=$(cd -- "$script_dir/../.." && pwd); fi
PROJECT=${RF_LINK_LAB_PROJECT:-$default_project}
PROJECT=$(realpath -- "$PROJECT")
[[ -f "$PROJECT/PROJECT_CONTEXT.json" && $(uname -m) == x86_64 ]]
if [[ -d "$PROJECT/hardware-tools/nuand-2025.10" ]]; then default_tools="$PROJECT/hardware-tools/nuand-2025.10";
else default_tools="$PROJECT/build/nuand-2025.10"; fi
tools=${RF_LINK_LAB_TOOLS:-$default_tools}
source_dir=${RF_LINK_LAB_NUAND_SOURCE:-"$tools/source"}
build_dir="$tools/cmake-build"
prefix="$tools/install"
image="$tools/hostedxA9-v0.16.0.rbf"
source_commit=fcf9423325ae49f5fdd2e5b52ab68bfc2576ad47
image_hash=3004467110e2b2790c8f6128f998c2149905a88eed91a87f11af3db524d60c65
mkdir -p "$tools"
if [[ ! -x "$prefix/bin/bladeRF-cli" || ! -s "$tools/build-identity.txt" ]]; then
    missing=()
    for program in gcc g++ make; do command -v "$program" >/dev/null || missing+=(build-essential); done
    for program in cmake pkg-config git patch curl; do command -v "$program" >/dev/null || missing+=("$program"); done
    if command -v pkg-config >/dev/null; then
        pkg-config --exists libusb-1.0 || missing+=(libusb-1.0-0-dev)
        pkg-config --exists libcurl || missing+=(libcurl4-openssl-dev)
        pkg-config --exists ncurses || missing+=(libncurses-dev)
    else
        missing+=(libusb-1.0-0-dev libcurl4-openssl-dev libncurses-dev)
    fi
    if [[ ${#missing[@]} -gt 0 ]]; then
        printf 'Missing build prerequisites: %s\n' "${missing[*]}"
        sudo -v -p $'[sudo] Ubuntu password for %u (type here):\n'
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    fi
    if [[ ! -e "$source_dir" ]]; then
        git clone --branch 2025.10 --depth 1 --recurse-submodules --shallow-submodules \
            https://github.com/Nuand/bladeRF.git "$source_dir"
    fi
    [[ $(git -C "$source_dir" rev-parse HEAD) == "$source_commit" ]] || {
        echo 'Unexpected Nuand source revision; isolated build refused.' >&2; exit 3;
    }
    git -C "$source_dir" submodule status --recursive | tee "$tools/submodules.txt"
    if grep -Eq '^[+-U]' "$tools/submodules.txt"; then
        echo 'Submodules do not match the pinned source; inspect before building.' >&2; exit 3
    fi
    cmake -S "$source_dir/host" -B "$build_dir" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
        -DCMAKE_INSTALL_LIBDIR=lib '-DCMAKE_INSTALL_RPATH=$ORIGIN/../lib' \
        -DENABLE_BACKEND_LIBUSB=ON -DINSTALL_UDEV_RULES=OFF \
        -DBUILD_DOCUMENTATION=OFF -DBUILD_BLADERF_CLI_INTERACTIVE_HELP=OFF \
        -DTEST_LIBBLADERF=OFF -DBUILD_BLADERF_FSK=OFF \
        -DCMAKE_DISABLE_FIND_PACKAGE_LibTecla=TRUE -DCMAKE_DISABLE_FIND_PACKAGE_LibEdit=TRUE
    cmake --build "$build_dir" --parallel 4
    cmake --install "$build_dir"
    mkdir -p "$prefix/share/nuand-licenses"
    cp -- "$source_dir/COPYING" "$prefix/share/nuand-licenses/"
    cp -a -- "$source_dir/legal/licenses" "$prefix/share/nuand-licenses/"
    printf '%s\n' "$source_commit" > "$tools/build-identity.txt"
fi
[[ $(cat "$tools/build-identity.txt") == "$source_commit" ]]
export PATH="$prefix/bin:$PATH"
export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[[ $(bladeRF-cli --lib-version) == 2.6.0* ]]
[[ $(bladeRF-cli --version) == 1.10.0* ]]
ldd "$prefix/bin/bladeRF-cli" | tee "$tools/linked-libraries.txt"
grep -F "$prefix/lib/libbladeRF.so" "$tools/linked-libraries.txt" >/dev/null
if [[ ! -f "$image" ]]; then
    curl --fail --location --connect-timeout 15 --max-time 180 \
        --output "$image.download" https://www.nuand.com/fpga/v0.16.0/hostedxA9.rbf
    printf '%s  %s\n' "$image_hash" "$image.download" | sha256sum --check
    mv -- "$image.download" "$image"
fi
printf '%s  %s\n' "$image_hash" "$image" | sha256sum --check
echo 'Loading the matched xA9 FPGA into volatile RAM; power cycling restores the SPI image.'
bladeRF-cli -d "*:serial=$serial" -l "$image" -e version -e info | tee "$tools/loaded-device.txt"
grep -Eq 'FPGA version:[[:space:]]+0\.16\.0' "$tools/loaded-device.txt"
grep -Eq 'Firmware version:[[:space:]]+2\.6\.0' "$tools/loaded-device.txt"
printf '\nMATCHED_TOOLS=%s\nVOLATILE_FPGA=0.16.0\n' "$prefix"
