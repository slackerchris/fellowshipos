#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG="$(pwd)/build.log"
IMAGE="debian:trixie"
WORKDIR="/build"
VERSION="1.0"
TEMPLATE_NAME="fellowshipos-trixie-amd64-${VERSION}"
ISO_NAME="live-image-amd64.hybrid.iso"

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
rm -f "${TEMPLATE_NAME}_lxc.tar.gz"

run "Building" docker run --rm \
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

# Package LXC template from the chroot live-build already created
run "Creating LXC template" docker run --rm \
    --privileged \
    -v "$(pwd):${WORKDIR}" \
    -w "${WORKDIR}" \
    "$IMAGE" \
    bash -c "
        set -e

        # Remove live-boot/live-config — not needed in LXC, causes (live) prompt
        chroot chroot dpkg --purge live-boot live-boot-initramfs-tools live-config live-config-systemd 2>/dev/null || true

        # Remove kernel and bootloader — not needed in LXC
        chroot chroot dpkg --purge linux-image-amd64 grub-efi grub-common 2>/dev/null || true

        # Clean up leftover live artifacts
        rm -rf chroot/lib/live chroot/etc/live chroot/etc/fstab.d/live 2>/dev/null || true

        # Tar up the rootfs in Proxmox-compatible format
        tar -czf ${TEMPLATE_NAME}_lxc.tar.gz \
            --numeric-owner \
            --anchored \
            --exclude='./dev/*' \
            --exclude='./proc/*' \
            --exclude='./sys/*' \
            --exclude='./tmp/*' \
            --exclude='./run/*' \
            -C chroot .
    "

ISO="$ISO_NAME"
TEMPLATE="${TEMPLATE_NAME}_lxc.tar.gz"

if [ ! -f "$ISO" ]; then
    echo "[fellowshipos] FAILED: expected ISO not found: $ISO"
    exit 1
fi

if [ ! -f "$TEMPLATE" ]; then
    echo "[fellowshipos] FAILED: expected LXC template not found: $TEMPLATE"
    exit 1
fi

echo ""
echo "[fellowshipos] ISO:      ${ISO}"
echo "[fellowshipos] Template: ${TEMPLATE}"
echo ""
echo "Deploy to Proxmox:"
echo "  scp ${TEMPLATE} root@proxmox:/var/lib/vz/template/cache/"
