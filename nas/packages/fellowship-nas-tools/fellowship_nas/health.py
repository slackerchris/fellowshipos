"""The hourly check: finding problems and alerting once per problem."""
import glob
import os
import re
import time

from .common import DAY, HOUR, STATE_DIR, read_json, run, write_json
from . import alerts, backup, config, disks, pools, shares, snapshots
from .engines import zfs


CHECK_STATE = os.path.join(STATE_DIR, "check.json")
REMIND_AFTER = DAY
FULL_WARNING = 80
FULL_CRITICAL = 90
# Debian scrubs on the second Sunday of each month, so up to 35 days apart
SCRUB_MAX_AGE = 40 * DAY
MODULES_DIR = "/lib/modules"


def finding(key, severity, title, body):
    return {"key": key, "severity": severity, "title": title, "message": body}


def pool_findings(pools):
    found = []
    for pool in pools:
        percent = pool["allocated"] * 100 // pool["size"]
        if percent >= FULL_WARNING:
            severity = "critical" if percent >= FULL_CRITICAL else "warning"
            found.append(finding(f"pool-full-{severity}-{pool['name']}", severity,
                                 f"Pool {pool['name']} is {percent}% full",
                                 "ZFS slows down as a pool fills. Free some space or add disks."))
    return found


def describe_disk(path, slots):
    """A disk as someone holding a screwdriver would look for it."""
    whole = disks.whole_disk(path) if path else ""
    bay = slots.get(whole, {}).get("bay")
    # The by-id name carries the model and serial, as in ata-WDC_WD80EFAX_VAG1234
    name = re.sub(r"-part\d+$", "", os.path.basename(path)).split("-", 1)[-1] if path else ""
    return ", ".join(part for part in (bay, f"disk {name}" if name else "") if part) or "a missing disk"


def health_findings(found_pools):
    """Pools that have lost a disk, naming each one by bay or serial number."""
    found = []
    slots = disks.bays()
    for pool in found_pools:
        if pool["health"] == "ONLINE":
            continue
        status = pools.ENGINES[pool["engine"]].pool_status(pool["name"])
        bad = [disk for group in status["groups"] for disk in zfs.leaves(group)
               if disk["state"] not in ("ONLINE",)]
        named = "; ".join(f"{describe_disk(d['path'], slots)} is {d['state'].lower()}" for d in bad)
        severity = "warning" if pool["health"] == "DEGRADED" else "critical"
        found.append(finding(f"pool-health-{pool['name']}-{pool['health']}", severity,
                             f"Pool {pool['name']} is {pool['health'].lower()}",
                             (named + ". " if named else "")
                             + "Replace the disk from Storage in the desktop."))
    return found


def scrub_findings(pools, now):
    found = []
    for pool in pools:
        age = now - zfs.last_scrub(pool)
        if age > SCRUB_MAX_AGE:
            found.append(finding(f"scrub-old-{pool}", "warning", f"Pool {pool} hasn't been scrubbed",
                                 f"The last scrub was {age // DAY:.0f} days ago. "
                                 f"Start one with: sudo zpool scrub {pool}"))
    return found


def snapshot_findings(shares, now):
    found = []
    if run("systemctl", "is-failed", "--quiet", "sanoid.service", "sanoid-prune.service",
           check=False).returncode == 0:
        found.append(finding("sanoid-failed", "critical", "Snapshots failed",
                             "See what went wrong with: journalctl -u sanoid.service"))
    for share in shares:
        ages = [snapshots.SNAPSHOT_MAX_AGE[period] for period, kept in share["policy"].items() if kept]
        if share["readonly"] or not ages:
            continue
        taken = [s["created"] for s in snapshots.list_snapshots(share["dataset"])
                 if s["name"].startswith("autosnap_")]
        newest = max(taken) if taken else zfs.created_at(share["dataset"])
        if now - newest > min(ages):
            found.append(finding(f"snapshot-old-{share['name']}", "warning",
                                 f"Snapshots of {share['name']} have stopped",
                                 f"The newest is {(now - newest) / HOUR:.0f} hours old."))
    return found


def backup_findings(now):
    settings = read_json(backup.BACKUP_FILE)
    if not settings:
        return []
    state = read_json(backup.BACKUP_STATE, {})
    if state.get("error"):
        return [finding("backup-failed", "critical", "Backup failed", state["error"])]
    last = state.get("last_success", settings["since"])
    if now - last > backup.BACKUP_MAX_AGE:
        return [finding("backup-overdue", "warning", "Backup overdue",
                        f"The last backup to {settings['target']} was "
                        f"{(now - last) / DAY:.0f} days ago.")]
    return []


def newest_kernel():
    kernels = os.listdir(MODULES_DIR)
    return max(kernels, key=lambda k: [int(n) for n in re.findall(r"\d+", k)], default=None)


def kernel_findings():
    kernel = newest_kernel()
    if kernel and not glob.glob(os.path.join(MODULES_DIR, kernel, "updates/dkms/zfs.ko*")):
        return [finding("zfs-module", "critical", f"No ZFS module for kernel {kernel}",
                        "Don't reboot into it: the pool wouldn't mount. "
                        "See /var/lib/dkms/zfs/*/build/make.log")]
    return []


def config_findings():
    return [finding(f"config-{pool}", "warning", f"Pool {pool} has settings from another install",
                    f"Settings aren't being saved to {pool}. Run fnas config restore {pool}, or "
                    f"fnas config adopt {pool} to replace them.") for pool in config.foreign_config_pools()]


def find_problems(now):
    found = pools.list_pools()
    writable = [p["name"] for p in found if not p["readonly"] and p["engine"] == "zfs"]
    return (pool_findings(found) + health_findings(found) + scrub_findings(writable, now)
            + snapshot_findings([s for s in shares.list_shares() if s["engine"] == "zfs"], now) + backup_findings(now)
            + kernel_findings() + config_findings())


def run_checks():
    """Send an alert for each new problem, a reminder each day, and a note when one clears."""
    config.after_change()
    now = time.time()
    found = find_problems(now)
    previous = read_json(CHECK_STATE, {})
    current = {}
    for item in found:
        entry = previous.get(item["key"])
        if not entry or now - entry["sent"] >= REMIND_AFTER:
            alerts.send_alert(item["severity"], item["title"], item["message"])
            entry = {"sent": now, "title": item["title"]}
        current[item["key"]] = entry
    for key in previous.keys() - current.keys():
        alerts.send_alert("info", f"Resolved: {previous[key]['title']}", "This no longer needs attention.")
    write_json(CHECK_STATE, current)
    alerts.retry_alerts(now)
    return found
