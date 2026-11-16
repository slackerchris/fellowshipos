# NAS installed-system verification — November 15, 2026

Handoff for the session that built a fresh NAS ISO from the **committed**
`nas-edition` and ran the installed-system checks that phases 6 and 7 had left
open. Chris's rules for it: the uncommitted installer LVM fix stays out of the
ISO, the full must-change flow stays out of scope, everything stays local, and
a push would publish all eight pending commits (none pushed).

Every verdict below was observed on the installed machine — its own screen
via QEMU monitor screendumps, its desktop API over HTTPS, and SSH from the
host — not inferred from the container tests.

## How the ISO and the install were made

- **Clean worktree at `64e971b`** (`git worktree add … 64e971b`), because
  `build.sh` compiles the installer from the working tree: building in
  Chris's checkout would have baked his uncommitted LVM fix into the ISO.
  The worktree's `git status` was clean before and after the build.
  `FELLOWSHIP_EDITIONS=nas ./build.sh` (packages + live-build in Docker).
  The ISO was copied to the checkout root
  (`fellowshipos-nas-amd64-1.0.hybrid.iso`, gitignored) and the worktree
  removed afterwards.
- **Install through the graphical installer's own API**, the way a browser on
  another computer does it: `nas/tests/vm/vm.sh remote` (HTTPS on
  127.0.0.1:8443, pairing code read from the serial log), then pair →
  `/api/machine` → `/api/recovery-key` → `/api/install` with `erase vda` and
  the key's last two groups. All eight steps finished; the recovery key is
  saved at `/tmp/nas-verify/recovery-key.txt`. Accounts: `kim` (NAS admin,
  shares-only as the installer makes it) and root with its own password.
- **Boot and drive**: `vm.sh boot` on the same work dir; the console typed
  through the QEMU monitor (`sendkey`), screens captured with `screendump`
  and converted to PNG. Drivers kept in `/tmp/nas-verify/`
  (`console.py`, `install.py`, `desk.py`, `rootssh-check.sh`); screendumps in
  `/tmp/nas-verify/shots/` (names cited below). The VM was stopped at the end
  of the session; its installed disk survives at
  `/tmp/fellowship-nas-verify/os.qcow2` and boots again with
  `FELLOWSHIP_VM_DIR=/tmp/fellowship-nas-verify FELLOWSHIP_MONITOR=… vm.sh boot`.

## Verdicts

| # | Check | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | ISO from committed source, installer WIP excluded | **pass** | clean worktree at `64e971b`; build succeeded |
| 2 | Graphical install, separated NAS, root + NAS admin | **pass** | installer API, 8 steps done |
| 3 | First boot, NAS role, desktop admin login | **pass** | login 200, `admin: true`; issue screen healthy |
| 4 | P6 console waiting-change line, own monitor (DHCP and static) | **pass** | shots 11, 13: "a change is waiting: confirm at https://10.0.2.15 / …10.0.2.50 by <deadline>, or the previous settings come back" |
| 5 | P6 line gone once kept or rolled back | **pass** | shots 16, 20 |
| 6 | P6 DHCP change against a router outside the NAS | **pass** | QEMU user-mode networking's DHCP: lease 10.0.2.15, gateway 10.0.2.2, DNS 10.0.2.3 learned; apply answer + `network show`. A physical-LAN-router run is still Chris's |
| 7 | P6 rollback timer under installed systemd (file, state, timer, alert, console) | **pass** | two "Network change rolled back" alerts in the alert store; timer `inactive`; pending cleared; dhcp stanza restored; console line gone (shots 20–22) |
| 8 | P6 rollback restores the **live address** on a DHCP-booted NAS | **fail — open finding** | leftover static stays primary after rollback (shots 17, 21); see below |
| 9 | P6 confirm gate refuses a confirm arriving at the old address, installed | **pass** | 403 "Confirm from the NAS's new address: open it in the browser and sign in there." — QEMU's forward delivers at the old lease address, and the gate caught it |
| 10 | P6 confirm from the new address via the desktop, installed | **not repeated here** | container integration proves it (51 checks); from the host, NAT delivers at the old address, and a guest-originated confirm was not typed out. Not a fail — an unchecked repetition |
| 11 | P7 console root line, off | **pass** | shot 00: "Root: logs in here, at the console, for system administration" |
| 12 | P7 console root line, on | **pass** | shot 10: "Root: SSH with a key, and here at the console" (after the issue timer's two-minute tick) |
| 13 | P7 console root line back after off | **pass** | shot 11 |
| 14 | P7 `fellowship-root-ssh on` without a key refused | **pass** | shot 02: "add a public key to /root/.ssh/authorized_keys first…" |
| 15 | P7 root SSH end to end, key, from another machine | **pass** | host `ssh -p 2222 -i rootkey root@127.0.0.1` ran `fnas --json ssh show` (`root_ssh: true`) |
| 16 | P7 root password over SSH refused | **pass** | `rootssh-check.sh refuse-password` |
| 17 | P7 `off` refuses the key again | **pass** | `rootssh-check.sh refuse-key` after off |
| 18 | P7 root console login with the installer-set password | **pass** | shot 01 |
| 19 | Reboot lands clean (boot guard idle, DHCP lease primary) | **pass** | `network show`: dhcp, 10.0.2.15, pending null |

SSH has no hardware-dependent part, so the installed VM is the whole of check
15; a run on the Dell remains available to Chris but would add nothing for
sshd. The must-change flow was left out of scope, as instructed.

## The open finding: leftover addresses across method changes (phase 6)

**Observed.** On this NAS, which booted with DHCP:

- DHCP → static: the static address becomes primary, but the boot lease stays
  on the interface as `secondary dynamic` (shot 15: `.50` primary, `.15`
  secondary). The old address therefore stays reachable — which is also why
  check 9 could be observed at all.
- static → DHCP: `fnas network apply --dhcp` reported the **leftover static**
  address as the lease ("The NAS is now at 10.0.2.50", shot 17), because
  `current_address` reads the interface's primary address and the leftover
  static is still primary; the real lease sat secondary.
- Rollback: file, pending state, timer, alert and console line all restored,
  but the live address was **not** — the abandoned static stayed primary
  (shot 21: `.60` `valid_lft forever` primary, `.15` secondary), so the NAS
  kept answering at the address the change was meant to leave, and the
  console's `Open:` line showed it.

**Root cause.** `network.py`'s `restart_interface` (and `put_back`) rewrite
the ifupdown stanza *before* `ifdown`, so `ifdown` acts on the new method and
never releases the old one: the boot `dhclient` survives a switch to static,
and on a switch back to DHCP `ifup`'s dhclient sees a running client and skips
the fresh-bind path that would flush the interface. Addresses from the
previous method are never flushed explicitly.

**Why the container tests pass.** `network-rollback.sh` starts from a static
configuration with no boot dhclient, so every DHCP `ifup` starts a fresh
client whose bind flushes the interface. The DHCP-booted case — every real
NAS's first boot — was never in the harness.

**Proposed fix (Chris's call, not applied).** In `restart_interface` and
`put_back`, before `ifup`: release/stop any dhclient on the interface and
flush its IPv4 addresses (`ip -4 addr flush dev NAME`), so the new method
starts from a clean interface regardless of what ran before. Add an
integration case that starts from DHCP (a boot-like state) and asserts the
old address is gone after a confirmed static change and after a rollback.
Until then, a reboot clears the leftovers (verified: check 19).

## Pitfalls for the next session

- **The issue screen redraws on a two-minute timer** (and on fnas network
  events). A root-SSH state change shows on the machine's screen only after
  the next tick; and the issue block lives above the *login prompt* — log out
  (`exit`) to see it, because an active shell scrolls it away.
- **Getty prompt timing**: after a failed or finished login, getty respawns
  with a delay; typed characters land in the wrong prompt if the waits are
  short. Use ~4 s between username and password, and screendump to confirm
  the shell before typing commands.
- **QEMU's port forward delivers at the DHCP-learned address**, not whatever
  address the guest currently prefers — so a desktop confirm from the host
  arrives at the old address while a lease lingers. The gate's refusal is
  correct behavior, not a harness bug.
- **Host port 8000 was already taken** by another service; the guest's first
  key fetch got that service's response. Use a checked-free port (8017 here)
  and overwrite (`>`) the key file, don't append.
- `sftp -b` and `PerSourcePenalties` pitfalls from the phase 7 session still
  apply to any new SSH harness (see that handoff).

## State at the end of the session

- ISO: `fellowshipos-nas-amd64-1.0.hybrid.iso` at the checkout root
  (gitignored), built from `64e971b`. Build worktree removed.
- VM stopped; installed disk kept at `/tmp/fellowship-nas-verify/os.qcow2`
  (boot it again with `vm.sh boot` and the same `FELLOWSHIP_VM_DIR` /
  `FELLOWSHIP_MONITOR`). Screendumps and drivers in `/tmp/nas-verify/`.
- Docs updated: the phase 6 and 7 entries and the test list in
  `fellowship-nas-account-separation.md`, this handoff. Committed locally.
- **Nothing pushed**: eight commits ahead of `origin/nas-edition`, shared
  branch — list and show Chris before any push.
- Chris's installer LVM fix: still uncommitted, untouched, and excluded from
  the ISO by construction.
