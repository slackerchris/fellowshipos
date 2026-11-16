#!/bin/bash
# Network changes with rollback (docs/fellowship-nas-account-separation.md,
# phase 6; docs/fellowship-nas-network-rollback-plan.md), against the real
# packages, helper and web server, on a real interface with a real DHCP
# server: a change is kept only when confirmed from the new address, the old
# address can't confirm it, the timeout puts everything back, and the rollback
# fires even with the web server and helper stopped. Static and DHCP both.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/network-rollback.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
# systemd is stubbed here, so the rollback timer's firing is simulated by
# running the unit's ExecStart (fnas network rollback) directly; the real
# timer, and the boot guard, are proven on an installed system in a VM.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/network-rollback.sh:/run.sh:ro" -v "$repo:/repo:ro" \
        debian:trixie bash /run.sh --inside | tee "$out"
    grep -q '^PASS' "$out" && ! grep -q '^FAIL' "$out"
    exit
fi

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; }
expect() { # expect <label> <command...>: passes when the command succeeds
    local label=$1; shift
    if "$@" > /dev/null 2>&1; then pass "$label"; else fail "$label"; fi
}
refuse() { # refuse <label> <command...>: passes when the command fails
    local label=$1; shift
    if "$@" > /dev/null 2>&1; then fail "$label"; else pass "$label"; fi
}

echo "== installing the NAS packages"
sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources
echo "deb [trusted=yes] file:/repo ./" > /etc/apt/sources.list.d/fellowship-nas.list
apt-get update -qq > /dev/null
# No systemd here: packages can't start services, which this starts by hand,
# and their systemctl calls are answered by a stand-in that does nothing
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod +x /usr/sbin/policy-rc.d
dpkg-divert --quiet --local --rename --add /usr/bin/systemctl
printf '#!/bin/sh\nexit 0\n' > /usr/bin/systemctl && chmod +x /usr/bin/systemctl
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fellowship-nas-tools fellowship-nas-web \
    fellowship-nas-smb curl rsyslog ifupdown isc-dhcp-client dnsmasq iproute2 > /tmp/apt.log 2>&1 \
    || { tail -20 /tmp/apt.log; exit 1; }

echo "== making the NAS separated, and two people"
python3 -c 'from fellowship_nas import accounts; accounts.create_store()'
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin
echo 'ann password 22' | fnas user create ann --password-stdin

echo "== a syslog, so the journal lines can be read back"
rsyslogd; sleep 1

echo "== a LAN on a veth pair, with a real DHCP server"
# Nothing after the package install needs the outside network, and docker's
# own default route would collide with the test LAN's (ifup adds, not replaces)
ip route del default 2> /dev/null || true
# The router lives in its own namespace, as a real one is a separate machine:
# a gateway that is a local address is refused by the kernel
ip netns add router
ip link add veth0 type veth peer name veth1
ip link set veth0 netns router
ip netns exec router ip addr add 10.99.0.1/24 dev veth0
ip netns exec router ip link set lo up
ip netns exec router ip link set veth0 up
ip link set veth1 up
mkdir -p /etc/network/interfaces.d
# The installer's own shape, IPv6 stanza included
printf '# Written by the FellowshipOS installer\nallow-hotplug veth1\niface veth1 inet dhcp\niface veth1 inet6 auto\n' \
    > /etc/network/interfaces.d/veth1
V6=$(grep inet6 /etc/network/interfaces.d/veth1)
echo "10.99.0.0/24" > /etc/fellowshipos/nas-lan
ip netns exec router dnsmasq --interface=veth0 --bind-interfaces --port=0 \
    --dhcp-range=10.99.0.100,10.99.0.150,12h --dhcp-option=3,10.99.0.1 \
    --dhcp-leasefile=/tmp/dnsmasq.leases --pid-file=/run/dnsmasq-test.pid --log-facility=/tmp/dnsmasq.log
ifup veth1
ip -4 addr show dev veth1 | grep -q "10.99.0." && pass "the interface took a DHCP lease" \
    || fail "the interface took a DHCP lease: $(ip -4 addr show dev veth1)"
# The container's default route belongs to docker's eth0; pin the test LAN
fnas network interface veth1

echo "== the desktop"
fnas system certificate --if-missing > /dev/null
mkdir -p /run/fellowship-nas
start_desktop() {
    /usr/lib/fellowship-nas-web/fellowship-nas-helper > /tmp/helper.log 2>&1 &
    HELPER_PID=$!
    runuser -u fnas-web -- /usr/lib/fellowship-nas-web/fellowship-nas-web > /tmp/web.log 2>&1 &
    WEB_PID=$!
    for _ in $(seq 20); do curl -sk -o /dev/null https://127.0.0.1/ && break; sleep 0.5; done
}
start_desktop
api() { # api <base> <cookie jar> <method> <path> [json]: as the desktop sends it
    local base=$1 jar=$2 method=$3 path=$4 json=$5 token
    token=$(cat "$jar.csrf" 2> /dev/null || true)
    curl -sk -b "$jar" -c "$jar" -X "$method" -H 'Content-Type: application/json' -H "Origin: $base" \
        -H "X-Fellowship-Token: $token" ${json:+-d "$json"} -w '\n%{http_code}' "$base$path"
}
login() { # login <base> <cookie jar> <user> <password>
    local r
    r=$(api "$1" "$2" POST /api/login "{\"user\":\"$3\",\"password\":\"$4\"}")
    head -n -1 <<< "$r" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("csrf",""))
except Exception: pass' > "$2.csrf"
    echo "$r"
}
code() { tail -1 <<< "$1"; }
body() { head -n -1 <<< "$1"; }
PENDING=/var/lib/fellowship-nas/network-rollback/pending.json
FILE=/etc/network/interfaces.d/veth1
K=$(mktemp); A=$(mktemp)
login https://127.0.0.1 "$K" kim 'admin password 1' > /dev/null
login https://127.0.0.1 "$A" ann 'ann password 22' > /dev/null
fnasapi() { api https://127.0.0.1 "$1" POST /api/fnas "{\"args\":$2}"; }
# The loopback session in use: a restarted helper forgets every session
LOOP=$K
apply() { # apply <address>: a fixed change through the desktop, over loopback
    fnasapi "$LOOP" "[\"network\",\"apply\",\"--address\",\"$1/24\",\"--gateway\",\"10.99.0.1\",\"--dns\",\"10.99.0.1\"]"
}

echo "== a fixed address, confirmed from it"
r=$(apply 10.99.0.50)
[ "$(code "$r")" = 200 ] && pass "the change applied" || fail "the change applied: $r"
grep -q "iface veth1 inet static" "$FILE" && pass "the file says static" || fail "the file says static"
grep -qF "$V6" "$FILE" && pass "the IPv6 stanza is preserved" || fail "the IPv6 stanza is preserved"
[ -f "$PENDING" ] && pass "the change waits to be confirmed" || fail "the change waits to be confirmed"
ip -4 addr show dev veth1 | grep -q 10.99.0.50 && pass "the new address is live" || fail "the new address is live"
N=$(mktemp)
login https://10.99.0.50 "$N" kim 'admin password 1' > /dev/null
r=$(api https://10.99.0.50 "$N" POST /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "confirmed from the new address" || fail "confirmed from the new address: $r"
[ ! -f "$PENDING" ] && pass "nothing waits anymore" || fail "nothing waits anymore"
grep -q "iface veth1 inet static" "$FILE" && pass "the change is kept" || fail "the change is kept"

echo "== the old address can't confirm a change away from it"
r=$(apply 10.99.0.60)
[ "$(code "$r")" = 200 ] && pass "a second change applied" || fail "a second change applied: $r"
# Make the old address genuinely reachable again, as a second address on the
# port, so the refusal is proven end to end and not by absence alone
ip addr add 10.99.0.50/24 dev veth1
O=$(mktemp)
login https://10.99.0.50 "$O" kim 'admin password 1' > /dev/null
r=$(api https://10.99.0.50 "$O" POST /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 403 ] && pass "confirm through the old address: refused" || fail "confirm through the old address: refused: $r"
N2=$(mktemp)
login https://10.99.0.60 "$N2" kim 'admin password 1' > /dev/null
r=$(api https://10.99.0.60 "$N2" POST /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "confirm through the new address: kept" || fail "confirm through the new address: kept: $r"
ip addr del 10.99.0.50/24 dev veth1

echo "== no confirm: the rollback puts everything back"
r=$(apply 10.99.0.70)
[ "$(code "$r")" = 200 ] && pass "a third change applied" || fail "a third change applied: $r"
# The timer's ExecStart, as systemd would run it when the window runs out
fnas network rollback
grep -q "address 10.99.0.60/24" "$FILE" && pass "the previous file is back" || fail "the previous file is back"
grep -qF "$V6" "$FILE" && pass "the IPv6 stanza came back too" || fail "the IPv6 stanza came back too"
ip -4 addr show dev veth1 | grep -q 10.99.0.60 && pass "the previous address is live" || fail "the previous address is live"
[ ! -f "$PENDING" ] && pass "nothing waits anymore" || fail "nothing waits anymore"
fnas --json alert list | grep -q "Network change rolled back" && pass "the rollback alerted" || fail "the rollback alerted"
expect "rolling back again does nothing" fnas network rollback

echo "== the rollback fires with the web server and helper stopped"
r=$(apply 10.99.0.80)
[ "$(code "$r")" = 200 ] && pass "a fourth change applied" || fail "a fourth change applied: $r"
kill "$HELPER_PID" "$WEB_PID" 2> /dev/null || true
wait "$HELPER_PID" "$WEB_PID" 2> /dev/null || true
fnas network rollback
grep -q "address 10.99.0.60/24" "$FILE" && pass "restored without the helper or web server" \
    || fail "restored without the helper or web server"
start_desktop
K2=$(mktemp)
r=$(login https://10.99.0.60 "$K2" kim 'admin password 1')
[ "$(code "$r")" = 200 ] && pass "the desktop is back at the old address" || fail "the desktop is back at the old address: $r"
# The session cookie is host-only, so loopback needs its own sign-in
K3=$(mktemp); A2=$(mktemp)
login https://127.0.0.1 "$K3" kim 'admin password 1' > /dev/null
login https://127.0.0.1 "$A2" ann 'ann password 22' > /dev/null
LOOP=$K3

echo "== DHCP: the lease is the new address, and the desktop learns it"
r=$(fnasapi "$K3" '["network","apply","--dhcp"]')
[ "$(code "$r")" = 200 ] && pass "the DHCP change applied" || fail "the DHCP change applied: $r"
LEASE=$(fnas --json network show | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending"]["address"])')
OCTET=${LEASE##*.}
[ "$OCTET" -ge 100 ] && [ "$OCTET" -le 150 ] && pass "the lease arrived and network show reports it" \
    || fail "the lease arrived and network show reports it: $LEASE"
body "$r" | grep -q "$LEASE" && pass "the apply answer reports it too" || fail "the apply answer reports it too: $r"
grep -q "iface veth1 inet dhcp" "$FILE" && pass "the file says dhcp" || fail "the file says dhcp"
grep -qF "$V6" "$FILE" && pass "the IPv6 stanza is preserved under DHCP" || fail "the IPv6 stanza is preserved under DHCP"
D=$(mktemp)
login "https://$LEASE" "$D" kim 'admin password 1' > /dev/null
r=$(api "https://$LEASE" "$D" POST /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "the DHCP change confirmed from its lease" || fail "the DHCP change confirmed from its lease: $r"

echo "== DHCP unconfirmed: the static settings come back exactly"
r=$(apply 10.99.0.90)
[ "$(code "$r")" = 200 ] && pass "back to a fixed address" || fail "back to a fixed address: $r"
S=$(mktemp)
login https://10.99.0.90 "$S" kim 'admin password 1' > /dev/null
r=$(api https://10.99.0.90 "$S" POST /api/fnas '{"args":["network","confirm"]}')
[ "$(code "$r")" = 200 ] && pass "and confirmed" || fail "and confirmed: $r"
STATIC=$(cat "$FILE")
r=$(fnasapi "$K3" '["network","apply","--dhcp"]')
[ "$(code "$r")" = 200 ] && pass "a DHCP change over it" || fail "a DHCP change over it: $r"
fnas network rollback
[ "$(cat "$FILE")" = "$STATIC" ] && pass "the static file is back byte for byte" || fail "the static file is back byte for byte"
ip -4 addr show dev veth1 | grep -q 10.99.0.90 && pass "the static address is live" || fail "the static address is live"

echo "== the LAN subnet follows the change, both directions"
r=$(fnasapi "$K3" '["network","apply","--address","10.50.0.50/24","--gateway","10.50.0.1","--dns","10.50.0.1"]')
[ "$(code "$r")" = 200 ] && pass "a change into another subnet applied" || fail "a change into another subnet applied: $r"
[ "$(cat /etc/fellowshipos/nas-lan)" = "10.50.0.0/24" ] && pass "the LAN subnet followed" || fail "the LAN subnet followed"
fnas network rollback
[ "$(cat /etc/fellowshipos/nas-lan)" = "10.99.0.0/24" ] && pass "and came back on rollback" || fail "and came back on rollback"

echo "== both locks"
r=$(fnasapi "$A2" '["--json","network","show"]')
[ "$(code "$r")" = 403 ] && pass "ann, not an admin, can't see the network" || fail "ann, not an admin, can't see the network: $r"
r=$(fnasapi "$A2" '["network","apply","--dhcp"]')
[ "$(code "$r")" = 403 ] && pass "nor change it" || fail "nor change it: $r"
r=$(fnasapi "$K3" '["network","apply","--address","nonsense","--gateway","10.99.0.1","--dns","10.99.0.1"]')
[ "$(code "$r")" = 403 ] && pass "an address that isn't one: refused by the helper" || fail "an address that isn't one: refused by the helper: $r"
r=$(fnasapi "$K3" '["network","apply","--address","8.8.8.8/24","--gateway","8.8.8.1","--dns","8.8.8.1"]')
[ "$(code "$r")" = 422 ] && pass "a public address: refused by fnas" || fail "a public address: refused by fnas: $r"
r=$(fnasapi "$K3" '["network","rollback"]')
[ "$(code "$r")" = 403 ] && pass "the rollback is root's alone" || fail "the rollback is root's alone: $r"
r=$(fnasapi "$K3" '["network","interface","veth0"]')
[ "$(code "$r")" = 403 ] && pass "pinning the interface is root's alone" || fail "pinning the interface is root's alone: $r"
for cmd in "network rollback" "network interface veth0"; do
    out=$(fnas --caller kim $cmd 2>&1) && fail "fnas refuses '$cmd' to a caller: ran" || {
        grep -q "for root alone" <<< "$out" && pass "fnas refuses '$cmd' to a caller" || fail "fnas refuses '$cmd' to a caller: $out"; }
done
r=$(apply 10.99.0.91)
[ "$(code "$r")" = 200 ] && pass "a change to wait on" || fail "a change to wait on: $r"
r=$(apply 10.99.0.92)
[ "$(code "$r")" != 200 ] && pass "a second change while one waits: refused" || fail "a second change while one waits: refused: $r"
fnas network rollback

echo "== written down"
sleep 1
grep -q "fellowship-nas-maintenance.*kim asked for: network apply" /var/log/auth.log \
    && pass "the journal says kim asked for the changes" || fail "the journal says kim asked for the changes"
grep -q "kim asked for: network confirm" /var/log/auth.log \
    && pass "and for the confirms" || fail "and for the confirms"
fnas --json alert list | grep -q "Maintenance by kim: network apply" \
    && pass "every admin hears about a network change" || fail "every admin hears about a network change"
fnas --json alert list | grep -q "Maintenance by kim: network confirm" \
    && pass "and about a confirm" || fail "and about a confirm"
refuse "nothing ann asked for was written down" grep -q "ann asked for: network" /var/log/auth.log
