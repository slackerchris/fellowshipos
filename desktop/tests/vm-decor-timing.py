#!/usr/bin/env python3
"""Does a GTK3 app answer the decoration question a moment after mapping?

If it does, the double bar the seat run photographed is a race — our frame
drawn before `set_mode(client_side)` lands — and the geometry guess built
on 2026-11-15 is both unnecessary and wrong, because a dialog that never
answers (zenity --info, which has no headerbar) is exactly the client that
needs our frame.

Runs against the image as shipped (no swap), so the pre-fix compositor is
what answers.

    desktop/tests/vm-decor-timing.py [output-dir]
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


vm_measure = import_module("vm-measure")  # the Serial helper, unchanged

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

APPS = [("mousepad", "mousepad", "mousepad"), ("nemo", "nemo --no-desktop", "nemo"),
        ("zenity", "zenity --info --text=timing", "zenity"), ("foot", "foot", "foot")]


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else test_dir("decor-timing"))
    rc = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)]).returncode
    if rc != 0:
        sys.exit("boot failed")

    ser = vm_measure.Serial(out / "serial.sock")
    ser.login()
    ser.wait_session(300)

    for name, cmd, match in APPS:
        print(f"\n=== {name}: decorated, polled once a second ===")
        ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
                f"fellowship-msg --socket $SOCK spawn {cmd}", "SPAWNED", timeout=30)
        seen = []
        for _ in range(25):
            if ser.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock | head -1); "
                       "fellowship-msg --socket $SOCK windows", "W", timeout=20):
                for blk in re.findall(r"\{[^{}]*\}", ser.transcript):
                    f = dict(re.findall(r'"(\w+)": ("[^"]*"|\w+)', blk))
                    if match in (f.get("app_id", "") + f.get("title", "")).lower():
                        seen.append(f.get("decorated", "?"))
            time.sleep(1)
        print("  " + " ".join(seen))
        first = seen[0] if seen else "-"
        last = seen[-1] if seen else "-"
        print(f"{name}: first={first} last={last} "
              f"{'ANSWERS LATE (race)' if seen and first != last and last == 'false' else ''}")
        ser.run(f"pkill -x {name} 2>/dev/null; pkill -f {name} 2>/dev/null; sleep 1", "K", timeout=30)

    ser.close()
    subprocess.run(["kill", (out / "vm.pid").read_text().strip()], check=False)
    print("\nVM stopped")


if __name__ == "__main__":
    main()
