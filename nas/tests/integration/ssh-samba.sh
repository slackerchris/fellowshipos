#!/bin/bash
# Phase 7 (docs/fellowship-nas-ssh-samba-plan.md): SSH and Samba settings on a
# separated NAS, against the real packages, sshd and smbd. SFTP passwords are
# checked against the store through PAM, keys come from the store and nowhere
# else, root's SSH is his own keys-only switch, turning SFTP off never touches
# it, and a Windows password change is refused without drifting either
# password. The old model is checked to be unchanged.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/ssh-samba.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/ssh-samba.sh:/run.sh:ro" -v "$repo:/repo:ro" \
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
    fellowship-nas-smb smbclient openssh-server curl rsyslog > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
rsyslogd; sleep 1
# fnas health reads the kernel's module directory; a container has none
mkdir -p "/lib/modules/$(uname -r)"
# Debian's own PAM files, to prove the old model keeps them
cp /etc/pam.d/sshd /tmp/sshd.pam.debian
cp /etc/pam.d/samba /tmp/samba.pam.debian
ssh-keygen -A > /dev/null; mkdir -p /run/sshd
printf 'ls\n' > /tmp/sftp-batch
# Passwords go to ssh through SSH_ASKPASS_REQUIRE=force: no pty tricks, the
# password is handed over deterministically
printf '#!/bin/sh\necho "$SSHPASS"\n' > /tmp/askpass && chmod +x /tmp/askpass
# This harness tries many wrong passwords on purpose; OpenSSH's per-source
# penalties would refuse the later, correct attempts, so they are off here only
echo "PerSourcePenalties no" > /etc/ssh/sshd_config.d/00-test.conf

echo "== a separated NAS, made the way the installer makes one"
echo 'root:root pass 99' | chpasswd
fnas accounts init > /dev/null
grep -q "^AllowGroups nas-sftp$" /etc/ssh/sshd_config.d/fellowship.conf && pass "the separation hook wrote the SFTP-only drop-in" || fail "the separation hook wrote the SFTP-only drop-in"
grep -q "^PermitRootLogin no$" /etc/ssh/sshd_config.d/fellowship.conf && pass "root SSH is off by default" || fail "root SSH is off by default"
grep -q "pam_exec.so expose_authtok /usr/lib/fellowship-nas/sftp-auth" /etc/pam.d/sshd && pass "the SFTP PAM stack is in place" || fail "the SFTP PAM stack is in place"
grep -q "^password   requisite   pam_deny.so" /etc/pam.d/samba && pass "Samba's password path is denied" || fail "Samba's password path is denied"
grep -q "^   unix password sync = yes" /etc/samba/smb.conf && pass "Samba's sync stays on (the lock refuses the change)" || fail "Samba's sync stays on"
echo 'admin password 1' | fnas user create kim --full-name Kim --admin --kind sftp --password-stdin > /dev/null
echo 'ann password 22' | fnas user create ann --kind sftp --password-stdin > /dev/null
echo 'sam password 33' | fnas user create sam --password-stdin > /dev/null
mkdir -p /srv/t && chmod 777 /srv/t
printf '[t]\npath = /srv/t\nread only = no\n' >> /etc/samba/smb.conf
smbd -D; sleep 2
/usr/sbin/sshd

sftp_pw() { # sftp_pw <user> <password>: SFTP with a password, as a client does.
    # No -b here: batch mode implies BatchMode=yes, which disables password
    # authentication; the commands go in on stdin instead
    printf 'ls\n' | SSHPASS="$2" SSH_ASKPASS=/tmp/askpass SSH_ASKPASS_REQUIRE=force \
        sftp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no "$1@127.0.0.1" > /dev/null 2>&1
}
sftp_key() { # sftp_key <user> <keyfile>: SFTP with a key
    sftp -i "$2" -b /tmp/sftp-batch -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=publickey -o PasswordAuthentication=no "$1@127.0.0.1" > /dev/null 2>&1
}
ssh_pw() { # ssh_pw <user> <password> [command...]
    local user=$1 password=$2; shift 2
    SSHPASS="$password" SSH_ASKPASS=/tmp/askpass SSH_ASKPASS_REQUIRE=force \
        ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no "$user@127.0.0.1" "$@" > /dev/null 2>&1
}
ssh_key() { # ssh_key <user> <keyfile> [command...]
    local user=$1 key=$2; shift 2
    ssh -i "$key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=publickey -o PasswordAuthentication=no "$user@127.0.0.1" "$@" > /dev/null 2>&1
}
reload_sshd() { pkill -HUP -x sshd; sleep 1; }

echo "== SFTP passwords, checked against the store"
expect "kim: SFTP with her NAS password" sftp_pw kim 'admin password 1'
expect "ann: SFTP with his NAS password" sftp_pw ann 'ann password 22'
refuse "a wrong password is refused" sftp_pw kim nope
refuse "a stranger is refused" sftp_pw nobody 'admin password 1'
refuse "sam, shares-only, is refused at the door" sftp_pw sam 'sam password 33'
out=$(SSHPASS='admin password 1' SSH_ASKPASS=/tmp/askpass SSH_ASKPASS_REQUIRE=force timeout 8 \
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no kim@127.0.0.1 'echo SHELL_WORKED' 2>/dev/null || true)
grep -q SHELL_WORKED <<< "$out" && fail "kim gets no shell: internal-sftp is forced" || pass "kim gets no shell: internal-sftp is forced"
grep -q "    ForceCommand internal-sftp" /etc/ssh/sshd_config.d/fellowship.conf && pass "the Match forces internal-sftp, no forwarding" || fail "the Match forces internal-sftp"

echo "== keys from the store, and nowhere else"
ssh-keygen -t ed25519 -f /tmp/kimkey -N '' -q
ssh-keygen -t ed25519 -f /tmp/kimkey2 -N '' -q
ssh-keygen -t ed25519 -f /tmp/planted -N '' -q
fnas user keys kim --set-stdin < /tmp/kimkey.pub > /dev/null
expect "kim: SFTP with a store key" sftp_key kim /tmp/kimkey
# A key left in the home folder sshd would once have read must not work:
# the store is the only source for SFTP people
mkdir -p /nonexistent/.ssh
cp /tmp/planted.pub /nonexistent/.ssh/authorized_keys
chown -R kim:kim /nonexistent
chmod 700 /nonexistent/.ssh; chmod 600 /nonexistent/.ssh/authorized_keys
refuse "a key in the old home file is not consulted" sftp_key kim /tmp/planted
fnas user keys kim --set-stdin < /tmp/kimkey2.pub > /dev/null
refuse "a replaced key stops working at once, with no reload" sftp_key kim /tmp/kimkey
expect "the new key works" sftp_key kim /tmp/kimkey2
fnas user keys kim --set-stdin < /dev/null > /dev/null
refuse "removed keys stop working" sftp_key kim /tmp/kimkey2
expect "password SFTP still works after the key changes" sftp_pw kim 'admin password 1'
refuse "fnas keeps keys for SFTP people only" bash -c 'fnas user keys sam --set-stdin < /tmp/kimkey.pub'
expect "fnas auth keys answers with the store's keys" bash -c 'fnas user keys ann --set-stdin < /tmp/kimkey2.pub > /dev/null && [ "$(fnas auth keys ann)" = "$(cat /tmp/kimkey2.pub)" ]'
[ -z "$(fnas auth keys root)" ] && pass "fnas auth keys answers nothing for root" || fail "fnas auth keys answers nothing for root"
[ -z "$(fnas auth keys nobody)" ] && pass "and nothing for a stranger" || fail "and nothing for a stranger"

echo "== a turned-off person is refused, keys included"
expect "ann: SFTP with a store key" sftp_key ann /tmp/kimkey2
fnas user set ann --enabled off > /dev/null
refuse "ann, turned off: password SFTP refused" sftp_pw ann 'ann password 22'
refuse "ann, turned off: key SFTP refused too (PAM's account check)" sftp_key ann /tmp/kimkey2
fnas user set ann --enabled on > /dev/null
expect "turned back on, the key works again" sftp_key ann /tmp/kimkey2

echo "== root's own SSH: off, keys only, his alone"
ssh-keygen -t ed25519 -f /tmp/rootkey -N '' -q
refuse "root: password SSH refused while off" ssh_pw root 'root pass 99' true
refuse "root: key SSH refused while off" ssh_key root /tmp/rootkey true
refuse "fellowship-root-ssh on without a key is refused" fellowship-root-ssh on
mkdir -p /root/.ssh; cp /tmp/rootkey.pub /root/.ssh/authorized_keys
chmod 700 /root/.ssh; chmod 600 /root/.ssh/authorized_keys
expect "fellowship-root-ssh on, with a key" fellowship-root-ssh on
reload_sshd
grep -q "^AllowGroups root nas-sftp$" /etc/ssh/sshd_config.d/fellowship.conf && pass "root joins AllowGroups" || fail "root joins AllowGroups"
grep -q "^PermitRootLogin prohibit-password$" /etc/ssh/sshd_config.d/fellowship.conf && pass "root is keys only" || fail "root is keys only"
expect "root: key SSH works" ssh_key root /tmp/rootkey true
refuse "root: password SSH still refused" ssh_pw root 'root pass 99' true
fnas --json ssh show | grep -q '"root_ssh": true' && pass "the desktop can read the state" || fail "the desktop can read the state"
grep -q "fellowship-root-ssh.*root turned SSH on" /var/log/auth.log && pass "the change is written to the journal" || fail "the change is written to the journal"

echo "== SFTP off never touches root's SSH (ruling 2)"
fnas ssh set --enabled off > /dev/null
reload_sshd
grep -q "^AllowGroups root$" /etc/ssh/sshd_config.d/fellowship.conf && pass "the drop-in admits root alone" || fail "the drop-in admits root alone"
refuse "kim: password SFTP refused with SFTP off" sftp_pw kim 'admin password 1'
refuse "ann: key SFTP refused with SFTP off" sftp_key ann /tmp/kimkey2
expect "root: key SSH still works" ssh_key root /tmp/rootkey true
fnas ssh set --enabled on > /dev/null
reload_sshd
expect "SFTP back on" sftp_pw kim 'admin password 1'
fellowship-root-ssh off > /dev/null
reload_sshd
refuse "root: key SSH refused again after off" ssh_key root /tmp/rootkey true
fnas ssh set --enabled off > /dev/null
reload_sshd
refuse "with SFTP and root SSH both off, nobody gets in" sftp_pw kim 'admin password 1'
fnas ssh set --enabled on > /dev/null
reload_sshd

echo "== keys-only SFTP needs no admin key (ruling 3)"
expect "passwords off without any admin key" fnas ssh set --passwords off
reload_sshd
refuse "password SFTP refused" sftp_pw kim 'admin password 1'
expect "key SFTP still works" sftp_key ann /tmp/kimkey2
fnas ssh set --passwords on > /dev/null
reload_sshd

echo "== the SFTP switch survives the service having stopped"
fnas ssh set --enabled off > /dev/null
# What ssh.service's RuntimeDirectory does when it stops: the privsep dir goes,
# and sshd -t then refuses every settings check until something remakes it
rmdir /run/sshd
expect "SFTP turns back on with the directory gone" fnas ssh set --enabled on
[ -d /run/sshd ] && pass "the settings check remade /run/sshd" || fail "the settings check remade /run/sshd"
reload_sshd
expect "SFTP works after the round trip" sftp_pw kim 'admin password 1'

echo "== Health expects the daemon only while SFTP or root SSH is on"
fnas ssh set --enabled off > /dev/null
fellowship-root-ssh off > /dev/null
fnas --json health | python3 -c 'import json,sys
problems = json.load(sys.stdin)["problems"]
sys.exit(0 if not [p for p in problems if p["key"] in ("service-ssh.service", "service-sftp")] else 1)' \
    && pass "both off: no SSH or SFTP finding" || fail "both off: no SSH or SFTP finding"
fnas --json health | python3 -c 'import json,sys
services = json.load(sys.stdin)["services"]
sys.exit(0 if next(s for s in services if s["unit"] == "ssh.service")["state"] == "off" else 1)' \
    && pass "both off: the daemon reads as turned off" || fail "both off: the daemon reads as turned off"
fnas ssh set --enabled on > /dev/null
reload_sshd

echo "== a Windows password change is refused, and nothing drifts (ruling 1)"
printf 'admin password 1\nwindows try 88\nwindows try 88\n' | smbpasswd -r 127.0.0.1 -U kim > /dev/null 2>&1 \
    && fail "the Windows change was accepted" || pass "the Windows change is refused"
expect "kim's SMB password is unchanged" smbclient //127.0.0.1/t -U 'kim%admin password 1' -c ls
refuse "the attempted password doesn't work" smbclient //127.0.0.1/t -U 'kim%windows try 88' -c ls
[ "$(getent shadow kim | cut -d: -f2 | cut -c1)" = "!" ] && pass "kim's Linux password stayed locked" || fail "kim's Linux password stayed locked"
expect "the desktop password still works for SFTP" sftp_pw kim 'admin password 1'

echo "== the desktop's two locks"
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
login() { # login <cookie jar> <user> <password>
    local r
    r=$(api "$1" POST /api/login "{\"user\":\"$2\",\"password\":\"$3\"}")
    head -n -1 <<< "$r" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("csrf",""))
except Exception: pass' > "$1.csrf"
    echo "$r"
}
code() { tail -1 <<< "$1"; }
K=$(mktemp); A=$(mktemp)
login "$K" kim 'admin password 1' > /dev/null
login "$A" ann 'ann password 22' > /dev/null
r=$(api "$K" POST /api/fnas '{"args":["ssh","set","--root","on"]}')
[ "$(code "$r")" = 403 ] && pass "the helper refuses ssh set --root" || fail "the helper refuses ssh set --root: $r"
r=$(api "$K" POST /api/fnas '{"args":["auth","keys","kim"]}')
[ "$(code "$r")" = 403 ] && pass "the helper won't send auth keys" || fail "the helper won't send auth keys: $r"
r=$(api "$A" POST /api/fnas '{"args":["ssh","set","--enabled","off"]}')
[ "$(code "$r")" = 403 ] && pass "a non-admin can't change the service" || fail "a non-admin can't change the service: $r"
r=$(api "$K" POST /api/fnas '{"args":["--json","ssh","show"]}')
[ "$(code "$r")" = 200 ] && grep -q root_ssh <<< "$r" && pass "the desktop reads the SSH state, root SSH included" || fail "the desktop reads the SSH state: $r"
r=$(api "$K" POST /api/fnas '{"args":["ssh","set","--enabled","on"]}')
[ "$(code "$r")" = 200 ] && pass "an admin can change the service" || fail "an admin can change the service: $r"
grep -q "fellowship-nas-maintenance.*kim asked for: ssh set --enabled on" /var/log/auth.log \
    && pass "the journal says kim asked for it" || fail "the journal says kim asked for it"
refuse "fnas itself refuses auth keys to a caller" fnas --caller kim auth keys kim

echo "== the old model is unchanged"
mv /var/lib/fellowship-nas/accounts.json /tmp/store.json
cp /tmp/sshd.pam.debian /etc/pam.d/sshd
cp /tmp/samba.pam.debian /etc/pam.d/samba
fnas ssh refresh > /dev/null
reload_sshd
grep -q "^AllowGroups nas-ssh nas-sftp$" /etc/ssh/sshd_config.d/fellowship.conf && pass "old model: both groups, as before" || fail "old model: both groups"
grep -q "^PermitRootLogin no$" /etc/ssh/sshd_config.d/fellowship.conf && pass "old model: root never logs in" || fail "old model: root never logs in"
grep -q pam_exec /etc/pam.d/sshd && fail "old model: refresh leaves Debian's sshd PAM alone" || pass "old model: refresh leaves Debian's sshd PAM alone"
useradd -m -s /bin/bash shelluser
echo 'shelluser:shell pass 1' | chpasswd
fnas user set shelluser --kind ssh > /dev/null
expect "old model: shell SSH with the Linux password" ssh_pw shelluser 'shell pass 1' true
echo 'sftp pass 22' | fnas user create sftpuser --kind sftp --password-stdin > /dev/null
expect "old model: SFTP with the Linux password" sftp_pw sftpuser 'sftp pass 22'
refuse "old model: root key SSH still refused" ssh_key root /tmp/rootkey true
refuse "old model: passwords off still needs an admin key" fnas ssh set --passwords off
grep -q pam_deny /etc/pam.d/samba && fail "old model: Debian's samba PAM untouched" || pass "old model: Debian's samba PAM untouched"
