#!/bin/bash
# Build the Fellowship packages into a local apt repo inside the image
set -e

cd "$(dirname "$0")"

REPO="$(pwd)/../../config/includes.chroot/usr/share/fellowshipos/nas-repo"
WORK="$(mktemp -d)"

for package in fellowship-nas-tools fellowship-nas; do
    cp -r "$package" "$WORK/"
    (cd "$WORK/$package" && dpkg-buildpackage -b -us -uc)
done

rm -rf "$REPO"
mkdir -p "$REPO"
cp "$WORK"/*.deb "$REPO/"
cd "$REPO"
dpkg-scanpackages --multiversion . > Packages
rm -rf "$WORK"
