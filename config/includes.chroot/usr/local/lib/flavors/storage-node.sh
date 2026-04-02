#!/bin/bash
# Flavor: storage-node
# Installs NFS server, Samba, mergerfs, snapraid
set -e

echo "[flavor] storage-node"

apt-get install -y \
    nfs-kernel-server \
    samba \
    mergerfs \
    snapraid

echo "[flavor] storage-node complete — configure /etc/exports and /etc/samba/smb.conf"
