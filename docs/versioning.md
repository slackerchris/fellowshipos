# Versioning

One file, `VERSION` at the repository root, holds the release version of the
Multi-purpose and Desktop images, the LXC template and the installer package.
FellowshipNAS keeps its own `VERSION` in the FellowshipOS-NAS repository since
it became its own project on 2026-11-20; both stood at 0.3.0 then. It is a
**prerelease** scheme, `0.major.minor` (Chris, 2026-11-17): while FellowshipOS is
pre-release the major stays 0; the middle number bumps per release or tour
batch, the last number for fixes within one. `1.0.0` is reserved for the first
release-worthy image.

Current: **0.4.2** — the desktop's first release candidate (Chris,
2026-11-21). He chose the number, keeping to the prerelease scheme rather
than `1.0.0~rc1`. RC1 covers a desktop that installs in a virtual machine and
is used on a wired network with one display: the login screen on greetd,
admin prompts, the keyring, default apps with folders in Strider, and the
everyday services and firmware in the image. Left out of RC1, and to be said
so in its release notes: joining Wi-Fi from the desktop (NetworkManager is
in the image, but nothing on screen drives it), the lock screen, idle
dimming, suspend and lid close, more than one monitor and HiDPI, X11 apps,
battery and brightness, and any run on real hardware. Updates to the
desktop itself come from a signed apt repository on GitHub Pages
([desktop-update-feed.md](desktop-update-feed.md)).

Before it, **0.3.0** was the desktop tour batch (Chris, 2026-11-20): Strider
(Fellowship Files) in the desktop image beside Nemo, with its GVfs backends,
and the NAS announcing itself on the network; the theme manager's light mode,
by time of day (sunrise and sunset, or set hours) and the wallpaper fade, all
switched live; a darker gold for Strider's toolbar icons in light mode; and
Qt apps' menu and tool bars following a live switch under Breeze 6.3.

Before it, **0.2.3** was a slimmer NAS image: no Docker, git, nmap or full mtr
(mtr-tiny instead), 479 MB less installed and a 1.01 GB ISO. The other images
keep them.

Before that, **0.2.2** made Files feel like a file browser: right-click menus, a
folder tree in the side, a selection line and Properties, and a recycle bin
that follows each share's SMB bin switch. Also the installer's Tab order on the
You step, and the appliance-first decision in the release list.

Then **0.2.1** had fixes and asks from the same tour: a choice of time
servers (pool.ntp.org, Google, Cloudflare or your own), desktop widgets that
fold or hide, the installer screen drawn with pixman so it no longer tears in
VMs, no recovery-key save button (cog drops downloads), and the Fellowship
boot menu theme.

Before that, **0.2.0** was the 2026-11-17 Proxmox tour batch: delete for ZFS and
Unraid-style pools, shares for everyone open in Files through the renamed
`file-share` group, the NAS's own time on the clock with a calendar and a time
format per person, a power button in the account menu, Import a pool beside
Create a pool, the tree logo, and the installer's LVM fix.

Earlier, **0.1.0** was the first tracked prerelease, covering the account
separation phases, the network rollback work, and the November 2026 tour fixes
(CPU sampler, theme-following status colours, delete array, service controls,
installer recovery-key gate removal).

## Where it flows

- `build.sh` reads `VERSION` and names the ISOs
  `fellowshipos-<edition>-amd64-<version>.hybrid.iso` and the LXC template
  `fellowshipos-trixie-amd64-<version>_lxc.tar.gz`.
- Each build keeps one ISO per edition in the repository folder: it deletes
  that edition's older ISOs (renamed test builds too) and hands the new one to
  the user who ran the build, not root. Copy an ISO to `../fellowship/build/`
  to keep it; `FELLOWSHIP_KEEP_OLD_ISOS=1` keeps them all.
- `installer/build.sh` stamps the **temporary build copy's** `debian/changelog`
  first line with `VERSION`, so the installed package carries the release
  version. The checkout's changelogs are never
  touched by a build.
- `proxmox/create-ct.sh` expects the template at
  `/var/lib/vz/template/cache/fellowshipos-trixie-amd64-<version>_lxc.tar.gz`;
  override with `FELLOWSHIP_TEMPLATE` if you keep several.

## Bumping

Edit `VERSION` only. Nothing else hardcodes the number. If you want the Debian
changelog to record the bump as history, add an entry with `dch -v <version>`
in each package before building — the build stamps the top line either way.

The short-SHA suffixes on handover ISOs (`…-1.0-<sha>.hybrid.iso` in
`~/Git/fellowship/build/`) are added at copy time to identify the exact commit;
they are not part of the version scheme.
