#!/bin/bash
# Deleting an array from the desktop: create a RAID5 through the desktop's own
# API on four disposable disks, put a share on it, watch delete refuse and name
# the share, remove the share, watch delete demand the pool's name and list the
# disks, delete, and build again from the same four. A fifth disk with a file on
# it must come through untouched.
#
# The disks are scsi_debug: RAM-backed, real disk-type devices with stable
# by-id names, which the desktop's disk list requires (loop devices are type
# loop and invisible to it). The module is removed at the end, taking the disks
# with it, so nothing is left on the host.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/pool-delete.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/pool-delete.sh:/run.sh:ro" -v "$repo:/repo:ro" \
        -v /lib/modules:/lib/modules:ro \
        debian:trixie bash /run.sh --inside | tee "$out"
    grep -q '^PASS' "$out" && ! grep -q '^FAIL' "$out"
    exit
fi

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }

echo "== installing the NAS packages and mdadm"
sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources
echo "deb [trusted=yes] file:/repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
apt-get update -qq > /dev/null
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
dpkg-divert --quiet --local --rename --add /usr/bin/systemctl
printf '#!/bin/sh\nexit 0\n' > /usr/bin/systemctl && chmod +x /usr/bin/systemctl
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fellowship-nas-tools fellowship-nas-web \
    fellowship-nas-smb mdadm e2fsprogs curl kmod > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
mkdir -p "/lib/modules/$(uname -r)"
echo 200000 > /proc/sys/dev/raid/speed_limit_max

echo "== five disposable disks, one of them keeping a file"
cleanup() {
    umount /mnt/tank 2>/dev/null || true
    umount /mnt/unrelated 2>/dev/null || true
    mdadm --stop /dev/md/tank /dev/md127 /dev/md0 2>/dev/null || true
    rmmod scsi_debug 2>/dev/null || echo "warning: scsi_debug could not be removed"
}
trap cleanup EXIT
# No udev in a container: make the device nodes and the stable by-id names
# itself, from what the kernel reports, exactly as udev would on a real machine
before=$(ls /sys/block | sort)
modprobe scsi_debug num_tgts=5 max_luns=1 size_mb=256
# The host's udev runs blkid over freshly appeared disks; give it time to let
# go before mdadm opens them exclusively
sleep 5
after=$(ls /sys/block | sort)
new=$(comm -13 <(echo "$before") <(echo "$after"))
[ "$(wc -l <<< "$new")" -eq 5 ] || { echo "scsi_debug did not give five disks"; exit 1; }
mkdir -p /dev/disk/by-id
i=0
for n in $new; do
    i=$((i + 1))
    dev=$(cat "/sys/block/$n/dev")
    [ -e "/dev/$n" ] || mknod "/dev/$n" b "${dev%%:*}" "${dev##*:}"
    ln -sf "/dev/$n" "/dev/disk/by-id/scsi-TOUR-$i"
done
read -r d1 d2 d3 d4 unrelated <<< "$(echo $new | sed 's#\([^ ]*\)#/dev/\1#g')"
b1=/dev/disk/by-id/scsi-TOUR-1; b2=/dev/disk/by-id/scsi-TOUR-2
b3=/dev/disk/by-id/scsi-TOUR-3; b4=/dev/disk/by-id/scsi-TOUR-4
pass "the four disks have stable names"
mkfs.ext4 -q "$unrelated"
mkdir -p /mnt/unrelated; mount "$unrelated" /mnt/unrelated
echo "keep me" > /mnt/unrelated/keep.txt
uuid_before=$(blkid -s UUID -o value "$unrelated")

echo "== a separated NAS with an admin, and the desktop's own API"
fnas accounts init > /dev/null
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin > /dev/null
echo 'ann password 22' | fnas user create ann --password-stdin > /dev/null
fnas system certificate --if-missing > /dev/null
mkdir -p /run/fellowship-nas
/usr/lib/fellowship-nas-web/fellowship-nas-helper > /tmp/helper.log 2>&1 &
sleep 1
runuser -u fnas-web -- /usr/lib/fellowship-nas-web/fellowship-nas-web > /tmp/web.log 2>&1 &
for _ in $(seq 20); do curl -sk -o /dev/null https://127.0.0.1/ && break; sleep 0.5; done
api() {
    local token
    token=$(cat "$1.csrf" 2> /dev/null || true)
    curl -sk -b "$1" -c "$1" -X "$2" -H 'Content-Type: application/json' -H 'Origin: https://127.0.0.1' \
        -H "X-Fellowship-Token: $token" ${4:+-d "$4"} -w '\n%{http_code}' "https://127.0.0.1$3"
}
login() {
    local r
    r=$(api "$1" POST /api/login "{\"user\":\"$2\",\"password\":\"$3\"}")
    head -n -1 <<< "$r" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("csrf",""))
except Exception: pass' > "$1.csrf"
    echo "$r"
}
code() { tail -1 <<< "$1"; }
body() { head -n -1 <<< "$1"; }
K=$(mktemp); A=$(mktemp)
login "$K" kim 'admin password 1' > /dev/null
login "$A" ann 'ann password 22' > /dev/null
fnas_call() { api "$1" POST /api/fnas "{\"args\":$2}"; }
create_args() {
    echo "[\"pool\",\"create\",\"tank\",\"raid5\",\"$b1\",\"$b2\",\"$b3\",\"$b4\",\"--engine\",\"mdadm\",\"--filesystem\",\"ext4\",\"--confirm\",\"erase 4 disks\"]"
}
create_tank() {
    # udev can still hold a disk open for a moment after a wipe or a create;
    # retry only that race, never a real refusal
    local attempt
    for attempt in 1 2 3 4; do
        R=$(fnas_call "$K" "$(create_args)")
        [ "$(code "$R")" = 200 ] && return 0
        grep -q "busy" <<< "$(body "$R")" || return 1
        # A create that lost the race leaves a part-built array holding the
        # first disk; stop it so the next attempt sees free disks
        mdadm --stop /dev/md127 /dev/md/tank 2>/dev/null || true
        sleep 3
    done
    return 1
}
wait_online() {
    for _ in $(seq 60); do
        health=$(fnas --json pool list | python3 -c 'import json,sys
pools = json.load(sys.stdin)
print(pools[0]["health"] if pools else "none")')
        [ "$health" = ONLINE ] && return 0
        sleep 1
    done
    return 1
}

echo "== create the array through the desktop"
create_tank && pass "the desktop creates tank on the four disks" || fail "the desktop creates tank on the four disks: $R"
wait_online && pass "the new array finishes building and reads ONLINE" || fail "the new array finishes building and reads ONLINE"

echo "== a share on it blocks delete, and names itself"
r=$(fnas_call "$K" '["share","create","docs","tank","--everyone","--smb"]')
[ "$(code "$r")" = 200 ] && pass "a share is made on tank" || fail "a share is made on tank: $r"
r=$(fnas_call "$K" '["pool","delete","tank"]')
[ "$(code "$r")" != 200 ] && grep -q "docs" <<< "$(body "$r")" && grep -q "Shares" <<< "$(body "$r")" \
    && pass "delete refuses and names the share and where to remove it" || fail "delete refuses and names the share and where to remove it: $r"

echo "== with the share gone, delete lists the disks and needs the name"
r=$(fnas_call "$K" '["share","delete","docs","--confirm","docs"]')
[ "$(code "$r")" = 200 ] && pass "the share is removed" || fail "the share is removed: $r"
r=$(fnas_call "$K" '["pool","delete","tank","--confirm","nope"]')
[ "$(code "$r")" != 200 ] && grep -q "$d1" <<< "$(body "$r")" && grep -q "$d4" <<< "$(body "$r")" \
    && pass "the refusal lists the exact member disks" || fail "the refusal lists the exact member disks: $r"
r=$(fnas_call "$A" '["pool","delete","tank","--confirm","tank"]')
[ "$(code "$r")" = 403 ] && pass "a non-admin cannot delete" || fail "a non-admin cannot delete: $r"
r=$(fnas_call "$K" '["pool","delete","tank","--confirm","tank"]')
[ "$(code "$r")" = 200 ] && pass "typing the array name deletes it" || fail "typing the array name deletes it: $r"
fnas --json pool list | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin) == [] else 1)' \
    && pass "the pool is gone" || fail "the pool is gone"
fnas --json disk list | python3 -c 'import json,sys
disks = {d["path"]: d["state"] for d in json.load(sys.stdin)}
sys.exit(0 if all(disks.get(p) == "empty" for p in sys.argv[1:5]) else 1)' "$d1" "$d2" "$d3" "$d4" \
    && pass "the four disks read as empty again" || fail "the four disks read as empty again"

echo "== the unrelated disk is untouched"
[ "$(cat /mnt/unrelated/keep.txt)" = "keep me" ] && pass "the unrelated file is still there" || fail "the unrelated file is still there"
[ "$(blkid -s UUID -o value "$unrelated")" = "$uuid_before" ] && pass "the unrelated filesystem is unchanged" || fail "the unrelated filesystem is unchanged"

echo "== the same four disks build a new array"
sleep 3
create_tank && pass "the desktop rebuilds tank from the same four disks" || fail "the desktop rebuilds tank from the same four disks: $R"
wait_online && pass "the rebuilt array reads ONLINE" || fail "the rebuilt array reads ONLINE"
