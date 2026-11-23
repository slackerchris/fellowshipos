#!/bin/bash
# Upgrade this machine's desktop from source: pull fellowship-desktop and
# this repository, build the fellowship-desktop package the way the image
# does (desktop/build.sh, natively rather than in a container), and install
# it with apt.
#
#   desktop/upgrade-from-source.sh              pull, build, install
#   desktop/upgrade-from-source.sh --no-pull    build what is checked out now
#   desktop/upgrade-from-source.sh --rollback   reinstall the previous build
#   desktop/upgrade-from-source.sh --list       the builds kept
#
# Run it as yourself on an installed FellowshipOS desktop; it asks for sudo
# only to install build tools and the package. The two repositories sit side
# by side (~/Git/fellowshipos and ~/Git/fellowship-desktop); set
# FELLOWSHIP_DESKTOP_SRC to build another checkout.
#
# The package is versioned <VERSION>+git<UTC date and time>.<commit>, e.g.
# 0.4.6+git202611222015.9b6f40a: above the published 0.4.6, below 0.4.7, and
# later builds above earlier ones, so apt treats it as an ordinary upgrade
# and the next release replaces it. Uncommitted changes are built too, and
# marked "+dirty" in the version.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DESKTOP=${FELLOWSHIP_DESKTOP_SRC:-$(cd "$ROOT/.." && pwd)/fellowship-desktop}
STATE=${XDG_CACHE_HOME:-$HOME/.cache}/fellowship-upgrade
DEBS=$STATE/debs
KEEP=3

say() { printf '\n== %s\n' "$*"; }
die() { printf 'upgrade-from-source: %s\n' "$*" >&2; exit 1; }

installed_version() { dpkg-query -W -f='${Version}' fellowship-desktop 2>/dev/null || echo none; }

install_deb() {
    say "Installing $(basename "$1") (was $(installed_version))"
    # --allow-downgrades only matters for --rollback.
    sudo apt-get install -y --allow-downgrades "$1"
}

after_install() {
    cat <<'EOF'

Installed. What takes effect when:
  - Settings, Files and other apps: the next time each is opened.
  - The top bar, dock and launcher: now, with   fellowship-msg restart-shell
    (windows stay open).
  - The compositor, the login screen and the session: at the next login.
EOF
}

builds() { ls -1t "$DEBS"/fellowship-desktop_*_amd64.deb 2>/dev/null || true; }

case "${1:-}" in
    --list)
        echo "Installed: $(installed_version)"
        builds | sed 's|.*/|  |'
        exit 0
        ;;
    --rollback)
        current=$(installed_version)
        previous=$(builds | while read -r deb; do
            v=$(dpkg-deb -f "$deb" Version)
            [ "$v" != "$current" ] && { echo "$deb"; break; }
        done)
        [ -n "$previous" ] || die "no earlier build kept in $DEBS to go back to"
        install_deb "$previous"
        after_install
        exit 0
        ;;
    --no-pull|"") ;;
    *) die "unknown option $1 (see the top of this script)" ;;
esac

[ -f "$DESKTOP/Cargo.toml" ] || die "no fellowship-desktop checkout at $DESKTOP"
[ -f /etc/os-release ] && grep -q trixie /etc/os-release || die "this builds for Debian trixie, which this machine isn't"

# --- Pull ------------------------------------------------------------------
if [ "${1:-}" != "--no-pull" ]; then
    for repo in "$ROOT" "$DESKTOP"; do
        say "Pulling $(basename "$repo") ($(git -C "$repo" branch --show-current))"
        git -C "$repo" pull --ff-only || die "$repo can't be fast-forwarded; sort it out, or run with --no-pull"
    done
fi

# --- Build tools -------------------------------------------------------------
# What desktop/build.sh installs in its container, plus git.
NEED="build-essential pkg-config curl ca-certificates git libwayland-dev libxkbcommon-dev
      libegl1-mesa-dev libgbm-dev libudev-dev libseat-dev libinput-dev libx11-dev
      libxcursor-dev libxrandr-dev libxi-dev libglib2.0-dev"
missing=$(for p in $NEED; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || echo "$p"; done)
if [ -n "$missing" ]; then
    say "Installing build tools: $(echo $missing)"
    sudo apt-get install -y $missing
fi
[ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
if ! command -v cargo >/dev/null; then
    # Debian's rustc is too old for zbus (desktop/build.sh says why).
    say "Installing Rust with rustup (into ~/.cargo)"
    curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
    . "$HOME/.cargo/env"
fi

# --- Version -----------------------------------------------------------------
base=$(cat "$ROOT/VERSION")
commit=$(git -C "$DESKTOP" rev-parse --short=7 HEAD)
version="$base+git$(date -u +%Y%m%d%H%M).$commit"
if [ -n "$(git -C "$DESKTOP" status --porcelain)" ] || [ -n "$(git -C "$ROOT" status --porcelain -- desktop themes VERSION)" ]; then
    version="$version+dirty"
fi

# --- Build -------------------------------------------------------------------
say "Building fellowship-desktop $version"
echo "   fellowship-desktop $(git -C "$DESKTOP" branch --show-current) at $commit"
echo "   fellowshipos       $(git -C "$ROOT" branch --show-current) at $(git -C "$ROOT" rev-parse --short=7 HEAD)"
FELLOWSHIP_VERSION=$version \
FELLOWSHIP_DEB_DIR=$DEBS \
FELLOWSHIP_DESKTOP_SRC=$DESKTOP \
CARGO_TARGET_DIR=$STATE/target \
    "$ROOT/desktop/build.sh"
deb="$DEBS/fellowship-desktop_${version}_amd64.deb"
[ -f "$deb" ] || die "the build finished without $deb"

# --- Install -----------------------------------------------------------------
install_deb "$deb"

# Keep the newest few builds for --rollback.
builds | tail -n +$((KEEP + 1)) | xargs -r rm -f
after_install
