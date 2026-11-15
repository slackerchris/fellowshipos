#!/bin/bash
# Build ONLY the desktop .deb, the way build.sh's packages step does it,
# without rebuilding the installer, cog and NAS packages alongside it.
#
#   desktop/build-only.sh
#
# Same image, same read-only mount of the fellowship-desktop checkout and
# the same desktop/build.sh inside, so what this produces is what a full
# image build would install. Two host directories persist across runs —
# the toolchain plus crates registry, and the release target — so a repeat
# costs a compile, not a download. The result lands in
# config/packages.chroot/, exactly where the image build expects it.
#
# The caches live outside .build/ on purpose: `lb clean` empties that.
set -e
cd "$(dirname "$0")/.."
IMAGE="debian:trixie"
LOG="$(pwd)/desktop-package-build.log"
CACHE="${FELLOWSHIP_CARGO_CACHE:-"$HOME/.cache/fellowship-cargo"}"
mkdir -p "$CACHE/registry" "$CACHE/target"

echo "[desktop] building the package in $IMAGE (log: $LOG, cache: $CACHE)"
docker run --rm --privileged \
    -v "$(pwd):/build" \
    -v "$(cd .. && pwd)/fellowship-desktop:/fellowship-desktop:ro" \
    -v "$CACHE/registry:/cargo" \
    -v "$CACHE/target:/tmp/desktop-target" \
    -w /build \
    "$IMAGE" \
    bash -c '
        set -e
        # rustup honours both, so the toolchain and the registry land in the
        # mounted cache. Installing here rather than letting desktop/build.sh
        # do it keeps its own setup off the cache paths it cannot see.
        export CARGO_HOME=/cargo RUSTUP_HOME=/cargo/rustup
        apt-get update -qq
        apt-get install -y -qq build-essential pkg-config curl ca-certificates \
            libwayland-dev libxkbcommon-dev libegl1-mesa-dev libgbm-dev \
            libudev-dev libseat-dev libinput-dev libx11-dev libxcursor-dev \
            libxrandr-dev libxi-dev
        if [ ! -x /cargo/bin/cargo ]; then
            curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
        fi
        PATH=/cargo/bin:$PATH desktop/build.sh
    ' >> "$LOG" 2>&1

ls -l config/packages.chroot/fellowship-desktop_*.deb | tail -1
echo "done"
