#!/bin/bash
# Boot the disk vm-install-drive.sh installed, headless, with a QMP socket for
# vm-qmp.py: the first-boot half of the milestone 2 gate, driven step by step.
#
#   desktop/tests/vm-installed.sh [work-dir]     # starts the VM in the background
#   desktop/tests/vm-qmp.py WORK/qmp.sock shot WORK/greeter.png
#
# No ISO is attached: the machine boots from its own disk through the UEFI
# variables the install left. The serial log is WORK/boot-serial.log.
set -euo pipefail
cd "$(dirname "$0")/../.."

# On the main drive, not /tmp: /tmp is RAM here, and a 32G disk filling it
# stopped a run on 2026-11-22
WORK=${1:-$HOME/Git/fellowship/build/vm}
DISK=$WORK/disk.qcow2
VARS=$WORK/ovmf-vars.fd
QMP=$WORK/qmp.sock
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
[ -f "$DISK" ] && [ -f "$VARS" ] || { echo "no installed disk in $WORK: run vm-install-drive.sh" >&2; exit 1; }
rm -f "$QMP"

qemu-system-x86_64 \
    -machine q35,vmport=off -enable-kvm -cpu host -m 8192 -smp 4 \
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,file=$VARS" \
    -drive "file=$DISK,format=qcow2,if=none,id=hd0" -device virtio-blk-pci,drive=hd0,bootindex=1 \
    -vga none -device virtio-gpu-pci,xres=1920,yres=1080 \
    -device qemu-xhci -device usb-kbd -device usb-tablet \
    -audiodev none,id=snd0 -device ich9-intel-hda -device hda-duplex,audiodev=snd0 \
    -nic user,model=virtio-net-pci \
    -display none -serial "file:$WORK/boot-serial.log" \
    -qmp "unix:$QMP,server=on,wait=off" \
    -daemonize -pidfile "$WORK/qemu.pid"
for _ in $(seq 50); do [ -S "$QMP" ] && break; sleep 0.1; done
echo "booting $DISK (pid $(cat "$WORK/qemu.pid")); QMP at $QMP"
