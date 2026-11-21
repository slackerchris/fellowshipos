#!/bin/bash
# Build the graphical installer into the live image's own packages. It only
# lives in the live system: installing removes it from the installed one.
set -e

cd "$(dirname "$0")"
# The shared theme files, which the page's build reads from outside its copy
export FELLOWSHIP_THEMES_DIR="$(pwd)/../themes"
# The release version stamps the copy, never the checkout
VERSION="$(cat "$(pwd)/../VERSION")"

OUT="$(pwd)/../config/packages.chroot"
WORK="$(mktemp -d)"

cp -r . "$WORK/fellowship-installer"
sed -i "1s/([^)]*)/(${VERSION})/" "$WORK/fellowship-installer/debian/changelog"
cd "$WORK/fellowship-installer"
rm -rf web/node_modules web/dist
(cd web && npm ci --no-audit --no-fund && npm run build)
dpkg-buildpackage -b -us -uc

mkdir -p "$OUT"
rm -f "$OUT"/fellowship-installer_*.deb
cp ../fellowship-installer_*.deb "$OUT/"
rm -rf "$WORK"
