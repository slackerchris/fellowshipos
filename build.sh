#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "[fellowshipos] Cleaning previous build..."
sudo lb clean

echo "[fellowshipos] Configuring..."
sudo lb config

echo "[fellowshipos] Building ISO..."
sudo lb build

echo ""
echo "[fellowshipos] Done! ISO is at: $(ls -1 *.iso 2>/dev/null | head -1)"
