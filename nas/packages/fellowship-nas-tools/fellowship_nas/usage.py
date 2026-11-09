"""How full each pool has been over time, and how the system itself has been
running, recorded by the hourly check, plus how soon a pool fills at that rate."""
import os
import time

from .common import DAY, HOUR, STATE_DIR, read_json, write_json


USAGE_FILE = os.path.join(STATE_DIR, "usage.json")
KEEP_HOURLY = 7 * DAY
KEEP_DAILY = 400 * DAY
# A trend needs this much history before it says anything
MIN_SPAN = 3 * DAY
# The system's own history sits beside the pools. A pool can't be called this,
# since a pool name holds no "$", so one can't be mistaken for the other.
SYSTEM_KEY = "$system"
# Each system point is [time, memory used, memory total, 1-minute load, system
# disk used, system disk size, hottest sensor or None], in this order
SYSTEM_FIELDS = ("memory_used", "memory_total", "load", "disk_used", "disk_size", "temperature")


def add(entry, point, now):
    """Add one point: an hour of points for a week, one a day for a year."""
    hourly = [p for p in entry["hourly"] if now - p[0] < KEEP_HOURLY]
    # The newest point stands in until the hour is up, so checks run by hand don't add points
    if len(hourly) > 1 and hourly[-1][0] - hourly[-2][0] < HOUR - 60:
        hourly.pop()
    entry["hourly"] = hourly + [point]
    today = time.localtime(now)[:3]
    entry["daily"] = [p for p in entry["daily"] if now - p[0] < KEEP_DAILY and time.localtime(p[0])[:3] != today]
    entry["daily"].append(point)


def record(found_pools, now=None, system=None):
    """Add each pool's usage, and the system's when asked, as the hourly check does."""
    now = int(now or time.time())
    history = read_json(USAGE_FILE, {})
    for pool in found_pools:
        add(history.setdefault(pool["name"], {"hourly": [], "daily": []}),
            [now, pool["allocated"], pool["size"]], now)
    if system is not None:
        add(history.setdefault(SYSTEM_KEY, {"hourly": [], "daily": []}),
            [now] + [system.get(field) for field in SYSTEM_FIELDS], now)
    write_json(USAGE_FILE, history)


def history(name):
    return read_json(USAGE_FILE, {}).get(name, {"hourly": [], "daily": []})


def all_history():
    """Everything recorded: each pool under its name, the system under SYSTEM_KEY."""
    return read_json(USAGE_FILE, {})


def system_history():
    return history(SYSTEM_KEY)


def projection(points):
    """When the pool fills at the rate of its history, by a straight-line fit.
    Returns {"days": n} or {"reason": why there's no projection}."""
    if len(points) < 3 or points[-1][0] - points[0][0] < MIN_SPAN:
        return {"reason": "Not enough history yet: a projection needs three days of it"}
    times = [p[0] for p in points]
    used = [p[1] for p in points]
    mean_t = sum(times) / len(times)
    mean_u = sum(used) / len(used)
    spread = sum((t - mean_t) ** 2 for t in times)
    slope = sum((t - mean_t) * (u - mean_u) for t, u in zip(times, used)) / spread
    if slope <= 0:
        return {"reason": "Not filling up: usage has held steady or fallen"}
    size, latest = points[-1][2], points[-1][1]
    return {"days": round((size - latest) / slope / DAY), "per_day": int(slope * DAY)}
