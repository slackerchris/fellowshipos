"""The nightly backup of one pool's shares to another pool."""
import os
import re
import time

from .common import DAY, FnasError, STATE_DIR, locked, read_json, run, write_json
from . import health, pools, shares, snapshots
from .engines import zfs


BACKUP_FILE = "/etc/fellowshipos/nas-backup.json"
BACKUP_STATE = os.path.join(STATE_DIR, "backup.json")
BACKUP_LOCK = os.path.join(STATE_DIR, "backup.lock")
BACKUP_DATASET = "fellowship-backup"
BACKUP_SHARE = re.compile(rf"^[^/]+/{BACKUP_DATASET}/([^/]+)/shares/([^/]+)$")
BACKUP_MAX_AGE = 2 * DAY


def backup_root(target):
    return f"{target}/{BACKUP_DATASET}"


def set_backup(source, target):
    if source == target:
        raise FnasError("a backup needs a second pool, or losing one loses both")
    writable = pools.writable_pools()
    for pool in (source, target):
        if pool not in writable:
            raise FnasError(f"no writable imported pool: {pool}")
    root = backup_root(target)
    if run("zfs", "list", "-H", "-o", "name", root, check=False).returncode != 0:
        # Never mounted, so nothing can change a backup or share it by accident
        run("zfs", "create", "-o", "mountpoint=none", root)
    write_json(BACKUP_FILE, {"source": source, "target": target, "since": int(time.time())})


def sync_backup(settings):
    """Copy the shares to the backup pool, returning what went wrong, if anything."""
    imported = [p["name"] for p in pools.list_pools()]
    for pool in (settings["source"], settings["target"]):
        if pool not in imported:
            return f"pool {pool} isn't imported"
    target = f"{backup_root(settings['target'])}/{settings['source']}"
    run("zfs", "create", "-p", target)
    # Syncoid's own snapshot stays until the next run, so the backup pool can be
    # away for longer than Sanoid keeps anything. Properties go too, so a restored
    # share is still a share.
    result = run("syncoid", "--recursive", "--quiet", "--identifier=fellowship", "--sendoptions=p",
                 f"{settings['source']}/shares", f"{target}/shares", check=False, timeout=None)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "syncoid failed"
    # Share settings live in the source pool's config folder, so a copy goes
    # with the data for a restore to use
    folder = zfs.config_dir(settings["target"])
    if folder:
        source = pools.find_pool(settings["source"])
        write_json(os.path.join(folder, f"backup-of-{settings['source']}.json"),
                   {"shares": shares.saved_settings(source)})
    return None


def run_backup():
    settings = read_json(BACKUP_FILE)
    if not settings:
        raise FnasError("no backup job: run fnas backup set POOL TARGET")
    with locked(BACKUP_LOCK, wait=False):
        error = sync_backup(settings)
    state = read_json(BACKUP_STATE, {})
    state.update(last_attempt=int(time.time()), error=error)
    if not error:
        state["last_success"] = state["last_attempt"]
    write_json(BACKUP_STATE, state)
    health.run_checks()
    if error:
        raise FnasError(error)


def backup_status():
    settings = read_json(BACKUP_FILE)
    return {"job": settings, **read_json(BACKUP_STATE, {})} if settings else {"job": None}


def backup_target(pool):
    if pool:
        return pool
    settings = read_json(BACKUP_FILE)
    if not settings:
        raise FnasError("no backup job, so name the backup pool with --from")
    return settings["target"]


def list_backups(pool):
    root = backup_root(backup_target(pool))
    result = run("zfs", "list", "-H", "-o", "name", "-r", root, check=False)
    if result.returncode != 0:
        raise FnasError(f"no backups in {root}")
    backups = []
    for dataset in result.stdout.split():
        match = BACKUP_SHARE.match(dataset)
        if match:
            backups.append({"name": match.group(2), "pool": match.group(1), "dataset": dataset,
                            "snapshots": len(snapshots.list_snapshots(dataset))})
    return backups


def restore_backup(name, from_pool, to_pool):
    backups = [b for b in list_backups(from_pool) if b["name"] == name]
    if not backups:
        raise FnasError(f"no backup of {name}")
    if len(backups) > 1:
        raise FnasError(f"{name} has backups from more than one pool")
    pool = to_pool or backups[0]["pool"]
    if pool not in pools.writable_pools():
        raise FnasError(f"no writable imported pool: {pool}")
    if name.lower() in [s["name"].lower() for s in shares.list_shares()]:
        raise FnasError(f"a share named {name} already exists, and a restore never replaces one")
    dataset = f"{pool}/shares/{name}"
    if run("zfs", "list", "-H", "-o", "name", dataset, check=False).returncode == 0:
        raise FnasError(f"{dataset} already exists and isn't a share")
    run("syncoid", "--quiet", "--no-sync-snap", "--sendoptions=p", backups[0]["dataset"], dataset, timeout=None)
    folder = zfs.config_dir(backup_target(from_pool))
    saved = read_json(os.path.join(folder, f"backup-of-{backups[0]['pool']}.json"), {}) if folder else {}
    settings = shares.check_settings(name, (saved.get("shares") or {}).get(name, {}))
    if not settings and name not in [s["name"] for s in shares.list_shares()]:
        # Nothing says how it was shared, so it comes back private
        settings = shares.new_settings("chosen", {"users": {}, "groups": {}}, False, False)
    if settings:
        shares.save_settings(pool, {"name": name, **settings})
    shares.write_config()
