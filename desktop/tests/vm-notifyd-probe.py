#!/usr/bin/env python3
"""Pin why fellowship-notifyd does not run on the seat VM.

    desktop/tests/vm-notifyd-probe.py [output-dir]

The nested tests start the daemon explicitly; the VM must activate it
through the installed bus file on the session's private
dbus-run-session bus. The three candidate causes — cannot activate,
starts and crashes, cannot reach the bus — answer different questions,
so this probe separates them in order: the binary and bus files exist,
the private bus is reachable through the compositor's own environment
(nsenter), StartService-by-hand either works or prints dbus-daemon's
error, and a real notify-send through the compositor either brings the
pop-up layer or leaves the reason on the console.

Boots the seat VM (after replacing any guest from an earlier run of this
script), hot-swaps the freshest fellowship-desktop .deb, restarts the
session, then runs the probe. Reads evidence; changes nothing.
"""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from importlib import import_module
import os


def test_dir(name):
    """Where a run keeps its output: on disk, not /tmp, which is RAM on the
    build machine (it filled on 2026-11-22). FELLOWSHIP_TEST_DIR overrides."""
    base = os.environ.get("FELLOWSHIP_TEST_DIR") or os.path.expanduser("~/.cache/fellowship-tests")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, name)


vm_measure = import_module("vm-measure")  # the Serial helper, unchanged

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def main():
    args = sys.argv[1:]
    no_swap = "--no-swap" in args
    dirs = [a for a in args if not a.startswith("--")]
    out = pathlib.Path(dirs[0] if dirs
                       else test_dir("notifyd-probe"))
    # The freshest desktop package, wherever the image build left it: a
    # running NAS build parks it in .build/desktop-package; VM workdirs
    # from earlier seat runs keep the copies that were shared in.
    candidates = [d for d in ([p for r in (REPO / "config" / "packages.chroot", REPO / ".build" / "desktop-package")
                               for p in r.glob("fellowship-desktop_*.deb")] +
                              [p for t in pathlib.Path(test_dir("")).glob("*/share/pkg")
                               for p in t.glob("fellowship-desktop_*.deb")])
                  if d.resolve() != (out / "share" / "pkg" / d.name).resolve()]
    if not candidates and not no_swap:
        sys.exit("no fellowship-desktop .deb built; run desktop/build.sh first")
    deb = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    # vm-interact.sh only shares what it can find in packages.chroot; the
    # probe puts the package it chose itself, wherever the build left it.
    if deb:
        (out / "share" / "pkg").mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-f", str(deb), str(out / "share" / "pkg" / deb.name)], check=True)

    boot = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)])
    if boot.returncode != 0:
        sys.exit("boot failed")

    ser = vm_measure.Serial(out / "serial.sock")
    ser.login()
    ser.run("sudo -n true 2>/dev/null || { echo live | sudo -S -v; }", "SUDO_OK", timeout=60)
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt",
            "MOUNTED", timeout=60)

    if no_swap:
        print("=== reading the session as the image ships it (no swap) ===")
        ser.wait_session(300)
    else:
        print("=== swapping in the fresh package and restarting the session ===")
        ser.run(f"sudo dpkg -i /mnt/pkg/{deb.name} >/tmp/dpkg.log 2>&1; tail -1 /tmp/dpkg.log",
                "DPKG_DONE", timeout=240)
        ser.run("pkill -x fellowship-comp; pkill -f dbus-run-session; sleep 2; "
                "sudo systemctl restart getty@tty1.service", "SWAPPED", timeout=60)
        ser.wait_session(300)

    def ask(name, cmd, marker, timeout=90):
        print(f"\n=== {name} ===")
        ser.run(cmd, marker, timeout=timeout)

    ask("the binary is where the bus file says it is",
        "ls -l /usr/bin/fellowship-notifyd; test -x /usr/bin/fellowship-notifyd "
        "&& echo BINARY_IN_PLACE", "A1")
    ask("the bus files are installed and readable",
        "for f in /usr/share/dbus-1/services/org.freedesktop.Notifications.service "
        "/usr/share/dbus-1/services/org.fellowship.Notifications.service; do "
        "echo \"== $f\"; cat \"$f\" 2>&1; done", "A2")
    ask("the session really runs on a private bus",
        "pgrep -af 'dbus-run-session|dbus-daemon' | head -6", "A3")
    ask("what the session log says about notifyd (if anything)",
        "LOG=\"$HOME/.local/state/fellowship/session.log\"; "
        "[ -f \"$LOG\" ] || LOG=/tmp/fellowship-session.log; "
        "grep -i notify \"$LOG\" 2>/dev/null | tail -10; "
        "tail -5 \"$LOG\"", "A4")

    # The bus is the compositor's own; reach it through that process's
    # environment (the live user is a member of its user namespace).
    env_cmd = ("COMP=$(pgrep -x fellowship-comp | head -1); "
               "sudo -n nsenter -t $COMP -m -U --preserve-credentials -e -- sh -c '{CMD}' 2>&1")
    ask("what the session bus address is",
        env_cmd.replace("{CMD}", "echo $DBUS_SESSION_BUS_ADDRESS"), "A5")
    ask("does dbus-daemon know the name at all (no such service = bus file missing)",
        env_cmd.replace("{CMD}", "timeout 15 gdbus call --session --dest org.freedesktop.DBus "
        "--object-path /org/freedesktop/DBus --method org.freedesktop.DBus.StartService "
        "org.freedesktop.Notifications"), "A6", timeout=60)
    ask("is the name on the bus after that StartService",
        env_cmd.replace("{CMD}", "timeout 15 gdbus call --session --dest org.freedesktop.DBus "
        "--object-path /org/freedesktop/DBus --method org.freedesktop.DBus.ListNames "
        "| tr ',' '\\n' | grep -i Notification"), "A7")
    ask("did the daemon survive a manual start",
        "sleep 1; pgrep -af fellowship-notifyd || echo NOTIFYD_NOT_RUNNING", "A8")

    # The way an app really triggers it: through the compositor's spawn.
    ask("a real notify-send through the compositor",
        "rm -f /tmp/ndspawn; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "fellowship-msg --socket $SOCK spawn 'notify-send -t 60000 Probe-from-the-VM notifyd-activation' "
        "&& touch /tmp/ndspawn; sleep 2; pgrep -af fellowship-notifyd || echo STILL_NOT_RUNNING",
        "A9", timeout=60)
    ask("did a pop-up layer appear",
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "fellowship-msg --socket $SOCK layers | grep -c 'fellowship-notify-' "
        "|| echo NO_POPUP_LAYER", "A10")

    ser.close()
    print("\nprobe done; VM left running — serial: " + str(out / "serial.sock"))


if __name__ == "__main__":
    main()
