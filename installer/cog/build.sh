#!/bin/bash
# Build cog 0.18.5 for trixie into the live image's own packages.
#
# The installer's screen is cage running cog. Trixie's cog 0.18.4 sends the
# wrong button code with Wayland pointer events, so WebKit fires mousedown and
# mouseup but never click: with a mouse nothing on the installer can be
# pressed, while the keyboard still works. Upstream fixed it in 0.18.5
# (Igalia/cog#760, "wl: fix the button of pointer button events"), which Debian
# has in sid. This rebuilds that exact source for trixie. Its version sorts
# below 0.18.5-1, so a trixie update to a fixed cog replaces it.
#
# Drop this once trixie ships cog 0.18.5 or later.
set -e

cd "$(dirname "$0")"

OUT="$(pwd)/../../config/packages.chroot"
SNAPSHOT="https://snapshot.debian.org/archive/debian/20250530T143637Z/pool/main/c/cog"
DSC="cog_0.18.5-1.dsc"
DSC_SHA256="fd845a0c44b30f84e6d95e7b2951f345b092f44ae027f0a3f1a4cf6b4a719265"
VERSION="0.18.5-1~fellowship1"
WORK="$(mktemp -d)"

apt-get install -y -qq curl dpkg-dev > /dev/null
cd "$WORK"
# The .dsc is pinned here; it pins the other three files, which dpkg-source checks
for file in "$DSC" cog_0.18.5.orig.tar.xz cog_0.18.5.orig.tar.xz.asc cog_0.18.5-1.debian.tar.xz; do
    curl -sfL -o "$file" "$SNAPSHOT/$file"
done
echo "$DSC_SHA256  $DSC" | sha256sum -c --quiet
dpkg-source -x "$DSC" cog
apt-get build-dep -y -qq ./cog > /dev/null

cd cog
{
    echo "cog ($VERSION) trixie; urgency=medium"
    echo
    echo "  * Rebuild sid's 0.18.5-1 for trixie, for working mouse clicks (cog#760)."
    echo
    echo " -- FellowshipOS <fellowshipos@localhost>  $(date -R)"
    echo
    cat debian/changelog
} > debian/changelog.new
mv debian/changelog.new debian/changelog
DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b -us -uc

mkdir -p "$OUT"
rm -f "$OUT"/cog_*.deb
cp ../cog_"$VERSION"_amd64.deb "$OUT/"
rm -rf "$WORK"
