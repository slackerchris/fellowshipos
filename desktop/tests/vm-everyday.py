#!/usr/bin/env python3
"""Measure the everyday gaps on the seat, and prove the fix from the login.

    desktop/tests/vm-everyday.py [--fix] [output-dir]

Three things the checklist says a person notices the moment they use the
desktop, each measured rather than assumed:

1. **The user folders.** `xdg-user-dirs` is the only thing that creates
   Desktop/Downloads/Pictures and writes `~/.config/user-dirs.dirs`; the
   compositor's screenshot path reads that file and falls back to
   `~/Pictures`. Does either exist on a clean seat?
2. **Text that is not ASCII.** The image ships one real font family, DejaVu.
   Measured on the seat: Arabic renders (DejaVu covers it), CJK is tofu and
   emoji has no colour glyphs. This photographs a dialog full of them, which
   is the only way to see it.
3. **Thumbnails.** Nemo already pulls `shared-mime-info`,
   `libgdk-pixbuf-2.0-0` and `gvfs` as hard dependencies, so the everyday
   image-thumbnail path may already work without the 132-package Xfce
   `tumbler` stack. The honest test is Nemo's own output: browse a folder
   holding a real picture and look for the file it writes under
   `~/.cache/thumbnails`.

`--fix` installs the small candidates (xdg-user-dirs, the Noto fonts),
hot-swaps the freshest desktop package — which is what carries the session
hook that runs `xdg-user-dirs-update` at login, since the session reads
only the person's autostart folder and Debian's system entry never runs —
and restarts the console so the login itself is what creates the folders.
The folders check therefore measures the shipped mechanism, not the command
typed by hand.

Needs network in the guest for `--fix`; the checks themselves need none.
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

FIX = "--fix" in sys.argv[1:]
dirs = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = pathlib.Path(dirs[0] if dirs else test_dir("everyday"))

# A picture for the thumbnail test, made here so the guest only has to copy
# it: 240x160 of hard-edged colour, which no thumbnailer can produce by
# accident.
def make_test_image():
    from PIL import Image
    (OUT / "share").mkdir(parents=True, exist_ok=True)
    path = OUT / "share" / "thumbme.png"
    img = Image.new("RGB", (240, 160))
    for y in range(160):
        for x in range(240):
            img.putpixel((x, y), ((x * 3) & 255, (y * 5) & 255, (x + y) & 255))
    img.save(path)
    return path


def ask(ser, name, cmd, marker, timeout=120):
    print(f"\n=== {name} ===")
    ser.run(cmd, marker, timeout=timeout)


def main():
    pidfile = OUT / "vm.pid"
    if pidfile.exists():
        subprocess.run(["kill", pidfile.read_text().strip()], check=False)
        subprocess.run(["sleep", "3"], check=False)
    rc = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(OUT)]).returncode
    if rc != 0:
        sys.exit("boot failed")

    img = make_test_image()
    ser = vm_measure.Serial(OUT / "serial.sock")
    ser.login()
    ser.run("sudo -n true 2>/dev/null || { echo live | sudo -S -v; }", "SUDO_OK", timeout=60)
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt",
            "MOUNTED", timeout=60)
    ser.wait_session(300)

    if FIX:
        print("\n=== installing the small candidates ===")
        ser.run("sudo apt-get install -y xdg-user-dirs fonts-noto-core fonts-noto-color-emoji "
                ">/tmp/apt.log 2>&1; grep -E 'newly installed|E:' /tmp/apt.log | tail -2",
                "APT_DONE", timeout=900)
        # The session hook that runs the updater ships in the desktop
        # package, so swap it in and log in again: the folders must appear
        # because the login made them, not because this script asked.
        print("\n=== swapping in the package with the hook, and logging in again ===")
        if ser.run("ls /mnt/pkg/fellowship-desktop_*.deb", "DEBPRESENT", timeout=30):
            ser.run("sudo dpkg -i /mnt/pkg/fellowship-desktop_1.0_amd64.deb >/tmp/dpkg.log 2>&1; "
                    "tail -1 /tmp/dpkg.log", "DPKG_DONE", timeout=240)
            ser.run("rm -rf ~/.config/user-dirs.dirs; pkill -x fellowship-comp; "
                    "pkill -f dbus-run-session; sleep 2; sudo systemctl restart getty@tty1.service",
                    "RELAUNCHED", timeout=60)
            ser.wait_session(300)
        else:
            print("no shared package: the folders check will not see the hook")

    ask(ser, "1. the user folders",
        "ls -d ~/Desktop ~/Downloads ~/Pictures ~/Documents ~/Music ~/Videos 2>&1 | head -8; "
        "echo ---; cat ~/.config/user-dirs.dirs 2>&1 | head -4; echo ---; "
        "command -v xdg-user-dirs-update || echo NO_UPDATER",
        "DIRSDONE")

    ask(ser, "2. what fonts the session actually has",
        "fc-list | wc -l; echo families:; fc-list : family | tr ',' '\\n' | sort -u | head -12; "
        "echo ---; fc-list | grep -ci 'noto\\|emoji' || echo NO_NOTO_OR_EMOJI",
        "FONTDONE")

    # The dialog is photographed through the compositor, because tofu only
    # shows up in pixels: the text is present in every case.
    ask(ser, "3. a dialog full of non-ASCII text",
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "fellowship-msg --socket $SOCK spawn "
        "'zenity --info --text=emoji😀_chinese中文_japanese日本語_korean한국어_"
        "devanagariहिन्दी_thaiไทย_tamilதமிழ_arabicالعربية_done'",
        "SPAWNDONE", timeout=60)
    subprocess.run(["sleep", "6"], check=False)
    ask(ser, "3b. and its picture",
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "mkdir -p /mnt/out; timeout 60 fellowship-msg --socket $SOCK screenshot /mnt/out/everyday.png",
        "SHOTDONE", timeout=120)
    ser.run("pkill zenity 2>/dev/null; sleep 1", "ZAPDONE", timeout=30)

    ask(ser, "4. thumbnails, without the tumbler stack",
        "mkdir -p ~/Pictures && cp /mnt/thumbme.png ~/Pictures/ && "
        "rm -rf ~/.cache/thumbnails; pgrep -x tumbler >/dev/null && echo TUMBLER_RUNNING || echo NO_TUMBLER; "
        "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
        "fellowship-msg --socket $SOCK spawn 'nemo ~/Pictures'", "NMSPAWNDONE", timeout=60)
    subprocess.run(["sleep", "12"], check=False)
    ask(ser, "4b. what Nemo wrote",
        "find ~/.cache/thumbnails -type f 2>/dev/null | head -4; "
        "find ~/.cache/thumbnails -name '*.png' 2>/dev/null | grep -q . "
        "&& echo THUMBNAIL_MADE || echo NO_THUMBNAIL; pkill nemo 2>/dev/null; sleep 1",
        "THUMBDONE", timeout=60)

    print(f"\npicture: {OUT / 'share/out/everyday.png'}")
    ser.close()
    subprocess.run(["kill", pidfile.read_text().strip()], check=False)
    print("VM stopped")


if __name__ == "__main__":
    main()
