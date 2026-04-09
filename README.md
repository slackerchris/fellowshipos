# FellowshipOS

FellowshipOS is a custom Debian Trixie-based live image and Proxmox LXC template build system for homelab nodes.

The project builds two artifacts in one run:

- A bootable Debian live ISO
- A Proxmox-compatible LXC rootfs tarball

It also ships first-boot setup automation, node flavor scripts, and a curated homelab CLI baseline.

## What This Repository Does

- Uses `live-build` inside Docker for reproducible host-independent builds
- Produces:
  - `live-image-amd64.hybrid.iso`
  - `fellowshipos-trixie-amd64-<version>_lxc.tar.gz`
- Runs a first-boot setup wizard (`fellowshipos-setup`) on deployed systems
- Supports post-install role layering via flavor scripts (`homelabos-flavor`)
- Includes helper scripts for Proxmox container creation

## Requirements

Build host requirements:

- Linux with Docker installed and running
- Internet access (Debian + GitHub release downloads during build)
- Enough disk space for live-build cache and chroot artifacts (10+ GB recommended)

Runtime/deployment requirements:

- Proxmox VE for LXC template deployment

## Quick Start

From the repository root:

```bash
chmod +x build.sh
./build.sh
```

On success, you will get:

- `live-image-amd64.hybrid.iso`
- `fellowshipos-trixie-amd64-1.0_lxc.tar.gz` (version depends on `build.sh`)

Build logs are written to:

- `build.log`

## Build Flow

`build.sh` performs the following:

1. Pulls `debian:trixie`
2. Runs `lb clean`, `lb config`, `lb build` in Docker (`--privileged`)
3. Reuses the generated `chroot/` to package an LXC rootfs tarball
4. Removes live-only packages/artifacts from the template rootfs
5. Verifies expected artifacts exist before exiting

## Output Artifacts

### 1) Live ISO

- File: `live-image-amd64.hybrid.iso`
- Purpose: Bootable test/install media

### 2) Proxmox LXC Template

- File: `fellowshipos-trixie-amd64-<version>_lxc.tar.gz`
- Purpose: Direct container template import for Proxmox

## Deploy to Proxmox

Copy template artifact to your Proxmox node:

```bash
scp fellowshipos-trixie-amd64-1.0_lxc.tar.gz root@<proxmox-host>:/var/lib/vz/template/cache/
```

Then on Proxmox:

```bash
cd /path/to/repo/proxmox
chmod +x create-ct.sh
./create-ct.sh --flavor base
```

Useful examples:

```bash
# Docker-capable node
./create-ct.sh --flavor docker --id 200 --hostname fellowshipos-docker

# Static IP
./create-ct.sh --flavor base --id 201 --ip 192.168.1.50/24 --gw 192.168.1.1
```

Flavor presets currently available in the script:

- `base`
- `docker`
- `monitor`
- `storage`

## First Boot Wizard

A systemd oneshot service runs on first boot:

- Service: `fellowshipos-setup.service`
- Script: `/usr/local/bin/fellowshipos-setup`

The wizard configures:

- Hostname and timezone
- Primary user account and password
- Optional sudo membership
- Optional SSH host key regeneration
- Optional SSH public key install
- Optional node role/component installation

Completion flag:

- `/etc/fellowshipos/.setup-complete`

Wizard log:

- `/var/log/fellowshipos/setup.log`

## Role/Flavor Layering

Manual role layering command:

```bash
sudo homelabos-flavor <flavor>
```

Available flavor scripts in this repo:

- `docker-node`
- `monitor-node`
- `storage-node`
- `gateway-node`
- `dev-node`

Notes:

- The first-boot wizard currently offers `docker-node`, `monitor-node`, and `storage-node` directly.
- Server mode in the wizard can install nginx, Node.js, Python, PostgreSQL, Redis, and Traefik components.

## Customization Guide

Main places to customize:

- Build profile/config:
  - `auto/config`
  - `config/*`
- Package sets:
  - `config/package-lists/base.list.chroot`
  - `config/package-lists/live.list.chroot`
- First boot behavior:
  - `config/includes.chroot/usr/local/bin/fellowshipos-setup`
  - `config/includes.chroot/etc/systemd/system/fellowshipos-setup.service`
- Flavor scripts:
  - `config/includes.chroot/usr/local/lib/flavors/*.sh`
- Chroot hooks:
  - `config/hooks/normal/*.hook.chroot`

To change the template naming version, update `VERSION` in `build.sh`.

## Troubleshooting

### Build fails

- Inspect `build.log`
- Confirm Docker daemon is running
- Confirm outbound network access to Debian mirrors and GitHub

### Missing expected artifacts

`build.sh` checks for both final outputs and exits with failure if either is missing.

### First-boot setup issues

- Inspect `/var/log/fellowshipos/setup.log`
- Re-run manually if needed:

```bash
sudo fellowshipos-setup
```

## Repository Layout (Important Paths)

- `build.sh`: Main build entrypoint
- `auto/config`: live-build configuration command
- `config/package-lists/`: package selections
- `config/includes.chroot/`: files injected into target rootfs
- `config/hooks/normal/`: build-time install/customization hooks
- `proxmox/create-ct.sh`: helper for creating LXC containers from template

## Notes

- This repository currently does not include a license file.
- Build artifacts and live-build generated directories are gitignored by default.
