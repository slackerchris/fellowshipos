# FellowshipOS

FellowshipOS builds a Debian Trixie live ISO and a Proxmox LXC template for amd64 homelab systems. **FellowshipNAS** is its full-install NAS role. It manages disks, ZFS pools and traditional RAID arrays, shares, snapshots, backups, and health from a browser desktop with movable windows, status widgets, light and dark appearances, and four themes chosen in the account menu.

[Build](#build) · [Install FellowshipNAS](#install-fellowshipnas) · [Manage the NAS](#manage-the-nas) · [Deploy to Proxmox LXC](#deploy-to-proxmox-lxc) · [Develop and test](#develop-and-test)

## Build

On a Linux host with Docker, internet access, and at least 10 GB of free space, run this from the repository root:

```bash
./build.sh
```

| Output | Use |
| --- | --- |
| `live-image-amd64.hybrid.iso` | Try FellowshipOS live or install it on physical hardware or a VM |
| `fellowshipos-trixie-amd64-1.0_lxc.tar.gz` | Create a general-purpose Proxmox container |

The build packages the NAS tools and graphical installer, runs Debian `live-build` in Docker, and packages the LXC root filesystem from the resulting chroot. See `build.log` for build and packaging errors. The template version is set in [build.sh](build.sh). If it changes, update the expected filename in [create-ct.sh](proxmox/create-ct.sh) as well.

## Install FellowshipNAS

1. Boot the ISO and choose **Install FellowshipOS NAS (graphical)** on a UEFI system. Use the local screen or open the displayed address on another computer and enter the pairing code.
2. Select the OS disk and review the layout. The graphical installer uses LVM for the system and can leave a separate data partition on disks of at least 64 GiB. It requires a typed erase confirmation and writes only to the selected disk. Create storage pools after installation from the NAS desktop.
3. Reboot and complete the first-boot wizard. The graphical installer preselects **NAS / Home Server**. This step needs access to the Debian mirrors. The current NAS role setup stops if Secure Boot is enabled.
4. Open the HTTPS address printed by the wizard and sign in with your local NAS account. The default certificate is self-signed. **Settings → Certificate** shows its fingerprint and accepts a replacement.
5. On the first admin login, **Setup** guides you through storage, the LAN subnet, people and shares, alerts, and backup. Skip it or reopen it from **Settings → Setup**.

The ISO also has a live session and a Debian text installer for other FellowshipOS roles. On a full text install, you can select the NAS role in the first-boot wizard. The first-boot wizard runs on installed systems and LXC containers. The NAS role requires a full install with access to disks and the ZFS kernel module.

## Manage the NAS

| App | What you can do |
| --- | --- |
| **Files** | Browse the shares your own account can use, open folders, and preview pictures, video, audio, PDFs and text, with download. Where the share allows it you can also upload, make folders, rename, delete, copy, cut and paste, and move by dragging or with **Move to…**. Pick several at once to delete, move or download them together as a zip. Search the whole share by name, and add Kind, Owner and Permissions columns; when you can't change something, Files says why. A share you can't use never appears or answers, and one you can only read hides the changes and refuses them anyway. Earlier versions from a snapshot come next. |
| **Storage** | Create, scan, import, check, and grow ZFS pools and RAID arrays, choosing XFS or ext4 for an array. Repair an array's mismatched blocks after a check. Replace or identify disks and follow background jobs. **Details and history** shows space use, compression, a Week or Year chart, and a fill estimate after at least three days of history if usage is rising. |
| **Users** | Manage local accounts, groups, administrators, and SSH eligibility. Password changes through FellowshipNAS set the Linux and Samba passwords together. |
| **Shares** | Share over SMB, NFS, or both. Grant access to everyone or give selected users and groups read or write permissions. Configure SMB guest access, recycle bin, Time Machine, and Previous Versions, or set NFS client rules and identity mapping. |
| **Snapshots & backup** | Set snapshot retention and schedule a backup to a second ZFS pool. Restore a missing share from that backup. Array shares show these controls disabled, with the reason. |
| **Health & Alerts** | See what needs attention at a glance, from the verdict banner through cards for CPU, memory, load, pools, disks, and services, then drill down. The system and each pool draw Week or Year charts from what the hourly check recorded. View local alerts and configure optional email or ntfy delivery. |
| **Settings** | Manage your password, sessions, certificates, system disk space, storage check schedules, SSH, and power controls. |

**Available today.** ZFS pools, traditional RAID arrays and Unraid-style pools (mergerfs with SnapRAID parity) are the implemented storage engines. SMB and NFS are the implemented share protocols. The desktop shows planned choices with their availability. The Files app browses, downloads and previews; uploading, file operations and snapshots come next, followed by more share protocols and Btrfs. See the [architecture plan](docs/fellowship-nas-plan.md) for scope and verification gates.

### Storage and shares

Pools mount at `/mnt/POOL`, with shares at `/mnt/POOL/shares/NAME`. ZFS supports single disks, mirrors, RAIDZ1/2/3, and hot spares. Traditional RAID supports RAID 1, 5, 6 and 10 with hot spares, on XFS or ext4, as one array per pool. Unraid-style pools keep whole files on each XFS disk, joined by mergerfs, with one or two SnapRAID parity disks synced nightly; disks may differ in size, and idle ones spin down. The graphical installer's eligible data partition can hold a single-disk pool. For the OS volume itself, **Settings → System space** can allocate free space from its LVM volume group.

```bash
sudo fnas disk list
sudo fnas pool scan
sudo fnas pool layouts SERIAL1 SERIAL2 SERIAL3 SERIAL4 SERIAL5
sudo fnas pool create tank raidz2 SERIAL1 SERIAL2 SERIAL3 SERIAL4 SERIAL5 --width 4 --spares 1 --confirm "erase 5 disks"
sudo fnas pool create vault raid6 SERIAL1 SERIAL2 SERIAL3 SERIAL4 --engine mdadm --filesystem xfs --confirm "erase 4 disks"
sudo fnas pool scrub vault
sudo fnas job list
```

Use disk serials or stable `/dev/disk/by-id/` paths to identify pool members. Disks with existing data require `--wipe` in addition to the erase confirmation. Run `fnas pool grow --help` or `fnas pool replace --help` for maintenance options. Add `--json` before a supported command for machine-readable output.

```bash
sudo fnas share create media tank --everyone --nfs --smb
sudo fnas share create docs tank --user sam=read --group family=write --smb
sudo fnas share smb media --recycle on --recycle-days 14
sudo fnas share nfs media --client 192.168.1.0/24=ro
```

Create the referenced users and groups before assigning them to a share. Shares for everyone map access to a common account so SMB and NFS clients can edit the same files. Set the share subnet during Setup. NFS client rules can allow other private networks or hosts. NFS clients using per-user access need numeric IDs that match the NAS. Changing a share's access can rewrite existing file permissions. Deleting a share destroys its dataset and snapshots and requires typed confirmation.

### Accounts and SSH

Desktop administrators belong to `nas-admins`. Regular users can change their own password in **Settings → Your account**, and their own light or dark and theme in **Settings → Appearance**, which the NAS keeps with the account so it follows them to another browser. Desktop and SSH permissions are separate. SSH accepts **Shares and SSH** accounts through `nas-ssh`, and root SSH login is disabled.

Manage public keys under **Users → Change**, and control the service and password authentication from **Settings → SSH**. Add a key for an enabled administrator with SSH access before turning off password authentication.

```bash
sudo fnas user create sam --kind shares --password-stdin
sudo fnas user set sam --kind ssh --admin on
sudo fnas user keys sam --set-stdin < ~/.ssh/id_ed25519.pub
sudo fnas ssh show
sudo fnas look show sam
```

`fnas share roots USER` prints the shares an account can use, where each one is on disk and whether it keeps snapshots. Files asks it rather than working out a path, and WebDAV will ask the same.

The network-facing `fellowship-nas-web` process runs as an unprivileged account. A separate local helper authenticates with PAM, runs approved `fnas` commands for authorized users, and serves files for the Files app as the signed-in person, in a child running with that person's own account, inside the share roots they were given.

### Snapshots, backup, and recovery

ZFS shares default to 24 hourly, 30 daily, and 6 monthly snapshots. Traditional RAID arrays and Unraid-style pools have no snapshots, and the backup job covers ZFS pools only. With SMB Previous Versions enabled, Windows can expose those snapshots. The single scheduled backup job copies a source pool's shares to a second ZFS pool at 03:00 local time. A share restore leaves an existing share in place.

```bash
sudo fnas snapshot list media
sudo fnas backup set tank usb
sudo fnas backup show
sudo fnas backup restore media
```

Pools also save configuration for a reinstall. It includes share settings, accounts and numeric IDs, groups, the LAN subnet, the backup definition, alert channels, and SSH settings and public keys. Set user passwords and any SMTP password again after restoring.

```bash
sudo fnas pool scan
sudo fnas pool import tank
sudo fnas config restore tank
```

To inspect a pool first, import it with `--readonly`. Configuration restore requires a writable imported pool. A pool that was never exported may require `--force` to import. Make sure its former host is offline before using that option. The new install preserves saved settings until you restore them or explicitly take ownership with `fnas config adopt`.

## Deploy to Proxmox LXC

Copy the template to the Proxmox node:

```bash
scp fellowshipos-trixie-amd64-1.0_lxc.tar.gz root@PROXMOX:/var/lib/vz/template/cache/
```

From a checkout on the Proxmox node, use [create-ct.sh](proxmox/create-ct.sh):

```bash
cd proxmox
./create-ct.sh --flavor base
```

The helper supports `base`, `docker`, `monitor`, and `storage` container presets. For FellowshipNAS on Proxmox, install the ISO in a VM.

## Develop and test

Run the Python and Go suites from the repository root. The Go modules require Go 1.24 or later. The web helper uses cgo and PAM development headers.

```bash
python3 -m unittest nas/tests/unit/test_fnas.py
(cd installer && go test ./...)
(cd nas/packages/fellowship-nas-web && go test ./...)
```

[CI](.github/workflows/ci.yml) runs those suites on every push and pull request,
then checks `gofmt` and `go vet` in both Go modules, type-checks and bundles both
frontends, and runs ShellCheck on the shell scripts. The phase gates in the plan
still run by hand in a VM, since they need real disks and hours of patience.

For desktop development, build the frontend and run the web server and helper in Docker with sample `fnas` responses:

```bash
(cd nas/packages/fellowship-nas-web/web && npm ci && npm run build)
nas/packages/fellowship-nas-web/dev/run.sh
```

Open `http://localhost:8080`. The script lists the development accounts. Run `npm run watch` in the web directory to rebuild while editing.

For disk and reinstall testing, use the [QEMU/KVM VM harness](nas/tests/vm/vm.sh) with a built ISO, OVMF, and a dedicated VM work directory. Its modes and environment variables are documented at the top of the script. The installed guest's HTTPS is forwarded to `https://localhost:8443/`. Set `FELLOWSHIP_KEEP_DATA=1` when reinstalling a test VM with data disks you want to preserve.

| Path | Purpose |
| --- | --- |
| [auto/config](auto/config) and [config/](config/) | Live-build profile, boot menu, package lists, first-boot wizard, and role scripts |
| [installer/](installer/) | Graphical NAS installer in Go and TypeScript |
| [nas/packages/](nas/packages/) | NAS packages, Python CLI, Go services, browser desktop, and scheduled jobs |
| [nas/tests/](nas/tests/) | Python unit tests and VM harness |
| [docs/](docs/) | Architecture plan and [November 9 review notes](docs/fellowship-nas-changes.md) |
| [CHANGELOG.md](CHANGELOG.md) | What changed, and what is waiting to be released |

## Troubleshooting

- **Build failed:** Check `build.log`, Docker, and access to Debian mirrors and GitHub. The script checks that both output files exist before reporting success.
- **First boot or role setup failed:** Check `/var/log/fellowshipos/setup.log`. Use `sudo fellowshipos-setup` to run the wizard manually or `sudo homelabos-flavor nas-node` to retry the NAS role. If setup reports that ZFS was built for a newer kernel, reboot before using the NAS.
- **Desktop unavailable:** Run `systemctl status fellowship-nas-web fellowship-nas-helper` on the NAS and check the corresponding service logs.

This repository currently has no license file.
