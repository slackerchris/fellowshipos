# FellowshipNAS Architecture Plan (revision 3)

Nov 7, 2026 · @Chris

Revision 2 made four foundational choices without putting them to Chris: Cockpit as the web engine, ZFS as the only storage engine, a NAS role separate from the existing storage node, and plain pages before the desktop. Revision 3 replaces all four with his decisions. Phases 0–3 (install, ZFS, sharing, protection) are built and stay. The Phase 4 Cockpit pages are superseded; their page logic and the `fnas` commands behind them carry over.

## What changed from revision 2

| Area | Revision 2 | Revision 3 |
| --- | --- | --- |
| Web engine | Cockpit, with custom pages on top | Our own server, `fellowship-nas-web`, written in Go. No Cockpit in the image |
| Web interface | Plain pages first, windowed desktop in the last phase | The windowed desktop from the start: login screen, desktop, windows, taskbar, launcher |
| Storage engine | ZFS only, fixed by the role | Chosen per pool inside the OS: ZFS, Btrfs, or mergerfs + SnapRAID, side by side on one NAS |
| Wizard roles | "NAS / Home Server" plus a separate "Storage node (mergerfs + SnapRAID)" | One NAS role; the storage node is absorbed as the mergerfs + SnapRAID engine |
| Scale | Four disks, one data pool | 12–16 drives behind an HBA, several pools, multi-group ZFS pools, spares, multi-parity SnapRAID, drive bay identification |
| Secure Boot | Stops the whole role | Only ZFS is unavailable, with the reason shown; Btrfs and mergerfs still work |
| Share settings | ZFS dataset properties (`sharenfs`, `org.fellowshipos:*`) | Stored in each pool's config folder for every engine; `fnas` writes NFS exports and Samba files from them |
| Backup | `syncoid` between two ZFS pools | Engine-native when both pools match (`syncoid`, `btrfs send`), `rsync` otherwise |

## Overview

FellowshipNAS is the "NAS / Home Server" role in FellowshipOS: storage pools on the engine of your choice, NFS and SMB shares, automatic snapshots where the engine supports them, health alerts, and a desktop in the browser. It replaces TrueNAS for a home setup, without TrueNAS's apps, VMs, clustering and middleware.

**In scope**

- Install FellowshipOS to disk from the ISO
- One wizard option that turns an installed system into a NAS
- Several pools at once, each on its own engine: ZFS, Btrfs, or mergerfs + SnapRAID
- 12–16 drives behind an HBA, with each drive identified by bay where the hardware reports it
- NFS and SMB shares on any pool
- Snapshots (ZFS and Btrfs), parity syncs (SnapRAID), scrubs, SMART monitoring and alerts
- Scheduled backup from one pool to another
- A desktop in the browser, served by our own web server: windows, taskbar, app launcher, notification centre, and a real file manager

**Out of scope for v1**

- Container apps, VMs, Kubernetes, iSCSI, off-site replication, clustering, directory services
- The NAS role in the LXC template (a container can't own disks or load ZFS)
- Btrfs RAID5/6, which is still not considered safe
- Native mobile apps (the desktop works in a phone browser instead)
- A terminal in the browser (use SSH)

**Design direction:** the feel of Synology DSM and FygoOS: a desktop in the browser with apps in windows, served by the NAS's own web server. FellowshipOS's own name, icons, colours and layout; nothing copied from either product.

**Guiding rules**

1. Layer on top of Debian, never fork it, so Debian keeps supplying security updates.
2. Use proven packages for storage and sharing (ZFS, Btrfs, mergerfs, SnapRAID, Samba, NFS). Write our own code for everything the user sees and for the path from browser to system.
3. Anything that can destroy data happens behind a confirmation, never silently. The only disk the installer may touch is the OS disk, chosen by hand.
4. The person using the NAS makes the choices: which engine, which layout, which disks. The system explains each option; it doesn't choose for them.

## System architecture

```
Browser ──HTTPS──► fellowship-nas-web ──► fnas ──► ZFS / Btrfs / mergerfs + SnapRAID, NFS, Samba
                        │                   └─────► Sanoid, btrbk, smartd, zed, alerts
                        └──► file helper, run as the logged-in user (Files app)
NFS and SMB clients ─────────────────────────────► NFS server, Samba
```

1. **Clients:** browsers, NFS clients, SMB clients
2. **Web:** the FellowshipNAS Desktop (TypeScript, Preact) served by `fellowship-nas-web` (Go)
3. **Helper CLI:** `fnas`, the only code that changes storage, shares or system settings. Every argument is checked, because it is the privilege boundary
4. **Services:** NFS, Samba, Sanoid, btrbk, SnapRAID schedules, `smartd`, `zed`, alerts
5. **Storage:** the engines, on Debian Trixie

NFS and SMB clients talk straight to their servers. The browser path always goes through `fnas`, so all risky logic sits in one testable place, used the same way from the desktop and over SSH.

## Install and first boot

There are three separate stages, and each one owns its own decisions.

| Stage | Runs on | Decides |
| --- | --- | --- |
| Debian installer | ISO, local console | OS disk, first user, time zone |
| First-boot wizard | `tty1`, local console | Hostname, SSH, role, LAN subnet |
| NAS setup | The desktop, in a browser | Pools and their engines, shares, users, alerts, backup |

**Debian installer:** `--debian-installer live` in `auto/config` makes the installer copy the live system to disk. Partitioning stays manual and is never preseeded, because the data disks are attached. With data disks present, the installer's default partitioning method becomes "largest continuous free space", so the person must pick "use entire disk" and then the OS disk. The docs should recommend unplugging data disks for a first install if the OS disk isn't obvious.

The installer needs three corrections, in place since Phase 0:

- **Removal list:** live-build's list includes everything added after its install pass, so it would remove Docker and keep `live-boot`. A binary hook replaces it with just the live packages.
- **Apt sources:** the installer leaves the install media as the only source, which means no security updates and no ZFS. A `late_command` restores `/usr/share/fellowshipos/sources.list`.
- **Preseed loading:** the boot menu doesn't load `/install/preseed.cfg` by itself, so `--bootappend-install` passes it.

With root login off, the installer always creates a first user, with `sudo`. The wizard reuses that user, which also leaves a working login if the wizard fails.

**Base system changes (Phase 0):** the first-boot wizard doesn't run in a live session, waits for the network, and pauses before it exits so failures stay on screen.

**Role option:** one "NAS / Home Server" entry in the wizard. The old "Storage node (mergerfs + SnapRAID)" entry is removed, and its packages become part of the NAS role. Other roles can still be layered afterward with `homelabos-flavor`.

**Role install:** the `nas-node.sh` flavor:

1. Stops with a clear message in a container.
2. Checks the network and Debian mirror access.
3. Installs `fellowship-nas`, `fellowship-nas-smb` and `fellowship-nas-web`, which bring every engine's tools.
4. Builds and loads the ZFS module. If Secure Boot is on or the module fails, the role still completes: ZFS is marked unavailable with the reason, and Btrfs and mergerfs pools work.
5. Adds the wizard's user to `nas-admins` and writes `/etc/fellowshipos/.nas-role-complete`.

The flavor is safe to run twice; if the marker is missing, the wizard shows the retry command, `sudo homelabos-flavor nas-node`. At the end, the wizard prints the desktop's address, `https://ADDRESS`.

**Packages:** `fellowship-nas` is a meta-package with every engine's dependencies. `fellowship-nas-smb` holds Samba and discovery, `fellowship-nas-tools` holds `fnas`, and `fellowship-nas-web` holds the server and the desktop. For v1 they ship as a local apt repo inside the ISO, with dependencies from Debian during the role install. A hosted, signed repo arrives in the release phase.

## Storage

**One idea for every engine:** a pool is a named set of disks mounted at `/NAME`, with shares inside it and a config folder that remembers its settings. The Storage app shows every pool the same way and says plainly what each engine can and can't do. Different pools can use different engines on the same NAS, for example a ZFS pool for family files, a mergerfs pool for films, and a Btrfs mirror of two SSDs.

**Choosing an engine** (shown when creating a pool, with this comparison)

| | ZFS | Btrfs | mergerfs + SnapRAID |
| --- | --- | --- | --- |
| Best for | Important files, photos, documents | Small mirrors, SSDs, mixed sizes | Large media libraries on mixed disks |
| Redundancy | Mirrors, RAIDZ1/2/3, hot spares | Mirror (`raid1`), three copies (`raid1c3`), `raid10` | 1–6 parity disks |
| Mixed disk sizes | Wastes space | Works | Works |
| Add one disk later | To a RAIDZ group (RAIDZ expansion) or as a new group | Yes | Yes |
| Checksums and self-repair | Yes, live | Yes, live | At sync and scrub time only |
| Protected immediately | Yes | Yes | Only after the next sync (nightly by default) |
| Snapshots and Windows "Previous Versions" | Yes (Sanoid) | Yes (btrbk) | No |
| A failed disk loses | Nothing within the layout's tolerance | Nothing within the layout's tolerance | Nothing up to the parity count, if synced; each disk stays readable on its own |
| Secure Boot | Must be off | Works | Works |
| Imports an existing TrueNAS pool | Yes | No | No |

**Disk handling**

- Pools are always built from `/dev/disk/by-id` paths, so device names can change.
- `fnas` never offers the OS disk, refuses disks in use, and needs `--wipe` plus a typed confirmation ("erase 6 disks") for disks with old data.
- **Drive bays:** where the HBA and backplane report enclosure slots (SES, through `/sys/class/enclosure`), disks are shown and alerted by bay ("Bay 7, serial ZL2…"). The locate light is blinked with `ledctl` or `sg_ses`. Without enclosure reporting, disks are identified by serial number, and "Identify" reads from the disk so its activity light flickers.
- **HBA:** must run in IT (pass-through) mode, so every disk is visible on its own with its SMART data. SAS and SATA disks behind it are both supported.
- **Spin-down:** mergerfs data disks may spin down when idle (`hd-idle`); ZFS and Btrfs disks don't, because both touch every disk regularly.

**ZFS** (built in Phases 1–3)

- Layouts: single, mirror, RAIDZ1, RAIDZ2, RAIDZ3, and pools of several groups ("two 6-disk RAIDZ2 groups"). Hot spares. Growing a pool by adding a group or expanding a RAIDZ group by one disk.
- Defaults: `ashift=12`, `compression=lz4`, `atime=off`, `xattr=sa`, `acltype=posixacl`, `dnodesize=auto`.
- Shares are datasets in `POOL/shares`; settings are saved in `POOL/fellowship-config`.
- Snapshots by Sanoid, monthly scrub from Debian's `zfsutils-linux` job, events from `zed`.
- **Kernel and ZFS updates:** no version pin. `zfs-kernel-check` runs after kernel and header installs; with no module for the new kernel it keeps GRUB on the running kernel and `fnas check` raises an alert. `unattended-upgrades` never reboots by itself.

**Btrfs**

- Layouts: single, `raid1`, `raid1c3`, `raid10`. Metadata uses at least the same redundancy as data. RAID5/6 isn't offered.
- Shares are subvolumes in `/NAME/shares`; settings in `/NAME/.fellowship`.
- Snapshots by `btrbk` into `/NAME/.snapshots`, named so Samba shows them as "Previous Versions". Monthly `btrfs scrub`, and `btrfs device stats` read by the hourly check.
- Mounted by filesystem UUID through a systemd mount unit, with `degraded` mounting only after a confirmed choice in the Storage app.

**mergerfs + SnapRAID** (absorbs the old storage node)

- Each data disk gets its own ext4 filesystem, mounted at `/srv/disks/SERIAL`; mergerfs joins them at `/NAME`. Parity disks must be at least as large as the largest data disk.
- Shares are top-level folders in `/NAME`; settings in `/NAME/.fellowship`, copied to every data disk so the pool can be rebuilt after a reinstall.
- `fnas` writes the mergerfs mount and `snapraid.conf`. A nightly `snapraid sync` (skipped with an alert if too many files were deleted, a sign of a missing disk), then a weekly partial `snapraid scrub`.
- A failed data disk is rebuilt onto its replacement with `snapraid fix`, through a guided flow in the Storage app.
- The Storage app says plainly that files changed since the last sync aren't protected yet, and when the last sync ran.

**Config recovery** (built in Phase 3 for ZFS; extended to every engine): after every change, `fnas` saves the LAN subnet, users and their IDs, share settings, schedules, the backup job and alert channels without secrets to every pool. After a reinstall, the desktop finds the pools, imports them, and offers to restore the settings. A fresh install never overwrites a saved copy it hasn't restored, and a restore checks everything on the disk before using it.

**Migrating from TrueNAS:** export the pool from TrueNAS, then import it read-only first (`fnas pool import NAME --readonly`), check permissions and user IDs, and only then import it normally. Never run `zpool upgrade` during migration, because it blocks a return to TrueNAS.

## Sharing

**One owner per protocol, the same on every engine:** `fnas` keeps each share's settings in its pool's config folder and generates both protocols' configuration from them. ZFS's `sharenfs` and `sharesmb` properties aren't used (revision 2 used `sharenfs`; Phase 6 moves it).

**NFS:** `nfs-kernel-server`, NFSv4 only by default. `fnas` writes `/etc/exports.d/fellowship.exports`, restricted to the LAN subnet, never `*`.

**SMB:** one Samba file per share plus a master file `smb.conf` includes. `wsdd2` for Windows network discovery and Avahi for macOS. SMB passwords are set from the Users app (`fnas user smb-password`). On ZFS and Btrfs, `vfs_shadow_copy2` shows snapshots as "Previous Versions"; mergerfs shares have none.

**User ID modes, per share:**

| Mode | How it works | Use when |
| --- | --- | --- |
| Matched users | Files keep their owners; NFS clients need the same user IDs | Personal folders, your own Linux machines |
| Shared folder | NFS `all_squash` and Samba `force user` to one fixed account, `nas-share` (ID 3000) | Media, family shares, anything everyone can write |

## Snapshots, backup, health and alerts

**Snapshots:** Sanoid on ZFS, btrbk on Btrfs, both generated by `fnas` from the same per-share policy (hourly 24, daily 30, monthly 6 by default). mergerfs shares show "No snapshots on this engine" rather than hiding the option.

**Backup:** one or more scheduled jobs, each copying a pool's shares to another pool. `syncoid` between ZFS pools and `btrfs send` between Btrfs pools keep snapshots; any other pair uses `rsync`, keeping a snapshot on the target where its engine allows. A failed or overdue backup raises an alert.

**Health monitoring**

| Source | Watches |
| --- | --- |
| `smartd` | SMART errors, reallocated and pending sectors, temperature, for every disk on the HBA |
| `zed` | ZFS pool degraded, checksum errors, scrub and resilver results |
| Hourly check (`fnas check`) | Pools over 80% full, missing or failed disks on any engine, Btrfs device errors, SnapRAID sync or scrub overdue or failed, snapshots stopped, scrub too old, backup overdue, ZFS module missing for a kernel, unrestored settings |

**Alerts:** every source calls `fellowship-nas-notify`, which sends email (`msmtp`) and ntfy push, and always records the alert in the desktop's notification centre. Undelivered alerts are retried for a day.

**As built in Phase 3 (ZFS)**

- Sanoid's daily and monthly runs are moved to 23:30 and 00:15 UTC, so hourly, daily and monthly snapshots don't share a timestamp. The very first run still makes all three at once.
- `smartd` runs inside VMs too (Debian's unit skips them), with its own config that calls `fellowship-nas-notify` directly.
- After a reinstall, `fnas pool import --force` imports a pool the old system never exported, but never one another running system has imported (ZFS multihost). One restore takes over every pool the old install saved to.

Not checked in the VM: `smartd` reacting to a real disk fault (the alert hook was tested directly), and the snapshot timing across a day and month boundary.

## Web server: `fellowship-nas-web`

Our own server, written in Go, replaces Cockpit entirely. It serves the desktop and a JSON API, and it never changes the system itself: every change goes through `fnas`.

**Three processes, so the part facing the network has no special rights** (the same split OpenSSH uses):

| Process | Runs as | Does |
| --- | --- | --- |
| Web front | Unprivileged `fnas-web` account | HTTPS, the desktop's files, sessions, the API |
| Privileged helper | root, reachable only through a local socket | Checks passwords with PAM, reports a user's groups, runs `fnas` for members of `nas-admins`, starts file helpers |
| File helper | The logged-in user | Every Files app operation, so Linux permissions decide what each person sees |

**Login and sessions**

- Normal Linux accounts, checked with PAM; no separate user database.
- A random session token in a `Secure`, `HttpOnly`, `SameSite=Strict` cookie, kept server-side. Sessions end after 30 minutes idle and 12 hours in total; long transfers count as activity.
- Every API request that changes something must carry a matching `Origin` and a per-session token header, against cross-site attacks.
- Failed logins slow down per address and per account, and are logged; repeated failures raise an alert.
- The desktop is HTTPS only: port 443, with port 80 redirecting. A self-signed certificate is made at first start, and the Settings app accepts your own.

**API:** one endpoint per `fnas` action, with arguments passed as a list (never through a shell). Long jobs (scrubs, resilvers, backups, SnapRAID syncs) report progress over server-sent events.

**Files:** served by the file helper with full `Range` support (video seeking) and file sizes (download progress). Uploads are chunked and resumable; folders download as a streamed zip.

**Built with:** Go's standard library for HTTPS, routing and streaming, and a small `cgo` binding to `libpam` written here. No web framework. Built in the same container as the ISO, with Debian's Go.

**Phase 4's transfer test (Cockpit 337, in a KVM VM)** showed what Cockpit couldn't do and set targets for our server: 10 GB upload at 85 MiB/s and download at 170 MiB/s through Cockpit, 5,000 thumbnails made in 17 s and shown in 5 s, folders zipped while streaming; but no video seeking (no `Range` support) and no download progress. Our server must match those speeds and pass the two Cockpit failed.

## Desktop

The desktop is the interface from the start. Every app opens in a window; there are no standalone pages.

- **Login screen** with FellowshipOS branding.
- **Desktop:** wallpaper, app icons, and widgets (pool health and capacity, disk temperatures, last scrub, sync, snapshot and backup, recent alerts).
- **Windows:** move, resize, minimise, maximise, stack, several open at once; layout remembered per user.
- **Taskbar:** open windows, launcher, clock, notification centre.
- **Phone:** windows open full-screen and the taskbar becomes a bottom bar.
- **Built with:** TypeScript, Preact 10 and esbuild (chosen in Phase 4). Phase 4's Setup, Storage, Shares and Health code moves into windows.
- **Non-admins** see only Files and their own settings.
- **Look (chosen by Chris):** a dark forest palette, with the fantasy in the atmosphere only. Controls stay clean and readable: solid panels, softly rounded windows, crisp icons, gold used sparingly. The theme follows the device; the light theme is the same palette in daylight.

  | Element | Colour |
  | --- | --- |
  | Desktop background | Midnight forest `#101A17` |
  | Windows and panels | Dark pine `#1B2923` |
  | Raised surfaces | Moss charcoal `#29392F` |
  | Borders | Weathered green `#405247` |
  | Primary text | Pale parchment `#EFE7D5` |
  | Secondary text | Muted linen `#B7B29E` |
  | Buttons and selection | Aged gold `#C5A465` |
  | Healthy | Leaf green `#86B88A` |
  | Warnings | Amber `#E0A452` |
  | Errors | Muted vermilion `#D87667` |

  Misty mountain wallpaper, generated at build time, and a restrained serif for the FellowshipOS wordmark. Nothing is copied from DSM or FygoOS.

**Apps in v1**

| App | What it does |
| --- | --- |
| Setup | First-run tour: import or create pools, LAN subnet, first share, users, alerts, backup. Rerunnable after adding disks |
| Storage | Pools on every engine, disks by bay, create with engine and layout choice, grow, scrub, sync, guided disk replacement, identify light |
| Shares | NFS and SMB shares on any pool, user ID modes, LAN subnet |
| Snapshots and backup | Schedules per share, browse and restore, backup jobs |
| Users | Accounts, groups, `nas-admins`, SMB passwords |
| Health | SMART per disk in plain words, temperatures, current problems |
| Alerts | Email and ntfy setup, test button, history |
| Files | The file manager (below) |
| Settings | Hostname, time, certificate, updates, restart and shut down, active sessions |

**Files app**

- Sidebar: pools, shares, home folder, favourites, recent
- Toolbar: back/forward/up, breadcrumb, search, grid/list, sort
- Grid with thumbnails for images and video; list with name, size, modified, owner, permissions
- Multi-select with click, Shift and Ctrl; drag to move, Ctrl-drag to copy
- Right-click menu: open, download, rename, copy, cut, paste, delete, compress, properties, previous versions
- Upload by dragging in from the computer; download folders as zip
- Quick look for images, video (with seeking), audio, PDF and text with the space bar
- **Previous versions panel** on ZFS and Btrfs: a timeline of snapshots containing the file, with preview, "Restore a copy" and "Replace"

## Security model

Built for a trusted home LAN; nothing is exposed to the internet by default.

- **Two roles:** `nas-admins` manage storage, shares, users and settings; everyone else sees Files for folders they already have permission to.
- **Privileges:** only the privileged helper runs as root. It runs `fnas` for `nas-admins` members and nothing else, and `fnas` checks every argument.
- **Network:** `nftables` allows HTTPS, NFS and SMB from the LAN subnet only.
- **Remote access:** not built in; a VPN such as WireGuard or Tailscale is documented.
- **Destructive actions** (destroy a pool, delete a share, roll back a snapshot, wipe disks) need a typed confirmation that names what will be lost.
- **Updates:** `unattended-upgrades` for Debian security updates, including the kernel, with no automatic reboot.
- **Review:** the web server's login, session and helper code gets a security review before release.

## Repository layout and packaging

```
auto/config                  installer and preseed boot options
build.sh                     builds the Fellowship packages and the ISO in a Debian container
config/includes.chroot/
  usr/local/bin/fellowshipos-setup       role option, web address, retry message
  usr/local/lib/flavors/nas-node.sh      the NAS role
config/hooks/normal/0800-installer-remove-list.hook.binary
config/preseed/fellowshipos.cfg.installer
CLAUDE.md                    rules for Claude Code
docs/
nas/
  packages/
    fellowship-nas/          meta-package: every engine's tools, schedules, kernel hook, alert hooks
    fellowship-nas-smb/      Samba and discovery
    fellowship-nas-tools/    fnas and fellowship-nas-notify
    fellowship-nas-web/      Go server and helpers, plus the desktop (web/)
  theme/                     icons, wallpaper, colour tokens
  tests/
    vm/                      vm.sh: install from the ISO's boot menu, with up to 16 virtual disks
    unit/                    fnas tests that never touch a real disk
```

`fellowship-nas-web` replaces Phase 4's Cockpit package, `fellowship-desktop`. All FellowshipNAS packages share one version number.

## Build phases and test checklist

No phase starts until the previous gate passes in a VM. From Phase 6 on, the VM has 16 virtual disks.

| Phase | Work | Gate | Status |
| --- | --- | --- | --- |
| 0. Base install | Debian live installer, wizard changes, `nas-node` flavor | Install from ISO, reboot, and settings persist; LXC template still builds | Done |
| 1. ZFS storage | ZFS packages, kernel hook, `fnas` pool commands, pool import | Create and import a pool from the CLI; real TrueNAS pool imports read-only | Done, except the TrueNAS import |
| 2. Sharing | NFS, SMB, user ID modes | Mount from Linux and Windows; "Previous Versions" works | Done, except a real Windows client |
| 3. Protection | Snapshots, backup, health, alerts, config copy | Failure tests pass from the CLI; a reinstall restores config | Done |
| 4. Cockpit pages | Transfer test; Setup, Storage, Shares and Health on Cockpit | Superseded by revision 3 | Code carries over to Phase 5 |
| 5. Web server and desktop | `fellowship-nas-web` with login, sessions, the helper split and the API; the desktop shell; Setup, Storage, Shares, Health, Alerts and Settings in windows; Cockpit removed | Full setup from the desktop in a browser; web security tests pass; non-admins can't reach admin apps or the API; no Cockpit installed | Done. Progress over server-sent events moves to Phase 6, with the long jobs it reports on |
| 6. Many drives and ZFS depth | Progress for long jobs (server-sent events), drive bays and identify light, multi-group pools, RAIDZ3, spares, growing pools, guided disk replacement, share settings moved to pool config, NFS through `exports.d`, the engine layer inside `fnas` | 16-disk VM: build a two-group RAIDZ2 pool with a spare, fail a disk, replace it from the desktop | |
| 7. mergerfs + SnapRAID | Engine, sync and scrub schedules, `snapraid fix` flow, spin-down, absorbing the old storage node | Create, share, sync; fail a data disk and rebuild it; alerts for a missed sync | |
| 8. Btrfs | Engine, btrbk snapshots, Previous Versions, scrub, degraded mount | Create a mirror, restore a Previous Version from Windows, fail a disk and replace it | |
| 9. Files and remaining apps | Files, Snapshots and backup, Users | Restore a file from a snapshot in Files; 10 GB transfers and video seeking; usable on a phone | |
| 10. Release | Theme and icons, hosted signed apt repo, updates in Settings | Updates arrive from the hosted repo | |

**Failure tests (run in the VM before every release)**

Install and role:

- [ ] Install to disk from the ISO with data disks attached, reboot: wizard runs once and settings persist
- [ ] Boot the ISO live: the first-boot wizard does not run
- [ ] Cut the network during the role install: failure is shown, and the retry command completes it
- [ ] Enable Secure Boot: role completes, ZFS shows as unavailable with the reason, Btrfs and mergerfs pools work
- [ ] Try the role in the LXC template: role stops with a clear message

Pools, on every engine:

- [ ] Reinstall the OS with pools attached: the desktop offers import and restore, and no disk is offered for erasing by default
- [ ] Reorder the disks: every pool still mounts
- [ ] Pull a disk: the pool shows degraded, the alert names the bay, and the identify light finds it
- [ ] Replace the disk through the guided flow: the pool returns to healthy
- [ ] Pull more disks than the layout survives: the desktop reports it clearly and nothing is destroyed automatically
- [ ] Fill a pool past 80%: a warning alert arrives
- [ ] SnapRAID: delete many files, then run the nightly job: sync is skipped and an alert arrives

Updates:

- [ ] Reboot after a kernel update: pools import and shares come back
- [ ] Force a ZFS module build failure: alert arrives, and the system boots the last good kernel with its pools

Data recovery:

- [ ] Restore a deleted file from a snapshot in Files and through Windows Previous Versions (ZFS and Btrfs)
- [ ] Restore a share from a backup job, between matching and mismatched engines
- [ ] Restore config after a reinstall: shares, schedules and user IDs return

Web and access:

- [ ] Log in as a non-admin: admin apps are hidden and their API calls are refused
- [ ] A request from another site, or without the session token, is refused
- [ ] Repeated wrong passwords slow down and raise an alert
- [ ] An idle session ends; a long upload keeps it alive
- [ ] Mount from an NFS client with a mismatched user ID: a clear permission result
- [ ] Write to a shared folder over NFS and SMB: each protocol can edit the other's files

## Open questions and risks

**Still to confirm**

- [ ] Does the TrueNAS pool import read-only on Debian's ZFS?
- [ ] Does the HBA run in IT mode, and does the backplane report bays (SES)?
- [ ] Where will the signed apt repo be hosted?

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Wrong disk chosen in the installer | Data loss | Partitioning is never preseeded; docs recommend unplugging data disks |
| Existing pool mistaken for empty disks | Data loss | Pool scan runs first; disks with labels or filesystems are flagged and need `--wipe` |
| A security hole in our own web server | Someone on the LAN gets root | Privilege split, `fnas` as the only root door, LAN-only firewall, tests for every login and session rule, security review before release |
| Three engines triple the work | Phases take longer | One engine layer in `fnas`; ZFS first, then mergerfs, then Btrfs, each with its own gate |
| SnapRAID's unsynced window | Recent files lost with a disk | Nightly sync, the last sync shown in Storage, overdue alerts |
| ZFS module fails after a kernel update | ZFS pools not mounted after reboot | Post-install check, last good kernel as default, alert |
| Bug in pool or share scripts | Data loss | All destructive logic in `fnas`, tested in VMs, typed confirmations |
| Pool loss | Data loss | Scheduled backup to another pool, with overdue alerts |
| Desktop scope grows | Months instead of weeks | Apps reuse Phase 4's logic; the window manager is small and built once |
| UID mismatch on NFS | Confusing permission errors | Per-share user ID mode with a clear warning |
| Look drifts too close to Synology | Legal trouble if sold | Own icons, palette and layout from the start |

**Next step for Claude Code:** "Read `CLAUDE.md` and this plan, then start Phase 6." Phase 1 still needs the read-only import of a real TrueNAS pool, and Phase 2 still needs a real Windows client: mount a share, then check Previous Versions.
