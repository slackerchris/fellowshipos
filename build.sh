#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG="$(pwd)/build.log"
IMAGE="debian:trixie"
WORKDIR="/build"

echo "[fellowshipos] Checking for Docker image..."
docker pull "$IMAGE" > /dev/null

run() {
    local label="$1"
    shift
    echo -n "[fellowshipos] $label... "
    if "$@" >> "$LOG" 2>&1; then
        echo "done"
    else
        echo "FAILED"
        echo ""
        echo "--- last 20 lines of build.log ---"
        tail -20 "$LOG"
        echo "----------------------------------"
        echo "Full log: $LOG"
        exit 1
    fi
}

> "$LOG"

run "Building ISO" docker run --rm \
    --privileged \
    -v "$(pwd):${WORKDIR}" \
    -w "${WORKDIR}" \
    "$IMAGE" \
    bash -c '
        set -e
        apt-get update -qq
        apt-get install -y -qq live-build
        lb clean
        lb config
        lb build
    '

ISO=$(ls -1 *.iso 2>/dev/null | head -1)
echo ""
echo "[fellowshipos] Done! ISO: $ISO"
