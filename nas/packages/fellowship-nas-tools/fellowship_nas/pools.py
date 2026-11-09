"""Pools on every engine. Each engine in ENGINES offers the same functions, and
everything outside the engines asks this module about pools."""
import os
import re

from .common import FnasError
from .engines import mdadm, zfs


POOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ENGINES = {"zfs": zfs, "mdadm": mdadm}
# Every engine in the plan, built or not, so the desktop can show them all:
# name, title, and the plan phase that builds it
CATALOGUE = [("zfs", "ZFS", None), ("mergerfs", "Unraid-style", 11), ("mdadm", "Traditional RAID", None),
             ("btrfs", "Btrfs", 14)]
# Every pool is mounted at /mnt/NAME, on every engine, so no pool name can
# collide with a folder the system uses
ROOT = "/mnt"


def check_pool_name(name):
    """The name every engine must accept. An engine may be stricter: ZFS, for
    one, can't use a name that starts with a word it reserves."""
    if not POOL_NAME.match(name):
        raise FnasError("pool names use lowercase letters, numbers, - and _, "
                        "start with a letter, and are at most 32 characters")


def engines():
    """Every engine, and why it can't be used here if it can't."""
    shown = []
    for name, title, phase in CATALOGUE:
        reason = ENGINES[name].unavailable() if name in ENGINES else f"Not built yet (Phase {phase})"
        shown.append({"name": name, "title": title, "available": not reason, "reason": reason})
    return shown


def usable_engines():
    return {name: engine for name, engine in ENGINES.items() if not engine.unavailable()}


def list_pools():
    return [{**pool, "engine": name} for name, engine in usable_engines().items()
            for pool in engine.list_pools()]


def find_pool(name):
    for pool in list_pools():
        if pool["name"] == name:
            return pool
    raise FnasError(f"no imported pool: {name}")


def engine_for(name):
    return ENGINES[find_pool(name)["engine"]]


def writable_pools():
    return [p["name"] for p in list_pools() if not p["readonly"]]


def scan_pools():
    return [{**pool, "engine": name} for name, engine in usable_engines().items()
            for pool in engine.scan_pools()]


def pool_devices():
    """Map each device in an imported pool, on any engine, to its pool's name."""
    devices = {}
    for engine in usable_engines().values():
        devices.update(engine.pool_devices())
    return devices


def check_new_name(name):
    check_pool_name(name)
    if name in [p["name"] for p in list_pools()] + [p.get("pool") for p in scan_pools()]:
        raise FnasError(f"a pool named {name} already exists")
    # An empty folder, such as one an exported pool left behind, is fine
    path = os.path.join(ROOT, name)
    if os.path.lexists(path) and (not os.path.isdir(path) or os.path.islink(path)
                                  or os.path.ismount(path) or os.listdir(path)):
        raise FnasError(f"{path} is already in use, so choose another name")


def engine_named(name):
    found = next((engine for engine in engines() if engine["name"] == name), None)
    if not found:
        raise FnasError(f"no such engine: {name}")
    if not found["available"]:
        raise FnasError(f"{found['title']} can't be used here: {found['reason']}")
    return ENGINES[name]


def mountpoint(name):
    return os.path.join(ROOT, name)

