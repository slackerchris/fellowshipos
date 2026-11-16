# NAS network rollback session — November 15, 2026

Handoff for the session that reviewed the FellowshipNAS account-separation
work, planned phase 6 with Chris, and then built and tested it: **network
changes with rollback**. Everything below was verified against the code and
by running the tests, not taken from the design doc's status claims.

## Current status

- **Phases 1–5** of `docs/fellowship-nas-account-separation.md` were already
  done and committed (`4d195c5`, `91f608d`, `d302f20`, `86f6e78`, `cd950c9`).
  The review confirmed the doc's claims matched the code.
- **Phase 6 (network changes with rollback) is implemented and tested.**
  The agreed plan is `docs/fellowship-nas-network-rollback-plan.md`; the
  results are recorded in the phase 6 entry of the account-separation doc,
  and its "Tests that must pass" list now covers the network.
- **Phase 7 (SSH and Samba settings) is next and not started:**
  `AllowGroups`, root SSH through `fellowship-root-ssh`, the SFTP PAM stack
  and `AuthorizedKeysCommand`, Samba's password sync turned off. The design
  is already in the account-separation doc; the doc notes these ship with
  phase 4 for new installs and phase 5 for migrated ones, so check what is
  already in place before building.
- **Committed on `nas-edition`** — one commit, 20 files, `nas:` prefix per
  PROJECTS.md, this handoff included. **Not pushed** — the branch is shared
  with concurrent sessions, so a push needs Chris's go-ahead and a check of
  `git log --oneline origin/nas-edition..HEAD` first.

## Repository and local changes

| Repository | Branch and baseline | Changes from this session |
| --- | --- | --- |
| `/home/chris/Git/fellowshipos` | `nas-edition`, `d550d95` | Phase 6 code, units, tests, UI, console line, docs, this handoff |

**Chris's uncommitted installer LVM fix was left untouched**
(`installer/internal/engine/engine.go`, `engine_test.go` — closing only
volume groups actually on the chosen disk, with a test). It is unrelated to
phase 6 and must not be staged with it. Verify with
`git diff --stat -- installer/` (3 + 27 lines, as it was before the session).

Phase 6 changes, grouped:

- **New:** `nas/packages/fellowship-nas-tools/fellowship_nas/network.py`;
  `systemd/fellowship-network-rollback.{service,timer}`;
  `nas/tests/integration/network-rollback.sh` and
  `network-rollback-timer.sh`; `docs/fellowship-nas-network-rollback-plan.md`.
- **fnas:** `cli.py` (the `network` area and dispatch), `maintenance.py`
  (show → read; apply/confirm → maintenance and announced; rollback and
  interface stay root-only), `debian/install` (the two units).
- **Helper/front:** `protocol.go` (`Request.Local`), `front.go`
  (`ConnContext` capture, `Local` on login and fnas), web `main.go`
  (`ConnContext` on both servers), `helper.go` (commands, argument shapes,
  the confirm gate), `helper_test.go` (gate test, shape cases).
- **Desktop:** `web/src/settings.tsx` (Network panel replaces the `NotBuilt`
  placeholder that wrongly said phase 15), `web/src/api.ts` (`NetworkState`).
- **Console:** `config/includes.chroot/usr/local/sbin/fellowship-nas-issue`
  (a waiting-change line: where to confirm, and the deadline).
- **Docs:** account-separation status line, phase 6 entry, test list.
- **Tests:** `nas/tests/unit/test_fnas.py` (13 Network tests).

Build artifacts land in the gitignored
`config/includes.chroot/usr/share/fellowshipos/nas-repo/` and `web/dist/`;
they do not show in `git status`.

## The design, in brief

Full detail and Chris's rulings are in the plan doc. The load-bearing parts:

- **Confirm is proved by the connection's local (destination) address**, not
  the client's source: the same laptop reaches the old and new NAS addresses
  with the same source IP. The front takes `conn.LocalAddr()` via
  `http.Server.ConnContext`, a new `Local` field carries it over the unix
  socket (same trust boundary as today's `Remote`; the socket is 0660
  root:fnas-web with `SO_PEERCRED`), and the helper refuses a confirm that
  didn't arrive at the pending target address. fnas owns the pending state
  and is asked via `fnas --json network show`; it never sees the connection.
- **The session cookie is `__Host-` prefixed (host-only)**, so the admin
  must sign in again at the new address — exactly what the design asks.
  This was proven in the integration test, not assumed: a session made at
  one address is no use at another, loopback included.
- **ifupdown stays**, as the installer writes it; the IPv6 stanza is
  preserved verbatim, options included. Static DNS goes to `resolv.conf`
  written in place (it can be a bind mount; `os.replace` fails on mounts).
- **The LAN interface** is the default-route port (the convention first boot
  already uses in `fellowshipos-setup`), falling back to the port in the
  configured LAN subnet; root can pin another with `fnas network interface`.
- **The LAN subnet follows the change** through `shares.set_lan`, which
  regenerates NFS exports, Samba share files and SFTP views; rollback
  restores it. Both directions are tested.
- **Rollback is a systemd unit**, independent of the helper and web server:
  `fellowship-network-rollback.timer` (`OnActiveSec=120`, `AccuracySec=1s`)
  started by apply, stopped by confirm or the rollback itself;
  `fellowship-network-rollback.service` runs `fnas network rollback` and is
  also enabled at boot — a change left pending by a restart is rolled back
  unconditionally, since no session survives to confirm it.
- **DHCP:** apply waits up to 30 s for the lease, records and reports the
  address; no lease → restore at once. The desktop learns the address from
  the apply answer / `network show` when it can still reach the NAS, from
  the console screen or `https://<name>.local/` (Avahi ships with
  `fellowship-nas-smb`) when it can't.

### Chris's decisions (2026-11-15, all recorded in the plan doc)

1. Keep ifupdown; preserve the existing IPv6 configuration.
2. Leave the uncommitted installer LVM fix untouched.
3. LAN interface: default-route pick with a root-only override; apply
   refuses while ambiguous.
4. lan_subnet follows automatically, restored on rollback.
5. Reboot during the window: roll back unconditionally at boot.
6. DHCP with no lease in 30 s: restore at once, stop the timer.

## Verification — what ran and passed

| Check | Result | How |
| --- | --- | --- |
| fnas unit tests | **338/338** | `cd nas/tests/unit && python3 -m unittest test_fnas` (host Python 3.14 works) |
| Go gofmt/vet/tests/build | **clean** | trixie container (no Go on the host — recipe below) |
| Frontend typecheck + build | **clean** | `cd nas/packages/fellowship-nas-web/web && FELLOWSHIP_THEMES_DIR=/home/chris/Git/fellowshipos/themes npm run build` |
| Integration, stubbed systemd | **51/51** | `bash nas/tests/integration/network-rollback.sh` |
| Integration, real systemd | **14/14** | `bash nas/tests/integration/network-rollback-timer.sh` (~5 min: two 120 s windows) |

The integration scripts build nothing themselves; they install from the
local apt repo, so **rebuild the packages first** after any change:

```bash
# Full build (four packages), in a container because the host isn't Debian:
docker run --rm -v /home/chris/Git/fellowshipos:/repo -w /repo/nas/packages debian:trixie \
  bash -c 'apt-get update -qq && apt-get install -y -qq dpkg-dev debhelper golang-go \
           libpam0g-dev libcrypt-dev npm >/dev/null && ./build.sh'

# Tools-only rebuild after a Python/unit change (much faster):
docker run --rm -v /home/chris/Git/fellowshipos:/repo -w /repo/nas/packages debian:trixie \
  bash -c 'apt-get update -qq && apt-get install -y -qq dpkg-dev debhelper >/dev/null && \
    work=$(mktemp -d) && cp -r fellowship-nas-tools "$work/" && \
    (cd "$work/fellowship-nas-tools" && dpkg-buildpackage -b -us -uc) && \
    cp "$work"/fellowship-nas-tools_0.1.0_all.deb \
       /repo/config/includes.chroot/usr/share/fellowshipos/nas-repo/ && \
    cd /repo/config/includes.chroot/usr/share/fellowshipos/nas-repo && \
    dpkg-scanpackages --multiversion . > Packages'

# Go tests alone:
docker run --rm -v /home/chris/Git/fellowshipos/nas/packages/fellowship-nas-web:/src:ro \
  -w /src debian:trixie \
  bash -c 'apt-get update -qq && apt-get install -y -qq golang-go libcrypt-dev libpam0g-dev >/dev/null && \
    gofmt -l . && go vet ./... && go test ./...'
```

The timer script builds a small derived image (`nas-rollback-timer-systemd`,
cached on the host) because the base trixie image has no systemd; it runs
systemd as PID 1 in a privileged container. Both scripts clean up their
containers. The web package's own `dpkg-buildpackage` runs `go test ./...`
as part of the build.

### What the tests cover (Chris's required cases, all passing)

- Successful confirmation, static and DHCP, from the new address.
- Confirm through the **old address**, kept reachable as a second address
  on the port, refused 403; through the new one, kept.
- Timeout restores the file, address, LAN subnet, resolv.conf and IPv6
  stanza, and alerts; rolling back again is a no-op.
- Rollback fires with the web server and helper **killed**; under real
  systemd the timer fires on its own at the deadline with both stopped.
- DHCP lease discovery (apply answer + `network show`), confirm from the
  lease, and unconfirmed DHCP restoring the static file byte for byte.
- LAN subnet follows into another subnet and comes back on rollback.
- Both locks: non-admins, bad shapes (helper 403), public address (fnas
  422), root-only rollback/interface refused to any caller; a second apply
  while one waits is refused; journal and alerts record what was asked for
  and nothing refused.
- Boot guard enabled; a pending change rolled back as at boot; a confirmed
  change still there after the window.

## What testing caught (fixed; pitfalls for next time)

1. **`resolv.conf` EBUSY** — atomic `os.replace` fails on a bind mount
   (docker) or symlink. Written in place now, as dhclient does.
2. **A failed restore left the change pending** — `ifup` failing on the
   saved config skipped state cleanup. Restore is best effort on the
   interface, strict on clearing state.
3. **The timer fired up to 60 s late** — systemd's default `AccuracySec`
   is a minute; a 120 s timer could fire at 180 s while the desktop counts
   down to 120. The unit sets `AccuracySec=1s`. Only the real-systemd test
   could catch this; the stubbed harness never would.
4. **Harness topology** — the test router (dnsmasq) must live in its own
   netns: a gateway that is a local address is kernel-refused ("Nexthop has
   invalid gateway"). Docker's own default route must be deleted after the
   package install: ifup's static method adds a default route and collides
   with it ("RTNETLINK answers: File exists").
5. **Sessions die with the helper and don't cross addresses** — the test
   re-signs-in after restarting the desktop and per address. This is the
   designed behavior (host-only cookie, in-memory sessions), not a bug.

## Open items (honestly)

- **Installed-system VM check, not done:** the console screen's
  waiting-change line on the machine's own monitor, and a DHCP change
  against a real router. Needs the next ISO build — no ISO exists in the
  checkout now. `nas/tests/vm/vm.sh` is the harness (`install`, `boot`,
  `nas`, `remote`; OVMF is present on the host). Phases 4 and 5 were
  checked this same way, by hand, after an image build. The phase 6 entry
  records this as still to check.
- **Phase 7 not started** (scope above).
- **Not pushed.** Phase 6 is the commit "nas: network changes with
  rollback, confirmed from the new address" (`git log --oneline -3`).
  Chris's installer LVM fix was deliberately kept out of it and is still
  uncommitted in the tree — it is his separate change.
- The account-separation doc's "What changes in the code" section is the
  original design list; the phase entries are the as-built record.

## Picking up

1. Read `docs/fellowship-nas-network-rollback-plan.md` (design + rulings +
   implementation notes) and the phase 6 entry in
   `docs/fellowship-nas-account-separation.md`.
2. `git status` — confirm the installer WIP is still Chris's alone.
3. Re-run the verification table above after any change; rebuild packages
   before the integration scripts.
4. For phase 7, start from the design doc's maintenance table and
   `ssh.py`/`sftp.py` as they stand — some of it shipped with phases 4/5.
