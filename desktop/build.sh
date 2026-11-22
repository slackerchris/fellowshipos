#!/bin/bash
# Build the Fellowship desktop package that the desktop edition installs.
#
#   FELLOWSHIP_EDITIONS=desktop ./build.sh
#
# build.sh runs this inside its Debian trixie container with the desktop
# checkout mounted at /fellowship-desktop. The result is one .deb in
# config/packages.chroot/, which live-build installs into the image: the
# compositor, shell, message tool, portal backend, Settings and Strider (the file manager), the shared themes, the
# portal's configuration and the console-session starter.
set -e

cd "$(dirname "$0")/.."                     # the FellowshipOS repository root
ROOT=$(pwd)
DESKTOP=${FELLOWSHIP_DESKTOP_SRC:-/fellowship-desktop}
# The one release version (docs/versioning.md), so the feed and apt agree
VERSION=$(cat "$ROOT/VERSION")

if [ ! -f "$DESKTOP/Cargo.toml" ]; then
    echo "desktop/build.sh: no fellowship-desktop checkout at $DESKTOP" >&2
    echo "Set FELLOWSHIP_DESKTOP_SRC, or keep the two repositories side by side." >&2
    exit 1
fi

# Debian trixie ships rustc 1.85, and the desktop's zbus needs 1.87, so the
# compiler comes from rustup inside this container. The binaries still link
# against trixie's glibc, which is what the image runs.
if ! command -v cargo >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq build-essential pkg-config curl ca-certificates \
        libwayland-dev libxkbcommon-dev libegl1-mesa-dev libgbm-dev libudev-dev \
        libseat-dev libinput-dev libx11-dev libxcursor-dev libxrandr-dev libxi-dev libglib2.0-dev
    curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
    . "$HOME/.cargo/env"
fi

export CARGO_TARGET_DIR=/tmp/desktop-target
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Build out of tree, so the checkout keeps its own target/ and the image gets
# what this run produced.
tar --exclude=.git --exclude=target -cf - -C "$DESKTOP" . | tar -xf - -C "$WORK"
(
    cd "$WORK"
    cargo build --release --workspace --locked
)

STAGE=$WORK/stage
install_bin() { install -Dm755 "$CARGO_TARGET_DIR/release/$1" "$STAGE/usr/bin/$1"; }
install_bin fellowship-comp
install_bin fellowship-shell
install_bin fellowship-msg
install_bin fellowship-portal
install_bin fellowship-notifyd
install_bin fellowship-settings
install_bin fellowship-files
install_bin fellowship-greeter
install_bin fellowship-appearanced
install -Dm644 "$WORK/crates/fellowship-files/data/org.fellowship.Files.desktop" \
    "$STAGE/usr/share/applications/org.fellowship.Files.desktop"
install -Dm644 "$WORK/assets/icons/apps/org.fellowship.Files.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/org.fellowship.Files.svg"
install -Dm644 "$WORK/assets/icons/apps/org.fellowship.Files-256.png" \
    "$STAGE/usr/share/icons/hicolor/256x256/apps/org.fellowship.Files.png"
install -Dm644 "$WORK/crates/fellowship-settings/data/org.fellowship.Settings.desktop" \
    "$STAGE/usr/share/applications/org.fellowship.Settings.desktop"
install -Dm644 "$WORK/assets/icons/apps/org.fellowship.Settings.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/org.fellowship.Settings.svg"
install -Dm755 desktop/session/fellowship-session "$STAGE/usr/bin/fellowship-session"
# Desktop updates: the signing key, the source line, and the unit that adds the
# source on an installed system's first boot (publish-apt.sh serves the repo)
install -Dm644 desktop/apt/fellowshipos.asc "$STAGE/usr/share/keyrings/fellowshipos.asc"
install -Dm644 desktop/apt/fellowshipos.list "$STAGE/usr/share/fellowshipos/fellowshipos.list"
install -Dm644 desktop/apt/fellowship-apt-source.service \
    "$STAGE/usr/lib/systemd/system/fellowship-apt-source.service"

# Default apps, read only in a Fellowship session (XDG_CURRENT_DESKTOP)
install -Dm644 desktop/fellowship-mimeapps.list \
    "$STAGE/usr/share/applications/fellowship-mimeapps.list"

# Graphical login: greetd hosts fellowship-greeter inside Cage on tty1.
# Installed systems boot directly to fellowship-greeter; live ISO boots enter
# the live desktop once and return to the greeter on logout.
install -Dm644 desktop/greeter/greetd.toml "$STAGE/etc/fellowship/greetd.toml"
install -Dm644 desktop/greeter/greetd-live.toml "$STAGE/etc/fellowship/greetd-live.toml"
install -Dm755 desktop/greeter/fellowship-greetd-launch "$STAGE/usr/libexec/fellowship-greetd-launch"
install -Dm755 desktop/greeter/fellowship-greeter-session "$STAGE/usr/libexec/fellowship-greeter-session"
install -Dm644 desktop/greeter/greetd-override.conf \
    "$STAGE/usr/lib/systemd/system/greetd.service.d/10-fellowship.conf"

# Restricted greeter appearance service (system D-Bus + hardened systemd unit).
ADATA=$WORK/crates/fellowship-appearanced/data
install -Dm644 "$ADATA/org.fellowship.GreeterAppearance.conf" \
    "$STAGE/usr/share/dbus-1/system.d/org.fellowship.GreeterAppearance.conf"
install -Dm644 "$ADATA/org.fellowship.GreeterAppearance.service" \
    "$STAGE/usr/share/dbus-1/system-services/org.fellowship.GreeterAppearance.service"
install -Dm644 "$ADATA/fellowship-appearanced.service" \
    "$STAGE/usr/lib/systemd/system/fellowship-appearanced.service"

# One place for the themes, read by the Rust desktop at run time, and the
# tree the dock draws in the middle
install -d "$STAGE/usr/share/fellowship/themes/wallpapers" "$STAGE/usr/share/fellowship/icons"
# Loose icons only: the folders beside them (apps, files, settings) are
# installed above or compiled into the apps.
find "$WORK/assets/icons" -maxdepth 1 -type f -exec install -m644 {} "$STAGE/usr/share/fellowship/icons/" \;
install -m644 themes/*.toml "$STAGE/usr/share/fellowship/themes/"
install -m644 themes/wallpapers/* "$STAGE/usr/share/fellowship/themes/wallpapers/"

# The notification service: it claims the standard bus name on activation,
# so the first app that notifies starts it. Both names point at one binary.
NDATA=$WORK/crates/fellowship-notifyd/data
install -Dm644 "$NDATA/org.freedesktop.Notifications.service" \
    "$STAGE/usr/share/dbus-1/services/org.freedesktop.Notifications.service"
install -Dm644 "$NDATA/org.fellowship.Notifications.service" \
    "$STAGE/usr/share/dbus-1/services/org.fellowship.Notifications.service"

# The decoration rules: the apps that draw their own title bar and never tell
# the compositor, measured on this same seat. Read at session start; a person
# may override any line in ~/.config/fellowship/decorations.
install -Dm644 "$WORK/crates/fellowship-comp/data/decorations" \
    "$STAGE/usr/share/fellowship/decorations"

# WirePlumber's fallback sink, so a machine with no sound hardware (a plain
# QEMU guest) still has a default sink for the volume keys and media apps.
install -Dm644 desktop/session/wireplumber/50-fallback-sink.conf \
    "$STAGE/usr/share/wireplumber/wireplumber.conf.d/50-fellowship-fallback-sink.conf"

# The colour-scheme backend, selected through XDG_CURRENT_DESKTOP=Fellowship
PORTAL=$WORK/crates/fellowship-portal/data
install -d "$STAGE/usr/share/xdg-desktop-portal/portals" "$STAGE/usr/share/dbus-1/services"
install -m644 "$PORTAL/fellowship.portal" "$STAGE/usr/share/xdg-desktop-portal/portals/"
install -m644 "$PORTAL/fellowship-portals.conf" "$STAGE/usr/share/xdg-desktop-portal/"
sed 's|^Exec=.*|Exec=/usr/bin/fellowship-portal|' \
    "$PORTAL/org.freedesktop.impl.portal.desktop.fellowship.service" \
    > "$STAGE/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.fellowship.service"
chmod 644 "$STAGE/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.fellowship.service"

SIZE=$(du -sk "$STAGE" | cut -f1)
install -d "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: fellowship-desktop
Version: $VERSION
Architecture: amd64
Maintainer: Chris <chris@fellowshipos.invalid>
Installed-Size: $SIZE
Section: x11
Priority: optional
Depends: libgbm1, libinput10, libseat1, libudev1, libxkbcommon0, xkb-data, kbd,
 libwayland-client0, libwayland-server0, libwayland-egl1, libegl1,
 libegl-mesa0, libgl1-mesa-dri, fonts-dejavu-core, dbus,
 xdg-desktop-portal, xdg-desktop-portal-gtk, libglib2.0-0t64,
 pipewire, pipewire-pulse, wireplumber, greetd, cage, accountsservice
Description: The FellowshipOS desktop
 The Wayland compositor, the shell (top bar, dock, launcher), the
 Fellowship graphical greeter and appearance service, the fellowship-msg
 control tool, the notification service, and the xdg-desktop-portal Settings
 backend, with the shared themes and session starter.
EOF

mkdir -p "$ROOT/config/packages.chroot"
rm -f "$ROOT"/config/packages.chroot/fellowship-desktop_*.deb "$ROOT"/cache/desktop-package/fellowship-desktop_*.deb
dpkg-deb --build --root-owner-group "$STAGE" \
    "$ROOT/config/packages.chroot/fellowship-desktop_${VERSION}_amd64.deb"

echo "desktop/build.sh: config/packages.chroot/fellowship-desktop_${VERSION}_amd64.deb"
