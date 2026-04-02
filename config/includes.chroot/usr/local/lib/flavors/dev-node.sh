#!/bin/bash
# Flavor: dev-node
# Installs dev tools
set -e

echo "[flavor] dev-node"

apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    make \
    ansible

echo "[flavor] dev-node complete"
