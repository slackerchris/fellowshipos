# The must-change password flow — plan, November 16, 2026

> **Stopped and backed out, 2026-11-16. Reference only, not approved work.**
> Chris deferred forced password changes and reset links
> (`docs/fellowship-nas-release.md`). Gate A was reverted whole in `23b048b`,
> including the migration carry-over, so nothing can flag a person or close
> their password access; ordinary admin resets and account disabling are
> exactly as they were. The `nas/` tree is byte-identical to `4fc295f`, and the
> packages were rebuilt so an installed image matches. The audit below still
> describes the code truthfully — the flag is unreachable, and it never gated
> SMB — but the plan and its two rulings are **not** a work order. The next
> round is driven by the gaps Chris's household VM tour actually hits.

Phase 2's residual, audited and ruled on. Branch `nas-edition` at `4fc295f`,
twelve commits ahead of origin, nothing pushed. Chris's installer LVM edits
(`installer/internal/engine/engine.go`, `engine_test.go`) stay uncommitted and
untouched; the physical-LAN DHCP check stays open and out of scope here.

## Chris's rulings (2026-11-16)

1. **Writer + badge + login-page change.** An admin can flag a person and see
   the flag; the flagged person meets a choose-a-new-password step on the login
   page and picks their own password. Not admin-reset-only.
2. **The flag gates SMB too, and completing the change restores it.** A
   temporary password must not keep reading files over SMB.

## What the audit found

The flag is a read-only dead field: nothing in production ever sets it.

| Question | Answer today | Evidence |
| --- | --- | --- |
| Who can set it | Nobody. `new_person` writes False, `set_password_hash` only ever clears it | `accounts.py:106,112` |
| | `fnas user set` has no such option; neither does `user create` | `cli.py:520-534` |
| | The helper's whitelist has no op that could carry it | `helper.go:56` |
| | Migration builds `new_person` and never reads shadow's `lastchg == 0` | `separate.py:300-305` |
| | The only writers of True are tests poking the JSON | `test_fnas.py:3601,3638`, `store_test.go:45` |
| How login handles it | A flat 401, no token, no session, no change affordance | `helper.go:244-247` |
| | Separated: `store.Check` → `ErrExpired`; old model: `pam_acct_mgmt` → `PAM_NEW_AUTHTOK_REQD` | `store.go:110-125`, `pam.go`, `main.go:178-186` |
| | `login.tsx` renders the prose in a notice and clears the password box | `login.tsx`, whole file |
| | The same refusal reaches WebDAV (401, re-prompt loop) and SFTP (`fnas auth check` → `"expired"`) | `dav.go:189-199`, `ssh.py:231`, `cli.py:794` |
| | The expired branch returns before `limiter.fail` and the alert | `helper.go:248-256` |
| What completes it | Only somebody else: Users → Set password, root's `fnas user password`, or recovery option 2 | `users.tsx:184-193`, `cli.py:959-961`, `recovery.py:244-257` |
| | `set_password` clears the flag, writes store hash and `smbpasswd` together, ends sessions | `users.py:232-247`, `219-230` |
| | Self-service exists but needs a session the flagged person cannot get | `helper.go:508-511` |
| | The bridge is half-built: `checkCurrent` already accepts `ErrExpired` as the right password | `helper.go:612-613` |

Gaps, numbered for the verdicts at each gate:

- **G1** no writer; **G2** no login-page flow (design doc rows 5 and 6,
  `fellowship-nas-account-separation.md:498-499`, claim it is handled);
  **G3** self-service unreachable for exactly these people; **G4** the state is
  invisible — `list_separated` returns it (`users.py:174`) but `print_users`
  omits it (`cli.py:210-218`) and `api.ts`'s `User` has no field, so Users shows
  only "Set" / "No password yet" (`users.tsx:300-301`); **G5** SMB ungated
  (code trace: smbd checks its NT hash and the flag is read nowhere on that
  path — the integration run must decide it); **G6** migration silently drops a
  pre-existing Linux must-change; **G7** no reuse check, so re-setting the same
  password clears the flag (`users.py:196-200`); **G8** expired attempts bypass
  the login limiter; **G9** no age-based expiry anywhere — `password_set` isn't
  even in `store.Person`, so "expired" can only mean the flag; **G10** on the
  old model nothing in fnas sets PAM expiry either.

## The design

### 1. The writer — `fnas user set NAME --must-change on|off`

`cli.py` gains the option beside `--enabled`; `users.change_user` and
`change_separated` gain `must_change=None`. Separated NAS only: on the old
model the flag is PAM's own, `chage -d 0` already sets it and the refusal
already works, so fnas refuses there with that pointer rather than inventing a
second mechanism. `user set` is already in `maintenance.NAS["user"]`, so the
desktop reaches it and the journal records it under the caller's name.

- **on:** set the flag; if the person has an SMB password, `smbpasswd -d NAME`;
  `end_sessions(name)` — a password being made temporary must not leave live
  sessions behind.
- **off:** clear the flag; if the entry is enabled and has an SMB password,
  `smbpasswd -e NAME`.
- Both guard on `/etc/samba` existing, as `set_password` does.
- No new stored state: the only reason fnas ever disables SMB is `enabled off`,
  so "may SMB be on" is derivable from `enabled` plus having a password.
- **The edge that follows:** `change_separated`'s `enabled True` branch must not
  re-enable SMB while the flag is set, and `set_password` must re-enable it when
  it clears the flag on an enabled entry. Whether `smbpasswd -s -a` already
  clears the D flag is a test question, not an assumption.

### 2. Visibility

`print_users` gains "must change password" among the extras. `api.ts`'s `User`
gains `must_change?: boolean` (absent on the old model). `users.tsx` shows a
warning badge in the Password column, a Notice in the detail panel, and a
checkbox wired to `user set --must-change`. Beside "Set password" a second
checkbox — "they must choose a new one at next sign-in" — runs the password set
first (which clears the flag) and then flags, which is how an admin hands out a
temporary password in one step.

### 3. The change at the login page

A new pre-auth op, kept as narrow as it can be.

- `protocol.OpChangePassword = "change-password"`, and `Response.MustChange`
  so the login refusal carries a structural signal instead of prose the
  browser would have to string-match.
- `helper.login` sets `MustChange` on the `ErrExpired` refusal. The prose stays
  as the fallback for a client that can't use the step. The expired branch
  stays uncounted by the limiter: it is a *correct* password, and counting it
  would throttle the person we are trying to let through. The new op does not
  inherit that exemption.
- `helper.changePassword` authorizes itself, since it does not go through
  `Allowed`:
  1. name matches `userName`, `Lookup` succeeds, UID in the people range;
  2. limiter wait on `user:` and `addr:`, the same keys as login;
  3. `Authenticate(user, current)` must return **`ErrExpired` specifically** —
     `nil` means the account isn't flagged, so refuse with "sign in and change
     it in Settings"; anything else is `limiter.fail` plus the generic
     wrong-current answer, so a guess learns nothing and a stranger looks like
     a wrong password;
  4. refuse a new password equal to the current one (G7), and cap both at 1024;
  5. `Fnas(["--caller", user, "user", "password", user, "--password-stdin"], new)`
     — the same call the signed-in flow already makes, which fnas's caller gate
     permits for a non-admin (`separated-login.sh` proves it). It clears the
     flag, sets both passwords, restores SMB and ends sessions;
  6. **no token and no session are issued.** The browser then logs in normally.
- `front.go`: `POST /api/password/change`, `sameOrigin` like login, `readJSON`
  at 8 KiB, no cookie, answers mapped by `writeFailure` — so throttling arrives
  as 429 with `Retry-After` for free.
- `api.ts`: `login()` throws an error carrying `must_change`;
  `changeExpiredPassword(user, current, password)` posts to the new endpoint.
- `login.tsx`: on that signal the card becomes "Choose a new password" —
  current (already typed, kept), new, confirm — and on success calls `login()`
  with the new password and enters the desktop. Refusals use the existing
  notice. No new route, no session held while unauthenticated.

The step is model-agnostic: on the old model `Authenticate` returns `ErrExpired`
from PAM and `fnas user password` runs `chpasswd`, which clears `lastchg`. Only
the writer is store-only.

### 4. Migration (G6)

`separation_plan` notes a person whose shadow `lastchg` is 0 with a usable hash,
and `write_store` carries that into `must_change`; `lock_down` disables their
SMB password with the flag. Narrow, and covered by its own test.

### 5. Docs

Design doc rows 5 and 6 flip to what is built **only once it is tested** — the
status-drift rule. This plan and the session handoff record the rest.

## Tests

**Unit** (`nas/tests/unit/test_fnas.py`, no container): the writer sets and
clears, refuses an unknown name, and refuses on a non-separated NAS; the SMB
disable/enable calls including the `enabled off`/`on`-while-flagged edges;
`set_password` restores SMB; `print_users` shows the flag; migration carries
`lastchg 0`. Keep green: `3599` (set clears), `3638` (`check_login` → expired).
Go: `helper_test.go:107-110` already asserts the refusal; add the new op's
cases — not-flagged refused, wrong current counted and throttled, reuse
refused, no token in any answer, turned-off and stranger refused, `MustChange`
set on the login refusal.

**Integration** — extend `nas/tests/integration/separated-login.sh`, which
already stands up smbd, sshd, the helper and the web front with `api`/`login`
helpers:

- flag through the admin API → login 401 with `must_change`, no cookie;
- **smbclient while flagged — this decides G5**; WebDAV 401; `fnas auth check`
  refuses, so SFTP is refused;
- the login-page change: wrong current refused and throttled, reuse refused,
  right current changes it, no session was issued by the change itself;
- afterwards: desktop login with the new password, old one refused, SMB takes
  the new one and refuses the old, SFTP and WebDAV work again;
- a live session dies when the flag is set;
- `--enabled off` while flagged stays disabled; `--enabled on` while flagged
  leaves SMB and the desktop refused; clearing the flag without a password
  change restores both;
- the flag visible in `--json user list` and in the text list;
- foreign Origin → 403; a not-flagged person and a stranger get no change.

Rebuild into the gitignored `nas-repo` first: `nas/packages/build.sh` in a
`debian:trixie` container, or the fast tools-only `dpkg-buildpackage -b -us -uc`
plus `dpkg-scanpackages --multiversion`. The frontend build needs
`FELLOWSHIP_THEMES_DIR` pointed at the repo's `themes/`.

**Installed VM** — reuse `/tmp/nas-verify/{console,install,desk}.py` and the
second-NIC forwards: admin flags a person in Users (badge shot) → their login
shows the choose-a-new-password card (shot) → the change completes and the
desktop opens (shot) → SMB from the host takes the new password and refuses the
old → root's console path still works when no admin can log in, and check what
the console screen says when the only admin is flagged.

## Gates

- **A — writer, badge, SMB gating** (fnas + Users): unit and
  `separated-login.sh` green, per-item verdicts, pause for review.
- **B — the change at the login page** (helper, front, `login.tsx`): unit and
  integration green, per-item verdicts, pause for review.
- **C — installed VM**: screenshots and verdicts, then the handoff.

Inconclusive stays open. Nothing pushed; `nas-edition` is shared, so any push
lists `origin/nas-edition..HEAD` and separates mine from theirs first.

## Out of scope

The physical-LAN DHCP run; the installer LVM edits; password-age policy (G9)
and a second factor; any change to root's SSH or the phase-7 Samba rulings.
