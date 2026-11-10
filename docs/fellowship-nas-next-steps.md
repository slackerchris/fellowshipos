# FellowshipNAS next steps

Working note, written November 9, 2026 against `nas-edition` at `3610577`.
The three canonical documents remain [the plan](fellowship-nas-plan.md),
[the overview](fellowship-nas-overview.md) and
[the review notes](fellowship-nas-changes.md). This file is a short brief for
picking work back up, not a fourth source of truth.

## 1. Where the build stands

| | |
| --- | --- |
| Storage engines built | ZFS, traditional RAID (mdadm), Unraid-style (mergerfs + SnapRAID) |
| Storage engines left | Btrfs (Phase 14) |
| Phases done | 1 to 11, with the gates recorded in the plan |
| Next phase | 12, the Files app, which now comes before more ways to share |
| Tests | 250 Python unit tests pass locally, and CI is green on every push |
| Docs baseline | The overview describes `e99ca0c`; `3610577` is the docs commit after it |

## 2. What the docs get right, and the two lines that drift

Verified consistent across README, overview and plan:

- README lists ZFS, traditional RAID and Unraid-style as built, with Files next,
  then the share protocols, then Btrfs.
- The overview's roadmap reads 12 Files (with the phone home-screen scope) and 13
  protocols, and its Settings row includes storage check schedules.
- The Phase 11 row records a shortened gate and names what was never tried in the
  VM. That is the right way to write it down.
- The Storage checks section matches the code: `fellowship-nas-scrub@ENGINE.timer`
  with `fnas pool scrub-all`, defaults that reproduce Debian's timing, arrays left
  on `mdcheck` with on or off only, Health thresholds of 40 and 100 days, and
  "turned off" treated as a choice.
- Disabling ZFS's own periodic scrub through `org.debian:periodic-scrub` keeps
  Debian's monthly TRIM while fnas owns the scrub.

Two lines in the plan are now out of date, both in the Unraid-style section:

1. The sync job is described as "a `sync` kind, which `jobs.KINDS` doesn't have
   yet". `KINDS` now reads `resilver, scrub, expand, sync, backup`.
2. The packages bullet still reads as a to-do. `mergerfs`, `snapraid` and
   `hd-idle` are already dependencies of `fellowship-nas-tools`.

## 3. The untried gate list

Every item below is named in the plan and none has been run on real disks yet.
They are the paths a release rests on.

Carried from Phase 10:

- [ ] RAID 6
- [ ] Sixteen disks in one array
- [ ] Importing an array made on another machine

Carried from Phase 11:

- [ ] Failing a data disk
- [ ] Replacing a data disk
- [ ] Pulling more disks than the parity covers
- [ ] A second parity disk
- [ ] The sync and scrub timers actually firing
- [ ] The 50-deleted-files limit skipping a sync with an alert
- [ ] Spin-down of idle data disks
- [ ] A reboot with pools mounted
- [ ] Importing a mergerfs + SnapRAID setup that fnas did not make

One VM session with four and sixteen disks should clear the list. Doing it before
Phase 12 keeps this debt from riding into a phase that carries a new kind of risk.

## 4. Phase 12: the Files app

Scope as written: browse and restore from snapshots, 10 GB transfers and video
seeking, and the desktop as an installable phone web app. Files also builds the
per-user file helper that WebDAV reuses in Phase 13.

### Three decisions needed before code

1. **How the file helper holds its privileges.** The existing helper runs `fnas`
   with an argv allowlist, which is a narrow surface. Browsing arbitrary paths is
   much wider. Suggested rule: the socket keeps the `SO_PEERCRED` check, each
   request is served as the logged-in user, and every path is resolved and then
   refused if it leaves that user's share roots. Needs a decision, then tests.
2. **Large uploads against the 60 second read timeout.** `ReadTimeout` in
   `cmd/fellowship-nas-web/main.go` covers the whole request, so a 10 GB upload is
   cut off. Either that route gets its own server or handler with a streaming
   deadline, or the timeout becomes per-route. This is the same argument the jobs
   stream already makes for having no write timeout.
3. **Snapshots exist only on ZFS.** Files must show browse and restore as
   disabled on arrays and Unraid-style pools, with the reason, or the gate's
   "restore an earlier version" quietly means ZFS only.

## 5. Suggested order of work

1. Fix the two stale plan lines, and pull the untried tests into one checklist
   under the release gate in the plan.
2. Settle the file helper's privilege and confinement model, and write it as a
   short plan section. WebDAV inherits it, so it is worth deciding once.
3. Run the catch-up gate on real disks for both engines.
4. Phase 12 in slices: browse, download and preview with the confinement tests
   first; then upload, rename, move, delete and restore; phone polish last, so
   the gate can still pass on desktop if the installable-app work runs long.

## 6. Notes for the UI work

What is true in the desktop today, so changes have a reference point:

- Three appearances sit beside the default: Silverwater, Hearthfield and
  Highwatch, chosen in Settings → Appearance (`docs/fellowship-nas-plan.md`,
  desktop section). The account menu keeps only the light, dark or automatic
  toggle. The choice is kept on the NAS per person (`fnas look show`, `fnas look
  set`), so it follows them to another browser; this browser holds a copy so the
  page paints the right one before the server answers.
- Severity has one scale everywhere: green good, yellow low, orange medium, red
  high, purple critical.
- Features that cannot work are shown disabled with the reason rather than hidden.
  Examples: "This pool doesn't support snapshots" and "The current backup engine
  only supports ZFS pools".
- Engines word their own growth and repair actions, so the desktop names no engine
  when it offers them. New engines should keep that true.
- Not-built notes carry phase numbers. After the reorder those are Files at 12 and
  SFTP, FTP and WebDAV at 13. The sample data in `web/dev/fake-fnas` follows them.
- The widget column scrolls on shorter screens, and icon slots fit a two-line
  label such as "Snapshots & backup".

When a UI change alters behaviour, these are the places that record it:

| File | What to update |
| --- | --- |
| `CHANGELOG.md` | An entry under Unreleased, in Added, Changed or Fixed |
| `docs/fellowship-nas-overview.md` | The Area table row, and the "Checked for this overview" test count and baseline |
| `README.md` | The app table, if the change is visible from outside |
| `docs/fellowship-nas-plan.md` | The desktop bullets, and the Phase row if a gate result changes |
