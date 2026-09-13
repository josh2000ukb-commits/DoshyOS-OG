#!/bin/bash
# Builds the doshy OS ISO. Must run as root on Debian/Ubuntu (native, WSL, or Docker).
#
#   sudo ./build.sh
#
# Environment variables:
#   BUILD_DIR   where the build happens (defaults to a Linux-native dir when the
#               project lives on a Windows mount, because chroots cannot be built on NTFS)
#   OS_NAME     name of the OS (default: doshy OS)
#   LIVE_LOCALE / LIVE_KEYBOARD / LIVE_TIMEZONE   e.g. en_GB.UTF-8 / gb / Europe/London
set -euo pipefail

SRC_DIR="${SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
export OS_NAME="${OS_NAME:-doshy OS}"

if [ "$(id -u)" -ne 0 ]; then
    echo "error: this script must be run as root (try: sudo $0)" >&2
    exit 1
fi

# Pick a build directory on a real Linux filesystem.
if [ -z "${BUILD_DIR:-}" ]; then
    case "$SRC_DIR" in
        /mnt/*|/src|/src/*) BUILD_DIR="/opt/doshy-build" ;;
        *)                  BUILD_DIR="$SRC_DIR" ;;
    esac
fi

echo "==> Source:    $SRC_DIR"
echo "==> Build dir: $BUILD_DIR"

# Install build dependencies
export DEBIAN_FRONTEND=noninteractive
if ! command -v lb >/dev/null 2>&1 || ! command -v rsync >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "==> Installing live-build"
    apt-get update
    apt-get install -y --no-install-recommends live-build debootstrap rsync python3 ca-certificates \
        xorriso squashfs-tools dosfstools mtools
fi

python3 "$SRC_DIR/scripts/build-update.py" --prepare

# Copy the config into the build dir (keeps the apt cache between builds)
if [ "$BUILD_DIR" != "$SRC_DIR" ]; then
    mkdir -p "$BUILD_DIR"
    rsync -a --delete "$SRC_DIR/auto" "$SRC_DIR/config" "$BUILD_DIR/"
fi
cd "$BUILD_DIR"

# Normalise files that may have come from Windows (CRLF, lost exec bits)
find auto config -type f ! -name '*.png' -exec sed -i 's/\r$//' {} +
find config/includes.chroot -type f -exec chmod 644 {} +
chmod 755 auto/* config/hooks/*/* \
          config/includes.chroot/usr/local/bin/* \
          config/includes.chroot/usr/local/lib/doshy/login-setup \
          config/includes.chroot/usr/share/doshy/menu.sh \
          config/includes.chroot/usr/share/doshy/apps.sh \
          config/includes.chroot/etc/xdg/openbox/autostart

echo "==> Cleaning previous build"
lb clean

echo "==> Configuring"
lb config

echo "==> Building (this takes a while and downloads ~1.5 GB)"
lb build

ISO="$(ls -1 ./*.iso 2>/dev/null | head -n1 || true)"
if [ -z "$ISO" ]; then
    echo "error: no ISO produced - see $BUILD_DIR/build.log" >&2
    exit 1
fi

mkdir -p "$SRC_DIR/out"
cp -v "$ISO" "$SRC_DIR/out/"
cp -v build.log "$SRC_DIR/out/" 2>/dev/null || true
echo
echo "==> Done: $SRC_DIR/out/$(basename "$ISO")"
