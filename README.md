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
- Purpose: install FellowshipOS to disk, or try it live

The boot menu asks what the machine is for:

- **Install FellowshipOS (base, Docker, server, monitor, storage)** uses Debian's text installer and gives the whole disk to the system. It asks for the OS disk, first user and time zone. Partitioning erases the disk you choose, so check it carefully if data disks are attached.
- **Install FellowshipOS NAS (graphical)** opens FellowshipOS's own installer, full screen on the machine's monitor. To install from another computer instead, open the address it shows there and type the pairing code from the machine's screen. It:
  - lists every disk with what's on it, and only ever changes the one you choose;
  - suggests a split from the disk's size: the system on LVM (with room to grow later), swap, and the rest as an empty data partition you can turn into storage from the desktop whenever you like. Disks under 64 GB give everything to the system. A slider changes the system's size;
  - asks for the NAS's name, time zone and your account, then needs "erase DISK" typed before it starts.

  After the restart, the first-boot wizard already has the NAS role selected. The NAS installer needs UEFI firmware; on a machine that starts in legacy BIOS mode, use the text installer.
- **Try FellowshipOS (live)** starts the live system without installing.

Nothing at install sets up storage: pools, RAID and ZFS are always optional, and set up later from the NAS desktop.

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

## NAS Desktop

FellowshipNAS has its own web server, `fellowship-nas-web`, which serves a desktop in the browser. After the wizard, open `https://NAS-ADDRESS` (the wizard prints it) and log in with your account on the NAS, the same one you use for SSH. The browser warns about the self-signed certificate until you accept it. **Settings → Certificate** shows its fingerprint to check, and takes your own certificate instead.

Apps open in windows that you can move, resize, minimise and maximise. The desktop remembers where they were in each browser. On a phone, windows open full screen. Every app shows all of its features, including those that can't be used yet, with the reason.

- **Setup** opens by itself the first time an admin logs in. It walks through storage, the LAN subnet, people and shares, alerts and the nightly backup. Once finished or skipped it leaves the desktop; **Settings → Setup** runs it again, and `sudo fnas setup reset` makes it open at the next login.
- **Storage:** pools and disks. **Details and history** on a pool shows how full it has been over the past week and year, when it fills at that rate, what uses its space, and its compression. Creating a pool starts with the kind of storage: ZFS, Unraid-style, traditional RAID or Btrfs. Only ZFS is built so far, and the others say so.
- **Users:** people, their one password, what they can do, and groups.
- **Shares:**
  - Every way to share files (SMB, NFS, and those still to be built), with its state and how to connect.
  - The shares themselves, each with who can use it and its SMB, NFS and snapshot settings.
  - The SMB and NFS settings for the whole NAS.
- **Snapshots and backup:** snapshot schedules, the nightly backup, and restoring a share from it.
- **Health:** every check with its result, plus the services, the system, pools, disks and where alerts go.
- **Alerts** and **Settings**.

The desktop also has status widgets and a notification centre for problems.

Only members of `nas-admins` see the management apps; the wizard adds its user. Add someone else with `sudo usermod -aG nas-admins NAME`. Everyone else sees Settings, where they can change their own password. Sessions end after 30 minutes idle or 12 hours, and repeated wrong passwords slow down and raise an alert.

The server runs as two processes:

- `fellowship-nas-web` faces the network as the unprivileged `fnas-web` account.
- `fellowship-nas-helper` runs as root behind a local socket. It checks passwords with PAM, keeps the sessions, and runs `fnas` for `nas-admins`, so every change from the browser goes through the same checks as the CLI.

Check either with `systemctl status fellowship-nas-web fellowship-nas-helper`.

To work on the desktop, run the real server with a stand-in `fnas` and sample data in a container:

```bash
nas/packages/fellowship-nas-web/dev/run.sh
```

Then open `http://localhost:8080` (the script lists the test accounts) and run `npm run watch` in `nas/packages/fellowship-nas-web/web`.

## NAS Storage

The `nas-node` role installs ZFS from the local apt repo on the ISO. Manage pools with `fnas`, which hides the OS disk and refuses disks that are in use:

```bash
sudo fnas disk list
sudo fnas pool scan
sudo fnas pool import tank --readonly
sudo fnas pool layouts SERIAL1 SERIAL2 SERIAL3 SERIAL4 SERIAL5
sudo fnas pool create tank raidz2 SERIAL1 SERIAL2 SERIAL3 SERIAL4 SERIAL5 --width 4 --spares 1 --confirm "erase 5 disks"
```

Every pool mounts at `/mnt/NAME`, and importing a pool made elsewhere moves it there. Disks can be given by serial, device path or `/dev/disk/by-id` path. A disk with old data also needs `--wipe`. Add `--json` before the command for machine-readable lists.

`pool engines` lists every kind of storage and whether it can be used here. `pool layouts` lists every layout the chosen disks fit (single, mirrored pairs, RAIDZ1, RAIDZ2 or RAIDZ3, in one or more groups, with up to two hot spares), with the usable space and how many failed disks each group survives. The first is the recommended one: RAIDZ2 in groups of 6 to 10 disks for larger sets. `--width` is the number of disks in each group, and `--spares` how many of the disks stand by to replace a failed one at once. Disks of similar size are grouped together and the largest become the spares; disks of different sizes in one group work, but the extra space on the larger ones goes unused, and the Storage app says how much.

Pools grow, and failed disks are replaced, without losing what's on them:

```bash
sudo fnas pool grow tank group SERIAL6 SERIAL7 SERIAL8 SERIAL9 --confirm "erase 4 disks"
sudo fnas pool grow tank expand SERIAL6 --target raidz2-0 --confirm "erase 1 disk"
sudo fnas pool grow tank mirror SERIAL6 --target DISK --confirm "erase 1 disk"
sudo fnas pool grow tank spare SERIAL6 --confirm "erase 1 disk"
sudo fnas pool replace tank OLD-DISK SERIAL6 --confirm "erase 1 disk"
sudo fnas job list
sudo fnas disk identify SERIAL6
```

`group` adds another group like the existing ones, `expand` adds one disk to a RAIDZ group, `mirror` turns a disk on its own into a mirror, and `spare` adds hot spares. Replacing, mirroring, expanding and scrubbing keep going in the background as jobs; `job list` and the desktop show their progress, and they finish even if the browser closes. `disk identify` lights the disk's bay where the enclosure reports bays, and otherwise reads from the disk for a minute so its activity light gives it away.

On a machine installed with the NAS installer, `disk list` also offers the empty data partition on the system disk as "the free part of the system disk", so a machine with one disk can still hold a pool. Only that partition is used; the system's own partitions are never offered. Such a pool has no redundancy until a second disk mirrors it, which Storage suggests.

The installer also leaves room beside the system on its LVM volume group. Give the system more of it from Settings → System space, or with `sudo fnas system grow 8G` (or `all`); it needs no restart, but can't be undone.

## NAS Users

Each person has one account and one password, for the desktop and every way to share. Manage them in the **Users** app or with `fnas`:

```bash
sudo fnas user create sam --full-name "Samwise" --kind shares --groups family --password-stdin
sudo fnas user list
sudo fnas user set sam --kind ssh --admin on
sudo fnas user password sam --password-stdin
sudo fnas user remove sam --keep-home --confirm "remove sam"
sudo fnas group create family --members sam,kim
sudo fnas group members family sam,kim,frodo
```

- `--kind shares` (the default) uses shares only and can't log in over SSH; `--kind ssh` can.
- `--admin` adds the account to `nas-admins`, so it manages the NAS from the desktop.
- `--password-stdin` reads the password: type it, then Ctrl-D. It sets the Linux and Samba passwords together, at least 8 characters.
- `user set NAME --enabled off` turns an account off without removing it.
- Nobody can turn off or remove the only administrator.
- Everyone can change their own password in **Settings → Your account**.

Only accounts set to `--kind ssh` can log in over SSH: they join the `nas-ssh` group, and sshd allows nobody else. Root never logs in over SSH. Give someone keys, and turn passwords off once an administrator has one:

```bash
sudo fnas user keys sam --set-stdin < ~/.ssh/id_ed25519.pub
sudo fnas ssh set --passwords off
sudo fnas ssh show
```

An account added with `adduser` rather than fnas isn't in `nas-ssh` until `sudo fnas ssh refresh` runs, which adds every account with a login shell.

## NAS Shares

Each share is a dataset in `POOL/shares`, mounted at `/mnt/POOL/shares/NAME`, offered over NFS, SMB or both, and only to the LAN subnet chosen in the wizard unless you choose other private networks. Its settings live in the pool's `fellowship-config/shares.json`, so they move with the pool, and `fnas` writes `/etc/exports.d/fellowship.exports` and the Samba files from them:

```bash
sudo fnas share create media tank --everyone --nfs --smb
sudo fnas share create docs tank --user sam=read --group family=write --smb
sudo fnas share access docs --user sam=write
sudo fnas share list
sudo fnas share set media --smb off
sudo fnas lan set 192.168.1.0/24
```

Who can use a share:

- `--everyone`: every person, and every allowed NFS computer, reads and writes as the `nas-share` account, so each protocol can edit the other's files. Use it for media and family folders.
- `--user NAME=read|write` and `--group NAME=read|write`: only those listed. Kept as ACLs on the folder, so new files inherit them. NFS computers need the same numeric user IDs as the NAS for this to work. `share access` rewrites the permissions of every file in the share.

SMB settings for one share, and for the whole NAS:

```bash
sudo fnas share smb media --guest on --recycle on --recycle-days 14 --hidden off
sudo fnas share smb backups --timemachine on --timemachine-size 500
sudo fnas sharing smb --workgroup HOBBITON --min-protocol SMB3 --encryption required
```

- **Guests** read without a password, and only on shares for everyone.
- **The recycle bin** keeps files deleted over SMB in `.recycle` for the chosen number of days.
- **Time Machine** sizes are in GB, and 0 means no limit.
- **SMB 1** is never offered.

NFS settings for one share, and for the whole NAS:

```bash
sudo fnas share nfs media --client 192.168.1.0/24=ro --client 192.168.1.20=rw --mapping auto --async off
sudo fnas share nfs media --home-network
sudo fnas sharing nfs --v3 on --fixed-ports on --threads 16
```

- **Clients** are private networks or addresses, never `*`.
- **`--mapping`:**
  - `auto` follows who can use the share.
  - `all` squashes everyone to the share account.
  - `root` squashes root only.
  - `none` trusts root on the client.
- **NFS version 3** is off unless turned on.
- **Fixed ports** put mountd on 20048, statd on 32765 and 32766, and lockd on 32768.

`share set` keeps the data. `share delete` destroys it, and lists the snapshots that go with it before asking for a typed confirmation.

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

`--force` is needed when the old system never exported the pool, which is the usual case after a dead OS disk; make sure that system is switched off first. `fnas` still refuses a pool that another running system has imported. One restore takes over every pool the old install saved to. Passwords are never saved, so `config restore` lists the accounts that need one again; set it in Users or with `fnas user password`. Groups, memberships, administrators and the SMB and NFS settings come back with the accounts. Until you restore, this install won't overwrite what the pool holds, and an alert reminds you. To replace the saved settings instead, use `fnas config adopt`.

To test: `python3 -m unittest nas/tests/unit/test_fnas.py` runs without touching any disks, and `nas/tests/vm/vm.sh` installs into a VM with data disks: four by default, or any number and sizes with `FELLOWSHIP_DATA_DISKS="8G 8G 16G"`.

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
- `config/bootloaders/grub-pc/`: the boot menu
- `installer/`: the graphical NAS installer (Go engine and its page); `installer/cmd/fellowship-installer -demo` shows sample disks and pretends to install, for working on the page
- `proxmox/create-ct.sh`: helper for creating LXC containers from template

## Notes

- This repository currently does not include a license file.
- Build artifacts and live-build generated directories are gitignored by default.
