"""The ZFS engine: pools built from groups of disks (single, mirror, RAIDZ1-3),
with hot spares, grown by adding a group or one disk at a time."""
import datetime
import json
import os
import re

from ..common import FnasError, run
from .. import disks, jobs, pools, shares


POOL_PROPERTIES = ["ashift=12"]
DATASET_PROPERTIES = ["compression=lz4", "atime=off", "xattr=sa",
                      "acltype=posixacl", "dnodesize=auto"]
DEFAULT_DATASETS = ["shares", "home", "backups", "fellowship-config"]
# Debian's monthly cron job scrubs every pool unless this says not to. fnas
# scrubs on the schedule in Settings instead, so only one of them runs.
DEBIAN_SCRUB = "org.debian:periodic-scrub"
LAYOUTS = ("single", "mirror", "raidz1", "raidz2", "raidz3")
# Disks per group that make sense for each layout; wider RAIDZ groups take
# days to resilver
WIDTHS = {"single": (1, 1), "mirror": (2, 3), "raidz1": (3, 8), "raidz2": (4, 12), "raidz3": (5, 16)}
PARITY = {"raidz1": 1, "raidz2": 2, "raidz3": 3}
MAX_SPARES = 2
# ZFS reads a dataset's first word as something of its own, so a pool can't
# start with one of these. Other engines have no such rule.
RESERVED_POOL_PREFIXES = ("mirror", "raidz", "draid", "spare", "log", "cache")
SCRUB_DONE = re.compile(r"scrub (?:repaired|canceled).* on (\w{3} \w{3} +\d+ [\d:]+ \d{4})")
# What this engine calls its monthly check, for Health's wording
CHECK_WORD = "scrub"
# How the desktop presents a pool on this engine, so it never asks which engine
# it is. {pool} and {group} are filled in where they're shown.
PRESENTATION = {
    "snapshots": True,
    "backup": True,
    "compresses": True,
    # Damage is mended as it's found, so there is no separate repair to start
    "repair": False,
    "grow": ["group", "expand", "mirror", "spare"],
    "grow_text": {},
    "expand_text": "Add one disk to {group}",
    "check_button": "Check for damage",
    "check_name": "check for damage",
    "check_text": ("Check for damage (a scrub) reads every block on {pool} and checks it against its checksum. "
                   "Damaged blocks are repaired from another copy or parity where the pool has one, and reported "
                   "where it doesn't. Shares stay usable while it runs."),
    "errors_label": "Read, write and checksum errors",
    "repair_notice": "",
    "repair_text": "",
    "replace_text": "ZFS copies its data onto the new disk; the pool stays in use meanwhile.",
    "filesystems": [],
}
MODULE_DIR = "/sys/module/zfs"
SIZE_ONLY = re.compile(r"^\s*(mirror|raidz) contains devices of different sizes$")
CONFIG_DATASET = "fellowship-config"
# Where share settings lived before they moved to shares.json
LEGACY_MODE = "org.fellowshipos:share-mode"
LEGACY_SMB = "org.fellowshipos:smb"
LEGACY_POLICY = "org.fellowshipos:snapshots"


def unavailable():
    """Why ZFS can't be used on this machine, or "" when it can."""
    if os.path.isdir(MODULE_DIR):
        return ""
    run("modprobe", "zfs", check=False)
    if os.path.isdir(MODULE_DIR):
        return ""
    if "enabled" in run("mokutil", "--sb-state", check=False).stdout:
        return "Secure Boot is on, and the ZFS module isn't signed for it"
    return "the ZFS kernel module isn't installed for this kernel"


def pool_devices():
    """Map each device in an imported pool to its pool's name."""
    devices = {}
    pool = None
    for line in run("zpool", "list", "-vHPL").stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if not line[0].isspace():
            pool = fields[0]
        elif fields[0].startswith("/dev/"):
            devices[fields[0]] = pool
    return devices


def scan_pools():
    """Return pools that could be imported, with what ZFS says about each."""
    result = run("zpool", "import", "-d", disks.BY_ID_DIR, check=False)
    found = []
    key = None
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*(pool|id|state|status|action|comment): ?(.*)$", line)
        if match:
            key = match.group(1)
            if key == "pool":
                found.append({})
            if found:
                found[-1][key] = match.group(2).strip()
        elif re.match(r"^\s*(config|see):", line):
            key = None
        elif key and found and line.strip():
            found[-1][key] = f"{found[-1].get(key, '')} {line.strip()}".strip()
    if result.returncode != 0 and not found and "no pools available" not in result.stderr:
        raise FnasError(result.stderr.strip() or "zpool import failed")
    return found


def list_pools():
    fields = ("name", "size", "allocated", "free", "health", "readonly")
    output = run("zpool", "list", "-Hp", "-o", ",".join(fields)).stdout
    found = [dict(zip(fields, line.split("\t"))) for line in output.splitlines()]
    # zpool counts parity and mirror copies, so a RAIDZ2 of four disks reads as
    # twice the room it has. The pool's top dataset says what files can use.
    usable = {}
    for line in run("zfs", "list", "-Hp", "-d", "0", "-o", "name,used,avail", check=False).stdout.splitlines():
        name, used, avail = line.split("\t")
        usable[name] = (int(used), int(avail))
    for pool in found:
        for key in ("size", "allocated", "free"):
            pool[key] = int(pool[key])
        if pool["name"] in usable:
            used, avail = usable[pool["name"]]
            pool.update(size=used + avail, allocated=used, free=avail)
        pool["readonly"] = pool["readonly"] == "on"
    return found


def group_usable(layout, sizes):
    smallest = min(sizes)
    if layout == "mirror":
        return smallest
    return smallest * (len(sizes) - PARITY.get(layout, 0))


def plan(layout, sizes, width=None, spares=0):
    """Split disks, given by size, into groups and spares. Similar sizes go
    together so less space is lost, and the largest disks become the spares,
    since a spare can only stand in for a disk no bigger than itself.
    Returns ([[index, ...], ...], [index, ...])."""
    if layout not in LAYOUTS:
        raise FnasError(f"no such layout: {layout} (ZFS offers {', '.join(LAYOUTS)})")
    if not 0 <= spares <= MAX_SPARES:
        raise FnasError(f"a pool takes 0 to {MAX_SPARES} spares")
    if layout == "single" and (len(sizes) != 1 or spares):
        raise FnasError("single takes exactly 1 disk, with no spares")
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    members, spare = order[:len(order) - spares], order[len(order) - spares:]
    lowest, highest = WIDTHS[layout]
    if width is None:
        width = 2 if layout == "mirror" else len(members)
    if not lowest <= width <= highest:
        raise FnasError(f"{layout} groups take {lowest} to {highest} disks")
    if len(members) < lowest:
        raise FnasError(f"{layout} groups take {lowest} to {highest} disks")
    if len(members) % width:
        raise FnasError(f"{len(members)} disks don't divide into {layout} groups of {width}")
    groups = [members[i:i + width] for i in range(0, len(members), width)]
    return groups, spare


def recommended(count):
    """The layout most people should use for this many disks: RAIDZ2 in groups
    of 6 to 10, with a spare or two when the count doesn't divide evenly."""
    if count <= 3:
        return {1: ("single", 1, 0), 2: ("mirror", 2, 0), 3: ("raidz1", 3, 0)}[count]
    if count <= 10:
        return ("raidz2", count, 0)
    for spares in range(MAX_SPARES + 1):
        for width in range(10, 5, -1):
            if (count - spares) % width == 0:
                return ("raidz2", width, spares)
    return None


def layouts(sizes):
    """Every layout these disks fit, with its usable space and how many failed
    disks each group survives. The recommended one comes first."""
    count = len(sizes)
    best = recommended(count) if count else None
    options = []
    for spares in range(min(MAX_SPARES, count - 1) + 1):
        for layout in LAYOUTS:
            if layout == "single" and (spares or count != 1):
                continue
            lowest, highest = WIDTHS[layout]
            for width in range(lowest, highest + 1):
                # A third copy costs a whole disk for little; RAIDZ2 does better
                if (count - spares) % width or (layout == "mirror" and width > 2):
                    continue
                groups, spare = plan(layout, sizes, width, spares)
                group_sizes = [[sizes[i] for i in group] for group in groups]
                usable = sum(group_usable(layout, g) for g in group_sizes)
                options.append({
                    "layout": layout, "width": width, "groups": len(groups), "spares": spares,
                    "usable": usable,
                    "unused": sum(sum(g) - min(g) * len(g) for g in group_sizes),
                    "survives": width - 1 if layout == "mirror" else PARITY.get(layout, 0),
                    "recommended": (layout, width, spares) == best,
                })
    options.sort(key=lambda o: (not o["recommended"], -o["survives"], -o["usable"], o["spares"]))
    return options


def vdev_args(layout, groups, spare_paths):
    args = []
    for group in groups:
        args += group if layout == "single" else [layout, *group]
    if spare_paths:
        args += ["spare", *spare_paths]
    return args


def run_accepting_sizes(*command):
    """Run zpool create, add or attach. ZFS wants -f for a group of disks of
    different sizes; that is passed only when it is the one objection, after
    the layout has shown how much space goes unused. Anything else ZFS
    refuses, such as a disk in use or groups that don't match, stays refused."""
    result = run(*command, check=False)
    if result.returncode == 0:
        return
    lines = result.stderr.strip().splitlines()
    if "use '-f' to override the following errors:" in lines:
        reasons = lines[lines.index("use '-f' to override the following errors:") + 1:]
        if reasons and all(SIZE_ONLY.match(reason) for reason in reasons):
            run(command[0], command[1], "-f", *command[2:])
            return
    raise FnasError(result.stderr.strip() or f"{command[0]} {command[1]} failed")


def check_reserved_name(name):
    """ZFS's own extra rule on a pool's name, on top of the shared ones."""
    if name.startswith(RESERVED_POOL_PREFIXES):
        raise FnasError(f"{name} starts with a word ZFS reserves")


def create_pool(name, layout, wanted, confirm, wipe_disks, width=None, spares=0, filesystem=None):
    if filesystem:
        raise FnasError("--filesystem is for arrays: a ZFS pool brings its own filesystem")
    chosen = disks.choose(wanted, wipe_disks)
    groups, spare = plan(layout, [d["size"] for d in chosen], width, spares)
    check_reserved_name(name)
    pools.check_new_name(name)
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    command = ["zpool", "create", "-m", pools.mountpoint(name)]
    for prop in POOL_PROPERTIES:
        command += ["-o", prop]
    for prop in DATASET_PROPERTIES + [f"{DEBIAN_SCRUB}=disable"]:
        command += ["-O", prop]
    paths = [d["by_id"] for d in chosen]
    run_accepting_sizes(*command, name, *vdev_args(layout, [[paths[i] for i in g] for g in groups],
                                                   [paths[i] for i in spare]))
    for dataset in DEFAULT_DATASETS:
        run("zfs", "create", f"{name}/{dataset}")


def number(value):
    return int(value) if value not in (None, "", "-") else 0


def describe_vdev(vdev):
    # RAIDZ groups say only "raidz"; their name says the parity, as in raidz2-0
    kind = re.match(r"(raidz[123]|mirror|replacing|spare)-\d+$", vdev.get("name", ""))
    shown = {"name": vdev.get("name", ""), "type": kind.group(1) if kind else vdev.get("vdev_type", ""),
             "state": vdev.get("state", ""), "path": vdev.get("path", ""),
             "size": number(vdev.get("phys_space") or vdev.get("total_space")),
             "errors": sum(number(vdev.get(k)) for k in ("read_errors", "write_errors", "checksum_errors")),
             "note": vdev.get("aux") or vdev.get("was", "") or ""}
    children = vdev.get("vdevs") or {}
    if children:
        shown["disks"] = [describe_vdev(child) for child in children.values()]
    return shown


def scan_progress(stats):
    if not stats:
        return None
    total = number(stats.get("to_examine"))
    done = number(stats.get("issued") or stats.get("examined"))
    return {"function": stats.get("function", "").lower(), "state": stats.get("state", "").lower(),
            "started": number(stats.get("start_time")), "ended": number(stats.get("end_time")),
            "errors": number(stats.get("errors")),
            "progress": min(done / total, 1.0) if total else 0.0}


def expand_progress(stats):
    if not stats:
        return None
    total = number(stats.get("to_reflow"))
    return {"group": stats.get("expanding_vdev", ""), "state": stats.get("state", "").lower(),
            "started": number(stats.get("start_time")), "ended": number(stats.get("end_time")),
            "progress": min(number(stats.get("reflowed")) / total, 1.0) if total else 0.0}


def pool_status(name):
    """A pool's groups, disks, spares and any scrub, resilver or expansion."""
    pools.find_pool(name)
    data = json.loads(run("zpool", "status", "-j", "--json-int", "-P", name).stdout)
    pool = data["pools"][name]
    root = next(iter(pool["vdevs"].values()))
    groups = []
    for vdev in (root.get("vdevs") or {}).values():
        shown = describe_vdev(vdev)
        if shown["type"] in ("disk", "file"):
            # A disk on its own is a group of one
            shown = {"name": shown["name"], "type": "single", "state": shown["state"], "path": "",
                     "size": shown["size"], "errors": 0, "note": "", "disks": [shown]}
        groups.append(shown)
    return {"name": name, "state": pool.get("state", ""), "status": pool.get("status", ""),
            "action": pool.get("action", ""), "groups": groups,
            "spares": [describe_vdev(v) for v in (pool.get("spares") or {}).values()],
            "scan": scan_progress(pool.get("scan_stats")),
            "expand": expand_progress(pool.get("raidz_expand_stats")),
            "expandable": feature_ready(name, "raidz_expansion")}


def feature_ready(name, feature):
    value = run("zpool", "get", "-H", "-o", "value", f"feature@{feature}", name, check=False).stdout.strip()
    return value in ("enabled", "active")


def member_paths(status):
    return [d["path"] for g in status["groups"] for d in leaves(g)] + [s["path"] for s in status["spares"]]


def member_disks(status):
    """Every disk a pool has, without the spares."""
    return [disk for group in status["groups"] for disk in leaves(group)]


def repair_pool(name, confirm=""):
    """ZFS mends a bad block from its other copies as soon as it reads it, so
    there is no separate repair to run."""
    raise FnasError("ZFS mends its own blocks from their checksums: a scrub is all it needs")


def last_scrub(name):
    """When a scrub last finished, in seconds since the epoch, or 0."""
    return scrub_finished(name) or 0


def leaves(vdev):
    if "disks" not in vdev:
        return [vdev]
    return [leaf for child in vdev["disks"] for leaf in leaves(child)]


def find_group(status, name):
    for group in status["groups"]:
        if group["name"] == name or (group["type"] == "single" and group["disks"][0]["path"] == name):
            return group
        for child in group.get("disks", []):
            if name in (child["name"], child["path"]):
                return group
    raise FnasError(f"no such group or disk in {status['name']}: {name}")


def grow(name, how, wanted, confirm, wipe_disks, target=None):
    """Add disks to a pool: a new group like the others, one more disk in a
    RAIDZ group, a mirror for a disk on its own or in a mirror, or spares.
    Returns a job when ZFS keeps working after the command returns."""
    status = pool_status(name)
    chosen = disks.choose(wanted, wipe_disks)
    paths = [d["by_id"] for d in chosen]
    data_groups = status["groups"]
    if how == "group":
        kinds = {(g["type"], len(g["disks"])) for g in data_groups}
        if len(kinds) != 1:
            raise FnasError(f"{name}'s groups differ, so there is no single kind of group to add")
        layout, width = kinds.pop()
        if layout == "single":
            raise FnasError("a second disk on its own would leave nothing able to survive a failure: "
                            "add a mirror to the disk instead")
        if len(chosen) != width:
            raise FnasError(f"each {layout} group in {name} has {width} disks, so add {width}")
        command = ["zpool", "add", name, layout, *paths]
    elif how == "spare":
        if all(g["type"] == "single" for g in data_groups):
            raise FnasError("a spare stands in for a failed disk in a mirror or RAIDZ group, "
                            "and this pool has none")
        biggest = max(d["size"] for g in data_groups for d in leaves(g))
        for disk in chosen:
            if disk["size"] < biggest:
                raise FnasError(f"{disk['by_id']} is smaller than the pool's largest disk, "
                                "so it couldn't stand in for it")
        command = ["zpool", "add", name, "spare", *paths]
    elif how in ("expand", "mirror"):
        if len(chosen) != 1 or not target:
            raise FnasError(f"{how} takes one disk and the --target it joins")
        group = find_group(status, target)
        if how == "expand":
            if not group["type"].startswith("raidz"):
                raise FnasError(f"{target} isn't a RAIDZ group")
            if not status["expandable"]:
                raise FnasError(f"{name} was made by an older ZFS: upgrade the pool to add single disks")
            joins = group["name"]
        else:
            if group["type"] not in ("single", "mirror"):
                raise FnasError(f"{target} isn't a disk on its own or in a mirror")
            joins = group["disks"][0]["path"]
        if chosen[0]["size"] < min(d["size"] for d in leaves(group)):
            raise FnasError(f"{chosen[0]['by_id']} is smaller than the disks in {group['name']}")
        command = ["zpool", "attach", name, joins, paths[0]]
    else:
        raise FnasError(f"no such way to grow a pool: {how}")
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    run_accepting_sizes(*command)
    if how == "expand":
        return jobs.start("expand", f"Adding a disk to {name}", pool=name)
    if how == "mirror":
        return jobs.start("resilver", f"Mirroring a disk in {name}", pool=name)
    return None


def replace(name, old, wanted, confirm, wipe_disks):
    """Swap a failed or failing disk for a new one, and follow the resilver."""
    status = pool_status(name)
    members = {d["path"]: d for g in status["groups"] for d in leaves(g)}
    by_name = {d["name"]: d for d in members.values()}
    disk = members.get(old) or by_name.get(old)
    if not disk:
        raise FnasError(f"{old} isn't a disk in {name}'s groups")
    chosen = disks.choose([wanted], wipe_disks)
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    # A missing disk has no path any more, only the name ZFS gave it
    run("zpool", "replace", name, disk["path"] or disk["name"], chosen[0]["by_id"])
    return jobs.start("resilver", f"Replacing a disk in {name}", pool=name)


def remove_spare(name, spare):
    status = pool_status(name)
    for disk in status["spares"]:
        if spare in (disk["path"], disk["name"]):
            if disk["state"] != "AVAIL":
                raise FnasError(f"{spare} is standing in for a failed disk: replace that disk first")
            run("zpool", "remove", name, disk["path"])
            return
    raise FnasError(f"{spare} isn't a spare in {name}")


def config_dir(name):
    result = run("zfs", "get", "-H", "-o", "value", "mountpoint", f"{name}/{CONFIG_DATASET}", check=False)
    path = result.stdout.strip()
    return path if result.returncode == 0 and os.path.isdir(path) else None


def legacy_policy(value):
    parts = value.split(",")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return dict(zip(("hourly", "daily", "monthly"), map(int, parts)))
    return None


def share_volumes(name):
    """Each dataset in POOL/shares, with any settings left on it by an older fnas."""
    fields = ("name", "mountpoint", "readonly", "sharenfs", LEGACY_MODE, LEGACY_SMB, LEGACY_POLICY)
    result = run("zfs", "list", "-H", "-d", "1", "-t", "filesystem", "-o", ",".join(fields),
                 f"{name}/shares", check=False)
    volumes = {}
    for line in result.stdout.splitlines():
        dataset, path, readonly, sharenfs, mode, smb, policy = line.split("\t")
        if dataset.count("/") != 2:
            continue
        volume = {"dataset": dataset, "path": path, "readonly": readonly == "on"}
        if mode in ("shared", "matched"):
            volume["legacy"] = {"mode": mode, "nfs": sharenfs != "off", "smb": smb == "on",
                                "policy": legacy_policy(policy) or {"hourly": 24, "daily": 30, "monthly": 6}}
        volumes[dataset.split("/")[2]] = volume
    return volumes


def clear_legacy(name, share):
    dataset = f"{name}/shares/{share}"
    run("zfs", "set", "sharenfs=off", dataset)
    for prop in (LEGACY_MODE, LEGACY_SMB, LEGACY_POLICY):
        run("zfs", "inherit", prop, dataset)


def create_volume(name, share):
    dataset = f"{name}/shares/{share}"
    if run("zfs", "list", "-H", "-o", "name", dataset, check=False).returncode == 0:
        raise FnasError(f"{dataset} already exists and isn't a share")
    run("zfs", "create", "-p", dataset)
    return run("zfs", "get", "-H", "-o", "value", "mountpoint", dataset).stdout.strip()


def volume_snapshots(share):
    return run("zfs", "list", "-H", "-t", "snapshot", "-o", "name", "-r", share["dataset"]).stdout.split()


def destroy_volume(share):
    # A large share takes as long as it takes; the typed confirmation already
    # covers the decision
    run("zfs", "destroy", "-r", share["dataset"], timeout=None)


def import_pool(pool, readonly, force=False):
    found = [p for p in scan_pools() if pool in (p.get("pool"), p.get("id"))]
    if not found:
        raise FnasError(f"no importable pool: {pool}")
    status = found[0].get("status", "")
    name = found[0]["pool"]
    command = ["zpool", "import", "-d", disks.BY_ID_DIR]
    if "currently imported by another system" in status:
        # Multihost protection: another machine has it imported right now
        raise FnasError(f"{pool} is in use by another system; export it there first")
    if "last accessed by another system" in status:
        # Normal after a reinstall: the old system never exported it
        if not force:
            raise FnasError(f"{pool} was last used by another system (normal after a "
                            "reinstall): make sure that system is off, then pass --force")
        command.append("-f")
    if readonly:
        command += ["-o", "readonly=on"]
    # Imported without mounting, so a pool made elsewhere can be moved under /mnt first
    run(*command, "-N", pool)
    value, source = run("zfs", "get", "-H", "-o", "value,source", "mountpoint", name).stdout.split("\t")
    if source.strip() == "default":
        if readonly:
            # Nothing can be written to it, so it is imported again with /mnt in
            # front of every path, for as long as it stays imported
            run("zpool", "export", name)
            run(*command, "-N", "-o", f"altroot={pools.ROOT}", pool)
        else:
            run("zfs", "set", f"mountpoint={pools.mountpoint(name)}", name)
    run("zfs", "mount", "-a")
    if not readonly:
        take_over_scrubs(name)
    shares.migrate()
    shares.write_config()


def take_over_scrubs(name):
    """Stop Debian's cron job scrubbing this pool, since fnas does it on its own
    schedule. The setting travels with the pool, which only Debian reads."""
    run("zfs", "set", f"{DEBIAN_SCRUB}=disable", name, check=False)


def export_pool(name):
    pools.find_pool(name)
    # NFS and Samba hold the shares open, so they let go first
    shares.write_config(leaving=name)
    result = run("zpool", "export", name, check=False)
    if result.returncode != 0:
        shares.write_config()
        raise FnasError(f"{result.stderr.strip()}: disconnect any clients and try again")


def created_at(dataset):
    return int(run("zfs", "get", "-Hp", "-o", "value", "creation", dataset).stdout)


BACKUP_DATASET = "fellowship-backup"


def space(name):
    """What uses a pool's space, in parts that add up to what's used: each share's
    files, every snapshot, backups of other pools, and the rest."""
    fields = ("name", "used", "usedbysnapshots", "usedbydataset", "usedbychildren", "available")
    output = run("zfs", "list", "-Hp", "-t", "filesystem", "-o", ",".join(fields), "-r", name).stdout
    rows = [dict(zip(fields, line.split("\t"))) for line in output.splitlines()]
    for row in rows:
        for key in fields[1:]:
            row[key] = int(row[key])
    root = next(row for row in rows if row["name"] == name)
    backups_root = f"{name}/{BACKUP_DATASET}"
    in_backups = lambda row: row["name"] == backups_root or row["name"].startswith(backups_root + "/")
    shares = [{"name": row["name"].split("/")[2], "files": row["usedbydataset"] + row["usedbychildren"],
               "snapshots": row["usedbysnapshots"]}
              for row in rows if row["name"].count("/") == 2 and row["name"].startswith(f"{name}/shares/")]
    snapshots = sum(row["usedbysnapshots"] for row in rows if not in_backups(row))
    backups = sum(row["used"] for row in rows if row["name"] == backups_root)
    files = sum(share["files"] for share in shares)
    return {"used": root["used"], "free": root["available"], "shares": sorted(shares, key=lambda s: -s["files"]),
            "parts": {"shares": files, "snapshots": snapshots, "backups": backups,
                      "other": max(root["used"] - files - snapshots - backups, 0)}}


def pool_details(name):
    """Everything the pool's details view shows, besides its history."""
    pools.find_pool(name)
    ratio, logical = run("zfs", "get", "-Hp", "-o", "value", "compressratio,logicalused", name).stdout.split()
    size, allocated, fragmentation = run("zpool", "list", "-Hp", "-o", "size,alloc,frag", name).stdout.split()
    status = pool_status(name)
    return {"name": name, "size": int(size), "allocated": int(allocated),
            "fragmentation": None if fragmentation == "-" else int(fragmentation.rstrip("%")),
            "compression": float(ratio.rstrip("x")), "logical": int(logical),
            "created": created_at(name), "last_scrub": scrub_finished(name),
            "errors": sum(disk["errors"] for group in status["groups"] for disk in leaves(group)),
            "space": space(name)}


def scrub_finished(pool):
    """When a scrub last finished, or None if none has."""
    match = SCRUB_DONE.search(run("zpool", "status", pool).stdout)
    if match:
        stamp = " ".join(match.group(1).split())
        return datetime.datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y").timestamp()
    return None


def last_scrub(pool):
    """When a scrub last finished, or when the pool was made if it never has."""
    return scrub_finished(pool) or created_at(pool)


def created(pool):
    return created_at(pool)


def damage(pool):
    """Errors ZFS counted on a pool's disks while every disk stays in it, as a
    Health finding's title and message, or None. A pool that lost a disk is
    reported by its state instead."""
    count = sum(disk["errors"] for group in pool_status(pool)["groups"] for disk in leaves(group))
    if not count:
        return None
    return (f"Pool {pool}: {count} read, write or checksum errors",
            "ZFS mended what it could from the pool's other copies, but a disk returned bad data. "
            f"See which one with sudo fnas pool status {pool}, run a scrub, and replace the disk "
            "if the errors come back.")


def problems(pool, now):
    """Anything else Health should say about this pool; its state and damage cover ZFS."""
    return []


def scrub_pool(name, scheduled=False):
    pools.find_pool(name)
    run("zpool", "scrub", name)
    return jobs.start("scrub", f"Checking every block on {name}", pool=name)


def follow(job):
    """Report a scrub, resilver or expansion until ZFS finishes it. Returns an
    error, or None."""
    status = pool_status(job["pool"])
    if job["kind"] == "expand":
        work = status["expand"]
    else:
        work = status["scan"]
        if work and work["function"] != job["kind"]:
            work = None
    if work and work["state"] == "scanning":
        return jobs.RUNNING, work["progress"]
    if job["kind"] == "scrub" and work and work["errors"]:
        return f"the scrub found {work['errors']} errors: see the pool's status", 1.0
    if work and work["state"] == "canceled":
        return f"the {job['kind']} was stopped", 1.0
    if job["kind"] == "resilver" and status["state"] != "ONLINE":
        # The resilver finished but a disk is still missing or failing
        return f"{job['pool']} is still {status['state'].lower()}", 1.0
    return None, 1.0
