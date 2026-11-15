#!/bin/bash
# SFTP and read-only shares, against a real sshd and kernel: someone who may
# only read a share can list and download from it, but can't upload, overwrite,
# rename, make folders or delete, even where the folder's own permissions would
# let them. Runs fnas's own view code in a privileged Debian trixie container.
#   nas/tests/integration/sftp-readonly.sh
# Every line of the result says PASS or FAIL; the script fails on any FAIL.
set -e
cd "$(dirname "$0")"
if [ "$1" != --inside ]; then
    out=$(mktemp)
    trap 'rm -f "$out"' EXIT
    docker run --rm --privileged -v "$(pwd)/sftp-readonly.sh:/run.sh:ro" \
        -v "$(cd ../../packages/fellowship-nas-tools && pwd):/src:ro" debian:trixie bash /run.sh --inside | tee "$out"
    ! grep -q '^FAIL' "$out"
    exit
fi
set -e
apt-get update -qq >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server openssh-client python3 >/dev/null 2>&1
cd /src && export PYTHONPATH=/src
groupadd -r nas-sftp; useradd -m -s /usr/sbin/nologin -G nas-sftp kim
# Two shares; the folders are writable by kim, so only the mount can stop a write
for s in readonly writable; do mkdir -p /srv/$s; echo "hello from $s" > /srv/$s/file.txt; chown -R kim /srv/$s; done
python3 - <<'PY'
from fellowship_nas import sftp
print("problems:", sftp.sync_view("kim", [
    {"share": "readonly", "path": "/srv/readonly", "level": "read"},
    {"share": "writable", "path": "/srv/writable", "level": "write"}]))
print("read-only mounts:", sorted(p for p in sftp.read_only_mounts() if "sftp" in p))
PY
ssh-keygen -A >/dev/null; mkdir -p /run/sshd
cat > /etc/ssh/sshd_config.d/t.conf <<'C'
Match Group nas-sftp
    ChrootDirectory /var/lib/fellowship-nas/sftp/%u
    ForceCommand internal-sftp
    AllowTcpForwarding no
C
mkdir -p ~kim/.ssh; ssh-keygen -q -t ed25519 -N '' -f /root/k; cp /root/k.pub ~kim/.ssh/authorized_keys
chown -R kim ~kim/.ssh; chmod 700 ~kim/.ssh
/usr/sbin/sshd
echo upload > /tmp/up.txt
t() { # t <label> <expect ok|refused> <sftp command>
  if printf '%s\n' "$3" | sftp -q -b - -i /root/k -o StrictHostKeyChecking=no -o BatchMode=yes kim@127.0.0.1 >/tmp/o 2>&1
  then got=ok; else got=refused; fi
  [ "$got" = "$2" ] && r=PASS || r=FAIL; echo "$r  $1: $got (expected $2)"
}
for s in readonly writable; do
  w=$([ $s = writable ] && echo ok || echo refused)
  echo "== $s share"
  t "list"       ok "ls /$s"
  t "download"   ok "get /$s/file.txt /tmp/got-$s.txt"
  t "upload"     $w "put /tmp/up.txt /$s/new.txt"
  t "overwrite"  $w "put /tmp/up.txt /$s/file.txt"
  t "rename"     $w "rename /$s/file.txt /$s/moved.txt"
  t "mkdir"      $w "mkdir /$s/folder"
  t "delete"     $w "rm /$s/$([ $s = writable ] && echo moved.txt || echo file.txt)"
done
echo "== access to writable changes to read-only, on the live view"
python3 -c 'from fellowship_nas import sftp; print("problems:", sftp.sync_view("kim", [{"share": "readonly", "path": "/srv/readonly", "level": "read"}, {"share": "writable", "path": "/srv/writable", "level": "read"}]))'
t "upload after the change" refused "put /tmp/up.txt /writable/after.txt"
t "list after the change" ok "ls /writable"
echo "== the read-only share's file afterwards: $(cat /srv/readonly/file.txt)"
echo "== downloaded: $(cat /tmp/got-readonly.txt)"
echo "== outside the chroot: $(printf 'ls /srv\n' | sftp -q -b - -i /root/k -o StrictHostKeyChecking=no kim@127.0.0.1 2>&1 | tail -1)"
