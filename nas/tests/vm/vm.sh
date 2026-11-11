#!/bin/bash
# Install FellowshipOS into a VM with data disks, then boot it.
#   vm.sh install   Install through the ISO's boot menu, with timed keystrokes
#   vm.sh boot      Boot the installed disk (monitor socket in the work dir);
#                   extra arguments go to QEMU, such as a second network card
#   vm.sh live      Boot the ISO's live session
#   vm.sh nas       Boot the NAS image's graphical installer
#   vm.sh multi     Boot the multi-purpose image's graphical installer
#   vm.sh remote    Boot the NAS image's installer served over HTTPS on
#                   127.0.0.1:8443, for a browser here instead of the window
#   vm.sh clean     Delete the VM disks
# FELLOWSHIP_EDITION picks the image (nas or multi, default nas);
# FELLOWSHIP_ISO points at another ISO file.
# The installer's page is served on the machine's own screen. To reach it from
# this machine, add fellowship.installer.remote=:443 to the boot entry (press e
# at the menu); the console then prints the certificate's fingerprint and the
# pairing code, which the serial log keeps.
# FELLOWSHIP_VM_DISPLAY=gtk shows the machine's screen in a window instead of
# hiding it (qemu's default here is none); use it to click through the
# graphical installer by hand.
# FELLOWSHIP_DATA_DISKS="2G 2G 4G" sets the data disks, one size each (default
# four of 2G; "" for none). FELLOWSHIP_DISK_ORDER="4 3 2 1" attaches them in
# a different order.
# FELLOWSHIP_KEEP_DATA=1 keeps the data disks through an install, to test a reinstall.
# FELLOWSHIP_OS_SIZE=500G sets the system disk's size (default 20G); disks are sparse.
# FELLOWSHIP_KEEP_OS=1 keeps the system disk too, to test installing over an old install.
set -e

cd "$(dirname "$0")"

ISO="${FELLOWSHIP_ISO:-$(realpath ../../../fellowshipos-${FELLOWSHIP_EDITION:-nas}-amd64-1.0.hybrid.iso)}"
WORK="${FELLOWSHIP_VM_DIR:-/tmp/fellowship-vm}"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS="/usr/share/OVMF/OVMF_VARS_4M.fd"

mkdir -p "$WORK"
read -ra DATA_SIZES <<< "${FELLOWSHIP_DATA_DISKS-2G 2G 2G 2G}"

qemu() {
    # Listed first so the OS disk is /dev/vda, the installer's default choice
    # vmport is off because its vmmouse puts the guest's cursor nowhere near the
    # window's pointer. Turning i8042 off to leave a usb tablet as the only
    # pointer is worse: the live kernel does not bring up the usb controller, so
    # the guest ends up with no input at all. The window therefore stays on
    # relative input; drive the installer with vm.sh remote instead.
    local disks=(-drive "file=$WORK/os.qcow2,if=none,id=os"
                 -device "virtio-blk-pci,drive=os,serial=FELLOWSHIP-OS,bootindex=1") i
    for i in ${FELLOWSHIP_DISK_ORDER:-$(seq ${#DATA_SIZES[@]})}; do
        disks+=(-drive "file=$WORK/data$i.qcow2,if=none,id=data$i"
                -device "virtio-blk-pci,id=dev$i,drive=data$i,serial=FELLOWSHIP-DATA-$i")
    done
    qemu-system-x86_64 \
        -machine pc,vmport=off \
        -enable-kvm -cpu host -m 4096 -smp 2 \
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,file=$WORK/vars.fd" \
        "${disks[@]}" \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:8443-:443,hostfwd=tcp:127.0.0.1:8088-:80 \
        -display "${FELLOWSHIP_VM_DISPLAY:-none}" \
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

# The ISO's own kernel and initramfs, so a boot can add to the kernel command
# line without editing the boot menu by hand. The image is attached as a disk
# instead of a disc: live-boot finds the live system on a block device, but not
# on a "medium" it can't see from the initramfs.
extract_live() {
    local file
    [ -f "$WORK/vmlinuz" ] && [ -f "$WORK/initrd.img" ] && return
    file=$(isoinfo -i "$ISO" -R -f 2> /dev/null | grep -m1 '^/live/vmlinuz-') || return 1
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/vmlinuz" 2> /dev/null
    file=$(isoinfo -i "$ISO" -R -f 2> /dev/null | grep -m1 '^/live/initrd.img-') || return 1
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/initrd.img" 2> /dev/null
}

boot_live_with() {
    extract_live
    # console=ttyS0 last, so /dev/console is the serial port and what the
    # installer prints there (the certificate's fingerprint and the pairing
    # code) lands in the serial log instead of behind the kiosk on tty1
    qemu -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd.img" \
        -append "boot=live components quiet splash $1 console=ttyS0" \
        -drive "file=$ISO,if=none,id=isorom,readonly=on" \
        -device "virtio-blk-pci,drive=isorom,serial=FELLOWSHIP-ISO"
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

# GRUB can take a while to draw the menu, and how long depends on the machine, so
# send the hotkey a few times rather than guessing one moment. Once the kernel
# starts the key lands on the console, where it does nothing.
boot_with_hotkey() {
    local i
    for i in $(seq 15); do
        sleep 2
        keys "$1"
    done
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
        boot_with_hotkey n                # GRUB: Install FellowshipOS NAS
        wait "$VM"
        ;;
    multi)
        create_disks
        boot_iso -no-reboot &
        VM=$!
        boot_with_hotkey m                # GRUB: Install FellowshipOS Multi-purpose
        wait "$VM"
        ;;
    remote)
        # The same page the kiosk shows, over HTTPS on every interface, so it
        # can be used from this machine's browser. The console prints the
        # certificate's fingerprint and the pairing code. Port 443 is the one
        # qemu forwards to 127.0.0.1:8443 here.
        create_disks
        boot_live_with "fellowship.installer=nas fellowship.installer.remote=:443"
        ;;
    clean)
        rm -rf "$WORK"
        ;;
    *)
        sed -n '2,16p' "$0"
        exit 1
        ;;
esac
