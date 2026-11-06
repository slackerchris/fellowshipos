#!/bin/bash
# Install FellowshipOS into a VM with 4 data disks, then boot it.
#   vm.sh install   Install through the ISO's boot menu, with timed keystrokes
#   vm.sh boot      Boot the installed disk (monitor socket in the work dir)
#   vm.sh live      Boot the ISO's live session
#   vm.sh clean     Delete the VM disks
set -e

cd "$(dirname "$0")"

ISO="$(realpath ../../../live-image-amd64.hybrid.iso)"
WORK="${FELLOWSHIP_VM_DIR:-/tmp/fellowship-vm}"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS="/usr/share/OVMF/OVMF_VARS_4M.fd"

mkdir -p "$WORK"

qemu() {
    # Listed first so the OS disk is /dev/vda, the installer's default choice
    local disks=(-drive "file=$WORK/os.qcow2,if=none,id=os"
                 -device "virtio-blk-pci,drive=os,serial=FELLOWSHIP-OS,bootindex=1") i
    for i in 1 2 3 4; do
        disks+=(-drive "file=$WORK/data$i.qcow2,if=none,id=data$i"
                -device "virtio-blk-pci,drive=data$i,serial=FELLOWSHIP-DATA-$i")
    done
    qemu-system-x86_64 \
        -enable-kvm -cpu host -m 4096 -smp 2 \
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,file=$WORK/vars.fd" \
        "${disks[@]}" \
        -nic user,model=virtio-net-pci \
        -display none \
        -serial "file:$WORK/serial-$MODE.log" \
        -monitor "unix:$WORK/monitor.sock,server,nowait" \
        "$@"
}

create_disks() {
    cp "$OVMF_VARS" "$WORK/vars.fd"
    qemu-img create -q -f qcow2 "$WORK/os.qcow2" 20G
    for i in 1 2 3 4; do
        qemu-img create -q -f qcow2 "$WORK/data$i.qcow2" 2G
    done
}

boot_iso() {
    qemu -drive "file=$ISO,media=cdrom,if=none,id=cd" \
        -device "ide-cd,drive=cd,bootindex=0" "$@"
}

keys() {
    local key
    for key in "$@"; do
        echo "sendkey $key" | socat - "UNIX-CONNECT:$WORK/monitor.sock" > /dev/null
        sleep 0.2
    done
}

type_line() {
    local i
    for ((i = 0; i < ${#1}; i++)); do
        keys "${1:i:1}"
    done
    keys ret
}

MODE="${1:-}"
case "$MODE" in
    install)
        create_disks
        boot_iso -no-reboot &
        VM=$!

        # Timed keystrokes through the real boot menu, so the shipped preseed
        # is what gets tested. Delays are generous and assume KVM.
        sleep 15; keys i                        # GRUB: Start installer
        sleep 70; keys ret; sleep 3; keys ret; sleep 3; keys ret   # Language, location, keyboard
        sleep 60; type_line tester              # Full name
        sleep 3;  keys ret                      # Username
        sleep 3;  type_line vmtest              # Password
        sleep 3;  type_line vmtest
        sleep 15; keys ret                      # Time zone
        sleep 15; keys ret                      # Guided, entire disk
        sleep 3;  keys ret                      # First disk: the OS disk
        sleep 3;  keys ret                      # All files in one partition
        sleep 3;  keys ret                      # Finish partitioning
        sleep 3;  keys tab ret                  # Write changes
        sleep 240; keys ret                     # Installation complete
        wait "$VM"
        ;;
    boot)
        qemu
        ;;
    live)
        create_disks
        boot_iso
        ;;
    clean)
        rm -rf "$WORK"
        ;;
    *)
        sed -n '2,6p' "$0"
        exit 1
        ;;
esac
