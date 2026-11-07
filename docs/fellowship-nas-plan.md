# FellowshipNAS Architecture Plan (revision 2)

Nov 5, 2026 · @Chris

Revises the original plan after a design review and a check against this repo. The main change is a new Phase 0: FellowshipOS can't install to disk yet, and everything else depends on that.

## What changed from revision 1

| Area | Revision 1 | Revision 2 |
| --- | --- | --- |
| Install | Assumed an installer with a role menu | New Phase 0 adds install to disk with `--debian-installer live` |
| Role selection | Installer sub-options | New option in the existing first-boot wizard, backed by a `nas-node` flavor |
| Existing pools | Wizard only checks for an imported pool | Wizard scans for importable pools before it offers to create one |
| Kernel and ZFS | Pinned as tested pairs | No pin: follow Debian stable, verify the module, fall back to the last good kernel |
| Phases 1–3 interface | Cockpit's stock pages | `fnas` CLI over SSH (stock Cockpit has no ZFS or share pages) |
| Files app | Assumed to work with no server-side code | Early transfer test decides this before Files is built |
| Backup | Replication out of scope | One scheduled `syncoid` job to a second local pool |
| Config recovery | None | Config copy stored on the pool |
| Web desktop | Built with the first apps | Apps ship as plain pages first; the windowed shell follows |
| Secure Boot | Not covered | Detected, and setup stops with a clear message in v1 |
| LXC | Not covered | NAS role is ISO-only |

## Overview

FellowshipNAS is a new "NAS / Home Server" role in FellowshipOS: ZFS storage, NFS and optional SMB shares, automatic snapshots, health alerts, and a desktop-style web interface. It replaces TrueNAS for a home setup that mostly needs NFS, without TrueNAS's apps, VMs, clustering and middleware.

**In scope**

- Install FellowshipOS to disk from the ISO
- One wizard option that turns an installed system into a NAS
- ZFS pools and datasets: import existing pools, or create new ones through a first-run wizard
- NFS (primary) and SMB (optional, pre-checked) shares
- Scheduled snapshots, scrubs, SMART monitoring and alerts
- One scheduled backup to a second local pool
- A web desktop: windows, taskbar and app launcher, with a real file manager as its centrepiece

**Out of scope for v1**

- Container apps, VMs, Kubernetes, iSCSI, off-site replication, clustering, directory services
- The NAS role in the LXC template (a container can't load ZFS modules or own the disks)
- Secure Boot support (disable it for v1)
- Native mobile apps (the web UI must work on a phone browser instead)

**Design direction:** the feel of Synology DSM and FygoOS (a desktop in the browser, apps in windows), with FellowshipOS's own name, icons, colours and layout. Nothing copied from either product.

**Guiding rules**

1. Layer on top of Debian, never fork it, so Debian keeps supplying security updates.
2. Use proven packages for the plumbing; spend custom work only on the user experience.
3. Anything that can destroy data happens behind a confirmation, never silently. The only disk the installer may touch is the OS disk, chosen by hand.

## Repo baseline

The repo before Phase 0:

| Item | Current state |
| --- | --- |
| Debian release | 13 Trixie |
| Architecture | amd64 only |
| Kernel | Stock `linux-image-amd64` (6.12.90 in the last build) |
| Apt sources | `main contrib non-free non-free-firmware`, so `contrib` is already enabled |
| Installer | None: `--debian-installer false`, and no install-to-disk tooling |
| Persistence | None: the ISO is live-only and nothing survives a reboot |
| Role menu | Bash prompt in `fellowshipos-setup`, one choice only |
| Role delivery | Flavor scripts in `/usr/local/lib/flavors`, run with `apt-get` during the wizard |
| Own apt repo or signing key | None |
| Debian packaging | None |
| ZFS or Cockpit | Not in the repo or the image |

## System architecture

FellowshipNAS is five layers on a stock FellowshipOS base, and every change made in the web UI goes through one helper CLI before it touches ZFS or a share config.

1. **Clients:** browsers, NFS clients, SMB clients
2. **Web:** FellowshipNAS Desktop and apps, on Cockpit
3. **Helper CLI:** `fnas` commands from `fellowship-nas-tools`
4. **Services:** NFS, Samba, Sanoid, `smartd`, `zed`, alerts
5. **Storage:** ZFS on the data disks, on Debian Trixie

NFS and SMB clients talk straight to their servers; the browser path goes Desktop, Cockpit, then the helper CLI, so all risky logic sits in one testable place.

## Install and first boot

There are three separate stages, and each one owns its own decisions.

| Stage | Runs on | Decides |
| --- | --- | --- |
| Debian installer | ISO, local console | OS disk, first user, time zone |
| First-boot wizard | `tty1`, local console | Hostname, SSH, role (and the first user in the LXC template) |
| NAS setup wizard | Web UI | Pools, datasets, first share, alerts |

**Debian installer:** `--debian-installer live` in `auto/config` makes the installer copy the live system to disk. Partitioning stays manual and is never preseeded, because the data disks are attached at this point. The docs should recommend unplugging data disks for a first install if the OS disk isn't obvious.

The installer needs three corrections, all in place since Phase 0:

- **Removal list:** live-build's list includes everything added after its install pass, so it would remove Docker and keep `live-boot`. A binary hook replaces it with just the live packages.
- **Apt sources:** the installer leaves the install media as the only source, which means no security updates and no ZFS. A `late_command` restores `/usr/share/fellowshipos/sources.list`.
- **Preseed loading:** the boot menu doesn't load `/install/preseed.cfg` by itself, so `--bootappend-install` passes it.

With root login off, the installer always creates a first user, with `sudo`. The wizard reuses that user, which also leaves a working login if the wizard fails.

**Changes to the base system**

- The first-boot wizard must not run in a live session, so `fellowshipos-setup.service` has a condition on the kernel command line.
- The service is ordered after `network-online.target`, because role installs need the network.
- The wizard pauses before it exits, because the login prompt clears the screen and would hide any failure message.

**Console access:** the installer already needs a monitor and keyboard, so the `tty1` wizard adds no new requirement. After first boot, the machine can run headless.

**Role option:** add "NAS / Home Server (ZFS + web UI)" to the wizard's menu. Rename the existing option to "Storage node (mergerfs + SnapRAID)" so the two aren't confused. They stay as separate roles, and combining them isn't supported. The wizard stays single-choice; other roles can be layered afterward with `homelabos-flavor`.

**Sub-options shown when the role is picked**

| Option | Default | What it installs |
| --- | --- | --- |
| SMB sharing for Windows/macOS | On, can untick | `fellowship-nas-smb` |
| Web file manager | On | FellowshipNAS Files app |

NFS is always installed. Alerts need no sub-option: `msmtp` is a small dependency of the meta-package, and channels are set up later.

**Role install:** a new `nas-node.sh` flavor runs these steps in order:

1. Stop with a clear message if this is a container, or if Secure Boot is on (read from the EFI variable, so `mokutil` isn't needed).
2. Check the network and Debian mirror access.
3. Install `fellowship-nas`, and `fellowship-nas-smb` if selected.
4. Confirm the ZFS module built and loads. If the role pulled in a newer kernel, the module only has to exist for that kernel, and the wizard asks for a reboot.
5. Write `/etc/fellowshipos/.nas-role-complete`.

The flavor must be safe to run twice. Today, the wizard writes `.setup-complete` even when a step fails. So if the marker in step 5 is missing, the wizard shows the failure and the retry command, `sudo homelabos-flavor nas-node`.

**Packages:** `fellowship-nas` is a meta-package that depends on everything the NAS needs. SMB lives in a separate `fellowship-nas-smb` package so unticking it simply skips that package. For v1, the Fellowship packages ship as a small local apt repo inside the ISO, while their dependencies come from Debian during the role install. The LXC build removes that local repo. A hosted, signed repo arrives in Phase 6 for updates.

**NAS setup wizard:** the `fellowship-nas-firstrun.service` unit opens this wizard on the dashboard until a pool is set up.

1. **Scan for existing pools** with `zpool import`. If any are found, offer to import them before anything else. Pool import is the default path, and creating a pool needs an explicit choice.
2. Show detected disks (model, size, serial, SMART status), hiding the OS disk. Flag any disk that has a ZFS label, partitions or a filesystem.
3. Pick a layout: single disk, mirror, RAIDZ1 or RAIDZ2, with usable space and drive-loss tolerance shown for each.
4. Name the pool and confirm with a typed confirmation ("erase 4 disks").
5. Create default datasets and a first share. On an imported pool, leave existing datasets alone.
6. Restore saved config if the pool has any (see Storage layer).
7. Set up alerts (optional, skippable).

## Storage layer

ZFS is the only storage engine in v1: one data pool, separate from the OS disk, with sensible defaults set once by the wizard.

**Packages:** `zfsutils-linux`, `zfs-dkms`, `zfs-zed`, `linux-image-amd64` and `linux-headers-amd64`. `zfs-zed` is listed because this image turns off apt's Recommends. The headers meta-package keeps headers matched to future kernels, and the image meta-package stops them running ahead of the installed kernel on an older ISO. All of these install during the role, so the module compiles at that point. It isn't baked into the image, because the LXC template is built from the same chroot.

**Kernel and ZFS updates:** there's no version pin, so kernel security updates keep flowing. Debian stable ships a matched kernel and ZFS, and backports stay off.

- `zfs-kernel-check` runs from both `/etc/kernel/postinst.d` and `header_postinst.d`, because a kernel and its headers can install in either order.
- With no module for the new kernel, it sets GRUB's default to the running kernel and logs an error (alerts arrive in Phase 3). When the module appears, it clears that default.
- `unattended-upgrades` never reboots automatically.
- `GRUB_DEFAULT=saved` is set by the role. Old kernels are kept until the new one has booted with its pool imported.

**Pool layouts offered**

| Layout | Min disks | Survives | Best for |
| --- | --- | --- | --- |
| Single | 1 | 0 failures | Testing only |
| Mirror | 2 | 1 per mirror | Small setups, fastest rebuilds |
| RAIDZ1 | 3 | 1 failure | 3–4 smaller disks |
| RAIDZ2 | 4 | 2 failures | Large disks, most data safety |

**Pool creation:** always use `/dev/disk/by-id` paths, so pools survive device names changing.

**Pool defaults:** `ashift=12`, `compression=lz4`, `atime=off`, `xattr=sa`, `acltype=posixacl`, `dnodesize=auto`. The last three matter for sharing one dataset over both NFS and SMB.

**Default datasets**

- `tank/shares` as the parent for user-created shares
- `tank/home` for per-user folders
- `tank/backups` as a landing spot for client backups
- `tank/fellowship-config` for the config copy

**Config recovery:** after every change, `fnas` saves share settings, the Sanoid config, alert settings (without secrets) and the user and UID list to `tank/fellowship-config`. After a reinstall, the wizard offers to restore them. Passwords aren't stored, so restored users set new ones.

**Disk replacement:** the Storage app has a guided flow for a failed disk: identify it by serial, choose the replacement, run `zpool replace`, and show resilver progress.

**Maintenance:** a monthly scrub through the `zfsutils-linux` cron job Debian already ships (the second Sunday of each month), with results shown on the dashboard. Pool import at boot uses the standard `zfs-import-cache` service.

## Migrating from TrueNAS

Test this before relying on it, using the real pool.

1. Export the pool from TrueNAS, and note its version and enabled feature flags.
2. On FellowshipNAS, try `zpool import -o readonly=on` first. Debian's ZFS (2.3.9 in Trixie at Phase 0) must support every active feature, or the import may be refused or read-only.
3. Check dataset encryption, permissions, and that UIDs match the new system.
4. Do a normal import only after that passes. Never run `zpool upgrade` as part of migration, because it blocks a return to TrueNAS.
5. Keep a backup of anything irreplaceable until the migration has run cleanly for a while.

## Sharing

NFS is the primary protocol and is managed by ZFS itself; SMB is optional and managed through Samba's own config. Each protocol has one owner, so settings never fight.

**NFS (primary)**

- `nfs-kernel-server`, NFSv4 only by default (v3 off unless a client needs it)
- Exports set per dataset with the ZFS `sharenfs` property, so share settings travel with the data
- Exports restricted to the LAN subnet chosen in the wizard, never `*`
- `fnas` writes `sharenfs` values; nothing edits `/etc/exports` by hand

**User ID matching:** NFS trusts numeric user IDs, so the same person needs the same UID on the NAS and on every client. FellowshipNAS offers two modes per share:

| Mode | How it works | Use when |
| --- | --- | --- |
| Matched users | UIDs kept identical across machines | Personal folders, your own Linux machines |
| Shared folder | `all_squash` to one fixed `anonuid`/`anongid`, and Samba `force user`/`force group` to the same account | Media, family shares, anything everyone can write |

**SMB (optional)**

- `fnas` writes one file per share, plus a generated master file that lists them. `smb.conf` includes only that master file, because Samba can't include a whole directory. ZFS's `sharesmb` property is not used.
- `wsdd2` so shares show up in Windows Network, and Avahi for macOS
- SMB passwords set alongside the system password from the Users app (`smbpasswd`)
- `vfs_shadow_copy2` mapped to Sanoid's snapshot names, so ZFS snapshots appear as "Previous Versions" in Windows File Explorer. See the Phase 2 test below. `shadow:snapprefix` keeps the real names, because Sanoid's period suffix can't be rebuilt from a time format. Snapshots must be taken by `sanoid.service`, which runs in UTC: a hand-run `sanoid` uses local time, and Samba can't open those.

**One dataset on both protocols** works when it uses `acltype=posixacl` and `xattr=sa` (set by default), and the web UI warns if a share is set to "Matched users" on NFS while SMB users have no matching UID.

## Snapshots, backup, health and alerts

Snapshots run on a fixed schedule from day one, and every warning a disk or pool can raise ends up in one alert pipeline.

**Snapshots: Sanoid**

- `sanoid` package, config in `/etc/sanoid/sanoid.conf` generated by `fnas`
- Default policy per share: hourly kept 24, daily kept 30, monthly kept 6 (editable per dataset)
- Samba's "Previous Versions" and the file manager's version panel both read Sanoid's snapshot names

**Backup: Syncoid**

Snapshots protect against deleted files, not against losing the pool. v1 includes one optional scheduled `syncoid` job to a second local pool, such as a USB disk. A failed or overdue backup raises an alert. Off-site replication stays out of scope.

**Health monitoring**

| Source | Watches | Package |
| --- | --- | --- |
| `smartd` | Disk SMART errors, reallocated sectors, temperature | `smartmontools` |
| ZFS event daemon (`zed`) | Pool degraded, checksum errors, scrub results, resilver done | `zfs-zed` |
| FellowshipNAS checker | Pool over 80% full, snapshot job failed, last scrub too old, backup overdue, ZFS module missing for a kernel | custom systemd timer |

**Alerts:** all sources call one script, `fellowship-nas-notify`, which sends to whatever channels are set up:

- Email through `msmtp` (any SMTP account)
- Push through ntfy (public server or self-hosted)
- Always: the dashboard's notification centre, stored locally

The Alerts app has a "Send test alert" button so setup can be checked in one click.

## Web UI

Cockpit is the engine and FellowshipNAS Desktop is the face: Cockpit handles login, permissions and running system commands, while custom apps provide the DSM-style experience.

**Why Cockpit underneath**

- Logs in with normal Linux accounts (PAM), so no separate user database
- Its JavaScript API (`cockpit.js`) runs commands, reads files and watches D-Bus with the logged-in user's permissions, through `sudo` or polkit for admin actions
- Serves over HTTPS on port 9090. Its automatic certificate is normally self-signed, so browsers will warn until the user trusts it or adds their own.
- Its built-in pages (logs, services, networking, updates, terminal) stay reachable as an "Advanced" app, so nothing has to be rebuilt

**What Cockpit doesn't provide:** its stock Storage page doesn't appear to manage ZFS, and it has no share or snapshot pages. Third-party ZFS plugins have reported problems on Debian and newer ZFS, so they aren't a dependency. Until the custom apps exist, the NAS is managed with `fnas` over SSH.

**Build order:** each app is a self-contained component that first ships as a plain Cockpit page. The windowed shell comes afterward and hosts the same components. That leaves a usable NAS UI at every step, and the shell can slip without blocking anything.

**FellowshipNAS Desktop (the shell)**

- Packaged as a Cockpit plugin (`/usr/share/cockpit/fellowship-desktop/`) set as the default landing page (confirm how Cockpit supports this)
- Desktop with wallpaper and icons, a taskbar with open windows and a clock, a start-style app launcher, and a notification centre
- Windows can be moved, resized, minimised, maximised and stacked; layout is remembered per user
- On a phone, windows open full-screen and the taskbar becomes a bottom bar
- Built with plain TypeScript and a small UI library (decided in Phase 4), bundled with esbuild

**Apps in v1**

| App | What it does | Built on |
| --- | --- | --- |
| Files | Desktop-style file manager (spec below) | Decided by the transfer test |
| Storage | Pools, datasets, disks, scrubs, layouts, disk replacement | Custom, calls `fnas` |
| Shares | NFS and SMB shares, access rules, user ID mode | Custom, calls `fnas` |
| Snapshots | Schedules per dataset, browse, restore, backup job | Custom, calls `fnas` |
| Users | Accounts, groups, SMB passwords | Custom, calls `fnas` |
| Health | SMART per disk in plain language, temperatures | Custom, reads `smartctl --json` |
| Alerts | Email and push setup, test button, history | Custom |
| Setup | NAS setup wizard, rerunnable for new disks | Custom |
| Advanced | Cockpit's own pages and terminal | Cockpit, unchanged |

**Files transfer test (before any Files work)**

Build a throwaway Cockpit page that does the following on modest hardware:

- Upload and download a 10 GB file, and survive Cockpit's idle timeout
- Stream a video for preview, with seeking
- Generate thumbnails for a folder of 5,000 images
- Download a large folder as a zip

Also evaluate the Cockpit project's `cockpit-files` plugin as a base. If `cockpit.js` alone can't pass this test, add a small local file service that runs as the logged-in user, and record that decision here. Revision 1's "no server-side code" rule applies only if the test passes.

**Files app spec** (the centrepiece)

- Sidebar: pools and datasets, shares, home folder, favourites, recent
- Toolbar: back/forward/up, breadcrumb path, search, grid/list toggle, sort
- Grid view with thumbnails for images and video; list view with name, size, modified, owner, permissions
- Multi-select with click, Shift and Ctrl; drag and drop to move, Ctrl-drag to copy
- Right-click menu: open, download, rename, copy, cut, paste, delete, compress, properties, "Previous versions"
- Upload by dragging from the computer into the window; download folders as zip
- Quick look preview for images, video, audio, PDF and text with the space bar
- Keyboard shortcuts matching common desktop file managers
- **Previous versions panel:** for any file or folder, a timeline of ZFS snapshots that contain it, with preview and "Restore a copy" or "Replace"
- File operations run as the logged-in user, so Linux permissions are always respected

**Dashboard widgets** (on the desktop): pool health and capacity bars, disk temperatures, active shares, last scrub, snapshot and backup, recent alerts.

**Look and feel:** FellowshipOS's own palette, icon set and wallpaper; light and dark themes. Inspired by the windowed feel of DSM and FygoOS, but no copied icons, layouts or artwork.

## Repository layout and packaging

FellowshipNAS lives on the `nas-edition` branch. This repo is a flat live-build project, so the NAS work goes in a new `nas/` folder, plus small changes to the existing build.

```
auto/config                  installer and preseed boot options
build.sh                     build Fellowship packages; remove the local repo from the LXC template
config/includes.chroot/
  usr/local/bin/fellowshipos-setup       new role option and retry message
  usr/local/lib/flavors/nas-node.sh      new flavor
  usr/share/fellowshipos/sources.list    apt sources restored after install
  etc/systemd/system/fellowshipos-setup.service
config/hooks/normal/0800-installer-remove-list.hook.binary
config/preseed/fellowshipos.cfg.installer
CLAUDE.md                    rules for Claude Code
docs/
nas/
  packages/
    fellowship-nas/          meta-package: depends, ZFS defaults, kernel hook
    fellowship-nas-smb/      Samba, discovery service, shadow copy config
    fellowship-nas-tools/    fnas CLI: notify, checker, firstrun, share helpers
    fellowship-desktop/      Cockpit plugin: desktop shell + all apps
  theme/                     icons, wallpaper, colour tokens
  tests/
    vm/                      vm.sh: install from the ISO's boot menu with 4 virtual disks
    unit/                    fnas tests that never touch a real disk
```

**Build:** each package builds with `dpkg-buildpackage`; the desktop is bundled first with esbuild. The ISO carries the built `.deb` files as a local apt repo for v1.

**Versioning:** all FellowshipNAS packages share one version number, so the role always pulls a matching set.

**Helper CLI:** the web apps call small, well-tested scripts in `fellowship-nas-tools` (for example `fnas share create`) instead of building shell commands in JavaScript. That keeps risky logic in one testable place and makes the CLI usable over SSH too. Every argument is validated, because these scripts are the privilege boundary.

## Security model

FellowshipNAS is built for a trusted home LAN; nothing is exposed to the internet by default.

- **Two roles:** members of a `nas-admins` group can manage storage, shares and users; everyone else only sees the Files app for folders they already have permission to. The role adds the wizard's first user to `nas-admins`.
- **Privileged actions** go through polkit rules that allow `nas-admins` to run the `fellowship-nas-tools` scripts, rather than broad `sudo` rights.
- **Web access:** HTTPS only on port 9090; firewall (`nftables`) allows 9090, NFS and SMB from the LAN subnet only.
- **Remote access:** not built in. The recommended route is a VPN such as WireGuard or Tailscale, documented but left to the user.
- **Destructive actions** (destroy pool, delete dataset, roll back snapshot) need a typed confirmation, and the confirmation lists every snapshot that will be lost along with the dataset.
- **Updates:** `unattended-upgrades` for Debian security updates, including the kernel, with no automatic reboot.

## Build phases and test checklist

Work moves in seven phases, and no phase starts until the previous gate passes in a VM with four virtual disks.

| Phase | Work | Gate |
| --- | --- | --- |
| 0. Base install | Debian live installer, wizard changes, `nas-node` flavor stub | Install from ISO, reboot, and settings persist; LXC template still builds |
| 1. Storage | ZFS packages, kernel hook, `fnas` pool commands, pool import | Create and import a pool from the CLI; real TrueNAS pool imports read-only |
| 2. Sharing | NFS, SMB, user ID modes | Mount from Linux and Windows; "Previous Versions" works with Sanoid names |
| 3. Protection | Snapshots, backup, health, alerts, config copy | Failure tests pass from the CLI; a reinstall restores config |
| 4. Core apps | Files transfer test, then Storage, Shares, Health and Setup as plain pages | Transfer test decided; full setup works from a browser |
| 5. Files and remaining apps | Files, Snapshots, Users, Alerts | Restore a file from a snapshot in the browser; phone browser usable |
| 6. Desktop and release | Windowed shell, theme, signed hosted apt repo | Apps run in windows; updates arrive from the hosted repo |

Phases 0–3 give a working NAS managed with `fnas` over SSH; phases 4–6 add the web experience on top.

**Failure tests (run in the VM before every release)**

Install and role:

- [ ] Install to disk from the ISO, reboot: wizard runs once and settings persist
- [ ] Boot the ISO live: the first-boot wizard does not run
- [ ] Cut the network during the role install: failure is shown, and the retry command completes it
- [ ] Enable Secure Boot: role stops with a clear message
- [ ] Try the role in the LXC template: role stops with a clear message

Pools:

- [ ] Reinstall the OS with a pool attached: the wizard offers import, and no disk is offered for erasing by default
- [ ] Reorder the virtual disks: pool still imports
- [ ] Pull one disk from a mirror and from a RAIDZ1 pool: pool shows degraded and an alert arrives
- [ ] Replace the disk through the guided flow and resilver: pool returns to healthy
- [ ] Pull two disks from RAIDZ1: the UI reports the failure clearly and nothing is destroyed automatically
- [ ] Fill a pool past 80%: a warning alert arrives

Updates:

- [ ] Reboot after a kernel update: pool imports and shares come back
- [ ] Force a ZFS module build failure: alert arrives, and the system boots the last good kernel with its pool

Data recovery:

- [ ] Restore a deleted file from a snapshot in the Files app and through Windows Previous Versions
- [ ] Restore a dataset from the `syncoid` backup
- [ ] Restore config after a reinstall: shares, schedules and UIDs return

Access:

- [ ] Mount from an NFS client with a mismatched UID: a clear permission result, not a silent failure
- [ ] Write to a "Shared folder" over NFS and SMB: each protocol can edit the other's files
- [ ] Log in as a non-admin: Files shows only permitted folders and admin apps are hidden

## Open questions and risks

**Still to confirm**

- [ ] How does Cockpit let a plugin replace the default landing page?
- [ ] Does your TrueNAS pool import read-only on Debian's ZFS?
- [ ] Where will the signed apt repo be hosted?

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Wrong disk chosen in the installer | Data loss | Partitioning is never preseeded, so a person always picks the disk; docs recommend unplugging data disks |
| Existing pool mistaken for empty disks | Data loss | Pool scan runs first; ZFS labels are flagged |
| ZFS module fails to build after a kernel update | Pool not mounted after reboot | Post-install check, last good kernel as boot default, alert |
| Role install fails on first boot | Half-built system | Network check first, safe to rerun, separate completion marker |
| Bug in pool or share scripts | Data loss | All destructive logic in `fellowship-nas-tools`, tested only in VMs, typed confirmations |
| Cockpit can't handle large file transfers | Files app needs a redesign | Transfer test runs before Files work starts |
| Pool loss | Data loss | Scheduled backup to a second pool, with overdue alerts |
| Web desktop scope grows | Months instead of weeks | Apps ship as plain pages first; shell is last and can slip |
| UID mismatch on NFS | Permission errors, confusing for users | Per-share user ID mode with a clear warning |
| Look drifts too close to Synology | Legal trouble if sold | Own icons, palette and layout from the start |

**Next step for Claude Code:** "Read `CLAUDE.md` and this plan, then start Phase 3." Phase 1 still needs the read-only import of a real TrueNAS pool, and Phase 2 still needs a real Windows client: mount a share, then check Previous Versions.
