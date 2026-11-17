# Versioning

One file, `VERSION` at the repository root, holds the release version. It is a
**prerelease** scheme, `0.major.minor` (Chris, 2026-11-17): while FellowshipOS is
pre-release the major stays 0; the middle number bumps per release or tour
batch, the last number for fixes within one. `1.0.0` is reserved for the first
release-worthy image.

Current: **0.2.0** — the 2026-11-17 Proxmox tour batch: delete for ZFS and
Unraid-style pools, shares for everyone open in Files through the renamed
`file-share` group, the NAS's own time on the clock with a calendar and a time
format per person, a power button in the account menu, Import a pool beside
Create a pool, the tree logo, and the installer's LVM fix.

Before it, **0.1.0** was the first tracked prerelease, covering the account
separation phases, the network rollback work, and the November 2026 tour fixes
(CPU sampler, theme-following status colours, delete array, service controls,
installer recovery-key gate removal).

## Where it flows

- `build.sh` reads `VERSION` and names the ISOs
  `fellowshipos-<edition>-amd64-<version>.hybrid.iso` and the LXC template
  `fellowshipos-trixie-amd64-<version>_lxc.tar.gz`.
- `nas/packages/build.sh` and `installer/build.sh` stamp the **temporary build
  copies'** `debian/changelog` first line with `VERSION`, so the installed
  packages carry the release version. The checkout's changelogs are never
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
