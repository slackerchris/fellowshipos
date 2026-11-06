#!/bin/bash
# Flavor: nas-node
# Installs the FellowshipNAS role (safe to run again after a failure)
set -e

DONE_FLAG="/etc/fellowshipos/.nas-role-complete"
SECURE_BOOT_VAR="/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

echo "[flavor] nas-node"

if systemd-detect-virt --container --quiet; then
    echo "[flavor] nas-node needs a full install: containers can't load ZFS or own disks"
    exit 1
fi

# Byte 5 of the EFI variable is the Secure Boot state
if [ -r "$SECURE_BOOT_VAR" ] && [ "$(od -An -tu1 -j4 -N1 "$SECURE_BOOT_VAR" | tr -d ' ')" = "1" ]; then
    echo "[flavor] Secure Boot is on, so the ZFS module can't load: disable it and retry"
    exit 1
fi

if ! curl -fsI --max-time 15 http://deb.debian.org/debian/dists/trixie/Release > /dev/null; then
    echo "[flavor] can't reach the Debian mirror: check the network and retry"
    exit 1
fi

# Phase 1 installs the fellowship-nas packages here

mkdir -p /etc/fellowshipos
touch "$DONE_FLAG"

echo "[flavor] nas-node complete"
