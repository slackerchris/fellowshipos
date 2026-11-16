#!/bin/bash
# The leftover-address regression (phase 6, found on an installed DHCP-booted
# NAS): start with an ACTIVE DHCP lease, as every real NAS's first boot does,
# and prove that a method change retires the old method completely.
#   - switching to static retires the lease: client gone, address gone
#   - the timeout restores DHCP and removes the abandoned static address
#   - switching back to DHCP reports the lease it actually acquired
#   - live reachability follows the addresses, not just the saved files
#   - the cleanup is scoped: a second port's IPv4 and IPv6 stay untouched, and
#     the managed port's IPv6 stanza and address survive every change
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/network-lease.sh
# Runs in a privileged Debian trixie container. systemd is stubbed, so the
# timeout is the rollback unit's ExecStart run directly, as in
# network-rollback.sh; the real timer is proven on an installed system.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/network-lease.sh:/run.sh:ro" -v "$repo:/repo:ro" \
        debian:trixie bash /run.sh --inside | tee "$out"
    grep -q '^PASS' "$out" && ! grep -q '^FAIL' "$out"
    exit
fi

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }
expect() { local label=$1; shift; if "$@" > /dev/null 2>&1; then pass "$label"; else fail "$label"; fi; }
refuse() { local label=$1; shift; if "$@" > /dev/null 2>&1; then fail "$label"; else pass "$label"; fi; }

echo "== installing the NAS packages"
sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources
echo "deb [trusted=yes] file:/repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
apt-get update -qq > /dev/null
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
dpkg-divert --quiet --local --rename --add /usr/bin/systemctl
printf '#!/bin/sh\nexit 0\n' > /usr/bin/systemctl && chmod +x /usr/bin/systemctl
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fellowship-nas-tools fellowship-nas-web \
    fellowship-nas-smb curl ifupdown isc-dhcp-client dnsmasq iproute2 iputils-ping procps \
    > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }

echo "== a separated NAS with one admin"
python3 -c 'from fellowship_nas import accounts; accounts.create_store()'
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin > /dev/null

echo "== a boot-like LAN: an active DHCP lease, IPv6, and a second port"
ip route del default 2> /dev/null || true
ip netns add router
ip link add veth0 type veth peer name veth1
ip link set veth0 netns router
ip netns exec router ip addr add 10.99.0.1/24 dev veth0
ip netns exec router ip link set lo up
ip netns exec router ip link set veth0 up
ip link set veth1 up
# A second port fnas does not manage, with its own IPv4 and IPv6
ip link add veth2 type dummy
ip link set veth2 up
ip addr add 10.98.0.5/24 dev veth2
ip -6 addr add 2001:db8:98::5/64 dev veth2 nodad
mkdir -p /etc/network/interfaces.d
# The installer's shape, with a static IPv6 stanza so the live v6 address is
# one ifupdown restores on every ifup
printf '# Written by the FellowshipOS installer\nallow-hotplug veth1\niface veth1 inet dhcp\niface veth1 inet6 static\n    address 2001:db8:99::5/64\n' \
    > /etc/network/interfaces.d/veth1
V6=$(grep -A1 inet6 /etc/network/interfaces.d/veth1)
echo "10.99.0.0/24" > /etc/fellowshipos/nas-lan
ip netns exec router dnsmasq --interface=veth0 --bind-interfaces --port=0 \
    --dhcp-range=10.99.0.100,10.99.0.150,12h --dhcp-option=3,10.99.0.1 \
    --dhcp-leasefile=/tmp/dnsmasq.leases --pid-file=/run/dnsmasq-test.pid --log-facility=/tmp/dnsmasq.log
ifup veth1
LEASE1=$(ip -4 -o addr show dev veth1 | awk '{print $4}' | cut -d/ -f1)
[ -n "$LEASE1" ] && pass "the port boots with an active DHCP lease ($LEASE1)" || fail "the port boots with an active DHCP lease"
expect "the lease's DHCP client is running" pgrep -f "dhclient.*veth1"
expect "the IPv6 address is live" bash -c 'ip -6 addr show dev veth1 | grep -q 2001:db8:99::5'
fnas network interface veth1 > /dev/null

echo "== the desktop, for reachability and confirms from the live address"
fnas system certificate --if-missing > /dev/null
mkdir -p /run/fellowship-nas
/usr/lib/fellowship-nas-web/fellowship-nas-helper > /tmp/helper.log 2>&1 &
runuser -u fnas-web -- /usr/lib/fellowship-nas-web/fellowship-nas-web > /tmp/web.log 2>&1 &
for _ in $(seq 20); do curl -sk -o /dev/null https://127.0.0.1/ && break; sleep 0.5; done
reach() { curl -sk -o /dev/null --connect-timeout 3 "https://$1/"; }
api() { # api <base> <jar> <path> [json]
    local base=$1 jar=$2 path=$3 json=$4 token
    token=$(cat "$jar.csrf" 2> /dev/null || true)
    curl -sk -b "$jar" -c "$jar" -X POST -H 'Content-Type: application/json' -H "Origin: $base" \
        -H "X-Fellowship-Token: $token" ${json:+-d "$json"} -w '\n%{http_code}' "$base$path" || true
}
login() { # login <base> <jar>
    local r
    r=$(api "$1" "$2" /api/login '{"user":"kim","password":"admin password 1"}')
    head -n -1 <<< "$r" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("csrf",""))
except Exception: pass' > "$2.csrf"
    echo "$r"
}
code() { tail -1 <<< "$1"; }
only_v4() { # the port's single IPv4 address, or nothing
    ip -4 -o addr show dev veth1 | awk '{print $4}' | cut -d/ -f1
}
FILE=/etc/network/interfaces.d/veth1
PENDING=/var/lib/fellowship-nas/network-rollback/pending.json

echo "== switching to static retires the old lease"
fnas network apply --address 10.99.0.50/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
grep -q "iface veth1 inet static" "$FILE" && pass "the file says static" || fail "the file says static"
grep -qF "$V6" "$FILE" && pass "the IPv6 stanza is preserved" || fail "the IPv6 stanza is preserved"
refuse "the lease's DHCP client is gone" pgrep -f "dhclient.*veth1"
[ "$(only_v4)" = "10.99.0.50" ] && pass "the static address is the port's only IPv4 (lease retired)" \
    || fail "the static address is the port's only IPv4: $(only_v4)"
expect "the IPv6 address survived the change" bash -c 'ip -6 addr show dev veth1 | grep -q 2001:db8:99::5'
expect "the second port's IPv4 is untouched" bash -c 'ip -4 addr show dev veth2 | grep -q 10.98.0.5'
expect "the second port's IPv6 is untouched" bash -c 'ip -6 addr show dev veth2 | grep -q 2001:db8:98::5'
expect "the new address is live over HTTPS" reach 10.99.0.50
refuse "the retired lease address is not reachable" reach "$LEASE1"
J=$(mktemp)
login https://10.99.0.50 "$J" > /dev/null
r=$(api https://10.99.0.50 "$J" /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "confirmed from the new address" || fail "confirmed from the new address: $r"

echo "== the timeout restores DHCP and removes the abandoned static"
# Rollback restores the last confirmed configuration, so make DHCP the
# confirmed one first: back to the lease, confirmed from it
BACK=$(fnas --json network apply --dhcp | python3 -c 'import json,sys; print(json.load(sys.stdin)["address"])')
JB=$(mktemp)
login "https://$BACK" "$JB" > /dev/null
r=$(api "https://$BACK" "$JB" /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "DHCP is the confirmed configuration again" || fail "DHCP is the confirmed configuration again: $r"
fnas network apply --address 10.99.0.60/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
[ "$(only_v4)" = "10.99.0.60" ] && pass "the abandoned static is live and alone" || fail "the abandoned static is live and alone: $(only_v4)"
refuse "the confirmed lease was retired by the static change" reach "$BACK"
# The timer's ExecStart, as systemd would run it when the window runs out
fnas network rollback > /dev/null
grep -q "iface veth1 inet dhcp" "$FILE" && pass "the dhcp file is back" || fail "the dhcp file is back"
grep -qF "$V6" "$FILE" && pass "the IPv6 stanza came back too" || fail "the IPv6 stanza came back too"
LEASE2=$(only_v4)
OCTET=${LEASE2##*.}
[ "$OCTET" -ge 100 ] && [ "$OCTET" -le 150 ] && pass "a lease was acquired again ($LEASE2)" || fail "a lease was acquired again: $LEASE2"
refuse "the abandoned static address is gone" bash -c 'ip -4 addr show dev veth1 | grep -q 10.99.0.60'
expect "the lease's DHCP client is running again" pgrep -f "dhclient.*veth1"
expect "the IPv6 address survived the rollback" bash -c 'ip -6 addr show dev veth1 | grep -q 2001:db8:99::5'
expect "the second port is still untouched" bash -c 'ip -4 addr show dev veth2 | grep -q 10.98.0.5 && ip -6 addr show dev veth2 | grep -q 2001:db8:98::5'
expect "the restored lease is live over HTTPS" reach "$LEASE2"
refuse "the abandoned static is not reachable" reach 10.99.0.60
[ ! -f "$PENDING" ] && pass "nothing waits anymore" || fail "nothing waits anymore"
fnas --json alert list | grep -q "Network change rolled back" && pass "the rollback alerted" || fail "the rollback alerted"

echo "== switching back to DHCP reports the lease it actually acquired"
fnas network apply --address 10.99.0.50/24 --gateway 10.99.0.1 --dns 10.99.0.1 > /dev/null
J2=$(mktemp)
login https://10.99.0.50 "$J2" > /dev/null
api https://10.99.0.50 "$J2" /api/fnas '{"args":["network","confirm"]}' > /dev/null
ANSWER=$(fnas --json network apply --dhcp | python3 -c 'import json,sys; print(json.load(sys.stdin)["address"])')
LEASE3=$(only_v4)
[ "$ANSWER" = "$LEASE3" ] && pass "the apply answer reports the live lease ($LEASE3)" || fail "the apply answer reports the live lease: answer=$ANSWER live=$LEASE3"
grep -q " $LEASE3 " /tmp/dnsmasq.leases && pass "and it is the router's lease" || fail "and it is the router's lease: $(cat /tmp/dnsmasq.leases)"
OCTET=${LEASE3##*.}
[ "$OCTET" -ge 100 ] && [ "$OCTET" -le 150 ] && pass "it is a lease, not the leftover static" || fail "it is a lease, not the leftover static: $LEASE3"
refuse "the static address is gone" bash -c 'ip -4 addr show dev veth1 | grep -q 10.99.0.50'
expect "the IPv6 address survived the switch back" bash -c 'ip -6 addr show dev veth1 | grep -q 2001:db8:99::5'
expect "the lease is live over HTTPS" reach "$LEASE3"
J3=$(mktemp)
login "https://$LEASE3" "$J3" > /dev/null
r=$(api "https://$LEASE3" "$J3" /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "the DHCP change confirmed from its lease" || fail "the DHCP change confirmed from its lease: $r"
expect "rolling back with nothing pending does nothing" fnas network rollback
