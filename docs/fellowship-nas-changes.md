# FellowshipNAS: changes from Chris's review (Nov 9)

These come from testing the desktop in a VM. They go into the next plan revision, which Chris sees before anything is built.

**The rule for every screen:** show every feature and its state, even when it can't be used yet, with the reason. Disable rather than hide. Say plainly what isn't built.

Status: **Agreed** (Chris asked for it), **Open** (needs Chris's answer), **Found** (found in the audit, not raised by Chris yet).

## Storage

1. **Agreed:** three storage engines, chosen per pool:
   - ZFS
   - Unraid-style: each disk formatted on its own, with parity disks (mergerfs + SnapRAID)
   - Traditional RAID: build RAID 1, 5, 6 or 10 with mdadm, then format it
2. **Agreed:** Create pool asks for the engine first, then shows only that engine's options. Engines that aren't built are shown as "Not built yet".
3. **Agreed:** layout labels in plain words: what you get, how much space, and how many disks can fail. The technical name goes in small print.
4. **Agreed:** drop layouts nobody would pick, such as a 3-disk mirror.
5. **Agreed:** Btrfs stays as a fourth engine (mirrors only: `raid1`, `raid1c3`, `raid10`).
6. **Agreed:** Unraid-style disks are XFS, as on Unraid. ext4 caps a single file at 16 TiB, and SnapRAID keeps each parity level in one file, so ext4 can't hold full parity for disks over 16 TiB. **Open:** traditional RAID offers ext4 and XFS, with XFS recommended.

## Sharing

7. **Agreed:** an always-visible "Ways to share" section, with each protocol's state, what it's for, how to connect, and its own settings.
8. **Agreed:** add SFTP, FTP and WebDAV now. DLNA and iSCSI come later. **Open:** plain FTP sends passwords unencrypted; offer FTP with TLS (FTPS) by default and plain FTP only as an opt-in?
9. **Found:** no per-user permissions on shares. A share is either "shared" (everyone writes as one account) or "matched" (one owner). There's no "Alice can read, Bob can write".

## Users

10. **Found:** no Users app. Accounts can't be created, removed or grouped from the desktop; only an SMB password can be set for an account that already exists.

## Health

11. **Agreed:** every check on its own line, with its result (passed, warning, failed, or doesn't apply yet and why) and when it last ran. No more "Everything looks fine" when there is nothing to check.
12. **Agreed:** add services (SMB, NFS and each new protocol; the web server; the scheduler; ZFS's event daemon).
13. **Agreed:** add the system (system disk, memory, load, temperatures, uptime, updates, reboot needed).
14. **Agreed:** per pool: state, space, last scrub, last snapshot, last backup.
15. **Agreed:** show whether alerts are set up and when the last one was sent.

## Alerts

16. **Agreed:** send through Apprise (100+ services: Discord, Telegram, Pushover, Gotify, Home Assistant, Slack, Matrix, webhooks, email, ntfy and more). Existing email and ntfy settings carry over.
17. **Agreed:** as many channels as wanted, each with a test button, routed by severity.
18. **Agreed:** history showing what was sent, where, and whether it got through; alerts also appear in the desktop's notification centre.
19. **Agreed:** by default, alerts go to the desktop's notification centre, with no outside service needed. Email, ntfy and the Apprise services are extra channels on top; which ones to offer is sorted out later.

## Setup and Settings

20. **Agreed:** Setup opens by itself on an admin's first login, with "Skip for now". Afterwards it is gone from the desktop and launcher.
21. **Agreed:** each setup step lives in its own app. Settings gets "Run setup again".
22. **Agreed:** after a reinstall, Setup first offers to restore settings found on a pool.
23. **Found:** the nightly backup can only be set up in Setup and the Shares page. Removing Setup must not take it away: it needs a home (a Backup section or app) showing every job, its last run and its next run.
24. **Found:** Settings has no network settings (IP address, static or DHCP, DNS, gateway) and no updates screen.

## Snapshots and files

25. **Found:** snapshots can be scheduled but not browsed or restored from the desktop.
26. **Found:** the Files app (a real file manager, with "Previous versions") is in the plan but not built.

## FygoOS features not in the plan

27. **Agreed:** Docker, an app centre, VMs, Immich and Jellyfin are not needed now. A working Files app (item 26) is wanted, and is a priority.

## How the work is checked

28. **Agreed:** before showing the rewritten plan, check every app against the FygoOS list and the rule above, so gaps don't wait to be found.
