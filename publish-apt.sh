#!/bin/bash
# Publish the built fellowship-desktop package to the signed apt repository
# that GitHub Pages serves at https://slackerchris.github.io/fellowshipos-apt/,
# so an installed FellowshipOS Desktop takes desktop fixes with apt instead of
# a reinstall. Modelled on FellowshipNAS's publish-apt.sh, with its own key and
# its own Pages repository; neither depends on the other.
#
# Run desktop/build.sh first (or FELLOWSHIP_EDITIONS=desktop ./build.sh): it
# leaves fellowship-desktop_<VERSION>_amd64.deb in config/packages.chroot.
#
# The signing key lives in this machine's GPG keyring and nowhere else; this
# script signs here and pushes only the signed repository. Back the private
# key up (docs/desktop-update-feed.md says how):
#   gpg --armor --export-secret-keys 9A9055842DE0422D0D9B7AFD73D46C9F680BB715
#
#   ./publish-apt.sh [--no-push]
# FELLOWSHIP_APT_DIR overrides the working copy of the Pages repository
# (default ~/Git/fellowshipos-apt). FELLOWSHIP_APT_KEY and FELLOWSHIP_APT_DEB
# sign with another key and publish another file (tests/apt-feed.sh uses both).
set -e
cd "$(dirname "$0")"

APT="${FELLOWSHIP_APT_DIR:-$HOME/Git/fellowshipos-apt}"
KEY="${FELLOWSHIP_APT_KEY:-9A9055842DE0422D0D9B7AFD73D46C9F680BB715}"
VERSION="$(cat VERSION)"
SUITE=trixie
DEB="${FELLOWSHIP_APT_DEB:-config/packages.chroot/fellowship-desktop_${VERSION}_amd64.deb}"

[ -f "$DEB" ] || { echo "no $DEB: run desktop/build.sh for $VERSION first" >&2; exit 1; }
gpg --list-secret-keys "$KEY" > /dev/null 2>&1 || { echo "the signing key $KEY is not in this machine's keyring" >&2; exit 1; }
[ -d "$APT/.git" ] || { echo "$APT is not a git checkout of the Pages repository" >&2; exit 1; }

# A version already published is never replaced: apt caches by version, so
# a different file under the same number would fail its checksum on machines
# that saw the first one. Bump VERSION instead.
POOL="$APT/pool/main/f/fellowship-desktop"
if [ -f "$POOL/$(basename "$DEB")" ] && ! cmp -s "$DEB" "$POOL/$(basename "$DEB")"; then
    echo "fellowship-desktop $VERSION is already published with different contents: bump VERSION" >&2
    exit 1
fi
mkdir -p "$POOL" "$APT/dists/$SUITE/main/binary-amd64"
cp "$DEB" "$POOL/"

# Old versions stay in the pool; --multiversion lists them all and apt picks
# the newest
(cd "$APT" && dpkg-scanpackages --multiversion --arch amd64 pool/main 2>/dev/null \
    > "dists/$SUITE/main/binary-amd64/Packages")
gzip -9cn "$APT/dists/$SUITE/main/binary-amd64/Packages" > "$APT/dists/$SUITE/main/binary-amd64/Packages.gz"

# The Release file names every index with its checksums; apt checks these
# against the signature before trusting a single package list
python3 - "$APT/dists/$SUITE" "$VERSION" <<'EOF'
import email.utils
import hashlib
import os
import sys

directory, version = sys.argv[1], sys.argv[2]
suite = os.path.basename(directory)
lines = ["Origin: FellowshipOS", "Label: FellowshipOS", f"Suite: {suite}", f"Codename: {suite}",
         f"Version: {version}", "Date: " + email.utils.formatdate(usegmt=True),
         "Architectures: amd64", "Components: main",
         "Description: FellowshipOS Desktop updates for Debian trixie"]
for label, algorithm in (("MD5Sum", "md5"), ("SHA256", "sha256"), ("SHA512", "sha512")):
    lines.append(label + ":")
    for root, _, files in os.walk(directory):
        for name in sorted(files):
            if name in ("Release", "InRelease", "Release.gpg"):
                continue
            path = os.path.join(root, name)
            with open(path, "rb") as file:
                data = file.read()
            lines.append(f" {hashlib.new(algorithm, data).hexdigest()} {len(data):>16} "
                         + os.path.relpath(path, directory))
with open(os.path.join(directory, "Release"), "w") as file:
    file.write("\n".join(lines) + "\n")
EOF

gpg --batch --yes --local-user "$KEY" --clearsign -o "$APT/dists/$SUITE/InRelease" "$APT/dists/$SUITE/Release"
gpg --batch --yes --local-user "$KEY" --detach-sign --armor -o "$APT/dists/$SUITE/Release.gpg" "$APT/dists/$SUITE/Release"

# Pages must serve the files as they are, not through Jekyll
[ -f "$APT/.nojekyll" ] || touch "$APT/.nojekyll"

echo "published:"
grep -E '^Package:|^Version:' "$APT/dists/$SUITE/main/binary-amd64/Packages" | paste - - | sort -u

if [ "${1:-}" = "--no-push" ]; then
    echo "--no-push: leaving the commit to you in $APT"
    exit 0
fi
git -C "$APT" add -A
git -C "$APT" commit -m "publish: fellowship-desktop $VERSION for $SUITE"
git -C "$APT" push
echo "live at https://slackerchris.github.io/fellowshipos-apt/ (Pages takes a moment to rebuild)"
