#!/bin/bash
# Delete-array and service controls, proven from the desktop on a fresh install
# of a built ISO in a QEMU/KVM VM with five disposable virtio disks: four build
# tank, the fifth keeps a file and must come through untouched. Create, share,
# refuse, remove share, refuse a wrong name, delete, rebuild -- all through the
# desktop's own HTTPS API; the unrelated disk is prepared and read back on the
# NAS's own console.
#
# A container cannot prove create-from-desktop on a host whose udisks/udev
# probes freshly appeared SCSI disks: it holds them open against mdadm's
# exclusive open. The VM's own udev is the only one probing its disks, so this
# is the faithful seat.
#   nas/tests/vm/vm-delete-proof.sh <iso> [skip-install]
# Needs KVM, OVMF and the ISO; installs through the installer API first unless
# skip-install reuses an installed disk in FELLOWSHIP_VM_DIR.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ISO="$1"
WORK="${FELLOWSHIP_VM_DIR:-/tmp/fellowship-nas-delete}"
MON="${FELLOWSHIP_MONITOR:-/tmp/fellowship-nas-delete-monitor.sock}"
export FELLOWSHIP_MONITOR="$MON"
export FELLOWSHIP_SERIAL="$WORK/serial-remote.log"
export FELLOWSHIP_SHOTS="${FELLOWSHIP_SHOTS:-/tmp/nas-delete-shots}"
VM="$(cd "$(dirname "$0")" && pwd)/vm.sh"
D() { python3 "$HERE/desk-api.py" fnas "$@" | python3 -c 'import json,sys
head, _, rest = sys.stdin.read().partition("\n")
print(head)
try:
    inner = json.loads(rest)
    print(inner["output"] if isinstance(inner, dict) and "output" in inner else rest)
except Exception:
    print(rest)'; }
C() { python3 "$HERE/console.py" "$@"; }
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }
code() { head -n 1 <<< "$1"; }

echo "== boot the installer from the new ISO"
if [ "${2:-}" = skip-install ]; then
    pass "reusing the installed disk from the earlier install of this ISO"
else
    rm -rf "$WORK"; mkdir -p "$WORK"
    FELLOWSHIP_ISO="$ISO" FELLOWSHIP_VM_DIR="$WORK" FELLOWSHIP_DATA_DISKS="2G 2G 2G 2G 2G" \
        nohup bash "$VM" remote > /tmp/nas-delete-vm.log 2>&1 &
    python3 "$HERE/install-api.py" > /tmp/nas-delete-install.log 2>&1 \
        && pass "the ISO installs through the installer API" || { fail "the ISO installs through the installer API"; tail -n 5 /tmp/nas-delete-install.log; exit 1; }
fi

echo "== boot the installed system"
pkill -f "qemu-system-x86_64.*$WORK" || true
sleep 3
FELLOWSHIP_VM_DIR="$WORK" FELLOWSHIP_DATA_DISKS="2G 2G 2G 2G 2G" nohup bash "$VM" boot > /tmp/nas-delete-boot.log 2>&1 &
for _ in $(seq 90); do
    curl -sk -o /dev/null https://127.0.0.1:8443/ && break
    sleep 2
done
python3 "$HERE/desk-api.py" login > /tmp/nas-delete-login.log 2>&1
grep -q '^200' /tmp/nas-delete-login.log && pass "the admin signs in to the installed desktop" || { fail "the admin signs in to the installed desktop"; cat /tmp/nas-delete-login.log; exit 1; }

echo "== the fifth disk keeps a file, prepared on the NAS's own console"
sleep 5
C type root; C enter; sleep 5
C type 'root verify pw 2'; C enter; sleep 5
C cmd 'mkfs.ext4 -q /dev/vdf && mkdir -p /mnt/unrelated && mount /dev/vdf /mnt/unrelated && echo keep me > /mnt/unrelated/keep.txt && blkid -p -s UUID -o value /dev/vdf > /tmp/uuid-before && echo prep-done'; sleep 12
C shot prep

echo "== create tank through the desktop"
D share delete docs --confirm docs > /dev/null 2>&1 || true
D pool delete tank --confirm tank > /dev/null 2>&1 || true
out=$(D pool create tank raid5 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-1 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-2 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-3 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-4 --engine mdadm --filesystem ext4 --confirm "erase 4 disks")
[ "$(code "$out")" = 200 ] && pass "the desktop creates tank on four disposable disks" || fail "the desktop creates tank on four disposable disks: $out"
online=no
for _ in $(seq 150); do
    out=$(D --json pool list)
    grep -q '"health": "ONLINE"' <<< "$out" && { online=yes; break; }
    sleep 2
done
[ "$online" = yes ] && pass "the new array finishes building and reads ONLINE" || fail "the new array finishes building and reads ONLINE"

echo "== a share blocks delete and names itself"
out=$(D share create docs tank --everyone --smb)
[ "$(code "$out")" = 200 ] && pass "a share is made on tank" || fail "a share is made on tank: $out"
out=$(D pool delete tank)
[ "$(code "$out")" != 200 ] && grep -q docs <<< "$out" && grep -q Shares <<< "$out" \
    && pass "delete refuses and names the share and where to remove it" || fail "delete refuses and names the share and where to remove it: $out"

echo "== with the share gone, delete lists the disks and needs the name"
out=$(D share delete docs --confirm docs)
[ "$(code "$out")" = 200 ] && pass "the share is removed" || fail "the share is removed: $out"
out=$(D pool delete tank --confirm nope)
[ "$(code "$out")" != 200 ] && grep -q virtio-FELLOWSHIP-DATA-1 <<< "$out" && grep -q virtio-FELLOWSHIP-DATA-4 <<< "$out" \
    && pass "the refusal lists the exact member disks" || fail "the refusal lists the exact member disks: $out"
out=$(D pool delete tank --confirm tank)
[ "$(code "$out")" = 200 ] && pass "typing the array name deletes it" || fail "typing the array name deletes it: $out"
out=$(D --json pool list)
grep -q '\[\]' <<< "$out" && pass "the pool is gone" || fail "the pool is gone: $out"
out=$(D --json disk list)
python3 -c 'import json,sys
disks = {d["path"]: d["state"] for d in json.load(sys.stdin)}
sys.exit(0 if all(disks.get(f"/dev/vd{c}") == "empty" for c in "bcde") else 1)' <<< "$(tail -n +2 <<< "$out")" \
    && pass "the four disks read as empty again" || fail "the four disks read as empty again"

echo "== the unrelated disk is untouched"
C cmd 'cat /mnt/unrelated/keep.txt; blkid -p -s UUID -o value /dev/vdf; cat /tmp/uuid-before; echo check-done'; sleep 8
C shot verify

echo "== the same four disks build a new array"
out=$(D pool create tank raid5 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-1 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-2 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-3 /dev/disk/by-id/virtio-FELLOWSHIP-DATA-4 --engine mdadm --filesystem ext4 --confirm "erase 4 disks")
[ "$(code "$out")" = 200 ] && pass "the desktop rebuilds tank from the same four disks" || fail "the desktop rebuilds tank from the same four disks: $out"
online=no
for _ in $(seq 150); do
    out=$(D --json pool list)
    grep -q '"health": "ONLINE"' <<< "$out" && { online=yes; break; }
    sleep 2
done
[ "$online" = yes ] && pass "the rebuilt array reads ONLINE" || fail "the rebuilt array reads ONLINE"

echo "== service controls from the desktop"
out=$(D --json service list)
grep -q '"name": "smb"' <<< "$out" && grep -q '"name": "ssh"' <<< "$out" \
    && pass "the desktop lists the controlled services with state" || fail "the desktop lists the controlled services with state: $out"
out=$(D service stop smb)
[ "$(code "$out")" = 200 ] && pass "the owner stops Samba" || fail "the owner stops Samba: $out"
out=$(D --json health)
grep -q service-smb <<< "$out" && fail "Health stays quiet about an intentional stop" || pass "Health stays quiet about an intentional stop"
out=$(D service start smb)
[ "$(code "$out")" = 200 ] && pass "the owner starts Samba again" || fail "the owner starts Samba again: $out"
out=$(D service stop ssh)
[ "$(code "$out")" = 200 ] && pass "the owner stops the SSH daemon" || fail "the owner stops the SSH daemon: $out"
out=$(D --json health)
grep -qE 'service-ssh.service|service-sftp' <<< "$out" && fail "Health stays quiet about the daemon stop" || pass "Health stays quiet about the daemon stop"
out=$(D service start ssh)
[ "$(code "$out")" = 200 ] && pass "the owner starts the SSH daemon again" || fail "the owner starts the SSH daemon again: $out"
out=$(D service stop web)
[ "$(code "$out")" != 200 ] && pass "the web server is not controllable from the desktop" || fail "the web server is not controllable from the desktop: $out"

echo "== done; stopping the VM"
pkill -f "qemu-system-x86_64.*$WORK" || true
