# FellowshipNAS Architecture Plan (revision 4)

Nov 9, 2026 · @Chris

Revision 4 comes from Chris testing the desktop in a VM on Nov 9. It corrects choices revision 3 made without him and fixes screens that hid what the system can do. The full list of his points is in [fellowship-nas-changes.md](fellowship-nas-changes.md).

Phases 0–7 are built and stay. The order of everything still to come changes.

## What changed from revision 3

| Area | Revision 3 | Revision 4 |
| --- | --- | --- |
| Storage engines | ZFS, Btrfs, mergerfs + SnapRAID. Traditional RAID from Chris's FygoOS list had been swapped for Btrfs without asking him | Four engines: ZFS, Unraid-style (mergerfs + SnapRAID), traditional RAID (mdadm, then a filesystem), and Btrfs |
| Order of work | ZFS in depth first (Phase 7), the other engines later | No more ZFS depth until the other engines exist. The engine choice is on screen now (Phase 8). Users and full SMB and NFS settings come next (Phase 9, Chris, Nov 9), then traditional RAID and Unraid-style |
| Unraid-style disks | ext4 | XFS, as on Unraid; ext4 can't hold SnapRAID parity for disks over 16 TiB |
| Sharing | NFS and SMB; FTP, WebDAV, DLNA and iSCSI cut without asking | NFS, SMB, SFTP, FTP and WebDAV. DLNA and iSCSI come later |
| Share permissions | One owner or one shared account per share | Read and write access per user and group, the same on every protocol |
| Screens | Features appear only once they apply (NFS was invisible until a pool existed) | Every screen shows every feature and its state, with the reason when it can't be used yet |
| Health | Disks, plus problems only when something fails | Every check, its result and when it ran; services, system, pools, disks and alerts |
| Alerts | Email and ntfy | In the desktop by default; email, ntfy and more services added later |
| Setup | A permanent app on the desktop | Opens once on first login, then is gone; its steps live in their own apps |
| Apps | Docker and VMs out of scope | Still out for now. A working Files app is a priority |

## Overview

FellowshipNAS is the "NAS / Home Server" role in FellowshipOS. It replaces TrueNAS for a home setup, without TrueNAS's apps, VMs, clustering and middleware. It offers:
- storage pools on the engine of your choice
- shares over the common protocols
- automatic snapshots where the engine supports them
- health checks and alerts
- a desktop in the browser

**In scope**

- Install FellowshipOS to disk from the ISO
- One wizard option that turns an installed system into a NAS
- Several pools at once, each on its own engine: ZFS, Unraid-style (mergerfs + SnapRAID), traditional RAID (mdadm), or Btrfs
- Any machine and any number of drives, from an old PC with one data disk to a 16-bay server behind an HBA. Nothing needs specific hardware; extras such as bay numbers and locate lights appear when the hardware supports them
- Shares over SMB, NFS, SFTP, FTP and WebDAV, with permissions per user and group
- Users and groups managed from the desktop
- Snapshots (ZFS and Btrfs), parity syncs (SnapRAID), scrubs and RAID checks, SMART monitoring, health checks and alerts
- Scheduled backup from one pool to another
- A desktop in the browser, served by our own web server: windows, taskbar, app launcher, notification centre, and a real file manager

**Later**

- DLNA, so TVs and consoles find media by themselves
- iSCSI, so a slice of a pool appears as a disk on another machine
- More alert services (Apprise covers 100+, such as Discord, Telegram, Pushover and Home Assistant)

**Not now**

- Docker, an app centre, VMs, photo and media apps (Immich, Jellyfin)
- Kubernetes, off-site replication, clustering, directory services
- The NAS role in the LXC template (a container can't own disks or load ZFS)
- Btrfs RAID5/6, which is still not considered safe
- Native mobile apps (the desktop works in a phone browser instead)
- A terminal in the browser (use SSH)

**Design direction:** the feel of Synology DSM and FygoOS: a desktop in the browser with apps in windows, served by the NAS's own web server. It uses FellowshipOS's own name, icons, colours and layout; nothing is copied from either product.

**Guiding rules**

1. **Debian underneath:** layer on top of Debian, never fork it, so Debian keeps supplying security updates.
2. **Proven parts below, our own code above:** use proven packages for storage and sharing (ZFS, mdadm, mergerfs, SnapRAID, Btrfs, Samba, NFS, OpenSSH, vsftpd). Write our own code for everything the user sees and for the path from browser to system.
3. **Nothing destroys data silently.** Anything that can destroy data happens behind a confirmation. The only disk the installer may touch is the OS disk, chosen by hand.
4. **The person using the NAS makes the choices:** which engine, which layout, which disks. The system explains each option; it doesn't choose for them.
5. **It runs on whatever machine it is put in.** The system finds out what the hardware offers and only enables options that work there: layouts the disk count allows, bay numbers where the enclosure reports them, a serial number and an activity-light blink where it doesn't.
6. **Show everything, hide nothing** (Chris, Nov 9).
   - Every screen lists all of its features and their state, including ones that can't be used yet, with the reason: "No pool yet", "Not built yet", "ZFS unavailable: Secure Boot is on".
   - Controls are disabled, never removed.
   - Labels say what you get in plain words, with the technical name in small print.
7. **Chris's reference list is the scope.** Anything from his FygoOS list, or anything he has asked for, is cut or postponed only after he agrees.

## System architecture

```
Browser ──HTTPS──► fellowship-nas-web ──► fnas ──► ZFS / mdadm / mergerfs + SnapRAID / Btrfs
                        │                   └─────► Samba, NFS, OpenSSH (SFTP), vsftpd (FTP),
                        │                           Sanoid, btrbk, smartd, zed, mdadm monitor, alerts
                        └──► file helper, run as the logged-in user (Files app and WebDAV)
SMB, NFS, SFTP and FTP clients ──────────────────► their servers
```

1. **Clients:** browsers, WebDAV clients, and SMB, NFS, SFTP and FTP clients
2. **Web:** the FellowshipNAS Desktop (TypeScript, Preact) served by `fellowship-nas-web` (Go), which also serves WebDAV
3. **Helper CLI:** `fnas`, the only code that changes storage, shares, users or system settings. Every argument is checked, because it is the privilege boundary
4. **Services:** Samba, NFS, OpenSSH, vsftpd, the snapshot, sync, scrub and RAID-check schedules, `smartd`, `zed`, `mdadm --monitor`, alerts
5. **Storage:** the engines, on Debian Trixie

SMB, NFS, SFTP and FTP clients talk straight to their servers. The browser path always goes through `fnas`, so all risky logic sits in one testable place, used the same way from the desktop and over SSH.

## Install and first boot

FellowshipOS is an OS with many roles: base, Docker node, server, monitor, storage, NAS, and later a full desktop. It is built as an ISO and as an LXC template, and later perhaps a VM image. The NAS is one of those roles. **Nothing at install forces storage setup:** pools and RAID are always optional and set up later from the NAS desktop.

**The role decides the disk layout.** The ISO boots into a text menu that asks what the machine is for, and each choice takes its own path:

| Choice | Installer | Disk layout |
| --- | --- | --- |
| Base, Docker node, server, monitor, storage | Today's Debian text installer | The whole disk for the system; Docker's data lives there too, which suits a 16–32 GB VM disk |
| NAS | A graphical FellowshipOS installer | The system on LVM, sized from the disk and the projected install; the rest left free for a data pool, set up later from the desktop |
| Desktop (later) | To be decided with the desktop role | The whole disk for the system and home folders |

The choice carries through: the first-boot wizard starts with that role selected, and it can still be changed there.

**The NAS installer** is a web page in the desktop's look, shown full screen on the machine's own monitor by a small built-in browser. The same installer is reachable from another computer's browser at an address the text menu prints, for a NAS with no monitor. The built-in display and browser add roughly 150–250 MB to the ISO and aren't installed on the target.

- **Smart suggestions, shown as adjustable sliders.** The system's size is worked out from:
  - the projected install: base, NAS packages and ZFS builds
  - room for updates, and for old kernels kept for rollback
  - logs

  That comes to about 16 GB on a 64 GB disk, 32 GB on 500 GB, and 48 GB on 4 TB and more. Disks under about 64 GB give the whole disk to the system, since a split makes no sense there.
- **Layout:** three parts:
  - an EFI partition
  - an LVM area for the system, with a little free space so it can grow later from Settings
  - a plain partition with the rest, for data

  The data part is a plain partition because ZFS works best outside LVM and a ZFS pool can't shrink later. That is why the suggestion matters at install time.
- **Other disks are never touched.** Only the disk chosen for the system is written to, and it is named by model, size and serial. Disks holding old data are shown as such and left alone.
- **Partitioning is our own code** on this path, so its gate covers every disk size and disks with old data on them.

**Limit:** a one-disk machine installed as, say, a Docker node gives the system the whole disk. Making it a NAS later needs a second disk for data, or a reinstall.

**Debian installer (non-NAS roles):** `--debian-installer live` in `auto/config` makes the installer copy the live system to disk.
- Partitioning stays manual and is never preseeded, because the data disks are attached.
- With data disks present, the installer's default partitioning method becomes "largest continuous free space", so the person must pick "use entire disk" and then the OS disk.
- The docs should recommend unplugging data disks for a first install if the OS disk isn't obvious.

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
3. Installs `fellowship-nas`, `fellowship-nas-smb` and `fellowship-nas-web`, which bring every engine's and every protocol's tools.
4. Builds and loads the ZFS module. If Secure Boot is on or the module fails, the role still completes: ZFS is marked unavailable with the reason, and the other engines work.
5. Adds the wizard's user to `nas-admins` and writes `/etc/fellowshipos/.nas-role-complete`.

The flavor is safe to run twice; if the marker is missing, the wizard shows the retry command, `sudo homelabos-flavor nas-node`. At the end, the wizard prints the desktop's address, `https://ADDRESS`.

**Packages:**
- `fellowship-nas`: a meta-package with every engine's and protocol's dependencies.
- `fellowship-nas-smb`: Samba and discovery.
- `fellowship-nas-tools`: `fnas` (a short command over the `fellowship_nas` Python package, with one module per engine).
- `fellowship-nas-web`: the server and the desktop.

For v1 they ship as a local apt repo inside the ISO, with dependencies from Debian during the role install. A hosted, signed repo arrives in the release phase.

## Storage

**One idea for every engine:** a pool is a named set of disks mounted at `/mnt/NAME`, with shares inside it and a config folder that remembers its settings. The Storage app shows every pool the same way and says plainly what each engine can and can't do. Different pools can use different engines on the same NAS. For example: a ZFS pool for family files, an Unraid-style pool for films, a RAID 6 array for a scratch library, and a Btrfs mirror of two SSDs.

**Creating a pool** always starts with the engine.

1. **Engine:** the four engines are listed side by side, with the comparison below. One that can't be used here says why ("Not built yet", "Needs at least 2 disks", "ZFS unavailable: Secure Boot is on") and stays on screen, disabled.
2. **Disks:** every disk is listed, and those that can't be used say why ("Holds the system", "Part of pool tank", "Has old data: tick to erase").
3. **Layout:** only that engine's layouts that make sense for the chosen disks, labelled in plain words.

   | Plain label | Small print |
   | --- | --- |
   | "Striped with parity · 17 GB usable · any 1 disk can fail" | RAIDZ1 |
   | "Two copies of everything · 8.6 GB usable · 1 disk can fail" | Mirror |
   | "Data disks + 1 parity disk · 17 GB usable · 1 disk can fail, files since the last sync excepted" | SnapRAID |

   Layouts nobody would pick, such as a 3-disk mirror, aren't offered.
4. **Name and confirm:** the pool is named, and a typed confirmation is needed for disks with old data.

**Choosing an engine** (this comparison is shown on the first step)

| | ZFS | Unraid-style (mergerfs + SnapRAID) | Traditional RAID (mdadm) | Btrfs |
| --- | --- | --- | --- | --- |
| Best for | Important files, photos, documents | Large media libraries on mixed disks | Simple, fast arrays any Linux machine can read | Small mirrors, SSDs, mixed sizes |
| Redundancy | Mirrors, RAIDZ1/2/3, hot spares | 1–6 parity disks | RAID 1, 5, 6, 10, hot spares | Mirror (`raid1`), three copies (`raid1c3`), `raid10` |
| Filesystem | ZFS itself | XFS on each disk | ext4 or XFS, chosen after the layout | Btrfs itself |
| Mixed disk sizes | Wastes space | Works | Wastes space | Works |
| Add one disk later | To a RAIDZ group (RAIDZ expansion) or as a new group | Yes | Yes (reshape, then grow the filesystem) | Yes |
| Checksums and self-repair | Yes, live | At sync and scrub time only | No; a monthly check finds mismatches but can't tell which copy is right | Yes, live |
| Protected immediately | Yes | Only after the next sync (nightly by default) | Yes | Yes |
| Snapshots and Windows "Previous Versions" | Yes (Sanoid) | No | No | Yes (btrbk) |
| More failed disks than it survives | The pool is lost | Only the failed disks' files are lost; every other disk stays readable on its own | The array is lost | The pool is lost |
| Disks spin down when idle | No | Yes | No | No |
| Secure Boot | Must be off | Works | Works | Works |
| Imports an existing pool | TrueNAS pools | Disks from another mergerfs + SnapRAID setup | mdadm arrays from another Linux machine | Btrfs filesystems |

**Disk handling**

- Pools are always built from `/dev/disk/by-id` paths, so device names can change.
- **The system disk can hold data too.** The NAS installer sizes the system to fit the disk and leaves a data partition (see Install and first boot). Storage offers it as "the rest of the system disk", so a machine with one disk is a working NAS with a single-disk pool. It is shown without redundancy, and Health suggests adding a disk to mirror it.
- **Safety:** `fnas` never offers the system partitions themselves and refuses disks in use. Disks with old data need `--wipe` plus a typed confirmation ("erase 6 disks").
- **Drive bays:** where the HBA and backplane report enclosure slots (SES, through `/sys/class/enclosure`), disks are shown and alerted by bay ("Bay 7, serial ZL2…"). The locate light is blinked with `ledctl` or `sg_ses`. Without enclosure reporting, disks are identified by serial number, and "Identify" reads from the disk so its activity light flickers.
- **Controllers:** onboard SATA, NVMe, USB enclosures and HBAs all work.
  - An HBA must run in IT (pass-through) mode so each disk is visible on its own with its SMART data; a RAID card that hides the disks is reported as such.
  - USB disks are offered with a warning, since many USB bridges drop out under load or hide SMART data.
- **Spin-down:** Unraid-style data disks may spin down when idle (`hd-idle`). The other engines touch every disk on every write, so their disks don't.

**ZFS** (built in Phases 1–3 and 7)

- Layouts: single, mirror, RAIDZ1, RAIDZ2, RAIDZ3, and pools of several groups ("two 6-disk RAIDZ2 groups"). Hot spares. Growing a pool by adding a group or expanding a RAIDZ group by one disk.
- Defaults: `ashift=12`, `compression=lz4`, `atime=off`, `xattr=sa`, `acltype=posixacl`, `dnodesize=auto`.
- Shares are datasets in `POOL/shares`; settings are saved in `POOL/fellowship-config`.
- Snapshots by Sanoid, monthly scrub from Debian's `zfsutils-linux` job, events from `zed`.
- **Kernel and ZFS updates:** there's no version pin.
  - `zfs-kernel-check` runs after kernel and header installs. With no module for the new kernel, it keeps GRUB on the running kernel, and `fnas check` raises an alert.
  - `unattended-upgrades` never reboots by itself.

**As built in Phase 7 (ZFS)**

- **Layouts for any number of disks:** `fnas pool layouts` lists every layout the chosen disks fit, and the desktop shows the best four first.
  - The recommendation is single for one disk, a mirror for two, RAIDZ1 for three, and one RAIDZ2 group for four to ten.
  - Beyond ten, it is RAIDZ2 groups of 6 to 10, with one or two spares when the count doesn't divide evenly (15 disks: two groups of 7 and a spare).
  - Revision 4 removes 3-disk mirrors from the offers and relabels the rest in plain words.
- **Mixed sizes:** similar sizes go in the same group, and the largest disks become the spares, since a spare only stands in for a disk no bigger than itself.
  - ZFS wants `-f` for a group of disks of different sizes. `fnas` passes it only when that is ZFS's one objection, after the layout has shown the space lost.
  - Everything else ZFS refuses stays refused, and old data still needs `--wipe`.
- **Growing:** a pool can grow in four ways:
  - another group like the existing ones
  - one disk added to a RAIDZ group (RAIDZ expansion, OpenZFS 2.3)
  - a mirror for a disk on its own
  - hot spares, which must be at least as large as the pool's largest disk

  A second disk on its own is refused, because it would leave nothing able to survive a failure.
- **Replacing:** the Storage app shows each failed disk on one row, with the spare standing in for it or the disk replacing it. `fnas pool replace` starts the resilver and follows it as a job; when it finishes, the spare goes back to waiting.
- **Where pools mount:** every pool mounts at `/mnt/NAME`, so no pool name can collide with a folder the system uses, such as `/home` (Chris, Nov 8).
  - New pools are created with that mountpoint.
  - Importing a pool that still uses ZFS's default, `/NAME`, sets its mountpoint to `/mnt/NAME`.
  - A read-only import changes nothing on the pool, and uses `/mnt` as its root for as long as it stays imported.
  - A pool moved back to TrueNAS, which adds `/mnt` itself, needs `zfs inherit mountpoint NAME` first.
- **Long jobs:** a resilver, scrub, expansion or backup started from `fnas` runs as its own systemd unit (`fellowship-job-ID`) and records its progress in `/var/lib/fellowship-nas/jobs`.
  - The desktop follows jobs over server-sent events, and they finish even if the browser closes or the web server restarts.
  - A job whose unit vanished is marked as stopped.
  - ZED already reports finished scrubs and resilvers, so jobs only alert on failure or a finished expansion.
  - Every engine's long jobs (reshapes, rebuilds, syncs, scrubs) use the same mechanism.
- **Bays and identify:** disks in an enclosure that reports slots (SES) are shown by bay, in the desktop and in alerts. Identify writes the slot's `locate` file; without SES it reads from the disk for a minute so its activity light gives it away.
- **Degraded pools:** the hourly check names each missing or failing disk by bay or by-id name.

**Traditional RAID (mdadm)** (new in revision 4; from Chris's FygoOS list)

- **Levels:** RAID 1 (2–4 disks), RAID 5 (3–8), RAID 6 (4–16) and RAID 10 (4 or more, an even number), plus hot spares.
- **RAID 5 warning:** on disks of 8 TB and more, RAID 5 is offered with a warning that a second failure during the long rebuild loses the array. RAID 6 is recommended there.
- **The two steps Chris expects:** build the array, then choose its filesystem: ext4 or XFS, with XFS recommended.
- **Defaults:**
  - an internal write-intent bitmap, so a crash only resyncs what changed
  - disks partitioned first, so a replacement only needs to be the same size or larger
  - the filesystem aligned to the array's stripe
- **Boot and alerts:** the array is recorded in `/etc/mdadm/mdadm.conf` and the initramfs, and mounted by filesystem UUID through a systemd mount unit. `mdadm --monitor` sends alerts through `fellowship-nas-notify`.
- **Checks:** Debian's monthly `checkarray` runs on every array, and its mismatch count is shown in Health.
- **Shares and settings:** shares are folders in `/mnt/NAME/shares`; settings in `/mnt/NAME/.fellowship`.
- **No snapshots:** shares show "No snapshots on this engine".
- **Growing:** one disk at a time. The reshape runs as a job; the filesystem is grown when it finishes.
- **Replacing:** a failed disk is marked failed, removed and replaced through the same guided flow as ZFS. The rebuild runs as a job, and a hot spare takes over by itself.
- **Importing:** an array from another Linux machine is assembled read-only first, then mounted.

**As built after Phase 10's engine work**

- **One array per pool.** An mdadm pool is a single array of every disk it has, so the
  layout list offers one group rather than ZFS's several: RAID 5 up to six disks, RAID 6
  above that, and RAID 10 with the standard two-copy layout. A mirror of two disks with
  the rest waiting as spares is offered when the disks outnumber what the layout needs.
- **Filesystem:** XFS is preselected in the CLI and ext4 sits beside it. The choice is
  recorded by the mount itself, so growing knows which tool to use.
- **Creating:** the array is made with an internal write-intent bitmap, recorded in
  `/etc/mdadm/mdadm.conf` and the initramfs, and mounted by filesystem UUID from
  `/etc/fstab`. This departs from the design above, which called for a systemd mount
  unit; `/etc/fstab` was used instead, and that is open to change.
- **Growing:** one disk at a time. The reshape runs as a job, and the filesystem is
  grown when the kernel finishes. A spare is added or removed without a job.
- **Checking and repairing:** a check writes `check` to the array's `sync_action`, runs
  as a job, and reports the blocks that do not match. Repair is a separate command with
  its own typed confirmation, because it can keep the wrong copy. When each check
  finished is kept in `/var/lib/fellowship-nas/checks.json`.
- **Health:** the monthly check is now **Monthly storage checks**. It covers every
  writable pool, not only ZFS ones, and each engine names the action in its own words:
  Scrub for a ZFS pool, Array check for an array.
- **No snapshots:** a share on an array keeps no history, so Previous Versions has
  nothing to offer, and the backup engine still covers ZFS pools only. Both stay visible
  in the desktop with that reason rather than disappearing.
- **Not yet verified:** the whole feature is tested only against a fake, with no array
  on a real disk. Creating, growing, replacing, checking and importing are all written
  from mdadm's documented behaviour. The four-disk and sixteen-disk gate below is what
  turns that into a proven feature.

**Unraid-style (mergerfs + SnapRAID)** (absorbs the old storage node)

- **Disks:** each data disk gets its own XFS filesystem, mounted at `/srv/disks/SERIAL`, and mergerfs joins them at `/mnt/NAME`.
  - XFS is used because SnapRAID keeps each parity level in one file, and ext4 caps a file at 16 TiB.
  - Parity disks are XFS too, and must be at least as large as the largest data disk.
- **Shares and settings:** shares are top-level folders in `/mnt/NAME`; settings are in `/mnt/NAME/.fellowship`, copied to every data disk so the pool can be rebuilt after a reinstall.
- **Schedules:** `fnas` writes the mergerfs mount and `snapraid.conf`. A nightly `snapraid sync` runs, then a weekly partial `snapraid scrub`. The sync is skipped with an alert if too many files were deleted, a sign of a missing disk.
- **Repairs:** a failed data disk is rebuilt onto its replacement with `snapraid fix`, through a guided flow in the Storage app.
- **Protection is visible:** the Storage app says plainly that files changed since the last sync aren't protected yet, and when the last sync ran.

**Btrfs**

- **Layouts:** single, `raid1`, `raid1c3`, `raid10`. Metadata uses at least the same redundancy as data, and RAID5/6 isn't offered.
- **Shares and settings:** shares are subvolumes in `/mnt/NAME/shares`; settings are in `/mnt/NAME/.fellowship`.
- **Snapshots:** made by `btrbk` into `/mnt/NAME/.snapshots`, named so Samba shows them as "Previous Versions".
- **Checks:** a monthly `btrfs scrub`, and `btrfs device stats` read by the hourly check.
- **Mounting:** by filesystem UUID through a systemd mount unit. A `degraded` mount only happens after a confirmed choice in the Storage app.

**Config recovery** (built in Phase 3 for ZFS; extended to every engine)
- **What's saved:** after every change, `fnas` saves the LAN subnet, users and groups with their IDs, share settings and permissions, schedules, backup jobs and alert channels to every pool, without secrets.
- **After a reinstall:** Setup's first question is whether to restore the settings found on a pool.
- **Safety:** a fresh install never overwrites a saved copy it hasn't restored, and a restore checks everything on the disk before using it.

**Migrating from TrueNAS:**
1. Export the pool from TrueNAS.
2. Import it read-only (`fnas pool import NAME --readonly`).
3. Check permissions and user IDs.
4. Only then import it normally.

Never run `zpool upgrade` during migration, because it blocks a return to TrueNAS.

## Sharing

**The Shares app always starts with "Ways to share"**: one row per protocol, shown whether or not a pool exists.

| Protocol | For | Server | Encrypted |
| --- | --- | --- | --- |
| SMB | Windows, Mac, phones | Samba, with `wsdd2` and Avahi for discovery | Yes (SMB 3) |
| NFS | Linux machines, Proxmox, other servers | `nfs-kernel-server`, NFSv4 only by default | No; LAN only |
| SFTP | File transfer over SSH: scripts, FileZilla, phones | OpenSSH's `internal-sftp` | Yes |
| FTP | Cameras, scanners, old devices | `vsftpd` | TLS (FTPS) by default; plain FTP is an opt-in for devices that need it |
| WebDAV | Mapping a drive over HTTPS, phone apps, calendars of files | `fellowship-nas-web`, through the file helper | Yes (HTTPS) |
| DLNA | TVs and consoles | Later | |
| iSCSI | A pool slice as a disk on another machine | Later | |

Each row shows whether that protocol is running, how to connect, and its own settings. SMB passwords sit under SMB, the network that can reach NFS under NFS, and plain FTP's opt-in under FTP. DLNA and iSCSI are listed as "Later".

**One owner per protocol, the same on every engine:** `fnas` keeps each share's settings in `shares.json` in its pool's config folder, and generates every protocol's configuration from it:
- `/etc/exports.d/fellowship.exports` for NFS
- one Samba file per share
- the SFTP and FTP views
- WebDAV's share list

ZFS's `sharenfs` and `sharesmb` properties aren't used.
- **Migration:** shares made before Phase 7 kept their settings on ZFS properties. `fnas share refresh` (run when the package is upgraded, after imports and after every change) moves them into `shares.json` and turns `sharenfs` off.
- **Backups:** a backup also copies the source pool's share settings to the backup pool, so a restored share comes back shared the way it was.
- **Exporting:** exporting a pool stops sharing it first, so every server lets go of it.

**Each share chooses which protocols it is on**, with one checkbox per protocol.

**Who can use a share** (Phase 9). Each share has one of two kinds of access, the same on every protocol:
- **Everyone on the home network** (today's "shared" mode): every user, and every NFS client in the allowed networks, reads and writes, and files belong to one account, `nas-share` (ID 3000). Best for media and family files.
- **Chosen users and groups** (replaces "one owner"): a list of users and groups, each with **read** or **read and write**.
  - Kept as POSIX ACLs on the share's folder, with matching default ACLs so new files inherit them.
  - Samba's `valid users`, `read list` and `write list` are written from the same list, so Windows sees the same rule.
  - NFS can only honour it when the client's user IDs match the NAS's, so ticking NFS on such a share shows that warning.
- **Existing shares migrate:** "shared" becomes everyone; "one owner" becomes that user with read and write.

**SMB settings** (Phase 9)

| Per share | What it does | Default |
| --- | --- | --- |
| Read-only | Nobody can change files over SMB | Off |
| Hidden | Not listed when browsing the NAS; still reachable by name | Off |
| Guest access | Lets people in without a password, read-only | Off |
| Recycle bin | Files deleted over SMB go to `.recycle` in the share, emptied after a chosen number of days | Off; 30 days |
| Time Machine | Mac backups to this share, with a size limit (`vfs_fruit`) | Off |
| Previous Versions | Snapshots shown in Windows (ZFS and Btrfs only) | On |

| For the whole NAS | Default |
| --- | --- |
| Workgroup | WORKGROUP |
| Description shown when browsing | FellowshipNAS |
| Oldest protocol allowed | SMB 2 (SMB 1 is never offered) |
| Encryption | When the computer supports it; can be made required |
| Discovery by Windows (`wsdd2`) and Macs (Avahi) | On |

**NFS settings** (Phase 9)

| Per share | What it does | Default |
| --- | --- | --- |
| Who can reach it | One or more networks or single addresses, each read-write or read-only. Never `*` | The home network, read-write |
| Read-only | Read-only for every client | Off |
| User mapping | Everyone as the share account (`all_squash`); real users with root mapped to nobody (`root_squash`); or real users including root (`no_root_squash`, for hypervisors and backup hosts, with a warning) | Follows the share's access: everyone uses the share account, chosen users keep their IDs with root squashed |
| Write mode | Safe (`sync`) or fast (`async`, which can lose recent writes in a power cut, with that warning) | Safe |

| For the whole NAS | Default |
| --- | --- |
| NFS version 3 for older clients and some hypervisors | Off (version 4 only) |
| Server threads | 8 |
| Fixed ports for mountd, statd and lockd, for firewalls (needed with version 3) | Off |

**Protocol details**

- **NFS:** `fnas` writes every export line from the settings above; nothing reaches `*`.
- **SMB:**
  - One Samba file per share, plus a master file that `smb.conf` includes; the NAS-wide settings go in their own included file.
  - On ZFS and Btrfs, `vfs_shadow_copy2` shows snapshots as "Previous Versions".
  - Users set one password in the Users app, and it is used for both the desktop and SMB.
- **SFTP:**
  - Members of `nas-sftp` get SFTP only, with no shell, through an `sshd` `Match Group` block with `ForceCommand internal-sftp`.
  - Each user is chrooted to a folder holding bind mounts of the shares they may use, so they see only those shares.
  - Admins keep normal SSH.
- **FTP:**
  - `vsftpd` uses the same chroot view as SFTP and requires TLS unless plain FTP is turned on.
  - Passive ports are a fixed range opened in the firewall for the LAN only.
- **WebDAV:**
  - Served at `https://ADDRESS/dav/` by our own server, through the file helper, so Linux permissions decide what each person sees, exactly as in the Files app.
  - It needs no extra package.

## Pool details and SSH (Chris, Nov 9, built between Phases 9 and 10)

- **Pool details:** Storage → a pool → **Details and history**.
  - The hourly check records each pool's usage: one point an hour for a week, and one a day for a year.
  - The view shows that history as a chart, a straight-line projection of when the pool fills (after three days of history), what uses the space (shares, snapshots, backups of other pools, the rest, and free) and compression.
  - Charts are our own SVG, coloured from a palette checked for colour blindness.
- **SSH:** per-person accounts, not one shared SSH login, which would mean a shared password and no record of who did what.
  - Only `nas-ssh` members log in (`AllowGroups`), and "Shares and SSH" accounts join it automatically. Shares-only accounts are refused before they get a shell or a tunnel.
  - Root never logs in over SSH.
  - Keys per person in Users. Passwords over SSH can be turned off once an administrator who may use SSH has a key.
  - SSH can be turned off in Settings, and shows in Health.

## Users and groups (Phase 9)

- **The Users app** lists every person's account with its groups, whether it is an admin, whether it has a password for shares, and which shares it can reach and how.
- **Adding someone:** name, full name, password twice, and what they may do:
  - **Shares only** (the default): uses shares and, later, Files; no SSH login (`/usr/sbin/nologin`).
  - **Shares and SSH**.
  - **Administrator**: also manages the NAS (`nas-admins`).
- **One password:** setting a password sets the Linux one and the Samba one together, so the desktop, SMB and later SFTP, FTP and WebDAV all take the same password. An account made before this, with no Samba password, says so and offers to set one.
- **Groups:** create, rename and remove groups, and choose their members. Shares can be given to a group.
- **Changing someone:** password, full name, groups, what they may do, and turning the account off without removing it.
- **Removing someone** asks whether to keep their home folder, names the shares they lose, and needs a typed confirmation. You can't remove yourself or the last administrator.
- **Accounts are normal Linux accounts** with IDs from 1000 up. Users, groups and their IDs are saved in the pool config, so they come back the same after a reinstall.

## Snapshots and backup

**Snapshots:** Sanoid on ZFS and btrbk on Btrfs, both generated by `fnas` from the same per-share policy (hourly 24, daily 30, monthly 6 by default). On the other engines, shares show "No snapshots on this engine" rather than hiding the option. Snapshots can be browsed and restored in the Snapshots and backup app and in Files.

**Backup:**
- **Jobs:** one or more scheduled jobs, each copying a pool's shares to another pool.
- **How it copies:** `syncoid` between ZFS pools and `btrfs send` between Btrfs pools keep snapshots. Any other pair uses `rsync`, keeping a snapshot on the target where its engine allows.
- **Where it's shown:** every job, with its last run, next run and result, in the Snapshots and backup app.
- **Alerts:** a failed or overdue backup raises one.

## Health

The Health app lists **every check, its result and when it last ran**. A check that doesn't apply yet says why ("No pool yet, so there's nothing to scrub"); it is never left out. The words "Everything looks fine" only appear when every check has run and passed.

| Section | Shows |
| --- | --- |
| Needs attention | Current problems at the top, with what to do |
| Services | SMB, NFS, SFTP, FTP and WebDAV (running, stopped, or turned off); the web server; the scheduler; `zed`, `mdadm --monitor` and `smartd` |
| System | System disk space, memory, load, temperatures, uptime, updates waiting, reboot needed, ZFS module for the next kernel |
| Pools | Per pool: state, space, and its last scrub, RAID check or sync, its last snapshot and its last backup, each with a date |
| Disks | SMART in plain words, temperature, power-on time, bay where reported |
| Alerts | Where alerts go, and when the last one was sent |

| Source | Watches |
| --- | --- |
| `smartd` | SMART errors, reallocated and pending sectors, temperature, for every disk |
| `zed` | ZFS pool degraded, checksum errors, scrub and resilver results |
| `mdadm --monitor` | Arrays degraded, failed disks, spares taking over, rebuilds finished |
| Hourly check (`fnas check`) | Pools over 80% full, missing or failed disks on any engine, RAID check mismatches, Btrfs device errors, SnapRAID sync or scrub overdue or failed, snapshots stopped, scrub too old, backup overdue, services stopped, system disk nearly full, ZFS module missing for a kernel, unrestored settings |

**As built after Phase 9**

- **The overview leads with a verdict:** a banner says either what needs attention, or that nothing does, with how many checks passed and when the screen was last worked out.
- **Two groups of cards follow:** System holds CPU usage, memory and CPU load; Storage & services holds the pools, the disks and the services. Each card carries a small icon, its reading, and a **View details** link into the full section.
- **A strip along the bottom** repeats the backup's last result and whether any alert is active, each opening its section.
- **The desktop's Health widget** is the same panel in miniature: the worst problem in a coloured medallion with its level written out, CPU and RAM, and a fill bar with a health label for each pool. Clicking anywhere on it opens Health.
- **The desktop's Disks widget** is a drive bay map: one numbered slot per drive, coloured by its level, with a check, an exclamation mark or a question mark. A line beneath names the worst drive ("Bay 3 · Medium warning"), and clicking a slot opens Health at that drive's row.
- **Severity has one scale everywhere:** green is good, yellow low, orange medium, red high and purple critical, shared by the medallion and the banner.
- **The system is recorded too:** the hourly check adds memory, load, temperature and the system disk to the same history file as each pool's usage. The CPU load card therefore draws the last day of points against the number of processors, and System and Pools draw a Week or Year chart with a hover readout.
- **An empty chart says why:** a new install shows that the chart fills in from the first checks, rather than looking broken. Two points are the minimum; the fill estimate still needs three days.
- **Where it lives:** `/var/lib/fellowship-nas/usage.json`, an hour of points for a week and one a day for 400 days. The system's history sits under a key no pool can take, so a pool named `system` is unaffected.

## Alerts

**Levels** (Chris, Nov 9): **critical** means something is broken now. **High**, **medium** and **low** are warnings, by how soon they need attention. **Info** means nothing is wrong, such as a resolved problem or a finished job.

| Level | Examples |
| --- | --- |
| Critical | A pool failed or lost a disk; a disk failing; SMB or NFS down while shares use it; a NAS service stopped; snapshots failing or stopped; a backup failed; a long job failed |
| High | A pool 90% full; the system disk 95% full; no ZFS module for the next kernel (a restart would leave ZFS pools unmounted); repeated wrong passwords |
| Medium | A pool 80% full; the system disk 85% full; a backup overdue; a disk to keep an eye on |
| Low | A pool not scrubbed in 40 days; settings from another install not restored |

On ntfy, critical is sent as urgent, high as high, medium as default, and low and info as low. Email subjects start with the level.

- **The default needs no setup:** every alert goes to the desktop's notification centre and the Alerts app's history.
- **Extra channels go on top:** email (`msmtp`) and ntfy, as built in Phase 3, each with a test button.
- **Later:** more services through Apprise, routing by severity, and several channels of each kind.
- **Delivery:** undelivered alerts are retried for a day, and the history shows whether each one got through.

**As built in Phase 3 (ZFS)**

- Sanoid's daily and monthly runs are moved to 23:30 and 00:15 UTC, so hourly, daily and monthly snapshots don't share a timestamp. The very first run still makes all three at once.
- `smartd` runs inside VMs too (Debian's unit skips them), with its own config that calls `fellowship-nas-notify` directly.
- After a reinstall, `fnas pool import --force` imports a pool the old system never exported, but never one another running system has imported (ZFS multihost). One restore takes over every pool the old install saved to.

Not checked in the VM: `smartd` reacting to a real disk fault (the alert hook was tested directly), and the snapshot timing across a day and month boundary.

## Web server: `fellowship-nas-web`

Our own server, written in Go, replaces Cockpit entirely. It serves the desktop, a JSON API and WebDAV, and it never changes the system itself: every change goes through `fnas`.

**Three processes, so the part facing the network has no special rights** (the same split OpenSSH uses):

| Process | Runs as | Does |
| --- | --- | --- |
| Web front | Unprivileged `fnas-web` account | HTTPS, the desktop's files, sessions, the API |
| Privileged helper | root, reachable only through a local socket | Checks passwords with PAM, reports a user's groups, runs `fnas` for members of `nas-admins`, starts file helpers |
| File helper | The logged-in user | Every Files app and WebDAV operation, so Linux permissions decide what each person sees |

**Login and sessions**

- Normal Linux accounts, checked with PAM; no separate user database.
- A random session token in a `Secure`, `HttpOnly`, `SameSite=Strict` cookie, kept server-side. Sessions end after 30 minutes idle and 12 hours in total; long transfers count as activity.
- Every API request that changes something must carry a matching `Origin` and a per-session token header, against cross-site attacks.
- Failed logins slow down per address and per account, and are logged; repeated failures raise an alert. WebDAV logins follow the same rules.
- The desktop is HTTPS only: port 443, with port 80 redirecting. A self-signed certificate is made at first start, and the Settings app accepts your own.

**API:** one endpoint per `fnas` action, with arguments passed as a list (never through a shell).
- **Long jobs:** reshapes, rebuilds, scrubs, resilvers, backups and SnapRAID syncs report progress over server-sent events. `/api/jobs/events` sends the job list whenever it changes.
- **Sessions:** following progress doesn't count as activity, so an idle session still ends.
- **Who runs jobs:** only `fnas` itself runs a job; the helper lets people list and follow them.

**Files:** served by the file helper.
- Full `Range` support, for video seeking.
- File sizes sent up front, for download progress.
- Uploads are chunked and resumable; folders download as a streamed zip.

**Built with:**
- Go's standard library for HTTPS, routing and streaming.
- `golang.org/x/net/webdav` for WebDAV.
- A small `cgo` binding to `libpam` written here.
- No web framework. It is built in the same container as the ISO, with Debian's Go.

**Phase 4's transfer test** (Cockpit 337, in a KVM VM) set targets for our server.
- **What Cockpit managed:** 10 GB upload at 85 MiB/s and download at 170 MiB/s, 5,000 thumbnails made in 17 s and shown in 5 s, and folders zipped while streaming.
- **What it failed:** video seeking (no `Range` support) and download progress.

Our server must match those speeds and pass the two Cockpit failed.

## Desktop

The desktop is the interface from the start. Every app opens in a window; there are no standalone pages.

- **Login screen** with FellowshipOS branding.
- **Desktop:** wallpaper, app icons, and widgets: pool health and capacity, disk temperatures, the last scrub, sync, snapshot and backup, and recent alerts.
- **Windows:** move, resize, minimise, maximise, stack, several open at once; layout remembered per user.
- **Taskbar:** open windows, launcher, clock, notification centre.
- **Phone:** windows open full-screen and the taskbar becomes a bottom bar.
- **Built with:** TypeScript, Preact 10 and esbuild (chosen in Phase 4).
- **Non-admins** see only Files and their own settings.
- **Look (chosen by Chris):** a dark forest palette, with the fantasy in the atmosphere only.
  - Controls stay clean and readable: solid panels, softly rounded windows, crisp icons, gold used sparingly.
  - The theme follows the device; the light theme is the same palette in daylight.

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

  - **Wallpapers:** Chris chose them, made in Canva: a dusty walking path across an open field toward a forest and mountains, stormy for the dark theme and sunny for the light one.
  - **Fallback:** a drawn misty mountain scene, generated at build time, shows only if a photo fails to load.
  - **Wordmark:** a restrained serif for FellowshipOS.
  - Nothing is copied from DSM or FygoOS.

**Setup runs once.**
- **First login:** the first time an admin logs in to a NAS that hasn't been set up, Setup opens by itself. It covers:
  - restoring saved settings, if any are found on a pool
  - system space
  - the first pool
  - the LAN subnet
  - users
  - the first share
  - backup

  Each step can be skipped, and so can the whole of Setup ("Skip for now").
- **Afterwards:** once finished or skipped, Setup leaves the desktop and the launcher. The NAS remembers this in its config, so a reboot or another browser doesn't bring it back.
- **Running it again:** Settings has "Run setup again".

**Apps**

| App | What it does |
| --- | --- |
| Storage | Pools on every engine; disks by bay; create (engine, then disks, then layout); grow; scrub, check or sync; guided disk replacement; identify light |
| Shares | "Ways to share" (every protocol, its state and settings); shares on any pool with a checkbox per protocol; permissions per user and group; the LAN subnet |
| Users | Accounts, passwords, groups, `nas-admins`, and the shares each person can reach |
| Snapshots and backup | Snapshot schedules per share; browse and restore; backup jobs with last and next run |
| Health | Every check with its result and time; services; system; pools; disks; alerts (see Health) |
| Alerts | The notification history, and email and ntfy with test buttons |
| Files | The file manager (below) |
| Settings | Hostname and time zone; network (DHCP or a static address, DNS, gateway); certificate; updates; system space; restart and shut down; active sessions; Run setup again |

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

- **Two roles:** `nas-admins` manage storage, shares, users and settings. Everyone else sees Files for folders they already have permission to.
- **Privileges:** only the privileged helper runs as root. It runs `fnas` for `nas-admins` members and nothing else, and `fnas` checks every argument.
- **Network:** `nftables` allows HTTPS, NFS, SMB, SSH/SFTP and FTP (with its passive range) from the LAN subnet only.
- **Protocols:** SFTP-only users get no shell. FTP requires TLS unless plain FTP is turned on, and the FTP row says plainly that passwords then travel unencrypted.
- **Remote access:** not built in; a VPN such as WireGuard or Tailscale is documented.
- **Destructive actions** (destroy a pool, delete a share, roll back a snapshot, wipe disks, remove a user) need a typed confirmation that names what will be lost.
- **Updates:** `unattended-upgrades` for Debian security updates, including the kernel, with no automatic reboot.
- **Review:** the web server's login, session, WebDAV and helper code gets a security review before release.

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
    fellowship-nas/          meta-package: every engine's and protocol's tools, schedules, kernel hook, alert hooks
    fellowship-nas-smb/      Samba and discovery
    fellowship-nas-tools/    fnas, its fellowship_nas package (engines/ holds one module per engine), and fellowship-nas-notify
    fellowship-nas-web/      Go server and helpers, plus the desktop (web/)
  theme/                     icons, wallpaper, colour tokens
  tests/
    vm/                      vm.sh: install from the ISO's boot menu, with up to 16 virtual disks
    unit/                    fnas tests that never touch a real disk
```

`fellowship-nas-web` replaces Phase 4's Cockpit package, `fellowship-desktop`. All FellowshipNAS packages share one version number.

## Build phases and test checklist

No phase starts until the previous gate passes in a VM. Each storage gate runs on several machines: the system disk alone, one data disk, two disks, four disks, sixteen disks, and disks of mixed sizes, so nothing only works on a big box. Every gate also checks rule 6: Chris can see from the screens alone what exists, what doesn't, and why.

| Phase | Work | Gate | Status |
| --- | --- | --- | --- |
| 0. Base install | Debian live installer, wizard changes, `nas-node` flavor | Install from ISO, reboot, and settings persist; LXC template still builds | Done |
| 1. ZFS storage | ZFS packages, kernel hook, `fnas` pool commands, pool import | Create and import a pool from the CLI; real TrueNAS pool imports read-only | Done, except the TrueNAS import |
| 2. Sharing | NFS, SMB, user ID modes | Mount from Linux and Windows; "Previous Versions" works | Done, except a real Windows client |
| 3. Protection | Snapshots, backup, health, alerts, config copy | Failure tests pass from the CLI; a reinstall restores config | Done |
| 4. Cockpit pages | Transfer test; Setup, Storage, Shares and Health on Cockpit | Superseded by revision 3 | Code carries over to Phase 5 |
| 5. Web server and desktop | `fellowship-nas-web` with login, sessions, the helper split and the API; the desktop shell; Setup, Storage, Shares, Health, Alerts and Settings in windows; Cockpit removed | Full setup from the desktop in a browser; web security tests pass; non-admins can't reach admin apps or the API; no Cockpit installed | Done |
| 6. Installer | Boot menu that asks for the role; the graphical NAS installer with smart, adjustable sizes; the chosen role carried into the first-boot wizard | Install a NAS on 32 GB, 500 GB and 4 TB disks, and with old data on other disks: suggestions fit each, only the chosen disk is written, the system boots, and the free part becomes a pool from the desktop | Done |
| 7. Any number of drives and ZFS depth | System growth from Settings, long-job progress, drive bays and identify, multi-group pools, RAIDZ3, spares, growing, guided replacement, share settings in pool config, NFS through `exports.d`, the engine layer in `fnas`, pools at `/mnt/NAME` | Layouts that fit on 0, 1, 2, 4 and 16 disks; on sixteen, a two-group RAIDZ2 pool with a spare survives a failed disk replaced from the desktop | Done. Bay lights tested against a fake sysfs only |
| 8. Show everything | The fixes from Chris's review of the existing apps: engine choice first in Create pool (only ZFS usable, the others "Not built yet"); plain-word layout labels without nonsense layouts; "Ways to share" on Shares; Health rewritten around every check; Setup once, then Settings; Snapshots and backup app holding the backup jobs | With no pool, every app still shows every feature and why it can't be used yet; Setup appears on first login only; Health lists each check with a time | Done. On a fresh VM install: Setup opened on first login, stayed gone after Skip and a new login, and came back from Settings. Health caught a stopped Samba with a critical alert, then a Resolved one. The gate found and fixed a disks widget that said "all healthy" with no SMART data |
| 9. Users and sharing settings | The Users app (accounts, shares-only accounts, groups, one password); share access by users and groups; every SMB and NFS setting listed under Sharing; existing shares migrated | From the desktop alone: add two shares-only users and a group; give one read and the other read and write; over SMB each gets exactly that and a guest gets nothing; a read-only NFS network can't write; recycle bin and Time Machine settings pass `testparm`; settings survive a reinstall and restore | Done. From the desktop: added Sam and Kim (shares only) and the family group, made docs with Sam read and family read and write, turned on guests and the recycle bin for media, and set the NAS-wide SMB and NFS settings. Over SMB, Sam read but couldn't write, Kim wrote through family, guests read media only, and deletes went to `.recycle`. Over NFS a read-only client couldn't write. After reinstalling the system disk, importing and restoring from the desktop brought back the accounts with their IDs, the group, the share access and both protocols' settings; passwords are set again in Users. The gate found and fixed: lockd and statd ignoring fixed ports, restored accounts shown as turned off, and guests silently shut out by required encryption (now refused with the reason). NFS access per user and the Time Machine setting were checked from the server side only (exports and `testparm`), with no Linux or Mac client |
| 10. Traditional RAID | The mdadm engine: RAID 1, 5, 6, 10, spares, ext4 or XFS, monitor alerts, monthly check, grow by one disk, guided replacement, import | Build RAID 5 and RAID 6 from the desktop on four and sixteen disks; fail a disk, replace it, and the data checks out; reboot and the array returns; import an array made on another machine | Engine, CLI and Health are built. Create pool, grow, replace, spare, export, import, status and check all work through `fnas`. Outstanding: the desktop screens for arrays, `mdadm --monitor` alerts wired into notifications, the monthly check schedule, and the gate itself, which has not run on real disks. |
| 11. Unraid-style | mergerfs + SnapRAID on XFS: sync and scrub schedules, `snapraid fix` flow, spin-down, absorbing the old storage node | Create with mixed disk sizes, share, sync; fail a data disk and rebuild it; pull more disks than the parity and the other disks stay readable; alert for a missed sync | |
| 12. More ways to share | SFTP, FTP (FTPS by default) and WebDAV, using Phase 9's users and access lists | Two users with different access see only their shares over SFTP, FTP and WebDAV; plain FTP stays off until turned on | |
| 13. Files | The Files app, snapshot browse and restore | Restore a file from a snapshot in Files; 10 GB transfers and video seeking; usable on a phone | |
| 14. Btrfs | Engine, btrbk snapshots, Previous Versions, scrub, degraded mount | Create a mirror, restore a Previous Version from Windows, fail a disk and replace it | |
| 15. Release | Network and updates in Settings, theme and icons, hosted signed apt repo | Set a static address from the desktop; updates arrive from the hosted repo | |

**Failure tests (run in the VM before every release)**

Install and role:

- [ ] Install to disk from the ISO with data disks attached, reboot: wizard runs once and settings persist
- [ ] Boot the ISO live: the first-boot wizard does not run
- [ ] Cut the network during the role install: failure is shown, and the retry command completes it
- [ ] Enable Secure Boot: role completes, ZFS shows as unavailable with the reason, the other engines work
- [ ] Try the role in the LXC template: role stops with a clear message

Pools, on every engine:

- [ ] Reinstall the OS with pools attached: the desktop offers import and restore, and no disk is offered for erasing by default
- [ ] Reorder the disks: every pool still mounts
- [ ] Pull a disk: the pool shows degraded, the alert names the bay, and the identify light finds it
- [ ] Replace the disk through the guided flow: the pool returns to healthy
- [ ] Pull more disks than the layout survives: the desktop reports it clearly and nothing is destroyed automatically
- [ ] Fill a pool past 80%: a warning alert arrives
- [ ] SnapRAID: delete many files, then run the nightly job: sync is skipped and an alert arrives
- [ ] mdadm: power off during a reshape: the array comes back and the reshape carries on

Updates:

- [ ] Reboot after a kernel update: pools and arrays come back, and shares with them
- [ ] Force a ZFS module build failure: alert arrives, and the system boots the last good kernel with its pools

Data recovery:

- [ ] Restore a deleted file from a snapshot in Files and through Windows Previous Versions (ZFS and Btrfs)
- [ ] Restore a share from a backup job, between matching and mismatched engines
- [ ] Restore config after a reinstall: shares, permissions, schedules and user IDs return

Web and access:

- [ ] Log in as a non-admin: admin apps are hidden and their API calls are refused
- [ ] A request from another site, or without the session token, is refused
- [ ] Repeated wrong passwords slow down and raise an alert, on the desktop and on WebDAV
- [ ] An idle session ends; a long upload keeps it alive
- [ ] Mount from an NFS client with a mismatched user ID: a clear permission result
- [ ] Write to a shared folder over NFS and SMB: each protocol can edit the other's files
- [ ] An SFTP-only user can't get a shell and sees only their shares

## Open questions and risks

**Answered by Chris**

- [x] Traditional RAID offers ext4 and XFS, with XFS recommended (Chris, Nov 9)
- [x] FTP uses TLS by default, with plain FTP as an opt-in (Chris, Nov 9)
- [x] Phase order (Chris, Nov 9): the screen fixes, then users and sharing settings, traditional RAID, Unraid-style, more ways to share, Files, and Btrfs last

**Still to confirm**

- [ ] Does the TrueNAS pool import read-only on Debian's ZFS?
- [ ] Traditional RAID mounts its array from `/etc/fstab`, not from the systemd mount
      unit this plan called for. Keep `/etc/fstab`, or switch to a unit so the mount and
      its options live with the other unit files?
- [ ] On Chris's own server: does the HBA run in IT mode, and does the backplane report bays (SES)? This only decides whether that box gets bay numbers; nothing depends on it
- [ ] Where will the signed apt repo be hosted?

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Wrong disk chosen in the installer | Data loss | Partitioning is never preseeded; docs recommend unplugging data disks |
| Existing pool or array mistaken for empty disks | Data loss | Pool and array scan runs first; disks with labels or filesystems are flagged and need `--wipe` |
| A security hole in our own web server or WebDAV | Someone on the LAN gets root or another user's files | Privilege split, `fnas` as the only root door, file helper as the user, LAN-only firewall, tests for every login and session rule, security review before release |
| Four engines and five protocols multiply the work | Phases take longer | One engine layer and one share-settings file in `fnas`; each engine and each protocol has its own gate |
| RAID 5 on large disks | A second failure during a rebuild loses the array | Warning on 8 TB and larger disks; RAID 6 recommended |
| mdadm can't tell which copy is right on a mismatch | Silent corruption goes unrepaired | Monthly check shown in Health; the comparison table says so before the engine is chosen |
| SnapRAID's unsynced window | Recent files lost with a disk | Nightly sync, the last sync shown in Storage, overdue alerts |
| ZFS module fails after a kernel update | ZFS pools not mounted after reboot | Post-install check, last good kernel as default, alert |
| Bug in pool or share scripts | Data loss | All destructive logic in `fnas`, tested in VMs, typed confirmations |
| Pool loss | Data loss | Scheduled backup to another pool, with overdue alerts |
| Plain FTP | Passwords readable on the LAN | Off by default; TLS required unless turned on, with a plain warning |
| Features decided without Chris | Rework and lost trust, as in revisions 2 and 3 | Rule 7: his reference list is the scope; anything cut is put to him first |
| Look drifts too close to Synology | Legal trouble if sold | Own icons, palette and layout from the start |

**Next step:** the desktop's screens for arrays. Create pool needs the filesystem choice
with XFS preselected and "RAID 10 · Two copies" named in full; Snapshots, Previous
Versions and Backup stay visible but disabled, saying "This pool doesn't support
snapshots" and "The current backup engine only supports ZFS pools"; and the pool view
needs a Check array button beside ZFS's Scrub. Then the four-disk and sixteen-disk gate.
Phase 1 still needs the read-only import of a real TrueNAS pool, and Phase 2 still needs
a real Windows client: mount a share, then check Previous Versions.
