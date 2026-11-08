#!/bin/bash
# Build the Fellowship packages into a local apt repo inside the image
set -e

cd "$(dirname "$0")"

REPO="$(pwd)/../../config/includes.chroot/usr/share/fellowshipos/nas-repo"
WORK="$(mktemp -d)"

for package in fellowship-nas-tools fellowship-nas fellowship-nas-smb fellowship-desktop; do
    cp -r "$package" "$WORK/"
    if [ -d "$WORK/$package/web" ]; then
        # Bundled here, so the .deb needs no Node and the checkout gets no build files
        rm -rf "$WORK/$package/web/node_modules" "$WORK/$package/web/dist"
        (cd "$WORK/$package/web" && npm ci --no-audit --no-fund && npm run build)
    fi
    (cd "$WORK/$package" && dpkg-buildpackage -b -us -uc)
done

rm -rf "$REPO"
mkdir -p "$REPO"
cp "$WORK"/*.deb "$REPO/"
cd "$REPO"
dpkg-scanpackages --multiversion . > Packages
rm -rf "$WORK"
