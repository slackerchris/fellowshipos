"""How each person likes the desktop: light, dark or the system's, and which
theme. Kept on the NAS so a person's choice follows them to another computer
or browser instead of staying in the one they first chose it in."""
import os
import pwd
import re

from .common import STATE_DIR, FnasError, locked, read_json, write_json
from . import users

LOOK_FILE = "/etc/fellowshipos/nas-look.json"
LOOK_LOCK = os.path.join(STATE_DIR, "look.lock")

MODES = ("system", "light", "dark")

# What someone sees before they choose. The desktop's own theme is the default.
DEFAULTS = {"mode": "system", "palette": "fellowship"}

# A theme's name as the desktop spells it. The themes themselves live in the
# desktop's themes.mjs; fnas only keeps the name, so a theme added or removed
# there needs nothing here.
THEME_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def check_user(name):
    """A person's account, which is who has a desktop to choose a look in. Asked
    of the account file itself, so answering doesn't run anything."""
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        raise FnasError(f"no such user: {name}") from None
    if not users.is_person(account):
        raise FnasError(f"{name} isn't a person's account")
    return account.pw_name


def chosen_looks():
    """Everyone's choice, by user name."""
    return (read_json(LOOK_FILE) or {}).get("users", {})


def show(user):
    """What this person chose, or the defaults if they haven't chosen yet."""
    check_user(user)
    return {"user": user, **DEFAULTS, **chosen_looks().get(user, {})}


def set_look(user, mode=None, palette=None):
    """Remember a person's light or dark and theme."""
    check_user(user)
    if mode is None and palette is None:
        raise FnasError("nothing to change: give --mode, --palette, or both")
    if mode is not None and mode not in MODES:
        raise FnasError(f"mode must be one of: {', '.join(MODES)}")
    if palette is not None and not THEME_NAME.match(palette):
        raise FnasError(f"{palette} isn't a theme name")
    with locked(LOOK_LOCK):
        saved = chosen_looks()
        chosen = {**DEFAULTS, **saved.get(user, {})}
        if mode is not None:
            chosen["mode"] = mode
        if palette is not None:
            chosen["palette"] = palette
        saved[user] = chosen
        write_json(LOOK_FILE, {"users": saved})
    return {"user": user, **chosen}


def forget_look(user):
    """Drop a person's choice, for when their account goes. Named apart from
    shares.forget, which forgets who a share was given to."""
    with locked(LOOK_LOCK):
        saved = chosen_looks()
        if saved.pop(user, None) is not None:
            write_json(LOOK_FILE, {"users": saved})
