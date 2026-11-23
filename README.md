# FellowshipOS

This repository builds **FellowshipOS Multi-purpose**: a Debian Trixie live ISO and a Proxmox LXC template for amd64 homelab systems. **FellowshipNAS**, the NAS appliance with its browser desktop, became its own project on 2026-11-20 and is built from the FellowshipOS-NAS repository (`~/Git/FellowshipOS-NAS`). **FellowshipOS Desktop**, the distro with the Fellowship desktop, followed on 2026-11-23 and is built from the fellowship-desktop repository (`~/Git/fellowship-desktop`, `image/build.sh`). [PROJECTS.md](PROJECTS.md) is the map.

[Build](#build) · [Deploy to Proxmox LXC](#deploy-to-proxmox-lxc) · [Develop and test](#develop-and-test)

## Build

On a Linux host with Docker, internet access, and at least 10 GB of free space, run this from the repository root:

```bash
./build.sh
```

| Output | Use |
| --- | --- |
| `fellowshipos-multi-amd64-<version>.hybrid.iso` | Install a general FellowshipOS machine, or try it live |
| `fellowshipos-trixie-amd64-<version>_lxc.tar.gz` | Create a general-purpose Proxmox container |

`auto/config` gives the ISO its name and boot menu, and the boot menu tells the graphical installer which image it is. The build packages the graphical installer, runs Debian `live-build` in Docker, and packages the LXC root filesystem from the image's chroot. See `build.log` for build and packaging errors. The release version lives in the [VERSION](VERSION) file — prerelease `0.major.minor`, see [docs/versioning.md](docs/versioning.md). `build.sh` names the ISO and template from it, and the package builds stamp their changelogs from it. If it changes, update the expected filename in [create-ct.sh](proxmox/create-ct.sh) or pass `FELLOWSHIP_TEMPLATE`.

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

Run the installer's Go suite from the repository root. It needs Go 1.24 or later.

```bash
(cd installer && go test ./...)
```

[CI](.github/workflows/ci.yml) runs it on every push and pull request under the
race detector, checks `gofmt` and `go vet`, type-checks and bundles the
installer page, and runs ShellCheck on the shell scripts.

The QEMU/KVM VM harness that drove the multi-purpose image's installer went to FellowshipOS-NAS with the NAS tests (`tests/vm/vm.sh` there); the Desktop image's went to fellowship-desktop (`image/desktop/tests/` there).

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
