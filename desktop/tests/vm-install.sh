#!/bin/bash
# Blank-disk UEFI install and post-install boot harness for FellowshipOS Desktop.
#
#   desktop/tests/vm-install.sh install [work-dir]   # blank disk + ISO via OVMF
#   desktop/tests/vm-install.sh boot    [work-dir]   # installed disk only (ISO ejected)
#
# Environment knobs:
#   FELLOWSHIP_ISO          desktop ISO (defaults to newest desktop ISO in repo)
#   FELLOWSHIP_DISK_SIZE    blank qcow2 size for install (default 32G)
#   FELLOWSHIP_VM_DISPLAY   QEMU display (default gtk; set none / egl-headless for headless)
#   FELLOWSHIP_GL=1         use virtio-gpu-gl-pci (virgl 3D)
#   FELLOWSHIP_OUTPUTS=2    expose 2 DRM outputs for multi-monitor testing
#   FELLOWSHIP_AUDIO        audiodev backend (default none; pipewire/pa for host sound)
#   FELLOWSHIP_QEMU_EXTRA   extra QEMU arguments
set -eu

MODE=${1:-}
case "$MODE" in
    install|boot) ;;
    *)
        echo "usage: $0 {install|boot} [work-dir]" >&2
        exit 2
        ;;
esac

cd "$(dirname "$0")/../.."
WORK=${2:-/tmp/fellowship-desktop-install-vm}
DISK=$WORK/disk.qcow2
VARS=$WORK/ovmf-vars.fd
MONITOR=$WORK/monitor.sock
SERIAL=$WORK/serial.sock
SHARE=$WORK/share

OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
[ -f "$OVMF_CODE" ] || { echo "needs OVMF at $OVMF_CODE" >&2; exit 1; }
[ -f "$OVMF_VARS" ] || { echo "needs OVMF vars at $OVMF_VARS" >&2; exit 1; }

mkdir -p "$WORK" "$SHARE/pkg"
rm -f "$MONITOR" "$SERIAL"

# Share newest fellowship-desktop .deb if present for quick VM iteration.
DEB=$(ls -t config/packages.chroot/fellowship-desktop_*.deb 2>/dev/null | head -1 || true)
[ -n "$DEB" ] && cp -f "$DEB" "$SHARE/pkg/"

ISO_ARGS=()
if [ "$MODE" = install ]; then
    DEFAULT_ISO=$(ls -t "$(pwd)"/fellowshipos-desktop-amd64-*.hybrid.iso 2>/dev/null | head -1 || true)
    ISO=${FELLOWSHIP_ISO:-$DEFAULT_ISO}
    [ -n "$ISO" ] && [ -f "$ISO" ] || { echo "no desktop ISO found (set FELLOWSHIP_ISO)" >&2; exit 1; }
    rm -f "$DISK" "$VARS"
    qemu-img create -f qcow2 "$DISK" "${FELLOWSHIP_DISK_SIZE:-32G}" >/dev/null
    cp -f "$OVMF_VARS" "$VARS"
    ISO_ARGS=(
        -drive "file=$ISO,media=cdrom,readonly=on,if=none,id=cd0"
        -device "ide-cd,drive=cd0,bootindex=1"
    )
    echo "install: fresh ${FELLOWSHIP_DISK_SIZE:-32G} disk at $DISK from $(basename "$ISO")"
else
    [ -f "$DISK" ] || { echo "no installed disk at $DISK; run '$0 install $WORK' first" >&2; exit 1; }
    [ -f "$VARS" ] || cp -f "$OVMF_VARS" "$VARS"
    echo "boot: booting installed disk $DISK (ISO ejected)"
fi

OUTPUTS=${FELLOWSHIP_OUTPUTS:-1}
DISPLAY_KIND=${FELLOWSHIP_VM_DISPLAY:-gtk}
GPU=(-vga none -device "virtio-gpu-pci,xres=1920,yres=1080,max_outputs=$OUTPUTS")
if [ "${FELLOWSHIP_GL:-0}" = 1 ]; then
    GPU=(-vga none -device "virtio-gpu-gl-pci,xres=1920,yres=1080,max_outputs=$OUTPUTS")
    if [ "$DISPLAY_KIND" = gtk ]; then
        DISPLAY_KIND="gtk,gl=on"
    elif [ "$DISPLAY_KIND" = none ]; then
        DISPLAY_KIND="egl-headless"
    fi
fi

AUDIO=(-audiodev "${FELLOWSHIP_AUDIO:-none},id=snd0" -device ich9-intel-hda -device hda-duplex,audiodev=snd0)

exec qemu-system-x86_64 \
    -machine q35,vmport=off \
    -enable-kvm -cpu host -m 8192 -smp 4 \
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,file=$VARS" \
    -drive "file=$DISK,format=qcow2,if=none,id=hd0" \
    -device "virtio-blk-pci,drive=hd0,bootindex=2" \
    "${ISO_ARGS[@]}" \
    "${GPU[@]}" \
    "${AUDIO[@]}" \
    -device qemu-xhci -device usb-kbd -device usb-tablet \
    -nic user,model=virtio-net-pci \
    -virtfs "local,path=$SHARE,mount_tag=hostshare,security_model=mapped-xattr,id=hostshare" \
    -display "$DISPLAY_KIND" \
    -serial "unix:$SERIAL,server=on,wait=off" \
    -monitor "unix:$MONITOR,server=on,wait=off" \
    ${FELLOWSHIP_QEMU_EXTRA:-}
