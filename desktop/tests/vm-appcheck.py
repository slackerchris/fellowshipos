#!/usr/bin/env python3
"""Launch every bundled app in the desktop VM and report pass/fail.

    desktop/tests/vm-appcheck.py [output-dir]

Boots the current desktop ISO with vm-interact.sh, waits for the session,
then for each of the ten everyday apps: spawns it through the compositor
(so it gets the session's environment, the way the launcher would), polls
`fellowship-msg windows` for its window, photographs the screen through the
QEMU monitor, records the window's size and decoration mode, and kills it
before the next one. Chromium gets a longer allowance and a crop of its own
title-bar area — that is where the double-bar question is answered.

Results are one line per app on stdout, and the pictures land in
<output-dir>/shots/. Exits non-zero if any app failed to show a window.

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

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

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
]


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fellowship-desktop-appcheck")
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for old in shots.glob("app-*"):
        old.unlink()

    boot = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)])
    if boot.returncode != 0:
        sys.exit("boot failed")

    ser = Serial(out / "serial.sock")
    ser.login()
    wait_session(ser)

    mon = Monitor(out / "monitor.sock")
    results = []
    for name, cmd, matchers, proc, budget in APPS:
        print(f"\n=== {name}: {cmd} ===")
        ser.run(f"fellowship-msg --socket $SOCK spawn {cmd}", "SPAWNED", timeout=30)
        win = poll_window(ser, matchers, budget)
        shot = shot_screen(mon, shots / f"app-{name}.ppm")
        if win:
            low = (win["app_id"] + " " + win["title"]).lower()
            matched = next((m for m in matchers if m in low), "?")
            print(f"{name}: PASS app_id={win['app_id']!r} title={win['title']!r} "
                  f"{win['width']}x{win['height']} decorated={win['decorated']} matched={matched}")
            if name == "chromium":
                crop_titlebar(shot, win, shots / "app-chromium-titlebar.png")
        else:
            print(f"{name}: FAIL no window within {budget}s")
        results.append((name, bool(win)))
        ser.run(f"pkill -x {proc} 2>/dev/null; pkill -f {proc} 2>/dev/null; sleep 2", "KILLED", timeout=30)

    print("\n=== summary ===")
    for name, ok in results:
        print(f"{name:12s} {'pass' if ok else 'FAIL'}")
    failed = [n for n, ok in results if not ok]
    ser.close()
    subprocess.run(["kill", (out / "vm.pid").read_text().strip()], check=False)
    print(f"\npictures: {shots}")
    sys.exit(1 if failed else 0)


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
        if pathlib.Path(path).exists():
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
