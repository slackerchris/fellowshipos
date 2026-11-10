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
- Several pools at once, each on its own engine: ZFS, Unraid-style (mergerfs + SnapRAID), or traditional RAID (mdadm)
- Any machine and any number of drives, from an old PC with one data disk to a 16-bay server behind an HBA. Nothing needs specific hardware; extras such as bay numbers and locate lights appear when the hardware supports them
- Shares over SMB, NFS, SFTP, FTP and WebDAV, with permissions per user and group
- Users and groups managed from the desktop
- Snapshots (ZFS), parity syncs (SnapRAID), scrubs and RAID checks, SMART monitoring, health checks and alerts
- Scheduled backup from one pool to another
- A desktop in the browser, served by our own web server: windows, taskbar, app launcher, notification centre, and a real file manager

**Later**

- Browser-based document and spreadsheet editing through Collabora Online (Phase 16)
- Btrfs storage and snapshots (Phase 17), after office editing
- DLNA, so TVs and consoles find media by themselves
- iSCSI, so a slice of a pool appears as a disk on another machine
- More alert services (Apprise covers 100+, such as Discord, Telegram, Pushover and Home Assistant)

**Not now**

- Docker, an app centre, VMs, photo and media apps (Immich, Jellyfin)
- Kubernetes, off-site replication, clustering, directory services
- The NAS role in the LXC template (a container can't own disks or load ZFS)
- Btrfs RAID5/6, which is still not considered safe
- A native mobile app: a project of its own, planned after the release (Phase 15). Until then the desktop works in a phone's browser and installs on the home screen, and WebDAV (Phase 13) puts the NAS in the phone's own file apps
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
- Snapshots by Sanoid, events from `zed`, and scrubs from `fnas`'s own timer (see Storage checks below). Every ZFS pool fnas creates, imports or sees on upgrade gets `org.debian:periodic-scrub=disable`, which Debian's `zfsutils-linux` cron job honours, so only fnas scrubs; the cron job's monthly TRIM still runs. The role depends on `cron` for that TRIM.
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
  - Every engine's long jobs use the same mechanism. Its kinds (`jobs.KINDS`) are resilver, scrub, expand, sync and backup; `sync` is SnapRAID's; a failed sync alerts, a finished one records its time.
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
- **Boot:** the array is recorded in `/etc/mdadm/mdadm.conf` and the initramfs, and mounted by filesystem UUID (from `/etc/fstab`; see the open question on a systemd mount unit).
- **Alerts:** `fnas` adds a `PROGRAM` line to `mdadm.conf`, so Debian's `mdmonitor` service runs `/usr/lib/fellowship-nas/md-alert` for each event, within its one-minute poll. A failed disk, a degraded or vanished array and a missing spare are alerts; a finished rebuild is a note.
- **Checks:** Debian's `mdcheck` timers check every array from the first Sunday of each month, six hours a night until done. Settings → Storage checks turns them on or off (off masks both timers, so mdadm's preset can't undo it); the timing stays Debian's until fnas has a check that spans nights. A drop-in on those services tells `fnas` which arrays they started and finished, so Health records them beside the checks `fnas` starts, and a check's mismatch count is a Health finding. On ZFS the same finding reports read, write and checksum errors on disks still in the pool.
- **Shares and settings:** shares are folders in `/mnt/NAME/shares`; settings in `/mnt/NAME/fellowship-config`.
- **No snapshots:** shares show "No snapshots on this engine".
- **Growing:** one disk at a time. The reshape runs as a job; the filesystem is grown when it finishes.
- **Replacing:** a failed disk is marked failed, removed and replaced through the same guided flow as ZFS. The rebuild runs as a job, and a hot spare takes over by itself.
- **Importing:** an array from another Linux machine is assembled read-only first, then mounted.

**As built in Phase 10 so far**

- **One array per pool.** An mdadm pool is a single array of every disk it has, so the
  layout list offers one group rather than ZFS's several: RAID 5 up to six disks, RAID 6
  above that, and RAID 10 with the standard two-copy layout. A mirror of two disks with
  the rest waiting as spares is offered when the disks outnumber what the layout needs.
- **Filesystem:** XFS is preselected in the CLI and the desktop, and ext4 sits beside it. The choice is
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
- **Health:** the storage check is now **Storage checks**. It covers every writable pool,
  not only ZFS ones, each engine names the action in its own words (Scrub for a ZFS pool,
  Array check for an array), and a pool is overdue by its engine's schedule in Settings.
- **No snapshots:** a share on an array keeps no history, so Previous Versions has
  nothing to offer, and the backup engine still covers ZFS pools only. Both stay visible
  in the desktop with that reason rather than disappearing.
- **Desktop:** Create pool names each layout in full ("Two copies of everything, striped
  across 4 disks", with "RAID 10 · Two copies, 4 disks" in small print) and asks for the
  format, XFS preselected. An array's card has a Traditional RAID label, Check array,
  Repair when a check found mismatches, and only the ways an array grows: one more disk,
  or spares. Pool details reads "Traditional RAID, formatted XFS" and shows that XFS
  doesn't compress rather than a ratio.
- **Shares and settings:** shares are plain folders in `/mnt/NAME/shares`, and the saved
  configuration is `/mnt/NAME/fellowship-config`, the same names ZFS uses for datasets.
- **Verified on real disks** (VM, four disks, all from the desktop): a RAID 5 array on
  XFS was created, shared over SMB, checked, had a disk failed and replaced, grew by a
  fifth disk, and came back after two reboots, with a test file's checksum intact. That
  run found seven bugs, all fixed and covered by tests. RAID 6, sixteen disks and
  importing an array made on another machine are still only written from mdadm's
  documented behaviour.

**Storage checks** (Chris, Nov 10)

- **Settings → Storage checks** has one row per engine, built or not: whether its pools are checked by themselves, when, the next run, its pools, and **Check now** (the same check as Storage's button, on every healthy pool of that engine).
- **Engines fnas schedules** (ZFS and, since Phase 11, SnapRAID) run from `fellowship-nas-scrub@ENGINE.timer`, which calls `fnas pool scrub-all ENGINE`. The row sets how often (every month, or every three months), the week (first to fourth), the day and the hour; fnas writes them as the timer's `OnCalendar` in a drop-in. The defaults reproduce Debian's: the second Sunday of each month, at midnight, so an upgrade changes nothing.
- **Arrays** keep Debian's `mdcheck`, which pauses each morning and carries on the next night, so their row only turns it on or off. Timing moves into fnas when, and if, fnas grows a check that spans nights.
- **Health** reads each engine's schedule: a pool is overdue after 40 days on a monthly schedule and 100 on a quarterly one. Automatic checks turned off are a choice, shown as such, not a late check.
- **Saved with the settings** on every pool, and brought back by a restore.

**Unraid-style (mergerfs + SnapRAID)** (absorbs the old storage node)

- **Disks:** each data disk gets its own XFS filesystem, mounted at `/srv/disks/SERIAL`, and mergerfs joins them at `/mnt/NAME`.
  - XFS is used because SnapRAID keeps each parity level in one file, and ext4 caps a file at 16 TiB.
  - Parity disks are XFS too, and must be at least as large as the largest data disk.
- **Shares and settings:** shares are folders in `/mnt/NAME/shares`, as on every engine, so the settings folder beside them isn't taken for a share; settings are in `/mnt/NAME/fellowship-config`, copied to every data disk so the pool can be rebuilt after a reinstall.
- **Jobs:** the nightly sync and a sync started by hand run as a `sync` job, one of `jobs.KINDS`; it goes through `jobs.work` like the others, with an alert when a sync finishes or fails.
- **Packages:** `mergerfs`, `snapraid` and `hd-idle` are dependencies of `fellowship-nas-tools`, as `mdadm` and `xfsprogs` are.
- **Schedules:** `fnas` writes the mergerfs mount and `snapraid.conf`. A nightly `snapraid sync` runs, then a weekly partial `snapraid scrub`, both from `fnas`'s own timers and set in the same Settings → Storage checks rows as ZFS, from the first day. The sync is skipped with an alert if too many files were deleted, a sign of a missing disk.
- **Repairs:** a failed data disk is rebuilt onto its replacement with `snapraid fix`, through a guided flow in the Storage app.
- **Protection is visible:** the Storage app says plainly that files changed since the last sync aren't protected yet, and when the last sync ran.

**As built in Phase 11** (Chris's choices, Nov 10: one or two parity disks, new files to the disk with the most free space, the sync on its own Settings row, spin-down on after 30 minutes)

- **Layouts:** one parity disk up to five data disks, two from six on; the largest disks become parity. Disks of any size mix, and only parity larger than the largest data disk goes unused.
- **On each disk:** an XFS filesystem labelled `fn-` and the start of the pool's id, mounted at `/srv/disks/SERIAL` by filesystem UUID with `nofail`, and a `fellowship-disk.json` naming its pool and role. A data disk's files sit in its `files/` folder, which is the mergerfs branch: an unmounted disk has no such folder, so mergerfs never writes onto the system disk in its place (checked in the VM).
- **The pool:** mergerfs at `/mnt/NAME` from `/etc/fstab`, started as its systemd mount unit (`mnt-NAME.mount`) rather than by `fnas` itself: mergerfs keeps running while the pool is mounted, and inside the desktop's helper it stopped whenever that service restarted, as on every update (found in the VM). Mounted with `category.create=mfs`, `posix_acl=true` so share access works as on other engines (checked in the VM), and `inodecalc=path-hash,noforget` for NFS. NFS exports of these shares carry a fixed `fsid`, since a FUSE filesystem has no device number. The pool's record is `/etc/fellowshipos/unraid/NAME.json`, SnapRAID's settings `/etc/snapraid/NAME.conf`, with a content file on every data disk and one on the system disk.
- **Settings folder:** kept out of parity, since fnas rewrites it at any time and SnapRAID stops a sync when a file changes under it (found in the VM); after each sync it is copied to every data disk instead, newest copy of each file winning.
- **Jobs:** `sync` is a job kind. SnapRAID runs as the job's process, its progress read from its log in `/var/lib/fellowship-nas/snapraid/logs`. A sync that only met files changing while it ran counts as done, since everything else's parity was saved. A finished sync records its time rather than sending an alert each night; a failed one alerts, as every job does.
- **Schedules:** `fellowship-nas-sync@mergerfs.timer` at 01:00 by default, before the 03:00 backup, and `fellowship-nas-scrub@mergerfs.timer` weekly (or monthly), reading 12% of the pool, oldest first, each time; Check now reads all of it. Health: a sync over 30 hours old (unless the nightly sync is off) and a skipped sync are findings; the scrub is overdue after 14 days on a weekly schedule.
- **Deleted files:** a sync is skipped, with a high alert and a Health finding, when more than 50 files went missing since the last one; Sync now in Storage, or `fnas pool sync NAME --force`, keeps the change.
- **Growing:** data disks join empty, no larger than the smallest parity disk, added to mergerfs while mounted; a second parity disk must be at least as large as the largest data disk, and is built with `snapraid sync -F`.
- **Replacing:** a disk that can still be read is copied across, then synced; one that can't is rebuilt with `snapraid fix -d NAME`, then synced. **Repair** after a scrub runs `snapraid -e fix`, then checks those blocks again.
- **Spin-down:** hd-idle, written into `/etc/default/hd-idle` by stable `/dev/disk/by-id` names, after 10 minutes to two hours or never, from the Settings row.
- **Importing:** finds fnas-made pools by their XFS labels and markers, without mounting every disk. Disks from a mergerfs + SnapRAID setup fnas didn't make aren't imported yet.
- **Checked in the VM** (6, 10 and 12 GB data disks and a 14 GB parity disk, created from the desktop): the pool, a share over SMB and NFS with ACLs, 60 files spread across disks, a sync, and their checksums. Failing and replacing a disk, pulling more disks than parity, a second parity disk, the timers, the deleted-files limit, spin-down and a reboot were not tried in the VM; Chris called the gate on what had run.

**Btrfs (Phase 17)**

- **Layouts:** single, `raid1`, `raid1c3`, `raid10`. Metadata uses at least the same redundancy as data, and RAID5/6 isn't offered.
- **Shares and settings:** shares are subvolumes in `/mnt/NAME/shares`; settings are in `/mnt/NAME/fellowship-config`.
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
  - **Built, read-only** (Nov 10): a share reaches WebDAV only when its own checkbox is on, and it is off on every existing share until someone turns it on, since a new way in should be a decision rather than a default.
    - A client signs in with the NAS user name and password on each request (HTTP Basic), and that is only accepted over HTTPS: over plain HTTP the answer is 403 before a password is looked at.
    - `/dav/` lists the shares the person may use *and* that are shared over WebDAV. `GET` and `HEAD` send a file's bytes and honour `Range`, so a client can seek in a film; `PROPFIND` (Depth 0 and 1) lists a folder with each name's size, type and date; `OPTIONS` says what is allowed.
    - A share that is not theirs or not shared over WebDAV answers 404 either way, so an address can't be used to learn what other people have. The reason is logged on the NAS.
    - Every write — `PUT`, `MKCOL`, `DELETE`, `MOVE`, `COPY`, `PROPPATCH`, `LOCK`, `POST` — answers 405 with `Allow: OPTIONS, GET, HEAD, PROPFIND`, so a client learns the shape of the share instead of failing mysteriously. `Depth: infinity` is refused with `propfind-finite-depth`, since walking a whole share is a way to tie up the disks.
    - A client's password is checked once and the session kept for 30 minutes, so a folder of fifty pictures costs one check rather than fifty.
    - `ETag` is the file's name, size and last change, so the answer to a range and to a whole read of the same file agree. The child's read now says when the file last changed, which is what that tag is made from.
    - **Writing** (Nov 10): `PUT`, `MKCOL`, `DELETE`, `MOVE` and `COPY`, every one of them carried out by the helper as the person through the ops Files already uses, so the kernel decides what may be written exactly as it does there.
      - `PUT` creates with 201 or replaces with 204, and answers the new `ETag`, which is a strong one: the child names each version by its inode, size and last change in nanoseconds.
      - `If-Match` is checked twice: once here so a stale client hears about it early, and again by the child as it commits, under a lock on the folder. Two clients holding the same version take turns, the second is told 412, and the first client's file is what stays on disk. `If-None-Match: *` means "only if new", and a weak `If-Match` is refused since a write can't rest on a loose comparison.
      - `MOVE` and `COPY` check both ends: the source has to exist and be theirs to read (a move only needs the folders, so the kernel alone would let someone shift a file they cannot open), the share has to be theirs to write, and the address they are going to has to be free — an existing name answers 412 rather than being replaced, since overwriting is what the desktop makes people type a name for. A move between shares answers 502 rather than being guessed at.
      - Nothing reaches into `.zfs`: any address with that as a step answers 403, on every method, so a client can't write into or over a snapshot.
      - A refusal the kernel makes reads as 403, not as a server error.
    - Walked with two regular accounts: a stale client got 412 and the first client's text stayed on disk; two clients PUTting the same version at the same instant left exactly one 204 and one 412; a read-only account could browse (207) and read (200) but every write was 403 "archive is read only for you"; a copy or move whose source was root's unreadable file was refused 403 and left it where it was; one into a root-owned folder was refused the same way; and every method against `.zfs` answered 403. rclone, as a real client, uploaded, read, renamed and deleted a file.
    - **Locks** (Nov 10): `LOCK` and `UNLOCK`, which is what a word processor takes before it will save to a drive. The desktop says `DAV: 1, 2` on every answer, which is how a client knows locking is offered at all, and `PROPFIND` says so per address too. A lock is exclusive and on one address, or on a folder and everything in it; every change — `PUT`, `MKCOL`, `DELETE`, `MOVE`, `COPY` — needs the token in its `If` header while it is held, or answers 423. A client may renew its own lock, and every lock has a time on it (30 minutes unless the client asks for something else, 12 hours at most), so one abandoned by a client that vanished holds nothing for longer than that. Locks live in the desktop's memory: a restart loses them, which is what clients expect after a server restart.
      - A lock may be taken on a name that does not exist yet, which is what a new file looks like to a mounted drive. It does **not** make the file: doing that made the client's own `If-None-Match: *` create fail with 412, and its write never landed — found with davfs2, which is the only client that both locks and creates.
      - Only exclusive locks are offered; a request for a shared one is refused with that reason.
      - **A lock is the account's, not the token's** (Nov 10). Writing through a lock, renewing it and releasing it all need the account that took it as well as the token, so a token overheard, copied out of a log or replayed by a second person who also has write access to the share is worth nothing. None of the 423 answers names a token any more: whose lock it is stays the holder's business. `UNLOCK` from another account answers 423, and from somebody holding no such lock at all, 409.
      - **A folder carries its locks with it.** Deleting or moving a folder takes whatever is inside, so every lock at or under that folder is checked before anything is touched: if one is not satisfied, the whole request answers 423 and the folder, its contents and the lock are all left exactly as they were.
    - Walked with curl and with **davfs2 mounting the share as a drive** on this machine: while the drive had a file open, another client's `PUT` to it answered 423, and after the drive let go the same `PUT` went through (204). A file written through the mount, and one changed through it, both landed on the NAS with their contents; a file written, then released by unmounting, kept its text rather than being taken away with the lock. Over curl a lock on an existing file kept a write out until the token came (423 then 204), a lock on a name that wasn't there left nothing on disk until the client created it, UNLOCK needed the token it was given, and a read-only account was refused a lock with "read only for you". Then, with two accounts that both have write on the same share, dana locked `media/folder/notes.txt` and gave guest the exact token: guest's `PUT`, renewal, `UNLOCK`, `DELETE` and `MOVE` of the file and of the folder all answered 423 — with no token in any of those answers and no `Lock-Token` header on them — while the file still held dana's text and her `PROPFIND` still showed the lock. Dana then wrote through the lock (204), renewed it (200) and released it (204), after which guest's write went through. The davfs2 mount as dana was repeated afterwards: the drive read the share, wrote a new file and changed an existing one, unmounted, and both landed on the NAS with no lock left behind.
  - **Still to come in this phase:** SFTP and FTP.

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

- **The Users app** has two tabs, People and Groups. People lists every account on one
  line: its groups, whether it is an admin, whether it has a password for shares, and
  which shares it can reach and how. A row's menu holds Change, Turn off and Remove, and
  **Add someone** opens the form under the list.
- **Adding someone:** name, full name, password twice, and what they may do:
  - **Shares only** (the default): uses shares and, later, Files; no SSH login (`/usr/sbin/nologin`).
  - **Shares and SSH**.
  - **Administrator**: also manages the NAS (`nas-admins`).
- **One password:** setting a password sets the Linux one and the Samba one together, so the desktop, SMB and later SFTP, FTP and WebDAV all take the same password. An account made before this, with no Samba password, says so and offers to set one.
- **Groups:** create, rename and remove groups, and choose their members. Shares can be given to a group.
- **Every other group on the machine** is offered under that heading, not a list fnas
  keeps: the packages' groups (video, render, wheel, docker), the groups of accounts fnas
  doesn't manage (Plex, the *arr services, a backup tool), `nas-admins`, and each
  account's own group, which is marked as such. A share can be given to any of them, which
  is how software that already runs here reaches the files it needs without a second
  account, and a person can be given one from their own form. Nothing is filtered; the
  list is searchable when a machine has dozens. fnas never renames or removes these
  groups, and when it changes who is in one it only adds and removes people it manages, so
  the app the group exists for stays where it is. `fnas group list --system` lists them,
  with their ID.
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
| Low | A pool not checked on its schedule (40 days for monthly, 100 for every three months; never, with automatic checks turned off); settings from another install not restored |

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
| File helper | The logged-in user | Every Files app and WebDAV operation, so Linux permissions decide what each person sees. Built in Phase 12 as the helper binary re-executed as that person, so there is no second socket and no second set of rules |

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
- **Desktop:** wallpaper, app icons, and widgets: pool health and capacity, disk temperatures, the last scrub, sync, snapshot and backup, and recent alerts. The widget column scrolls when it is taller than the screen.
- **Windows:** move, resize, minimise, maximise, stack, several open at once; layout remembered per user.
- **Taskbar:** open windows, launcher, clock, notification centre.
- **Phone:** the desktop is a mobile-friendly web app, built with Files (Phase 12), not a separate app (Chris, Nov 10).
  - It installs on the home screen (a web app manifest and icons) and opens full-screen, without the browser's bars.
  - Windows open full-screen and the taskbar becomes a bottom bar, with tap targets sized for fingers.
  - Every action has a tap path: nothing needs a right-click, hover or drag.
  - A web app can't upload in the background on iOS, so automatic photo backup waits for the native app.
- **Built with:** TypeScript, Preact 10 and esbuild (chosen in Phase 4).
- **Non-admins** see only Files and their own settings.
- **Look (chosen by Chris):** a dark forest palette, with the fantasy in the atmosphere only.
  - Controls stay clean and readable: solid panels, softly rounded windows, crisp icons, gold used sparingly.
  - The theme follows the device; the light theme is the same palette in daylight.
  - **Themes** (Chris, Nov 9): Silverwater, Hearthfield and Highwatch sit beside the default forest and gold, each with a light and a dark mode and its own wallpaper. Each is four colours per mode in `web/themes.mjs`; the build works out the rest and stops if any text falls below WCAG AA contrast.
  - **Where the look is chosen** (Chris, Nov 10): Settings → Appearance holds both light, dark or the system's and the four themes, because it is a preference and the account menu is for identity and signing out. The menu keeps the three-way toggle alone, since that is a frequent one-click change.
  - **Whose look it is** (Chris, Nov 10): the choice belongs to the person, not to the browser, so the NAS keeps it in `/etc/fellowshipos/nas-look.json` by user name. `fnas look show USER` reads it and `fnas look set USER --mode/--palette` writes it. Signing in applies it before the desktop draws, with the browser holding a copy so a reload paints the right one before the server answers. Each person's own look is theirs to change: the helper lets a non-admin run `look show` and `look set` for their own user name and nothing else. Removing a person forgets theirs, so a reused name starts clean. The file is not part of a pool's saved settings, because the look isn't the pool's.

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
| Shares | Two tabs: Shared folders, one line per share with its access and protocols and a Connect panel, and Sharing settings for the SMB and NFS server and the LAN subnet; permissions per user and group; the LAN subnet |
| Users | Accounts, passwords, groups, `nas-admins`, and the shares each person can reach |
| Snapshots and backup | Snapshot schedules per share; browse and restore; backup jobs with last and next run |
| Health | Every check with its result and time; services; system; pools; disks; alerts (see Health) |
| Alerts | The notification history, and email and ntfy with test buttons |
| Files | The file manager (below) |
| Settings | Hostname and time zone; network (DHCP or a static address, DNS, gateway); certificate; updates; system space; storage checks; restart and shut down; active sessions; Run setup again |

**Files app**

- Sidebar: pools, shares, home folder, favourites, recent
- Toolbar: back/forward/up, breadcrumb, search, grid/list, sort
- Grid with thumbnails for images and video; list with name, size, modified, owner, permissions
- Multi-select with click, Shift and Ctrl; drag to move, Ctrl-drag to copy
- Right-click menu: open, download, rename, copy, cut, paste, delete, compress, properties, previous versions
- Upload by dragging in from the computer; download folders as zip
- Quick look for images, video (with seeking), audio, PDF and text with the space bar
- **Previous versions panel** on ZFS and Btrfs: a timeline of snapshots containing the file, with preview, "Restore a copy" and "Replace"

## Files (Phase 12)

Chris's order (Nov 10): Files comes before more ways to share, because WebDAV in Phase 13 goes through the same helper.

**Scope.** Browse each share the signed-in person can already use, download, preview (images, video with seeking, audio, PDF, text), upload, rename, move, delete, and bring back an earlier version from a snapshot. The richer list above is the shape of the app rather than all of this phase: search and zip downloads wait.

**Snapshots are ZFS only** (decided Nov 10, of the three questions in the next-steps brief). On arrays and Unraid-style pools the previous-versions button is shown disabled with the reason, as the desktop already does elsewhere, rather than hidden or quietly empty.

**One helper, and the work is done as the person** (decided Nov 10).

- The existing `fellowship-nas-helper` gains file ops beside `fnas`. A second privileged service would mean a second socket, a second peer check and a second set of rules to keep in step.
- The person comes from the session secret the helper already keeps, never from the request.
- Each request is served by a child re-exec'd with `SysProcAttr.Credential` set to that person's uid, gid and groups, so the kernel enforces the answer and an error in the path check can't reach another person's files.
- The roots are that person's share roots, which the helper asks `fnas` for itself. The client sends a share name and a path inside it, never an absolute path.
- The path is resolved with symlinks followed, and refused if it leaves the root or doesn't resolve. An unmounted pool, an unknown share or an unreadable root answers "no" rather than naming a disk.
- Tests for every way out: `..` climbing out of a share, a symlink inside the share that points outside it, a dangling symlink, a NUL byte, a share the person can't use, and a root that has gone since its pool was exported.

**10 GB transfers** (decided Nov 10). `ReadTimeout` covers the whole request, so a large upload cannot fit in it. The upload and download routes clear the connection's deadline through `http.ResponseController` for their own duration, as the jobs stream already avoids a `WriteTimeout`; the rest of the desktop keeps the 60 second cover. A cap on each file and on the whole request keeps one upload from filling a pool or holding a worker. The route was walked with a 1 GiB file instead of ten, since the path is the same one: uploaded, downloaded again and compared byte for byte, then read from the middle with a `Range` that answered 206 and the right 50 bytes. Ten gigabytes itself is still only a cap in the code.

**Phone.** The gate is the installable web app on top of the phone rules the shell already has: a manifest and icons, a service worker for the shell so the home-screen icon opens instead of a blank page, and the Files screens laid out with tap targets and no sideways scrolling.

**Built so far.**

- **Slice 1:** `internal/files` resolves a share and a path against the roots a person may use, with thirty refusal tests: climbing out with `..`, a link that leads out of the share, a dangling link, an absolute path, a NUL byte, a share they can't use, and a share whose pool has gone. `Resolve` is for something that exists, `ResolveNew` for a name that must not exist yet and refuses one that does, so replacing a file goes through `Resolve` and an `O_NOFOLLOW` open rather than a link sitting on the name.

- **Slice 2** (browsing, downloading and previewing): `fnas share roots USER` answers the shares a person can use with where each one is on disk and whether it has snapshots, joining `shares_of` and `list_shares` so neither the desktop nor WebDAV works out a path. The helper's `file-list` and `file-read` take a share name and a path, never an absolute path, ask fnas for the roots itself, resolve the path, then re-exec the helper as the person with `SysProcAttr.Credential` to do the work: the child runs the same binary with `file-child <op> <resolved path>`, answers with one line of JSON and, for a read, the bytes. A refusal from the child keeps its own words, so a file the kernel won't hand over says "you don't have permission to open that" rather than a generic failure. The front's `/api/files` lists shares or a folder and `/api/files/download` sends the bytes with `Content-Type` for what the desktop can show, `Content-Disposition: attachment` for a download, and one `Range` answered as 206 so a video can be seeked. `/api/files` never carries a path, since only the helper knows where a share lives.
- **The one line of JSON and the file behind it** are read as a line on the connection, not with a JSON decoder: the decoder reads ahead into the file and leaves the newline that ended the head at the front of it, which cost the first few hundred bytes of every download until a round-trip test caught it.
- **In the harness** the container now makes two shares with real owners: `media` for both sample accounts and `family` for `nas-admins` alone, so a person is refused by the kernel as well as by fnas.

- **Slice 3** (changing what is there): upload, new folder, rename, move by dragging onto a folder, delete, and a filter box. Each change is an op the helper carries out the same way a read is: the share's level is checked first, so a share that is read only answers "*share* is read only for you" before any path work, then the path is resolved, then the child does it as the person. An upload arrives as the request's body, is measured as it goes and refused past the helper's 10 GB cap, and the child writes it through a temporary name in the same folder before renaming it into place, so a stalled upload never leaves half a file. A file it writes is `0664` and a folder it makes `0775` after the create, since the umask would otherwise leave a share's group unable to work in what it just made.
- **After a review of slices 1 to 3** (Nov 10): the child no longer trusts a whole path. The helper resolves as before, then hands the child the share's real folder and the path inside it with no links left; the child opens the folder and every part beneath it with `openat2` (`RESOLVE_BENEATH`, no links of any kind), so a folder swapped for a link since the helper looked, or a way out, is refused by the kernel, and works on a name through the folder it opened (`/proc/self/fd`). Downloads: text of every kind, HTML and scripts included, is sent as plain text, and every file's bytes carry their own `sandbox` policy, so nothing someone uploaded runs as the person opening it; a PDF may be framed by the desktop alone. Every child says when it finishes, so one that stops part way is an error rather than a success; the child's groups are always set; and a person's shares are kept for five seconds, so a folder of thumbnails asks fnas once. The review also found that deleting a non-empty folder never took its contents, since the path reached the child twice.
- **Two things the harness found in slice 3:** a new folder at the top of a share has an empty path, which the front's shared "share and path" check refused until each change said for itself what it needs; and the sample shares were `2750`, so the person could read but not write — a share fnas calls read and write has to be group-writable, as the ACLs make it on a real NAS.
- **The app**, shaped like a browser rather than a list of links: the shares down the left with whether each can be changed, a toolbar of back, up and refresh over a breadcrumb that starts at the share's name, a sortable list with a header row and lined-up size and changed columns, a grid of thumbnails for folders of pictures, and the picked file in a pane beside the list. Clicking picks, a double click or Enter opens, the arrows move, Backspace goes up and Escape lets go. `useLoad` grew an argument for what it is asking for, so a new folder is actually fetched; before that, only the first folder a window opened was ever loaded.
- **Seen in the development container, writes:** uploaded two files as `admin` (both landed with the right sizes and owner), made a folder, renamed a file, deleted one through the typed confirmation, and dragged a picture into `photos`, which the pool's disk showed had moved intact. As `guest`, the read-only `archive` share showed no change buttons and its row menu held only Download; uploaded to, made a folder in and deleted from it answered 403 "archive is read only for you"; uploading to a share they can change worked; the same name twice was refused with "already here"; and a name climbing out of the share was refused as outside it.
- **Seen in the development container:** images and text preview, download as an attachment, one account refused a share the other can read, a climb out refused, `Range` answered as 206, and a whole file matching byte for byte. Not seen: video, audio and PDF previews (same path, no sample to try), and nothing was run in the VM.

- **Slice D** (who may change what, Nov 10): each entry in a listing, and the folder itself, carries its permissions as `ls` writes them (`rwxrws---`), owner and group by name, whether a POSIX access list is set, and whether the person may change it, which the child asks with `faccessat` as the person, so access lists count. The desktop's **Columns** control adds Kind, Owner and Permissions (off by default, kept per browser); the preview names kind, owner and permissions; and when something can't be changed, the reason is read from the permissions: only the owner, a group they aren't in, an access list, or read only for everyone. A folder they can't change hides New folder, Upload and Paste even on a share they may write. Seen in the development container: as `admin`, a root-owned file read "Only its owner, root, can change it" and a root-owned folder hid the write buttons with the same reason for the folder; as `guest`, a folder for `nas-admins` read "The nas-admins group can change this folder, and you aren't in it".
- **Slice C** (search, Nov 10): Enter in the filter box with three or more letters runs the child's `search` op over the whole share: it reads each folder through its own descriptor, compares names in any case (a slash or `..` in the query is only text), never follows a link, and stops at 500 matches, eight deep or two seconds, answering what it found with why it stopped. The helper refuses a query shorter than two characters. Results replace the folder with the folder each is in; opening a folder goes there, and opening a file goes to its folder and picks it once loaded. Seen in the development container as `admin`: "SUN" found four names across three folders, and opening one went three folders deep and picked it; as `guest`, a share they can't use and a one-letter query were refused and `../etc` found nothing.
- **Slice B** (copy and move, Nov 10): a clipboard in the browser holds what Copy or Cut set aside (share, folder, names); Paste in another folder of the same share renames for a cut, or runs the child's new `copy` op, which writes a file or a whole folder under a `.fnas-copy-*` name beside the destination and renames it into place, never following a link and refusing a folder into itself (the helper checks that too, before any work). **Move to…** lists the share's folders from the child's `tree` op (500 at most, eight deep, links and its own temporary names left out), with the current folder and anything being moved greyed. Same share only. Seen in the development container as `admin`: two files and a folder copied into another, a file moved with Move to… and another with Cut and Paste; through the API, a copy onto a name already there, into itself and out of the share refused, and as `guest`, a copy on the read-only share and a tree of a share they can't use refused. The walk found that new folders lost the share's setgid bit, since setting their mode cleared it, leaving what was made inside them to the person's own group; New folder and copies now keep it.
- **Slice E** (earlier versions, Nov 10): "Earlier versions…" in a file's menu lists the snapshots that hold that path with their dates and sizes, **Look** opens one through the same download route with `snapshot=`, and **Put back a copy** writes it beside the file as `notes (from 11-10-2026).txt`, never over it. The reading is done by the helper as root, because ZFS leaves `.zfs` reachable to root alone on many installs, so the boundary is kept by three things instead: the snapshot's name comes from `fnas snapshot list`, never from the request; the path is resolved and confined against that snapshot's own folder; and the person must be able to list the file's folder through their own child, which still works for a file that has since been deleted. Everything written back goes through the ordinary write child as the person. On a pool without snapshots the item is disabled with "This pool doesn't support snapshots". Seen in the development container as `admin`: two versions listed, the older read as its own bytes, and the copy written as 48 bytes, owner `admin:media`, mode `664`, with the file itself unchanged; refused for an invented snapshot name, a name already in the folder and a target outside the share; as `guest`, `family` refused and its own `media` versions listed. Not seen: a real ZFS snapshot — the harness writes `.zfs` trees by hand, and there `.zfs` also shows in the listing, which it does not on a default install.
- **Slice A** (several at once, Nov 10): the pick is a set, by Ctrl-click or Cmd-click, Shift-click, Shift with the arrows, and Ctrl-A; a pane for several says how many and how big, with **Delete…** and **Download as zip**. A bulk delete is one typed confirmation naming the count and the share, then one op per item, so one refusal doesn't stop the rest. A zip is `GET /api/files/zip` with the folder and the names in it, so the browser saves it as it comes; the helper checks every name before any work, and the child walks beneath the share, never following a link, adds up the size and refuses past 2 GB before sending a byte, then writes the zip as it reads. Its answer runs to the end of the connection (`ToTheEnd`), since its size isn't known first. Seen in the development container as `admin` and `guest`: four picked by click, Ctrl-click and Shift-click, zipped and read back intact; a bulk delete of four with one folder the kernel wouldn't let `admin` empty, reported as "3 deleted, 1 refused"; and as `guest`, a zip from the read-only share worked while one from a share they can't use, or with `..` in a name, was refused.

**Slices, in order.** The resolver and its refusal tests, then list, download and preview, then upload, folders, rename, move and delete, then several at once, then copy and move, then search, then who may change what, then earlier versions from a snapshot: the first eight are built. The installable phone app is being kept as its own project (Chris, Nov 10) and the gate is walked on real hardware rather than in the VM here; what is left is planned in [the Files plan](fellowship-nas-files-plan.md).

## Security model

Built for a trusted home LAN; nothing is exposed to the internet by default.

- **Two roles:** `nas-admins` manage storage, shares, users and settings. Everyone else sees Files for folders they already have permission to.
- **Privileges:** only the privileged helper runs as root. It runs `fnas` for `nas-admins` members and nothing else, and `fnas` checks every argument. Files adds one narrow exception: the helper serves file requests as the signed-in person, in a child running with that person's uid and groups, inside that person's share roots (see Files).
- **Network:** `nftables` allows HTTPS, NFS, SMB, SSH/SFTP and FTP (with its passive range) from the LAN subnet only.
- **Protocols:** SFTP-only users get no shell. FTP requires TLS unless plain FTP is turned on, and the FTP row says plainly that passwords then travel unencrypted.
- **Remote access:** not built in; a VPN such as WireGuard or Tailscale is documented.
- **Destructive actions** (destroy a pool, delete a share, roll back a snapshot, wipe disks, remove a user) need a typed confirmation that names what will be lost.
- **Updates:** `unattended-upgrades` for Debian security updates, including the kernel, with no automatic reboot.
- **Review:** Phase 14 requires a security review of the release build and its installed defaults. The checklist below defines what to verify.

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
    fellowship-nas-tools/    fnas, its fellowship_nas package (engines/ holds one module per engine), fellowship-nas-notify, and each engine's packages and hooks
    fellowship-nas-web/      Go server and helpers, plus the desktop (web/)
  theme/                     icons, wallpaper, colour tokens
  tests/
    vm/                      vm.sh: install from the ISO's boot menu, with up to 16 virtual disks
    unit/                    fnas tests that never touch a real disk
```

`fellowship-nas-web` replaces Phase 4's Cockpit package, `fellowship-desktop`. All FellowshipNAS packages share one version number.

## Build phases and test checklist

No phase starts until the previous gate passes in a VM, except for items carried over by name: the phase's row lists them, and they are done before the release. Each storage gate runs on real disks in the VM (virtual disks the kernel sees as disks), never only against fakes: Phase 10's engine passed every unit test, and its first real-disk run still found seven bugs. Each storage gate runs on several machines: the system disk alone, one data disk, two disks, four disks, sixteen disks, and disks of mixed sizes, so nothing only works on a big box. Every gate also checks rule 6: Chris can see from the screens alone what exists, what doesn't, and why.

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
| 10. Traditional RAID | The mdadm engine: RAID 1, 5, 6, 10, spares, ext4 or XFS, monitor alerts, monthly check, grow by one disk, guided replacement, import | Build RAID 5 and RAID 6 from the desktop on four and sixteen disks; fail a disk, replace it, and the data checks out; reboot and the array returns; import an array made on another machine | Engine, CLI, Health, alerts and the desktop screens are built. Gate partly passed in the VM on four disks, all from the desktop: created a RAID 5 array on XFS, shared it over SMB, ran Check array, failed a disk and replaced it, added a fifth disk (the filesystem grew after the reshape), and rebooted twice; a 50 MB test file kept its checksum throughout. That run found and fixed seven engine bugs (see the changelog). Later, in the same VM: Debian's `mdcheck` ran a check that Health recorded, and a failed disk raised a critical alert within a minute. RAID 6 tested Nov 10 in a disposable VM: four disks made from the desktop, two pulled out one at a time, and a 100 MB file read back over SMB with its checksum intact; the run found and fixed an array missing disks reading as healthy. **Carried over** into Phase 11, to be done before the release: sixteen disks, and importing an array made on another machine |
| 11. Unraid-style | mergerfs + SnapRAID on XFS: sync and scrub schedules, `snapraid fix` flow, spin-down, absorbing the old storage node | Create with mixed disk sizes, share, sync; fail a data disk and rebuild it; pull more disks than the parity and the other disks stay readable; alert for a missed sync; all on real disks in the VM; writing `snapraid.conf` and the mergerfs mount twice changes nothing the second time, the check that caught `spares=1` in `mdadm.conf` || Built, with a shortened gate (Chris, Nov 10): created from the desktop on mixed disks, shared, filled and synced in the VM, which found and fixed a sync that failed whenever fnas saved settings. Not tried in the VM: failing, replacing and pulling disks, a second parity disk, the timers, the deleted-files limit, spin-down and a reboot. Also carried: RAID 6, sixteen disks and importing an array made on another machine (Phase 10), and importing disks from a mergerfs + SnapRAID setup fnas didn't make |
| 12. Files | The Files app (Chris, Nov 10: before more ways to share), snapshot browse and restore, and the per-user file helper that WebDAV reuses; the desktop as a mobile-friendly web app that installs on the home screen | Restore a file from a snapshot in Files; 10 GB transfers and video seeking; on a phone's browser, from the home-screen icon: browse folders, download, open a photo, play a video, upload from the photo library, rename, move and delete by tapping, and restore an earlier version, with no sideways scrolling | Decided Nov 10: the existing helper does the work, as the person, in their share roots; uploads clear the read deadline; snapshots are ZFS only (see Files). Built so far, each checked in the harness with an account that can change a share and one that can only read: the resolver with its refusal tests; browsing, downloading and previewing; uploading, folders, renaming, moving and deleting; several at once with zip downloads; copy, cut, paste and Move to…; search across a share; and who may change what. Left: snapshots (slice E), the installable phone app (F), and this gate in the VM (G) |
| 13. More ways to share | SFTP, FTP (FTPS by default) and WebDAV, using Phase 9's users and access lists; WebDAV goes through Phase 12's file helper | Two users with different access see only their shares over SFTP, FTP and WebDAV; plain FTP stays off until turned on | Read-only WebDAV over HTTPS is built and walked with two regular accounts and rclone; writes, SFTP and FTP are left |
| 14. Release | Network and updates in Settings, theme and icons, hosted signed apt repo, and a security review | Set a static address from the desktop; updates arrive from the hosted repo; pass the security review below | |
| 15. Mobile app | A native app for iPhone and Android, a project of its own (Chris, Nov 10): automatic photo backup in the background, the phone's share sheet ("Save to NAS"), and offline copies of files, on top of what the web app does | Photos taken on a phone reach the NAS without opening the app; a file saved from another app's share sheet lands in the chosen share; an offline file opens with no network | |
| 16. Office editing | Embed Collabora Online as an optional service for LibreOffice Writer and Calc. Launch it from Files to create or edit documents and spreadsheets. FellowshipNAS authorizes reads and saves through WOPI using the existing share permissions. | Create, edit, save and reopen a document and a spreadsheet in the same share. A read-only user can open them for viewing, and a user without share access cannot open them. | |
| 17. Btrfs | Engine, btrbk snapshots, Previous Versions, scrub, degraded mount | Create a mirror, restore a Previous Version from Windows, fail a disk and replace it | |

**Failure tests (run in the VM before every release)**

Install and role:

- [ ] Install to disk from the ISO with data disks attached, reboot: wizard runs once and settings persist
- [ ] Boot the ISO live: the first-boot wizard does not run
- [ ] Cut the network during the role install: failure is shown, and the retry command completes it
- [ ] Enable Secure Boot: role completes, ZFS shows as unavailable with the reason, the other engines work
- [ ] Try the role in the LXC template: role stops with a clear message

Pools, on every engine in the release build:

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

- [ ] Restore a deleted file from a snapshot in Files and through Windows Previous Versions on each snapshot-capable engine in the release build
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

**Security review before release**

- [ ] Review the authentication, session and authorization code. On a fresh VM installed from the release build, try expired and revoked sessions, cross-site requests and direct admin API calls as a regular user.
- [ ] Test share isolation with two users and both read-only and writable shares. Try unauthorized reads and writes through Files and each enabled sharing protocol. In Files and WebDAV, include crafted paths, symlinks and file replacement during an operation. Include uploads, archive downloads and snapshot restores in the file access review.
- [ ] Review the root helper and `fnas` for privilege escalation and command injection. Verify that each privileged operation checks authorization on the server and that file operations run with the caller's identity.
- [ ] Check the installed system from another machine. Verify which ports listen by default, which networks can reach them, and that each enabled protocol enforces its intended authentication and encryption settings.
- [ ] Review the upgrade path and check the actual release's Debian packages and Go and JavaScript dependencies for known vulnerabilities. Verify how a user authenticates an update before installing it.
- [ ] Record the build reviewed, checks run and findings. Fix and retest release blockers. Block release on known exploitable issues that allow authentication bypass, privilege escalation or unauthorized access to files, and document any other findings and their impact.

Use [OWASP ASVS](https://asvs.owasp.org/) for the web review and the [Debian Security Tracker](https://security-tracker.debian.org/tracker/) to check advisories against installed Debian package versions.

## Open questions and risks

**Answered by Chris**

- [x] Traditional RAID offers ext4 and XFS, with XFS recommended (Chris, Nov 9)
- [x] FTP uses TLS by default, with plain FTP as an opt-in (Chris, Nov 9)
- [x] Phase order (Chris, Nov 9): the screen fixes, then users and sharing settings, traditional RAID, Unraid-style, more ways to share, Files, and Btrfs last
- [x] Files before more ways to share (Chris, Nov 10): the Files app is Phase 12, and SFTP, FTP and WebDAV are Phase 13

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
| Four engines and five protocols multiply the work | Phases take longer | One engine layer and one share-settings file in `fnas`. Each engine module carries a `PRESENTATION` that `fnas pool engines` sends to the desktop (its check button and wording, ways to grow, formats, and whether it has snapshots, backup, compression and a separate repair), so the desktop never branches on an engine's name and a new engine doesn't edit it. Each engine and each protocol has its own gate |
| RAID 5 on large disks | A second failure during a rebuild loses the array | Warning on 8 TB and larger disks; RAID 6 recommended |
| mdadm can't tell which copy is right on a mismatch | Silent corruption goes unrepaired | Monthly check shown in Health; the comparison table says so before the engine is chosen |
| SnapRAID's unsynced window | Recent files lost with a disk | Nightly sync, the last sync shown in Storage, overdue alerts |
| ZFS module fails after a kernel update | ZFS pools not mounted after reboot | Post-install check, last good kernel as default, alert |
| Bug in pool or share scripts | Data loss | All destructive logic in `fnas`, tested in VMs, typed confirmations |
| Pool loss | Data loss | Scheduled backup to another pool, with overdue alerts |
| Plain FTP | Passwords readable on the LAN | Off by default; TLS required unless turned on, with a plain warning |
| Features decided without Chris | Rework and lost trust, as in revisions 2 and 3 | Rule 7: his reference list is the scope; anything cut is put to him first |
| Look drifts too close to Synology | Legal trouble if sold | Own icons, palette and layout from the start |

**Next step:** Phase 12's slice E, earlier versions from ZFS snapshots in Files, then the
installable phone app and the gate in the VM, carrying the untried Phase 10 and 11
tests over by name.
Phase 1 still needs the read-only import of a real TrueNAS pool, and Phase 2 still needs
a real Windows client: mount a share, then check Previous Versions.
