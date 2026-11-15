# FellowshipNAS accounts and passwords: review, 2026-11-15

Chris asked for a look at user and password management after the Proxmox
test. This is what exists today, the gaps found in the code, and a proposed
order to close them. Nothing here is built yet.

## What exists

- **One password per person** for the desktop, SMB, SFTP and SSH. `fnas user password` sets Linux (`chpasswd`) and Samba
  (`smbpasswd`) together and signs the person out everywhere
  (`users.py`, `set_password`).
- **Users app** (admins): add, change name, kind (shares only, Files over
  SFTP, SSH), admin on or off, groups, turn off or on, set a password, SSH
  keys, remove with a typed confirmation that names the shares lost.
- **Groups**: create, rename, remove, members. The machine's own groups can be
  given shares without being renamed or removed.
- **Settings → Your account → Your password** for everyone.
- **Sessions**: a list of where you are logged in (admins see everyone),
  ending one, and sign-out everywhere after a password change.
- **Login protection**: per-account and per-address throttling, and an alert
  after repeated wrong passwords (`helper.go`, `login`).
- **Guards**: the last admin can't be removed, demoted or turned off. A
  turned-off account loses its password, its Samba access and its sessions,
  and its expiry blocks SSH keys too.

## Gaps

Ordered by how much they hurt.

### 1. The installer's own account has no SMB password (bug)

The installer creates the account with `useradd` and `chpasswd` only
(`installer/internal/engine/engine.go`, `account`). Nothing ever gives it a
Samba password: first boot can't, because it never sees the password. So on a
fresh NAS, the admin's own account can't open a single share from Windows or a
Mac until the password is set again by hand. The Users app shows this only as
the `smb_password` field, which nothing on screen explains.

**Fix:** the installer sets the Samba password in the same step, now that
Samba is in the image (`smbpasswd -s -a` in the chroot). An account that still
has no Samba password says so in Users, with **Set password** next to it.

### 2. Changing your own password doesn't ask for the current one

`OwnPassword` (`settings.tsx`) sends only the new password, and the helper
lets anyone run `fnas user password <themselves>`. Anyone at an unlocked
browser, or holding a stolen session cookie, can take the account over, and
the sign-out everywhere that follows then locks the real owner out.

**Fix:** your own password needs the current one, checked through PAM by the
helper (the same check as login, with the same throttling). An admin setting
someone else's password doesn't need theirs, but does need to be signed in
recently, or to enter their own password again.

### 3. A password changed over SSH stops matching Samba

`passwd` over SSH changes only the Linux password, and nothing passes it on to
Samba (no `pam_exec`, no `unix password sync`). From then on, the desktop and
SSH take the new password while shares take the old one: "one password"
quietly stops being true.

**Fix:** a PAM password hook that also sets the Samba password whenever
`passwd` changes it (`pam_exec` with `expose_authtok`, calling `smbpasswd`).
Health checks for the drift where it can: an account with a Linux password
but no Samba one.

### 4. Nobody can get back in if the only admin forgets their password

Root is locked and the admin is the only way in. The only recovery is editing
the GRUB entry by hand (`init=/bin/bash`), which nothing documents. The
console's status screen is the natural place for this, since being at the
machine is the proof of ownership.

**Proposed:** a **Reset an admin's password** entry in the GRUB menu, or on
the NAS's own console. It asks which admin and the new password, sets Linux
and Samba together, and sends an alert. It's offered only at the machine
itself, never over the network.

**Decision for Chris:** a GRUB entry (works even if the NAS won't start
fully), or a key on the console's status screen (friendlier, but it needs the
system up)?

### 5. Expired passwords are a dead end

The desktop says "Your password has expired. Change it over SSH". A
shares-only or SFTP account has no SSH, so it has no way in at all. Nothing in
FellowshipOS sets an expiry, but an admin using `chage` (or a policy someone
adds) would hit this.

**Fix:** the login page lets an expired password be changed on the spot
(current, new, again), through PAM's change flow.

### 6. "Set a password, they change it" isn't possible

An admin who sets up an account for someone has to tell them a password and
trust them to change it. There's no **must change at next login**.

**Fix:** a checkbox when adding a person or setting their password. It uses
the same change-on-login flow as gap 5.

### 7. The password rules are only "8 characters"

`check_password` refuses empty, colon, newline and fewer than 8 characters.
It accepts `password`, `12345678` and the account's own name.

**Fix:** refuse the account's name, its full name, and a short built-in list
of the most common passwords. Show a strength hint while typing. No
composition rules: they make passwords worse, not better.

### 8. Nobody can see an account's state at a glance

Users doesn't show, for each person:
- when they last logged in, and from where
- whether their Samba password is missing (gap 1)
- whether they're throttled after wrong passwords
- whether they have SSH keys

**Fix:** a status line per person in Users, built from what the helper and
`pdbedit` already know.

### 9. No second factor

There's no TOTP. On a NAS that only answers on the home network this is lower
priority, but it matters as soon as someone forwards port 443.

**Proposed later:** TOTP for desktop login, optional per person and required
for admins once turned on. SMB and SSH keep their own mechanisms (SSH keys).

## Proposed order

1. **Gaps 1, 2, 3.** These are the "one password" promise and its safety. All
   three are small, and gap 1 hits every new NAS today.
2. **Gaps 4, 5, 6.** Getting back in, and handing an account to someone else.
3. **Gaps 7, 8.**
4. **Gap 9.**

Each step is checked in a VM: SMB login from a client with the installer's
account; a password changed over SSH, then used over SMB; a forgotten-admin
reset; an expired account logging in on the desktop.
