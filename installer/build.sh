#!/bin/bash
# Build the graphical installer into the live image's own packages. It only
# lives in the live system: installing removes it from the installed one.
set -e

cd "$(dirname "$0")"

OUT="$(pwd)/../config/packages.chroot"
WORK="$(mktemp -d)"

cp -r . "$WORK/fellowship-installer"
cd "$WORK/fellowship-installer"
rm -rf web/node_modules web/dist
# The page borrows the desktop's stylesheet and wallpapers
mkdir -p ../nas/packages/fellowship-nas-web
cp -r "$OLDPWD/../nas/packages/fellowship-nas-web/web" ../nas/packages/fellowship-nas-web/
(cd web && npm ci --no-audit --no-fund && npm run build)
dpkg-buildpackage -b -us -uc

mkdir -p "$OUT"
rm -f "$OUT"/fellowship-installer_*.deb
cp ../fellowship-installer_*.deb "$OUT/"
rm -rf "$WORK"
