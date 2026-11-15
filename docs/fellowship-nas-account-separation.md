# FellowshipNAS: separating NAS accounts from the system

**Status:** being built, in the phases at the end. The direction was agreed by
Chris on 2026-11-15 (option B), and his decisions are recorded below. The
credential reuse in the migration section was checked in a Debian trixie
container on 2026-11-15. Phases 1 (SFTP read-only shares), 2 (the NAS store
and login), 3 (the maintenance interface and updates) and 4 (install, the
recovery key and recovery) are done.

This supersedes the account parts of
`fellowship-nas-accounts-review-2026-11-15.md`. The gaps that review found are
mapped to this design at the end.

## The two kinds of administrator

| | **root** (system administrator) | **NAS admin** |
|---|---|---|
| Owns | The operating system | Everything inside the NAS software: pools, shares, people, alerts, backups |
| Credentials | Its own password, set in the installer | Its own name and password, set in the installer |
| Shell | Yes: the console, and SSH only if root turns it on (keys only) | None. No `sudo`, no shell, no Linux password |
| Reaches the system through | Anything | The maintenance interface only: a fixed list of operations, below |
| Recovers | NAS admin access | Nothing outside the NAS |

They never share a password: the installer refuses the same password for
both. Nobody is in `sudo`, and the installer no longer creates a human Linux
account.

**Recovery** sits outside both accounts. It uses a recovery key that you save
offline at install time, and it works with both passwords lost. See *Recovery*
below.

## NAS people

Every NAS person, admins included, still has a Unix UID and group. Files on
pools, SMB, NFS and SFTP need a numeric owner, and the kernel enforces
permissions by UID. What a NAS person no longer has:

- **A usable Linux password.** The shadow entry is locked (`!`), so PAM,
  `su`, `login` and SSH passwords all refuse them.
- **A shell.** It's `/usr/sbin/nologin`, or the SFTP-only shell when SFTP is
  allowed.
- **Membership in system groups that grant power:** `sudo`, `adm`, `disk`,
  `lxd`, `docker`. Groups that give access to files (`video`, an app's group)
  can still be given, as today.
- **A login home in `/home`.**

Their credentials live in the NAS's own store,
`/var/lib/fellowship-nas/accounts.json` (root, 0600). For each person it
holds:
- name, UID, full name, admin flag, on or off
- the desktop password as a crypt(3) hash
- the date the password was set
- a must-change flag
- whether SFTP is allowed
- SSH public keys, for SFTP

New hashes are yescrypt (`$y$`), as Debian's `chpasswd` writes. Older SHA-512
(`$6$`) hashes copied from `/etc/shadow` are accepted as they are.

Samba keeps its own NT hash in its passdb (`/var/lib/samba/private/passdb.tdb`).
`fnas` always sets both together, so one password works for the desktop and
every share.

**Verifying passwords.** Both are verified to work on trixie:
- The helper (Go) uses libcrypt's `crypt_r` through cgo; it already uses cgo
  for PAM today.
- `fnas` (Python) calls `libcrypt.so.1` through ctypes. Python 3.13 removed the
  `crypt` module, so the standard library can't be used.

**Samba changes** in `smb.conf`:
- Debian ships `unix password sync = yes` and `pam password change = yes`.
  Both are turned off. A password change from a Windows "change password"
  dialog is refused with a pointer to the desktop, because otherwise it would
  try to set a Linux password that no longer exists.
- `obey pam restrictions = yes` stays. It's what makes a turned-off account
  (account expiry) refuse SMB too, and that was verified.

### SFTP: a per-person permission

SFTP isn't an account type any more. It's an **Allow SFTP** switch on each
person, off by default. Anyone with it on:

- joins `nas-sftp`
- is forced into `internal-sftp`: no shell, no forwarding, no tunnels (as today)
- is chrooted to `/var/lib/fellowship-nas/sftp/<name>`, which holds one bind
  mount per share they may use, and nothing else (as today, in `sftp.py`)
- reaches files as their own UID, so the kernel allows exactly what Files and
  SMB allow

A share they have **read-only** access to is bind-mounted read-only in their
view (phase 1, done). A view that's already mounted is checked against the
kernel's mount table and mounted again when someone's access changes.
`nas/tests/integration/sftp-readonly.sh` runs the real view code against a
real sshd, with the share's folder writable by the person on purpose:

- **Read-only share:** listing and download work. Upload, overwrite, rename,
  making a folder and delete are all refused.
- **Writable share:** all seven work.
- **Access changed from write to read on a live view:** upload is refused at
  once.
- **Nothing outside the chroot** is visible.

Passwords for SFTP are checked against the NAS store by a dedicated PAM stack
for sshd: `pam_exec` with `expose_authtok`, calling `fnas auth check`. Keys
come from an `AuthorizedKeysCommand` that reads the store.

## The maintenance interface

The complete list of what a NAS admin can do to the operating system.
Everything else that changes the system is root's.

| Operation | What it may do | Bounds |
|---|---|---|
| Name and time | Set the hostname, the time zone, NTP on or off | Validated as today |
| Certificate | Make a new self-signed certificate, or install one from PEM | Only the desktop's certificate files |
| System space | Grow the system volume into the room left for it | Grow only; no other disk |
| Power | Restart, shut down | Typed confirmation, as today |
| Updates | See which updates are waiting (checked automatically), apply them, restart afterwards | Applying and restarting only on an explicit request; `apt-get upgrade` from the configured sources only; no installing or removing packages, no new sources |
| Network | DHCP or a static address, gateway and DNS on the LAN interface | Rolled back automatically unless confirmed from the desktop after reconnecting (see below) |
| SFTP | Allow or stop SFTP for a person; passwords or keys only for SFTP | Never root's SSH, never a shell |
| Logs | Read the NAS's own logs: fellowship units, Samba, NFS, ZFS, SMART, sshd's SFTP lines | Read-only |
| Support bundle | Download a redacted bundle for a bug report | Hashes, keys and the recovery key's hash left out |

**What it never does:**
- set or read root's password, keys or the recovery key, or turn on root's SSH
- run a shell or any command not on the list
- install or remove packages
- change `sudoers`, PAM (beyond the fixed SFTP stack), sshd (beyond the SFTP
  block), the boot loader, kernel parameters, or the firewall beyond the NAS's
  LAN subnet
- read or write files outside shares

### Updates

- A daily timer (`fellowship-nas-updates.timer`) runs `apt-get update` against
  the configured Debian and FellowshipOS sources and counts what's waiting.
- When something new is waiting it sends an alert: one per new set of updates,
  with security updates named. Health and Settings → Updates show the list.
- **Apply updates** in Settings is the only way they install, and it runs as a
  job with progress.
- If the kernel or a library in use was updated, Settings shows **Restart
  needed** with a **Restart now** button. Nothing restarts by itself.
- There is no `unattended-upgrades`.

### Network changes and rollback

1. The NAS admin edits the settings in Settings → Network and presses
   **Apply**. The helper checks them: a valid address, prefix, gateway and DNS,
   and the gateway in the subnet.
2. `fnas network apply` saves the current configuration and writes the new
   one. It starts a rollback timer (`fellowship-network-rollback.timer`, 120 s
   by default), then applies the change.
3. The desktop shows the new address and a countdown, and tries to reach the
   NAS there.
4. **Confirm** works only from a desktop session that reached the NAS over the
   *new* address and is signed in as a NAS admin. It calls
   `fnas network confirm`, which stops the timer and keeps the change.
5. If no confirm arrives in time, the timer puts the saved configuration back,
   restarts networking, and sends an alert ("Network change rolled back").
6. The rollback runs as a systemd unit, so it happens even if the web server
   or the helper died in the change.

## Where the limits are enforced

1. **The helper**, the only privileged process the desktop can reach.
   - It authenticates people against the NAS store.
   - It knows two roles, person and NAS admin.
   - It checks every request against the list above, with arguments validated
     per operation, not just per area and action as `commands` in `helper.go`
     does today.
   - Anything not listed is refused before `fnas` starts.
2. **`fnas` itself.** Each system-touching command is marked *maintenance* or
   *root only*. The helper always passes the caller (`--caller <name>`), and
   `fnas` refuses root-only commands whenever a caller is given. A mistake in
   one layer is still caught by the other.
3. **The operating system.**
   - NAS people have locked passwords and no shell.
   - `sudo` has no members, and `sudoers` names none.
   - sshd's `AllowGroups` admits only root (when root has turned SSH on) and
     `nas-sftp`, which is forced into `internal-sftp` in a chroot.
4. **The audit log.** Every maintenance operation is written to the journal
   with the NAS admin's name. Power, updates, network and certificate changes
   also go to the alert channels.

## root: access

- **Set in the installer**, on the You step, separately from the NAS admin.
  Nothing else sets it except root itself and recovery.
- **At the console:** always. The status screen above the login prompt says
  "log in as root for system administration".
- **Over SSH:** off by default. Root turns it on itself with
  `fellowship-root-ssh on`, keys only (`PermitRootLogin prohibit-password`),
  after adding a key. A root password over SSH is never accepted. The desktop
  shows whether root SSH is on, read-only.
- **Emergency and rescue modes** use `sulogin`, which asks for root's password.
  Getting root without a password is only possible through recovery.

## Recovery

Physical access alone doesn't authorise anything. Recovery needs the
**recovery key**.

**Creating the key, at install:**
- The installer generates a random key: 128 bits, shown as eight groups of
  five letters and digits, e.g. `K7QPM-3XW9T-…`.
- It's shown once, on its own step before Install. The step offers **Save as
  text file** and **Print**, and can't be passed until the key is typed back
  in, or its last two groups are.
- The system keeps only a hash of it: yescrypt, in
  `/etc/fellowshipos/recovery-key.hash`, root, 0600. The key itself is never
  written to the disk or logs.

**Using it:**
- The boot menu has **FellowshipOS recovery**. It boots to a recovery screen on
  the console, outside the normal system.
- The screen asks for the recovery key, with a growing delay after wrong ones.
  With the right key it offers:
  - set a new root password
  - set a NAS admin's password, or make a person a NAS admin
  - turn root's SSH off
- Every use is logged, and an alert goes out on the next normal start
  ("Recovery was used at the console").

**Making it the only way in at the machine:**
- **GRUB is locked against editing.** GRUB gets a superuser whose password is
  the recovery key (`password_pbkdf2`). The normal entries are
  `--unrestricted`, so the NAS still starts without anyone typing anything. But
  editing an entry (`e`), the GRUB command line (`c`) and the recovery entry
  all need the key. That closes the `init=/bin/bash` route.
- **Emergency and rescue shells need root's password** (`sulogin`).
- **What the recovery key protects, and what it doesn't.** It protects the
  NAS's own recovery path: the recovery boot entry, editing GRUB's entries,
  and GRUB's command line. It doesn't protect the disk. The system disk isn't
  encrypted, so someone who can boot the machine from their own USB stick, or
  take the disk out, can still read and change it, root's password included.
  Disk encryption is out of this phase (Chris, 2026-11-15). Until then, a
  firmware password and a locked boot order are the owner's way to close that
  door, in the machine's own settings.

**Changing the key:** root can make a new one with `fellowship-recovery-key
new`. It shows the new key once, replaces the hash, and updates GRUB's
password. The old key stops working.

**When the NAS admin forgets their password and root still works:** root runs
`fnas user password <name>` at the console, or `fnas user set <name> --admin
on`. Both send an alert. No recovery key is needed.

## Install and first boot

**Installer, You step:**
- The NAS admin: name, full name, password.
- The root password, twice. It must differ from the NAS admin's.

**Installer, Recovery key step:** shown once, saved, confirmed, as above.

**Installer, account step:**
- `fnas user create <name> --admin --password-stdin` inside the new system.
  That writes the NAS store and Samba's passdb together, offline in the
  chroot, since `fnas` and Samba are in the image. This fixes the missing SMB
  password (review gap 1).
- `chpasswd` for root.
- The recovery key's hash, and the GRUB password made from it, written before
  `update-grub`.
- No human Linux account, and no `sudo` membership.

**First boot (`nas-node.sh`):**
- Unchanged in spirit: turn the NAS services on, load ZFS, write the done flag.
- It no longer looks for "the installer's account" in `/etc/passwd`: the store
  already has its admin.
- The console status screen adds a root line: "root: console only" or
  "root: SSH, keys only".

## Migration of an existing NAS

For machines installed before this, whose people are Linux accounts. Chris's
Proxmox VM is one.

### What carries over, verified

Checked on Debian trixie with real `chpasswd`, `smbpasswd` and `smbd` on
2026-11-15:

| Credential | Carries over? | Evidence |
|---|---|---|
| Desktop password: yescrypt `$y$` from `/etc/shadow` | **Yes, copied as it is** | Verified from Go (libcrypt through cgo) and Python (ctypes); right password accepted, wrong refused |
| Desktop password: SHA-512 `$6$` | **Yes** | Same check |
| A turned-off account's hash (`!$y$…`) | Kept as off | A `!` hash verifies as nothing. Migration strips `!` only for accounts that are on, and copies the off state as is. |
| SMB password (NT hash in `passdb.tdb`) | **Yes, untouched** | SMB login still worked after `usermod -L`, shell set to `nologin`, and removal from `sudo` |
| Turned-off accounts and SMB | Still refused | Account expiry refuses SMB through `obey pam restrictions` |
| No Samba hash at all | **No: needs a password set** | Nothing to copy. Typical for the installer's own account today. |
| No usable Linux hash (`*`, empty) | **No: needs a password set** | Nothing to verify against |
| Linux and Samba passwords may differ | **Both keep working; flagged as possible** | The desktop keeps checking the copied Linux hash, and SMB keeps its own NT hash, so whatever worked before still works afterwards. Password dates (`/etc/shadow` field 3 against `pdbedit -v`'s *Password last set*) can flag a possible difference when the Linux one was changed on a later day. They can't prove the two match, and a difference made on the same day isn't seen. |

### Running it

`fnas accounts separate` is run by the current `sudo` admin, over SSH or at
the console. A package upgrade never runs it, because it ends that admin's
`sudo`.

**1. Dry run.** This is the default without `--yes`, and changes nothing. It
prints:

```
Accounts that move to the NAS store (UIDs and GIDs unchanged):

  chris   uid 3001  gid 3001  admin  groups: users,video
    desktop password  carries over ($y$)
    SMB password      NEEDS SETTING: no Samba password exists
    leaves            sudo, adm  (sudo ends for this account)
  anna    uid 3002  gid 3002         groups: family
    desktop password  carries over ($y$)
    SMB password      carries over
    note              possibly different passwords: the Linux one was
                      changed after the Samba one (2026-10-02 vs
                      2026-09-14). Both keep working as they do now.
                      They become one again the next time anna's
                      password is set.
  ...

Must be done before your sudo access ends:
  - set a root password (asked next)
  - set a password for chris, who has no SMB password (asked next,
    or choose "set it later in the desktop")

Password dates only flag *possible* differences between a desktop
and an SMB password. They can't prove two passwords match, and a
change made on the same day as the other isn't seen. Nothing here
changes a password that works today: whatever works for the desktop
and for shares now keeps working after this.
```

**Passwords that already differ** aren't forced together. During migration,
the desktop hash and the SMB hash are each copied as they are, so a person
whose two passwords differ keeps both working, exactly as before. The next
time their NAS password is set (by them in Settings, by a NAS admin, or by
root), `fnas` writes it to the store and Samba together, and from then on it's
one password. Users shows a note on that person until then: "the desktop and
shares may use different passwords; setting a new one makes them the same".

Accounts the dry run marks **NEEDS SETTING** are dealt with *before* anything
is removed. With `--yes`, the command asks for each one's new password
(twice), or accepts `--later` for people other than the person running it.
The running admin's own credentials must all carry over or be set now,
because their `sudo` ends at the last step.

**2. Root password.** Asked twice, unless root already has one. The command
won't go on without it, so there is always a way into the OS.

**3. Recovery key.** Made and shown once, as in the installer. GRUB's password
is set from it.

**4. Backup** to `/var/lib/fellowship-nas/accounts-before-separate/<time>/`,
root, 0700:
- `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/gshadow`
- `/etc/sudoers` and `/etc/sudoers.d/`
- `/etc/ssh/sshd_config.d/` (fnas's SSH settings)
- `/etc/samba/smb.conf`
- Samba's `passdb.tdb`, copied with `tdbbackup`
- the PAM files the design changes
- `/boot/grub/grub.cfg` and `/etc/grub.d/` files the design changes
- a manifest with every file's checksum

**5. Change, in this order:**
1. Write the NAS store: same names, UIDs, GIDs, groups and admin flags. Copy
   the desktop hashes as they are (minus `!` for accounts that are on). Shell
   kind becomes "Allow SFTP" on.
2. Set any passwords collected in step 1, for both the store and Samba.
3. Check the store: every person is there with the right UID.
4. Switch the desktop login to the store, and restart the helper.
5. **Prove both ways in before taking `sudo` away.** Nothing is removed until
   both of these pass. If either fails, the command undoes steps 1–4, and says
   which one failed.
   - *The new NAS admin login works.* The running admin's NAS password is
     asked once more and sent through the restarted helper's real login, the
     same request the desktop makes. It must return a session with the NAS
     admin role. One designated NAS admin passing is required; any other NAS
     admins are checked against the store only.
   - *Root console access is usable.* Root's password is checked through PAM
     with the `login` service, the same check the console makes. Root's
     account must also be unlocked and unexpired with a working shell, and a
     getty must be enabled on tty1.
6. Lock down the Unix side: lock the passwords, set shells to `nologin` or the
   SFTP shell, remove people from `sudo`, `adm` and other power groups, turn
   off Samba's password sync, and change sshd's `AllowGroups`.
7. Restart Samba and sshd.

**6. Check afterwards**, and print each result:
- Every person's UID and GID match the dry run.
- A sample of files on each pool keeps the same owners (`stat`, before and
  after).
- A desktop login with the running admin's password works through the helper.
- An SMB login works for one migrated person who had a Samba hash, through
  `smbclient` to localhost.
- root can authenticate.

If any of the first three checks fails, the command undoes the change itself
and says why.

**Ownership is never touched.** No `chown` runs anywhere. UIDs and GIDs don't
change, so files on pools, in shares and in old home folders keep their
owners. Home folders stay where they are; they just stop being login homes.

### Undo

`fnas accounts separate --undo [<time>]` puts back the newest backup, or the
one named.

**What it restores:** every file in the backup, exactly as it was, after
checking the manifest's checksums:
- Linux passwords, shells, groups and `sudo` (passwd, shadow, group, gshadow,
  sudoers)
- the SSH settings
- `smb.conf` and Samba's `passdb.tdb`, copied back with `smbd` stopped. That
  this brings back the exact old SMB passwords was verified.
- PAM and GRUB files

It then switches the desktop login back to PAM, and restarts the helper, sshd
and Samba.

**What it doesn't touch:**
- files on pools and in shares (migration never changed them)
- the NAS store, which is left in place, unused, so a later attempt can start
  from it
- root's password, which stays as set, so a way into the OS remains
- the recovery key and GRUB's password, which stay, since they protect the
  machine either way. `fellowship-recovery-key remove` (root) takes them off.

**If desktop login fails after migration:**
1. Sign in as root at the console. Root's password was set in step 2, so this
   works even when no NAS login does. Or use SSH if root turned it on.
2. Run:
   ```
   fnas accounts separate --undo
   ```
   It prints what it restored and the result of the same checks as above. The
   old `sudo` admin can then log in as before.
3. If root's password doesn't work either, start **FellowshipOS recovery**
   from the boot menu with the recovery key. It offers **Undo account
   separation** as well as resetting passwords.

Until a NAS is migrated, Users shows a banner explaining the change and the
command to run, and the NAS keeps working the old way.

## What changes in the code

- **`users.py`:**
  - The NAS store: read, write, a lock, and hashing through libcrypt via
    ctypes.
  - create, set, password and remove work against the store, with the Unix
    entry and Samba kept in step.
  - "Allow SFTP" replaces the kinds.
  - `fnas auth check`.
  - `fnas accounts separate`, with `--dry-run`, `--yes`, `--later` and
    `--undo`.
- **`sftp.py`:** read-only shares get read-only bind mounts.
- **New `fnas network`:** `show`, `apply` and `confirm`, plus the rollback
  unit.
- **New `fnas updates`:** `check` and `apply`, plus the daily check timer and
  its alert.
- **`cli.py`:** `--caller`, and maintenance versus root-only marks on commands.
- **`helper.go`:**
  - Login against the store, through libcrypt via cgo.
  - Two roles.
  - Per-operation argument checks.
  - Current-password checks for one's own password.
  - Network confirm only from the new address.
  - The audit log.
- **`ssh.py`:**
  - `AllowGroups` becomes root (when on) plus `nas-sftp`.
  - The SFTP PAM stack and `AuthorizedKeysCommand`.
  - `fellowship-root-ssh`.
- **The installer:** root password and NAS admin on the You step, the recovery
  key step, the NAS admin created through `fnas`, the GRUB password, and no
  `sudo` account.
- **The image:**
  - the recovery boot entry and its program
  - `fellowship-recovery-key`
  - GRUB's `--unrestricted` normal entries
  - Samba's password sync turned off
  - an empty `sudo` group
- **The console status screen:** a root line, and a hint when no NAS admin
  can log in.

## The accounts review's gaps under this design

| Gap | Here |
|---|---|
| 1. Installer's account has no SMB password | Gone: the installer creates the NAS admin through `fnas`. Existing installs are flagged and set during migration. |
| 2. Own password changed without the current one | The helper checks the current password against the store |
| 3. `passwd` over SSH drifts from Samba | Gone: NAS people have no Linux password. Existing drift is flagged by the dry run. |
| 4. Only admin forgets the password | root resets it at the console; with both lost, recovery with the recovery key |
| 5. Expired password dead end | The store's must-change flag is handled on the desktop login page |
| 6. Must change at next login | The same flag |
| 7. Password rules | Unchanged proposal, applied in `fnas` |
| 8. Account state in Users | Unchanged proposal, read from the store |
| 9. Second factor | Later; TOTP secrets would live in the store |

## Decisions (Chris, 2026-11-15)

1. **Recovery:** a separate recovery key saved offline at setup. It works with
   both admin passwords lost. Physical access alone doesn't authorise a root
   reset, which is why GRUB is locked. The unencrypted-disk limit above stays.
2. **Network:** NAS admins may change it through the maintenance interface,
   with automatic rollback unless confirmed from the desktop after
   reconnecting.
3. **Updates:** checked automatically with a notification. Applying them and
   restarting only on an explicit request.
4. **SFTP:** an opt-in permission per person, file access only, within the
   shares and permissions they already have.

## Out of this phase

- **Disk encryption** (Chris, 2026-11-15). The recovery key protects the
  built-in recovery path only. Someone who can boot external media can still
  change an unencrypted system disk.

## Tests that must pass

Run in VMs, from the image, against a real system:

- **SFTP read-only shares:** `nas/tests/integration/sftp-readonly.sh`. Done:
  all pass.
- **Fresh install:**
  - The NAS admin can log in to the desktop and use SMB with the installer's
    password.
  - Root can log in at the console.
  - Nobody is in `sudo`, and a NAS person can't get a shell over SSH.
  - The recovery entry refuses without the key, and editing GRUB entries asks
    for it.
- **Migration** of a system installed the old way:
  - people with matching passwords
  - one with no SMB password
  - one whose Linux and SMB passwords differ
  - one turned off

  After migration:
  - UIDs, GIDs and file owners are unchanged.
  - Every password that worked still works, for the desktop and SMB.
  - The differing person's two passwords become one after setting a new one.
- **Migration stops in time:** with a wrong NAS admin password, and with an
  unusable root password, it stops before `sudo` is touched and leaves the
  system as it was.
- **Rollback path 1, root at the console:** on a migrated system, root logs
  in at the console and runs `fnas accounts separate --undo`. Afterwards:
  - the old `sudo` admin can log in over SSH and use `sudo`
  - the old passwords work for the desktop and SMB
  - files are untouched
- **Rollback path 2, recovery key only:** on a migrated system where the
  desktop login is broken on purpose (the store emptied) and root's password
  has been changed to something unknown:
  - **FellowshipOS recovery** from the boot menu with the recovery key, then
    **Undo account separation**
  - afterwards, the same checks as path 1, plus root's password reset
    through recovery
  - a wrong recovery key is refused, with the growing delay

## Phases

1. **SFTP read-only shares.** Done.
2. **The NAS store and login.** Done.
   - `fellowship_nas/accounts.py`: the store, plus hashing and checking through
     libcrypt via ctypes
   - `users.py` keeps people in the store whenever it exists: no Linux
     password, no shell, no home
   - `fnas auth check` and `fnas accounts status`
   - the helper's `internal/store` package: login and the NAS admin role from
     the store, through libcrypt via cgo
   - changing your own password needs the current one, admins included, with
     the login page's throttle
   - Users only offers shares and Files over SFTP on a separated NAS

   A NAS without the store keeps PAM and the `nas-admins` group, as before.

   **Tested** with `nas/tests/integration/separated-login.sh`: the real
   packages, Samba, sshd, helper and web server, all 34 checks passing.
   - a person's Linux password is locked, with no shell and no home
   - the desktop and SMB take the NAS password; SSH doesn't
   - the admin role comes from the store
   - your own password can't be changed without the current one
   - after a change, the new password works for the desktop and SMB, the old
     one for neither, and you're signed out everywhere
   - turning someone off refuses the desktop and SMB without unlocking their
     Linux password

   While building it, a latent bug turned up and was fixed: with no ZFS module
   loaded, `fnas` checked Secure Boot with `mokutil`, which nothing installs,
   so the check itself crashed share listing and every user change. It now
   reads the firmware's Secure Boot variable.
3. **The maintenance interface.** Done.
   - **`fellowship_nas/maintenance.py`** sorts every `fnas` command into NAS
     work, read, maintenance or root, action by action. Anything unlisted is
     root.
   - **`--caller`:** the helper passes `--caller <name>` on everything it runs
     for the desktop, and `fnas` refuses root's commands to any caller.
   - **Audit:** maintenance is written to the journal
     (`fellowship-nas-maintenance`, `auth.notice`) under the caller's name.
     Restart, shut down, certificate and installing updates also send an
     alert.
   - **The helper checks argument shapes** for the maintenance operations: a
     hostname, a zone name, a size or `all`, `--confirm` with its phrase, on
     or off, and nothing extra.
   - **`fnas updates`:**
     - `show`, `check` and `install`
     - a daily `fellowship-nas-updates.timer` that checks and alerts once per
       new set of updates
     - installing is a job running `apt-get upgrade --with-new-pkgs`, which
       never removes a package
     - a needed restart is said, never done
   - **Settings → Updates** replaces the placeholder. Its old text claimed
     security updates install by themselves, which was never true:
     `unattended-upgrades` isn't in the image.
   - **A unit test reads the helper's list from `helper.go`** and fails if
     the two locks disagree in either direction. On its first run it caught
     six timer-only commands (`pool sync`, `pool sync-all`, `share roots` and
     others) that a whole-area rule would have opened to the desktop.

   **Tested** with `nas/tests/integration/maintenance.sh`: the real packages,
   helper and web server, all 26 checks passing.
   - Both locks refuse bad argument shapes, root-only commands and
     timer-only commands.
   - A person who isn't an admin reaches no maintenance at all.
   - The journal and alerts record what was asked for, and nothing that was
     refused.
   - Checking changes no package.
   - Installing upgrades what was waiting, removes nothing, and says so.

   `ssh set` still turns the whole of sshd on and off, as before. It becomes
   the SFTP service switch in phase 7.
4. **Install.** Done.
   - **The installer**, on the NAS image only:
     - The You step asks for root's own password (twice), and refuses one
       that matches the NAS admin's.
     - The engine sets root's password and runs `fnas accounts init` and
       `fnas user create <name> --admin` inside the new system, so the NAS
       admin gets their NAS and SMB password and no Linux login. No `sudo`
       account is made.
   - **The recovery key:**
     - The installer makes the key, once per run.
     - It's shown on the Install step, not a step of its own. That's simpler,
       and it's still before anything is erased: **Erase and install** stays
       off until its last two groups are typed back.
     - On the machine's own screen it says to write it down or photograph it.
       From another computer it can also be saved as a text file.
     - The page never sends the key back: the installer's server adds the one
       it made.
   - **`fellowship-recovery-key`** (root's tool, `fellowship_nas/recovery.py`):
     - `set`, `new`, `check`, `status` and `remove`
     - it keeps only a yescrypt hash
     - it makes the key GRUB's superuser password (`01_fellowship_recovery`)
     - it moves Debian's `10_linux` aside with `dpkg-divert` behind a wrapper
       that marks normal entries `--unrestricted` and adds **FellowshipOS
       recovery** (`--users recovery`, `systemd.unit=fellowship-recovery.target`)
   - **The recovery program** (`fellowship-recovery.target`) runs on tty1.
     - It asks for the key again, waiting 2, 4, 8 … up to 120 s after wrong
       ones.
     - It sets root's password, a NAS person's password, or makes someone a
       NAS admin.
     - Undoing separation comes in phase 5.
     - At the next normal start, `fellowship-recovery-notice` sends a
       high-severity alert that recovery was used.
   - **The console status screen** says that root logs in there.

   **Tested** by installing the built ISO with the mouse in a VM, then on the
   installed system:
   - **The installer:** the root fields work; a wrong key ending doesn't
     start the install, and the right one, typed in lower case without the
     dash, does.
   - **First boot**, ready in 36 s:
     - the NAS admin logs in to the desktop as a NAS admin
     - root logs in at the console
     - the NAS admin's Linux password is locked, with no shell and no home
     - `sudo` has no members
     - Samba has the NAS admin
     - the key hash is root-only, `10_linux` is diverted, and no unit failed
   - **GRUB:**
     - editing an entry asks for a username, and a wrong password returns to
       the menu
     - the four normal entries are unrestricted
     - **FellowshipOS recovery** asks for the key, and boots into the
       recovery program with it
   - **Recovery:**
     - a wrong key waits 2 s
     - with the right key, root's password and the NAS admin's were set
     - afterwards the old ones were refused and the new ones worked, at the
       console and on the desktop
     - the next normal start alerted "Recovery was used at the console", and
       `recovery-log.json` lists each step
5. **Migration and undo.** `fnas accounts separate`: dry run, checks before
   `sudo` goes, `--undo`, and undo from recovery. Then both rollback tests.
6. **Network changes with rollback.**
7. **SSH and Samba settings.** `AllowGroups`, root SSH through
   `fellowship-root-ssh`, the SFTP PAM stack and `AuthorizedKeysCommand`,
   Samba's password sync turned off. These ship with phase 4 for new installs,
   and with phase 5 for migrated ones.
