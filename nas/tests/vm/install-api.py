#!/usr/bin/python3
"""Install the NAS through the graphical installer's HTTPS API, the way a
browser on another computer does: pair with the code from the serial log,
read the machine and the recovery key, then install to the OS disk."""
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.request

BASE = "https://127.0.0.1:8443"
SERIAL = os.environ.get("FELLOWSHIP_SERIAL", "/tmp/fellowship-nas-verify/serial-remote.log")
SETTINGS = {
    "disk": "/dev/vda",
    "hostname": "nas-verify",
    "timezone": "UTC",
    "user": "kim",
    "full_name": "Kim Verify",
    "password": "kim verify pw 1",
    "root_password": "root verify pw 2",
}

context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                     urllib.request.HTTPSHandler(context=context))


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "Origin": BASE,
                                              "X-Fellowship-Installer": "1"})
    with opener.open(request, timeout=30) as answer:
        return json.load(answer)


def wait_for_code():
    for _ in range(180):
        try:
            with open(SERIAL) as file:
                found = re.findall(r"pairing code (\d{6})", file.read())
            if found:
                return found[-1]
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("no pairing code appeared in the serial log")


def main():
    code = wait_for_code()
    print(f"pairing with code {code}", flush=True)
    print(call("POST", "/api/pair", {"code": code}), flush=True)
    machine = call("GET", "/api/machine")
    print("disks:", [(d["path"], d.get("size"), d.get("refused", "")) for d in machine["disks"]], flush=True)
    if machine.get("blocked"):
        raise SystemExit("blocked: " + machine["blocked"])
    os_disk = next(d for d in machine["disks"] if d["path"] == "/dev/vda")
    layout = os_disk.get("layout") or {}
    SETTINGS["system"] = layout.get("system") or layout.get("System")
    if not SETTINGS["system"]:
        raise SystemExit(f"no suggested system size in {layout}")
    key = call("GET", "/api/recovery-key")["key"]
    with open(os.path.join(os.environ.get("FELLOWSHIP_VERIFY_DIR", "/tmp/nas-verify"),
                           "recovery-key.txt"), "w") as file:
        file.write(key + "\n")
    print("recovery key saved", flush=True)
    body = dict(SETTINGS, confirm="erase vda")
    print("installing:", call("POST", "/api/install", body), flush=True)
    for _ in range(600):
        state = call("GET", "/api/progress")
        if state["phase"] in ("done", "failed"):
            print(json.dumps(state, indent=2), flush=True)
            if state["phase"] == "failed":
                raise SystemExit("install failed")
            return
        time.sleep(3)
    raise SystemExit("install did not finish in 30 minutes")


if __name__ == "__main__":
    main()
