"""When each engine checks its pools by itself, as set in Settings → Storage
checks. fnas scrubs ZFS pools from its own timer, in place of Debian's cron
job, so only one of them runs. An array's check stays Debian's mdcheck, which
pauses each morning and carries on the next night; fnas's own check runs in one
go, so until it can span nights too, arrays are only turned on or off. An
Unraid-style pool's scrub, nightly parity sync and disk spin-down are fnas's
from the start."""
import os

from .common import DAY, FnasError, read_json, run, write_file, write_json
from . import pools


SETTINGS_FILE = "/etc/fellowshipos/nas-checks.json"
# One timer per engine fnas schedules itself, made from a template, and one for
# each engine's parity sync
TIMER = "fellowship-nas-scrub@{engine}.timer"
DROP_IN_DIR = "/etc/systemd/system/fellowship-nas-scrub@{engine}.timer.d"
SYNC_TIMER = "fellowship-nas-sync@{engine}.timer"
SYNC_DROP_IN_DIR = "/etc/systemd/system/fellowship-nas-sync@{engine}.timer.d"
# Debian's own monthly array check, and the nights it carries on
MDCHECK_TIMERS = ("mdcheck_start.timer", "mdcheck_continue.timer")
# The defaults reproduce what Debian did before: ZFS on the second Sunday of
# each month, just after midnight, and arrays from the first Sunday
# A SnapRAID scrub reads part of the pool each week, its sync runs nightly
# before the 03:00 backup, and idle disks spin down after half an hour
DEFAULTS = {"zfs": {"enabled": True, "every": "monthly", "week": 2, "day": "sun", "hour": 0},
            "mergerfs": {"enabled": True, "every": "weekly", "week": 1, "day": "sun", "hour": 2,
                         "sync": True, "sync_hour": 1, "spin_down": 30},
            "mdadm": {"enabled": True}}
# Engines whose timing fnas sets; the others only turn on and off
TIMED = ("zfs", "mergerfs")
# Engines with a parity sync of their own
SYNCED = ("mergerfs",)
EVERY = {"weekly": ("*", "each week"), "monthly": ("*", "each month"),
         "quarterly": ("01,04,07,10", "every three months")}
# How often each engine can check; a SnapRAID scrub reads only part of the pool
CHOICES = {"zfs": ("monthly", "quarterly"), "mergerfs": ("weekly", "monthly")}
SPIN_DOWN = (0, 10, 20, 30, 60, 120)
DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
        "sat": "Saturday", "sun": "Sunday"}
WEEKS = {1: "first", 2: "second", 3: "third", 4: "fourth"}
# How long after a check the next is overdue: the gap between them, plus a week
MAX_AGE = {"weekly": 14 * DAY, "monthly": 40 * DAY, "quarterly": 100 * DAY}


def checked(engine, saved):
    """One engine's settings, with anything missing or wrong put back to its default."""
    default = DEFAULTS[engine]
    current = {key: saved.get(key, value) if type(saved.get(key)) is type(value) else value
               for key, value in default.items()}
    if engine in TIMED:
        if current["every"] not in CHOICES[engine] or current["day"] not in DAYS or current["week"] not in WEEKS \
                or not 0 <= current["hour"] <= 23:
            current = dict(default)
    if engine in SYNCED and (not 0 <= current["sync_hour"] <= 23 or current["spin_down"] not in SPIN_DOWN):
        current = dict(default)
    return current


def settings():
    saved = read_json(SETTINGS_FILE, {})
    return {engine: checked(engine, saved.get(engine) if isinstance(saved.get(engine), dict) else {})
            for engine in DEFAULTS}


def calendar(current):
    """The schedule as systemd's OnCalendar: the nth weekday falls in days 1–7, 8–14 and so on."""
    months, _ = EVERY[current["every"]]
    if current["every"] == "weekly":
        return f"{current['day'].capitalize()} *-*-* {current['hour']:02d}:00:00"
    first = (current["week"] - 1) * 7 + 1
    return f"{current['day'].capitalize()} *-{months}-{first:02d}..{first + 6:02d} {current['hour']:02d}:00:00"


def describe(engine):
    """When an engine's pools are checked, in words, or "" when that's turned off."""
    current = settings().get(engine)
    if not current or not current["enabled"]:
        return ""
    if engine in TIMED:
        _, every = EVERY[current["every"]]
        if current["every"] == "weekly":
            return f"every {DAYS[current['day']]}, at {current['hour']:02d}:00"
        return (f"on the {WEEKS[current['week']]} {DAYS[current['day']]} of {every}, "
                f"at {current['hour']:02d}:00")
    return pools.ENGINES[engine].CHECK_SCHEDULE


def sentence(engine):
    """The line Storage and Health show about an engine's automatic checks."""
    when = describe(engine)
    return (f"It runs by itself {when}." if when
            else "Automatic checks are turned off in Settings, so it runs only when started here.")


def max_age(engine):
    """How old a pool's last check may get before Health says so, or None when
    automatic checks are turned off, which is a choice rather than a problem."""
    current = settings().get(engine)
    if not current or not current["enabled"]:
        return None
    return MAX_AGE[current["every"]] if engine in TIMED else MAX_AGE["monthly"]


def next_run(engine, timer=None):
    """When the engine's timer next fires, in seconds since the epoch, or None."""
    timer = timer or (TIMER.format(engine=engine) if engine in TIMED else MDCHECK_TIMERS[0])
    # systemctl show prints it as a local date, such as "Sun 2026-12-13 00:00:00 EST"
    value = run("systemctl", "show", "-P", "NextElapseUSecRealtime", timer, check=False).stdout.strip()
    if not value:
        return None
    stamp = run("date", "-d", value, "+%s", check=False).stdout.strip()
    return int(stamp) if stamp.isdigit() else None


def set_timer(timer, folder, when, enabled):
    """Give a timer its schedule and turn it on or off. Returns a problem, or ""."""
    os.makedirs(folder, exist_ok=True)
    # The empty line clears the template's own schedule before setting this one
    write_file(os.path.join(folder, "schedule.conf"),
               "# Written by fnas from Settings → Storage checks\n"
               f"[Timer]\nOnCalendar=\nOnCalendar={when}\n")
    run("systemctl", "daemon-reload", check=False)
    action = ("enable", "--now") if enabled else ("disable", "--now")
    if run("systemctl", *action, timer, check=False).returncode != 0:
        return f"couldn't {action[0]} {timer}"
    return ""


def apply():
    """Bring the timers in line with the settings. Returns what couldn't be done."""
    current = settings()
    problems = []
    used = pools.usable_engines()
    for engine in TIMED:
        # An engine that can't be used here has nothing to check, so its timer stays off
        problem = set_timer(TIMER.format(engine=engine), DROP_IN_DIR.format(engine=engine),
                            calendar(current[engine]), current[engine]["enabled"] and engine in used)
        problems += [problem] if problem else []
    for engine in SYNCED:
        problem = set_timer(SYNC_TIMER.format(engine=engine), SYNC_DROP_IN_DIR.format(engine=engine),
                            f"*-*-* {current[engine]['sync_hour']:02d}:00:00",
                            current[engine]["sync"] and engine in used)
        problems += [problem] if problem else []
    if "mergerfs" in used:
        pools.ENGINES["mergerfs"].write_spin_down()
    if "zfs" in used:
        for pool in pools.list_pools():
            if pool["engine"] == "zfs" and not pool["readonly"]:
                pools.ENGINES["zfs"].take_over_scrubs(pool["name"])
    if "mdadm" in used:
        # Masked rather than disabled, so mdadm turning them back on can't undo the choice
        if current["mdadm"]["enabled"]:
            run("systemctl", "unmask", *MDCHECK_TIMERS, check=False)
            if run("systemctl", "enable", "--now", *MDCHECK_TIMERS, check=False).returncode != 0:
                problems.append("couldn't turn on Debian's monthly array check (mdcheck_start.timer)")
        elif run("systemctl", "mask", "--now", *MDCHECK_TIMERS, check=False).returncode != 0:
            problems.append("couldn't turn off Debian's monthly array check (mdcheck_start.timer)")
    return problems


def set_schedule(engine, enabled=None, every=None, week=None, day=None, hour=None,
                 sync=None, sync_hour=None, spin_down=None):
    if engine not in DEFAULTS:
        raise FnasError(f"{engine} has no checks to schedule: choose " + " or ".join(DEFAULTS))
    timing = {"every": every, "week": week, "day": day, "hour": hour}
    syncing = {"sync": sync, "sync_hour": sync_hour, "spin_down": spin_down}
    if engine not in TIMED and any(value is not None for value in timing.values()):
        raise FnasError(f"{engine}'s check is Debian's own, which carries on over several nights, "
                        "so it can only be turned on or off")
    if engine not in SYNCED and any(value is not None for value in syncing.values()):
        raise FnasError(f"{engine} has no parity sync or spin-down to set")
    if every is not None and every not in CHOICES[engine]:
        raise FnasError(f"{engine}'s checks run " + " or ".join(CHOICES[engine]))
    if sync_hour is not None and not 0 <= sync_hour <= 23:
        raise FnasError("the sync hour is 0 to 23")
    if spin_down is not None and spin_down not in SPIN_DOWN:
        raise FnasError("disks spin down after " + ", ".join(map(str, SPIN_DOWN[1:])) + " minutes, or 0 for never")
    if week is not None and week not in WEEKS:
        raise FnasError("the week is 1 to 4: the first to fourth of that day in the month")
    if day is not None and day not in DAYS:
        raise FnasError("the day is one of " + ", ".join(DAYS))
    if hour is not None and not 0 <= hour <= 23:
        raise FnasError("the hour is 0 to 23")
    current = settings()
    if enabled is not None:
        current[engine]["enabled"] = enabled
    current[engine].update({key: value for key, value in {**timing, **syncing}.items() if value is not None})
    write_json(SETTINGS_FILE, current)
    problems = apply()
    if problems:
        raise FnasError("; ".join(problems))


def restore(saved):
    """Settings brought back from a pool after a reinstall."""
    if isinstance(saved, dict):
        write_json(SETTINGS_FILE, {engine: checked(engine, saved[engine]) for engine in DEFAULTS
                                   if isinstance(saved.get(engine), dict)})
    return apply()


def scrub_all(engine, scheduled=False):
    """Check every healthy writable pool on one engine, as its timer or Check now
    does. Returns what was started, and what wasn't and why."""
    return every_pool(engine, lambda name: pools.ENGINES[engine].scrub_pool(name, scheduled=scheduled))


def sync_all(engine):
    """Bring parity up to date on every healthy pool of an engine, as its nightly timer does."""
    return every_pool(engine, pools.ENGINES[engine].sync_pool)


def every_pool(engine, action):
    started, skipped = [], []
    for pool in pools.list_pools():
        if pool["engine"] != engine:
            continue
        if pool["readonly"] or pool["health"] != "ONLINE":
            skipped.append(f"{pool['name']} is {'read-only' if pool['readonly'] else pool['health'].lower()}")
            continue
        try:
            started.append(action(pool["name"]))
        except FnasError as error:
            skipped.append(f"{pool['name']}: {error}")
    return started, skipped


def status():
    """Every engine's row in Settings, built or not."""
    current = settings()
    found = pools.list_pools()
    rows = []
    for engine in pools.engines():
        name = engine["name"]
        row = {"engine": name, "title": engine["title"], "available": engine["available"],
               "reason": engine["reason"], "timed": name in TIMED,
               "pools": [p["name"] for p in found if p["engine"] == name]}
        if name in current and engine["available"]:
            row.update(current[name])
            row["description"] = describe(name)
            row["next"] = next_run(name) if current[name]["enabled"] else None
            row["choices"] = list(CHOICES.get(name, ()))
            if name in SYNCED:
                row["sync_next"] = next_run(name, SYNC_TIMER.format(engine=name)) if current[name]["sync"] else None
                row["spin_down_choices"] = list(SPIN_DOWN)
        rows.append(row)
    return rows
