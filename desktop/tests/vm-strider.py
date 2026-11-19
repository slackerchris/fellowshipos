#!/usr/bin/env python3
"""Strider against a real FellowshipNAS: the desktop image and an installed
NAS in two VMs on a private network segment, for what a nested session on
the build machine can't show: SMB shares through GVfs, a share's password,
finding the NAS by its Avahi advert, and USB drives.

    desktop/tests/vm-strider.py boot          # boot the desktop VM, log in
    desktop/tests/vm-strider.py sh 'command'  # run a command in the guest
    desktop/tests/vm-strider.py usb add|remove
    desktop/tests/vm-strider.py test          # strider-vm-test.sh's checks

The NAS VM comes from nas/tests/vm/vm.sh (install, then `boot` with the
same private card: see LAN below); give its private card 10.77.0.1/24 on
its console. The segment is QEMU's socket multicast bound to 127.0.0.1, so
nothing reaches the real network. The desktop gets 10.77.0.2/24 there and
keeps its user-mode card for the internet.

Guest commands run as the live user in its session, with
strider-guest.sh's helpers sourced; /mnt/out in the guest is
$FELLOWSHIP_OUT/share/out here. FELLOWSHIP_OUT defaults to
~/.cache/fellowship-strider-desktop (not /tmp: the USB stick's image and the
screenshots add up).
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from importlib import import_module  # noqa: E402

measure = import_module("vm-measure")

OUT = pathlib.Path(os.environ.get("FELLOWSHIP_OUT", pathlib.Path.home() / ".cache/fellowship-strider-desktop"))
LAN = "-netdev socket,id=lan,mcast=230.0.77.1:17701,localaddr=127.0.0.1 -device virtio-net-pci,netdev=lan,mac=52:54:00:77:00:02"
ISO = os.environ.get("FELLOWSHIP_ISO", str(HERE.parent.parent / "fellowshipos-desktop-amd64-0.2.3.hybrid.iso"))
STICK = OUT / "stick.img"


def serial():
    return measure.Serial(OUT / "serial.sock")


class Monitor:
    """The QEMU monitor, plainly: vm-measure's taps Super on connecting."""

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(5)
        self.s.connect(str(path))
        self.read()

    def read(self):
        out = b""
        end = time.time() + 10
        while time.time() < end and b"(qemu)" not in out:
            try:
                out += self.s.recv(65536)
            except socket.timeout:
                break
        return out.decode(errors="replace")

    def cmd(self, line):
        self.s.sendall((line + "\n").encode())
        # The monitor echoes what it's sent; only its last line matters.
        return self.read().replace("\x1b[K", "").splitlines()[-2:-1] or [""]


def monitor():
    return Monitor(OUT / "monitor.sock")


def boot():
    (OUT / "share/out").mkdir(parents=True, exist_ok=True)
    for script in ("strider-guest.sh", "strider-vm-test.sh"):
        shutil.copy(HERE / script, OUT / "share" / script)
    env = dict(os.environ, FELLOWSHIP_ISO=ISO, FELLOWSHIP_QEMU_EXTRA=LAN + " -device qemu-xhci,id=usbbus")
    subprocess.run([str(HERE / "vm-interact.sh"), str(OUT)], env=env, check=True)
    ser = serial()
    ser.login()
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt; test -e /mnt/strider-guest.sh && echo share-ok", "SHARE_UP", timeout=60)
    ser.run("for i in /sys/class/net/*; do [ \"$(cat $i/address)\" = 52:54:00:77:00:02 ] && sudo ip link set ${i##*/} up && sudo ip addr add 10.77.0.2/24 dev ${i##*/}; done; ip -br addr", "LAN_UP", timeout=30)
    ser.wait_session(240)
    ser.run("ping -c1 -W2 10.77.0.1 >/dev/null && echo nas-reachable || echo NAS-NOT-REACHABLE", "PING_DONE", timeout=20)
    print()


def sh(cmd, timeout=300):
    ser = serial()
    # The serial login keeps its shell between calls; each call re-sources
    # the helpers so they see the session socket as it is now.
    ser.run(f". /mnt/strider-guest.sh; {cmd}", "GUEST_DONE", timeout=timeout)
    print()
    return ser.transcript


def test():
    for script in ("strider-guest.sh", "strider-vm-test.sh"):
        shutil.copy(HERE / script, OUT / "share" / script)
    out = sh(". /mnt/strider-vm-test.sh; phase_net", 400)
    if "FAILED" not in out:
        usb("add")
        time.sleep(4)
        out += sh(". /mnt/strider-vm-test.sh; phase_usb", 200)
    import re
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out)
    lines = [l.strip() for l in clean.splitlines() if l.strip().startswith(("ok:", "FAILED", "NOT-LOGGED", "TITLE-MISMATCH"))]
    print("\n".join(lines))
    sys.exit(0 if lines and not any(not l.startswith("ok:") for l in lines) else 1)


def usb(action):
    mon = monitor()
    if action == "add":
        if not STICK.exists():
            # A 64 MB stick with a file on it, made without root: mkfs.ext4
            # fills it from a folder.
            files = OUT / "stick-files"
            files.mkdir(exist_ok=True)
            (files / "note.txt").write_text("on the stick\n")
            subprocess.run(["truncate", "-s", "64M", str(STICK)], check=True)
            subprocess.run(["mkfs.ext4", "-q", "-L", "STRIDERUSB", "-d", str(files), str(STICK)], check=True)
        print(mon.cmd(f"drive_add 0 if=none,id=stick,format=raw,file={STICK}"))
        print(mon.cmd("device_add usb-storage,bus=usbbus.0,drive=stick,id=stickdev,removable=on"))
    else:
        print(mon.cmd("device_del stickdev"))
        time.sleep(2)
        print(mon.cmd("drive_del stick"))


def main():
    match sys.argv[1:]:
        case ["boot"]:
            boot()
        case ["sh", cmd]:
            sh(cmd)
        case ["sh", cmd, timeout]:
            sh(cmd, int(timeout))
        case ["test"]:
            test()
        case ["usb", ("add" | "remove") as action]:
            usb(action)
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
