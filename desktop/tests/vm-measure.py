#!/usr/bin/env python3
"""Measure the desktop's responsiveness in the VM, before and after the
freshly built package — without rebuilding the image.

    desktop/tests/vm-measure.py            # llvmpipe baseline, then the new .deb
    desktop/tests/vm-measure.py --gl       # same through virtio-gpu-gl
    desktop/tests/vm-measure.py --only-new # image already carries the fix

Boots (after replacing any running guest, so the GPU mode always matches)
the interactive machine from vm-interact.sh, logs in on the serial console,
runs guest-measure.sh against the session as the image ships it, then
hot-swaps the shared fellowship-desktop .deb in (dpkg on the RAM-backed
live root), restarts the console login so the session comes back on the new
binaries, and measures again. The guest writes its screenshots into the
share's out/ folder, visible on the host.

What the numbers separate is the point: delivery stalls land as one slow run
beside fast ones, a render-bound machine shifts every run together, and the
dri-mapped line says which Mesa driver was actually loaded instead of
guessing it from the QEMU flags.

FELLOWSHIP_OUT picks the working directory (default
/tmp/fellowship-desktop-vm-interact, same as vm-interact.sh); --keep leaves
the VM running afterwards.
"""
import argparse
import os
import pathlib
import re
import socket
import statistics
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5, help="launcher taps per pass")
    ap.add_argument("--gl", action="store_true", help="boot with virtio-gpu-gl")
    ap.add_argument("--only-new", action="store_true",
                    help="skip the pre-swap pass (the image already carries the fix)")
    ap.add_argument("--keep", action="store_true", help="leave the VM running")
    ap.add_argument("--measure-timeout", type=int, default=300)
    args = ap.parse_args()

    out = pathlib.Path(os.environ.get("FELLOWSHIP_OUT", "/tmp/fellowship-desktop-vm-interact"))
    serial_path = out / "serial.sock"
    share = out / "share"

    deb = sorted((REPO / "config" / "packages.chroot").glob("fellowship-desktop_*.deb"))
    if not deb:
        sys.exit("no fellowship-desktop .deb built; run desktop/build.sh first")
    deb = deb[-1]

    # The probe goes into the share alongside the package.
    probe = share / "guest-measure.sh"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_bytes((REPO / "desktop" / "tests" / "guest-measure.sh").read_bytes())
    os.chmod(probe, 0o755)
    (share / "out").mkdir(exist_ok=True)
    for old_shot in (share / "out").glob("*.png"):
        old_shot.unlink()

    # A guest left from another run would keep the old GPU mode: the share
    # and the measurements are per-run, so always boot fresh.
    pidfile = out / "vm.pid"
    if pidfile.exists() and pid_alive(pidfile):
        subprocess.run(["kill", pidfile.read_text().strip()], check=False)
        for _ in range(30):
            if not pid_alive(pidfile):
                break
            time.sleep(1)

    env = dict(os.environ)
    if args.gl:
        env["FELLOWSHIP_GL"] = "1"
    boot = subprocess.run([str(REPO / "desktop" / "tests" / "vm-interact.sh"), str(out)], env=env)
    if boot.returncode != 0:
        sys.exit("boot failed")

    ser = Serial(serial_path)
    print("\n=== logging in on the serial console ===")
    ser.login()
    ser.run("sudo -n true 2>/dev/null || { echo live | sudo -S -v; }", "SUDO_OK", timeout=60)
    ser.run("mountpoint -q /mnt || sudo mount -t 9p -o trans=virtio,version=9p2000.L hostshare /mnt; "
            "test -e /mnt/guest-measure.sh", "SHARE_OK", timeout=60)

    results = {}
    mon = None
    if not args.gl:
        # screendump only sees the framebuffer on the plain virtio-gpu; the
        # GL trial reads back nothing, so blind taps are llvmpipe-only.
        mon = Monitor(out / "monitor.sock")
    if not args.only_new:
        if mon:
            print("\n=== blind taps on the shipped image (nothing touches the session) ===")
            results["old-blind"] = blind_taps(mon, args.runs)
        print("\n=== the session as the image ships it ===")
        results["old"] = ser.measure("old", args.runs, args.measure_timeout)

    print("\n=== installing the fresh package and restarting the session ===")
    ser.run(f"sudo dpkg -i /mnt/pkg/{deb.name} >/tmp/dpkg.log 2>&1; tail -2 /tmp/dpkg.log",
            "DPKG_DONE", timeout=240)
    ser.run("pkill -x fellowship-comp; sleep 2; sudo systemctl restart getty@tty1.service",
            "SWAPPED", timeout=60)
    ser.wait_session(args.measure_timeout)

    if mon:
        print("\n=== blind taps on the new package ===")
        results["new-blind"] = blind_taps(mon, args.runs)
    print("\n=== the same session on the new binaries ===")
    results["new"] = ser.measure("new", args.runs, args.measure_timeout)

    print("\n=== comparison ===")
    print_compare(results)
    print(f"\nguest screenshots: {share / 'out'}")

    if args.keep:
        print(f"VM left running (pid {pidfile.read_text().strip() if pidfile.exists() else '?'}); "
              f"serial {serial_path}")
    else:
        if pidfile.exists() and pid_alive(pidfile):
            subprocess.run(["kill", pidfile.read_text().strip()], check=False)
        print("VM stopped")
    ser.close()


def pid_alive(pidfile):
    try:
        os.kill(int(pidfile.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


class Monitor:
    """The QEMU human monitor: sendkey and screendump, none of which touch
    the session from inside — the blind tap measurement."""

    def __init__(self, path):
        deadline = time.time() + 120
        while True:
            try:
                self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.s.settimeout(5)
                self.s.connect(str(path))
                self.drain_prompt()
                self.key = self.pick_key()
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.time() > deadline:
                    sys.exit(f"no monitor at {path}")
                time.sleep(2)

    def drain_prompt(self):
        try:
            self.s.recv(65536)
        except socket.timeout:
            pass

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

    def pick_key(self):
        for name in ("meta_l", "meta", "super", "meta_r"):
            if "invalid" not in self.cmd(f"sendkey {name}").lower():
                return name
        sys.exit("the monitor has no Super key name; sendkey meta_l alone failed")


SHOT = "/tmp/fellowship-blind-shot.ppm"


def blind_taps(mon, runs):
    """Tap Super through the monitor and watch only the framebuffer: no
    socket, no request, no flush. Latency is keypress to the picture
    changing, plus at most one poll interval of observation overhead. The
    check is a toggle, not an open: it measures how long a change takes to
    appear, whichever way the launcher happened to sit at the start."""
    from PIL import Image, ImageChops, ImageStat

    pathlib.Path(SHOT).unlink(missing_ok=True)

    def shot():
        # The console surface only exists once the session has the VT; a
        # screendump asked for before that writes nothing, so wait for the
        # picture instead of believing the command returned.
        for _ in range(60):
            mon.cmd(f"screendump {SHOT}")
            if pathlib.Path(SHOT).exists():
                im = Image.open(SHOT).convert("L")
                w, h = im.size
                return im.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4))
            time.sleep(0.5)
        sys.exit("screendump never produced a picture; the session is not on the console")

    def changed(a, b):
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]

    baseline = shot()
    latencies = []
    for _ in range(min(runs, 5)):
        # settle back to the baseline state before each timed toggle
        for _ in range(10):
            if changed(shot(), baseline) < 3:
                break
            mon.cmd(f"sendkey {mon.key}")
            time.sleep(0.4)
        t0 = time.perf_counter()
        mon.cmd(f"sendkey {mon.key}")
        ms = None
        while time.perf_counter() - t0 < 8:
            time.sleep(0.05)
            if changed(shot(), baseline) > 6:
                ms = (time.perf_counter() - t0) * 1000
                break
        latencies.append(int(ms) if ms else None)
    real = [v for v in latencies if v is not None]
    line = "blind-toggle-ms " + " ".join("none" if v is None else str(v) for v in latencies)
    if real:
        line += f" | median {int(statistics.median(real))} max {max(real)}"
    print(line)
    return {"blind-toggle-ms": latencies}


class Serial:
    """Talk to the guest's serial console: one line in, text until a marker."""

    def __init__(self, path):
        self.buf = b""
        self.transcript = ""
        deadline = time.time() + 120
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
        if isinstance(marker, str):
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

    def run(self, cmd, marker, timeout=120):
        # Quote the marker's middle so the echo of the typed command line
        # never contains the literal the wait looks for: without that, the
        # serial echo matches first and commands pile into the tty input
        # queue unread. Callers must not put the marker in cmd either.
        head, tail = marker[: len(marker) // 2], marker[len(marker) // 2:]
        self.transcript = ""
        self.send(f'{cmd}; echo \'{head}\'{tail}')
        ok = self.read_until(marker, timeout)
        if not ok:
            print(f"\n[timeout waiting for {marker}]")
        return ok

    def login(self):
        self.send("")
        if not self.read_until("login:", 90, echo=False):
            self.send("")
            if not self.read_until("login:", 60, echo=False):
                sys.exit("no login prompt on the serial console")
        self.send("user")
        if not self.read_until("Password:", 20, echo=False):
            sys.exit("no password prompt on the serial console")
        self.send("live")
        if not self.read_until("$ ", 30, echo=False):
            sys.exit("no shell after the serial login")

    def measure(self, tag, runs, timeout):
        self.run(f"sh /mnt/guest-measure.sh {runs} {tag}", "MEASURE_DONE", timeout=timeout)
        rows = re.findall(rf"^{tag}: (\S+) (.+)$", self.transcript, re.MULTILINE)
        grouped = {}
        for key, value in rows:
            grouped.setdefault(key, []).append(value)
        if not rows:
            print(f"[{tag}] nothing measured; see the console output above")
        return grouped

    def wait_session(self, timeout):
        # The compositor's own socket, never the shell's channel: it sorts
        # first under fellowship-*.sock and answers nothing to these requests.
        self.run("for i in $(seq 60); do "
                 "SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock 2>/dev/null | head -1); "
                 "[ -n \"$SOCK\" ] && fellowship-msg --socket \"$SOCK\" frames "
                 ">/dev/null 2>&1 && break; sleep 2; done", "SESSION_UP",
                 timeout=min(timeout, 180))

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def taps(res, key):
    out = []
    for line in res.get(key, []):
        m = re.search(r"wall=(\d+) ask=(\d+) frames=(\d+) ok=(\w+)", line)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) == "yes"))
    return out


def report(tag, res):
    print(f"\n--- {tag} ---")
    for key in ("dri-mapped", "cursor-shape", "idle-painted", "idle-redraws",
                "motion-wall-ms", "motion-redraws", "motion-painted",
                "motion-pointer-redraws", "screenshot", "RESULT"):
        if key in res:
            print(f"{key:24s} {res[key][-1]}")
    for key in ("super-open-ms", "super-close-ms"):
        vals = taps(res, key)
        if vals:
            wall = [w for w, _, _, _ in vals]
            asks = [a for _, a, _, _ in vals]
            missed = sum(1 for *_, ok in vals if not ok)
            verdict = f"  [{missed} timed out]" if missed else ""
            if max(wall) > 2.5 * max(statistics.median(wall), 1):
                verdict += "  one-off stalls: look at delivery"
            elif statistics.median(wall) > 250:
                verdict += "  every tap slow together: render-bound"
            print(f"{key:24s} wall median {int(statistics.median(wall))}ms max {max(wall)}ms"
                  f" | ask median {int(statistics.median(asks))}ms{verdict}")
    if "blind-toggle-ms" in res:
        vals = [v for v in res["blind-toggle-ms"] if v is not None]
        note = "  [a toggle never appeared]" if len(vals) < len(res["blind-toggle-ms"]) else ""
        if vals:
            note += f"  median {int(statistics.median(vals))}ms max {max(vals)}ms"
        print(f"{'blind-toggle-ms':24s} {res['blind-toggle-ms']}{note}")


def print_compare(results):
    for tag in ("old", "old-blind", "new", "new-blind"):
        if tag in results:
            report(tag, results[tag])
    owall = [w for w, _, _, _ in taps(results.get("old", {}), "super-open-ms")]
    nwall = [w for w, _, _, _ in taps(results.get("new", {}), "super-open-ms")]
    if owall and nwall:
        print(f"\nsuper-open wall median: old {int(statistics.median(owall))}ms -> "
              f"new {int(statistics.median(nwall))}ms ; worst {max(owall)}ms -> {max(nwall)}ms")
    ob = [v for v in results.get("old-blind", {}).get("blind-toggle-ms", []) if v is not None]
    nb = [v for v in results.get("new-blind", {}).get("blind-toggle-ms", []) if v is not None]
    if ob and nb:
        print(f"blind-toggle median: old {int(statistics.median(ob))}ms -> "
              f"new {int(statistics.median(nb))}ms ; worst {max(ob)}ms -> {max(nb)}ms")
    elif ob or nb:
        print("blind-toggle ran on only one side; no before/after claim")


if __name__ == "__main__":
    main()
