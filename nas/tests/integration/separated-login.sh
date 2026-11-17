#!/bin/bash
# A separated NAS (docs/fellowship-nas-account-separation.md, phase 2), against
# the real packages: people get a UID but no Linux login, the desktop and SMB
# take the NAS password, SSH doesn't, admin rights come from the store, and
# changing your own password needs the current one.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/separated-login.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/separated-login.sh:/run.sh:ro" -v "$repo:/repo:ro" \
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
    fellowship-nas-smb smbclient openssh-server sshpass curl > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
# fnas health reads the kernel's module directory; a container has none
mkdir -p "/lib/modules/$(uname -r)"

echo "== making the NAS separated, and two people"
python3 -c 'from fellowship_nas import accounts; accounts.create_store()'
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --password-stdin
echo 'ann password 22' | fnas user create ann --password-stdin

echo "== the Unix side"
[ "$(getent shadow kim | cut -d: -f2)" = "!" ] && pass "kim's Linux password is locked" || fail "kim's Linux password is locked: $(getent shadow kim | cut -d: -f2)"
[ "$(getent passwd kim | cut -d: -f7)" = /usr/sbin/nologin ] && pass "kim has no shell" || fail "kim has no shell"
[ ! -e /home/kim ] && pass "kim has no home in /home" || fail "kim has no home in /home"
refuse "kim isn't in sudo" bash -c 'id -nG kim | grep -qw sudo'
expect "kim has a Samba password" bash -c 'pdbedit -L | grep -q "^kim:"'
[ "$(stat -c %a /var/lib/fellowship-nas/accounts.json)" = 600 ] && pass "the store is root's alone" || fail "the store is root's alone"
refuse "fnas refuses a shell for a NAS person" bash -c 'echo "long enough" | fnas user create sam --kind ssh --password-stdin'

echo "== fnas auth check"
expect "the right password passes" bash -c 'echo "admin password 1" | fnas auth check kim'
refuse "a wrong password fails" bash -c 'echo "nope" | fnas auth check kim'
refuse "a stranger fails" bash -c 'echo "admin password 1" | fnas auth check nobody'

echo "== Samba"
mkdir -p /srv/t && chmod 777 /srv/t
printf '[t]\npath = /srv/t\nread only = no\n' >> /etc/samba/smb.conf
smbd -D; sleep 2
expect "SMB takes kim's NAS password" smbclient //127.0.0.1/t -U 'kim%admin password 1' -c ls
refuse "SMB refuses a wrong password" smbclient //127.0.0.1/t -U 'kim%nope' -c ls

echo "== SSH"
ssh-keygen -A > /dev/null; mkdir -p /run/sshd
echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/00-test.conf
/usr/sbin/sshd
refuse "SSH refuses kim's NAS password" sshpass -p 'admin password 1' ssh -o StrictHostKeyChecking=no \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no kim@127.0.0.1 true

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
r=$(login "$K" kim 'admin password 1')
[ "$(code "$r")" = 200 ] && body "$r" | grep -q '"admin":true' && pass "kim logs in, as a NAS admin" || fail "kim logs in, as a NAS admin: $r"
r=$(login "$A" ann 'ann password 22')
[ "$(code "$r")" = 200 ] && body "$r" | grep -q '"admin":false' && pass "ann logs in, not an admin" || fail "ann logs in, not an admin: $r"
r=$(login "$(mktemp)" kim nope)
[ "$(code "$r")" != 200 ] && pass "a wrong password is refused" || fail "a wrong password is refused"
r=$(api "$A" POST /api/fnas '{"args":["user","list"]}')
[ "$(code "$r")" = 403 ] && grep -q "can't change the NAS" <<< "$r" && pass "ann can't run admin commands" || fail "ann can't run admin commands: $r"
r=$(api "$K" POST /api/fnas '{"args":["--json","user","list"]}')
[ "$(code "$r")" = 200 ] && pass "kim can" || fail "kim can: $r"

echo "== changing your own password"
change='{"args":["user","password","ann","--password-stdin"],"input":"ann new pass 3"'
r=$(api "$A" POST /api/fnas "$change}")
[ "$(code "$r")" = 403 ] && grep -q "current password is wrong" <<< "$r" && pass "without the current password: refused" || fail "without the current password: refused: $r"
r=$(api "$A" POST /api/fnas "$change,\"current\":\"wrong\"}")
[ "$(code "$r")" = 403 ] && grep -q "current password is wrong" <<< "$r" && pass "with a wrong current password: refused" || fail "with a wrong current password: refused: $r"
expect "ann's old password still works after the refusals" smbclient //127.0.0.1/t -U 'ann%ann password 22' -c ls
r=$(api "$A" POST /api/fnas "$change,\"current\":\"ann password 22\"}")
[ "$(code "$r")" = 200 ] && pass "with the right current password: changed" || fail "with the right current password: changed: $r"
r=$(login "$(mktemp)" ann 'ann new pass 3')
[ "$(code "$r")" = 200 ] && pass "the new password logs in to the desktop" || fail "the new password logs in to the desktop: $r"
r=$(login "$(mktemp)" ann 'ann password 22')
[ "$(code "$r")" != 200 ] && pass "the old password doesn't" || fail "the old password doesn't"
expect "SMB takes the new password" smbclient //127.0.0.1/t -U 'ann%ann new pass 3' -c ls
refuse "SMB refuses the old one" smbclient //127.0.0.1/t -U 'ann%ann password 22' -c ls
# The helper rereads who was signed out at most once a second
sleep 1.2
r=$(api "$A" GET /api/session)
[ "$(code "$r")" != 200 ] && pass "the change signed ann out everywhere" || fail "the change signed ann out everywhere: $r"

echo "== an admin sets someone else's password without theirs"
r=$(api "$K" POST /api/fnas '{"args":["user","password","ann","--password-stdin"],"input":"set by kim 44"}')
[ "$(code "$r")" = 200 ] && pass "kim sets ann's password" || fail "kim sets ann's password: $r"
expect "SMB takes it" smbclient //127.0.0.1/t -U 'ann%set by kim 44' -c ls

echo "== turning someone off"
fnas user set ann --enabled off > /dev/null
r=$(login "$(mktemp)" ann 'set by kim 44')
[ "$(code "$r")" != 200 ] && pass "the desktop refuses them" || fail "the desktop refuses them: $r"
refuse "SMB refuses them" smbclient //127.0.0.1/t -U 'ann%set by kim 44' -c ls
[ "$(getent shadow ann | cut -d: -f2)" = "!" ] && pass "their Linux password stayed locked" || fail "their Linux password stayed locked"
fnas user set ann --enabled on > /dev/null
expect "turned back on, SMB takes them again" smbclient //127.0.0.1/t -U 'ann%set by kim 44' -c ls
[ "$(getent shadow ann | cut -d: -f2)" = "!" ] && pass "and their Linux password is still locked" || fail "and their Linux password is still locked"

echo "== root resets a password at the console"
J=$(mktemp)
r=$(login "$J" ann 'set by kim 44')
[ "$(code "$r")" = 200 ] && pass "ann is signed in before the reset" || fail "ann is signed in before the reset: $r"
printf 'root reset 66\nroot reset 66\n' | fellowship-reset-password ann > /tmp/reset.log 2>&1
grep -q "password is set" /tmp/reset.log && pass "the console script sets one password for both" || fail "the console script sets one password for both: $(cat /tmp/reset.log)"
r=$(login "$(mktemp)" ann 'root reset 66')
[ "$(code "$r")" = 200 ] && pass "the reset password logs in to the desktop" || fail "the reset password logs in to the desktop: $r"
expect "SMB takes the reset password" smbclient //127.0.0.1/t -U 'ann%root reset 66' -c ls
refuse "SMB refuses the old one" smbclient //127.0.0.1/t -U 'ann%set by kim 44' -c ls
# The helper rereads who was signed out at most once a second
sleep 1.2
r=$(api "$J" GET /api/session)
[ "$(code "$r")" != 200 ] && pass "the reset ended ann's open session" || fail "the reset ended ann's open session: $r"
refuse "a mismatched pair is refused" bash -c 'printf "one long one\ntwo long two\n" | fellowship-reset-password ann'
refuse "a short password is refused" bash -c 'printf "short\nshort\n" | fellowship-reset-password ann'
refuse "an unknown name is refused before asking" bash -c 'printf "x long one\nx long one\n" | fellowship-reset-password nobody'
refuse "not root is refused" runuser -u nobody -- fellowship-reset-password ann
expect "the refusals changed nothing" smbclient //127.0.0.1/t -U 'ann%root reset 66' -c ls

echo "== clearing and dismissing the alert record"
fnas alert send --severity info "Tour notice" "a notice to dismiss" > /dev/null
r=$(api "$K" POST /api/fnas '{"args":["--json","alert","list","--limit","50","--all"]}')
body "$r" | python3 -c 'import json,sys
sys.exit(0 if any(a["title"] == "Tour notice" for a in json.loads(json.load(sys.stdin)["output"])) else 1)' \
    && pass "the notice is in the record" || fail "the notice is in the record: $r"
t=$(body "$r" | python3 -c 'import json,sys
print(next(a["time"] for a in json.loads(json.load(sys.stdin)["output"]) if a["title"] == "Tour notice"))')
r=$(api "$K" POST /api/fnas "{\"args\":[\"alert\",\"dismiss\",\"--time\",\"$t\",\"--title\",\"Tour notice\"]}")
[ "$(code "$r")" = 200 ] && pass "an admin dismisses a notice" || fail "an admin dismisses a notice: $r"
r=$(api "$K" POST /api/fnas '{"args":["--json","alert","list","--limit","50"]}')
body "$r" | python3 -c 'import json,sys
sys.exit(0 if not any(a["title"] == "Tour notice" for a in json.loads(json.load(sys.stdin)["output"])) else 1)' \
    && pass "the widget's list leaves it out" || fail "the widget's list leaves it out: $r"
r=$(api "$K" POST /api/fnas '{"args":["--json","alert","list","--limit","50","--all"]}')
body "$r" | python3 -c 'import json,sys
sys.exit(0 if any(a["title"] == "Tour notice" and a.get("dismissed")
                  for a in json.loads(json.load(sys.stdin)["output"])) else 1)' \
    && pass "the full record keeps it, marked" || fail "the full record keeps it, marked: $r"
before=$(fnas --json health | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["problems"]))')
r=$(api "$K" POST /api/fnas '{"args":["alert","clear"]}')
[ "$(code "$r")" = 200 ] && pass "an admin clears the history" || fail "an admin clears the history: $r"
after=$(fnas --json health | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["problems"]))')
[ "$before" = "$after" ] && pass "clearing leaves Health's findings alone" || fail "clearing leaves Health's findings alone: $before -> $after"
r=$(api "$K" POST /api/fnas '{"args":["--json","alert","list","--limit","50","--all"]}')
body "$r" | python3 -c 'import json,sys; sys.exit(0 if json.loads(json.load(sys.stdin)["output"]) == [] else 1)' \
    && pass "the record is empty" || fail "the record is empty: $r"
NA=$(mktemp)
login "$NA" ann 'root reset 66' > /dev/null
r=$(api "$NA" POST /api/fnas '{"args":["alert","clear"]}')
[ "$(code "$r")" = 403 ] && pass "a non-admin cannot clear" || fail "a non-admin cannot clear: $r"
