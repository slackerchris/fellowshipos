# NAS leftover-address fix and re-verification — November 16, 2026

Handoff for the session that fixed the phase 6 leftover-address finding from
`docs/fellowship-nas-iso-verify-session-2026-11-15.md`, added a regression
that starts from an active DHCP lease, and re-verified everything on an
installed VM built from a clean worktree. Chris's rules: scope the cleanup to
the managed LAN interface, preserve other interfaces and IPv6, check apply
and rollback, make DHCP report the newly acquired lease, check live
reachability, give the host a direct path to the new IP for the desktop
confirm if possible, leave the installer LVM edits untouched, commit locally
without pushing.

Commits: `10012d2` (retire + regression), `8ce9e4e` (release with whichever
DHCP client the system ships). The ISO used for the installed checks was
built in a clean worktree at `8ce9e4e`; the installer LVM fix was excluded by
construction and remains uncommitted in the tree.

## The bug, restated

On a NAS that booted with DHCP, `ifdown` reads the already-rewritten stanza,
so it never releases the old method's client; the boot client survives, and
`ifup`'s client then skips the fresh-bind flush. A static change kept the old
lease as a secondary address, a DHCP change reported the leftover static
address as its "lease", and a rollback restored the file and state but left
the abandoned address live. The container harness starts from static with no
boot client, which is why its 51 checks passed.

A second defect surfaced while fixing it: `common.run` raises for a missing
binary even with `check=False`, and the image ships **dhcpcd**, not
isc-dhcp-client — so a dhclient-only release aborted apply with
"dhclient is not installed", and the raise inside `put_back` skipped the
state cleanup, leaving a stuck pending change.

## The fix (`fellowship_nas/network.py`)

- `retire_addresses(name)`: release whichever client is present —
  `dhclient -r -pf /run/dhclient.<name>.pid <name>` (isc-dhcp-client, the
  harness and some installs) or `dhcpcd -k <name>` (the image) — then
  `ip -4 addr flush dev <name>`. Releasing alone is not enough: dhclient -r
  keeps the address. Scoped to the one interface; IPv6 and every other port
  untouched; a missing client is not an error.
- Called from `restart_interface` (apply) and `put_back` (rollback), before
  `ifdown`/`ifup`, so the new method starts from a clean port and DHCP's
  `wait_for_address` sees the newly acquired lease, not a leftover.
- `put_back`'s bring-up is wrapped in `contextlib.suppress(FnasError)` so a
  failed release can never skip the state cleanup again.

## Regression: `nas/tests/integration/network-lease.sh` (35 checks)

Starts with an **active DHCP lease** on a veth LAN behind a real dnsmasq in
its own namespace, plus a second unmanaged port with its own IPv4 and IPv6,
and a static IPv6 stanza on the managed port. Proves:

- switching to static retires the lease: client process gone, lease address
  gone, the static is the port's only IPv4; the retired address is not
  reachable over HTTPS while the new one is;
- the timeout (the rollback unit's ExecStart) restores DHCP, removes the
  abandoned static, re-acquires a lease, and the restored lease is reachable
  while the abandoned address is not;
- switching back to DHCP reports the lease it actually acquired: the apply
  answer equals the live primary address equals the router's lease file;
- the second port's IPv4 and IPv6, and the managed port's IPv6 stanza and
  live address, survive every change;
- confirms from the live address through the real desktop API.

## Verification totals

| Check | Result |
| --- | --- |
| fnas unit tests (3 new: dhclient scoping, dhcpcd variant, no-client rollback) | **363/363** |
| `network-lease.sh` (new, DHCP-start) | **35/35** |
| `network-rollback.sh` | **51/51** |
| `network-rollback-timer.sh` (real systemd, at `8ce9e4e`) | **14/14** |
| Installed VM, fresh install from the `8ce9e4e` ISO | below |

## Installed VM (QEMU/KVM, second NIC with direct host forwards)

The VM boots with its normal slirp NIC (management, host port 8440) plus a
second user-mode NIC on 10.0.3.0/24 whose host forwards name the guest
address explicitly: 8443→10.0.3.15, 8445→10.0.3.50, 8446→10.0.3.60. That gives
the host a **direct path to each LAN address**, so the desktop confirm can be
exercised from the new address itself. First boot only writes a stanza for
the detected LAN port, so the second port (`ens9`) was given a DHCP stanza at
the console, brought up, and pinned with `fnas network interface ens9`.

| Check | Verdict | Evidence |
| --- | --- | --- |
| Static change retires the boot lease | **pass** | shot 43: `ens9` holds only 10.0.3.50 (`valid_lft forever`); the 10.0.3.15 lease is gone; `ens3` keeps its own lease untouched |
| Desktop confirm **from the new address**, installed | **pass** | confirm over 8445 (→10.0.3.50) returned 200 "The network change is kept." — the check the previous session could not repeat |
| Switch back to DHCP reports the actual lease | **pass** | apply answer and `network show` both 10.0.3.15; confirm over 8443 returned 200 |
| Unconfirmed static: console waiting line | **pass** | shot 44: "a change is waiting: confirm at https://10.0.3.60 by 15:29, or the previous settings come back" |
| Timeout rollback removes the abandoned static | **pass** | shot 45: `ens9` holds only the dynamic lease 10.0.3.15; timer `inactive`; host: lease path 200, abandoned path (8446) refused; alert store has "Network change rolled back" |
| IPv6 preserved on the managed port | **pass** | shot 45: RA global (`fec0::…/64`) and link-local both present after the rollback |
| Other interface untouched throughout | **pass** | `ens3` lease unchanged in every shot; management path (8440) answered throughout |

Screendumps: `/tmp/nas-lease-shots/40…45-*.png`. Drivers:
`/tmp/nas-verify/{console,install,desk}.py` (base URL and monitor socket now
env-overridable). The VM was stopped at the end; its disk survives at
`/tmp/fellowship-nas-lease/os.qcow2`.

## Notes and pitfalls

- **The image's DHCP client is dhcpcd** (`dhcpcd-10.1.0` in the ifup output),
  pulled in by dependencies, not listed in the package lists. Any future
  client-side network code must handle both clients; `shutil.which` guards
  because `common.run` raises on a missing binary regardless of `check`.
- **ifupdown's link toggle drops live IPv6 addresses** (kernel behavior on
  link down); only addresses the stanza re-adds (static inet6) or RA
  re-acquires come back. The fix's flush is `-4` only and adds nothing to
  that; the regression uses a static inet6 stanza so preservation is
  provable, and the VM's RA addresses returned within seconds.
- **Getty timing on the console**: after boot or a failed login, getty
  respawns with a delay; typed characters land in the wrong prompt if waits
  are short. Use ~5 s between username and password and verify with a
  screendump (`whoami`) before typing commands. Two console chains were lost
  this way and redone.
- **`fnas network apply` prints text unless `--json`**; parsers need the flag.
- **Rollback restores the last *confirmed* configuration**, not the boot
  one: a regression that wants "timeout restores DHCP" must confirm DHCP
  first. The first draft of `network-lease.sh` got this wrong and failed
  three checks against correct product behavior.
- A curl to a forward whose guest address no longer exists hangs until
  timeout; `desk.py` reports "connection lost", which is the expected signal
  that an address change retired the old path.

## State at the end of the session

- `nas-edition` at `8ce9e4e`, **eleven commits ahead of origin; nothing
  pushed** (shared branch — list and show Chris before any push).
- ISO at the checkout root (gitignored), built from `8ce9e4e`; build
  worktrees removed.
- Chris's installer LVM fix: untouched, uncommitted, excluded from the ISO.
- Still open, unchanged: the must-change flow (phase 2 residual, Chris's
  separate task) and a DHCP run against Chris's physical LAN router.
