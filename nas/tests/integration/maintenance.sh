#!/bin/bash
# The maintenance interface (docs/fellowship-nas-account-separation.md, phase
# 3), against the real packages, helper and web server: a NAS admin reaches the
# system only through the listed operations and argument shapes, fnas refuses
# root's commands to a caller by itself, maintenance is written down under the
# admin's name, and checking for updates installs nothing.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/maintenance.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/maintenance.sh:/run.sh:ro" -v "$repo:/repo:ro" \
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
    fellowship-nas-smb curl rsyslog > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }

echo "== making the NAS separated, and two people"
python3 -c 'from fellowship_nas import accounts; accounts.create_store()'
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin
echo 'ann password 22' | fnas user create ann --password-stdin

echo "== a syslog, so the journal lines can be read back"
rsyslogd; sleep 1

echo "== the desktop"
fnas system certificate --if-missing > /dev/null
mkdir -p /run/fellowship-nas
/usr/lib/fellowship-nas-web/fellowship-nas-helper > /tmp/helper.log 2>&1 &
sleep 1
runuser -u fnas-web -- /usr/lib/fellowship-nas-web/fellowship-nas-web > /tmp/web.log 2>&1 &
for _ in $(seq 20); do curl -sk -o /dev/null https://127.0.0.1/ && break; sleep 0.5; done
api() { # api <cookie jar> <method> <path> [json]: as the desktop sends it, CSRF token included
    local token
    token=$(cat "$1.csrf" 2> /dev/null || true)
    curl -sk -b "$1" -c "$1" -X "$2" -H 'Content-Type: application/json' -H 'Origin: https://127.0.0.1' \
        -H "X-Fellowship-Token: $token" ${4:+-d "$4"} -w '\n%{http_code}' "https://127.0.0.1$3"
}
login() { # login <cookie jar> <user> <password>: keeps the session's CSRF token beside the jar
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
fnasapi() { api "$1" POST /api/fnas "{\"args\":$2}"; }

echo "== what a NAS admin may ask for"
r=$(fnasapi "$K" '["--json","updates","show"]')
[ "$(code "$r")" = 200 ] && pass "kim sees the updates" || fail "kim sees the updates: $r"
r=$(fnasapi "$K" '["--json","updates","check"]')
[ "$(code "$r")" = 200 ] && pass "kim can ask for a check" || fail "kim can ask for a check: $r"
# Both locks let it through; making it then restarts the web server through
# systemd, which this container hasn't got, so only the locks are judged here
r=$(fnasapi "$K" '["system","certificate","--self-signed"]')
[ "$(code "$r")" != 403 ] && ! grep -q "root alone" <<< "$r" && pass "kim may make a new certificate" || fail "kim may make a new certificate: $r"

echo "== lock one: the helper's list and argument shapes"
r=$(fnasapi "$K" '["system","grow","-1"]')
[ "$(code "$r")" = 403 ] && pass "a grow amount that isn't one: refused" || fail "a grow amount that isn't one: refused: $r"
r=$(fnasapi "$K" '["system","hostname","nas;reboot"]')
[ "$(code "$r")" = 403 ] && pass "a hostname that isn't one: refused" || fail "a hostname that isn't one: refused: $r"
r=$(fnasapi "$K" '["system","certificate","--if-missing"]')
[ "$(code "$r")" = 403 ] && pass "a certificate option not on the list: refused" || fail "a certificate option not on the list: refused: $r"
r=$(fnasapi "$K" '["auth","check","kim"]')
[ "$(code "$r")" = 403 ] && pass "a root-only command: refused" || fail "a root-only command: refused: $r"
r=$(fnasapi "$K" '["pool","sync-all"]')
[ "$(code "$r")" = 403 ] && pass "a command for timers only: refused" || fail "a command for timers only: refused: $r"
r=$(fnasapi "$A" '["--json","updates","show"]')
[ "$(code "$r")" = 403 ] && pass "ann, not an admin, can't see updates" || fail "ann, not an admin, can't see updates: $r"
r=$(fnasapi "$A" '["system","certificate","--self-signed"]')
[ "$(code "$r")" = 403 ] && pass "nor make a certificate" || fail "nor make a certificate: $r"

echo "== lock two: fnas itself, told who is asking"
for cmd in "auth check kim" "pool sync-all zfs" "job run 0123456789ab" "alert send x y"; do
    out=$(echo x | fnas --caller kim $cmd 2>&1) && fail "fnas refuses '$cmd' to a caller: ran" || {
        grep -q "for root alone" <<< "$out" && pass "fnas refuses '$cmd' to a caller" || fail "fnas refuses '$cmd' to a caller: $out"; }
done
expect "root itself still runs it" bash -c 'echo "admin password 1" | fnas auth check kim'

echo "== written down"
sleep 1
grep -q "fellowship-nas-maintenance.*kim asked for: updates check" /var/log/auth.log && pass "the journal says kim asked for a check" || fail "the journal says kim asked for a check: $(tail -3 /var/log/auth.log 2>&1)"
grep -q "kim asked for: system certificate --self-signed" /var/log/auth.log && pass "and for the certificate" || fail "and for the certificate"
refuse "nothing refused was written down as asked for" grep -q "kim asked for: system grow" /var/log/auth.log
fnas --json alert list | grep -q "Maintenance by kim: system certificate" && pass "every admin hears about the certificate" || fail "every admin hears about the certificate"
refuse "a check isn't announced as maintenance" bash -c 'fnas --json alert list | grep -q "Maintenance by kim: updates check"'

echo "== checking installs nothing"
before=$(dpkg-query -W -f '${Package}=${Version}\n' | sort | md5sum)
fnas updates check > /dev/null
after=$(dpkg-query -W -f '${Package}=${Version}\n' | sort | md5sum)
[ "$before" = "$after" ] && pass "no package changed" || fail "no package changed"
fnas --json updates show | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["checked"] else 1)' \
    && pass "the check was recorded" || fail "the check was recorded"

echo "== installing, when asked (run directly: jobs need systemd, which this container hasn't)"
dpkg-query -W -f '${Package}\n' | sort > /tmp/before
waiting=$(fnas --json updates show | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["packages"]))')
python3 -c 'from fellowship_nas import updates; updates.install()' > /tmp/install.log 2>&1 \
    && pass "installing finished ($waiting were waiting)" || fail "installing finished: $(tail -5 /tmp/install.log)"
dpkg-query -W -f '${Package}\n' | sort > /tmp/after
removed=$(comm -23 /tmp/before /tmp/after)
[ -z "$removed" ] && pass "nothing was removed" || fail "nothing was removed: $removed"
left=$(fnas --json updates show | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["packages"]))')
[ "$left" = 0 ] && pass "nothing is left waiting" || fail "nothing is left waiting: $left"
fnas --json alert list | grep -q '"Updates installed"' && pass "and it said so" || fail "and it said so"
