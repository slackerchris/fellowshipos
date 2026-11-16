#!/bin/bash
# Migrating a NAS whose people are Linux logins to a separated one, and back
# (docs/fellowship-nas-account-separation.md, phase 5), against the real
# packages, Samba, helper and web server. It starts from the old model, with an
# installer-made sudo admin with no Samba password, an ordinary person, a shell
# account whose two passwords already differ, and a turned-off account.
# Phase 7 (docs/fellowship-nas-ssh-samba-plan.md) is checked here too: the
# migrated SSH and Samba policy, SFTP through the store, migrated keys with the
# store as their only source, the refused Windows change, and undo putting the
# PAM files and sshd's settings back.
#   nas/packages/build.sh      (builds the packages this installs)
#   nas/tests/integration/separate.sh
# Runs in a privileged Debian trixie container. Every result line says PASS or
# FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    repo=$(cd ../../../config/includes.chroot/usr/share/fellowshipos/nas-repo && pwd)
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/separate.sh:/run.sh:ro" -v "$repo:/repo:ro" \
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
    fellowship-nas-smb smbclient openssh-server curl > /tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
ssh-keygen -A > /dev/null; mkdir -p /run/sshd
printf '#!/bin/sh\necho "$SSHPASS"\n' > /tmp/askpass && chmod +x /tmp/askpass
# The stopped runs try wrong passwords on purpose; OpenSSH's per-source
# penalties would refuse the later, correct attempts, so they are off here only
echo "PerSourcePenalties no" > /etc/ssh/sshd_config.d/00-test.conf

echo "== packages installed; this NAS isn't separated"
echo "== a NAS made the old way"
# The old installer: a sudo account with a Linux password and nothing for Samba;
# first boot put it among the NAS admins
useradd -m -s /bin/bash -G sudo,users tester
echo 'tester:tester old 1' | chpasswd
gpasswd -a tester nas-admins > /dev/null
mkdir -p /home/tester/docs && echo hello > /home/tester/docs/a.txt && chown -R tester:tester /home/tester/docs
echo 'ann password 22' | fnas user create ann --password-stdin > /dev/null
# A shell account whose password was changed with passwd a day after its Samba one
echo 'bob old smb 33' | fnas user create bob --kind ssh --password-stdin > /dev/null
echo 'bob:bob new linux 44' | chpasswd
chage -d $(( $(date +%s) / 86400 + 1 )) bob
# bob keeps an SSH key in his home, as a shell account would: migration copies
# it into the store, and phase 7 makes the store its only source
ssh-keygen -t ed25519 -f /tmp/bobkey -N '' -q
ssh-keygen -t ed25519 -f /tmp/bobkey2 -N '' -q
mkdir -p /home/bob/.ssh
cp /tmp/bobkey.pub /home/bob/.ssh/authorized_keys
chown -R bob:bob /home/bob/.ssh
chmod 700 /home/bob/.ssh; chmod 600 /home/bob/.ssh/authorized_keys
echo 'carol pass 555' | fnas user create carol --password-stdin > /dev/null
fnas user set carol --enabled off > /dev/null
mkdir -p /srv/t && chmod 777 /srv/t && printf '[t]\npath = /srv/t\nread only = no\n' >> /etc/samba/smb.conf
smbd -D; sleep 1
uid_before=$(id -u tester); owner_before=$(stat -c %u:%g /home/tester/docs/a.txt)
smb() { smbclient //127.0.0.1/t -U "$1%$2" -c ls > /dev/null 2>&1; }

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

desk() { # desk <user> <password>: logs in through the real desktop, prints admin true/false
    curl -sk -H 'Content-Type: application/json' -H 'Origin: https://127.0.0.1' \
        -d "{\"user\":\"$1\",\"password\":\"$2\"}" https://127.0.0.1/api/login \
        | python3 -c 'import json,sys
try:
    answer = json.load(sys.stdin)
    print(answer["admin"] if "admin" in answer else "refused")
except Exception: print("refused")'
}
[ "$(desk tester 'tester old 1')" = True ] && pass "before: tester logs in as an admin, through PAM" || fail "before: tester logs in"
refuse "before: tester has no Samba password (the old installer's gap)" smb tester 'tester old 1'

echo "== the dry run"
out=$(SUDO_USER=tester fnas accounts separate)
grep -q "Nothing has changed" <<< "$out" && pass "it says nothing changed" || fail "it says nothing changed"
grep -A3 "^  tester" <<< "$out" | grep -q "SMB password      NEEDS SETTING" && pass "tester's missing SMB password is shown" || fail "tester's missing SMB password is shown: $out"
grep -A6 "^  bob" <<< "$out" | grep -q "possibly different passwords" && pass "bob's possible difference is flagged" || fail "bob's possible difference is flagged"
grep -q "can't prove two passwords match" <<< "$out" && pass "and it says dates can't prove a match" || fail "and it says dates can't prove a match"
grep -A6 "^  bob" <<< "$out" | grep -q "becomes Files over SFTP" && pass "bob's shell is said to go" || fail "bob's shell is said to go"
grep -A6 "^  tester" <<< "$out" | grep -q "sudo ends for this account" && pass "tester is told sudo ends" || fail "tester is told sudo ends"
grep -q "set a password for tester (you" <<< "$out" && pass "tester's own password is asked before anything" || fail "tester's own password is asked before anything"
[ ! -e /var/lib/fellowship-nas/accounts.json ] && id -nG tester | grep -qw sudo && pass "the dry run changed nothing" || fail "the dry run changed nothing"

echo "== a run that can't prove the desktop login stops in time"
printf 'root new pw 1\nroot new pw 1\ntester new pw 66\ntester new pw 66\n' | \
    SUDO_USER=tester fnas accounts separate --yes --login-url https://127.0.0.1:9/api/login > /tmp/stop1 2>&1 \
    && fail "it stopped" || pass "it stopped"
grep -q "Stopped before anything was locked" /tmp/stop1 && pass "and said why" || fail "and said why: $(tail -3 /tmp/stop1)"
[ ! -e /var/lib/fellowship-nas/accounts.json ] && pass "no store is left" || fail "no store is left"
id -nG tester | grep -qw sudo && [ "$(getent passwd tester | cut -d: -f7)" = /bin/bash ] && pass "tester keeps sudo and a shell" || fail "tester keeps sudo and a shell"
[ "$(desk tester 'tester old 1')" = True ] && pass "tester's old login still works" || fail "tester's old login still works"
refuse "and Samba is as it was (still no password for tester)" smb tester 'tester new pw 66'

echo "== a run with a wrong root password stops in time"
printf 'not roots\ntester new pw 66\ntester new pw 66\n' | SUDO_USER=tester fnas accounts separate --yes > /tmp/stop2 2>&1 \
    && fail "it stopped" || pass "it stopped"
grep -q "root's password doesn't match" /tmp/stop2 && pass "because root's password doesn't match" || fail "because root's password doesn't match: $(tail -3 /tmp/stop2)"
id -nG tester | grep -qw sudo && [ ! -e /var/lib/fellowship-nas/accounts.json ] && pass "nothing was taken away" || fail "nothing was taken away"

echo "== separating"
printf 'root new pw 1\ntester new pw 66\ntester new pw 66\n' | SUDO_USER=tester fnas accounts separate --yes > /tmp/sep 2>&1 \
    && pass "it separated" || fail "it separated: $(tail -5 /tmp/sep)"
grep -q "Checked: tester logs in to the desktop as a NAS admin" /tmp/sep && pass "it proved the NAS admin login first" || fail "it proved the NAS admin login first"
grep -q "Checked: root can log in at the console" /tmp/sep && pass "and root's console login" || fail "and root's console login"
grep -qE "^    [A-Z0-9]{5}(-[A-Z0-9]{5}){7}$" /tmp/sep && pass "it showed a recovery key" || fail "it showed a recovery key"
[ "$(id -u tester)" = "$uid_before" ] && [ "$(stat -c %u:%g /home/tester/docs/a.txt)" = "$owner_before" ] && pass "UIDs and file owners are unchanged" || fail "UIDs and file owners are unchanged"
for who in tester ann bob carol; do
    [ "$(getent shadow $who | cut -d: -f2 | cut -c1)" = "!" ] && ok=1 || ok=
    [ -n "$ok" ] && pass "$who's Linux password is locked" || fail "$who's Linux password is locked"
done
refuse "tester has no sudo" bash -c 'id -nG tester | grep -qw sudo'
# tester had a login shell, so like every shell account becomes Files over SFTP:
# a shell that refuses, file access only
[ "$(getent passwd tester | cut -d: -f7)" = /usr/local/lib/fellowship-nas/sftp-only ] && pass "tester's shell is gone (Files over SFTP only)" || fail "tester's shell is gone: $(getent passwd tester)"
[ "$(getent passwd bob | cut -d: -f7)" = /usr/local/lib/fellowship-nas/sftp-only ] && pass "bob has Files over SFTP, not a shell" || fail "bob has Files over SFTP: $(getent passwd bob)"
grep -q "^   unix password sync = yes" /etc/samba/smb.conf && grep -q "^   pam password change = yes" /etc/samba/smb.conf \
    && pass "Samba keeps sync on: the locked password refuses Windows changes" || fail "Samba keeps sync on"
grep -q "^password   requisite   pam_deny.so" /etc/pam.d/samba && pass "Samba's PAM password path is denied" || fail "Samba's PAM password path is denied"
grep -q "^AllowGroups nas-sftp$" /etc/ssh/sshd_config.d/fellowship.conf && pass "sshd admits SFTP only, root off" || fail "sshd admits SFTP only, root off"
grep -q "pam_exec.so expose_authtok /usr/lib/fellowship-nas/sftp-auth" /etc/pam.d/sshd && pass "the SFTP PAM stack is in place" || fail "the SFTP PAM stack is in place"

echo "== every credential that worked still works"
[ "$(desk tester 'tester new pw 66')" = True ] && pass "tester: desktop, as a NAS admin" || fail "tester: desktop"
expect "tester: SMB, with the password set during migration" smb tester 'tester new pw 66'
[ "$(desk ann 'ann password 22')" = False ] && pass "ann: desktop" || fail "ann: desktop"
expect "ann: SMB" smb ann 'ann password 22'
[ "$(desk bob 'bob new linux 44')" = False ] && pass "bob: desktop, with his Linux password" || fail "bob: desktop"
expect "bob: SMB, still with his Samba password" smb bob 'bob old smb 33'
[ "$(desk carol 'carol pass 555')" = refused ] && pass "carol, turned off, stays off on the desktop" || fail "carol stays off on the desktop"
refuse "and on SMB" smb carol 'carol pass 555'
echo 'bob one pass 77' | fnas user password bob --password-stdin > /dev/null
[ "$(desk bob 'bob one pass 77')" = False ] && expect "bob's new NAS password works for SMB too: one again" smb bob 'bob one pass 77' \
    || fail "bob's new password works on the desktop"
refuse "and his old Samba one doesn't" smb bob 'bob old smb 33'

echo "== phase 7: SFTP and Samba after migration"
/usr/sbin/sshd
printf 'ls\n' > /tmp/sftp-batch
sftp_pw() { # sftp_pw <user> <password>. No -b: batch mode implies
    # BatchMode=yes, which disables password authentication
    printf 'ls\n' | SSHPASS="$2" SSH_ASKPASS=/tmp/askpass SSH_ASKPASS_REQUIRE=force \
        sftp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no "$1@127.0.0.1" > /dev/null 2>&1
}
sftp_key() { # sftp_key <user> <keyfile>
    sftp -i "$2" -b /tmp/sftp-batch -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o PreferredAuthentications=publickey -o PasswordAuthentication=no "$1@127.0.0.1" > /dev/null 2>&1
}
expect "tester: SFTP with the NAS password, through the PAM stack" sftp_pw tester 'tester new pw 66'
expect "bob: SFTP with his migrated key, now from the store" sftp_key bob /tmp/bobkey
fnas user keys bob --set-stdin < /tmp/bobkey2.pub > /dev/null
refuse "bob's old key stops working: the store is the only source" sftp_key bob /tmp/bobkey
expect "bob's new key works at once, with no reload" sftp_key bob /tmp/bobkey2
printf 'tester new pw 66\nwindows try 88\nwindows try 88\n' | smbpasswd -r 127.0.0.1 -U tester > /dev/null 2>&1 \
    && fail "the Windows change was accepted" || pass "the Windows change is refused"
expect "tester's SMB password is unchanged" smb tester 'tester new pw 66'
refuse "the attempted one doesn't work" smb tester 'windows try 88'
[ "$(getent shadow tester | cut -d: -f2 | cut -c1)" = "!" ] && pass "tester's Linux password stayed locked" || fail "tester's Linux password stayed locked"

echo "== undo"
fnas accounts separate --undo > /tmp/undo 2>&1 && pass "undo ran" || fail "undo ran: $(tail -3 /tmp/undo)"
id -nG tester | grep -qw sudo && [ "$(getent passwd tester | cut -d: -f7)" = /bin/bash ] && pass "tester has sudo and a shell again" || fail "tester has sudo and a shell again"
[ "$(desk tester 'tester old 1')" = True ] && pass "tester's old password logs in again, through PAM" || fail "tester's old password logs in again"
expect "bob's old Samba password is back" smb bob 'bob old smb 33'
refuse "tester is back to no Samba password, as before" smb tester 'tester new pw 66'
[ ! -e /var/lib/fellowship-nas/accounts.json ] && ls /var/lib/fellowship-nas/accounts.json.undone-* > /dev/null 2>&1 \
    && pass "the store was moved aside, not deleted" || fail "the store was moved aside"
python3 -c 'from fellowship_nas import separate; import sys; sys.exit(0 if separate.root_usable("root new pw 1") in (None, "no login prompt is enabled on the console") else 1)' \
    && pass "root keeps the password it was given" || fail "root keeps the password it was given"
[ "$(stat -c %u:%g /home/tester/docs/a.txt)" = "$owner_before" ] && pass "file owners are still unchanged" || fail "file owners are still unchanged"
# The SSH and Samba settings go back too, PAM files included
pkill -x sshd; sleep 0.5; /usr/sbin/sshd
grep -q pam_exec /etc/pam.d/sshd && fail "undo restored Debian's sshd PAM" || pass "undo restored Debian's sshd PAM"
grep -q pam_deny /etc/pam.d/samba && fail "undo restored Debian's samba PAM" || pass "undo restored Debian's samba PAM"
[ ! -e /etc/ssh/sshd_config.d/fellowship.conf ] && pass "undo removed the separated drop-in" || fail "undo removed the separated drop-in"
expect "tester: shell SSH with the old Linux password, as before" env SSHPASS='tester old 1' \
    SSH_ASKPASS=/tmp/askpass SSH_ASKPASS_REQUIRE=force ssh \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null tester@127.0.0.1 true
