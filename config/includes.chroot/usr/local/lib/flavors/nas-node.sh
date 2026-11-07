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

echo "deb [trusted=yes] file:/usr/share/fellowshipos/nas-repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y fellowship-nas fellowship-nas-smb

# The role can pull in a newer kernel than the one running
NEWEST_KERNEL=$(ls -v /lib/modules | tail -1)
if modprobe zfs; then
    # zfsutils starts its services before zfs-dkms has built the module
    systemctl reset-failed zfs-load-module.service
    systemctl start zfs-import.target zfs.target
elif [ "$NEWEST_KERNEL" != "$(uname -r)" ] && modinfo -k "$NEWEST_KERNEL" zfs > /dev/null; then
    echo "[flavor] ZFS built for kernel $NEWEST_KERNEL: reboot to load it"
else
    echo "[flavor] the ZFS module didn't build: see /var/lib/dkms/zfs/*/build/make.log"
    exit 1
fi

mkdir -p /etc/fellowshipos
touch "$DONE_FLAG"

echo "[flavor] nas-node complete"
