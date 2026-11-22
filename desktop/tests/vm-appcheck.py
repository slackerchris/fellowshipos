#!/usr/bin/env python3
"""Launch every bundled app in the desktop VM and report pass/fail.

    desktop/tests/vm-appcheck.py [--as-shipped] [output-dir]

Boots the current desktop ISO with vm-interact.sh, waits for the session,
hot-swaps in the newest desktop package from the host if one exists (so a
fix committed after the image was built is what actually gets tested),
then for each app: spawns it through the compositor (so it gets the
session's environment, the way the launcher would), polls `fellowship-msg
windows` for its window, photographs the screen through the QEMU monitor,
records the window's size and decoration mode, and kills it before the
next one. Chromium gets a longer allowance and a crop of its own title-bar
area — that is where the double-bar question is answered.

`--as-shipped` skips the swap: it tests the image exactly as built, which
is the control half of any change that is not in an image yet — the pair
of runs is what separates a fix that worked from a change that moved
something else as well.

Pass means the window appeared *and*, for the apps in DECOR, the frame is
whosever the desktop promises it is: that column is how the decoration
rules for the silent clients (GTK 4 drawing its own, foot and mpv taking
ours) are measured on the seat rather than assumed.

Results are one line per app on stdout, and the pictures land in
<output-dir>/shots/. Exits non-zero if any app failed to show a window or
landed in the wrong decoration mode.

FELLOWSHIP_ISO picks the image; the app list and its matchers live in APPS
below — a matcher is a substring of the lower-cased app_id or title.
"""
import os
import pathlib
import re
import socket
import subprocess
import sys
import time


def test_dir(name):
    """Where a run keeps its output: on disk, not /tmp, which is RAM on the
    build machine (it filled on 2026-11-22). FELLOWSHIP_TEST_DIR overrides."""
    base = os.environ.get("FELLOWSHIP_TEST_DIR") or os.path.expanduser("~/.cache/fellowship-tests")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, name)

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# The bar's height in logical pixels, as `fellowship-comp` draws it, and the
# cached colour of its fill. The seat runs at scale 1, so logical is physical.
BAR_H = 34
_RAISED = None

APPS = [
    # name            spawn command                                        matchers                pkill      budget
    ("foot",          "foot",                                              ["foot"],               "foot", 40),
    ("mousepad",      "mousepad",                                          ["mousepad"],           "mousepad", 40),
    ("nemo",          "nemo --no-desktop",                                 ["nemo", "files"],      "nemo", 40),
    ("ristretto",     "ristretto",                                         ["ristretto"],          "ristretto", 40),
    ("atril",         "atril",                                              ["atril", "document"], "atril", 60),
    ("engrampa",      "engrampa",                                          ["engrampa", "archive"], "engrampa", 60),
    ("qalculate",     "qalculate-gtk",                                     ["qalculate", "calculator"], "qalculate-gtk", 60),
    ("mpv",           "mpv --force-window --idle",                         ["mpv"],                "mpv", 40),
    ("rhythmbox",     "rhythmbox",                                         ["rhythmbox", "music"], "rhythmbox", 90),
    ("chromium",      "chromium --no-first-run --no-default-browser-check", ["chromium", "chrom"], "chromium", 120),
    # The silent crowd, which the 2026-11-15 control run split in two, both
    # halves measured on the seat: zenity --info never asks and draws NO
    # titlebar (its bold "Information" is dialog content), so our frame is
    # its only one — the 2026-11-14 note mistook that label for a client
    # headerbar, and the decoration guess built on it stripped the dialog's
    # only bar. mousepad, nemo, ristretto, atril, engrampa, qalculate and
    # rhythmbox are silent in the same way but DO draw their own titlebar,
    # so those seven really are double-barred and stay open: nothing in the
    # protocol, the surface geometry, or GTK_CSD=0 separates the two halves
    # (docs/title-bars.md carries the measurements).
    ("zenity",        "zenity --info --text=appcheck",                     ["zenity", "information"], "zenity", 40),
]

# Who draws each window's frame, where that is a promise rather than an
# observation: True is our Fellowship bar, False is the app's own. Anything
# not listed opens a window, and its mode is recorded but not asserted —
# nobody has tested those and this table does not invent expectations.
#
# The seven `False` rows for silent GTK clients are promises the shipped
# rules file makes (crates/fellowship-comp/data/decorations), each measured
# on this seat before it was written down: those apps draw their own titlebar
# and never ask, so a second bar above them is the defect and one bar is the
# requirement. zenity is the opposite case and stays ours.
DECOR = {
    "foot": True,          # silent, plain edges: ours
    "mpv": True,           # silent, plain edges: ours
    "zenity": True,        # silent, draws NO titlebar of its own: ours is the only one
    "chromium": False,     # asks for ClientSide, and is answered in kind
    "mousepad": False,     # silent, draws its own titlebar: named in the rules
    "nemo": False,
    "ristretto": False,
    "atril": False,
    "engrampa": False,
    "qalculate": False,
    "rhythmbox": False,
}


def main():
    args = [a for a in sys.argv[1:]]
    as_shipped = "--as-shipped" in args
    dirs = [a for a in args if not a.startswith("--")]
    out = pathlib.Path(dirs[0] if dirs else test_dir("desktop-appcheck"))
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for old in shots.glob("app-*"):
        old.unlink()

    # Stage the package into the share before the VM boots: the 9p export is
    # fixed at boot, so anything copied in later is invisible to the guest.
    deb = None if as_shipped else find_fresh_package()
    if deb:
        (out / "share" / "pkg").mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-f", str(deb), str(out / "share" / "pkg" / deb.name)], check=True)

    boot = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)])
    if boot.returncode != 0:
        sys.exit("boot failed")

    ser = Serial(out / "serial.sock")
    ser.login()
    wait_session(ser)
    swap_fresh_package(ser, deb)

    mon = Monitor(out / "monitor.sock")
    results = []
    for name, cmd, matchers, proc, budget in APPS:
        print(f"\n=== {name}: {cmd} ===")
        ser.run(f"fellowship-msg --socket $SOCK spawn {cmd}", "SPAWNED", timeout=30)
        win = poll_window(ser, matchers, budget)
        shot = shot_screen(mon, shots / f"app-{name}.ppm")
        expect = DECOR.get(name, "?")
        ok = False
        if win:
            low = (win["app_id"] + " " + win["title"]).lower()
            matched = next((m for m in matchers if m in low), "?")
            wrong = expect != "?" and win["decorated"] != expect
            # The flag says who drew the frame; the picture says how many
            # bars are on screen. Both must agree, and both must match the
            # promise: a window whose frame is ours shows our bar above its
            # content, and one that decorates itself shows none.
            bar = bar_above(shot, win)
            pixel_wrong = bar is not None and bar != win["decorated"]
            ok = not wrong and not pixel_wrong
            who = "ours" if win["decorated"] else "its own"
            seen = "no picture" if bar is None else ("bar above" if bar else "no bar above")
            said = "" if expect == "?" else f" (expected {'ours' if expect else 'its own'})"
            print(f"{name}: {'PASS' if ok else 'FAIL'} app_id={win['app_id']!r} "
                  f"title={win['title']!r} {win['width']}x{win['height']} "
                  f"decorated={win['decorated']} [{who}] {said} {seen} matched={matched}")
            if name == "chromium":
                crop_titlebar(shot, win, shots / "app-chromium-titlebar.png")
        else:
            print(f"{name}: FAIL no window within {budget}s")
        results.append((name, ok))
        ser.run(f"pkill -x {proc} 2>/dev/null; pkill -f {proc} 2>/dev/null; sleep 2", "KILLED", timeout=30)

    print("\n=== summary ===")
    for name, ok in results:
        print(f"{name:12s} {'pass' if ok else 'FAIL'}")
    failed = [n for n, ok in results if not ok]
    ser.close()
    subprocess.run(["kill", (out / "vm.pid").read_text().strip()], check=False)
    print(f"\npictures: {shots}")
    sys.exit(1 if failed else 0)


def find_fresh_package():
    """The newest desktop package on the host, wherever the image build
    left it: a running NAS build parks it in .build/desktop-package."""
    found = [d for r in (REPO / "config" / "packages.chroot", REPO / ".build" / "desktop-package")
             for d in r.glob("fellowship-desktop_*.deb")]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def swap_fresh_package(ser, deb):
    """Install the package staged into the share, if there is one, and
    bring the session back on those binaries. Without this the sweep can
    only test what was baked into the ISO, which for a fix committed after
    the image was built is the wrong code — the 2026-11-15 decoration
    detection is exactly that case. The restart kills the compositor *and*
    its parent dbus-run-session: a leftover wrapper holds the session lock
    and the new session half-starts behind it."""
    if deb is None:
        print("\n=== sweeping the image exactly as shipped ===")
        return
    print(f"\n=== swapping in {deb.name} and restarting the session ===")
    ser.run("sudo -n true 2>/dev/null || { echo live | sudo -S -v; }", "SUDO_OK", timeout=60)
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt",
            "MOUNTED", timeout=60)
    ser.run(f"sudo dpkg -i /mnt/pkg/{deb.name} >/tmp/dpkg.log 2>&1; tail -1 /tmp/dpkg.log",
            "DPKG_DONE", timeout=240)
    ser.run("pkill -x fellowship-comp; pkill -f dbus-run-session; sleep 2; "
            "sudo systemctl restart getty@tty1.service", "SWAPPED", timeout=60)
    wait_session(ser)


def wait_session(ser):
    ser.run("for i in $(seq 90); do SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock 2>/dev/null | head -1); "
            "[ -n \"$SOCK\" ] && fellowship-msg --socket $SOCK frames >/dev/null 2>&1 && break; sleep 2; done; "
            "test -n \"$SOCK\"", "SESSION_UP", timeout=240)


def poll_window(ser, matchers, budget):
    end = time.time() + budget
    while time.time() < end:
        if ser.run("fellowship-msg --socket $SOCK windows", "WINEND", timeout=30):
            for win in parse_windows(ser.transcript):
                low = (win["app_id"] + " " + win["title"]).lower()
                if any(m in low for m in matchers) and win["width"] > 0:
                    return win
        time.sleep(2)
    return None


def parse_windows(text):
    out = []
    for block in re.findall(r"\{[^{}]*\}", text):
        fields = dict(re.findall(r'"(\w+)": ("[^"]*"|\w+)', block))
        if "app_id" in fields and "title" in fields:
            strip = lambda v: v.strip('"')
            try:
                out.append({
                    "app_id": strip(fields["app_id"]),
                    "title": strip(fields["title"]),
                    "decorated": fields.get("decorated", "?") == "true",
                    "x": int(fields.get("x", 0)), "y": int(fields.get("y", 0)),
                    "width": int(fields.get("width", 0)), "height": int(fields.get("height", 0)),
                })
            except ValueError:
                pass
    return out


def shot_screen(mon, path):
    pathlib.Path(path).unlink(missing_ok=True)
    for _ in range(20):
        mon.cmd(f"screendump {path}")
        # The file appearing is not the picture being finished: a screendump
        # still flushing reads as a truncated image and raises in PIL. Wait
        # for the size to stop moving before handing it back.
        if pathlib.Path(path).exists():
            size = pathlib.Path(path).stat().st_size
            for _ in range(6):
                time.sleep(0.3)
                if pathlib.Path(path).stat().st_size == size:
                    return path
            return path
        time.sleep(0.5)
    print(f"[no screendump for {path.name}]")
    return None


def crop_titlebar(shot, win, path):
    if not shot:
        return
    from PIL import Image
    im = Image.open(shot).convert("RGB")
    top = im.crop((max(0, win["x"] - 8), max(0, win["y"] - 8),
                   min(im.width, win["x"] + win["width"] + 8),
                   min(im.height, win["y"] + min(140, win["height"]))))
    top.save(path)
    print(f"chromium title-bar crop: {path}")


def theme_raised():
    """The bar's fill colour, as the shipped dark theme states it. Read once.

    Built without an f-string: a quantifier's braces would be read as a
    replacement field, which is how an earlier check of mine reported "no
    raised colour" against a file that plainly had one.
    """
    global _RAISED
    if _RAISED is None:
        _RAISED = ()
        pat = re.compile(r'^\s*raised\s*=\s*"#([0-9A-Fa-f]{6})"')
        toml = REPO / "themes" / "fellowship.toml"
        try:
            for line in toml.read_text().splitlines():
                m = pat.match(line)
                if m:
                    h = m.group(1)
                    _RAISED = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
                    break
        except OSError:
            pass
    return _RAISED


def bar_above(shot, win):
    """Is our title bar drawn where the picture says it should be?

    `decorated` is the compositor's word for who drew the frame. This is the
    screen's: the strip of BAR_H pixels above the window's reported top edge,
    sampled for the theme's bar colour. A window we frame shows the bar there;
    one that decorates itself shows whatever is behind it. Returning None
    means there is no picture to read, which is never a verdict.
    """
    raised = theme_raised()
    if not shot or not pathlib.Path(str(shot)).exists() or not raised:
        return None
    from PIL import Image
    try:
        im = Image.open(shot).convert("RGB")
    except OSError as err:
        # A picture that will not decode is no evidence either way.
        print(f"  [{pathlib.Path(str(shot)).name}: unreadable screenshot, {err}]")
        return None
    x0 = max(0, win["x"])
    y0 = max(0, win["y"] - BAR_H)
    x1 = min(im.width, x0 + min(200, max(1, win["width"])))
    y1 = min(im.height, win["y"])
    if x1 <= x0 or y1 <= y0:
        return None
    strip = im.crop((x0, y0, x1, y1))
    hits = sum(1 for px in strip.getdata()
               if all(abs(a - b) <= 12 for a, b in zip(px, raised)))
    return hits > strip.width * strip.height * 0.5


class Serial:
    def __init__(self, path):
        self.buf = b""
        self.transcript = ""
        deadline = time.time() + 240
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.s.settimeout(2)
                self.s.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.time() > deadline:
                    sys.exit(f"no serial console at {path}")
                time.sleep(2)

    def read_until(self, marker, timeout=120, echo=True):
        marker = marker.encode()
        end = time.time() + timeout
        while time.time() < end:
            if marker in self.buf:
                self.transcript += self.buf.decode(errors="replace").replace("\r", "")
                self.buf = b""
                return True
            try:
                data = self.s.recv(65536)
                if data:
                    self.buf += data
                    if echo:
                        sys.stdout.write(data.decode(errors="replace"))
                        sys.stdout.flush()
            except socket.timeout:
                pass
        self.transcript += self.buf.decode(errors="replace").replace("\r", "")
        self.buf = b""
        return False

    def send(self, line):
        self.s.sendall((line + "\n").encode())
        time.sleep(0.2)

    def run(self, cmd, marker, timeout=120, echo=True):
        # The split echo keeps the marker out of the typed line, so the tty
        # echo cannot end the wait before the command has actually finished.
        head, tail = marker[: len(marker) // 2], marker[len(marker) // 2:]
        self.transcript = ""
        self.send(f"{cmd}; echo '{head}'{tail}")
        ok = self.read_until(marker, timeout, echo)
        if not ok:
            print(f"\n[timeout waiting for {marker}]")
        return ok

    def login(self):
        self.send("")
        if not self.read_until("login:", 120, echo=False):
            self.send("")
            if not self.read_until("login:", 60, echo=False):
                sys.exit("no login prompt on the serial console")
        self.send("user")
        if not self.read_until("Password:", 20, echo=False):
            sys.exit("no password prompt on the serial console")
        self.send("live")
        if not self.read_until("$ ", 30, echo=False):
            sys.exit("no shell after the serial login")
        # Put the compositor socket where the helpers can reach it.
        self.run("SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock 2>/dev/null | head -1); "
                 "export SOCK; test -n \"$SOCK\"", "SOCKSET", timeout=30)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Monitor:
    def __init__(self, path):
        deadline = time.time() + 120
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.s.settimeout(5)
                self.s.connect(str(path))
                self.s.recv(65536)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.time() > deadline:
                    sys.exit(f"no monitor at {path}")
                time.sleep(2)

    def cmd(self, line):
        self.s.sendall((line + "\n").encode())
        out = b""
        end = time.time() + 10
        while time.time() < end:
            try:
                data = self.s.recv(65536)
            except socket.timeout:
                break
            if data:
                out += data
                if b"(qemu)" in out:
                    break
        return out.decode(errors="replace")


if __name__ == "__main__":
    main()
