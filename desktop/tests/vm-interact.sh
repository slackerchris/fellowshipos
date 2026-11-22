#!/bin/bash
# Boot the desktop image interactively, for the responsiveness work.
#
#   desktop/tests/vm-interact.sh [output-dir]
#
# Like vm.sh this boots the ISO's own kernel and initramfs, but the machine
# is left open to drive: a serial console socket (a login on it is a root
# shell away, and the live user needs no password for sudo), a QEMU monitor
# socket, and a 9p share carrying the freshly built desktop package and the
# measurement scripts. vm-measure.py drives all of this; use it by hand when
# a window is what you want.
#
# Two GPUs, one switch:
#   default         virtio-gpu-pci, Mesa renders on the CPU (llvmpipe);
#                   QEMU's screendump works, so vm.sh-style photos work too
#   FELLOWSHIP_GL=1 virtio-gpu-gl-pci on the host's own GL, through
#                   egl-headless (no window needed); the guest uses the
#                   Mesa virtio driver instead of llvmpipe
#
#   FELLOWSHIP_VM_DISPLAY   QEMU display (default: none, or egl-headless
#                           with FELLOWSHIP_GL=1; `gtk` or `gtk,gl=on` show
#                           the console for watching by hand)
#   FELLOWSHIP_VIDEO        kernel video= argument (default 1920x1080; empty
#                           lets the driver pick)
#   FELLOWSHIP_OUTPUTS      number of virtual DRM outputs (default 1; set 2
#                           for multi-monitor testing)
#   FELLOWSHIP_AUDIO        QEMU audiodev backend (default none; set pipewire
#                           or pa when interactive to hear guest sound)
#   FELLOWSHIP_ISO          the desktop image (defaults to newest desktop ISO)
#   FELLOWSHIP_WAIT         seconds allowed for the boot (default 300)
#   FELLOWSHIP_QEMU_EXTRA   more QEMU arguments, split on spaces: a second
#                           network card on a private segment shared with a
#                           NAS VM, a USB stick (vm-strider.py uses both)
set -eu

cd "$(dirname "$0")/../.."
DEFAULT_ISO=$(ls -t "$(pwd)"/fellowshipos-desktop-amd64-*.hybrid.iso 2>/dev/null | head -1 || true)
ISO=${FELLOWSHIP_ISO:-$DEFAULT_ISO}
OUT=${1:-/tmp/fellowship-desktop-vm-interact}
WORK=$OUT/work
MONITOR=$OUT/monitor.sock
SERIAL=$OUT/serial.sock
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd

[ -f "$ISO" ] || { echo "no image at $ISO" >&2; exit 1; }
[ -f "$OVMF_CODE" ] || { echo "needs OVMF at $OVMF_CODE" >&2; exit 1; }
mkdir -p "$WORK" "$OUT/share/pkg" "$OUT/share/out"
cp -f "$OVMF_VARS" "$WORK/vars.fd"
rm -f "$SERIAL" "$MONITOR"

# The newest desktop package, if one has been built: vm-measure.py hot-swaps
# it into the running guest without rebuilding the image.
DEB=$(ls -t config/packages.chroot/fellowship-desktop_*.deb 2>/dev/null | head -1 || true)
if [ -n "$DEB" ]; then
    cp -f "$DEB" "$OUT/share/pkg/"
    echo "sharing $(basename "$DEB") for the guest"
fi

# Re-extract whenever the image is newer than the copy: testing a rebuilt
# ISO with a cached old kernel proves nothing about the image on disk.
if [ ! -f "$WORK/vmlinuz" ] || [ ! -f "$WORK/initrd.img" ] || [ "$ISO" -nt "$WORK/vmlinuz" ]; then
    file=$(isoinfo -i "$ISO" -R -f 2>/dev/null | grep -m1 '^/live/vmlinuz-') || true
    [ -n "$file" ] || { echo "no live kernel in $ISO" >&2; exit 1; }
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/vmlinuz" 2>/dev/null
    file=$(isoinfo -i "$ISO" -R -f 2>/dev/null | grep -m1 '^/live/initrd.img-') || true
    [ -n "$file" ] || { echo "no live initramfs in $ISO" >&2; exit 1; }
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/initrd.img" 2>/dev/null
fi

OUTPUTS=${FELLOWSHIP_OUTPUTS:-1}
GPU=(-vga none -device "virtio-gpu-pci,xres=1920,yres=1080,max_outputs=$OUTPUTS")
DISPLAY_KIND="${FELLOWSHIP_VM_DISPLAY:-none}"
VIDEO_ARG=${FELLOWSHIP_VIDEO-video=Virtual-1:1920x1080}
if [ "${FELLOWSHIP_GL:-0}" = 1 ]; then
    GPU=(-vga none -device "virtio-gpu-gl-pci,xres=1920,yres=1080,max_outputs=$OUTPUTS")
    [ "$DISPLAY_KIND" = none ] && DISPLAY_KIND=egl-headless
fi
AUDIO=(-audiodev "${FELLOWSHIP_AUDIO:-none},id=snd0" -device ich9-intel-hda -device hda-duplex,audiodev=snd0)

if [ -f "$OUT/vm.pid" ] && kill -0 "$(cat "$OUT/vm.pid")" 2>/dev/null; then
    echo "already running (pid $(cat "$OUT/vm.pid")): $OUT"; exit 0
fi

APPEND="boot=live components quiet splash console=ttyS0,115200"
[ -n "$VIDEO_ARG" ] && APPEND="$APPEND $VIDEO_ARG"

qemu-system-x86_64 \
    -machine pc,vmport=off \
    -enable-kvm -cpu host -m 8192 -smp 4 \
    -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd.img" \
    -append "$APPEND" \
    -drive "file=$ISO,if=none,id=isorom,readonly=on" \
    -device "virtio-blk-pci,drive=isorom,serial=FELLOWSHIP-ISO" \
    "${GPU[@]}" \
    "${AUDIO[@]}" \
    -device qemu-xhci -device usb-kbd -device usb-tablet \
    -nic user,model=virtio-net-pci \
    -virtfs "local,path=$OUT/share,mount_tag=hostshare,security_model=mapped-xattr,id=hostshare" \
    -display "$DISPLAY_KIND" \
    -serial "unix:$SERIAL,server=on,wait=off" \
    -monitor "unix:$MONITOR",server,nowait \
    ${FELLOWSHIP_QEMU_EXTRA:-} \
    -daemonize -pidfile "$OUT/vm.pid"

echo "booting: serial $SERIAL ; monitor $MONITOR ; share $OUT/share"
echo "login on the serial console as user/live; stop with kill \$(cat $OUT/vm.pid)"
