#!/bin/bash
# Flavor: monitor-node
# Installs Prometheus node-exporter and glances
set -e

echo "[flavor] monitor-node"

apt-get install -y prometheus-node-exporter glances

systemctl enable prometheus-node-exporter
systemctl start prometheus-node-exporter

echo "[flavor] monitor-node complete"
