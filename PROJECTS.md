# The three products, and where they live

FellowshipOS is one Debian live-build tree with three editions, plus a separate
repository for the desktop environment. This file is the map: which product a
folder belongs to, which branch ships it, and how to keep a push to one product.

| Product | What it is | Code | Edition |
| --- | --- | --- | --- |
| **FellowshipOS Multi-purpose** | Task-specific Debian for Proxmox LXC and general machines | `config/`, `proxmox/`, `installer/` | `multi` |
| **FellowshipNAS** | NAS appliance with the browser desktop | `nas/`, plus the NAS parts of `config/` | `nas` |
| **FellowshipOS Desktop** | Own distro with the Fellowship desktop | `desktop/` (integration) and the [fellowship-desktop](../fellowship-desktop) checkout (the desktop itself) | `desktop` |

The first two are editions of one build, not separate codebases. `auto/config`
sets the ISO name, boot entry and installer role from `FELLOWSHIP_EDITION`, and
all editions share `config/`, `installer/` and `themes/`.

## What belongs where

| Path | Product | Notes |
| --- | --- | --- |
| `auto/config`, `build.sh` | all | Edition switch and build entry point |
| `config/` | all | Live-build profile, package lists, first-boot wizard, role scripts |
| `installer/` | all | Graphical installer; the NAS and desktop editions preselect their role |
| `nas/packages/` | NAS | Python CLI, Go services, browser desktop, packages |
| `nas/tests/` | NAS | Unit tests and the NAS VM harness |
| `proxmox/` | Multi-purpose | LXC templates and `create-ct.sh` |
| `desktop/` | Desktop | Session starter, package list, Chromium flags, seat tests |
| `themes/` | NAS + Desktop | Source of truth for the shared themes and wallpapers |
| `docs/` | all | Indexed in [docs/README.md](docs/README.md) |
| `../fellowship-desktop/` | Desktop | The Rust compositor, shell and apps, in its own repository |

Build output does not belong in either checkout. ISOs go to
`../fellowship/build/`; `chroot/`, `cache/`, `binary/`, `config/packages.chroot/`
and the generated `config/binary`–`config/source` files are recreated by the
next build.

## Building one product

```bash
FELLOWSHIP_EDITIONS=multi   ./build.sh   # Multi-purpose ISO and LXC template
FELLOWSHIP_EDITIONS=nas     ./build.sh   # FellowshipNAS ISO
FELLOWSHIP_EDITIONS=desktop ./build.sh   # Desktop ISO; needs ../fellowship-desktop
```

`./build.sh` with no variable builds `nas` and `multi`.

## Branch and push rules

- **One long-lived branch per repository.** `fellowshipos` ships from
  `nas-edition`, which is the GitHub default since 2026-11-15. `master` is an
  ancestor and is no longer the default. `fellowship-desktop` ships from `main`.
- **Short-lived branches only.** Name them for the product (`nas/...`,
  `installer/...`, `desktop/...`), merge them, delete them. The two desktop
  branches `notifications-design` and `shared-themes` were merged and removed on
  2026-11-15.
- **One commit, one product.** Prefix the subject with the area (`nas:`,
  `installer:`, `desktop:`, `themes:`, `docs:`). Stage by path, never with
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

## The contract between the two repositories

- `desktop/build.sh` mounts the desktop checkout at `../fellowship-desktop`
  (override with `FELLOWSHIP_DESKTOP_SRC`).
- The desktop's CI checks out `fellowshipos@nas-edition` with a sparse
  `themes/` checkout, then points the theme tests at it with
  `FELLOWSHIP_THEMES_DIR`.
- `themes/fellowship.toml` is the default theme. The Rust desktop also carries
  a built-in copy in `assets/themes/fellowship.toml`; its tests fail when the
  two differ, so copy a change to both.

## Documentation

Both repositories keep their documents where they are linked from, because
READMEs, the CHANGELOG and source comments refer to them by filename. Use the
indexes instead of moving files:

- [docs/README.md](docs/README.md) — NAS plans, installer records, desktop-image
  sessions.
- [../fellowship-desktop/docs/README.md](../fellowship-desktop/docs/README.md) —
  desktop design documents and session records.