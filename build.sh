#!/bin/bash
set -e

cd "$(dirname "$0")"

LOG="$(pwd)/build.log"
IMAGE="debian:trixie"
WORKDIR="/build"
# The one release version, prerelease 0.major.minor; see docs/versioning.md
VERSION="$(cat VERSION)"
TEMPLATE_NAME="fellowshipos-trixie-amd64-${VERSION}"
# The images are the same live system, built per edition. FELLOWSHIP_EDITIONS
# picks which to build; the LXC template comes from the multi-purpose one. Add
# `desktop` for the image that boots into the Fellowship desktop. FellowshipNAS
# is its own project now, in the FellowshipOS-NAS repository.
EDITIONS="${FELLOWSHIP_EDITIONS:-multi}"
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

# The packages are the same for every image, so they are built once. The
# desktop package needs the fellowship-desktop checkout beside this
# repository; building it costs a Rust build, so it runs only when asked for.
PACKAGES='
        set -e
        apt-get update -qq
        apt-get install -y -qq live-build debhelper nodejs npm golang-go libpam0g-dev libcrypt-dev
        installer/build.sh
        installer/cog/build.sh
    '
DESKTOP_MOUNT=()
case " $EDITIONS " in
    *" desktop "*)
        PACKAGES="$PACKAGES
        desktop/build.sh"
        DESKTOP_MOUNT=(-v "$(cd .. && pwd)/fellowship-desktop:/fellowship-desktop:ro")
        ;;
esac

if [ "${FELLOWSHIP_SKIP_PACKAGES:-}" = 1 ]; then
    echo "[fellowshipos] Building the packages... skipped (FELLOWSHIP_SKIP_PACKAGES)"
else
    run "Building the packages" docker run --rm \
        --privileged \
        -v "$(pwd):${WORKDIR}" \
        "${DESKTOP_MOUNT[@]}" \
        -w "${WORKDIR}" \
        "$IMAGE" \
        bash -c "$PACKAGES"
fi

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
            apt-get install -y -qq live-build debhelper nodejs npm golang-go libpam0g-dev libcrypt-dev

            # live-build installs every .deb in packages.chroot, so the
            # desktop's package is there for the desktop image and kept aside
            # for the images that are not it. Aside means cache/: lb clean
            # empties .build/, which lost the package between images.
            mkdir -p cache/desktop-package
            if [ \"$edition\" = desktop ]; then
                # Only restore a parked package if packages.chroot doesn't already
                # hold a freshly built one (otherwise a stale parked .deb from a
                # previous multi build would overwrite what desktop/build.sh just made).
                if ! ls config/packages.chroot/fellowship-desktop_*.deb >/dev/null 2>&1; then
                    ls cache/desktop-package/fellowship-desktop_*.deb >/dev/null 2>&1 &&
                        mv cache/desktop-package/fellowship-desktop_*.deb config/packages.chroot/
                else
                    rm -f cache/desktop-package/fellowship-desktop_*.deb
                fi
                ls config/packages.chroot/fellowship-desktop_*.deb >/dev/null 2>&1 ||
                    { echo 'the desktop package is missing: run desktop/build.sh' >&2; exit 1; }
            else
                ls config/packages.chroot/fellowship-desktop_*.deb >/dev/null 2>&1 &&
                    mv config/packages.chroot/fellowship-desktop_*.deb cache/desktop-package/
            fi

            lb clean
            lb config
            lb build
            mv live-image-amd64.hybrid.iso fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso
            # One ISO per edition here: older ones, renamed test builds
            # included, piled up to a dozen root-owned copies by 2026-11-22.
            # Handover copies belong in ../fellowship/build, which this leaves
            # alone. FELLOWSHIP_KEEP_OLD_ISOS=1 keeps them all.
            if [ \"${FELLOWSHIP_KEEP_OLD_ISOS:-}\" != 1 ]; then
                for old in fellowshipos-${edition}-amd64-*.hybrid.iso; do
                    [ \"\$old\" = fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso ] || rm -fv \"\$old\"
                done
            fi
            # Owned by whoever ran the build, not root, so it can be moved or
            # deleted without sudo
            chown $(id -u):$(id -g) fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso
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

if [ ! -f "$TEMPLATE" ] && [ "$(echo " $EDITIONS " | grep -c " $TEMPLATE_FROM ")" = 1 ]; then
    echo "[fellowshipos] FAILED: expected LXC template not found: $TEMPLATE"
    exit 1
fi

echo ""
for edition in $EDITIONS; do
    echo "[fellowshipos] ISO:      fellowshipos-${edition}-amd64-${VERSION}.hybrid.iso"
done
if [ -f "$TEMPLATE" ]; then
    echo "[fellowshipos] Template: ${TEMPLATE}"
    echo ""
    echo "Deploy to Proxmox:"
    echo "  scp ${TEMPLATE} root@proxmox:/var/lib/vz/template/cache/"
fi
