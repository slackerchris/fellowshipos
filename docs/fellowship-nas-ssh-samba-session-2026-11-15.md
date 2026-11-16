# NAS SSH and Samba session — November 15, 2026

Handoff for the session that audited phase 7 against the code, brought Chris
the three decisions that affect who can log in or reach files, and then built
and tested it: **SSH and Samba settings**. Everything below was verified
against the code and by running the tests, not taken from the design doc's
status claims.

## Current status

- **Phases 1–7** of `docs/fellowship-nas-account-separation.md` are done and
  committed. The agreed plan, with the audit and the rulings, is
  `docs/fellowship-nas-ssh-samba-plan.md`; the results are recorded in the
  phase 7 entry of the account-separation doc, and its "Tests that must pass"
  list now covers SSH and Samba.
- **Committed on `nas-edition`** — the phase 7 work in one commit, `nas:`
  prefix per PROJECTS.md, this handoff included, on top of the phase 6 commit
  `d6425d0` and the plan commit `289570e`. **Not pushed** — the branch is
  shared with concurrent sessions, so a push needs Chris's go-ahead and a
  check of `git log --oneline origin/nas-edition..HEAD` first.
- **Chris's uncommitted installer LVM fix was left untouched**
  (`installer/internal/engine/engine.go`, `engine_test.go`, 3 + 27 lines).
  The installer engine was not edited at all this phase: the fresh-install
  hook lives in `fnas accounts init` instead. Verify with
  `git diff --stat -- installer/`.

## What the audit found (before building)

- `ssh.py` was old-model for both modes: `AllowGroups nas-ssh nas-sftp`,
  `PermitRootLogin no` hardcoded, keys in `~/.ssh/authorized_keys`, `ssh set`
  toggling all of sshd.
- `fellowship-root-ssh`, the SFTP PAM stack and `AuthorizedKeysCommand`
  existed only in the design doc. The store's `keys` field was written by
  migration but never read; `fnas user keys` was unusable on a separated NAS
  (it required the forbidden `ssh` kind, and new people have home
  `/nonexistent`).
- Samba sync-off existed only in migration — and was **wrong**: verified
  against real smbd on trixie, turning both options off accepts a Windows
  password change and drifts the SMB password from the desktop one, while
  Debian's defaults (both on) refuse it via PAM old-password auth against the
  locked hash. Migrated NASes had the drift hole; fresh installs were
  accidentally safe.

## Chris's rulings (2026-11-15, all taken as recommended)

1. **Samba:** keep both sync options `yes` and add
   `password requisite pam_deny.so` to `/etc/pam.d/samba` on a separated NAS.
   Migration's both-off is replaced. The design doc's "turned off" wording is
   superseded.
2. **SFTP off:** drop `nas-sftp` from `AllowGroups` while sshd keeps running
   if root SSH is on; root's SSH is never touched by the desktop. With root
   SSH off, sshd stops entirely.
3. **Keys-only guard:** no admin-key guard for `--passwords off` in separated
   mode; the old model keeps it.

Standing: leave the installer LVM fix untouched; keep the work local; no push.

## The build, grouped

- **`fellowship_nas/ssh.py`:** two models. `drop_in_old` is the original
  function body unchanged (checked by AST comparison); `drop_in_separated`
  adds root to `AllowGroups` only while his SSH is on, `PermitRootLogin
  prohibit-password` then, `AuthorizedKeysCommand /usr/sbin/fnas auth keys %u`
  run as root, `AuthorizedKeysFile none` in the `nas-sftp` Match, and
  `DenyGroups *` when both switches are off. `refresh()` also writes
  `/etc/pam.d/sshd` in separated mode and restores both files if `sshd -t`
  refuses; the service stays up for root SSH even with SFTP off.
  `set_root_ssh`/`root_ssh`/`root_has_key` hold root's switch; `status()`
  gains `root_ssh` and answers `allowed` from `nas-sftp`.
- **`fellowship_nas/accounts.py`:** `ssh_keys(name)` — the store's keys, or
  nothing for root and unknown names.
- **`fellowship_nas/cli.py`:** `fnas auth keys` (root-only in both locks);
  `accounts init` runs `ssh.refresh()` + `shares.samba_password_policy()`;
  `print_ssh` speaks SFTP and root SSH on a separated NAS.
- **`fellowship_nas/shares.py`:** `set_smb_option` moved here from
  `separate.py` (re-exported there); `samba_password_policy()` writes both
  `yes` and the PAM deny file when separated, called from `write_smb_config`.
- **`fellowship_nas/separate.py`:** backup keeps `/etc/pam.d/sshd` and
  `/etc/pam.d/samba`; `lock_down` applies the Samba policy; `new_stamp()`
  gives a same-second retry its own backup folder.
- **New package files:** `fellowship-root-ssh` (`usr/sbin`) and `sftp-auth`
  (`usr/lib/fellowship-nas`, the pam_exec wrapper: `fnas auth check
  "$PAM_USER"`), both in `debian/install`.
- **Helper/web:** the expired-password message points at a NAS admin, not
  SSH; `SshStatus` gains `root_ssh`; Settings → SSH becomes the SFTP switch
  with a read-only root line on a separated NAS; Users offers the key editor
  for SFTP people.
- **Console:** `fellowship-nas-issue`'s root line follows the root-SSH state.
- **Tests:** `nas/tests/unit/test_fnas.py` (+20: SeparatedSsh,
  SambaPasswordPolicy, the stamp regression, `auth keys` in the root-only
  refusal list); new `nas/tests/integration/ssh-samba.sh`; `separate.sh`
  extended with the phase 7 policy, SFTP, keys, Windows change and undo
  checks.
- **Docs:** account-separation status, Samba section, phase 7 entry and test
  list; the plan's status; overview and plan notes; this handoff.

Build artifacts land in the gitignored
`config/includes.chroot/usr/share/fellowshipos/nas-repo/` and `web/dist/`.

## Verification — what ran and passed

| Check | Result | How |
| --- | --- | --- |
| fnas unit tests | **358/358** | `cd nas/tests/unit && python3 -m unittest test_fnas` (host Python) |
| Go gofmt/vet/tests | **clean** | trixie container (recipe in the network-session handoff) |
| Frontend typecheck + build | **clean** | `FELLOWSHIP_THEMES_DIR=…/themes npm run build` in `web/` |
| SSH and Samba, separated + old model | **66/66** | `bash nas/tests/integration/ssh-samba.sh` |
| Migration + phase 7 + undo | **65/65** | `bash nas/tests/integration/separate.sh` |
| Separated login regression | **34/34** | `bash nas/tests/integration/separated-login.sh` |
| SFTP read-only regression | **all pass** | `bash nas/tests/integration/sftp-readonly.sh` |
| Maintenance regression | **26/26** | `bash nas/tests/integration/maintenance.sh` |
| Network rollback regression | **51/51** | `bash nas/tests/integration/network-rollback.sh` |
| Network timer, real systemd | **14/14** | `bash nas/tests/integration/network-rollback-timer.sh` (~5 min) |

The last two runs of `ssh-samba.sh` and `separate.sh` were against a fresh
tools rebuild from the exact committed source. The integration scripts
install from the local apt repo, so **rebuild the packages first** after any
change (recipes in `docs/fellowship-nas-network-session-2026-11-15.md`).

## What testing caught (fixed; pitfalls for next time)

1. **`sftp -b` disables password authentication.** Batch mode implies
   `BatchMode=yes`; the verbose log shows the client never even tries the
   password. The password-SFTP checks pipe `ls` on stdin instead. This masqueraded
   as a PAM-stack failure at first — the old model failed the same way, which
   is what gave it away.
2. **OpenSSH's per-source penalties poison a test harness.** Trixie's sshd
   penalizes a source after failed attempts; the script's deliberate wrong
   passwords made later, correct key logins fail. The harness writes
   `PerSourcePenalties no` in its own `00-test.conf` — test-only, never the
   product drop-in.
3. **A same-second migration retry crashed on its own backup.** `back_up`
   used a second-resolution stamp; a stopped run leaves its folder behind,
   and the retry landed on it (`FileExistsError`). `new_stamp()` suffixes
   `-2`, `-3`…; undo's newest-first sort still works. Found by `separate.sh`,
   fixed in the product, with a unit test.
4. **`ssh show` answers text unless `--json` is passed**, and the helper
   wraps fnas output in an escaped-JSON `output` field — the desktop check
   greps for `root_ssh` bare, not quoted.

## Open items (honestly)

- **Installed-system checks, not done:** the console root line on the
  machine's own monitor, and root SSH end to end on real hardware. Needs the
  next ISO build — no ISO exists in the checkout now. `nas/tests/vm/vm.sh` is
  the harness. Phase 6's console waiting-change line and DHCP-against-a-real-
  router checks share that build.
- **The must-change flow is a phase 2 residual, flagged to Chris, not built.**
  Nothing in the code sets `must_change`, and `desktop/login.tsx` has no flow
  for it — phase 2's claim that the flag "is handled on the desktop login
  page" does not match the code. The flag is unreachable today, so there is
  no live dead end; this phase fixed the helper's misleading "Change it over
  SSH" message only. Building the flow is Chris's call, separately.
- **Not pushed.** Seven commits sit ahead of `origin/nas-edition` (phase 6,
  the plan, and phase 7 among them); the branch is shared with concurrent
  sessions, so list `origin/nas-edition..HEAD` and show Chris what a push
  would publish before doing it.

## Picking up

1. Read `docs/fellowship-nas-ssh-samba-plan.md` (audit + rulings + design)
   and the phase 7 entry in `docs/fellowship-nas-account-separation.md`.
2. `git status` — confirm the installer WIP is still Chris's alone.
3. Re-run the verification table above after any change; rebuild packages
   before the integration scripts.
4. The next phase is whatever Chris names; the account-separation design is
   fully built. The residuals above are the honest candidates.
