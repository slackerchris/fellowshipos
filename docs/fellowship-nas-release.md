# FellowshipNAS release list

FellowshipNAS is a home NAS. The owner manages it, family members store and
share files, and media services and automation apps have access to the folders
they need.

## What the first release needs to prove

- The owner can install from an ISO, sign in, create storage, and find their
  storage and shares after reboot.
- The owner can give a family member access to their own folder and selected
  shared folders. The owner can reset a password or disable an account.
- Plex or Jellyfin can read the media library. An automation account can write
  to the download and library folders it manages.
- The owner can restore a file and recover access after a failed network change.

## Current milestone

Chris will do another clean VM install and tour the desktop with disposable
disks before trying real hardware. Record what the tour turns up. Chris decides
which gaps to fix for this release and in what order.

Forced password changes and reset links are deferred. Commit `23b048b` reverted
Gate A, including migration of the old Linux password-change flag. Ordinary
admin password resets and account disabling remain available. The
[must-change plan](fellowship-nas-must-change-plan.md) records the work for reference.

## VM tour findings

All six are built and proven on a VM as of 2026-11-16. The commits are not yet
pushed to `origin/nas-edition`. Chris's next tour confirms them. The details and test
results are in the [tour plan](fellowship-nas-tour-plan.md).

1. **Clear alert history.** Add a clear action in Alerts. Clearing history
   should leave current Health findings visible.
   *Built in `168ac5d`.*
2. **Clear the desktop alert widget.** Let the owner dismiss notices and stop
   showing a warning once Health confirms the issue has resolved.
   *Built in `168ac5d`.*
3. **Check when SSH should be running.** An alert said SSH was stopped after
   SFTP was turned off. Verify both switches on the VM. Health should expect
   the shared daemon to run whenever SFTP or root SSH is enabled.
   *Built in `168ac5d`: Health expects the daemon only when one of those
   switches is on.*
4. **Delete array.** Storage offers Export, which preserves the array. Add a
   separate Delete array action that shows the affected shares and disks and
   requires confirmation. On the test VM, verify the four disks from `tank`
   can be used to create a new array.
   *Built in `c6fa979`. Delete is allowed only after the array's shares are
   removed. `nas/tests/vm/vm-delete-proof.sh` proves it from the desktop,
   including reusing the freed disks (`8838625`).*
5. **Check array.** Clicking Check array on the four-disk RAID 5 `tank`
   showed “stopped before it finished.” Determine whether the check stopped
   or the desktop lost its result. Verify that Storage reports completion on
   success and shows a reason if the check fails.
   *Built in `028af98`. The desktop lost the result: a check now reports done
   when it finishes, and a job watcher that dies keeps its reason.*
6. **Start, stop, and restart services.** Give the owner desktop controls
   for individual services such as Samba, NFS, and SSH, and show their current
   state. Keep SFTP access separately configurable and make clear that stopping
   the shared SSH daemon also stops SFTP. Verify that stopping one service
   leaves unrelated services available and that Health recognizes an intentional
   stop.
   *Built in `c6fa979`. Settings → Services covers Samba, NFS and the SSH daemon,
   with a typed confirmation for each action. `ssh-samba.sh` passes 82/82. The
   web server and its helper can't be controlled from the desktop.*

## Also fixed since the last tour

- **Recovery key.** The installer no longer asks you to type the key back. The
  typed erase phrase is the only confirmation (`ef3241a`, `5e805f3`).
- **CPU figure.** Health, the desktop widget and the command line now read one
  continuous sampler (`d64b0d2`).

## For the next tour

- Confirm findings 1–6 on a clean VM install with disposable disks.
- Compare the CPU figure on Proxmox against `mpstat` under `stress-ng`.
- **Decision for Chris:** restarting the SSH daemon cuts any SFTP transfer in
  progress. The panel warns, and the action stays available. Keep it that way?

## Proxmox tour, 2026-11-17

Chris is touring a ZFS install on Proxmox. Findings so far:

1. **No Delete on a ZFS pool.** Storage shows Add disks, Check for damage and
   Export for the RAIDZ1 `tank`, but no Delete. The button exists only for
   mdadm arrays (`storage.tsx:382`), and nothing says why it's missing.
   Unraid-style pools had no Delete either. Chris confirmed this is a gap.
   *Built 2026-11-17, not committed: ZFS and Unraid-style pools now have Delete
   pool…, with the same rules as an array. Shares go first, then you type the
   pool's name. ZFS destroys the pool and clears each disk's labels, so
   `zpool import -D` can't bring it back. The confirmation also names any backups
   of other pools kept on it. Unraid-style unmounts the pool and wipes every
   disk, parity included. Unit tests pass (395). Not yet run on a VM.*
2. **Calendar from the clock.** Chris asked for the taskbar time to open a
   calendar. *Built 2026-11-17, not committed: clicking the clock opens a panel
   with the time to the second, the full date, and a month you can page through
   with Back to today. The week starts on the browser's local first day. Checked
   in a local preview of the desktop.*
3. **Time format.** Chris asked for a way to change how the time reads.
   *Built 2026-11-17, not committed: Settings → Appearance → Clock, also under
   Time format… in the calendar, offers Auto, 12-hour or 24-hour, seconds on
   the taskbar, and a date beside the time (none, short or numeric). The choice
   is saved with the person's account on the NAS (`fnas look set --hours
   --seconds --date`), like light and dark. Unit tests pass (397). Checked in a
   local preview.*
4. **NAS time on the clock.** The clock and calendar showed the browser's time.
   Chris chose the NAS's. *Built 2026-11-17, not committed: a new
   `GET /api/time`, answered by the helper, gives the NAS's time, its time zone
   from `/etc/localtime`, and whether the kernel says chrony has synced the
   clock. Every signed-in person can read it. The desktop asks at sign-in and
   every five minutes, then corrects the device's ticking by the difference. The
   taskbar, calendar and the samples in Settings show NAS time in the NAS's
   zone. The calendar says "NAS time · zone". It warns when the NAS clock isn't
   synced, and the taskbar shows a "!". If the device's clock is two or more
   minutes off, the calendar says so. If the device is in another zone, it shows
   the device's time on a second line. If the NAS can't be asked, it falls back
   to the device's time and says so. Go tests, vet and gofmt pass in the
   `golang:1.24` container. Checked in a local preview with a pretend NAS in
   London. Not yet checked against chrony on a VM.*
5. **"Everyone" shares are read-only in Files.** `pit` is set to Everyone on
   the home network, but Files says "The nas-share group can change this
   folder, and you aren't in it." This is a bug, not wording. An Everyone
   share's files belong to the `nas-share` account and group (mode 2775). SMB
   and NFS write as that account (`force user`, `all_squash`), so they work.
   Files, WebDAV and SFTP act as the person, and nobody is in the `nas-share`
   group, so they can read but not write.
   *Chris chose: rename the group to something plain, and put every person in it
   unless an administrator takes them out. Built 2026-11-17, not committed:*
   - *`nas-share` is now `file-share`. The package renames the account and group
     in place, keeping ID 3000, so no file changes owner.*
   - *Every person joins once. `fnas share refresh`, which runs at install,
     update and every boot, adds anyone not yet offered it. That covers
     existing people on an updated NAS. `fnas user create` adds new people
     unless `--no-everyone-shares` is given.*
   - *Taking someone out sticks. A hand-made change is recorded, so a refresh
     never puts them back.*
   - *Users shows a "Can use shares for everyone" tick for each person, on by
     default for someone new, instead of leaving the group under System groups.*
   - *Someone taken out is out over SMB too (`valid users = @file-share`),
     except on a share with guest access on. NFS trusts whole computers, so it
     can't tell people apart.*
   - *Files tells a person who was taken out why they can't write, and that an
     administrator can put them back.*
   - *Unit tests pass (401). Installer Go tests pass. Not yet run on a VM.*
6. **Attached pools doesn't need a tab.** It only imports pools found on
   attached disks. *Built 2026-11-17, not committed: Storage has Pools and
   Disks tabs. **Import a pool…** sits beside Create a pool and opens the same
   import card in place, with a line saying what it's for and a Close button.
   The empty-pools text mentions it. Setup's import step is unchanged.
   Type-check and build pass. Not looked at on a NAS yet.*
7. **Restart and shut down from the account menu.** Chris asked for them near
   Sign out, then as an icon that asks which. *Built 2026-11-17, not
   committed: administrators see a power icon (⏻) beside Sign out. It opens one
   question over the whole desktop, "Restart or shut down the NAS?", which says
   what each does and offers Cancel, Restart and Shut down. Cancel has focus,
   so a stray Enter is safe. Family accounts don't see the icon. Settings →
   Power keeps its two buttons. Checked in a local preview.*
8. **Choosing the time servers.** The NAS already followed pool.ntp.org through
   chrony. Chris asked for a choice in Settings. *Built 2026-11-17, in
   0.2.1: Settings → Name and time → Time servers offers
   pool.ntp.org (the default, plus any your router offers), Google, Cloudflare
   with NTS, or up to four of your own. Any choice other than the pool turns
   off Debian's pool line and the router's servers in chrony.conf, so Google's
   smeared leap seconds never mix with others. Choosing the pool again puts
   those lines back. The card shows whether the clock is synced, with which
   server and how far apart, and each server chrony hears. It's `fnas system
   time` and `fnas system time-source --source … [--server …]`, and admins can
   run it on a separated NAS. Unit tests pass (406), and Go vet, tests and
   gofmt pass. Checked in a local preview. Not yet run against real chrony.*
9. **Folding and hiding the desktop widgets.** Chris asked for a way to
   dismiss or shrink them. *Built 2026-11-17, in 0.2.1: each widget has a
   fold button in its corner that shrinks it to its title. Folded, it still
   shows a badge when something needs attention ("tank degraded",
   "1 problem"), or "All well", and a click opens its app. **Hide** above the
   column, or the desktop's right-click menu, slides it away and leaves a
   "‹ Status" tab with a count of what needs attention. Both are saved with the
   person's look on the NAS (`fnas look set --widgets hidden --collapsed
   storage,health`). Unit tests pass (407). Type-check passes. Checked in a
   local preview with a degraded pool.*
10. **The installer's screen tears when you click or something updates.**
    Reproduced in a local QEMU VM on standard VGA, the same display Proxmox
    uses by default. The page was reloaded 15 times, with 180 screen captures.
    With cage drawing through OpenGL (Mesa's software renderer), 12 frames
    showed half-drawn pages with black staircase streaks. With
    `WLR_RENDERER=pixman`, none of 180 did, though 91 were caught mid-redraw.
    *Fixed 2026-11-17, in 0.2.1: the installer's kiosk service now uses
    pixman. Needs confirming on Proxmox.*

## Keeping the list current

- Start each task with the household workflow it serves and an observable
  finish line. Agents may fix bugs within the requested scope that block that
  workflow or threaten data or access.
- Bring new features, policy changes, and proposed scope expansions to Chris
  before implementation. Explain who needs the change and what it lets them do.
  Chris decides whether it belongs in this release.
- Use architecture plans and handoffs for technical context. Record useful
  findings and deferred ideas there. Add work to this list once Chris approves
  it, even if an older phase already names it.
- When handing off work, record what was verified, what still needs checking,
  and the next approved item. If the next step is Chris's tour or decision,
  leave it as the next step.
