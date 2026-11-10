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
  builds, and ShellCheck over the shell scripts. The ISO build stays a local
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
- `fellowship-nas` now depends on `mdadm` and `xfsprogs`, which the engine needs.
- A Health dial showing a single part in full colour had a notch at twelve
  o'clock. The gap between parts now appears only when there is more than one.
- A gauge beside a short row grew to fill the whole track and stretched its
  sparkline with it. Gauges are capped and centred in their cell.
- `__pycache__` and `*.pyc` are ignored, so a byte-compile pass leaves nothing to
  commit.
