# The Proxmox tour's six findings — plan, November 16, 2026

Chris toured the installed NAS on Proxmox (VM `fellowshipnas`, 9999) and brought
back six findings. This is the work order, the cause of each where one is known,
and the gate it lands in. Gates are reviewed one at a time; nothing is pushed.

| # | Finding | Gate |
| --- | --- | --- |
| 1 | Clear alert history, without touching current Health findings | A |
| 2 | Dismiss desktop notices; stop warning once Health says resolved | A |
| 3 | Health must expect the SSH daemon only when SFTP or root SSH is on | A |
| 5 | "Check array" said *stopped before it finished* | B |
| 4 | Delete array, with shares and disks shown and a confirmation | C |
| 6 | Start / stop / restart individual services from the desktop | D |

## Gate A — alerts and Health tell the truth

**1. Clear history.** `alerts.py` keeps the log in `STATE_DIR/alerts.json`
(`send_alert`, `MAX_ALERTS` 500); there is no clear. Health's findings are
computed live by `health_report()` from the checks, never read from that log, so
clearing the log cannot hide a current problem — the test asserts exactly that.
Add `alerts.clear()`, `fnas alert clear`, the maintenance and helper allow-lists,
and a **Clear history** action in the Alerts app behind the existing typed
confirmation.

**2. Dismiss notices.** The desktop's *Recent alerts* widget
(`desktop/shell.tsx`) prints the newest three log entries forever, so a critical
from a resolved problem keeps staring at the owner. Add a per-notice dismiss:
`alerts.dismiss(time, title)` marks the entry, `fnas alert dismiss`, and the
widget skips dismissed entries. The Alerts app keeps the full history (dismissed
entries greyed), because history is the record; dismissal is about the widget.
The *warning* half of the finding is the Health widget, which already derives
from live `health.problems` — it goes quiet by itself once a finding is gone,
which is what finding 3's false SSH critical was defeating. No client-side-only
state: dismissal lives in the store so every browser and every reload agrees.

**3. When SSH should run.** `services.system_services()` marks `ssh.service`
"off" when `ssh.settings()["enabled"]` is false — on a separated NAS that is the
**SFTP** switch, and it ignores root's own SSH. So with SFTP off and root SSH on
the daemon is labelled "Turned off in Settings" while it is actually running,
and the expectation Health checks is wrong in both directions. The expectation
is: the shared daemon runs whenever **SFTP is on or root SSH is on** (old model:
the SSH switch). `services.protocols()` likewise reports SFTP "running" from the
unit while SFTP is switched off and the daemon is up only for root; it must say
"off", and Health must not raise a critical for shares that use a protocol the
owner turned off on purpose. Verified across all four switch combinations in
`ssh-samba.sh`, against real sshd.

**Gate A result, 2026-11-16:** built and tested. Unit **374/374** (6 new);
`separated-login.sh` **53/53** (8 new: dismiss, clear, record kept and marked,
Health's findings unchanged by clearing, non-admin refused); `ssh-samba.sh`
**71/71** (2 new: both switches off raises no SSH/SFTP finding and the daemon
reads as turned off); `maintenance.sh` **26/26**; `mdadm-building.sh` **9/9**;
`tsc --noEmit` clean. One harness trap found and fixed: a container has no
`/lib/modules`, so `fnas health` crashed mid-suite and the wrapper's grep-based
exit code hid the unfinished run — the suites now create the directory, and a
run is judged by its last check, not by the wrapper's exit.

## Gate B — the check that "stopped before it finished"

`jobs.run_job()` catches only `FnasError`; any other exception kills the
`systemd-run` watcher, the unit goes inactive with the job still RUNNING, and
`jobs.checked()` then reports *stopped before it finished* — the desktop lost
the result, the kernel may have finished or never started. The likely trigger on
the tour is the name bug fixed in `5775c81`: while an array builds,
`mdadm --detail --scan` omits its name, `array_device()` returns nothing, and
`missed(None)` / `sync_from(None)` raise a TypeError nobody catches.

Two fixes, both testable on a real array: resolve a missing array to a
`FnasError` with the pool's name instead of crashing, and let `run_job` record
*any* exception as the job's error so Storage always shows a reason. Then prove
the whole arc in `mdadm-building.sh`: start a check on a finished array, watch
`job show` reach `done` with the check recorded, and force a failure and see its
reason. A restart mid-check must still say *stopped before it finished* — that
message is honest when the watcher really was killed.

**Gate B result, 2026-11-16:** built and tested. `array_device()` already raised
a named `FnasError`, so the silent-loss class was the uncaught-exception path:
`run_job` now records any exception as the job's error, and a kernel-refused
`sync_action` write becomes a named refusal at the click instead of a traceback.
On a real four-disk array: a check started while building is refused with
"already busy with a resilver" (no traceback); once protected, the check reaches
`done` and `last_scrub` is recorded on the pool; a failed disk still reads
DEGRADED and critical. Unit **376/376** (2 new); `mdadm-building.sh` **15/15**;
`separated-login.sh` 53/53, `ssh-samba.sh` 71/71, `maintenance.sh` 26/26.
The container has no systemd, so the harness launches the job watcher the way
`systemd-run` would (a stub, like the existing systemctl one) — the job logic
and the kernel's check are what get tested. Chris's exact click on the old image
was not reproducible here; the completion path is proved and the silent-loss
class is closed, and a future watcher death carries its reason.

## Gate C — delete array

Export keeps the array; there is no delete. Add `fnas pool delete NAME` with the
same typed confirmation as removing a person: the answer names the shares that
would lose their home and the disks that would be wiped, and nothing happens
without the phrase. It unmounts, forgets the shares' pool, stops the array and
wipes the mdadm superblocks so the disks read as empty and can build a new
array. ZFS and mergerfs keep their own existing removal paths; this gate is the
Traditional RAID one the tour hit, and the confirmation lists what each engine
would destroy. Proven on the test VM's four disks: build `tank`, delete it, build
again from the same four.

**Chris's ruling (2026-11-16):** delete does **not** remove shares and does not
leave them homeless. While any share lives on the pool, delete is **refused**:
the answer names those shares and offers a button that opens them in Shares, so
the owner removes them first and then deletes. With no shares left, the typed
confirmation names the disks about to be wiped.

**Gate C result, 2026-11-16:** built and proven from the desktop on a fresh
install of the built ISO in a QEMU VM with five disposable virtio disks
(`nas/tests/vm/vm-delete-proof.sh`, 21 checks): create through the API; a share
blocks delete and names itself; a wrong name is refused listing the exact four
member disks; typing the pool's name deletes it; the four read empty; the same
four rebuild and read ONLINE; the fifth disk's file survives. Unit tests cover
the refusal, the disk listing and the wipe calls. The container-only
`pool-delete.sh` was removed: on a host whose udisks/udev probes freshly
appeared SCSI disks, mdadm cannot open them exclusively, so create-from-desktop
is impossible there and the VM is the faithful seat. One honest gap in the
evidence: the unrelated disk's UUID comparison printed nothing because `blkid`
without `-p` reads a stale cache; the file surviving, the filesystem still
reading, and the refusal naming exactly the four members carry the claim. The
harness now uses `blkid -p`.

## Gate D — service controls

Settings gains per-service start / stop / restart with live state, for the
sharing servers (Samba, NFS) and the SSH daemon. `fnas service start|stop|restart
UNIT` behind the maintenance allow-list, restricted to a fixed set so the desktop
can never stop the web server or the helper that serves it. Stopping the SSH
daemon stops SFTP with it, and the panel says so before the click; SFTP's own
switch stays separate. Health treats an intentional stop as "off", not a failure
(Gate A's expectation rules), and the test proves stopping Samba leaves NFS and
the desktop reachable.

**Decision for Chris:** restart of the SSH daemon while connected over SFTP will
cut that transfer; the panel warns, and the action stays available. Restart of
the web server or helper is deliberately not offered — it would cut the desktop
off mid-click.

**Gate D result, 2026-11-16:** built and proven in the same VM run and in
`ssh-samba.sh` (82/82): Settings → Services lists Samba, NFS and the SSH daemon
with live state; start, stop and restart sit behind a typed confirmation;
stopping the SSH daemon says SFTP goes offline with it and never moves SFTP's
own switch; Health stays quiet for a stop the owner asked for and still flags a
crash; the web server and helper are not controllable from the desktop. Unit
tests cover the remembered stop, its clearing on start, and the crash case.

## The recovery-key type-back gate — removed, 2026-11-16

Chris met it again on the Proxmox install and ordered it gone. It was never
removed earlier: it had been audited, condemned against precedent (every real
product hides the secret while confirming it), and left deferred — a gap in
follow-through, not a decision to keep it. Now removed on both sides: the page
shows the key once with **Save as text file** in every context (including the
machine's own screen, with a note that a copy kept on the NAS is lost with it),
and the install's only confirmation is the typed erase phrase naming the disk.
The server no longer accepts or checks `recovery_confirm`; the key it made is
still stored and still shown once, never typed back. Installer Go tests and the
page's `tsc --noEmit` pass; a full install through the installer API without any
type-back is proven by `nas/tests/vm/vm-delete-proof.sh`.

## The CPU gauge — fixed, 2026-11-17

Chris reported it again from Proxmox: 0% while the machine was at ~5%, and 0% at
~1% idle. The cause was the one diagnosed on 11-14 and never built: every request
sampled `/proc/stat` for 0.12 s inside a fresh `fnas` process, so the sample mostly
measured that process starting and rounded to zero.

Now the helper runs one continuous sampler (`internal/helper/cpu.go`): a reading
every 2 s, a smoothed "now" over the last 10 s, and five minutes of history,
written to `cpu.json` in the state dir. `fnas health` reads that figure when it is
fresh (≤6 s) and only samples a whole second itself when no helper is running —
so the Health page, the desktop widget and the command line show **one number**.
iowait counts as idle; steal is reported apart ("N% taken by the host") because in
a VM it is time the host took back. The Health CPU card says "of N processors"
and draws the five-minute sparkline.

Proven: unit tests for the tick math, the smoothing and the fresh/stale figure;
Go tests for the sampler; and `ssh-samba.sh` under two real busy loops — the
figure lands near 2-of-N cores, the desktop and the CLI agree within 3 points,
and it falls back at idle. The Proxmox eyeball check against `mpstat` and
`stress-ng` is the remaining done-when from the 11-14 plan, for the next tour.

## Out of scope here

Green-in-every-theme and the black rectangles.
