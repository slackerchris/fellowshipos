# VM walkthrough of both images: features, bugs and suggestions

November 11, 2026, against `nas-edition` at `7250dc2`, with both ISOs rebuilt
from that commit. Everything ran in QEMU/KVM with OVMF (Secure Boot off).

Each image was booted live, installed through the graphical installer in
remote mode from a browser, then started from its own disk. The NAS went on
through the first-boot wizard, the NAS desktop and its Setup; the
Multi-purpose image went through the wizard with the Docker role. The text
installer was not run.

## What was tried and what happened

| Path | Result |
| --- | --- |
| NAS ISO, live boot | Works: system `running`, no failed units; autologin shell on tty2 |
| Multi ISO, live boot | Works, same as the NAS one |
| NAS graphical install (remote, pairing code) | Works, about 80 s |
| NAS first boot, NAS role | Works, about 2½ min; ZFS loads, Samba, NFS and the desktop start. System left **degraded** (hd-idle, see bugs) |
| NAS desktop: log in, Setup, ZFS RAIDZ2 pool, SMB/NFS share, Files | Works |
| Multi graphical install | Works, about 80 s |
| Multi first boot, Docker role | Works: `running`, no failed units, Docker active, user in `docker` |

## Features, by screen

### Boot menu
- Entries: text install, graphical install for the image, Try (live),
  Advanced, Utilities (UEFI settings, check the boot medium).
- Waits with no timeout, which is right for an installer.

### Live session
- Auto-login on tty2 with the FELLOWSHIPOS banner and an info block (host,
  kernel, uptime, load, memory, disk, IP).
- `bat` and `eza` stand in for `cat` and `ls`.

### Graphical installer
- Remote mode: HTTPS, a pairing code and the certificate fingerprint printed
  on the console.
- **Disk:** every disk is listed with its size, serial and a reason when it
  can't be used (too small, the boot stick). Nothing is hidden.
- **Space (NAS):**
  - A stacked bar of System, Room to grow, Swap and Data, with one slider.
  - It explains in plain words that the system sits on LVM and the data part
    stays empty for the desktop to use.
- **Space (Multi):** the system takes the whole disk, with swap.
- **You:**
  - Name and time zone.
  - The user name fills itself in from "Your name".
  - Password and its repeat, with a live "passwords don't match".
- **Install:**
  - A summary table and a red "Erase vda and install" button.
  - Eight named progress steps with a collapsible log, and Restart now at the
    end.

### First-boot wizard (tty1)
- Uses the account the installer made.
- Remembers the role chosen at install: NAS is the default on the NAS image.
- Detects the LAN subnet and shows a summary before applying.
- NAS: ends with "Finish setting up the NAS in a browser: https://10.0.2.15".

### NAS desktop
- **Look and layout:**
  - The mountain photo and a dark green card on the login page.
  - A desktop with an icon column, a widget column (Storage, Health gauges,
    Pools, Disks, Recent alerts) and a taskbar with bell and user.
- **Setup opens by itself** on first login, with six steps: Storage, Network,
  People and shares, Alerts, Backup, Done. "Skip setup for now" is there, and
  the Setup icon goes away once it's finished.
- **Create a pool:**
  - The four engines, with Btrfs shown disabled with its reason, and "Compare
    them".
  - Layout cards that say what you get ("8.6 GB usable · Any 2 disks can
    fail"), with the technical name in small print.
  - You type "erase 4 disks" to confirm.
- **People and shares:**
  - The three account kinds, the administrator box and system groups.
  - Shares for everyone or chosen people, over SMB, NFS, WebDAV or SFTP, with
    FTP shown disabled as not built.
- **Backup:** disabled with its reason ("needs two ZFS pools, and this NAS has
  one").
- **Health:** each check with its state, services (SMB, NFS, SFTP, WebDAV,
  SSH), CPU, memory and load.
- **Files:** shares down the left.

## Bugs (wrong now, worth fixing first)

**Status:** all eleven fixed in the working tree on Nov 11. The CHANGELOG's
Fixed section lists each fix. For bug 8, connect lines now fall back to the
NAS's name only when the page is opened on loopback, and the login card still
shows the address it was opened on. The NAS role's refusal on the Multi-purpose
image was found while fixing these, and is fixed too. The boot menu and install
page fixes are covered by tests and builds, but they won't be seen running
until the ISOs are rebuilt.

1. **Enter at the boot menu starts the Debian text installer, on both ISOs.**
   - `set default=0`, and `grub.cfg` sources `install_start.cfg` (the text
     entry) before the graphical one.
   - The comment just above says "The text installer stays in the advanced
     menu", and the README and overview say the same.
   - The text entry's label still lists "storage" as a role.
2. **The installer's finish screen shows a literal placeholder:** "the NAS
   desktop opens at https://NAME". The name chosen on the You step should be
   there (`installer/web/src/main.tsx:315`).
3. **The NAS is `degraded` straight after setup.**
   - `hd-idle.service` is enabled, but Debian's `/etc/default/hd-idle` has
     `START_HD_IDLE=false`. The unit exits, restarts five times and hits the
     start limit.
   - Health doesn't notice the failed unit.
4. **Btrfs is labelled "Not built yet (Phase 14)"; the plan says Phase 17.**
   `nas/tests/unit/test_fnas.py:2327` expects 14 as well.
5. **Free space is overstated about two times.**
   - Setup and the Storage widget show the RAIDZ2 pool as "17 GB" and "17 GB
     free of 17 GB".
   - `zfs list` gives 7.27 GB available, and the layout card had promised
     8.6 GB.
6. **Widgets don't refresh.** After the pool and share were made, the Storage
   and Pools widgets said "No pools yet" until the page was reloaded.
7. **Placeholders where real values exist:**
   - The pool-name hint reads `/mnt/NAME`.
   - Setup's Done page shows `mount …:/mnt/POOL/shares/NAME`, although `tank`
     and `media` exist.
8. **Setup's Done page and the login card use the browser's host.** Both show
   `127.0.0.1`, not the NAS's name or address, which is wrong behind a port
   forward or a proxy.
9. **NAS wording on the Multi installer:**
   - The tab title is "Install FellowshipOS NAS".
   - The pairing page says "the NAS's own screen".
   - The user-name hint mentions "the desktop", which Multi doesn't have.
10. **The installer's step buttons lose their styling during the install:**
    they show as plain text, "1. Disk 2. Space 3. You [4. Install]".
11. **The two disk counts disagree:** the Disks widget says 4 drives and Health
    says 5.

## Suggestions, by screen

### Boot menu and branding
- **FellowshipOS identity:**
  - Give GRUB a FellowshipOS theme (the palette and mountains) in place of
    Debian's hard hat, "Debian GNU/Linux 13 (trixie)" and the live-build
    version lines.
  - Drop the start-up beep.
  - Name the firmware boot entry FellowshipOS (it is "debian").
  - Set `PRETTY_NAME` in `/etc/os-release`.
- **Menu order:** put the graphical install first, and move the text install
  under Advanced, as the docs already describe.

### Live session
- Put the auto-login shell on tty1, or print "Press Alt+F2 for a shell" there.
  tty1 shows only kernel messages.
- Give the live system a FellowshipOS hostname, not `debian`.
- Say how to install from the live session.
- Drop Debian's legal motd under the banner.
- Soften "Only those with root may pass", which reads like an error in red.

### Graphical installer
- **Console:** print the real address (`https://10.0.2.15`), not `https://:443`.
- **Pairing page:**
  - Say how many digits the code has.
  - Point to the fingerprint on the console.
  - Name the image ("FellowshipOS NAS").
- **Disk:**
  - Preselect the disk when only one fits.
  - Use a radio button instead of a checkbox.
  - Say why "Space →" does nothing when no disk is chosen.
  - On the NAS image, say of the small disks: "Make a storage pool from this
    after the install", instead of only "Too small".
- **Space:**
  - Add a number box beside the slider.
  - On Multi, leave out "room to grow 0 B" from the summary.
- **You:**
  - Detect the time zone from the browser.
  - Group or trim the list (`Etc/GMT+N`, `Factory`).
  - Add a show-password toggle.
  - Make "At least 8 characters" turn red when it isn't met.
- **Layout:**
  - Keep the card at a fixed height from the top, so the step buttons don't
    jump between steps.
  - Put the Install action bottom-right, where every other step has it.
- **Summary:** name the image, and for the NAS say that the role finishes on
  first boot (it needs the network, and Secure Boot must be off).
- **Finish:**
  - Show the IP as well as the name.
  - Add a Shut down button beside Restart, for headless boxes.

### First-boot wizard
- Don't ask again for the host name and time zone the installer collected.
- Regenerate the SSH keys without asking.
- On the NAS image, skip the role menu, or at least drop "Storage node", which
  the plan folded into the NAS.
- Ask for the LAN subnet once, in the desktop's Setup (Network step), rather
  than in both places.
- Keep kernel and systemd messages off the wizard's screen: dozens of
  `AF_VSOCK` lines, and `[FAILED] zfs-load-module` before ZFS is built.
- On Multi, end with the IP and how to connect over SSH, as the NAS version
  does.
- The text wizard looks dated next to the browser installer. In the long run,
  move these questions into the installer or the desktop's Setup.

### NAS login and desktop
- **Login page:**
  - Show the NAS's name on the login card.
  - On first login, say "the account you made in the installer", not "set in
    Users".
- **Health:** the summary line "Nothing needs attention · 3 of 10 checks
  passed" reads as a contradiction. Add "· 7 not applicable" in the widget too,
  as the Health app does.
- **Disks widget:** explain the "?" icons ("no SMART data on virtual disks").
- **Small screens:** windows open small and overlap at 800 px. Open them
  maximised below some width.

### Setup
- **Storage:**
  - Make "Choose all 5" leave out the free part of the system disk when it
    doesn't match the other disks' size, since ZFS wants disks of the same
    size.
  - Show usable space, not raw size, in "Your pools".
- **People and shares:**
  - After "Create share", list the shares made so far.
  - "…are under Settings once it exists" should name the Shares app.
- **Step buttons:** make them clickable, as they are in the installer.
- **Alerts:** say that alerts already go to the desktop's notification centre
  with nothing set up, so "Email: off, ntfy: off" doesn't read as "no alerts".
- **Files:** open the share when there is only one.

## Harness notes (nas/tests/vm/vm.sh)
- **`live` mode:** it never presses the hotkey for the live entry.
- **Monitor socket path:** it can exceed the 108-byte limit for a Unix
  socket.
- **Fixed ports:** 2222, 8443 and 8088 are fixed, so a second VM can't run
  beside the first.
- **Test password:** `vmtest` is 6 characters and would fail the graphical
  installer's 8-character rule.
- **Remote mode:** it boots the installer by direct kernel boot, so "Restart
  now" restarts the installer. Booting the installed system needs `vm.sh boot`.
