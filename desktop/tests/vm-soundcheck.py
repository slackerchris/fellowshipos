#!/usr/bin/env python3
"""Check sound and notifications on the shipped image, in the seat VM.

    desktop/tests/vm-soundcheck.py [output-dir]

Boots the current desktop ISO, hot-swaps the freshly built
fellowship-desktop .deb (the session restarts on it), and asks the things
the nested tests cannot: does the session really run PipeWire and
WirePlumber, do the volume keys move the real default sink, does the OSD
appear, does `notify-send` activate the notification service through its
installed bus file and put a pop-up on the screen. Prints one PASS/FAIL
line per check and a screenshot through the compositor.
"""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from importlib import import_module
vm_measure = import_module("vm-measure")  # the Serial helper, unchanged

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fellowship-desktop-sound")
    # The freshest desktop package, wherever an image build left it: a
    # running NAS build parks it in .build/desktop-package while it runs,
    # and earlier seat runs keep the copies that were shared into the VM.
    deb = [d for d in ([p for r in (REPO / "config" / "packages.chroot", REPO / ".build" / "desktop-package")
                        for p in r.glob("fellowship-desktop_*.deb")] +
                       [p for t in pathlib.Path("/tmp").glob("fellowship-*/share/pkg")
                        for p in t.glob("fellowship-desktop_*.deb")])
           if d.resolve() != (out / "share" / "pkg" / d.name).resolve()]
    if not deb:
        sys.exit("no fellowship-desktop .deb built; run desktop/build.sh first")
    deb = max(deb, key=lambda p: p.stat().st_mtime)

    # vm-interact.sh only shares what it finds in packages.chroot; put the
    # chosen package where the dpkg swap below reads it, whatever the
    # image build is doing with the original.
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

    print("=== swapping in the fresh package and restarting the session ===")
    ser.run(f"sudo dpkg -i /mnt/pkg/{deb.name} >/tmp/dpkg.log 2>&1; tail -1 /tmp/dpkg.log",
            "DPKG_DONE", timeout=240)
    # The parented bus wrapper must go with the compositor: a leftover
    # dbus-run-session holds the session lock, and the restarted session
    # then half-starts behind it (seen 2026-11-15: the shell never reached
    # the bus, so notifyd was never activated).
    ser.run("pkill -x fellowship-comp; pkill -f dbus-run-session; sleep 2; "
            "sudo systemctl restart getty@tty1.service",
            "SWAPPED", timeout=60)
    ser.wait_session(300)

    # When a check below fails, these lines say whether the swapped-in
    # session was ever the healthy one the same image produces by hand
    # (seen 2026-11-15: gate runs failed with a healthy-by-hand twin;
    # without this evidence the cause stays a guess).
    print("=== the session as the swap left it ===")
    ser.run("date; pgrep -af 'fellowship-comp|fellowship-shell$|fellowship-notifyd' | head -6; "
            "LOG=\"$HOME/.local/state/fellowship/session.log\"; "
            "[ -f \"$LOG\" ] || LOG=/tmp/fellowship-session.log; "
            "grep -ac 'notifications claimed' \"$LOG\"; "
            "ls -l /run/user/1000/fellowship-wayland-*.sock", "SEEN", timeout=90)

    checks = []

    # What the machine actually looks like before the verdicts: the
    # fragments wireplumber should have, the sound processes, and the sink
    # list. When a check below fails, this is what says why.
    print("=== diagnosis ===")
    ser.run("ls /usr/share/wireplumber/wireplumber.conf.d/ 2>&1; pgrep -a pipewire | head -2; "
            "pgrep -a wireplumber; aplay -L 2>/dev/null | grep -A1 ':\\|hdg\\|pcsp' | head -8",
            "DIAG_DONE", timeout=60)
    ser.run("timeout 10 wpctl status 2>&1 | sed -n '/Sinks/,/Sources/p'; "
            "timeout 10 wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>&1",
            "WPCTL_DONE", timeout=60)

    def drain():
        # Throw away whatever the console has already typed but not shown:
        # bytes from a previous check's output, and — after a hanging
        # command — a queued next line the busy shell never ran. Ctrl-C
        # first clears the tty input queue itself, so a stale line cannot
        # execute in front of the check being typed now.
        ser.send("\x03")
        ser.s.settimeout(0.5)
        while True:
            try:
                if not ser.s.recv(65536):
                    break
            except (TimeoutError, OSError):
                break
        ser.s.settimeout(2)
        ser.buf = b""

    def check(name, cmd, marker, timeout=90):
        # The marker is split so the echo of the typed line never contains
        # it, and printed only when the check itself passed: an early match
        # on the echo, or an unconditional one, would make a false PASS.
        # (Serial.run appends its echo unconditionally, so this drives the
        # console directly instead.)
        head, tail = marker[: len(marker) // 2], marker[len(marker) // 2:]
        drain()
        ser.transcript = ""
        ser.send(f"{cmd} && echo '{head}'{tail}")
        ok = ser.read_until(marker, timeout)
        checks.append((name, ok))
        print(f"{name}: {'PASS' if ok else 'FAIL'}")

    # A check whose command contains a hanging tool (wpctl in this guest)
    # leaves the console's tty input queue carrying the NEXT typed line
    # unread while the shell is still busy: queued lines never run in
    # order, and their markers time out as false FAILs. Seen 2026-11-15:
    # notifyd was provably running (pgrep'd right after the swap) yet the
    # notify checks below the sound ones failed. Two defences: notification
    # checks run BEFORE the hanging sound ones, and every call below is
    # wrapped in `timeout`, with any stale bytes drained before typing.
    check(
        "notify-send through the bus file starts the notification service",
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "timeout 15 fellowship-msg --socket $SOCK spawn 'notify-send -t 0 From-the-VM sound-check'; "
        "rm -f /tmp/nd; for i in $(seq 240); do pgrep -f fellowship-notifyd >/dev/null && touch /tmp/nd && break; sleep 0.5; done; "
        "test -e /tmp/nd",
        "NOTIFYD_OK", timeout=180,
    )
    check(
        "a real notify-send reaches the shell as a pop-up",
        "rm -f /tmp/popup; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "for i in $(seq 240); do timeout 5 fellowship-msg --socket $SOCK layers "
        "| grep -q 'fellowship-notify-' && touch /tmp/popup && break; sleep 0.5; done; "
        "test -e /tmp/popup",
        "POPUP_OK", timeout=180,
    )

    check(
        "pipewire and wireplumber run with the session",
        "pgrep -x pipewire >/dev/null && pgrep -x wireplumber >/dev/null",
        "PW_OK",
    )
    check(
        "a default sink exists (null sink in a VM without sound hardware)",
        "timeout 10 wpctl status 2>/dev/null | grep -q Sink && timeout 10 wpctl get-volume @DEFAULT_AUDIO_SINK@ >/dev/null",
        "SINK_OK",
    )
    check(
        "the volume key moves the real sink",
        "before=$(timeout 10 wpctl get-volume @DEFAULT_AUDIO_SINK@); "
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "timeout 10 fellowship-msg --socket $SOCK key volumeup; sleep 1; "
        "after=$(timeout 10 wpctl get-volume @DEFAULT_AUDIO_SINK@); "
        'echo "sink before=$before after=$after"; [ "$before" != "$after" ]',
        "KEY_OK",
    )
    check(
        "the on-screen display appears with the key",
        "rm -f /tmp/osd; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "timeout 10 fellowship-msg --socket $SOCK key volumedown; "
        "for i in $(seq 40); do timeout 5 fellowship-msg --socket $SOCK layers "
        "| grep -q fellowship-osd && touch /tmp/osd && break; sleep 0.05; done; test -e /tmp/osd",
        "OSD_OK", timeout=120,
    )
    ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
            "timeout 60 fellowship-msg --socket $SOCK screenshot /mnt/out/sound.png",
            "SHOT_DONE", timeout=180)

    failed = [name for name, ok in checks if not ok]
    print(f"\nscreenshot: {out / 'share/out/sound.png'}")
    ser.close()
    subprocess.run(["kill", (out / "vm.pid").read_text().strip()], check=False)
    print("VM stopped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
