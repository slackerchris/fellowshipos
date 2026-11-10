# Changelog

All notable changes to FellowshipOS and its FellowshipNAS role.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The
version numbers in a release heading match the `debian/changelog` files under
`nas/packages/`, which stay the authoritative per-package record.

Add entries under **Unreleased** as work lands. When a phase gate passes in the
VM, move them under a release heading with the version and date from the package
bump.

## [Unreleased]

### Added

- Unraid-style pools (Phase 11): whole files on each XFS disk, joined at `/mnt/NAME` by
  mergerfs, with one or two SnapRAID parity disks. Disks may differ in size, new files go
  to the disk with the most free space, and with more disks lost than parity only those
  disks' files go. Create, grow by data disks or a second parity disk, replace, repair,
  export and import (pools fnas made), from the desktop and `fnas`.
- A nightly parity sync (`fellowship-nas-sync@mergerfs.timer`, 01:00) and a weekly partial
  scrub, both on the Unraid-style row of Settings → Storage checks, with idle-disk
  spin-down through hd-idle (30 minutes by default). Storage shows when parity was last
  synced, with **Sync now**. A sync is skipped, with an alert, when over 50 files went
  missing since the last one.
- `fnas pool sync`, `fnas pool sync-all` and `fnas pool scrub-all --scheduled`, and a
  `sync` job kind. `mergerfs`, `snapraid` and `hd-idle` are dependencies of
  `fellowship-nas-tools`.
- Three themes beside the default forest and gold, chosen in the account menu with
  light, dark or the system's: **Silverwater** (slate blue, the waterfall city),
  **Hearthfield** (warm parchment and rust, the hill homes) and **Highwatch** (mauve
  stone, the mountain fortress), each with its own wallpaper. Each is four colours per
  mode in `web/themes.mjs`; the build works out the rest, including the taskbar and the
  app icon tiles, and stops if a theme's text would fall below WCAG AA contrast.
- **Settings → Storage checks**: one row per engine, with whether its pools are checked
  by themselves, when, the next run, and Check now. ZFS pools are scrubbed by fnas's own
  `fellowship-nas-scrub@zfs.timer`, monthly or every three months, on a chosen week,
  day and hour; the default is Debian's second Sunday at midnight, so an upgrade changes
  nothing. Arrays keep Debian's mdcheck, which the row turns on or off.
- `fnas pool schedules`, `fnas pool schedule ENGINE`, `fnas pool scrub-all ENGINE` and
  `fnas pool apply-schedules`. The schedules are saved with the settings on every pool.
- Health reports read, write and checksum errors ZFS counted on disks still in a pool.
  Before, a pool with errors and every disk online raised nothing until a disk dropped
  out.
- mdadm's array events reach alerts the moment `mdmonitor` sees them, rather than at
  the next hourly check: a failed disk, a degraded or vanished array and a missing spare
  are alerts, and a finished rebuild is a note. `fnas` points mdadm at
  `/usr/lib/fellowship-nas/md-alert` through a `PROGRAM` line in `mdadm.conf`, and the
  package adds it to existing systems on upgrade.
- Health records the monthly array checks Debian's `mdcheck` timers run, not only the
  ones fnas starts, through a drop-in on those services. A check's finish is a note, and
  blocks it found that don't match are a Health finding and an alert.
- Create pool names RAID layouts in full ("Two copies of everything, striped across
  4 disks", "RAID 10 · Two copies, 4 disks") and asks for the format, XFS or ext4. An
  array's card offers Check array, Repair when a check found mismatches, and only the
  ways an array grows: one more disk, or spares. Snapshots, Previous Versions and the
  nightly backup stay visible for arrays, disabled with the reason.
- Traditional RAID, as a second storage engine: a pool is one array of RAID 1, 5,
  6 or 10 with up to two hot spares, on XFS (recommended, and preselected) or
  ext4. RAID 10 uses the standard two-copy layout. Arrays can be created, grown
  by one disk, have a failed disk replaced, have a spare added or removed, and be
  exported and imported, from the CLI or from the desktop.
- `fnas pool scrub` checks every block of any pool, and `fnas pool repair` mends
  the blocks a check found to be wrong. A repair is never automatic: it can keep
  the wrong copy, so it needs its own confirmation.
- CI in [.github/workflows/ci.yml](.github/workflows/ci.yml): the Python unit
  tests, `gofmt` and `go vet` plus `go test` for both Go modules, both frontend
  builds, and ShellCheck over the shell scripts, including the hook and maintainer
  scripts that carry no `.sh`. The ISO build stays a local
  step, since it needs Docker and Debian mirrors.
- Health opens on a verdict banner, then two groups of cards: System (CPU usage,
  memory, CPU load) and Storage & services (pools, disks, services). Each card
  carries an icon, its reading, and a **View details** link into the full section.
  A strip along the bottom repeats the backup's last result and whether any alert
  is active.
- CPU usage is sampled from `/proc/stat` over a moment, so the card shows what the
  processors are doing now rather than the average since boot.
- The desktop's Health widget is the panel in miniature: the worst problem in a
  severity medallion with its level written out, CPU and RAM, and a fill bar with a
  health label for each pool. Clicking it opens Health.
- The desktop's Disks widget is a drive bay map: one numbered slot per drive in its
  severity colour, and a line naming the worst drive. Clicking a slot opens Health at
  that drive's row, which is marked and scrolled into view.
- The hourly check records the system beside each pool: memory in use,
  one-minute load, the system disk, and the hottest sensor. It stores them in the
  same file as the pool history, under a key no pool name can take.
- Health draws trends from that history: memory, load and temperature under
  System, and each pool's fill under Pools. Both offer a Week or Year range with
  a hover readout, and each pool gauge shows a sparkline at a glance.
- `fnas pool layouts` and `fnas pool import` take `--engine`, and the desktop
  asks the chosen engine for its layouts, so a second engine will not inherit
  ZFS's.
- Tests for the reserved-name rule, an engine refusing a layout it lacks, a
  command that never returns, and the new system history.

### Changed

- fnas scrubs ZFS pools instead of Debian's `zfsutils-linux` cron job: each pool gets
  `org.debian:periodic-scrub=disable`, which that job honours, so only one runs. Its
  monthly TRIM still does.
- Health's **Storage checks** judges each pool by its engine's schedule (40 days on a
  monthly one, 100 on a quarterly one), and shows automatic checks turned off as a
  choice rather than a late check.
- The role's postinst applies the saved schedules instead of always turning Debian's
  array check on, so turning it off in Settings stays off across upgrades.
- Each engine describes how the desktop presents its pools: the check button and its
  wording, the ways it grows, its formats, and whether it has snapshots, backup,
  compression and a separate repair. `fnas pool engines` sends that, shares carry a
  `snapshots` flag, and the desktop no longer branches on an engine's name, so Phase
  11's engine needs no desktop edits for those. Health's pool cards name the engine
  ("Traditional RAID") rather than printing "MDADM".
- The array check's alert no longer says to back up first, since the backup job
  covers ZFS pools only; it says to copy what matters elsewhere.
- Health's storage check names when each engine checks itself (Debian's ZFS scrub on the
  second Sunday, mdcheck from the first), and an array never checked falls due 40 days
  after it was made instead of at once.
- `mdadm` and `xfsprogs` are dependencies of `fellowship-nas-tools`, which holds the
  engine, rather than of the role, so the tools alone can run it. The role depends on
  `cron`, which Debian's monthly ZFS scrub needs.
- `fnas` stops any command still running after 300 seconds and reports it.
  `syncoid` and `zfs destroy -r` are exempt, since they take as long as they take.
- The privileged helper runs at most eight `fnas` processes at once, so a flood
  of requests cannot fill the machine with root processes.
- The desktop's HTTPS server sets a read timeout. It still sets no write timeout,
  because the jobs stream is a long response.
- The rule that a pool name cannot start with a word ZFS reserves now lives in
  the ZFS engine, so another engine may use those names.
- The parser accepts any layout name for `pool create`. The chosen engine refuses
  one it does not have, and says what it offers.
- Severity has one five-step scale across the panel and its widget: green good,
  yellow low, orange medium, red high, purple critical.
- Health's monthly pool check is now **Monthly storage checks**, it covers every
  writable pool rather than only ZFS ones, and each engine names its own action:
  Scrub for a ZFS pool, Array check for traditional RAID.

### Fixed

- The desktop's widget column ran under the taskbar on shorter screens, hiding the lower
  drive rows and Recent alerts; it now scrolls. A two-line icon label, such as Snapshots
  & backup, no longer runs into the icon below it.
- An Unraid-style pool unmounted whenever the desktop's helper restarted, as on every
  update: mergerfs keeps running while a pool is mounted, and fnas had started it inside
  whichever service called it. The pool now mounts through its own systemd mount unit.
- Found in the VM while building Unraid-style pools: a sync failed whenever fnas saved
  its settings during it, since SnapRAID stops when a file changes under it. The settings
  folder is kept out of parity and copied to every data disk after each sync instead.
- A Light or Dark choice only took effect once the account menu was opened, so a reload
  went back to the system's; the chosen mode and theme now apply before the page draws.
- Traditional RAID, found by running it on real disks in the VM:
  - `mdadm --detail --scan` names an array `/dev/md/NAME` and `/proc/mdstat` calls it
    `md127`; nothing matched the two, so a new array was never listed as a pool, and
    progress, mismatch counts and the end of a reshape were never seen.
  - Shares made on an array were saved but never listed, shared or exported.
  - A failed disk showed under Hot spares with an empty "Missing disk" slot; it now
    shows in its own slot, and replacing it takes it out of the array first.
  - Disks showed as `/dev/vdX` with no size; they show by their stable by-id names.
  - mdadm.conf kept the `spares=1` a new array reports while it builds.
  - The pool details view failed before an array's first check.
  - `fnas pool status NAME` ran `zpool status` for arrays too.
- A Health dial showing a single part in full colour had a notch at twelve
  o'clock. The gap between parts now appears only when there is more than one.
- A gauge beside a short row grew to fill the whole track and stretched its
  sparkline with it. Gauges are capped and centred in their cell.
- `__pycache__` and `*.pyc` are ignored, so a byte-compile pass leaves nothing to
  commit.
