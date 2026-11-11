#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG="$(pwd)/build.log"
IMAGE="debian:trixie"
WORKDIR="/build"
VERSION="1.0"
TEMPLATE_NAME="fellowshipos-trixie-amd64-${VERSION}"
# Both images are the same live system, built twice. FELLOWSHIP_EDITIONS picks
# which to build; the LXC template comes from the multi-purpose one.
EDITIONS="${FELLOWSHIP_EDITIONS:-nas multi}"
TEMPLATE_FROM="multi"

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

# The packages are the same for both images, so they are built once
run "Building the packages" docker run --rm \
    --privileged \
    -v "$(pwd):${WORKDIR}" \
    -w "${WORKDIR}" \
    "$IMAGE" \
    bash -c '
        set -e
        apt-get update -qq
        apt-get install -y -qq live-build debhelper nodejs npm golang-go libpam0g-dev
        nas/packages/build.sh
        installer/build.sh
    '

for edition in $EDITIONS; do
    run "Building the ${edition} image" docker run --rm \
        --privileged \
        -e FELLOWSHIP_EDITION="$edition" \
        -v "$(pwd):${WORKDIR}" \
        -w "${WORKDIR}" \
        "$IMAGE" \
        bash -c "
            set -e
            apt-get update -qq
            apt-get install -y -qq live-build debhelper nodejs npm golang-go libpam0g-dev

            # The NAS packages are in the NAS image only: the multi-purpose
            # image leaves the role to first boot and offers no NAS choice.
            # The packages step above makes them again on every run.
            if [ \"$edition\" = nas ]; then
                [ -d config/includes.chroot/usr/share/fellowshipos/nas-repo ] ||
                    { echo 'the NAS packages are missing: run nas/packages/build.sh' >&2; exit 1; }
            else
                rm -rf config/includes.chroot/usr/share/fellowshipos/nas-repo
            fi

            lb clean
            lb config
            lb build
            mv live-image-amd64.hybrid.iso fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso
        "

    # The general container comes from the general image, whose chroot is the
    # one `lb build` has just left behind
    if [ "$edition" = "$TEMPLATE_FROM" ]; then
        run "Creating the LXC template" docker run --rm \
            --privileged \
            -v "$(pwd):${WORKDIR}" \
            -w "${WORKDIR}" \
            "$IMAGE" \
            bash -c "
                set -e

                # Remove live-boot/live-config — not needed in LXC, causes (live) prompt
                chroot chroot dpkg --purge live-boot live-boot-initramfs-tools live-config live-config-systemd user-setup 2>/dev/null || true

                # Remove kernel and bootloader — not needed in LXC
                chroot chroot dpkg --purge linux-image-amd64 grub-efi grub-common 2>/dev/null || true

                # Clean up leftover live artifacts
                rm -rf chroot/lib/live chroot/etc/live chroot/etc/fstab.d/live 2>/dev/null || true
                rm -f chroot/etc/debian_chroot

                # The NAS packages belong to the NAS image, not the general container
                rm -rf chroot/usr/share/fellowshipos/nas-repo

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
    fi
done

TEMPLATE="${TEMPLATE_NAME}_lxc.tar.gz"

for edition in $EDITIONS; do
    ISO="fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso"
    if [ ! -f "$ISO" ]; then
        echo "[fellowshipos] FAILED: expected ISO not found: $ISO"
        exit 1
    fi
done

if [ ! -f "$TEMPLATE" ]; then
    echo "[fellowshipos] FAILED: expected LXC template not found: $TEMPLATE"
    exit 1
fi

echo ""
for edition in $EDITIONS; do
    echo "[fellowshipos] ISO:      fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso"
done
echo "[fellowshipos] Template: ${TEMPLATE}"
echo ""
echo "Deploy to Proxmox:"
echo "  scp ${TEMPLATE} root@proxmox:/var/lib/vz/template/cache/"
