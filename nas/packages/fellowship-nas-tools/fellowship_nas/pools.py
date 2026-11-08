"""Pools on every engine. Each engine in ENGINES offers the same functions, and
everything outside the engines asks this module about pools."""
import os
import re

from .common import FnasError
from .engines import zfs


POOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED_POOL_PREFIXES = ("mirror", "raidz", "draid", "spare", "log", "cache")
ENGINES = {"zfs": zfs}
# Every pool is mounted at /mnt/NAME, on every engine, so no pool name can
# collide with a folder the system uses
ROOT = "/mnt"


def check_pool_name(name):
    if not POOL_NAME.match(name):
        raise FnasError("pool names use lowercase letters, numbers, - and _, "
                        "start with a letter, and are at most 32 characters")
    if name.startswith(RESERVED_POOL_PREFIXES):
        raise FnasError(f"{name} starts with a word ZFS reserves")


def engines():
    """Each engine, and why it can't be used here if it can't."""
    shown = []
    for name, engine in ENGINES.items():
        reason = engine.unavailable()
        shown.append({"name": name, "available": not reason, "reason": reason})
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
    if name not in ENGINES:
        raise FnasError(f"no such engine: {name}")
    reason = ENGINES[name].unavailable()
    if reason:
        raise FnasError(f"{name} can't be used here: {reason}")
    return ENGINES[name]


def mountpoint(name):
    return os.path.join(ROOT, name)

