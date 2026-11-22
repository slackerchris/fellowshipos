# FellowshipOS Desktop 0.4.3 — release candidate 1

Built 2026-11-22 with `FELLOWSHIP_EDITIONS=desktop ./build.sh`, from clean
checkouts. The first version published to the update feed.

| | |
| --- | --- |
| ISO | `fellowshipos-desktop-amd64-0.4.3.hybrid.iso`, 1,721,997,312 bytes |
| SHA-256 | `086ddc23ebc305ba6951579a7cb8e2a8608f920f0ede9185855a2146c2e88e75` |
| fellowshipos | `14599b1` (build: 0.4.3, the release candidate with the VM run's fixes) |
| fellowship-desktop | `f2a5be3` (net, settings: fixes from the RC1 VM run) |
| fellowship-desktop package | `fellowship-desktop_0.4.3_amd64.deb`, published to the feed |
| Packages | [fellowshipos-desktop-0.4.3-packages.txt](fellowshipos-desktop-0.4.3-packages.txt), 1003 lines |

Scope (Chris): a VM, wired or Wi-Fi, one display; restart and shut down from
the power menu; firewall and SSH switches in Settings. What it leaves out is
in [../versioning.md](../versioning.md).

## What was tested, and on what

Tested in QEMU on **0.4.2** (fellowshipos `6c82b0b`, fellowship-desktop
`3cd2e60`), installed unattended on a blank UEFI disk through the remote
installer (`desktop/tests/vm-install-drive.sh`), then driven over QMP:

- Remote install with the firewall running: passed (the ufw fix, `b989fee`).
- Greeter, sign-in, the desktop with the network icon showing wired: passed.
- Settings → Firewall and remote login: read without a password; turning SSH
  on asked for the administrator's password, started ssh and allowed OpenSSH
  through ufw, and showed the ssh line to connect with: passed.
- The update source added on first boot, its stamp written: passed.
  `apt update` failed on the feed, which had nothing published yet; 0.4.3 is
  published with this build.
- Wi-Fi with `mac80211_hwsim` and a hostapd WPA2 access point: the network
  appeared in the top bar's popover; a wrong password failed and wasn't
  kept; the right one was accepted. Two faults found, fixed in 0.4.3.

**Not tested on 0.4.3** (Chris, 2026-11-22: skip testing). Changed since the
run and never run on a real system:

- Wi-Fi keeps NetworkManager's last real failure reason (a wrong password
  now says so).
- Wi-Fi waits 90 s and deletes a new profile only when the password was
  refused; a slow network's profile is kept.
- Only a radio NetworkManager manages is used.
- The SSH page leaves out container and VM bridge addresses.

Look there first if Wi-Fi or the SSH page misbehaves on real hardware.

Noticed, not changed: a new account's dock holds only the tree; the image
carries Docker from the shared config; `cat` is aliased to `bat`; the greeter
says "Unlock" for a listed account.
