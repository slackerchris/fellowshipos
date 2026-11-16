# Phase 6: network changes with rollback — implementation and test plan

**Status:** implemented and tested 2026-11-15; the plan below was agreed by
Chris before coding. Implements phase 6 of
`fellowship-nas-account-separation.md`, which records the results. Rulings
given by Chris (2026-11-15): keep ifupdown, preserve the existing IPv6
configuration, leave the uncommitted installer LVM fix untouched, and the
four decisions recorded at the end, all taken as recommended.

Everything here was checked against the code on branch `nas-edition`
(`d550d95`), not against the design doc alone.

## What exists today

- No `fnas network` area, no rollback units, no entries in either maintenance
  lock. Settings → Network is a `NotBuilt` placeholder (it says `phase={15}`;
  the main plan puts network settings in phase 14 — the placeholder number is
  stale and goes away when this is built).
- The installer writes ifupdown config per wired port
  (`installer/internal/engine/engine.go`): `/etc/network/interfaces.d/<port>`
  with `allow-hotplug`, `iface <port> inet dhcp`, `iface <port> inet6 auto`.
- First boot (`config/includes.chroot/usr/local/bin/fellowshipos-setup`) already
  picks the LAN from the **default-route interface** and writes its subnet to
  `/etc/fellowshipos/nas-lan`.
- `shares.set_lan()` writes that file and calls `write_config()`, which
  regenerates NFS exports, every Samba share file and every SFTP view.
- The image has no resolvconf and no systemd-resolved: dhclient writes
  `/etc/resolv.conf` directly. Avahi **is** in the image (a dependency of
  `fellowship-nas-smb`), so `https://<name>.local/` follows an address change,
  and the self-signed certificate already carries that name.

## The browser-to-helper path, traced

1. Browser → HTTPS on `:443` (all interfaces), served by `fellowship-nas-web`
   running as the unprivileged `fnas-web` account
   (`cmd/fellowship-nas-web/main.go`).
2. `POST /api/fnas` → `front.withSession` (`internal/front/front.go`):
   - `sameOrigin`: `Sec-Fetch-Site: same-origin` and `Origin == https://<Host>`;
   - the session cookie `__Host-fellowship-session` — the `__Host-` prefix makes
     it **host-only**: it is never sent to a different address;
   - `OpCheck` to the helper, then the CSRF header `X-Fellowship-Token`
     compared with the session's token.
3. The front calls the helper over the unix socket
   `/run/fellowship-nas-web/helper.sock` (`internal/protocol/protocol.go`),
   one JSON request per connection. `OpFnas` carries the session token, args
   and input — **no address information at all today**. Only `OpLogin` carries
   `Remote`, the client's *source* address.
4. The helper socket is `0660 root:fnas-web`; the helper admits only that UID
   or root via `SO_PEERCRED` (`cmd/fellowship-nas-helper/main.go`). It runs as
   root, checks `Allowed()` (the `commands` map plus `maintenanceArgs` shapes),
   then runs `fnas --caller <name> …`, where `maintenance.py` applies the
   second lock and writes the audit line.

### Why the client's source IP can't prove confirmation

The source address is the *client's*. The same laptop reaches the NAS at the
old address and at the new one with the same source IP, so `Remote` cannot say
which NAS address was dialed. `Host` and `Origin` are what the browser claims
about the URL; they are checked against each other for CSRF, not against
where the connection actually landed.

### The proof: the connection's local (destination) address

The kernel only delivers a TCP connection to a socket bound to an address the
machine actually holds. A connection that arrived at the new address has
`LocalAddr() == new address`, and the client cannot forge that. So:

1. **Front:** capture `conn.LocalAddr()` per connection with
   `http.Server.ConnContext` (the standard Go mechanism), and carry it as a new
   `Local` field on `protocol.Request` for `OpFnas` (and `OpLogin`, for
   symmetry). This rides the same trust boundary as today's `Remote`: the
   front is the only account that can reach the helper socket, and the helper
   already trusts it for the source address.
2. **Helper:** `network confirm` is gated before `fnas` runs — the session
   must be a NAS admin, a pending change must exist, and `request.Local`
   (host part) must equal the pending change's target address. The pending
   state is read through `fnas --json network show`, so fnas stays the single
   owner of the state format. Only then does the helper run
   `fnas network confirm --caller <name>`.
3. **fnas:** `network confirm` requires a pending change, stops the rollback
   timer, clears the state, audits and alerts. fnas never sees the connection,
   so the address proof lives in the helper alone, by necessity; the state
   machine stays two-layered (a confirm with no pending change is refused by
   fnas even if the helper's gate were bypassed).

Two consequences, both intended:

- **A fresh sign-in at the new address.** The host-only cookie doesn't follow
  the browser, so the admin signs in again at `https://<new address>/`. That
  login's connection lands on the new address, and the confirm from that
  session carries `Local == new`. The design's "signed in as a NAS admin at
  the new address" falls out of the existing cookie rules with no change.
- **The old tab can never confirm**, even if the old address is still alive
  (an alias, a second port): its connections land on the old address, so
  `Local != target` and the helper refuses. This is testable end to end.

Confirm over IPv6 (the preserved `inet6 auto` gives SLAAC) carries a v6
`Local` and is refused against a v4 target; the desktop links to the literal
new IPv4 address, which forces the browser onto v4.

## The LAN interface on a multi-port install

**Agreed (Chris, 2026-11-15):** follow the convention first boot already uses.

1. The interface of the first default route (`ip -4 route show default`,
   lowest metric first).
2. No default route: the interface whose connected subnet matches the
   configured `nas-lan`.
3. Still ambiguous (several default routes on different ports, or none and no
   match): `network apply` refuses with a clear error. Root can pin the choice
   with a new root-only `fnas network interface NAME`, persisted in
   `/etc/fellowshipos/nas-network.json`. `network show` reports the interface
   and how it was chosen.

The override also makes the integration tests deterministic: the container's
default route belongs to docker's `eth0`, and the tests point fnas at the
test veth instead.

## lan_subnet: one transaction, both directions

**Agreed (Chris, 2026-11-15):** the LAN subnet follows the network change,
through the existing machinery, inside the same transaction.

- `apply` derives the new subnet: static → the network of address/prefix;
  DHCP → the subnet of the acquired lease.
- If it differs from `nas-lan`, apply calls `shares.set_lan(new)` — which
  already regenerates NFS exports, Samba share files and SFTP views. The old
  `nas-lan` is part of the backup.
- Rollback restores the old `nas-lan` through `set_lan(old)`, so exports and
  share access follow back. NFS "home network" access and share access are
  consistent with the live LAN after apply, after confirm and after rollback.
- Validation reuses `check_subnet`'s bounds (private, /8–/30), so a static
  prefix that would make an unusable LAN subnet is refused up front.
- `fnas lan set` stays as the manual override it is today; nothing changes it.

## The change itself (ifupdown, IPv6 preserved)

### State

`/var/lib/fellowship-nas/network-rollback/` (root, 0700):

- the saved `interfaces.d/<iface>` file, the saved `nas-lan`, the saved
  `resolv.conf`;
- `pending.json`: interface, mode, target address (static: configured; DHCP:
  acquired, or null until the lease arrives), deadline, start time.

### `fnas network apply`

`--dhcp`, or `--address A/P --gateway G --dns D[,D…]`.

1. Refuse if a pending change exists (one at a time). Validate: private
   address, prefix /8–/30, not the network or broadcast address, gateway
   inside the subnet, DNS servers valid IPs. Both locks check the shapes.
2. Back up the interface file, `nas-lan`, `resolv.conf`; write `pending.json`.
3. Start `fellowship-network-rollback.timer` (transient, `OnActiveSec=120`).
   Started *before* the change, as the design says, so a half-finished apply
   still rolls back.
4. Write the new `interfaces.d/<iface>`: the installer's shape for DHCP;
   `iface <iface> inet static` + address/gateway for static, with `dns-*`
   handled by writing `/etc/resolv.conf` (no resolvconf in the image).
   **The existing `inet6` stanza is copied over verbatim**; if the file has
   none, none is added. Nothing else in the file is touched.
5. `ifdown <iface> && ifup <iface>`.
6. DHCP: poll for the lease up to 30 s; record the acquired address in
   `pending.json`. No lease in time → restore the backup at once, stop the
   timer, report the failure (agreed: fail fast rather than wait out the
   window).
7. Follow the LAN subnet if it changed (above).
8. Answer `{address, deadline}`. The answer may never reach the browser — the
   connection it came in on dies when the address changes — so the desktop
   treats apply as fire-and-forget and rediscovers the state (below). The
   helper and fnas finish regardless; only the browser's TCP connection is
   lost.

Any caught failure restores the backup, stops the timer and clears the state.

### `fnas network confirm`

Root-only to callers except through the helper's gate. Requires a pending
change; stops the timer, clears the state, keeps the config, audits under the
caller and alerts.

### `fnas network rollback` (root-only; the unit's ExecStart)

No pending state → exit 0 (idempotent). Otherwise restore the three saved
files, `set_lan(old)`, `ifdown/ifup`, alert "Network change rolled back",
clear the state. Runs as a systemd oneshot, independent of the helper and the
web server — the design's core property.

### Reboot during the window

**Agreed (Chris, 2026-11-15):** the rollback service is also wanted at boot
(`WantedBy=multi-user.target`, the updates-service pattern). If it finds a
pending change at boot it rolls back unconditionally: every desktop session
died with the reboot, so no confirm can arrive anyway, and the known-good
configuration is the safe state. A confirmed change has no pending state and
boots normally.

### Units

- `fellowship-network-rollback.service`: `Type=oneshot`,
  `ExecStart=/usr/sbin/fnas network rollback`, `[Install]
  WantedBy=multi-user.target` (boot guard).
- `fellowship-network-rollback.timer`: `OnActiveSec=120`,
  `Unit=fellowship-network-rollback.service`. Started by apply, stopped by
  confirm or rollback; never enabled.

### `fnas network show` (read)

JSON: interface and how it was chosen, mode, current address/prefix, gateway,
DNS, LAN subnet, and `pending` (mode, target address, deadline) or null.
This is what the desktop polls and what the helper reads for the confirm gate.

### `fnas network interface NAME` (root-only)

Pin the LAN interface (Decision 1). Persists; `show` reports it.

## How the desktop learns the new address

- **Static:** the address is known before apply. The Network panel shows it
  with the countdown and a link: "Open https://NEW/ and sign in to confirm."
- **DHCP, same lease (the common case):** the connection survives; the apply
  answer and `network show` polling report the address.
- **DHCP, new lease:** the old tab loses the NAS. The address is in
  `pending.json` once the lease arrives, and the **console status screen**
  gains a line with the current address and the pending countdown, so the
  owner reads it off the machine's own screen and types it in.
  `https://<name>.local/` (Avahi ships with the SMB package) is the stable
  way that follows the change without knowing the address; the router's
  lease list is the other way. If nobody confirms, the timer restores the
  old address, which the old tab finds again on its next poll.
- The panel polls `network show` with the background header (polling must not
  keep idle sessions alive) and shows three outcomes: confirmed, rolled back
  (with the alert's wording), or still pending with the countdown.

## The two locks and the audit

- `maintenance.py`: `("network","show")` → READ; `("network","apply")` and
  `("network","confirm")` → MAINTENANCE and ANNOUNCED (the design says network
  changes go to the alert channels). `interface` and `rollback` stay unlisted
  → root.
- `helper.go`: `commands["network"] = {show, apply, confirm}`;
  `maintenanceArgs` cases: `--dhcp` alone, or `--address` CIDR + `--gateway`
  IP + `--dns` comma-separated IPs, nothing extra; `confirm` takes no
  arguments. The existing `MaintenanceInterface` unit test parses helper.go
  and fails if the locks disagree, so it enforces the new entries
  automatically.
- Apply and confirm are audited under the caller's name by the existing
  `audit()`; rollback alerts without a caller (the system did it).

## Files touched

| File | Change |
|---|---|
| `fellowship_nas/network.py` (new) | show/apply/confirm/rollback/interface, validation, ifupdown generation, state, timer, lease wait, LAN follow |
| `fellowship_nas/cli.py` | the `network` area |
| `fellowship_nas/maintenance.py` | READ/MAINTENANCE/ANNOUNCED entries |
| `internal/protocol/protocol.go` | `Request.Local` |
| `internal/front/front.go`, `cmd/fellowship-nas-web/main.go` | ConnContext capture, `Local` on OpFnas/OpLogin |
| `internal/helper/helper.go` | commands, maintenanceArgs, the confirm gate |
| `systemd/fellowship-network-rollback.{service,timer}` (new) | the rollback unit and its timer |
| `web/src/settings.tsx` | the Network panel replaces NotBuilt |
| console status screen script | address line + pending countdown |
| `docs/fellowship-nas-account-separation.md` | phase 6 entry and the test list |

Not touched: the installer (its uncommitted LVM fix stays as it is), Samba
settings, sshd — those are phase 7.

## Test plan

### Unit — Go (`helper_test.go`)

- The confirm gate: pending static change; `Local` = new → allowed; `Local` =
  old → refused; `Local` empty → refused; non-admin → refused; no pending
  change → failed. DHCP pending with the acquired address recorded: same
  checks against that address.
- `maintenanceArgs`: valid and invalid CIDR, gateway, DNS lists, `--dhcp`,
  extra arguments, `confirm` with arguments.

### Unit — Python (`test_fnas.py`)

- Generated files: static and DHCP shapes; the `inet6` stanza preserved
  verbatim; a file without one gains none.
- Validation: public address, bad prefix, network/broadcast address, gateway
  outside the subnet, bad DNS.
- The state machine: a second apply while pending is refused; confirm clears
  the state; rollback with no state is a no-op; a caught apply failure
  restores everything.
- LAN follow: apply into a new subnet updates `nas-lan` and regenerates
  exports; rollback restores both (mocked `run`, as the existing tests do).

### Integration — `nas/tests/integration/network-rollback.sh` (new)

The established pattern: a privileged Debian trixie container, the real
packages, helper and web server started by hand, systemctl stubbed. Because
systemd is stubbed, the timer's *firing* is simulated by running the unit's
ExecStart (`fnas network rollback`) directly; the real timer is proven in the
VM pass below.

Setup: a veth pair — `veth0` carries dnsmasq (10.99.0.1/24, pool
10.99.0.100–150), `veth1` is the NAS side, pinned with `fnas network interface
veth1`. Separated store, admin kim, ordinary ann.

Chris's required cases, each a PASS/FAIL line:

1. **Successful confirmation, static.** Apply 10.99.0.50/24 → the file is
   written with the inet6 stanza intact, the address is live, `nas-lan` and
   the NFS exports followed. Sign in at `https://10.99.0.50/`, confirm → 200,
   state cleared, config kept.
2. **Confirmation through the old address is rejected when the address
   changed.** After a change, re-add the old address as a secondary
   (`ip addr add`) so it is genuinely reachable, sign in through it, confirm
   → 403; then sign in through the new address and confirm → 200. This proves
   the local-address check end to end, not just in a unit test.
3. **Timeout restores the prior configuration.** Apply a second change, do
   not confirm, run the rollback ExecStart → the old file, `nas-lan`, exports
   and `resolv.conf` are back, the old address works, the alert is logged.
4. **Rollback fires after the web server and helper stop.** Apply, kill both
   processes, run the rollback ExecStart → configuration restored; restart
   both, sign in at the old address → works.
5. **DHCP change and address discovery.** Apply `--dhcp` → the lease arrives
   from dnsmasq, the apply answer and `network show` report the acquired
   address, `nas-lan` follows the lease's subnet; confirm through that
   address → kept.
6. **DHCP timeout.** Apply `--dhcp` again with the previous config static,
   don't confirm, fire rollback → the static config is back exactly.

Also in the same script:

- Both locks: ann reaches no network command; bad shapes are refused by the
  helper and by fnas; `network rollback` and `network interface` are refused
  to any caller; a second apply while pending is refused.
- Audit: apply and confirm appear in the journal under kim's name; apply,
  confirm and rollback appear in the alerts; refused requests appear in
  neither.
- IPv6: the stanza survives static and DHCP changes and rollback byte for
  byte.

### VM pass — real systemd, from the built ISO

The container can't run timers, so these are proven on the installed system,
the way phases 4 and 5 were:

- Apply a static change, don't confirm, wait out the 120 s → the timer fires
  on its own, the old configuration is back, the alert arrives.
- Apply, `systemctl stop fellowship-nas-web fellowship-nas-helper`, wait →
  rollback still fires (the design's core property, with real systemd).
- Confirm, wait past the deadline → nothing rolls back.
- Reboot during the window → the boot guard rolls back; a confirmed change
  reboots normally.
- DHCP against the VM's real network: the desktop learns the address from
  `network show` when the lease is unchanged, and from the console status
  screen when it changed; confirm from the new address works.
- Two-NIC VM: the default-route interface is chosen; the root override pins
  the other one; apply refuses while ambiguous.

## Implementation notes (2026-11-15)

Things the building and testing turned up, beyond the plan above:

- **resolv.conf is written in place, not replaced.** It can be a bind mount
  (a container) or a symlink, and `os.replace` onto a mount fails with EBUSY.
  dhclient writes it in place too. The interface file and the LAN file keep
  the usual atomic replace.
- **Restoring is best effort about the interface, strict about the state.**
  If `ifup` fails on the saved configuration too, the files are still back
  and the pending state is still cleared; a failed restore must not leave a
  change waiting forever.
- **The test router lives in its own network namespace.** A gateway that is
  a local address (the same netns) is refused by the kernel ("Nexthop has
  invalid gateway"), and docker's own default route collides with the one
  ifup adds. The harness deletes docker's default route after the package
  install and puts dnsmasq across a veth pair in a `router` namespace.
- **The host-only cookie was proven, not just assumed.** The integration
  test signs in at the old address, keeps it reachable as a second address
  on the port, and watches the helper refuse that session's confirm; a
  session made at one address is also no use at another (loopback included),
  and a restarted helper forgets every session.
- **The timer is proven under real systemd**, in a privileged container
  running systemd as PID 1 (`network-rollback-timer.sh`): it fires on its
  own after the window with the desktop stopped, a confirm stops it, and
  the boot guard is enabled. The base trixie image has no systemd; the
  script builds a small derived image that does.
- **The timer needs `AccuracySec=1s`.** The first real-systemd run caught
  it firing late: systemd's default minute of slack lets a 120 s timer fire
  anywhere up to 180 s, while the desktop counts down to the deadline. With
  the accuracy set, the rollback keeps the time it shows.

## Decisions (Chris, 2026-11-15)

1. **LAN interface:** default-route pick (the first-boot convention) with a
   root-only override; apply refuses while ambiguous.
2. **lan_subnet:** follows the change automatically through `set_lan`, in the
   same transaction, and is restored on rollback.
3. **Reboot during the window:** roll back unconditionally at boot — sessions
   die with the reboot, so no confirm can arrive.
4. **DHCP with no lease in 30 s:** restore at once and stop the timer.
