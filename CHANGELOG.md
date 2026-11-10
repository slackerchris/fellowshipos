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

- Files, slice B: **Copy** and **Cut** (row menu, the pane for several, Ctrl-C and
  Ctrl-X) set things aside, and **Paste** in another folder of the same share copies or
  moves them there (Ctrl-V). A copy of a file or a whole folder is made under a
  temporary name and renamed into place, so one that fails leaves nothing half made; it
  never follows a link, and a folder can't be copied into itself. **Move to…** picks a
  destination from the share's folders (at most 500, eight deep), so moving needs no
  dragging, which a phone can't do.
- Files, slice A: pick several things at once (Ctrl-click or Cmd-click adds or takes one
  away, Shift-click and Shift with the arrows pick a run, Ctrl-A picks everything shown,
  Escape lets go), with a pane that says how many and how big. Delete them together
  behind one typed confirmation ("delete 4 from media"); each goes on its own, so one
  refused doesn't stop the rest, and the status line says "3 deleted, 1 refused: …".
  Drag a picked one onto a folder and the others go with it.
- Download several things, or a folder, as one zip, made by the person's own child
  process while it is sent, so nothing is kept on the NAS or held in the browser. A zip
  never follows a link, counts its size first and is refused past 2 GB before a byte is
  sent, and carries the same sandbox as any download.
- Files can change things now, not only look at them: **Upload** takes one or
  more files, or drop them on the folder; **New folder**; **Rename…** and
  **Delete…** in a row's menu, the delete asking for the name to be typed as
  everywhere else; and dragging a file onto a folder moves it there. A box filters
  the folder as you type. A share fnas says is read only hides the three change
  buttons and refuses them if they are called anyway, and a folder delete takes
  what is inside only when the desktop says so.

- Phase 12's Files app, first two slices: browse the shares your account can use,
  open a folder, and preview an image, video, audio file, PDF or text file, with
  download. The helper does the work as you: it asks `fnas share roots USER` where
  your shares are, resolves the path, then re-executes itself as your account, so
  the kernel decides what you can open as well as fnas. A share you can't use is
  refused with "family isn't a share you can use", and a path that climbs out of a
  share is refused before any file work. Downloads and previews answer `Range`, so
  a video can be seeked.
- `fnas share roots USER`: the shares a person can use, where each one is on disk
  and whether it keeps snapshots. Files asks this instead of working out paths, and
  WebDAV will ask the same.

- Each person's look follows them: light, dark or automatic and the theme are
  kept on the NAS per user name (`/etc/fellowshipos/nas-look.json`, `fnas look
  show` and `fnas look set`) and applied as they sign in, so the same account
  wears the same desktop on any computer or browser. The browser keeps a copy so
  a reload paints the right look before the server answers. A non-admin can
  change their own and no one else's, and removing a person forgets theirs.

- Every group on the machine is offered where groups are chosen, with nothing
  filtered out: the packages' groups such as video and docker, the groups of accounts
  fnas doesn't manage, `nas-admins`, and each account's own group, marked as such. The
  list is searchable when a machine has dozens. A share can be given to any of them,
  so Plex, the *arr apps or a backup tool that already runs on the NAS reach the files
  they need without a second account, and a person can be given one from their own
  form. fnas never renames or removes these groups, and changing who is in one only
  adds and removes people it manages, so the app the group exists for stays put.
  `fnas group list --system` lists them with their ID.

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
- Three themes beside the default forest and gold, chosen in Settings →
  Appearance (Nov 10) with light, dark or the system's: **Silverwater** (slate
  blue, the waterfall city),
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

- Files looks and behaves like a file browser: the shares down the left with how
  you may use each one, a toolbar with back, up and refresh over a breadcrumb that
  starts at the share's name, a sortable list with a header for name, size and
  changed, a grid of thumbnails for folders of pictures, and the picked file in a
  pane beside the list rather than under it. Clicking picks, a double click or
  Enter opens, the arrow keys move, Backspace goes up and Escape forgets the pick.
  A narrow window drops the shares column, then puts the preview under the list.

- The theme choice moved out of the account menu into **Settings → Appearance**,
  beside the light, dark or automatic setting, because the look is a preference
  and the menu is for identity and signing out. The menu keeps the three-way
  toggle, since that is a frequent one-click change.
- The Users app is two tabs, People and Groups. Each person is one line with what
  they can do, their groups and whether they have a password, and Change, Turn off
  and Remove move into a menu on the row. **Add someone** opens the form under the
  list, and the group list takes the same shape with **New group**.
- The Shares app is two tabs. **Shared folders** lists each share on one line with
  who can use it and which protocols it is on, and **Connect** opens that share's
  paths and its SMB and NFS switches under the row. A menu on each row holds its
  settings and deletion. **Sharing settings** holds the NAS-wide SMB and NFS
  settings and the home network, which used to sit below the folder table. A
  compact **Ways to share** card replaces the four-column protocol table.
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

- A folder made in Files lost the share's setgid bit, since setting its mode cleared
  it, so what was then made inside belonged to the person's own group and others in the
  share's group couldn't change it. New and copied folders keep the bit.
- Found by a RAID 6 test in the VM, with two of four disks pulled out: an array missing
  disks read as healthy everywhere but the pool card's table, since a disk that has gone
  isn't listed among the array's members. The pool, the Storage and Health widgets and
  Health's "every disk present" check now read the array's own count (`[4/2]`) and say
  degraded. A missing disk is named by the disk last seen in its slot ("DATA-2
  (missing)") rather than "Missing disk", and the notice says when no protection is left:
  "2 disks failed, so the array has no protection left: one more failure loses it."
- Files, from a review of slices 1 to 3:
  - A file someone uploaded could run as whoever opened it: HTML and scripts were sent
    as themselves, and the desktop's policy let a page load a script from the same
    address. Text of every kind now goes as plain text, and every file's bytes carry
    a sandbox with no scripts and no origin, an SVG included.
  - The child that does the work as the person was handed a whole path the helper
    had resolved earlier, so a folder swapped for a link in between could lead out of
    the share. It now gets the share's real folder and the path inside it, and opens
    every part beneath that folder with `openat2`, which refuses a way out or any link.
  - Deleting a folder with anything in it always failed with "that folder isn't
    empty": the path reached the child twice, and the word saying to take the
    contents landed in the wrong place.
  - A child that stopped part way, saying nothing, was taken for success. Each one now
    says when it finishes.
  - PDF previews were refused by the desktop's own "never in a frame" rule; a PDF may
    now be framed by the desktop alone.
  - A person with no groups besides their own could leave the child with the helper's
    groups; the child's groups are now always set.
  - Each file request ran `fnas share roots`, so a folder of thumbnails ran it once a
    picture. A person's shares are now kept for five seconds.
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
