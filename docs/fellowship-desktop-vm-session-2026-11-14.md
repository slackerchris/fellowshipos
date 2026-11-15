# Desktop VM session — November 14, 2026

> **Superseded the same day and the next.** This handoff records the state
> at the time it was written; several things it lists as open defects have
> since been closed and verified, so do not use it as current status. The
> flush fix shipped into a rebuilt ISO and the pointer and display size
> checked out in the VM (in the desktop repository:
> `docs/session-2026-11-14-vm-responsiveness.md`,
> `docs/session-2026-11-14-iso-and-apps.md`, and
> `docs/session-2026-11-14-day-summary.md`);
> the notification and sound blockers were closed on November 15
> (`session-2026-11-15-notifyd-probe.md`), the decoration question was
> measured, reverted and then solved with an explicit rules file
> (`session-2026-11-15-csd-measured-and-reverted.md`,
> `session-2026-11-15-decoration-signal.md`), and the everyday gaps were
> costed and shipped (`session-2026-11-15-everyday-gaps-measured.md`,
> `session-2026-11-15-clean-image.md`). What is still genuinely open is
> sound on real hardware — the Dell — and the blank-VM install test.

## Current status

The user wants to test the Fellowship desktop in a usable VM before moving to the Dell. The live desktop boots and has rendered at 1920 × 1080. Interactive testing remains incomplete. The user repeatedly reported a missing mouse pointer, inconsistent launcher response, and lag. Treat these as open defects.

A compositor input-delivery fix has compiled locally and passed the existing launcher integration suite. It still needs a Debian-compatible build and verification in the VM. The current ISO predates that fix.

## Repositories and local changes

| Repository | Branch and baseline | Changes from this session |
| --- | --- | --- |
| `/home/chris/Git/fellowshipos` | `nas-edition`, `f312127` | Default app additions, Chromium configuration, cursor-theme default, this handoff |
| `/home/chris/Git/fellowship-desktop` | `main`, `374a2b6` | End-of-event-loop Wayland client flush in `crates/fellowship-comp/src/main.rs` |

All session changes are local and uncommitted. No commits or pushes were made in this session. `/home/chris/Documents/ChatGPT/fellowshipOS` is a separate planning workspace. Engineering sources live in the two Git repositories above.

## Apps included in the desktop ISO

The agreed defaults were checked against `fellowship-desktop/docs/basic-desktop.md`. The existing ISO manifest already listed Foot, Nemo, Mousepad, Ristretto, Atril, mpv, Engrampa, and Qalculate!. Chromium and Rhythmbox were added to `desktop/package-list.chroot`. The rebuilt app image's manifest confirmed all ten packages. Zenity is also included.

LibreOffice Writer and Calc remain optional under the recorded agreement. The task manager and software/update application remain undecided in that checklist.

`desktop/chromium.conf` exports Chromium flags `--ozone-platform=wayland --gtk-version=3`. `auto/config` installs it at `/etc/chromium.d/fellowship` for the desktop edition and removes the generated file when building another edition. `.gitignore` excludes that generated copy.

The app image was built with the existing desktop and installer packages using `FELLOWSHIP_EDITIONS=desktop FELLOWSHIP_SKIP_PACKAGES=1 ./build.sh`. Individual app launches, file associations, media playback, and the installer have not been comprehensively tested.

## VM observations

- The existing `desktop/tests/vm.sh` passed on the original ISO and again after the app additions. It directly boots the ISO's live kernel and initramfs and uses dark/gold screenshot samples as its success condition. This establishes desktop rendering only. It does not measure interaction latency or exercise the ISO boot menu or installer.
- Initial virtual display mode was 1280 × 800. Later launches used `virtio-gpu-pci,xres=1920,yres=1080` plus kernel argument `video=Virtual-1:1920x1080`. Framebuffer captures after desktop rendering confirmed 1920 × 1080 in several sessions. The user continued to report a small or wrong-looking display in some viewer configurations. Guest pixel dimensions alone do not establish satisfactory host-window sizing or scaling.
- GTK, SDL, GTK through XWayland, and fullscreen SPICE/remote-viewer were tried. Fullscreen SPICE displayed a 1080p guest and an opened launcher. User-reported responsiveness and cursor visibility remained unsatisfactory.
- Allocations ranged from 2 vCPUs/4 GiB to 12 vCPUs/16 GiB. The host reported 24 logical CPUs and roughly 94 GiB RAM. More resources did not resolve the reported symptoms. Recent fullscreen SPICE launches used 4 vCPUs/8 GiB.
- Standard virtio graphics used software rendering. A `virtio-gpu-gl-pci`/SDL OpenGL trial started, but QEMU screendump returned `no surface`. Guest renderer selection and successful hardware acceleration were never verified. This capture error alone does not diagnose the guest rendering failure.
- One screenshot-polling trial observed a launcher change after approximately 287 ms following a simulated Super tap. Another capture showed an open launcher about a second after a keypress. These are rough observations, include measurement overhead, and do not establish acceptable latency or a reliable frame rate.

## Cursor investigation

The session exported `XCURSOR_THEME=Breeze`, while the built filesystem contained `/usr/share/icons/breeze_cursors/cursors/default`. The default in `desktop/session/fellowship-session` was changed to `breeze_cursors`, preserving any explicit environment override.

The existing `.deb` was unpacked in a Debian container, its session script replaced, and the package rebuilt. The ISO was then rebuilt with package compilation skipped. A fresh fullscreen SPICE guest was launched as `FellowshipOS 1080p Cursor Fix`.

The final framebuffer capture measured 1920 × 1080. A small crop near the injected pointer position showed launcher content and did not establish a visible cursor. The correction is included in the rebuilt image, but mouse-pointer visibility still requires verification. Also inspect whether cursor theme settings are overridden later and whether the cursor is assigned to a hardware plane that the viewer or capture path handles differently. Those are investigation leads.

## Compositor input-delivery finding

Previously, `main.rs` ran the event loop with an empty end-of-turn callback. Existing flushes occurred after client request dispatch and drawing. Hardware input can queue Wayland keyboard, pointer, or focus events without causing either path to run immediately. This can delay delivery to an idle client until another event causes a flush.

The local change calls `state.display_handle.flush_clients()` after each event-loop turn and logs a warning on failure. This addresses a concrete delivery gap. It remains a candidate explanation for the observed inconsistency until tested in the DRM guest. Super-tap launcher activation also uses the shell control socket, so that shortcut needs its own latency measurement.

Validation completed on the host:

- `cargo build -p fellowship-comp --locked` succeeded.
- `tests/launcher.sh /tmp/fellowship-launcher-flush-test` exited successfully. Its eight checks covered opening/search, app context menu, Favorites, dock pinning, section changes, outside-click dismissal, persistence across shell restart, and reopening.
- `git diff --check` passed for the compositor change.

The launcher suite runs a nested session and does not establish DRM input timing, guest cursor visibility, or VM performance. A regression check should exercise input after the desktop has been idle, with no pointer motion or unrelated redraw to release buffered events.

## Current ISO and build limitation

Artifact path is `/home/chris/Git/fellowshipos/fellowshipos-desktop-amd64-1.0.hybrid.iso`.

At handoff, its size was 1,386,577,920 bytes and SHA-256 was:

```text
18efc6ef694337e016dc55ecd39adcc2f551daeab447e29730cd6d38f5df6017
```

It contains the app additions and cursor-session correction. The end-of-loop compositor flush is present only in source and the host debug build.

The host uses glibc 2.43. The rebuilt host compositor references `GLIBC_2.43` symbols, including `atan2f` and `acosf`, whereas the ISO uses Debian trixie's older glibc. Build the compositor against the image's libraries before deploying it to the guest. `desktop/build.sh` already supplies the Debian container build path.

The attempted Debian-compatible compositor build was blocked before execution because automatic approval review could not complete after an account usage limit was reached. The tool explicitly reported a review failure. No Debian build of this fix completed during the session.

## Resume work

1. Inspect both repositories' working trees and preserve the local changes recorded above.
2. Rebuild the desktop package and image through the existing container workflow, including the compositor source change:

   ```bash
   cd /home/chris/Git/fellowshipos
   FELLOWSHIP_EDITIONS=desktop ./build.sh
   ```

   This build must include the package step to incorporate the new compositor. Inspect the resulting package and ISO before testing.
3. Run the desktop smoke test in a fresh output directory so its cached kernel and initramfs come from the new ISO:

   ```bash
   cd /home/chris/Git/fellowshipos
   desktop/tests/vm.sh "$(mktemp -d /tmp/fellowship-desktop-check.XXXXXX)"
   ```

4. Start one visible guest with a fresh kernel/initramfs extraction, KVM, 4 vCPUs, 8 GiB, virtio GPU at 1920 × 1080, the explicit video kernel argument, and a USB tablet. Fullscreen SPICE was the last viewer configuration used. Check the guest framebuffer and host display sizing separately. Inspect running processes before replacing a session the user may be using.
5. Verify the pointer visibly moves and clicks land correctly. If it remains absent, inspect the guest environment, cursor loading, and cursor-plane rendering.
6. Measure repeated launcher openings by both Super tap and dock click, including idle periods and stationary-pointer clicks. Check search typing, dismissal, window dragging, and app launches. Record latency distributions and the actual guest renderer rather than inferring performance from resource allocations.
7. If lag persists, capture compositor/shell timing and CPU usage inside the guest and verify the rendering driver before changing VM settings again.

The acceptance condition is a visibly tracked pointer and consistently prompt interaction at the intended display size. Hardware testing remains pending.

## Temporary evidence

These directories were created during the session and may disappear after cleanup or reboot. The durable conclusions are recorded above.

| Location | Evidence |
| --- | --- |
| `/tmp/fellowship-apps-vm.BGtAR2/` | App-image boot screenshot and extracted boot files |
| `/tmp/fellowship-spice.nUmA8l/` | SPICE captures and rough menu timing captures |
| `/tmp/fellowship-cursor-fixed.gYcJw3/` | Latest cursor-image boot files, framebuffer and crop |
| `/tmp/fellowship-launcher-flush-test/` | Successful nested launcher suite screenshots and session log |

Other open items inherited from the earlier handoff include the double title bar, the portal's missing `version` property, rebuilding NAS/multi images from the updated tree, and Dell hardware verification.
