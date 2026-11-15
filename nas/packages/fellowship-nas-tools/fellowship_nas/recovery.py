"""The recovery key, and the recovery program it opens.

docs/fellowship-nas-account-separation.md: physical access alone doesn't
authorise a root password reset. A recovery key, saved offline at install time,
is what does, and it works with both root's and the NAS admin's passwords lost.

- The system keeps only a hash of the key (RECOVERY_HASH, root, 0600).
- The key is GRUB's password too: the normal entries start without it
  (--unrestricted), but editing an entry, GRUB's command line and the
  FellowshipOS recovery entry all ask for it. That closes the init=/bin/bash way
  round.
- The recovery entry starts fellowship-recovery.target, which runs the console
  program in this module instead of a login. It asks for the key again.

What the key does not protect: the system disk isn't encrypted, so someone who
boots the machine from other media can still change it. Disk encryption is out
of this phase.
"""
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import time

from . import accounts
from .common import STATE_DIR, FnasError, run, write_file, write_json, read_json


RECOVERY_HASH = "/etc/fellowshipos/recovery-key.hash"
# The key as GRUB's password, and the entries it guards
GRUB_USERS = "/etc/grub.d/01_fellowship_recovery"
GRUB_LINUX = "/etc/grub.d/10_linux"
GRUB_LINUX_DEBIAN = "/usr/lib/fellowship-nas/grub/10_linux.debian"
GRUB_USER = "recovery"
RECOVERY_TARGET = "fellowship-recovery.target"
# What recovery did, for the alert at the next normal start
USED = os.path.join(STATE_DIR, "recovery-used.json")
USED_LOG = os.path.join(STATE_DIR, "recovery-log.json")

# 30 letters and digits that can't be mistaken for each other when written
# down: no I, L, O, U, 0 or 1. Eight groups of five is about 196 bits.
ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
GROUPS, GROUP_LENGTH = 8, 5
KEY = re.compile(r"^[%s]{5}(-[%s]{5}){7}$" % (ALPHABET, ALPHABET))


def generate():
    """A new key, as it is shown and typed: eight groups of five, upper case."""
    letters = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LENGTH))
    return "-".join(letters[i:i + GROUP_LENGTH] for i in range(0, len(letters), GROUP_LENGTH))


def normalise(typed):
    """The key as typed, in its one written form: upper case, dashes between the
    groups, whatever spaces or dashes were typed."""
    letters = re.sub(r"[^A-Za-z0-9]", "", typed or "").upper()
    if len(letters) != GROUPS * GROUP_LENGTH:
        return None
    key = "-".join(letters[i:i + GROUP_LENGTH] for i in range(0, len(letters), GROUP_LENGTH))
    return key if KEY.match(key) else None


def exists():
    return os.path.exists(RECOVERY_HASH)


def check(typed):
    """Whether typed is this NAS's recovery key."""
    key = normalise(typed)
    if not key or not exists():
        return False
    with open(RECOVERY_HASH) as file:
        return accounts.verify_password(key, file.read().strip())


def grub_password(key):
    """GRUB's own PBKDF2 form of the key, from grub-mkpasswd-pbkdf2."""
    out = run("grub-mkpasswd-pbkdf2", stdin=f"{key}\n{key}\n").stdout
    found = re.search(r"(grub\.pbkdf2\.sha512\.\S+)", out)
    if not found:
        raise FnasError("grub-mkpasswd-pbkdf2 gave no password")
    return found.group(1)


GRUB_USERS_SCRIPT = """#!/bin/sh
# Written by fellowship-recovery-key. The recovery key is GRUB's password: the
# normal entries start without it, but editing one, GRUB's command line and the
# FellowshipOS recovery entry ask for it. Change it with:
#   fellowship-recovery-key new
cat << 'EOF'
set superusers="%(user)s"
password_pbkdf2 %(user)s %(password)s
EOF
"""

# Debian's own 10_linux, moved aside by dpkg-divert, so a grub upgrade updates
# that copy and this wrapper keeps working
GRUB_LINUX_WRAPPER = r"""#!/bin/sh
# Written by fellowship-recovery-key: Debian's entries, open to everyone to
# start, plus FellowshipOS recovery, which only the recovery key opens.
set -e
out=$(%(debian)s "$@")
printf '%%s\n' "$out" | sed -E 's/^([[:space:]]*(menuentry|submenu) .*) \{$/\1 --unrestricted {/'
# Recovery: the first entry again, under its own name and id, for the key's
# holder only, starting the recovery program instead of the system
printf '%%s\n' "$out" | awk '
    !done && /^menuentry / { copying = 1 }
    copying { print }
    copying && /^}/ { copying = 0; done = 1 }' | sed -E \
    -e "1s/^menuentry '[^']*'/menuentry 'FellowshipOS recovery'/" \
    -e '1s/gnulinux-simple-/fellowship-recovery-/' \
    -e '1s/ \{$/ --users %(user)s {/' \
    -e 's/^([[:space:]]*linux[[:space:]].*)$/\1 systemd.unit=%(target)s/'
"""


def divert_linux():
    """Put Debian's 10_linux aside once, and the wrapper in its place."""
    listed = run("dpkg-divert", "--list", GRUB_LINUX, check=False).stdout
    if GRUB_LINUX_DEBIAN not in listed:
        os.makedirs(os.path.dirname(GRUB_LINUX_DEBIAN), exist_ok=True)
        run("dpkg-divert", "--local", "--rename", "--divert", GRUB_LINUX_DEBIAN, "--add", GRUB_LINUX)
    write_file(GRUB_LINUX, GRUB_LINUX_WRAPPER % {"debian": GRUB_LINUX_DEBIAN, "user": GRUB_USER,
                                                 "target": RECOVERY_TARGET}, mode=0o755)


def update_grub():
    """Rewrite grub.cfg, where there is a boot disk to write it for. The
    installer runs update-grub itself when it makes the disk bootable."""
    if run("grub-probe", "/", check=False).returncode == 0:
        run("update-grub")


def set_key(typed):
    key = normalise(typed)
    if not key:
        raise FnasError(f"a recovery key is {GROUPS} groups of {GROUP_LENGTH} letters and digits")
    os.makedirs(os.path.dirname(RECOVERY_HASH), exist_ok=True)
    write_file(RECOVERY_HASH, accounts.hash_password(key) + "\n", mode=0o600)
    # The PBKDF2 hash ends up in grub.cfg, which is how GRUB works; with a key
    # this long it can't be guessed from there
    write_file(GRUB_USERS, GRUB_USERS_SCRIPT % {"user": GRUB_USER, "password": grub_password(key)}, mode=0o700)
    divert_linux()
    update_grub()


def remove_key():
    for path in (RECOVERY_HASH, GRUB_USERS):
        if os.path.exists(path):
            os.remove(path)
    if GRUB_LINUX_DEBIAN in run("dpkg-divert", "--list", GRUB_LINUX, check=False).stdout:
        os.remove(GRUB_LINUX)
        run("dpkg-divert", "--local", "--rename", "--divert", GRUB_LINUX_DEBIAN, "--remove", GRUB_LINUX)
    update_grub()


def key_main(argv=None):
    """fellowship-recovery-key: root's own tool for the recovery key."""
    import argparse
    parser = argparse.ArgumentParser(prog="fellowship-recovery-key",
                                     description="the recovery key that opens FellowshipOS recovery")
    action = parser.add_subparsers(dest="action", required=True)
    action.add_parser("new", help="make a new key, show it once, and make the old one stop working")
    action.add_parser("set", help="use the key given on stdin (the installer's)")
    action.add_parser("check", help="exit 0 if the key on stdin is this NAS's")
    action.add_parser("status", help="whether this NAS has a recovery key")
    action.add_parser("remove", help="remove the key, and with it the lock on GRUB")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise FnasError("run as root")
        if args.action == "new":
            key = generate()
            set_key(key)
            print("Your new recovery key. It is shown only now: write it down or print it,\n"
                  "and keep it somewhere other than this NAS. The old key no longer works.\n")
            print(f"    {key}\n")
        elif args.action == "set":
            set_key(sys.stdin.readline())
        elif args.action == "check":
            return 0 if check(sys.stdin.readline()) else 1
        elif args.action == "status":
            print("This NAS has a recovery key." if exists() else "This NAS has no recovery key.")
            return 0 if exists() else 1
        else:
            remove_key()
            print("The recovery key is gone, and GRUB is no longer locked with it.")
    except FnasError as error:
        print(f"fellowship-recovery-key: {error}", file=sys.stderr)
        return 1
    return 0


# The console program ----------------------------------------------------------

def note_recovery(what, now=None):
    used = read_json(USED, []) or []
    used.append({"time": int(time.time() if now is None else now), "what": what})
    write_json(USED, used, mode=0o600)


def announce_used():
    """At a normal start after recovery: alert, and keep the record."""
    used = read_json(USED, None)
    if not used:
        return
    from . import alerts
    lines = "\n".join(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(u['time']))}: {u['what']}" for u in used)
    alerts.send_alert("high", "Recovery was used at the console",
                      "Someone with the recovery key used FellowshipOS recovery:\n" + lines)
    log = read_json(USED_LOG, []) or []
    write_json(USED_LOG, log + used, mode=0o600)
    os.remove(USED)


def ask_new_password(label, ask=getpass.getpass, say=print):
    while True:
        first = ask(f"New password for {label}: ")
        if len(first) < 8:
            say("At least 8 characters, please.")
            continue
        if first != ask("The same again: "):
            say("Those don't match. Again.")
            continue
        return first


def set_root_password(ask=getpass.getpass, say=print):
    password = ask_new_password("root", ask, say)
    run("chpasswd", stdin=f"root:{password}\n")
    # An expired or locked root can't log in at the console either
    run("usermod", "--unlock", "--expiredate", "", "root", check=False)
    note_recovery("set root's password")
    say("Root's password is set. Root can log in at the console.")


def nas_people():
    return sorted(accounts.store_people()) if accounts.is_separated() else []


def set_person_password(ask=input, secret=getpass.getpass, say=print):
    from . import users
    people = nas_people()
    if not people:
        say("This NAS keeps no NAS people (it hasn't been separated), so there is nothing to set here.")
        return
    say("NAS people: " + ", ".join(people))
    name = ask("Whose password? ").strip()
    if name not in people:
        say(f"{name or 'Nobody'} isn't one of them.")
        return
    users.set_password(name, ask_new_password(name, secret, say))
    note_recovery(f"set {name}'s NAS password")
    say(f"{name}'s password is set, for the desktop and shares.")


def make_admin(ask=input, say=print):
    from . import users
    people = nas_people()
    if not people:
        say("This NAS keeps no NAS people (it hasn't been separated).")
        return
    say("NAS people: " + ", ".join(people))
    name = ask("Make whom a NAS admin? ").strip()
    if name not in people:
        say(f"{name or 'Nobody'} isn't one of them.")
        return
    users.set_admin(name, True)
    users.change_user(name, enabled=True)
    note_recovery(f"made {name} a NAS admin")
    say(f"{name} is a NAS admin, and turned on.")


def undo_separation(ask=input, say=print):
    if not os.path.isdir(os.path.join(STATE_DIR, "accounts-before-separate")):
        say("This NAS has nothing to undo: it was installed separated, not migrated.")
        return
    if ask('Type "undo" to put back the accounts as they were before separating: ').strip() != "undo":
        say("Left as it is.")
        return
    result = run("fnas", "accounts", "separate", "--undo", check=False, timeout=900)
    say(result.stdout + result.stderr)
    note_recovery("undid account separation" if result.returncode == 0 else "tried to undo account separation")


MENU = (("1", "Set root's password"),
        ("2", "Set a NAS person's password"),
        ("3", "Make a NAS person an admin"),
        ("4", "Undo account separation"),
        ("5", "Restart"))


def console_main(ask=input, secret=getpass.getpass, say=print, sleep=time.sleep):
    """The program fellowship-recovery.target runs on the console."""
    say("\n  FellowshipOS recovery\n")
    if not exists():
        say("This NAS has no recovery key, so recovery can't be used. Restarting in 30 seconds.")
        sleep(30)
        return "restart"
    wrong = 0
    while not check(secret("Recovery key: ")):
        wrong += 1
        delay = min(2 ** wrong, 120)
        say(f"That isn't this NAS's recovery key. Wait {delay} seconds.")
        sleep(delay)
    note_recovery("opened recovery with the key")
    actions = {"1": set_root_password, "2": set_person_password, "3": make_admin, "4": undo_separation}
    while True:
        say("")
        for number, label in MENU:
            say(f"  {number}) {label}")
        choice = ask("Choose: ").strip()
        if choice == "5":
            return "restart"
        if choice in actions:
            try:
                if choice == "1":
                    set_root_password(secret, say)
                elif choice == "2":
                    set_person_password(ask, secret, say)
                elif choice == "4":
                    undo_separation(ask, say)
                else:
                    make_admin(ask, say)
            except FnasError as error:
                say(f"That didn't work: {error}")


def console(argv=None):
    if os.geteuid() != 0:
        print("run as root", file=sys.stderr)
        return 1
    if console_main() == "restart":
        print("Restarting.")
        subprocess.run(["systemctl", "reboot"])
    return 0


if __name__ == "__main__":
    sys.exit(key_main())
