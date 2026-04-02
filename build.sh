#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG="$(pwd)/build.log"

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

run "Cleaning"    sudo lb clean
run "Configuring" sudo lb config
run "Building"    sudo lb build

ISO=$(ls -1 *.iso 2>/dev/null | head -1)
echo ""
echo "[fellowshipos] Done! ISO: $ISO"
