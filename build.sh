#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "[homelabos] Cleaning previous build..."
sudo lb clean

echo "[homelabos] Configuring..."
sudo lb config

echo "[homelabos] Building ISO..."
sudo lb build

echo ""
echo "[homelabos] Done! ISO is at: $(ls -1 *.iso 2>/dev/null | head -1)"
