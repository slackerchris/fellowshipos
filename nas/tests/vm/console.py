#!/usr/bin/python3
"""Drive the installed NAS's own screen through the QEMU monitor: type on the
active VT, and take screendumps as PNG evidence. Usage:
  console.py type 'text'        type text (no Enter)
  console.py enter              press Enter
  console.py cmd 'command'      type a command and press Enter
  console.py key ret|tab|...    one sendkey
  console.py shot NAME          screendump -> /tmp/nas-verify/shots/NAME.png
"""
import os
import socket
import sys
import time

MONITOR = os.environ.get("FELLOWSHIP_MONITOR", "/tmp/nas-verify-monitor.sock")
SHOTS = os.environ.get("FELLOWSHIP_SHOTS", "/tmp/nas-verify/shots")

KEYS = {
    " ": "spc", "-": "minus", ".": "dot", "/": "slash", ":": "shift-semicolon",
    "_": "shift-minus", "@": "shift-2", "=": "equal", "'": "apostrophe",
    '"': "shift-apostrophe", ">": "shift-dot", "<": "shift-comma",
    "|": "shift-backslash", "~": "shift-grave", "$": "shift-4", "!": "shift-1",
    "(": "shift-9", ")": "shift-0", "&": "shift-7", "*": "shift-8",
    "+": "shift-equal", "?": "shift-slash", ";": "semicolon", ",": "comma",
    "[": "bracket_left", "]": "bracket_right", "{": "shift-bracket_left",
    "}": "shift-bracket_right", "\\": "backslash", "#": "shift-3", "%": "shift-5",
    "^": "shift-6", "`": "grave_accent",
}


def send(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(MONITOR)
        sock.sendall((command + "\n").encode())
        time.sleep(0.05)
        try:
            sock.recv(65536)
        except OSError:
            pass


def type_text(text):
    for char in text:
        key = KEYS.get(char, char)
        send(f"sendkey {key}")
        time.sleep(0.12)


def shot(name):
    import os
    from PIL import Image
    os.makedirs(SHOTS, exist_ok=True)
    ppm = f"{SHOTS}/{name}.ppm"
    send(f"screendump {ppm}")
    time.sleep(1.0)
    Image.open(ppm).save(f"{SHOTS}/{name}.png")
    print(f"saved {SHOTS}/{name}.png")


def main():
    action = sys.argv[1]
    if action == "type":
        type_text(sys.argv[2])
    elif action == "enter":
        send("sendkey ret")
    elif action == "cmd":
        type_text(sys.argv[2])
        time.sleep(0.3)
        send("sendkey ret")
    elif action == "key":
        send(f"sendkey {sys.argv[2]}")
    elif action == "shot":
        shot(sys.argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
