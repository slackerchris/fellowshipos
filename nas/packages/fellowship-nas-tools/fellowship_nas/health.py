"""The hourly check: finding problems and alerting once per problem, and the
whole picture the Health app shows."""
import contextlib
import glob
import os
import re
import time

from .common import DAY, HOUR, STATE_DIR, read_json, run, when, write_json
from . import alerts, backup, config, disks, pools, services, shares, snapshots, storage_checks, usage
from .engines import zfs


CHECK_STATE = os.path.join(STATE_DIR, "check.json")
REMIND_AFTER = DAY
FULL_WARNING = 80
FULL_HIGH = 90
MODULES_DIR = "/lib/modules"
SYSTEM_WARNING = 85
SYSTEM_HIGH = 95
REBOOT_FLAG = "/run/reboot-required"
THERMAL_GLOB = "/sys/class/thermal/thermal_zone*/temp"
# Long enough for the kernel's counters to move, short enough not to be noticed
CPU_SAMPLE = 0.12
# Units that may be stopped without anything being wrong
MAY_STOP = {"smartmontools.service"}


def finding(key, severity, title, body):
    return {"key": key, "severity": severity, "title": title, "message": body}


def pool_findings(pools):
    found = []
    for pool in pools:
        percent = pool["allocated"] * 100 // pool["size"]
        if percent >= FULL_WARNING:
            severity = "high" if percent >= FULL_HIGH else "medium"
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
        bad = [disk for disk in pools.ENGINES[pool["engine"]].member_disks(status)
               if disk["state"] not in ("ONLINE",)]
        named = "; ".join(f"{describe_disk(d['path'], slots)} is {d['state'].lower()}" for d in bad)
        # Missing a disk is broken too: the pool has lost its protection
        severity = "critical"
        found.append(finding(f"pool-health-{pool['name']}-{pool['health']}", severity,
                             f"Pool {pool['name']} is {pool['health'].lower()}",
                             (named + ". " if named else "")
                             + "Replace the disk from Storage in the desktop."))
    return found


def engine_of(name):
    """The engine that owns a pool, so the shared checks ask it rather than
    assuming ZFS."""
    return pools.ENGINES[pools.find_pool(name)["engine"]]


def scrub_findings(names, now):
    found = []
    for pool in names:
        engine = engine_of(pool)
        kind = pools.find_pool(pool)["engine"]
        damaged = engine.damage(pool)
        if damaged:
            found.append(finding(f"damage-{pool}", "medium", *damaged))
        # With automatic checks turned off in Settings, an old check is a choice
        allowed = storage_checks.max_age(kind)
        if allowed is None:
            continue
        last = engine.last_scrub(pool)
        # A pool never checked falls due a check's interval after it was made
        age = now - (last or engine.created(pool))
        if age > allowed:
            word = "been checked" if engine.CHECK_WORD == "check" else "been scrubbed"
            since = (f"The last {engine.CHECK_WORD} was {age // DAY:.0f} days ago" if last
                     else f"It hasn't had a {engine.CHECK_WORD} since it was made, {age // DAY:.0f} days ago")
            found.append(finding(f"scrub-old-{pool}", "low", f"Pool {pool} hasn't {word}",
                                 f"{since}. {storage_checks.sentence(kind)} "
                                 f"Start one now from Storage, or sudo fnas pool scrub {pool}"))
    return found


def checks_off(found_pools):
    """The engines, among these pools', whose automatic checks are turned off."""
    return sorted({pools.engine_title(p["engine"]) for p in found_pools
                   if storage_checks.max_age(p["engine"]) is None})


def checks_passed(found_pools):
    off = checks_off(found_pools)
    return "Every pool checked on schedule" + (
        f"; automatic checks turned off in Settings for {' and '.join(off)}" if off else "")


def checks_not_applicable(found_pools):
    """With every engine's automatic checks turned off, there is no schedule to be late for."""
    if found_pools and len(checks_off(found_pools)) == len({p["engine"] for p in found_pools}):
        return "Automatic checks are turned off in Settings → Storage checks"
    return ""


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
            found.append(finding(f"snapshot-old-{share['name']}", "critical",
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
        return [finding("backup-overdue", "medium", "Backup overdue",
                        f"The last backup to {settings['target']} was "
                        f"{(now - last) / DAY:.0f} days ago.")]
    return []


def newest_kernel():
    kernels = os.listdir(MODULES_DIR)
    return max(kernels, key=lambda k: [int(n) for n in re.findall(r"\d+", k)], default=None)


def kernel_findings():
    kernel = newest_kernel()
    if kernel and not glob.glob(os.path.join(MODULES_DIR, kernel, "updates/dkms/zfs.ko*")):
        return [finding("zfs-module", "high", f"No ZFS module for kernel {kernel}",
                        "Don't reboot into it: the pool wouldn't mount. "
                        "See /var/lib/dkms/zfs/*/build/make.log")]
    return []


def config_findings():
    return [finding(f"config-{pool}", "low", f"Pool {pool} has settings from another install",
                    f"Settings aren't being saved to {pool}. Run fnas config restore {pool}, or "
                    f"fnas config adopt {pool} to replace them.") for pool in config.foreign_config_pools()]


def system_disk_findings():
    used = system_disk()
    percent = used["used"] * 100 // used["size"]
    if percent < SYSTEM_WARNING:
        return []
    severity = "high" if percent >= SYSTEM_HIGH else "medium"
    return [finding(f"system-full-{severity}", severity, f"The system disk is {percent}% full",
                    "Updates and logs need room. Give the system more space in Settings, or clear old logs.")]


def service_findings():
    """NAS services that should run but don't, and sharing servers shares rely on."""
    found = []
    for service in services.system_services():
        if service["state"] in ("stopped", "failed") and service["unit"] not in MAY_STOP:
            found.append(finding(f"service-{service['unit']}", "critical", f"{service['title']} isn't running",
                                 f"{service['detail']}. See why with: journalctl -u {service['unit']}"))
    for protocol in services.protocols():
        if protocol["shares"] and protocol["state"] in ("stopped", "failed", "missing"):
            found.append(finding(f"service-{protocol['name']}", "critical", f"{protocol['title']} isn't running",
                                 f"{'Share' if len(protocol['shares']) == 1 else 'Shares'} "
                                 f"{', '.join(protocol['shares'])} can't be reached over {protocol['title']}."))
    return found


def disk_findings(found_disks):
    """SMART verdicts, shown but not alerted, since smartd alerts itself."""
    found = []
    for disk in found_disks:
        name = describe_disk(disk["by_id"] or disk["path"], {})
        if disk["verdict"] == "failing":
            found.append(finding(f"disk-{disk['path']}", "critical", f"{name} is failing", "; ".join(disk["notes"])))
        elif disk["verdict"] == "watch":
            found.append(finding(f"disk-{disk['path']}", "medium", f"Keep an eye on {name}", "; ".join(disk["notes"])))
    return found


def check(key, section, title, findings, passed, not_applicable="", alerting=True):
    """One check as the desktop lists it: passed, the most severe problem it found, or
    not applicable and why."""
    state = ("na" if not_applicable and not findings
             else max((f["severity"] for f in findings), key=alerts.RANK.get) if findings else "passed")
    detail = not_applicable if state == "na" else "; ".join(f["title"] for f in findings) or passed
    return {"key": key, "section": section, "title": title, "state": state, "detail": detail,
            "problems": findings, "alerting": alerting}


def all_checks(now, found_disks=None):
    found = pools.list_pools()
    writable = [p["name"] for p in found if not p["readonly"]]
    checkable = [p for p in found if not p["readonly"]]
    zfs_shares = [s for s in shares.list_shares() if s["engine"] == "zfs"]
    no_pool = "" if found else "No pool yet"
    count = f"{len(found)} pool" + ("" if len(found) == 1 else "s")
    settings = read_json(backup.BACKUP_FILE)
    state = read_json(backup.BACKUP_STATE, {})
    zfs_reason = zfs.unavailable()
    kernel = newest_kernel()
    if found_disks is None:
        found_disks = [disks.disk_health(d) for d in disks.list_disks() if d["kind"] == "disk"]
    reporting = [d for d in found_disks if d["verdict"] != "unknown"]
    return [
        check("pool-state", "Pools", "Every pool has all its disks", health_findings(found),
              f"{count}, every disk present", no_pool),
        check("pool-space", "Pools", "Pools have free space", pool_findings(found),
              f"Every pool under {FULL_WARNING}% full", no_pool),
        check("scrub", "Storage", "Storage checks", scrub_findings(writable, now), checks_passed(checkable),
              checks_not_applicable(checkable) or ("" if checkable else no_pool or "No writable pool to check")),
        check("snapshots", "Protection", "Snapshots are being taken", snapshot_findings(zfs_shares, now),
              "Every share's newest snapshot is on time", "" if zfs_shares else "No shares yet"),
        check("backup", "Protection", "Backups are up to date", backup_findings(now),
              "Last backup " + (when(state["last_success"]) if state.get("last_success") else "not run yet"),
              "" if settings else "No backup job. Set one up in Snapshots and backup"),
        check("settings", "Protection", "Settings are saved on every pool", config_findings(),
              "Saved after every change", no_pool),
        check("system-disk", "System", "The system disk has room", system_disk_findings(),
              f"{system_disk()['used'] * 100 // system_disk()['size']}% full"),
        check("zfs-module", "System", "ZFS works with the newest kernel", [] if zfs_reason else kernel_findings(),
              f"Module built for {kernel}", f"ZFS can't be used here: {zfs_reason}" if zfs_reason else ""),
        check("services", "Services", "Services are running", service_findings(),
              "Every service the NAS needs is running"),
        check("disks", "Disks", "Disks report good health", disk_findings(found_disks),
              f"{len(reporting)} disk" + ("" if len(reporting) == 1 else "s") + " healthy",
              "" if reporting else "No disk here reports SMART data, as in most virtual machines",
              alerting=False),
    ]


def find_problems(now, checks=None):
    return [problem for item in (checks or all_checks(now)) if item["alerting"] for problem in item["problems"]]


def system_disk():
    stats = os.statvfs("/")
    return {"size": stats.f_blocks * stats.f_frsize,
            "used": (stats.f_blocks - stats.f_bfree) * stats.f_frsize}


def read_lines(path):
    with open(path) as file:
        return file.read().splitlines()


def updates_waiting():
    """Packages apt would upgrade, as of the last package list refresh."""
    result = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade", check=False)
    return sum(line.startswith("Inst ") for line in result.stdout.splitlines()) if result.returncode == 0 else None


def memory_status():
    memory = dict(line.split(":", 1) for line in read_lines("/proc/meminfo"))
    kilobytes = lambda key: int(memory[key].split()[0]) * 1024
    return {"total": kilobytes("MemTotal"), "available": kilobytes("MemAvailable")}


def load_status():
    return [float(n) for n in read_lines("/proc/loadavg")[0].split()[:3]]


def temperature_status():
    temperatures = []
    for path in glob.glob(THERMAL_GLOB):
        with contextlib.suppress(OSError, ValueError):
            temperatures.append(int(read_lines(path)[0]) / 1000)
    return max(temperatures) if temperatures else None


def cpu_usage():
    """The share of the processors busy just now, as a percentage. The kernel's
    counters only ever grow, so two reads a moment apart are what says
    anything about now."""
    def ticks():
        values = [int(n) for n in read_lines("/proc/stat")[0].split()[1:]]
        # user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice
        return sum(values), sum(values[3:5])

    total_before, idle_before = ticks()
    time.sleep(CPU_SAMPLE)
    total_after, idle_after = ticks()
    elapsed = total_after - total_before
    busy = elapsed - (idle_after - idle_before)
    return round(busy * 100 / elapsed) if elapsed > 0 else 0


def system_status():
    return {"disk": system_disk(),
            "memory": memory_status(),
            "load": load_status(),
            "cpu": cpu_usage(),
            "cpus": os.cpu_count(),
            "uptime": int(float(read_lines("/proc/uptime")[0].split()[0])),
            "temperature": temperature_status(),
            "updates": updates_waiting(),
            "reboot": os.path.exists(REBOOT_FLAG),
            "kernel": os.uname().release,
            "newest_kernel": newest_kernel()}


def system_sample():
    """The few numbers the hourly check records, so Health can draw them over
    time. Deliberately cheap: no package check, no SMART."""
    memory = memory_status()
    disk = system_disk()
    return {"memory_used": memory["total"] - memory["available"], "memory_total": memory["total"],
            "load": load_status()[0], "disk_used": disk["used"], "disk_size": disk["size"],
            "temperature": temperature_status()}


def newest_snapshot(pool, found_shares):
    """The newest snapshot of any share on the pool, scheduled or taken by a backup."""
    taken = [s["created"] for share in found_shares if share["pool"] == pool
             for s in snapshots.list_snapshots(share["dataset"])]
    return max(taken, default=None)


def pool_details(found, histories=None):
    settings = read_json(backup.BACKUP_FILE)
    state = read_json(backup.BACKUP_STATE, {})
    found_shares = shares.list_shares()
    histories = histories or {}
    shown = []
    for pool in found:
        zfs_pool = pool["engine"] == "zfs"
        backed_up = settings and settings["source"] == pool["name"]
        recorded = histories.get(pool["name"], {})
        shown.append({**pool,
                      "engine_title": pools.engine_title(pool["engine"]),
                      "shares": len([s for s in found_shares if s["pool"] == pool["name"]]),
                      "last_scrub": pools.ENGINES[pool["engine"]].last_scrub(pool["name"]) or None,
                      "last_snapshot": newest_snapshot(pool["name"], found_shares) if zfs_pool else None,
                      "history": {"hourly": recorded.get("hourly", []), "daily": recorded.get("daily", [])},
                      "projection": usage.projection(recorded.get("daily") or recorded.get("hourly") or []),
                      "backup": ({"target": settings["target"], "last": state.get("last_success"),
                                  "error": state.get("error")} if backed_up else None)})
    return shown


def health_report(now):
    """Everything the Health app shows, worked out now and sending nothing."""
    found_disks = [disks.disk_health(d) for d in disks.list_disks() if d["kind"] == "disk"]
    checks = all_checks(now, found_disks)
    log = read_json(alerts.ALERT_LOG, [])
    histories = usage.all_history()
    return {"time": int(now),
            "hourly": {"last": int(os.path.getmtime(CHECK_STATE)) if os.path.exists(CHECK_STATE) else None,
                       "every": HOUR},
            "checks": checks,
            "problems": sorted(find_problems(now, checks) + [p for c in checks if not c["alerting"] for p in c["problems"]],
                               key=lambda p: -alerts.RANK[p["severity"]]),
            "services": services.system_services(),
            "protocols": services.protocols(),
            "system": system_status(),
            "history": {"system": histories.get(usage.SYSTEM_KEY, {"hourly": [], "daily": []})},
            "pools": pool_details(pools.list_pools(), histories),
            "disks": found_disks,
            "alerts": {"channels": alerts.alert_channels(), "last": log[-1] if log else None}}


def run_checks():
    """Send an alert for each new problem, a reminder each day, and a note when one clears."""
    config.after_change()
    shares.empty_recycle_bins()
    usage.record(pools.list_pools(), system=system_sample())
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
