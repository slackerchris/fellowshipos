#!/bin/bash
# The rollback timer under real systemd (docs/fellowship-nas-network-rollback-plan.md):
# a privileged container running systemd as PID 1, the real packages and units.
# Proves what network-rollback.sh can only simulate with the unit's ExecStart:
# the timer fires on its own after the window, fires with the web server and
# helper stopped, is stopped by a confirm, and the boot guard is enabled.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/network-rollback-timer.sh
# Every result line says PASS or FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    name="nas-rollback-timer-$$"
    trap 'rm -f "$out"; docker rm -f "$name" > /dev/null 2>&1 || true' EXIT
    # The base image has no systemd; this one runs it as PID 1
    image="nas-rollback-timer-systemd"
    docker image inspect "$image" > /dev/null 2>&1 || docker build -q -t "$image" - <<'EOF' > /dev/null
FROM debian:trixie
RUN apt-get update -qq && apt-get install -y -qq systemd systemd-sysv dbus > /dev/null && rm -rf /var/lib/apt/lists/*
EOF
    docker run -d --privileged --name "$name" \
        -v "$(pwd)/network-rollback-timer.sh:/run.sh:ro" -v "$repo:/repo:ro" \
        "$image" /sbin/init > /dev/null
    for _ in $(seq 60); do
        state=$(docker exec "$name" systemctl is-system-running 2> /dev/null || true)
        [ "$state" = running ] || [ "$state" = degraded ] && break
        sleep 1
    done
    docker exec "$name" bash /run.sh --inside | tee "$out"
    grep -q '^PASS' "$out" && ! grep -q '^FAIL' "$out"
    exit
fi

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }

echo "== installing the NAS packages under real systemd"
sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources
echo "deb [trusted=yes] file:/repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
apt-get update -qq > /dev/null
# Services are started by hand below, but systemctl itself is real
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fellowship-nas-tools fellowship-nas-web \
    fellowship-nas-smb curl ifupdown isc-dhcp-client dnsmasq iproute2 > /tmp/apt.log 2>&1 \
    || { tail -20 /tmp/apt.log; exit 1; }

echo "== a separated NAS with one admin"
python3 -c 'from fellowship_nas import accounts; accounts.create_store()'
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin

echo "== a LAN on a veth pair, the router in its own namespace"
ip route del default 2> /dev/null || true
ip netns add router
ip link add veth0 type veth peer name veth1
ip link set veth0 netns router
ip netns exec router ip addr add 10.99.0.1/24 dev veth0
ip netns exec router ip link set lo up
ip netns exec router ip link set veth0 up
ip link set veth1 up
printf '# Written by the FellowshipOS installer\nallow-hotplug veth1\niface veth1 inet dhcp\niface veth1 inet6 auto\n' \
    > /etc/network/interfaces.d/veth1
echo "10.99.0.0/24" > /etc/fellowshipos/nas-lan
ip netns exec router dnsmasq --interface=veth0 --bind-interfaces --port=0 \
    --dhcp-range=10.99.0.100,10.99.0.150,12h --dhcp-option=3,10.99.0.1 \
    --dhcp-leasefile=/tmp/dnsmasq.leases --pid-file=/run/dnsmasq-test.pid --log-facility=/tmp/dnsmasq.log
ifup veth1 > /dev/null 2>&1
fnas network interface veth1
fnas system certificate --if-missing > /dev/null
systemctl start fellowship-nas-helper fellowship-nas-web
PENDING=/var/lib/fellowship-nas/network-rollback/pending.json
FILE=/etc/network/interfaces.d/veth1
ORIGINAL=$(cat "$FILE")

echo "== the timer fires on its own, with the desktop stopped"
fnas network apply --address 10.99.0.50/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
systemctl is-active --quiet fellowship-network-rollback.timer \
    && pass "apply started the rollback timer" || fail "apply started the rollback timer"
systemctl stop fellowship-nas-web fellowship-nas-helper
echo "   (waiting out the 120 second window with the web server and helper stopped)"
sleep 120
# The rollback starts at the deadline; bringing the interface back up on the
# saved DHCP config takes a few seconds more (dhclient release and discover)
for _ in $(seq 30); do [ ! -f "$PENDING" ] && break; sleep 1; done
[ "$(cat "$FILE")" = "$ORIGINAL" ] && pass "the timer put the previous file back" || fail "the timer put the previous file back"
ip -4 addr show dev veth1 | grep -q "10.99.0.1" && pass "the previous address is live" || fail "the previous address is live"
[ ! -f "$PENDING" ] && pass "nothing waits anymore" || fail "nothing waits anymore"
! systemctl is-active --quiet fellowship-network-rollback.timer \
    && pass "the timer is spent" || fail "the timer is spent"
fnas --json alert list | grep -q "Network change rolled back" && pass "the rollback alerted" || fail "the rollback alerted"
journalctl -u fellowship-network-rollback.service --no-pager | grep -q "previous network settings" \
    && pass "the service ran as its own unit" || fail "the service ran as its own unit"

echo "== a confirm stops the timer, and nothing rolls back"
systemctl start fellowship-nas-helper fellowship-nas-web
fnas network apply --address 10.99.0.60/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
systemctl is-active --quiet fellowship-network-rollback.timer \
    && pass "the timer is running again" || fail "the timer is running again"
fnas network confirm
! systemctl is-active --quiet fellowship-network-rollback.timer \
    && pass "confirm stopped the timer" || fail "confirm stopped the timer"
echo "   (waiting past the window to see that nothing rolls back)"
sleep 125
grep -q "address 10.99.0.60/24" "$FILE" && pass "the confirmed change is still there" || fail "the confirmed change is still there"
ip -4 addr show dev veth1 | grep -q 10.99.0.60 && pass "its address is still live" || fail "its address is still live"

echo "== the boot guard"
systemctl is-enabled --quiet fellowship-network-rollback.service \
    && pass "the rollback service is enabled at boot" || fail "the rollback service is enabled at boot"
fnas network apply --address 10.99.0.70/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
# What the service does at boot when it finds a change still pending
systemctl start fellowship-network-rollback.service
grep -q "address 10.99.0.60/24" "$FILE" \
    && pass "a pending change is rolled back as at boot" || fail "a pending change is rolled back as at boot"
[ ! -f "$PENDING" ] && pass "nothing waits anymore" || fail "nothing waits anymore"
