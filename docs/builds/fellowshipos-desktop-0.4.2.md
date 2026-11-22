# FellowshipOS Desktop 0.4.2 — release candidate 1

Built 2026-11-21 with `FELLOWSHIP_EDITIONS=desktop ./build.sh`, from clean
checkouts.

| | |
| --- | --- |
| ISO | `fellowshipos-desktop-amd64-0.4.2.hybrid.iso`, 1,721,636,864 bytes |
| SHA-256 | `49b70ecfa5ee374ecb0e011a8fea24fc36dd9dcaa0cbb5d72f5ba6458960e8b1` |
| Handover copy | `~/Git/fellowship/build/fellowshipos-desktop-amd64-0.4.2-872a620.hybrid.iso` |
| fellowshipos | `872a620` (docs: restart and shut down from the desktop in the CHANGELOG) |
| fellowship-desktop | `8c06e11` (shell: restart and shut down from the power menu, each confirmed) |
| fellowship-desktop package | `fellowship-desktop_0.4.2_amd64.deb` |
| Packages | [fellowshipos-desktop-0.4.2-packages.txt](fellowshipos-desktop-0.4.2-packages.txt), 997 lines |

Checked in the image before any VM run: the package is 0.4.2, the shell has
the power dialog, the compositor sets `FELLOWSHIP_BACKEND`, and the update
source line and key are in `/usr/share/fellowshipos/` and
`/usr/share/keyrings/`.

Scope, as decided by Chris: a VM, a wired network, one display. What it leaves
out is listed in [../versioning.md](../versioning.md).

## Acceptance

The blank-disk install (`desktop/tests/vm-install.sh`) and the update-and-
reboot check are still to run. Results go here.
