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
    /usr/lib/fellowship-nas-web/fellowship-nas-helper &
    sleep 1
    echo "Open http://localhost:8080"
    exec setpriv --reuid=fnas-web --regid=fnas-web --init-groups --inh-caps=+net_bind_service \
        --ambient-caps=+net_bind_service /usr/lib/fellowship-nas-web/fellowship-nas-web \
        -static /src/web/dist -dev-http -listen :80
'
