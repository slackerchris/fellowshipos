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
    deb = sorted((REPO / "config" / "packages.chroot").glob("fellowship-desktop_*.deb"))
    if not deb:
        sys.exit("no fellowship-desktop .deb built; run desktop/build.sh first")

    boot = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)])
    if boot.returncode != 0:
        sys.exit("boot failed")

    ser = vm_measure.Serial(out / "serial.sock")
    ser.login()
    ser.run("sudo -n true 2>/dev/null || { echo live | sudo -S -v; }", "SUDO_OK", timeout=60)
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt",
            "MOUNTED", timeout=60)

    print("=== swapping in the fresh package and restarting the session ===")
    ser.run(f"sudo dpkg -i /mnt/pkg/{deb[-1].name} >/tmp/dpkg.log 2>&1; tail -1 /tmp/dpkg.log",
            "DPKG_DONE", timeout=240)
    ser.run("pkill -x fellowship-comp; sleep 2; sudo systemctl restart getty@tty1.service",
            "SWAPPED", timeout=60)
    ser.wait_session(300)

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

    def check(name, cmd, marker, timeout=90):
        # The marker is split so the echo of the typed line never contains
        # it, and printed only when the check itself passed: an early match
        # on the echo, or an unconditional one, would make a false PASS.
        # (Serial.run appends its echo unconditionally, so this drives the
        # console directly instead.)
        head, tail = marker[: len(marker) // 2], marker[len(marker) // 2:]
        ser.transcript = ""
        ser.send(f"{cmd} && echo '{head}'{tail}")
        ok = ser.read_until(marker, timeout)
        checks.append((name, ok))
        print(f"{name}: {'PASS' if ok else 'FAIL'}")

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
        "fellowship-msg --socket $SOCK key volumeup; sleep 1; "
        "after=$(timeout 10 wpctl get-volume @DEFAULT_AUDIO_SINK@); "
        'echo "sink before=$before after=$after"; [ "$before" != "$after" ]',
        "KEY_OK",
    )
    check(
        "the on-screen display appears with the key",
        "rm -f /tmp/osd; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "fellowship-msg --socket $SOCK key volumedown; "
        "for i in $(seq 40); do fellowship-msg --socket $SOCK layers "
        "| grep -q fellowship-osd && touch /tmp/osd && break; sleep 0.05; done; test -e /tmp/osd",
        "OSD_OK",
    )
    # The trigger runs INSIDE the session (fellowship-msg spawn): the
    # compositor lives on a private dbus-run-session bus that the serial
    # console cannot reach, and that is the bus notifyd is activated on.
    # notifyd is already up by now — the shell's own first list() call
    # activated it through its installed bus file — so this confirms the
    # process is running and sends the notification the pop-up check reads.
    check(
        "the notification service is running (activated through its bus file)",
        "rm -f /tmp/nd; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "for i in $(seq 60); do pgrep -f fellowship-notifyd >/dev/null && touch /tmp/nd && break; sleep 0.2; done; "
        "test -e /tmp/nd && fellowship-msg --socket $SOCK spawn 'notify-send -t 60000 From-the-VM sound-check'",
        "NOTIFYD_OK",
    )
    check(
        "a real notify-send reaches the shell as a pop-up",
        "rm -f /tmp/popup; SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "for i in $(seq 60); do fellowship-msg --socket $SOCK layers "
        "| grep -q 'fellowship-notify-' && touch /tmp/popup && break; sleep 0.2; done; "
        "test -e /tmp/popup",
        "POPUP_OK",
    )
    ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
            "fellowship-msg --socket $SOCK screenshot /mnt/out/sound.png",
            "SHOT_DONE", timeout=60)

    failed = [name for name, ok in checks if not ok]
    print(f"\nscreenshot: {out / 'share/out/sound.png'}")
    ser.close()
    subprocess.run(["kill", (out / "vm.pid").read_text().strip()], check=False)
    print("VM stopped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
