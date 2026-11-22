# The desktop update feed

**Decided by Chris, 2026-11-21:** an installed FellowshipOS Desktop takes
fixes to the desktop itself from a signed apt repository on GitHub Pages,
instead of only by reinstalling from a new ISO. It has its own repository and
its own key, apart from FellowshipNAS's feed, and the published `.deb` is
public although the fellowship-desktop source repository is private.

**Status, 2026-11-21:** set up, nothing published yet. The first publish is
the 0.4.2 package, once the RC1 image is built from clean commits.
`desktop/tests/apt-feed.sh` passes all six checks.

## The shape

**A signed repository on GitHub Pages.** `slackerchris/fellowshipos-apt`
(public), served at <https://slackerchris.github.io/fellowshipos-apt/>:

```
dists/trixie/Release            index checksums, Origin/Label FellowshipOS
dists/trixie/InRelease          Release, clearsigned
dists/trixie/Release.gpg        Release, detached signature
dists/trixie/main/binary-amd64/Packages{,.gz}
pool/main/f/fellowship-desktop/fellowship-desktop_<version>_amd64.deb
```

Suite `trixie` is the Debian base the image ships on. Old versions stay in
the pool and apt picks the newest. A version already published is never
replaced with a different file; `publish-apt.sh` refuses, and the fix is to
bump `VERSION`.

**The key.** `FellowshipOS Updates <slackerchris@gmail.com>`, fingerprint
`9A9055842DE0422D0D9B7AFD73D46C9F680BB715`, sign-only RSA 4096, no expiry,
made 2026-11-21. Like the NAS key it has no passphrase, so publishing can
sign unattended, and it lives in this machine's `~/.gnupg` and nowhere else;
only the signed repository is pushed. The revocation certificate is
`~/.gnupg/openpgp-revocs.d/9A9055842DE0422D0D9B7AFD73D46C9F680BB715.rev`.

- **Back it up, encrypted and offline.** Losing it means a new key in a new
  ISO before desktop updates can flow again:

  ```bash
  gpg --armor --export-secret-keys 9A9055842DE0422D0D9B7AFD73D46C9F680BB715 | gpg --symmetric --cipher-algo AES256 -o fellowshipos-updates-key.asc.gpg
  ```

  Keep the passphrase apart from the media, with the revocation certificate.
- **Rotate it:** make a new key, add its public part to
  `desktop/apt/fellowshipos.asc` (one file may hold both), ship that in a
  package signed with the old key, then publish with the new one.

**What the image carries.** The `fellowship-desktop` package installs:

- `/usr/share/keyrings/fellowshipos.asc` — the public key
- `/usr/share/fellowshipos/fellowshipos.list` — the source line:

  ```
  deb [signed-by=/usr/share/keyrings/fellowshipos.asc] https://slackerchris.github.io/fellowshipos-apt trixie main
  ```

  `signed-by` trusts that key for that source only, and no other source
  trusts it.
- `fellowship-apt-source.service` — on an installed system's first boot,
  copies the line to `/etc/apt/sources.list.d/fellowshipos.list` once, and
  leaves a stamp in `/var/lib/fellowship/` so a person who deletes the
  source keeps it deleted. It does nothing on a live boot, and the image
  build never sees the network source, so building needs no network trust.

**Taking an update.** `sudo apt update && sudo apt upgrade` brings desktop
fixes with Debian's own. The desktop has no Updates page yet (Settings'
Updates row is 📋 in fellowship-desktop's `basic-desktop.md`).

## Publishing a fix

1. Bump `VERSION`.
2. `FELLOWSHIP_EDITIONS=desktop ./build.sh`, or `desktop/build.sh` for the
   package alone.
3. `./publish-apt.sh` — copies the package into the pool, writes the indexes,
   signs `Release`, commits and pushes `~/Git/fellowshipos-apt`.
   `--no-push` leaves the commit for review.
4. Pages rebuilds in a moment; installed desktops see it at their next
   `apt update`.

## Tests

`desktop/tests/apt-feed.sh` (docker, `debian:trixie`, no network): publishes
a stand-in package with a throwaway key; apt accepts it through the shipped
source line and offers the current version, refuses it checked against
another key, and refuses it unsigned; the shipped key is the FellowshipOS
Updates key and the same file the Pages repository serves.

Still to prove once 0.4.2 is published: a fresh install in the VM adds the
source on first boot, fetches the hosted `InRelease` over HTTPS, and takes a
newer package with `apt upgrade`.
