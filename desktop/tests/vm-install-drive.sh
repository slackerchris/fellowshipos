#!/bin/bash
# Install the desktop image on a blank UEFI disk, unattended, through the
# installer's own remote mode — the milestone 2 install gate without a person
# at the screen.
#
#   desktop/tests/vm-install-drive.sh [work-dir]
#
# Boots the ISO's own kernel with `fellowship.installer=desktop
# fellowship.installer.remote=:443`, forwards the page to localhost:8443, reads
# the pairing code from the serial log, and installs over the page's API the
# way the browser would: pair, read the machine, install to the blank disk,
# follow the progress. Leaves the installed disk and its UEFI variables in the
# work directory for `desktop/tests/vm-install.sh boot` or
# `desktop/tests/vm-firstboot.py`.
#
# The account is a throwaway for this VM only: user `tester`, password
# `tester pw 1` (the same style as strider-vm-test.sh's alice).
#
# FELLOWSHIP_ISO picks the image (default: the newest desktop ISO here).
# FELLOWSHIP_INSTALL_APPEND adds to the kernel command line (for an image
# built before ufw stood aside for remote installs, `systemd.mask=ufw.service`).
set -euo pipefail
cd "$(dirname "$0")/../.."

# On the main drive, not /tmp: /tmp is RAM here, and a 32G disk filling it
# stopped a run on 2026-11-22
WORK=${1:-$HOME/Git/fellowship/build/vm}
ISO=${FELLOWSHIP_ISO:-$(ls -t "$(pwd)"/fellowshipos-desktop-amd64-*.hybrid.iso | head -1)}
DISK=$WORK/disk.qcow2
VARS=$WORK/ovmf-vars.fd
SERIAL=$WORK/install-serial.log
JAR=$WORK/cookies
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
PORT=${FELLOWSHIP_INSTALL_PORT:-8443}
URL=https://127.0.0.1:$PORT

[ -f "$ISO" ] || { echo "no desktop ISO" >&2; exit 1; }
mkdir -p "$WORK"
rm -f "$DISK" "$VARS" "$SERIAL" "$JAR" "$WORK/vmlinuz" "$WORK/initrd.img"
qemu-img create -f qcow2 "$DISK" 32G >/dev/null
cp -f "$OVMF_VARS" "$VARS"
for part in vmlinuz initrd.img; do
    # grep stops reading at its match, so isoinfo's broken pipe is expected
    file=$( (isoinfo -i "$ISO" -R -f 2>/dev/null || true) | grep -m1 "^/live/$part-" || true)
    [ -n "$file" ] || { echo "no /live/$part in $ISO" >&2; exit 1; }
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/$part" 2>/dev/null
done
echo "install: $(basename "$ISO") onto a blank 32G disk in $WORK"

qemu-system-x86_64 \
    -machine q35,vmport=off -enable-kvm -cpu host -m 8192 -smp 4 \
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,file=$VARS" \
    -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd.img" \
    -append "boot=live components toram fellowship.installer=desktop fellowship.installer.remote=:443 console=ttyS0 ${FELLOWSHIP_INSTALL_APPEND:-}" \
    -drive "file=$ISO,media=cdrom,readonly=on,if=none,id=cd0" -device ide-cd,drive=cd0 \
    -drive "file=$DISK,format=qcow2,if=none,id=hd0" -device virtio-blk-pci,drive=hd0 \
    -vga none -device virtio-gpu-pci \
    -device qemu-xhci -device usb-kbd -device usb-tablet \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PORT-:443" \
    -display none -serial "file:$SERIAL" -no-reboot &
VM=$!
trap 'kill $VM 2>/dev/null || true' EXIT

CODE=
for _ in $(seq 180); do
    # The serial log appears a moment after QEMU starts
    CODE=$( (sed -n 's/.*type the pairing code \([^ ]*\).*/\1/p' "$SERIAL" 2>/dev/null || true) | tail -1 | tr -d '\r')
    [ -n "$CODE" ] && break
    kill -0 $VM 2>/dev/null || { echo "the VM stopped; see $SERIAL" >&2; exit 1; }
    sleep 2
done
[ -n "$CODE" ] || { echo "no pairing code in $SERIAL after 6 minutes" >&2; exit 1; }
FPR=$(sed -n "s/.*the certificate's SHA-256 is \([^ ]*\).*/\1/p" "$SERIAL" | tail -1 | tr -d '\r')
echo "installer up; certificate $FPR"

api() { # METHOD PATH [JSON]
    curl -sk --max-time 30 -b "$JAR" -c "$JAR" -X "$1" -H 'X-Fellowship-Installer: 1' \
        -H 'Content-Type: application/json' ${3:+--data "$3"} "$URL$2"
}
for _ in $(seq 30); do api GET /api/hello >/dev/null && break; sleep 2; done
api POST /api/pair "{\"code\":\"$CODE\"}" >/dev/null
MACHINE=$(api GET /api/machine)
echo "$MACHINE" | jq -e .disks >/dev/null || { echo "pairing failed: $MACHINE" >&2; exit 1; }
DISK_PATH=$(echo "$MACHINE" | jq -r '[.disks[] | select((.refused // "") == "")][0].path')
echo "disks offered: $(echo "$MACHINE" | jq -c '[.disks[] | {path, refused}]'); installing to $DISK_PATH"

BODY=$(jq -nc --arg disk "$DISK_PATH" --arg confirm "erase $(basename "$DISK_PATH")" '{
    disk: $disk, system: 0, hostname: "rc1-test", timezone: "America/New_York",
    user: "tester", full_name: "RC Tester", password: "tester pw 1", confirm: $confirm}')
ANSWER=$(api POST /api/install "$BODY")
echo "$ANSWER" | jq -e '.error' >/dev/null 2>&1 && { echo "install refused: $ANSWER" >&2; exit 1; }

LAST=-2
for _ in $(seq 720); do
    STATE=$(api GET /api/progress || true)
    PHASE=$(echo "$STATE" | jq -r '.phase // empty' 2>/dev/null || true)
    STEP=$(echo "$STATE" | jq -r '.step // -1' 2>/dev/null || true)
    if [ "$STEP" != "$LAST" ]; then
        echo "  [$PHASE] $(echo "$STATE" | jq -r --argjson s "$STEP" '.steps[$s] // ""')"
        LAST=$STEP
    fi
    case $PHASE in
        done) echo "install: done"; echo "$STATE" | jq -r '.log[-5:][]' > "$WORK/install-tail.log"; break ;;
        failed) echo "install FAILED: $(echo "$STATE" | jq -r .error)"; echo "$STATE" | jq -r '.log[]' > "$WORK/install-log.txt"; exit 1 ;;
    esac
    sleep 5
done
[ "$PHASE" = done ] || { echo "install did not finish in an hour" >&2; exit 1; }
echo "$STATE" | jq -r '.log[]' > "$WORK/install-log.txt"
kill $VM 2>/dev/null || true
wait $VM 2>/dev/null || true
trap - EXIT
echo "installed disk: $DISK (log: $WORK/install-log.txt)"
