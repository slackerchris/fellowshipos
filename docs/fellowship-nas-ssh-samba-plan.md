# Phase 7: SSH and Samba settings — implementation and test plan

**Status:** implemented and tested 2026-11-15; the plan below was agreed by
Chris before coding. Implements phase 7 of
`fellowship-nas-account-separation.md`, which records the results; the session
handoff is `docs/fellowship-nas-ssh-samba-session-2026-11-15.md`. Two things
the plan left open were settled while building: with SFTP and root SSH both
off the drop-in says `DenyGroups *` (so a manually started sshd admits
nobody), and the password-SFTP integration checks pipe commands on stdin —
`sftp -b` implies `BatchMode=yes`, which disables password authentication.

Everything here was checked against the code on branch `nas-edition`
(`d6425d0`), not against the design doc alone. The Samba behavior was verified
against real smbd on Debian trixie in a container, the same way the migration
section's credential claims were verified.

Standing rules for this work: Chris's uncommitted installer LVM fix
(`installer/internal/engine/engine.go`, `engine_test.go`) stays untouched —
which also means the installer engine is not edited at all in this phase; the
work stays local on `nas-edition`; nothing is pushed (the branch is shared
with concurrent sessions and already carries six unpushed commits).

## What exists today — the audit

| Design item | State | Evidence |
|---|---|---|
| `AllowGroups` = root (when on) + `nas-sftp` | old model only | `ssh.py` `drop_in()` writes `AllowGroups nas-ssh nas-sftp` and `PermitRootLogin no` for both modes. On a separated NAS `nas-ssh` ends up empty only because `SEPARATED_KINDS` forbids the `ssh` kind — accident, not policy. |
| `fellowship-root-ssh` | absent | docs only; no tool, no state file, no desktop display. The console root line is static. |
| SFTP PAM stack | absent | no `pam_exec` anywhere in code. The hook it will call, `fnas auth check` (root-only, password on stdin, exit-code answer), exists since phase 2. |
| `AuthorizedKeysCommand` from the store | absent | the store has a `keys` field and migration copies keys into it (`separate.py` `write_store`), but nothing reads it. |
| SFTP key management | broken on a separated NAS | `ssh.set_keys` requires kind `ssh` (forbidden there) and writes `~/.ssh/authorized_keys`; new people get home `/nonexistent` (`users.create_user`). The Users panel only offers keys to `ssh`-kind accounts (`users.tsx` `SshKeys`). |
| Samba password sync off | migration only — and wrong | `separate.py` `lock_down` sets both options `no`; fresh installs ship Debian's defaults (`smb_global_config` never touches them). See the verified finding below: "off" is the one configuration that breaks the design's intent. |
| `ssh set` as the SFTP service switch | not started | still toggles all of sshd; the desktop panel still describes shells. Phase 3's entry says it becomes the SFTP switch in phase 7. |

What already works in our favor:

- The helper refuses `ssh set --root on` (`helper_test.go`), and `ssh show` /
  `ssh set` are read / maintenance in both locks (`maintenance.py`,
  `helper.go` `commands`). Root's SSH stays unreachable from the desktop.
- Refresh paths exist: `fellowship-nas` postinst runs `fnas ssh refresh`;
  `dispatch_user` runs `ssh.sync_members()` + `sftp.sync()` after
  create/set/remove; migration's `lock_down` calls `ssh.refresh()`.
  **`fnas accounts init` refreshes nothing** — that is the hook a fresh
  install needs, since the installer engine can't be edited.
- `sftp.py`'s `MATCH_OPTIONS` (chroot, internal-sftp, no forwarding) are
  proven by `sftp-readonly.sh` and stay as they are.
- `openssh-server` is in the image (`config/package-lists/base.list.chroot`).

## The Samba finding — verified, and it overturns the design's letter

The design says both sync options off **and** Windows password changes
"refused with a pointer to the desktop". Those two are incompatible. Tested
with real smbd on trixie: a person with a locked Linux password (`!$y$…`),
`nologin` shell, and a Samba hash — the separated-NAS state — changing their
password the way Windows does (`smbpasswd -r`, the remote change protocol):

| smb.conf | Change | /etc/shadow | SMB afterwards |
|---|---|---|---|
| Debian defaults: both `yes` | **refused** (Access Denied) | untouched, still locked | old password works |
| both `no` (the design's letter) | **accepted** | untouched | **new password works, old refused — silent drift** |
| both `yes` + `password requisite pam_deny.so` in `/etc/pam.d/samba` | **refused** | untouched | old password works |
| `unix password sync = yes`, `pam password change = no` | refused | untouched | — |
| `unix password sync = no`, `pam password change = yes` (+ deny line) | accepted | untouched | drift — the pam option alone does nothing |

Mechanism: `pam password change` only steers the unix-sync path (PAM instead
of the passwd program). With sync on, smbd verifies the old password through
PAM first, and a locked hash can't authenticate — so the change is refused
before anything is set. With sync off, smbd changes only its own passdb.

Consequences:

- **Migrated NASes have the drift hole today**: `lock_down` turns both off,
  so a Windows change silently splits the SMB password from the desktop one.
- **Fresh installs are accidentally safe**: Debian's defaults refuse.
- The "pointer to the desktop" never reaches the SMB client in any variant —
  the error is a generic Access Denied. The pointer lives in the desktop and
  the docs, not in the protocol error.

## Chris's rulings (2026-11-15, all taken as recommended)

1. **Samba:** keep `unix password sync = yes` and `pam password change = yes`
   on a separated NAS, and add `password requisite pam_deny.so` to
   `/etc/pam.d/samba` — explicit refusal, verified, one password preserved.
   Migration's both-off is replaced by this. The design doc's "both turned
   off" wording is superseded by this ruling.
2. **SFTP off:** `ssh set --enabled off` drops `nas-sftp` from `AllowGroups`
   while sshd keeps running if root SSH is on; root's SSH is never touched by
   the desktop. With root SSH off, sshd stops entirely, as today.
3. **Keys-only guard:** in separated mode `ssh set --passwords off` just makes
   SFTP keys-only — no admin-key guard, since admins never use SSH and nobody
   can be locked out of the desktop by it. Non-separated NASes keep the old
   guard.

## The design, component by component

### `ssh.py`: two modes, one code path

Non-separated NASes keep today's behavior byte for byte: the same drop-in,
the same guard, `nas-ssh` managed from the `ssh` kind. Everything below applies
only when `accounts.is_separated()`.

The separated drop-in (still one generated file, still validated with
`sshd -t` and restored on refusal, still closed with `Match all`):

```
AllowGroups root nas-sftp        # root only while root SSH is on; else nas-sftp alone
PermitRootLogin prohibit-password   # while root SSH is on; else no
PasswordAuthentication yes|no       # the SFTP passwords toggle
KbdInteractiveAuthentication no
AuthorizedKeysCommand /usr/sbin/fnas auth keys %u
AuthorizedKeysCommandUser root

Match Group nas-sftp
    ChrootDirectory /var/lib/fellowship-nas/sftp/%u
    ForceCommand internal-sftp
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    AuthorizedKeysFile none

Match all
```

- `AuthorizedKeysFile none` inside the Match makes the store the **only**
  source of keys for SFTP people: a migrated person's old
  `~/.ssh/authorized_keys` stops being consulted, so replacing or removing a
  key in Users takes effect at once. Root keeps the global default file.
- `ssh set --enabled off` (ruling 2): with root SSH on, rewrite the drop-in
  with `AllowGroups root` and reload — SFTP is refused at the door, root's
  session is untouched; with root SSH off, `systemctl disable --now ssh` as
  today. `--enabled on` puts `nas-sftp` back.
- `ssh set --passwords off` (ruling 3): no `check_someone_gets_in` in
  separated mode.
- `ssh.status()` gains `root_ssh` (bool) for the desktop's read-only line;
  `allowed` keeps meaning "who may SFTP now".
- `sync_members()` in separated mode manages `nas-sftp` only (through
  `sftp.sync()`); `nas-ssh` is left alone.

### The SFTP PAM stack

`ssh.refresh()` in separated mode also writes `/etc/pam.d/sshd`, with the
same validate-and-restore care as the drop-in (a refused stack must not stop
sshd starting):

```
auth     requisite   pam_exec.so expose_authtok /usr/lib/fellowship-nas/sftp-auth
account  requisite   pam_unix.so
session  requisite   pam_unix.so
```

- The wrapper (shipped by fellowship-nas-tools, like the `sftp-only` shell):
  `exec /usr/sbin/fnas auth check "$PAM_USER"` — pam_exec sets `PAM_USER` in
  the environment and passes the password on stdin with `expose_authtok`;
  `fnas auth check` already answers with its exit code.
- The `account` phase is what keeps a turned-off person refused over SFTP:
  `users.change_separated` sets the expiry, and pam_unix enforces it — for
  key logins too, since sshd runs the PAM account phase with `UsePAM yes`.
- Root is not in the store, so the auth phase refuses him — consistent with
  keys-only root SSH (pubkey logins skip the auth phase, not account/session).
- Non-separated NASes keep Debian's `/etc/pam.d/sshd`.

### `fnas auth keys` — the AuthorizedKeysCommand

A new root-only action under `auth` (the area is already root-only in both
locks): prints a store person's keys, one per line; prints nothing for root
or an unknown name; always exits 0. AllowGroups and the PAM account phase do
the gating, so the command stays simple. Keys are read per connection — no
sshd reload is needed when keys change.

### `user keys` against the store

In separated mode `ssh.set_keys` / `list_keys` read and write the store's
`keys` field, not `~` files: allowed for people with SFTP on; a shares-only
person is refused with a pointer to enable SFTP first (the shape of today's
kind message). `check_key` validation and `MAX_KEYS` stay. The Users panel
offers the key editor for SFTP people; its text stops promising shells.

### `fellowship-root-ssh`

Root's own tool, shipped in fellowship-nas-tools (`usr/sbin`), beside
`fellowship-recovery-key`:

- `on` / `off` / `status`. Refuses on a non-separated NAS (the old model is
  unchanged).
- State in `/etc/fellowshipos/root-ssh.json`, root-only — separate from
  fnas's `nas-ssh.json`, read by `ssh.py` when it generates the drop-in and
  by the console screen.
- `on` refuses unless `/root/.ssh/authorized_keys` holds at least one key:
  root adds his own key at the console first, so enabling can never lock
  him out. Then it writes the state and runs `fnas ssh refresh`.
- `off` restores `PermitRootLogin no` and `AllowGroups` without root.
- A journal line under its own tag for each change. The desktop can only
  read the state (`ssh show`); neither lock lists the tool, and it is not an
  fnas maintenance command.

### Samba (ruling 1)

- Separated mode: `smb_global_config` writes `unix password sync = yes` and
  `pam password change = yes` explicitly (so a Debian upgrade can't drift
  them), and the same path writes `/etc/pam.d/samba` as Debian's three
  includes plus `password requisite pam_deny.so`.
- Migration's `lock_down` stops setting both options `no`; it applies the
  same policy instead. `set_smb_option` stays — it is how the values are
  written.
- Non-separated NASes: untouched.
- The refusal's pointer to the desktop lives in the Users panel note and the
  docs; the SMB client itself only ever sees Access Denied (verified).

### Migration and undo

- `SAVED_FILES` gains `/etc/pam.d/sshd` and `/etc/pam.d/samba` — the design
  already promises "the PAM files the design changes" are backed up; today
  they are not. Undo restores them with the same checksum checks, and the
  existing logic removes files the migration added to saved directories.
- `lock_down` gets the new SSH policy through `ssh.refresh()` (already
  called) and the Samba policy above.
- A migrated person's keys, already copied into the store, start working
  over SFTP; their old `authorized_keys` file stops being consulted
  (`AuthorizedKeysFile none`).

### Fresh installs: the separation hook

`fnas accounts init` (dispatch in `cli.py`) runs `ssh.refresh()` after
creating the store — the moment a fresh install becomes separated, the SSH
policy and PAM stack follow. The installer engine is not touched (Chris's
WIP lives there). The postinst's existing `fnas ssh refresh` still covers
upgrades; on a separated NAS it now writes the separated policy.

### Desktop, console, wording

- Settings → SSH: separated mode shows the **Files over SFTP** service
  switch, the passwords/keys-only toggle (no guard), who may SFTP now, and a
  read-only root line ("root: SSH, keys only" / "root: console only").
  Non-separated mode keeps today's panel.
- `api.ts` `SshStatus` gains `root_ssh`; `users.tsx` `SshKeys` serves SFTP
  people.
- `fellowship-nas-issue`: the root line reads the root-SSH state file.
- `helper.go`'s expired-password message ("Change it over SSH, then log in
  here") becomes truthful for a separated NAS: ask a NAS admin to set a new
  one in Users. The must-change flag itself is a phase 2 residual (below),
  not built here.
- `print_ssh` console text follows the new semantics.

## Build order

1. `ssh.py` dual mode + `fnas auth keys` + store keys (unit tests).
2. `fellowship-root-ssh` + state file (unit tests).
3. The SFTP PAM stack + wrapper (unit tests, then integration).
4. The Samba policy (unit tests; the behavior itself is already verified).
5. Migration and undo updates (extend `separate.sh`).
6. Desktop, console, wording (typecheck + build).
7. Docs: the phase 7 as-built entry, the test list, the overview/plan
   corrections, and a handoff.

Each step keeps the non-separated path unchanged; the whole verification
table runs again at the end. Packages are rebuilt into the gitignored
nas-repo before any integration script runs — the scripts install from
there (recipes in `docs/fellowship-nas-network-session-2026-11-15.md`).

## Tests that must pass

**Unit** (host Python; Go and the frontend in the trixie container, recipes
in the network-session handoff):

- The separated drop-in: AllowGroups with and without root SSH,
  `PermitRootLogin` both ways, `AuthorizedKeysCommand`/`User`,
  `AuthorizedKeysFile none` in the Match, `Match all` closing; the
  non-separated drop-in unchanged (the existing test at `test_fnas.py`
  keeps passing).
- `ssh set --enabled off` with root SSH on keeps sshd running and refuses
  SFTP; with root SSH off stops it. `--passwords off` without the guard in
  separated mode, with it otherwise.
- `fellowship-root-ssh`: refuses without a key, refuses on a non-separated
  NAS, state round-trips, off restores.
- Store keys: validation, the 32-key limit, shares-only refused, root and
  unknown names answered with nothing by `auth keys`.
- The Samba policy writes both `yes` and the deny line in separated mode,
  nothing in the old model.
- Both locks still agree (the existing unit test reads `helper.go`);
  `ssh set --root on` still refused; bad shapes still refused.

**Integration** — a new `nas/tests/integration/ssh-samba.sh`, the real
packages, sshd and smbd, modeled on `separated-login.sh` and
`sftp-readonly.sh`:

- SFTP password login works through the PAM stack (the behavior change);
  wrong password refused; a turned-off person refused (expiry); an unknown
  name refused.
- A store key logs in; replacing or removing it stops the old key at once
  (the old `authorized_keys` file is not consulted); a shares-only person is
  refused by AllowGroups; no shell, no forwarding (the existing Match).
- Root: password SSH refused always; key SSH refused while off;
  `fellowship-root-ssh on` without a key refused; with a key, root logs in
  with the key and still not with a password; `off` refuses again. The
  desktop can't reach the tool (helper 403, fnas root-only).
- SFTP off with root SSH on: SFTP refused, root's key login still works;
  SFTP on restores it.
- A Windows password change is refused; the SMB and desktop passwords are
  unchanged; shadow stays locked; SMB login still works.
- **Non-separated regression:** shell SSH through `nas-ssh`, PAM passwords,
  the old guard, Debian's Samba behavior — all as before.
- Both locks and the journal: shapes, non-admins, root-only commands; the
  audit records what was asked for and nothing refused.

**Migration** — `separate.sh` extended: after `--yes`, the new AllowGroups,
PAM stack and store keys work; a Windows change is refused; `--undo`
restores `/etc/pam.d/sshd`, `/etc/pam.d/samba`, the sshd drop-in and
`smb.conf` byte for byte, and the old sudo admin's SSH and sudo work again.

**Installed system** — deferred to the next ISO build, as with phases 4–6:
the console root line on the machine's own monitor, and root SSH end to end
on real hardware. No ISO exists in the checkout now; `nas/tests/vm/vm.sh`
is the harness.

## Open items and residuals (honestly)

- **The must-change flow is a phase 2 residual, not phase 7 work.** Nothing
  in the code ever sets `must_change`, and `desktop/login.tsx` has no flow
  for it — it only shows the helper's error. Phase 2's claim that the flag
  "is handled on the desktop login page" does not match the code. The flag
  is unreachable today, so there is no live dead end; this phase fixes the
  message only. Building the flow is Chris's call, separately.
- The SMB client never sees the pointer to the desktop (generic Access
  Denied, verified). Accepted with ruling 1; the pointer lives in Users and
  the docs.
- Phase 6's installed-system checks (console waiting-change line, DHCP
  against a real router) are still open and share the next ISO build.
