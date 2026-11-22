# FellowshipOS

FellowshipOS builds two Debian Trixie live ISOs and a Proxmox LXC template for amd64 homelab systems: **FellowshipOS Multi-purpose** and **FellowshipOS Desktop**. **FellowshipNAS**, the NAS appliance with its browser desktop, became its own project on 2026-11-20 and is built from the FellowshipOS-NAS repository (`~/Git/FellowshipOS-NAS`).

[Build](#build) · [Deploy to Proxmox LXC](#deploy-to-proxmox-lxc) · [Develop and test](#develop-and-test)

## Build

On a Linux host with Docker, internet access, and at least 10 GB of free space, run this from the repository root:

```bash
./build.sh
```

| Output | Use |
| --- | --- |
| `fellowshipos-multi-amd64-0.1.0.hybrid.iso` | Install a general FellowshipOS machine, or try it live |
| `fellowshipos-desktop-amd64-0.1.0.hybrid.iso` | Boot into the Fellowship desktop, or install it |
| `fellowshipos-trixie-amd64-0.1.0_lxc.tar.gz` | Create a general-purpose Proxmox container |

The images are the same live system built per edition. `auto/config` gives each one its ISO name and boot menu from `FELLOWSHIP_EDITION`, and the boot menu tells the graphical installer which image it is. `./build.sh` builds the multi-purpose image; `FELLOWSHIP_EDITIONS="multi desktop"` builds both. The build packages the graphical installer, runs Debian `live-build` in Docker, and packages the LXC root filesystem from the multi-purpose chroot. See `build.log` for build and packaging errors. The release version lives in the [VERSION](VERSION) file — prerelease `0.major.minor`, see [docs/versioning.md](docs/versioning.md). `build.sh` names the ISOs and template from it, and the package builds stamp their changelogs from it. If it changes, update the expected filename in [create-ct.sh](proxmox/create-ct.sh) or pass `FELLOWSHIP_TEMPLATE`.

### The desktop image

The desktop image is that same live system with the Fellowship desktop in it. Build it from a checkout with the `fellowship-desktop` repository beside this one:

```bash
FELLOWSHIP_EDITIONS=desktop ./build.sh
```

`desktop/build.sh` builds the Rust desktop in the build container (rustup's stable; trixie's rustc is too old for its dependencies), packs it as `fellowship-desktop` — compositor, shell, `fellowship-msg`, the portal Settings backend, the shared themes and the console session starter — and `live-build` installs it. The extra packages, the session's portals, software rendering, fonts, icons and everyday GTK apps, are listed in [desktop/package-list.chroot](desktop/package-list.chroot).

An installed system boots to the login screen, `fellowship-greeter`, which greetd runs in Cage on tty1; signing in starts the desktop with `fellowship-session`. A live boot goes straight into the desktop as `user` once and returns to the login screen on logout. The other consoles (Ctrl+Alt+F2 and on) stay plain text logins, the way in if the session needs looking at. The session's own output goes to `~/.local/state/fellowship/session.log`. In QEMU:

```bash
qemu-system-x86_64 -enable-kvm -m 4G -smp 2 \
    -drive file=fellowshipos-desktop-amd64-0.1.0.hybrid.iso,media=cdrom,readonly=on \
    -device virtio-gpu-pci -device qemu-xhci -device usb-kbd -device usb-tablet \
    -display gtk
```

The session draws through Mesa's software renderer, so a VM with no 3D acceleration still shows the desktop. On an installed system, logging in on a text console starts the desktop the same way. The installer records no desktop role: the desktop is part of the image rather than a role first boot adds.

`desktop/tests/vm.sh` boots the image in QEMU, waits for the session and writes a picture of the screen (`desktop.png`), failing if the picture is not the desktop. It needs OVMF and `python3` with Pillow.

The rest of `desktop/tests/` answers questions a picture cannot. They all drive the machine `vm-interact.sh` boots — the ISO's own kernel, a serial console socket, a QEMU monitor socket and a 9p share — and log in over serial as `user` / `live`:

| Script | What it settles |
| --- | --- |
| `vm-measure.py` | Responsiveness as numbers: blind `sendkey` taps diffed against `screendump`, before and after the fresh package is hot-swapped |
| `vm-appcheck.py` | Every bundled app opened through the compositor, recording size and decoration mode. `--as-shipped` tests the image exactly as built; otherwise the freshest package is swapped in, which makes a fix committed after the image testable on the seat. A wrong decoration mode is a FAIL, and the strip above each window is checked for the theme's bar colour, so the picture has to agree with the flag |
| `vm-soundcheck.py` | Sound and notifications on a clean seat, including whether `notify-send` activates the service through its installed bus file |
| `vm-notifyd-probe.py` | Why a notification service is not running: cannot activate, starts and crashes, or cannot reach the bus |
| `vm-everyday.py` | The everyday layer measured before paying for it — user directories, the fonts the session actually has, text photographed in eight scripts, and whether Nemo thumbnails without the Xfce thumbnailer |

`desktop/build-only.sh` builds just the desktop package in the trixie container, the same way the full packages step does it, with the toolchain, registry and release target cached on the host: about 2m19s. To test a committed revision without disturbing a working tree, add detached worktrees **side by side** (`build.sh` mounts the desktop checkout at `../fellowship-desktop`) and run `FELLOWSHIP_EDITIONS=desktop ./build.sh` inside the OS one. [The desktop VM records](docs/fellowship-desktop-vm-session-2026-11-14.md) and the desktop repository's `docs/vm-testing.md` carry the results and the harness gotchas.

## Deploy to Proxmox LXC

Copy the template to the Proxmox node:

```bash
scp fellowshipos-trixie-amd64-1.0_lxc.tar.gz root@PROXMOX:/var/lib/vz/template/cache/
```

From a checkout on the Proxmox node, use [create-ct.sh](proxmox/create-ct.sh):

```bash
cd proxmox
./create-ct.sh --flavor base
```

The helper supports `base`, `docker`, `monitor`, and `storage` container presets.

## Develop and test

The [November 14 desktop VM handoff](docs/fellowship-desktop-vm-session-2026-11-14.md) records the current ISO, app additions, cursor investigation, pending compositor input fix, and VM verification steps.

Run the installer's Go suite from the repository root. It needs Go 1.24 or later.

```bash
(cd installer && go test ./...)
```

[CI](.github/workflows/ci.yml) runs it on every push and pull request under the
race detector, checks `gofmt` and `go vet`, type-checks and bundles the
installer page, and runs ShellCheck on the shell scripts.

The QEMU/KVM VM harness that drove the multi-purpose image's installer went to FellowshipOS-NAS with the NAS tests (`tests/vm/vm.sh` there); the desktop image has its own in [desktop/tests/](desktop/tests/).

| Path | Purpose |
| --- | --- |
| [auto/config](auto/config) and [config/](config/) | Live-build profile, boot menu, package lists, first-boot wizard, and role scripts |
| [installer/](installer/) | Graphical installer in Go and TypeScript |
| [PROJECTS.md](PROJECTS.md) | The products, which folders belong to each, and the branch, commit and push rules |
| [docs/](docs/) | [Document index](docs/README.md), [versioning](docs/versioning.md) and the [November 11 VM walkthrough](docs/fellowship-vm-walkthrough-2026-11-11.md) |
| [CHANGELOG.md](CHANGELOG.md) | What changed, and what is waiting to be released |

## Troubleshooting

- **Build failed:** Check `build.log`, Docker, and access to Debian mirrors and GitHub. The script checks that both output files exist before reporting success.
- **First boot or role setup failed:** Check `/var/log/fellowshipos/setup.log`. Use `sudo fellowshipos-setup` to run the wizard manually.

This repository currently has no license file.
