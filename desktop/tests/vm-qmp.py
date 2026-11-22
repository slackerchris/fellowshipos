#!/usr/bin/env python3
"""Drive a running test VM through its QMP socket: look, type, press, click.

    desktop/tests/vm-qmp.py SOCKET shot OUT.png
    desktop/tests/vm-qmp.py SOCKET type 'some text'
    desktop/tests/vm-qmp.py SOCKET key ctrl-alt-f2 [ret ...]
    desktop/tests/vm-qmp.py SOCKET click X Y [left|right]
    desktop/tests/vm-qmp.py SOCKET move X Y
    desktop/tests/vm-qmp.py SOCKET quit

X and Y are screen pixels on the 1920x1080 virtio-gpu output; the usb-tablet
takes them as absolute positions. Used with vm-installed.sh, which boots the
disk vm-install-drive.sh installed, so each step of the first-boot check can
be looked at before the next one.
"""
import json
import os
import socket
import sys
import tempfile
import time

WIDTH, HEIGHT = 1920, 1080

# Characters a US keyboard types with shift, and the key under each
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8',
           '(': '9', ')': '0', '_': 'minus', '+': 'equal', '{': 'bracket_left',
           '}': 'bracket_right', '|': 'backslash', ':': 'semicolon', '"': 'apostrophe',
           '<': 'comma', '>': 'dot', '?': 'slash', '~': 'grave_accent'}
PLAIN = {' ': 'spc', '-': 'minus', '=': 'equal', '[': 'bracket_left', ']': 'bracket_right',
         '\\': 'backslash', ';': 'semicolon', "'": 'apostrophe', ',': 'comma', '.': 'dot',
         '/': 'slash', '`': 'grave_accent', '\n': 'ret', '\t': 'tab'}
NAMES = {'ctrl': 'ctrl', 'alt': 'alt', 'shift': 'shift', 'super': 'meta_l', 'enter': 'ret',
         'esc': 'esc', 'escape': 'esc', 'backspace': 'backspace', 'space': 'spc'}


class Qmp:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(path)
        self.file = self.sock.makefile('rw')
        self.file.readline()  # greeting
        self.call('qmp_capabilities')

    def call(self, command, **arguments):
        self.file.write(json.dumps({'execute': command, 'arguments': arguments}) + '\n')
        self.file.flush()
        while True:
            reply = json.loads(self.file.readline())
            if 'return' in reply:
                return reply['return']
            if 'error' in reply:
                raise SystemExit(f'{command}: {reply["error"]["desc"]}')


def qcode(name):
    name = NAMES.get(name, name)
    return {'type': 'qcode', 'data': name}


def press(qmp, keys):
    qmp.call('send-key', keys=[qcode(k) for k in keys], **{'hold-time': 60})
    time.sleep(0.08)


def type_text(qmp, text):
    for ch in text:
        if ch.isalpha() and ch.isupper():
            press(qmp, ['shift', ch.lower()])
        elif ch.isalnum():
            press(qmp, [ch])
        elif ch in SHIFTED:
            press(qmp, ['shift', SHIFTED[ch]])
        elif ch in PLAIN:
            press(qmp, [PLAIN[ch]])
        else:
            raise SystemExit(f'cannot type {ch!r}')


def move(qmp, x, y):
    qmp.call('input-send-event', events=[
        {'type': 'abs', 'data': {'axis': 'x', 'value': int(x * 32767 / (WIDTH - 1))}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': int(y * 32767 / (HEIGHT - 1))}}])


def click(qmp, x, y, button='left'):
    move(qmp, x, y)
    time.sleep(0.15)
    for down in (True, False):
        qmp.call('input-send-event', events=[{'type': 'btn', 'data': {'down': down, 'button': button}}])
        time.sleep(0.08)


def shot(qmp, out):
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        ppm = os.path.join(tmp, 'screen.ppm')
        qmp.call('screendump', filename=ppm)
        for _ in range(50):
            if os.path.exists(ppm) and os.path.getsize(ppm) > 0:
                break
            time.sleep(0.1)
        time.sleep(0.3)
        Image.open(ppm).save(out)
    print(out)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp = Qmp(sys.argv[1])
    what, rest = sys.argv[2], sys.argv[3:]
    if what == 'shot':
        shot(qmp, rest[0])
    elif what == 'type':
        type_text(qmp, ' '.join(rest))
    elif what == 'key':
        for combo in rest:
            press(qmp, combo.split('-'))
    elif what == 'click':
        click(qmp, int(rest[0]), int(rest[1]), rest[2] if len(rest) > 2 else 'left')
    elif what == 'move':
        move(qmp, int(rest[0]), int(rest[1]))
    elif what == 'quit':
        qmp.call('quit')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
