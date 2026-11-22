#!/bin/bash
# Boot the desktop image in a VM and photograph what it shows.
#
#   desktop/tests/vm.sh [output-dir]
#
# A live boot logs in as `user` on tty1 and the desktop starts on the panel by
# itself. This boots the ISO that way, waits for the session, asks QEMU for a
# screendump and writes it as PNG, then checks the picture is the desktop and
# not a console: it has to be mostly dark with the theme's gold in it.
#
# The machine has one display device, virtio-gpu, which is the one the
# compositor draws to; QEMU's default VGA would otherwise be the screen the
# picture is taken from.
#
# FELLOWSHIP_ISO points at another image; FELLOWSHIP_VM_DISPLAY=gtk opens the
# window on this machine instead of keeping it off screen. A VNC display
# (`vnc=127.0.0.1:10`) works too, but its screendumps came back black once the
# compositor owned the card; the default (`none`) does not. FELLOWSHIP_WAIT is
# how many seconds to wait for the session (default 240).
set -eu

cd "$(dirname "$0")/../.."
ISO=${FELLOWSHIP_ISO:-$(pwd)/fellowshipos-desktop-amd64-1.0.hybrid.iso}
OUT=${1:-${FELLOWSHIP_TEST_DIR:-$HOME/.cache/fellowship-tests}/desktop-vm}
WORK=$OUT/work
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.fd
MONITOR=$WORK/monitor.sock

[ -f "$ISO" ] || { echo "no image at $ISO" >&2; exit 1; }
[ -f "$OVMF_CODE" ] || { echo "needs OVMF at $OVMF_CODE" >&2; exit 1; }
mkdir -p "$WORK"
cp -f "$OVMF_VARS" "$WORK/vars.fd"
rm -f "$WORK/serial.log"

# The live kernel and initramfs, extracted once
if [ ! -f "$WORK/vmlinuz" ] || [ ! -f "$WORK/initrd.img" ]; then
    file=$(isoinfo -i "$ISO" -R -f 2>/dev/null | grep -m1 '^/live/vmlinuz-') || true
    [ -n "$file" ] || { echo "no live kernel in $ISO" >&2; exit 1; }
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/vmlinuz" 2>/dev/null
    file=$(isoinfo -i "$ISO" -R -f 2>/dev/null | grep -m1 '^/live/initrd.img-') || true
    [ -n "$file" ] || { echo "no live initramfs in $ISO" >&2; exit 1; }
    xorriso -osirrox on -indev "$ISO" -extract "$file" "$WORK/initrd.img" 2>/dev/null
fi

qemu-system-x86_64 \
    -machine pc,vmport=off \
    -enable-kvm -cpu host -m 4096 -smp 2 \
    -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd.img" \
    -append "boot=live components quiet splash console=ttyS0" \
    -drive "file=$ISO,if=none,id=isorom,readonly=on" \
    -device "virtio-blk-pci,drive=isorom,serial=FELLOWSHIP-ISO" \
    -vga none -device virtio-gpu-pci \
    -device qemu-xhci -device usb-kbd -device usb-tablet \
    -nic user,model=virtio-net-pci \
    -display "${FELLOWSHIP_VM_DISPLAY:-none}" \
    -serial "file:$WORK/serial.log" \
    -monitor "unix:$MONITOR",server,nowait \
    -no-reboot &
VM=$!
trap 'kill $VM 2>/dev/null || true' EXIT

keys() {
    local key
    for key in "$@"; do
        echo "sendkey $key" | socat - "UNIX-CONNECT:$MONITOR" > /dev/null 2>&1 || true
        sleep 0.2
    done
}

# The ISO's own kernel and initramfs are booted with the live command line, so
# the check does not depend on pressing a hotkey at the right moment at the
# boot menu: doing that booted the graphical installer often enough to make
# this script lie about the desktop.


shot() { # file.ppm
    echo "screendump $1" | socat - "UNIX-CONNECT:$MONITOR" > /dev/null
    sleep 0.5
}

# The desktop is up when the screen looks like the theme. Give the boot, the
# autologin and the renderer the time they need.
DEADLINE=$(($(date +%s) + ${FELLOWSHIP_WAIT:-240}))
RESULT=no
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    sleep 6
    shot "$WORK/screen.ppm"
    [ -s "$WORK/screen.ppm" ] || continue
    if python3 - "$WORK/screen.ppm" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
px = im.load()
step = max(1, w // 160)
mean = gold = total = 0
for y in range(0, h, step):
    for x in range(0, w, step):
        r, g, b = px[x, y]
        mean += (r + g + b) / 3
        if 150 <= r <= 230 and 120 <= g <= 190 and 60 <= b <= 130 and r > g > b:
            gold += 1
        total += 1
mean /= total
print(f"mean {mean:.0f}/255, gold {gold}/{total} samples")
sys.exit(0 if mean < 110 and gold >= 3 else 1)
PY
    then
        RESULT=yes
        break
    fi
done

if [ -s "$WORK/screen.ppm" ]; then
python3 - "$WORK/screen.ppm" "$OUT/desktop.png" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGB").save(sys.argv[2])
PY
fi

if [ "$RESULT" = yes ]; then
    echo "Desktop: the session is on the panel. Picture: $OUT/desktop.png"
    exit 0
fi

echo "Desktop: nothing that looks like the session yet." >&2
echo "Picture: $OUT/desktop.png; serial log: $WORK/serial.log" >&2
exit 1
