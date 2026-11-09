# FellowshipNAS project overview

Baseline as of November 9, 2026 · `nas-edition` at `3b3c6a6`

FellowshipNAS is the NAS role in FellowshipOS, a Debian Trixie-based homelab OS. FellowshipOS builds a bootable ISO and a Proxmox LXC template. The NAS role runs on a full install on a physical machine or a VM. At this baseline, it manages ZFS pools, serves files over SMB and NFS, and provides a browser desktop for administration. Traditional RAID is the next planned development phase.

| Reference | What it covers |
| --- | --- |
| [Repository README](../README.md) | Build, install, deployment and CLI examples |
| [Architecture plan, revision 4](fellowship-nas-plan.md) | Design decisions, phase gates and future architecture |
| [November 9 VM review](fellowship-nas-changes.md) | Original review findings and requests. The revised plan resolves some items marked Open there. |
| This overview | Behavior present in the checkout, where it lives and what has been verified |

## Install and first run

1. Build the ISO with `./build.sh` on a Linux host with Docker and network access. The build also produces the LXC root filesystem tarball. The ISO carries a local apt repository for the NAS packages, with Debian supplying dependencies during role setup.
2. Boot the ISO and choose **Install FellowshipOS NAS (graphical)**. This path uses UEFI. Use the local installer screen or pair a remote browser using the displayed code. The installer lists disks and requires a typed erase confirmation for the selected OS disk. It sets up the system on LVM and can leave a separate data partition on larger disks. The ISO also has a Debian text install path.
3. After reboot, finish the first-boot wizard. It carries forward the NAS selection from the graphical installer, layers the role, adds the initial administrator to `nas-admins` and prints the desktop address. The wizard logs to `/var/log/fellowshipos/setup.log`. The role can be retried with `sudo homelabos-flavor nas-node`.
4. Visit `https://NAS-ADDRESS` and sign in with the local account. The default certificate is self-signed. **Settings → Certificate** shows its fingerprint and accepts a replacement. The desktop Setup app opens on an admin's first login. It can be skipped or reopened from Settings and offers recovery when it finds saved settings on an imported pool. Pool creation happens after installation, when the operator chooses to do it.

## How the running system fits together

- The **Preact and TypeScript desktop** runs in the browser. It calls the Go web front over HTTPS. The front runs as `fnas-web`, serves static assets and routes authenticated requests. The root helper listens on a local socket, uses PAM to authenticate local accounts, maintains sessions and runs allowed `fnas` commands. Management requires `nas-admins`; a regular account can use its own account settings.
- **`fnas`**, implemented in Python, handles storage, system settings, users and shares for both the desktop and CLI. The storage engine interface currently dispatches to ZFS. The CLI validates disks and typed confirmations before destructive operations. Long-running operations report progress separately from the browser window.
- **Samba and the Linux NFS server** serve files directly. `fnas` turns share definitions into Samba configuration and `/etc/exports.d/fellowship.exports`. OpenSSH handles shell access under its own `nas-ssh` policy.
- **systemd, Sanoid, syncoid, SMART and ZFS event monitoring** handle scheduled work and health signals. The hourly NAS check records pool usage and tracks health findings. A separate persistent timer runs the configured backup at 03:00 local time.

## What ships at this baseline

| Area | Operator-facing behavior |
| --- | --- |
| Storage | List and identify disks, scan and import ZFS pools, create single-disk or redundant layouts, scrub, grow or replace disks, and follow supported background jobs. Pools mount at `/mnt/POOL`. The eligible data partition on the system disk can be used for a pool. Create Pool shows four engine choices and explains which are available. |
| Pool details | **Storage → pool → Details and history** shows used and free space, compression, a fill estimate, hourly and daily charts, a capacity line and per-point hover values. The breakdown separates shares, snapshots, incoming backups, other usage and free space, with a per-share table and pool diagnostics. A fill estimate requires at least three samples spanning three days and a rising trend. |
| Users and groups | Manage local accounts, passwords, account kind, administrators and groups. A password change through FellowshipNAS sets the Linux and Samba passwords together. Shares can grant access to everyone or give selected users and groups read or write access. |
| Sharing | SMB and NFS can be enabled per share. SMB has guest read access on eligible shares, recycle bin, Time Machine and Previous Versions settings, plus server-wide controls. NFS has private client networks or hosts, read/write rules, identity mapping and global options including optional NFSv3 and fixed ports. For NFS access based on individual users, the client and NAS must agree on numeric IDs. |
| Snapshots and backup | Sanoid schedules ZFS snapshots per share. The default retention is 24 hourly, 30 daily and 6 monthly snapshots. One configured ZFS-to-ZFS backup job copies a pool's shares and snapshots to a second pool. The desktop manages schedules and can restore a missing share from backup. File-level browsing and restore in the desktop are planned. |
| Health and alerts | Health opens on a verdict banner, then cards for CPU usage, memory and load, and for the pools, disks and services, each with a **View details** link. A strip along the bottom shows the backup's last result and whether any alert is active. The desktop's Health widget is the same panel in miniature and opens it, and its Disks widget maps the drive bays by level, each opening that drive's row. The system and each pool draw Week or Year charts from what the hourly check recorded. A check that cannot run shows its reason. Alerts go to the desktop notification center and local history, with email and ntfy available as extra channels. |
| SSH | **Users → Change** manages each eligible user's public keys. **Settings → SSH** controls the service and password logins. Health shows an intentional shutdown as **Turned off in Settings**. See the SSH policy below. |
| Settings | Includes own password, hostname, time zone, certificate, system volume growth, power controls, sessions and rerunning Setup. Network and update management are planned. |

The desktop's design rule is to keep unavailable features visible and explain their state. Access to management screens and actions is governed by the user's role.

### Pool history and configuration recovery

The hourly check keeps roughly a week of hourly points and up to 400 days of daily points in `/var/lib/fellowship-nas/usage.json`. The UI presents Week and Year views. The projection is a straight-line estimate using daily samples when available, with hourly samples as a fallback. It starts with an empty state on a new install. Usage history lives on the OS disk, outside the pool configuration restored by `fnas config restore`. The storage list and usage history use pool-level `zpool` accounting. The space breakdown uses the root dataset's `zfs` accounting, so the displayed capacities can differ. On the VM, `tank` showed 25 GB in Storage and 16 GB in Details and history.

A ZFS share lives under `/mnt/POOL/shares/NAME`. The pool's `fellowship-config` dataset holds share definitions and a copy of the appliance configuration. The copy includes the LAN subnet, users and groups with numeric IDs, share permissions, SMB and NFS settings, the backup definition, alert channel settings, SSH settings and public keys. Set user passwords and any SMTP password again after restoring. A fresh install preserves configuration owned by the previous install until the operator restores or explicitly adopts it.

To recover settings, import the existing pool, then use the desktop restore flow or `sudo fnas config restore POOL`. If the former host never exported the pool, a forced import may be needed after the old host is offline. For initial inspection of an unfamiliar pool, the CLI supports `sudo fnas pool import POOL --readonly`. Restoring configuration requires an imported writable pool.

### SSH policy

Desktop administration and SSH access are separate permissions. **Shares and SSH** accounts managed by FellowshipNAS join `nas-ssh`, and OpenSSH uses `AllowGroups nas-ssh`. Shares-only users cannot open a shell or a tunnel, and root SSH login is disabled. Refreshing SSH on upgrade derives membership from existing eligible users with login shells. Accounts added outside `fnas` can be picked up with `sudo fnas ssh refresh`.

The key editor validates public keys and rejects leading authorized-key options such as `command=`. When SSH is enabled, switching off password authentication requires an enabled administrator with SSH access and a key. `fnas` generates `/etc/ssh/sshd_config.d/fellowship.conf`, validates it with `sshd -t`, and restores the previous drop-in if validation fails before reloading the service. The appliance-managed, share-scoped SFTP feature is a separate Phase 12 deliverable.

## Decisions and roadmap

Cockpit was the Phase 4 prototype. The shipped desktop and privilege split arrived in Phase 5. Phases 6 and 7 added the graphical installer and expanded ZFS management. Phases 8 and 9 made feature states visible and added user, group and share controls. After Phase 9, `7f80680` added the Health overview, browser cache update fix and login text. `3b3c6a6` added pool history and SSH lockdown.

| Phase | Planned work |
| --- | --- |
| 10 | Traditional RAID via mdadm. RAID 1, 5, 6 and 10 with spares, ext4 or XFS (XFS recommended), import, monitoring, monthly checks, growth and guided replacement. |
| 11 | Unraid-style pools with mergerfs and SnapRAID on XFS disks. Add scheduled parity sync and scrub, disk recovery and spin-down support. |
| 12 | Share-scoped SFTP, FTP and WebDAV using the existing users and share permissions. FTPS is the default for FTP, with plain FTP an explicit opt-in. |
| 13 | Files app with snapshot browsing and file-level restoration. |
| 14 | Btrfs mirror layouts and snapshot, scrub and replacement workflows. |
| 15 | Network and update controls, theme and icon work, and a hosted signed apt repository. |

DLNA and iSCSI are later additions. Apprise, multiple external alert channels and severity routing are also planned. The plan describes a per-user file helper for Files and WebDAV, and multiple backup jobs across engines. The shipped system currently has two web processes and a single ZFS backup job.

## Development and verification

Run these from the repository root. Go tests require Go 1.24 or later, and the NAS web build uses cgo and PAM development headers.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest nas/tests/unit/test_fnas.py
(cd installer && go test ./...)
(cd nas/packages/fellowship-nas-web && go test ./...)
```

For desktop development, run `npm ci` and `npm run build` in `nas/packages/fellowship-nas-web/web`, then run `nas/packages/fellowship-nas-web/dev/run.sh` from the root. The script uses real web and helper processes, PAM and sample `fnas` responses in a Docker container at `http://localhost:8080`. Run `npm run watch` in the web directory for iterative UI work. The VM harness, `nas/tests/vm/vm.sh`, supports configurable disk sizes and order. Use a dedicated `FELLOWSHIP_VM_DIR` and set `FELLOWSHIP_KEEP_DATA=1` when testing a reinstall with preserved data disks.

[CI](../.github/workflows/ci.yml) runs the Python and Go suites, `gofmt` and `go vet`, both frontend builds and ShellCheck on every push. The phase gates below still run by hand in a VM.

**Checked for this overview.** The checked-out branch is `nas-edition` at `3b3c6a6`. All 191 Python unit tests passed on a fresh run. The handoff reports passing Go tests and VM checks. Go was unavailable in the shell used for this overview, and the VM checks come from the supplied handoff.

**Running development VM.** On the host, [https://localhost:8443/](https://localhost:8443/) forwards to the VM's HTTPS port. On November 9, the signed-in desktop showed both `tank` and `usb`. The `tank` details rendered the space breakdown, per-share table and diagnostics. Both Week and Year showed insufficient history to draw a chart, so chart hover remains unverified. Settings showed SSH and password authentication enabled. The guest's exact revision was not checked.

**Reported in the VM handoff.** A shares-only account was refused an SSH login and tunnel, while an eligible account could log in. The actual `tank` view had no usage history. The populated chart screenshot used temporary sample data. The earlier Phase 9 reinstall restored users, numeric IDs, group and share access settings, and SMB and NFS settings.

**Still to verify.** Check chart hover in a browser and run a full reinstall that restores SSH settings and user keys. Verify read-only import of the real TrueNAS pool, Windows Previous Versions, a real NFS client's per-user access, Time Machine from macOS and physical bay identification on the target HBA and backplane. The plan also tracks the signed apt repository's hosting decision and the broader release checklist.

## Source map

| Area | Repository location |
| --- | --- |
| ISO and first boot | [build.sh](../build.sh), [auto/config](../auto/config), [first-boot wizard](../config/includes.chroot/usr/local/bin/fellowshipos-setup), [NAS role](../config/includes.chroot/usr/local/lib/flavors/nas-node.sh) |
| Graphical installer | [installer](../installer) |
| CLI, engine and appliance state | [fellowship_nas Python package](../nas/packages/fellowship-nas-tools/fellowship_nas) |
| Network front, helper and desktop | [fellowship-nas-web](../nas/packages/fellowship-nas-web) |
| Debian packaging and timers | [NAS packages](../nas/packages), [NAS role services](../nas/packages/fellowship-nas) |
| Verification | [Python tests](../nas/tests/unit/test_fnas.py), [VM harness](../nas/tests/vm/vm.sh) |
