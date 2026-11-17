# FellowshipNAS on Proxmox, 2026-11-14: findings and plan

Chris installed the NAS image in a Proxmox VM (`fellowshipnas`, VM 9999 on
`helmsdeep`: OVMF, i440fx, VirtIO SCSI single, 4 GiB, 4 × x86-64-v2-AES,
standard VGA, noVNC console) before moving to bare metal. This lists what he
found, what caused it, and where each fix stands.

## Fixed and committed

| Found | Cause | Fix | Commit |
|---|---|---|---|
| Nothing on the installer could be clicked; **Space →** never advanced | Trixie's cog 0.18.4 sends the wrong button code with Wayland pointer events, so WebKit fires `mousedown`/`mouseup` but never `click` (Igalia/cog#760) | Build sid's cog 0.18.5 for trixie into the image (`installer/cog/build.sh`) | `6523147` |
| GRUB: "attempt to read or write outside of disk `cd0`" | The ISO upload to Proxmox stopped at 618 MB of 1 GB | Upload again and check the size (981.78 MiB) | none needed |
| First boot asked hostname, time zone and SSH questions in text | The shared first-boot wizard ran for the NAS too | A graphical NAS install finishes the role with no questions; other roles keep the wizard | `4429a5a` |
| The NAS's own screen was a bare login prompt | Nothing was planned for it | Above the prompt: the address, the certificate's SHA-256 fingerprint, desktop/pools/health status (`fellowship-nas-issue`) | `4429a5a` |
| The role failed silently with Secure Boot on (the VM has `pre-enrolled-keys=1`) | ZFS's module is built on the machine and Secure Boot won't load it | The installer stops on every step with the reason and the way to turn Secure Boot off; the server refuses too | `4429a5a` |
| `ssh.service` failed on first boot | The image ships no host keys | `ssh.service` makes missing keys before it starts | `4429a5a` |

## Fixed, checked in a VM, not yet committed

Checked with a Proxmox-like VM (q35, VirtIO SCSI, xHCI tablet, standard VGA):
a mouse-only install took 47 s. The CD was ejected *before* **Restart now**,
and the restart went through. About 45 s later the console showed the
finished status with no `[FAILED]` lines. ZFS was loaded, no units had
failed, and the account was in `nas-admins` and `nas-ssh`. The web desktop
logged it in as an admin, with no shares yet. A role that stops is retried at
the next start; that was checked by rebooting after a failed run.

| Found | Cause | Fix |
|---|---|---|
| **Restart now** hung after the virtual CD was removed | The live system ran from the CD, so the restart couldn't load what it needed | The install entry boots with `toram`: the live system is copied into memory, and the stick or disc can come out at any time. The installer still refuses a FellowshipOS installer stick as a target, found by its `FELLOWSHIPOS_*` label. The finished page now says to restart first. |
| First boot took 90+ s and showed `[FAILED]` for `hd-idle`, `zfs-load-module`, `zfs-import-cache` | First boot downloaded the NAS packages and compiled ZFS while their services were already starting | Chris chose to build the role into the ISO: `hooks/normal/0750-nas-role` installs the packages and compiles ZFS for the image's kernel. It turns their services off so the live installer isn't a NAS (it would take port 80 and import pools). First boot only turns them on. Installing needs no network. |
| (found while checking) The account wasn't made an admin | First boot looked for the installer's account at UID 1000; with `nas-share` (UID 3000) already in the image, the account is 3001 | First boot takes the first account that can log in |
| (found while checking) The role stopped at once on first boot | `systemctl reset-failed zfs-load-module` errors when the unit has never run | Ignored when there is nothing to reset |
| Files told the admin to ask "an administrator" | The empty state didn't tell admins apart, and there were no pools or shares yet | Admins see that shares live on a pool, with **Open Storage** and **Open Shares** |

## Open: CPU usage is wildly wrong

**Seen:** the Health widget showed 12% and 26%, the Health app showed 0% at
the same moment, and Proxmox showed 1.5% of 4 CPUs.

**Cause** (`fellowship_nas/health.py`, `cpu_usage`):

- Each request runs `fnas health`, a new Python process, and samples
  `/proc/stat` over **0.12 s** inside it. The machine is busy with exactly that
  request: Python starting, `lsblk`, SMART and ZFS queries. The gauge mostly
  measures the act of measuring.
- The widget and the app each take their own sample, so they disagree at the
  same moment.
- 0.12 s is about 48 kernel ticks across 4 CPUs, so one busy tick moves the
  figure by 2 points.

**Plan:**

1. Sample once, in a process that is already running. The web server reads
   `/proc/stat` every 2 s and keeps a smoothed value (the last 10 s) plus the
   last 5 minutes of samples. Health, the widget and `fnas health` all read
   that one figure, and nothing samples inside a request. `fnas health` on the
   command line, without the server, samples over 1 s itself.
2. Count iowait as idle, as today, and show **steal** separately when it
   isn't zero. In a VM that's time the host took back, which is what Proxmox's
   view adds up differently.
3. Say what the number is: "of 4 processors", with a 5-minute sparkline under
   the dial from the server's samples.
4. **Done when:** idle in Proxmox, the dial stays within 3 points of `mpstat
   10 1` in the VM and close to Proxmox's own figure. Under `stress-ng --cpu
   2` on 4 CPUs it reads about 50%. The widget and the app always show the
   same number.

## Open: green stays green in every theme

**Seen:** in Silverwater, Highwatch and Hearthfield, the memory ring, the CPU
dial, the "Nothing needs attention" banner and its tick are all the same
Fellowship green, in light and dark.

**Cause:**

- `--good`, `--warning`, `--bad` and their soft versions are defined once in
  `web/src/style.css`. `themes.mjs` never works them out, so no theme can
  change them.
- Plain measurements default to `kind="good"` (`Gauge`, `ArcGauge`,
  `Sparkline`, `TrendChart` in `charts.tsx`, and `level()` in `health.tsx`), so
  "14% of memory used" is drawn in the "healthy" colour.

**Plan:**

1. **Measurements use the theme's accent.** Memory, CPU, load and space are
   readings, not verdicts: they use the accent until they cross a threshold,
   then warning or bad. Green is kept for things that passed: checks, the
   banner, done steps.
2. **Status colours come from each theme.** `themes.mjs` works out good,
   warning and bad for each theme and mode from its window and accent. Each
   keeps its meaning (green-ish, amber, red), is pulled toward the theme's
   hue, and is checked for contrast like the text is. A theme can pin any of
   them under `[mode.pinned]`. The Rust desktop reads the same rule, so
   `themes/README.md` gets the new keys and the rule.
3. **Done when:** the Health app and the widget are screenshotted in all four
   themes, light and dark. No measurement is green unless it's a pass, every
   status colour passes the contrast check, and Fellowship looks as it does
   now.

**Decision for Chris:** should "good" stay recognisably green in every theme,
only tinted toward the theme (recommended, so a pass still reads as a pass at
a glance)? Or should it follow the theme fully, for example teal in
Silverwater and plum in Highwatch?

## Open: black rectangles in the console while moving the mouse

**Seen:** in the noVNC console during the install, black blocks appeared on
the wallpaper where the pointer had been, on hover and click.

**Status:** reproduced locally on 2026-11-15, during a phase 4 install test.
- Black bars and blocks showed in QEMU's own screen captures (`screendump`):
  over the recovery key while typing, and on the wallpaper while the install
  copied files.
- So the black is in what the guest draws (cage and cog on standard VGA), not
  something noVNC adds.
- It shows under load, which matches Chris's screenshot, taken mid-install.

Earlier it hadn't reproduced, through noVNC, with the same CPU type, machine
type and standard VGA. Redraws in this setup are slow: the page repaints
about 5 times a second while the pointer moves, all in software. Hover
highlights lag behind the pointer, but no black blocks were seen. The
screenshot was taken while the install was running, the busiest moment.

**Next:**

- Check whether the blocks appear while idle on the Disk step.
- Try the VM with Display set to VirtIO-GPU.
- Grab a recording of the noVNC session if possible.

## Update, 2026-11-16: a new array is building, not degraded

Chris created a RAID5 pool on the Proxmox tour and got a **critical "Pool tank is
degraded"** at the moment of creation, while `fnas pool status tank` a minute
later said `clean`. Both were true at different moments, and the alarm was wrong
to fire at all.

Measured on a real four-disk RAID5 (`nas/tests/integration/mdadm-building.sh`):
while a new array writes its initial parity the kernel shows `[4/3] [UUU_]`,
`mdadm --detail` says `State : clean, degraded, recovering`, and mdadm's monitor
emits `DegradedArray` — with every disk present and none failed. mdadm's word
describes protection, not membership: until parity is consistent a disk loss
would lose data. It is still not a missing disk, and waking someone for it is
wrong. Chris's ruling: while it is building, wait; no critical.

Fixed:

- `engines/mdadm.py health()` returns **BUILDING** while a resync, recovery or
  reshape runs with every disk present, and ONLINE when it finishes. A disk
  actually gone or failed is still DEGRADED.
- `health.py health_findings()` raises nothing for BUILDING, so no critical
  finding and no alert from the checks.
- `md-alert` on `DegradedArray` now reads `mdadm --detail` and only raises
  critical when working devices are fewer than raid devices or a device has
  failed; otherwise it sends info "Pool … is building".
- The desktop shows BUILDING as an amber "Building" badge/chip (Storage, Health,
  the widgets), not a red degraded one.
- A second bug in the same window: `mdadm --detail --scan` prints **no name**
  while the array is building (it counts the disk being written as a spare), so
  the pool could not be matched to its mount and vanished from `pool list`.
  `scan_arrays()` now falls back to the name in `mdadm --detail`, which always
  has it.

`mdadm-building.sh` proves the whole arc on real loop disks: BUILDING and no
critical mid-sync, info from the monitor hook, ONLINE once protected, and still
DEGRADED plus critical when a disk is really failed out. It takes only free loop
devices and gives them and the array back at the end.

Also fixed the same day: the Network settings panel kept "The NAS didn't answer"
and the "open the new address" notice over a NAS that had already rolled back and
was answering again. It now keeps asking while an apply went unanswered, and
when the NAS answers with nothing waiting it clears the stale failure and shows
the settings it actually has.

**Still open, unchanged:** the CPU gauge (the 0.12 s in-request sample above),
green in every theme, and the black rectangles. The CPU fix waits for Chris's
go-ahead.
