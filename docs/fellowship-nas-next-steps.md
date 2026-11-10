# FellowshipNAS next steps

Working note, written November 9, 2026 against `nas-edition` at `3610577`, and
brought up to date against `059a150` when Phase 12 started.
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
| Next phase | 12, the Files app, which now comes before more ways to share. Slices 1 to 3 built: the resolver, browsing and previewing, then uploading, folders, renaming, moving and deleting |
| Tests | 261 Python unit tests pass locally, the Go tests cover the resolver, the file ops and the stream, and CI is green on every push |
| Docs baseline | The overview describes `6772f13`, and the plan's Files section records the decisions and the two slices built |

## 2. What the docs get right, and what drifted

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

Two lines in the plan were out of date, both in the Unraid-style section, and
are fixed as of this pass:

1. The sync job read as a kind `jobs.KINDS` didn't have yet. `KINDS` reads
   `resilver, scrub, expand, sync, backup`, and the line now says so.
2. The packages bullet read as a to-do and left out `hd-idle`. All three are
   dependencies of `fellowship-nas-tools`.

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

### The three decisions, now answered (Nov 10)

1. **How the file helper holds its privileges.** Answered: the existing helper,
   gaining file ops beside `fnas`; the person comes from the session secret; each
   request runs in a child with that person's uid, gid and groups through
   `SysProcAttr.Credential`; the client sends a share name and a path inside it;
   every path is resolved with symlinks followed and refused if it leaves the root
   or doesn't resolve; an unmounted pool or an unreadable root answers "no".
2. **Large uploads against the 60 second read timeout.** Answered: the upload and
   download routes clear the connection's deadline with `http.ResponseController`
   for their own duration, as the jobs stream already avoids a write timeout, and
   the rest of the desktop keeps the 60 second cover. Caps on each file and on the
   whole request keep a stuck upload from holding a worker.
3. **Snapshots exist only on ZFS.** Answered: the previous-versions control is
   shown disabled with the reason on arrays and Unraid-style pools, which is
   already the house rule for a feature that can't work.

The plan's Files section carries the same answers, and the code follows them.
What is left of Phase 12 is planned slice by slice in
[the Files plan](fellowship-nas-files-plan.md): multi-select, copy and a "Move
to…" picker, search, the owner and permission columns, snapshots, then the
installable phone app.

## 5. Order of work

Done in this pass:

1. The two stale plan lines are fixed, and the untried tests are one checklist
   in section 3.
2. The three Phase 12 questions are answered in the plan's Files section, which
   WebDAV in Phase 13 inherits.

Left, in order:

3. Phase 12's remaining slices: snapshots on ZFS only and restoring from them;
   then the phone work, so the gate can still pass on desktop if the
   installable-app work runs long. Browsing, downloading, previewing, uploading,
   folders, renaming, moving and deleting are built.
4. The catch-up gate on real disks for both engines, which section 3 lists item
   by item.

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
- Every group the machine has is offered where a group can be granted, marked
  when it belongs to the machine or to one account, and searchable once a machine
  has dozens. Nothing is filtered out, and fnas only adds or removes people from
  those groups so the app they exist for keeps working (`fnas group list
  --system`).
- Lists hold one line per thing, with its actions in a menu on the row, as Shares
  and Users now do; a form opens under the list rather than in a separate page.
- Files is shaped like a browser: shares down the left, a toolbar of back, up and
  refresh over a breadcrumb that starts at the share's name, a sortable list or a
  grid of thumbnails, and the picked file in a pane beside the list. Clicking
  picks, a double click or Enter opens. It shows only the shares the signed-in
  account has, so nothing in it is a link the server would refuse.
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
