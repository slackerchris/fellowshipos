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
#   dana  / fellowship-dana    (not an admin; a share of her own)
#
# Set FELLOWSHIP_DEV_TLS=1 to serve HTTPS on 127.0.0.1:9443 with a certificate
# made here, instead of plain HTTP on 127.0.0.1:8080. WebDAV only answers over
# HTTPS, so its walks need this.
set -e

cd "$(dirname "$0")/.."
[ -d web/dist ] || (cd web && npm run build)

# 8443 is the VM harness's, so HTTPS here uses 9443
TLS_PORT="${FELLOWSHIP_DEV_TLS_PORT:-9443}"
if [ "${FELLOWSHIP_DEV_TLS:-}" = "1" ]; then
    PUBLISH="-p 127.0.0.1:$TLS_PORT:443"
else
    PUBLISH="-p 127.0.0.1:8080:80"
fi
docker run --rm -it $PUBLISH -e FELLOWSHIP_DEV_TLS -e FELLOWSHIP_DEV_TLS_PORT="$TLS_PORT" \
    -v "$PWD:/src:ro" debian:trixie bash -c '
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
    useradd -m -s /bin/bash dana
    echo admin:fellowship-admin | chpasswd
    echo guest:fellowship-guest | chpasswd
    echo dana:fellowship-dana | chpasswd
    # Two shares with real permissions, so Files is refused by the kernel as well
    # as by fnas: media is for both people, family only for nas-admins
    groupadd --system media
    usermod -aG media admin
    usermod -aG media guest
    usermod -aG media dana
    python3 /src/dev/sample-files.py /mnt/tank/shares
    chown -R root:media /mnt/tank/shares/media
    chown -R root:media /mnt/tank/shares/archive
    chown -R root:nas-admins /mnt/tank/shares/family
    # A share for dana alone, so another regular account is refused by the kernel too
    chown -R dana:dana /mnt/tank/shares/private
    # A share fnas calls read and write is writable by its group, as the ACLs on
    # a real NAS would make it; family is for nas-admins alone
    find /mnt/tank/shares -type d -exec chmod 2770 {} +
    find /mnt/tank/shares -type f -exec chmod 0660 {} +
    # People walk through the pools folders to reach their share
    chmod 755 /mnt/tank /mnt/tank/shares
    # Snapshots are root only here, exactly as ZFS leaves .zfs on a real NAS, so
    # the helper has to be the one that opens an earlier version
    find /mnt/tank/shares -type d -name .zfs -exec chmod 700 {} +
    # HTTPS, for anything that only works encrypted, such as WebDAV; the
    # certificate is made here and is trusted by nobody, which is the point
    serve="-dev-http -listen :80"
    where="http://localhost:8080"
    if [ "$FELLOWSHIP_DEV_TLS" = "1" ]; then
        mkdir -p /etc/fellowship-nas/web
        openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
            -keyout /etc/fellowship-nas/web/key.pem -out /etc/fellowship-nas/web/cert.pem \
            > /dev/null 2>&1
        chown fnas-web:fnas-web /etc/fellowship-nas/web/key.pem
        chmod 600 /etc/fellowship-nas/web/key.pem
        serve=""
        where="https://localhost:${FELLOWSHIP_DEV_TLS_PORT:-9443}"
    fi
    /usr/lib/fellowship-nas-web/fellowship-nas-helper &
    sleep 1
    echo "Open $where"
    exec setpriv --reuid=fnas-web --regid=fnas-web --init-groups --inh-caps=+net_bind_service \
        --ambient-caps=+net_bind_service /usr/lib/fellowship-nas-web/fellowship-nas-web \
        -static /src/web/dist $serve
'
