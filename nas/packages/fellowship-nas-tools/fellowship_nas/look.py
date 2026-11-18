"""How each person likes the desktop: light, dark or the system's, which
theme, how the clock reads, and which status widgets are folded or hidden. Kept on the NAS so a person's choice follows them to another computer
or browser instead of staying in the one they first chose it in."""
import os
import pwd
import re

from .common import STATE_DIR, FnasError, locked, read_json, write_json
from . import users

LOOK_FILE = "/etc/fellowshipos/nas-look.json"
LOOK_LOCK = os.path.join(STATE_DIR, "look.lock")

MODES = ("system", "light", "dark")
# auto follows the device's language, as the clock always has
HOURS = ("auto", "12", "24")
# What the taskbar shows beside the time
DATES = ("off", "short", "numeric")

# The status widgets on an administrator's desktop, which can each be folded
# to their title, and all together hidden behind a tab
WIDGETS = ("storage", "health", "disks", "alerts")
WIDGETS_SHOWN = ("shown", "hidden")

# What someone sees before they choose. The desktop's own theme is the default.
DEFAULTS = {"mode": "system", "palette": "fellowship", "hours": "auto", "seconds": False, "date": "off",
            "widgets": "shown", "collapsed": []}

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


def set_look(user, mode=None, palette=None, hours=None, seconds=None, date=None, widgets=None, collapsed=None):
    """Remember a person's light or dark, theme, clock and widgets."""
    check_user(user)
    change = {"mode": mode, "palette": palette, "hours": hours, "seconds": seconds, "date": date,
              "widgets": widgets, "collapsed": None if collapsed is None else sorted(set(collapsed))}
    change = {key: value for key, value in change.items() if value is not None}
    if not change:
        raise FnasError("nothing to change: give --mode, --palette, --hours, --seconds, --date, --widgets "
                        "or --collapsed")
    for name in change.get("collapsed", []):
        if name not in WIDGETS:
            raise FnasError(f"{name} isn't a widget: choose from {', '.join(WIDGETS)}")
    for key, allowed in (("mode", MODES), ("hours", HOURS), ("date", DATES), ("widgets", WIDGETS_SHOWN)):
        if key in change and change[key] not in allowed:
            raise FnasError(f"{key} must be one of: {', '.join(allowed)}")
    if "palette" in change and not THEME_NAME.match(palette):
        raise FnasError(f"{palette} isn't a theme name")
    if "seconds" in change and not isinstance(seconds, bool):
        raise FnasError("seconds is on or off")
    with locked(LOOK_LOCK):
        saved = chosen_looks()
        chosen = {**DEFAULTS, **saved.get(user, {}), **change}
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
