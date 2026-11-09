"""How full each pool has been over time, recorded by the hourly check, and
how soon it fills at that rate."""
import os
import time

from .common import DAY, HOUR, STATE_DIR, read_json, write_json


USAGE_FILE = os.path.join(STATE_DIR, "usage.json")
KEEP_HOURLY = 7 * DAY
KEEP_DAILY = 400 * DAY
# A trend needs this much history before it says anything
MIN_SPAN = 3 * DAY


def record(found_pools, now=None):
    """Add each pool's usage: one point per hour for a week, one per day for a year."""
    now = int(now or time.time())
    history = read_json(USAGE_FILE, {})
    for pool in found_pools:
        entry = history.setdefault(pool["name"], {"hourly": [], "daily": []})
        point = [now, pool["allocated"], pool["size"]]
        hourly = [p for p in entry["hourly"] if now - p[0] < KEEP_HOURLY]
        # The newest point stands in until the hour is up, so checks run by hand don't add points
        if len(hourly) > 1 and hourly[-1][0] - hourly[-2][0] < HOUR - 60:
            hourly.pop()
        entry["hourly"] = hourly + [point]
        today = time.localtime(now)[:3]
        entry["daily"] = [p for p in entry["daily"] if now - p[0] < KEEP_DAILY and time.localtime(p[0])[:3] != today]
        entry["daily"].append(point)
    write_json(USAGE_FILE, history)


def history(name):
    return read_json(USAGE_FILE, {}).get(name, {"hourly": [], "daily": []})


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
