#!/bin/bash
# Flavor: gateway-node
# Hardens the machine for edge/gateway use
set -e

echo "[flavor] gateway-node"

apt-get install -y ufw fail2ban unattended-upgrades wireguard

# Basic ufw rules
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow mosh
ufw --force enable

# Enable fail2ban
systemctl enable fail2ban
systemctl start fail2ban

# Enable unattended security upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "[flavor] gateway-node complete"
