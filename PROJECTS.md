# The products, and where they live

FellowshipOS is three products in three repositories. This one builds
**FellowshipOS Multi-purpose**. FellowshipNAS left on 2026-11-20 for the
FellowshipOS-NAS repository (`~/Git/FellowshipOS-NAS`), and FellowshipOS
Desktop on 2026-11-23 for the fellowship-desktop repository
(`~/Git/fellowship-desktop`, its image build in `image/`). Each has its own
installer copy, live-build profile, themes and version, and neither
repository's build may need another's checkout. This file is the map.

| Product | What it is | Repository |
| --- | --- | --- |
| **FellowshipOS Multi-purpose** | Task-specific Debian for Proxmox LXC and general machines | this one: `config/`, `proxmox/`, `installer/` |
| **FellowshipNAS** | NAS appliance with the browser desktop | FellowshipOS-NAS |
| **FellowshipOS Desktop** | Own distro with the Fellowship desktop | fellowship-desktop (the Rust desktop and `image/`) |

## What belongs where

| Path | Notes |
| --- | --- |
| `auto/config`, `build.sh` | Build entry point; `FELLOWSHIP_EDITION` is `multi` |
| `config/` | Live-build profile, package lists, first-boot wizard, role scripts |
| `installer/` | Graphical installer, Multi-purpose edition only |
| `proxmox/` | LXC templates and `create-ct.sh` |
| `themes/` | Themes and wallpapers for the installer page and boot menu; the other products keep their own copies |
| `docs/` | Indexed in [docs/README.md](docs/README.md) |

Build output does not belong in the checkout. ISOs go to
`../fellowship/build/`; `chroot/`, `cache/`, `binary/`, `config/packages.chroot/`
and the generated `config/binary`–`config/source` files are recreated by the
next build.

## Building

```bash
./build.sh   # Multi-purpose ISO and LXC template
```

FellowshipNAS is built with `./build.sh` in the FellowshipOS-NAS checkout,
FellowshipOS Desktop with `image/build.sh` in the fellowship-desktop checkout.

## Branch and push rules

- **One long-lived branch per repository.** `fellowshipos` ships from
  `nas-edition`, which is the GitHub default since 2026-11-15. `master` is an
  ancestor and is no longer the default. `fellowship-desktop` ships from `main`.
- **Short-lived branches only.** Name them for the area
  (`installer/...`, `proxmox/...`), merge them, delete them.
- **One commit, one area.** Prefix the subject with the area
  (`installer:`, `build:`, `proxmox:`, `docs:`). Stage by path, never with
  `git add -A` while another session's changes are in the tree.
- **`nas-edition` is shared with concurrent sessions.** Before pushing, list
  what a push would publish and separate your commits from anyone else's:

  ```bash
  git log --oneline origin/nas-edition..HEAD
  ```

  Push only up to your last commit when the branch has moved on:

  ```bash
  git push origin <sha>:nas-edition
  ```

  A push of a specific SHA is an ordinary fast-forward and leaves later commits
  local.

## Between the repositories

There is no build-time contract any more: nothing here reads another
checkout, and the other repositories read nothing here. What was shared was
copied when each product left — the installer, the live-build profile and
`themes/` — so a fix wanted in more than one product is made in each.

## Documentation

Each repository keeps its documents where they are linked from, because
READMEs, the CHANGELOG and source comments refer to them by filename. Use the
indexes instead of moving files:

- [docs/README.md](docs/README.md) — installer and image records.
- fellowship-desktop's `docs/README.md` — the desktop's design documents,
  session records and the Desktop image's release records.
