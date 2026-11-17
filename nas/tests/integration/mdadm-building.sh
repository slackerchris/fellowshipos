#!/bin/bash
# A new Traditional RAID array writes its parity before it is protected, and
# mdadm calls that degraded: /proc/mdstat shows [4/3], --detail says
# "clean, degraded, recovering", and its monitor fires DegradedArray. No disk is
# missing, so FellowshipNAS must say BUILDING and send info, not a critical
# "missing a disk", until the work finishes -- and still cry critical when a
# disk really goes.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/mdadm-building.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/mdadm-building.sh:/run.sh:ro" -v "$repo:/repo:ro" \
        debian:trixie bash /run.sh --inside | tee "$out"
    grep -q '^PASS' "$out" && ! grep -q '^FAIL' "$out"
    exit
fi

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }

echo "== installing the NAS tools and mdadm"
sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources
echo "deb [trusted=yes] file:/repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
apt-get update -qq > /dev/null
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
dpkg-divert --quiet --local --rename --add /usr/bin/systemctl
printf '#!/bin/sh\nexit 0\n' > /usr/bin/systemctl && chmod +x /usr/bin/systemctl
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fellowship-nas-tools mdadm e2fsprogs > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
# fnas health reads the kernel's module directory; a container has none
mkdir -p "/lib/modules/$(uname -r)"

echo "== a new raid5, held mid-sync so the state can be observed"
# Keep the kernel's parity write slow enough to look at, then let it run
echo 1000 > /proc/sys/dev/raid/speed_limit_max
# Loop devices and arrays are the host's even from a container: take loops
# nobody is using, and give everything back at the end so a rerun finds them free
loops=""
cleanup() {
    umount "$mnt" 2>/dev/null || true
    mdadm --stop /dev/md0 > /dev/null 2>&1 || true
    for l in $loops; do losetup -d "$l" 2>/dev/null || true; done
}
trap cleanup EXIT
for i in 0 1 2 3; do
    truncate -s 256M /tmp/d$i
    n=""
    for c in $(seq 8 63); do
        [ -e /dev/loop$c ] || mknod -m 660 /dev/loop$c b 7 $c
        if [ ! -e /sys/block/loop$c/loop/backing_file ]; then n=$c; break; fi
    done
    [ -n "$n" ] || { echo "no free loop device"; exit 1; }
    losetup /dev/loop$n /tmp/d$i; loops="${loops:+$loops }/dev/loop$n"
done
first=${loops%% *}
mdadm --create /dev/md0 --level=5 --raid-devices=4 --run --name=tank $loops > /dev/null 2>&1
grep -q '\[4/3\]' /proc/mdstat && pass "the new array is writing parity ([4/3])" || fail "the new array is writing parity ([4/3])"
mnt=$(python3 -c 'from fellowship_nas import pools; print(pools.mountpoint("tank"))')
mkdir -p "$mnt"; mkfs.ext4 -q /dev/md0; mount /dev/md0 "$mnt"
findmnt -n "$mnt" > /dev/null && pass "the new pool is mounted" || fail "the new pool is mounted at $mnt"

pool_health() { fnas --json pool list | python3 -c 'import json,sys
pools = json.load(sys.stdin)
print(pools[0]["health"] if pools else "none")'; }
alert_severity() { # alert_severity <title fragment>: the severity of the newest alert whose title holds it
    fnas --json alert list | python3 -c 'import json,sys
found = [a for a in json.load(sys.stdin) if sys.argv[1] in a["title"]]
print(found[0]["severity"] if found else "none")' "$1"
}

echo "== while it builds: building, and no critical"
[ "$(pool_health)" = BUILDING ] && pass "pool list says BUILDING" || fail "pool list says BUILDING: $(pool_health)"
fnas --json health | python3 -c 'import json,sys
sys.exit(0 if not [p for p in json.load(sys.stdin)["problems"] if p["key"].startswith("pool-health")] else 1)' \
    && pass "Health raises no pool finding while building" || fail "Health raises no pool finding while building"
/usr/lib/fellowship-nas/md-alert DegradedArray /dev/md0
[ "$(alert_severity "is building")" = info ] && pass "mdadm's DegradedArray during the build is info" || fail "mdadm's DegradedArray during the build is info: $(alert_severity "is building")"

echo "== when the parity is written: online"
echo 200000 > /proc/sys/dev/raid/speed_limit_max
# mdstat also writes resync=PENDING between phases, so watch for the word itself
for _ in $(seq 60); do grep -qE 'resync|recovery|reshape' /proc/mdstat || break; sleep 1; done
sleep 1
grep -qE 'resync|recovery|reshape' /proc/mdstat && fail "the initial sync finished" || pass "the initial sync finished"
[ "$(pool_health)" = ONLINE ] && pass "pool list says ONLINE once protected" || fail "pool list says ONLINE once protected: $(pool_health)"

echo "== a disk really gone: still degraded, still critical"
mdadm --manage /dev/md0 --fail "$first" --remove "$first" > /tmp/fail.log 2>&1 \
    || { cat /tmp/fail.log; fail "a disk can be failed out of the array"; }
[ "$(pool_health)" = DEGRADED ] && pass "a missing disk is DEGRADED" || fail "a missing disk is DEGRADED: $(pool_health)"
/usr/lib/fellowship-nas/md-alert DegradedArray /dev/md0
[ "$(alert_severity "is degraded")" = critical ] && pass "a missing disk still raises critical" || fail "a missing disk still raises critical: $(alert_severity "is degraded")"
