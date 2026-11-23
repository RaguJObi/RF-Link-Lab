#!/usr/bin/env bash
# Explicit one-time operating-system setup. Never called by the app launcher.
set -Eeuo pipefail
trap 'printf "Setup stopped at line %s. Fix the reported package/network error and rerun this script.\n" "$LINENO" >&2' ERR

if [[ "${1:-}" == "--help" ]]; then
    printf '%s\n' 'Usage: bash SETUP-UBUNTU.sh' \
        'For Ubuntu 22.04 x86_64. Installs GUI/USB tools and Nuand bladeRF packages.' \
        'Requires internet and sudo. Does not flash any device or install Python packages.'
    exit 0
fi
if [[ $# -ne 0 ]]; then
    printf '%s\n' 'Unknown argument. Use --help.' >&2
    exit 2
fi
if [[ ! -r /etc/os-release ]]; then
    printf '%s\n' 'Cannot identify this Linux distribution. Ubuntu 22.04 x86_64 is required.' >&2
    exit 3
fi
# This OS-owned file defines ID and VERSION_ID.
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 22.04 || "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'This bundle targets Ubuntu 22.04 on x86_64. No setup changes were made.' >&2
    exit 3
fi
if [[ $EUID -eq 0 ]]; then
    printf '%s\n' 'Run this script as your normal desktop user, without sudo before the script name.' \
        'It will request sudo only for the required system changes.' >&2
    exit 3
fi
login_user=$(id -un)
printf '%s\n' \
    'RF Link Lab one-time Ubuntu setup:' \
    '  Install native GUI libraries, ST-LINK tools, and USB utilities.' \
    '  Add Nuand official bladeRF PPA and install host tools plus the xA9 FPGA image.' \
    "  Add $login_user to dialout and plugdev for device access." \
    'No MCU or bladeRF flash operation will run.'
sudo -v
sudo apt-get update
sudo apt-get install -y software-properties-common ca-certificates
# Enable package sources before installing dependencies that may be in universe.
sudo add-apt-repository -y universe
sudo add-apt-repository -y ppa:nuandllc/bladerf
sudo apt-get update
sudo apt-get install -y \
    libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 \
    libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-xkb1 libfontconfig1 fonts-dejavu-core \
    libdbus-1-3 libnss3 libasound2 libusb-1.0-0 usbutils \
    coreutils stlink-tools bladerf bladerf-fpga-hostedxa9
for device_group in dialout plugdev; do
    if ! getent group "$device_group" >/dev/null; then
        sudo groupadd "$device_group"
    fi
done
sudo usermod -a -G dialout,plugdev -- "$login_user"
sudo udevadm control --reload-rules
bundle_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
launcher_path="$bundle_root/START-RF-LINK-LAB.sh"
launcher_quoted=${launcher_path//\\/\\\\}
launcher_quoted=${launcher_quoted//\"/\\\"}
launcher_quoted=${launcher_quoted//%/%%}
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p -- "$applications_dir"
printf '[Desktop Entry]\nType=Application\nName=RF Link Lab\nComment=Telemetry and RF learning workbench\nExec="%s"\nTerminal=false\nCategories=Education;Science;\n' "$launcher_quoted" > "$applications_dir/rf-link-lab.desktop"
printf '\n%s\n' \
    'Setup complete. Sign out of the desktop session, then sign back in for group changes.' \
    'After signing in, reconnect both USB boards and run START-RF-LINK-LAB.sh.' \
    'The app includes its own Python runtime. No compiler or pip installation is needed.' \
    'An RF Link Lab application-menu entry was installed. Keep the extracted bundle in its current location.' \
    'bladeRF firmware has not been changed. ModemManager has not been disabled.'
