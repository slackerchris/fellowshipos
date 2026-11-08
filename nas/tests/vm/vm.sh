#!/bin/bash
# Install FellowshipOS into a VM with data disks, then boot it.
#   vm.sh install   Install through the ISO's boot menu, with timed keystrokes
#   vm.sh boot      Boot the installed disk (monitor socket in the work dir);
#                   extra arguments go to QEMU, such as a second network card
#   vm.sh live      Boot the ISO's live session
#   vm.sh nas       Boot the ISO's graphical NAS installer, reachable from
#                   this machine at http://localhost:8088
#   vm.sh clean     Delete the VM disks
# FELLOWSHIP_DATA_DISKS="2G 2G 4G" sets the data disks, one size each (default
# four of 2G; "" for none). FELLOWSHIP_DISK_ORDER="4 3 2 1" attaches them in
# a different order.
# FELLOWSHIP_KEEP_DATA=1 keeps the data disks through an install, to test a reinstall.
# FELLOWSHIP_OS_SIZE=500G sets the system disk's size (default 20G); disks are sparse.
# FELLOWSHIP_KEEP_OS=1 keeps the system disk too, to test installing over an old install.
set -e

cd "$(dirname "$0")"

ISO="$(realpath ../../../live-image-amd64.hybrid.iso)"
WORK="${FELLOWSHIP_VM_DIR:-/tmp/fellowship-vm}"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS="/usr/share/OVMF/OVMF_VARS_4M.fd"

mkdir -p "$WORK"
read -ra DATA_SIZES <<< "${FELLOWSHIP_DATA_DISKS-2G 2G 2G 2G}"

qemu() {
    # Listed first so the OS disk is /dev/vda, the installer's default choice
    local disks=(-drive "file=$WORK/os.qcow2,if=none,id=os"
                 -device "virtio-blk-pci,drive=os,serial=FELLOWSHIP-OS,bootindex=1") i
    for i in ${FELLOWSHIP_DISK_ORDER:-$(seq ${#DATA_SIZES[@]})}; do
        disks+=(-drive "file=$WORK/data$i.qcow2,if=none,id=data$i"
                -device "virtio-blk-pci,id=dev$i,drive=data$i,serial=FELLOWSHIP-DATA-$i")
    done
    qemu-system-x86_64 \
        -enable-kvm -cpu host -m 4096 -smp 2 \
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,file=$WORK/vars.fd" \
        "${disks[@]}" \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8443-:443,hostfwd=tcp:127.0.0.1:8088-:80 \
        -display none \
        -serial "file:$WORK/serial-$MODE.log" \
        -monitor "unix:$WORK/monitor.sock,server,nowait" \
        "$@"
}

create_disks() {
    if [ -z "$FELLOWSHIP_KEEP_OS" ]; then
        cp "$OVMF_VARS" "$WORK/vars.fd"
        qemu-img create -q -f qcow2 "$WORK/os.qcow2" "${FELLOWSHIP_OS_SIZE:-20G}"
    fi
    [ -n "$FELLOWSHIP_KEEP_DATA" ] && return
    rm -f "$WORK"/data*.qcow2
    for i in "${!DATA_SIZES[@]}"; do
        qemu-img create -q -f qcow2 "$WORK/data$((i + 1)).qcow2" "${DATA_SIZES[i]}"
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
        sleep 15
        # Kept data disks have free space, so the menu gains a first entry
        # ("largest continuous free space") and the default moves onto it
        [ -n "$FELLOWSHIP_KEEP_DATA" ] && keys down
        keys ret                                # Guided, entire disk
        sleep 3;  keys ret                      # First disk: the OS disk
        sleep 3;  keys ret                      # All files in one partition
        sleep 3;  keys ret                      # Finish partitioning
        sleep 3;  keys tab ret                  # Write changes
        sleep 240; keys ret                     # Installation complete
        # A keystroke that lands on the wrong screen leaves the installer
        # waiting forever, so give up rather than hang
        for _ in $(seq 120); do
            kill -0 "$VM" 2> /dev/null || break
            sleep 5
        done
        if kill -0 "$VM" 2> /dev/null; then
            echo "Install did not finish; the screen is waiting for input" >&2
            kill "$VM"
            exit 1
        fi
        wait "$VM"
        ;;
    boot)
        shift
        qemu "$@"
        ;;
    live)
        create_disks
        boot_iso
        ;;
    nas)
        create_disks
        boot_iso -no-reboot &
        VM=$!
        sleep 15; keys n                        # GRUB: Install FellowshipOS NAS
        wait "$VM"
        ;;
    clean)
        rm -rf "$WORK"
        ;;
    *)
        sed -n '2,15p' "$0"
        exit 1
        ;;
esac
