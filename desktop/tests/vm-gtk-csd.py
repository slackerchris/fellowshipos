#!/usr/bin/env python3
"""When do silent GTK clients settle their decoration mode, and does
GTK_CSD=0 change that?

Two questions the seat photographs raised and the compositor's own answers
have to settle:

1. A silent GTK3 client that draws its own titlebar shows two bars. Does it
   answer `set_mode(client_side)` a moment after mapping — in which case the
   double bar is a race and the fix is to hold our frame back until the
   answer arrives — or does it never answer at all?
2. GTK's own switch, `GTK_CSD=0`, is the standard way to make a GTK client
   stop drawing decorations and ask the compositor for them: the path Kate
   already takes. Does it work here, and what does it do to a GTK4 dialog
   that draws no titlebar (zenity --info), which must keep our frame?

Runs on the image as shipped: the compositor's geometry guess was reverted,
and what is being measured is the client's behaviour.

Markers are multi-character because Serial.run splits them to avoid matching
the tty echo of the typed line — a one-character marker self-matches and
returns a transcript holding nothing but that echo.

    desktop/tests/vm-gtk-csd.py [output-dir]
"""
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from importlib import import_module
import os


def test_dir(name):
    """Where a run keeps its output: on disk, not /tmp, which is RAM on the
    build machine (it filled on 2026-11-22). FELLOWSHIP_TEST_DIR overrides."""
    base = os.environ.get("FELLOWSHIP_TEST_DIR") or os.path.expanduser("~/.cache/fellowship-tests")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, name)


vm_measure = import_module("vm-measure")

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

CASES = [
    ("mousepad plain", "mousepad", "mousepad"),
    ("mousepad CSD-off", "env GTK_CSD=0 mousepad", "mousepad"),
    ("nemo plain", "nemo --no-desktop", "nemo"),
    ("nemo CSD-off", "env GTK_CSD=0 nemo --no-desktop", "nemo"),
    ("zenity plain", "zenity --info --text=csd", "zenity"),
    ("zenity CSD-off", "env GTK_CSD=0 zenity --info --text=csd", "zenity"),
]

SAMPLES = 12


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else test_dir("gtk-csd"))
    pidfile = out / "vm.pid"
    if pidfile.exists():
        subprocess.run(["kill", pidfile.read_text().strip()], check=False)
        time.sleep(3)
    rc = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)]).returncode
    if rc != 0:
        sys.exit("boot failed")

    ser = vm_measure.Serial(out / "serial.sock")
    ser.login()
    ser.wait_session(300)

    rows = []
    for label, cmd, match in CASES:
        print(f"\n=== {label}: {cmd} ===")
        ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
                f"fellowship-msg --socket $SOCK spawn '{cmd}'", "SPAWNEDOK", timeout=30)
        seen = []
        for _ in range(SAMPLES):
            time.sleep(1.5)
            if ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
                       "timeout 5 fellowship-msg --socket $SOCK windows", "WINDOWLIST", timeout=25):
                for blk in re.findall(r"\{[^{}]*\}", ser.transcript):
                    f = dict(re.findall(r'"(\w+)": ("[^"]*"|\w+)', blk))
                    app = (f.get("app_id", "") + " " + f.get("title", "")).replace('"', "").lower()
                    if match in app and int(f.get("width", 0) or 0) > 0:
                        seen.append((f.get("decorated"), f.get("width") + "x" + f.get("height")))
        if not seen:
            print(f"{label:18s} no window seen in {SAMPLES} polls")
            rows.append((label, "-", "-"))
            continue
        first, last = seen[0], seen[-1]
        print(f"{label:18s} polls={len(seen)} first={first[0]} {first[1]} "
              f"last={last[0]} {last[1]} sequence={''.join('T' if d == 'true' else 'F' for d, _ in seen)}")
        rows.append((label, last[0], last[1]))
        ser.run(f"pkill -f {match} 2>/dev/null; sleep 2", "KILLEDOK", timeout=30)

    print("\n=== plain vs GTK_CSD=0 ===")
    by_app = {}
    for label, dec, size in rows:
        by_app.setdefault(label.split()[0], {})[label] = (dec, size)
    for app, seen in by_app.items():
        plain = seen.get(app + " plain", ("?", "?"))
        off = seen.get(app + " CSD-off", ("?", "?"))
        print(f"{app:10s} plain={plain[0]} {plain[1]:>9s}   CSD-off={off[0]} {off[1]:>9s}   "
              f"{'CHANGED' if plain[0] != off[0] else 'unchanged'}")

    ser.close()
    subprocess.run(["kill", pidfile.read_text().strip()], check=False)
    print("VM stopped")


if __name__ == "__main__":
    main()
