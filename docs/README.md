# FellowshipOS documents

Every document here is linked by filename from the README, the CHANGELOG or a
source comment. Add an entry when you add a file; do not rename or move an
existing one without updating its links.

The product map and the build, branch and push rules are in
[../PROJECTS.md](../PROJECTS.md).

## FellowshipNAS

FellowshipNAS became its own project on 2026-11-20. Its documents moved with
their history to the FellowshipOS-NAS repository (`docs/` there), starting with
the release list, `fellowship-nas-release.md`.

## Installer and images

| Document | What it answers |
| --- | --- |
| [fellowship-vm-walkthrough-2026-11-11.md](fellowship-vm-walkthrough-2026-11-11.md) | Both images walked through in QEMU/KVM: features, bugs and suggestions |

## Desktop edition

| Document | What it answers |
| --- | --- |
| [builds/fellowshipos-desktop-0.4.3.md](builds/fellowshipos-desktop-0.4.3.md) | The 0.4.3 release candidate: commits, checksum, what was tested on 0.4.2 and what wasn't on 0.4.3 |
| [builds/fellowshipos-desktop-0.4.2.md](builds/fellowshipos-desktop-0.4.2.md) | The 0.4.2 release candidate: commits, checksum, package list, acceptance results |
| [desktop-update-feed.md](desktop-update-feed.md) | The signed apt repository that brings desktop fixes to installed systems: key, layout, publishing, tests |
| [fellowship-desktop-vm-session-2026-11-14.md](fellowship-desktop-vm-session-2026-11-14.md) | A dated handoff, superseded the same day; kept for the record |

Design documents for the desktop environment itself, and the current seat
records, live in the desktop repository:
[fellowship-desktop/docs/](../../fellowship-desktop/docs/README.md).
