#!/bin/bash
# Run the real server and helper in a Debian container, with real PAM and a
# fake fnas, then open http://localhost:8080. It serves plain HTTP so any
# browser opens it without a certificate warning; it only listens on this
# machine. Rebuild the desktop with `npm run build` (or `npm run watch`) in
# web/ and reload the page.
#
# Test accounts, for this container only:
#   admin / fellowship-admin   (in nas-admins)
#   guest / fellowship-guest   (not an admin)
set -e

cd "$(dirname "$0")/.."
[ -d web/dist ] || (cd web && npm run build)

docker run --rm -it -p 127.0.0.1:8080:80 -v "$PWD:/src:ro" debian:trixie bash -c '
    set -e
    apt-get update -qq
    apt-get install -y -qq golang-go libpam0g-dev gcc python3 > /dev/null
    cd /src
    GOCACHE=/tmp/gocache GOFLAGS=-buildvcs=false go build -o /usr/lib/fellowship-nas-web/ ./cmd/...
    install -m 755 dev/fake-fnas /usr/sbin/fnas
    cp debian/fellowship-nas-web.pam /etc/pam.d/fellowship-nas-web
    groupadd --system nas-admins
    useradd --system --no-create-home --shell /usr/sbin/nologin fnas-web
    useradd -m -s /bin/bash -G nas-admins admin
    useradd -m -s /bin/bash guest
    echo admin:fellowship-admin | chpasswd
    echo guest:fellowship-guest | chpasswd
    # Two shares with real permissions, so Files is refused by the kernel as well
    # as by fnas: media is for both people, family only for nas-admins
    groupadd --system media
    usermod -aG media admin
    usermod -aG media guest
    python3 /src/dev/sample-files.py /mnt/tank/shares
    chown -R root:media /mnt/tank/shares/media
    chown -R root:media /mnt/tank/shares/archive
    chown -R root:nas-admins /mnt/tank/shares/family
    # A share fnas calls read and write is writable by its group, as the ACLs on
    # a real NAS would make it; family is for nas-admins alone
    find /mnt/tank/shares -type d -exec chmod 2770 {} +
    find /mnt/tank/shares -type f -exec chmod 0660 {} +
    # People walk through the pools folders to reach their share
    chmod 755 /mnt/tank /mnt/tank/shares
    # Snapshots are root only here, exactly as ZFS leaves .zfs on a real NAS, so
    # the helper has to be the one that opens an earlier version
    find /mnt/tank/shares -type d -name .zfs -exec chmod 700 {} +
    /usr/lib/fellowship-nas-web/fellowship-nas-helper &
    sleep 1
    echo "Open http://localhost:8080"
    exec setpriv --reuid=fnas-web --regid=fnas-web --init-groups --inh-caps=+net_bind_service \
        --ambient-caps=+net_bind_service /usr/lib/fellowship-nas-web/fellowship-nas-web \
        -static /src/web/dist -dev-http -listen :80
'
