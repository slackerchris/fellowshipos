#!/bin/bash
# The desktop update feed, checked end to end in a debian:trixie container,
# without the network or the real signing key:
#
#   1. publish-apt.sh lays out and signs a repository (throwaway key, dummy
#      fellowship-desktop at the current VERSION, --no-push)
#   2. apt accepts it with the shipped source line pointed at it, and offers
#      that version as the candidate
#   3. apt refuses the same repository checked against another key
#   4. apt refuses it unsigned
#   5. the shipped key is the FellowshipOS Updates key, and the same file the
#      Pages repository serves (when ~/Git/fellowshipos-apt is checked out)
#
#   desktop/tests/apt-feed.sh
# Needs docker and the debian:trixie image.
set -eu
cd "$(dirname "$0")/../.."

FPR=9A9055842DE0422D0D9B7AFD73D46C9F680BB715
VERSION=$(cat VERSION)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
pass=0
fail=0
ok() { echo "PASS $1"; pass=$((pass + 1)); }
bad() { echo "FAIL $1"; fail=$((fail + 1)); }

# Two throwaway keys: the one that signs, and one apt must not accept
export GNUPGHOME=$WORK/gnupg
mkdir -m700 "$GNUPGHOME"
gpg -q --batch --passphrase '' --quick-gen-key "feed test <test@example.invalid>" rsa2048 sign never
gpg -q --batch --passphrase '' --quick-gen-key "wrong key <wrong@example.invalid>" rsa2048 sign never
GOOD=$(gpg --with-colons --list-secret-keys test@example.invalid | awk -F: '/^fpr/{print $10; exit}')
mkdir "$WORK/keys"
gpg --armor --export "$GOOD" > "$WORK/keys/good.asc"
gpg --armor --export wrong@example.invalid > "$WORK/keys/wrong.asc"

# A dummy package standing in for the real one
PKG=$WORK/pkg
mkdir -p "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: fellowship-desktop
Version: $VERSION
Architecture: amd64
Maintainer: test <test@example.invalid>
Description: feed test stand-in
EOF
dpkg-deb --build --root-owner-group "$PKG" "$WORK/fellowship-desktop_${VERSION}_amd64.deb" >/dev/null

# 1. Publish into a scratch Pages checkout
git init -q "$WORK/apt"
if FELLOWSHIP_APT_DIR=$WORK/apt FELLOWSHIP_APT_KEY=$GOOD \
   FELLOWSHIP_APT_DEB=$WORK/fellowship-desktop_${VERSION}_amd64.deb \
   ./publish-apt.sh --no-push >"$WORK/publish.log" 2>&1 &&
   [ -f "$WORK/apt/dists/trixie/InRelease" ]; then
    ok "publish-apt.sh signs a repository"
else
    bad "publish-apt.sh"; cat "$WORK/publish.log"
fi
cp -r "$WORK/apt" "$WORK/unsigned"
rm -f "$WORK/unsigned/dists/trixie/InRelease" "$WORK/unsigned/dists/trixie/Release.gpg"

# The shipped source line, pointed at the scratch repository
sed -e 's|https://slackerchris.github.io/fellowshipos-apt|file:/repo|' \
    -e 's|/usr/share/keyrings/fellowshipos.asc|/keys/KEY.asc|' \
    desktop/apt/fellowshipos.list > "$WORK/source.list"

check() { # name key repo expect(accept|refuse)
    sed "s|KEY|$2|" "$WORK/source.list" > "$WORK/$1.list"
    docker run --rm --network none \
        -v "$WORK/$3:/repo:ro" -v "$WORK/keys:/keys:ro" -v "$WORK/$1.list:/etc/apt/sources.list.d/fellowshipos.list:ro" \
        debian:trixie sh -c 'rm -f /etc/apt/sources.list.d/debian.sources
            apt-get update -q 2>&1; echo "update-exit=$?"
            apt-cache policy fellowship-desktop' > "$WORK/$1.out" 2>&1 || true
}

check accept good apt
if grep -q 'update-exit=0' "$WORK/accept.out" && ! grep -q '^W:\|^E:' "$WORK/accept.out" &&
   grep -q "Candidate: $VERSION" "$WORK/accept.out"; then
    ok "apt accepts the signed repository and offers $VERSION"
else
    bad "apt with the right key"; cat "$WORK/accept.out"
fi

check wrongkey wrong apt
if grep -q 'NO_PUBKEY\|not signed\|is not signed' "$WORK/wrongkey.out" &&
   ! grep -q "Candidate: $VERSION" "$WORK/wrongkey.out"; then
    ok "apt refuses the repository against another key"
else
    bad "apt with the wrong key"; cat "$WORK/wrongkey.out"
fi

check unsigned good unsigned
if grep -q 'not signed\|does not have a Release file\|is not signed' "$WORK/unsigned.out" &&
   ! grep -q "Candidate: $VERSION" "$WORK/unsigned.out"; then
    ok "apt refuses the repository unsigned"
else
    bad "apt unsigned"; cat "$WORK/unsigned.out"
fi

# 5. The shipped key
if gpg --show-keys --with-colons desktop/apt/fellowshipos.asc | grep -q "^fpr:::::::::$FPR:"; then
    ok "the image ships the FellowshipOS Updates key"
else
    bad "desktop/apt/fellowshipos.asc is not $FPR"
fi
PAGES=$HOME/Git/fellowshipos-apt/fellowshipos.asc
if [ -f "$PAGES" ]; then
    if cmp -s desktop/apt/fellowshipos.asc "$PAGES"; then
        ok "the shipped key matches the Pages repository's"
    else
        bad "desktop/apt/fellowshipos.asc differs from $PAGES"
    fi
fi

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
