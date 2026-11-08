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
- Purpose: Live session, plus "Start installer" in the boot menu to install to disk

The installer asks for the OS disk, first user and time zone. Partitioning erases the disk you choose, so check it carefully if data disks are attached.

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

A systemd oneshot service runs on first boot of an installed system or LXC container, but not in a live session:

- Service: `fellowshipos-setup.service`
- Script: `/usr/local/bin/fellowshipos-setup`

The wizard configures:

- Hostname and timezone
- Primary user account and password, with optional sudo membership (LXC template only: the installer creates this user on disk installs)
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
- `nas-node` (full installs only: installs ZFS and the `fnas` tool)
- `gateway-node`
- `dev-node`

Notes:

- The first-boot wizard currently offers `docker-node`, `monitor-node`, `storage-node`, and `nas-node` directly.
- Server mode in the wizard can install nginx, Node.js, Python, PostgreSQL, Redis, and Traefik components.

## NAS Web Pages

These pages run on Cockpit for now. Phase 5 of [the plan](docs/fellowship-nas-plan.md) replaces Cockpit with FellowshipNAS's own web server and a desktop in the browser.

After the wizard, open `https://NAS-ADDRESS:9090` (the wizard prints it), log in as the wizard's user and choose **NAS Setup**. It walks through storage, the LAN subnet, shares and SMB passwords, alerts and the nightly backup. **Storage**, **Shares** and **Health** stay in the menu afterwards, and Cockpit's own pages (logs, services, terminal) sit alongside them. The browser warns about Cockpit's self-signed certificate until you trust it.

Only members of `nas-admins` can make changes; the wizard adds its user. Add someone else with `sudo usermod -aG nas-admins NAME`, and they log in again. A polkit rule lets that group run `fnas` and nothing else as root, so every change from the browser goes through the same checks as the CLI.

To work on the pages: `cd nas/packages/fellowship-desktop/web && npm install && npm run watch`, then copy `dist/` to `~/.local/share/cockpit/fellowship-desktop` on a test machine.

## NAS Storage

The `nas-node` role installs ZFS from the local apt repo on the ISO. Manage pools with `fnas`, which hides the OS disk and refuses disks that are in use:

```bash
sudo fnas disk list
sudo fnas pool scan
sudo fnas pool import tank --readonly
sudo fnas pool create tank raidz1 SERIAL1 SERIAL2 SERIAL3 --confirm "erase 3 disks"
```

Disks can be given by serial, device path or `/dev/disk/by-id` path. A disk with old data also needs `--wipe`. Add `--json` before the command for machine-readable lists.

## NAS Shares

Each share is a dataset in `POOL/shares`, offered over NFS (version 4 only), SMB or both, and only to the LAN subnet chosen in the wizard:

```bash
sudo fnas share create media tank --mode shared --nfs --smb
sudo fnas share create chris tank --mode matched --owner chris --nfs
sudo fnas share list
sudo fnas share set media --smb off
sudo fnas lan set 192.168.1.0/24
```

- `shared`: everyone reads and writes as the `nas-share` account, so each protocol can edit the other's files. Use it for media and family folders.
- `matched`: only the owner gets in, and NFS clients need the same numeric user ID as the NAS. Use it for personal folders.

Anyone connecting over SMB needs a Samba password, set on the Shares page or with `sudo fnas user smb-password USER --password-stdin` (type the password, then Ctrl-D). `share set` keeps the data. `share delete` destroys it, and lists the snapshots that go with it before asking for a typed confirmation.

## NAS Protection

**Snapshots** start on their own for every share. Each keeps 24 hourly, 30 daily and 6 monthly snapshots, which appear in Windows as Previous Versions:

```bash
sudo fnas snapshot list media
sudo fnas snapshot policy media --hourly 48 --monthly 12
```

A policy of all zeros stops new snapshots and keeps the ones already there.

**Backup** copies every share to a second pool, such as a USB disk, at 3am each night. If the pool is unplugged, the backup fails and an alert says so, and the next run picks up where the last one stopped:

```bash
sudo fnas backup set tank usb
sudo fnas backup run
sudo fnas backup show
sudo fnas backup list
sudo fnas backup restore media
```

`restore` brings back a share that no longer exists, with its snapshots and settings, and never replaces one. Add `--to POOL` to restore to a new pool.

**Alerts** cover failing disks, pool faults and errors, pools over 80% full, stopped snapshots, overdue scrubs and backups, and kernels with no ZFS module. They're always kept on the NAS, and can also go to email or an [ntfy](https://ntfy.sh) topic:

```bash
sudo fnas alert list
echo 'PASSWORD' | sudo fnas alert email-set --to me@example.com --host smtp.example.com --user me@example.com --password-stdin
sudo fnas alert ntfy-set https://ntfy.sh/a-long-private-topic
sudo fnas alert test
sudo fnas check
```

**Saved settings:** every pool keeps a copy of the LAN subnet, users and their IDs, alert channels without passwords, and the backup job. After reinstalling, import the pool and bring them back:

```bash
sudo fnas pool import tank --force
sudo fnas config restore tank
```

`--force` is needed when the old system never exported the pool, which is the usual case after a dead OS disk; make sure that system is switched off first. `fnas` still refuses a pool that another running system has imported. One restore takes over every pool the old install saved to. Passwords are never saved, so `config restore` lists the ones to set again, and SMB users need their password set again on the Shares page or with `fnas user smb-password`. Until you restore, this install won't overwrite what the pool holds, and an alert reminds you. To replace the saved settings instead, use `fnas config adopt`.

To test: `python3 -m unittest nas/tests/unit/test_fnas.py` runs without touching any disks, and `nas/tests/vm/vm.sh` installs into a VM with four data disks.

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
