"""The mdadm engine: traditional Linux RAID 1, 5, 6 and 10, on ext4 or XFS.

An mdadm pool is one array, mounted at /mnt/NAME like every other engine's
pool. It has no snapshots, so a share on one can't offer Previous Versions.

What is here so far is what can be built without a machine to build an array
on: finding the arrays a machine has, what they are made of, how full they
are, and which layouts a set of disks can carry. Creating, growing, replacing
and importing come next.
"""
import os
import re

from ..common import FnasError, run
from .. import disks, pools


LAYOUTS = ("mirror", "raid5", "raid6", "raid10")
# Disks per group that make sense for each layout. A wide RAID 5 takes days to
# rebuild, RAID 6 gets slower past a dozen, and RAID 10 counts in pairs
WIDTHS = {"mirror": (2, 2), "raid5": (3, 8), "raid6": (4, 12), "raid10": (4, 16)}
PARITY = {"raid5": 1, "raid6": 2}
MAX_SPARES = 2
FILESYSTEMS = ("xfs", "ext4")
# XFS is the recommendation: it grows an array in place and mends itself
RECOMMENDED_FILESYSTEM = "xfs"
MDSTAT = "/proc/mdstat"
# Where the kernel lists mounts, so a read-only array is reported as one
MOUNTS = "/proc/mounts"
# "md127 : active raid5 sdd1[3] sdc1[1] sdb1[0]"
ARRAY_LINE = re.compile(r"^(md\w+)\s*:\s*(\S+)\s*(.*)$")
# "sdd1[3]" or "sdc1[1](F)" for a disk that has failed
MEMBER = re.compile(r"^(\S+)\[(\d+)\](?:\((.)\))?$")
# "  [=>...................]  recovery = 8.3% (65/781) finish=120.4min"
PROGRESS = re.compile(r"^\s*\[[=>.]+\]\s*(\w+)\s*=\s*([\d.]+)%")


def mdadm_installed():
    """Whether the mdadm command is on this machine."""
    return os.path.exists("/sbin/mdadm") or os.path.exists("/usr/sbin/mdadm")


def md_driver():
    """Whether this kernel has the md driver, which lists its arrays in
    /proc/mdstat."""
    return os.path.exists(MDSTAT)


def unavailable():
    """Why mdadm can't be used on this machine, or "" when it can."""
    if not mdadm_installed():
        return "mdadm isn't installed"
    if not md_driver():
        return "this kernel has no md driver"
    return ""


def read(path):
    with open(path) as file:
        return file.read()


def arrays():
    """Every array the kernel has, keyed by device, from /proc/mdstat."""
    found = {}
    current = None
    for line in read(MDSTAT).splitlines():
        match = ARRAY_LINE.match(line)
        if match:
            current = {"device": f"/dev/{match.group(1)}", "state": match.group(2),
                       "members": [], "sync": None}
            for token in match.group(3).split():
                member = MEMBER.match(token)
                if member:
                    current["members"].append({
                        "device": f"/dev/{member.group(1)}", "slot": int(member.group(2)),
                        "state": "failed" if member.group(3) == "F" else "in sync"})
            found[current["device"]] = current
            continue
        progress = PROGRESS.match(line) if current else None
        if progress:
            current["sync"] = {"function": progress.group(1).lower(),
                               "state": "running",
                               "progress": float(progress.group(2)) / 100}
    return found


def scan_arrays():
    """Every array mdadm knows the parts of, with the name it was made under."""
    found = []
    for line in run("mdadm", "--detail", "--scan", check=False).stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != "ARRAY":
            continue
        array = {"device": fields[1], "metadata": "", "name": "", "uuid": ""}
        for field in fields[2:]:
            key, _, value = field.partition("=")
            if key.lower() in ("metadata", "name", "uuid"):
                array[key.lower()] = value
        # mdadm writes the name it was given as host:name
        array["name"] = array["name"].split(":", 1)[-1] or os.path.basename(array["device"])
        found.append(array)
    return found


def array_names():
    return {array["device"]: array["name"] for array in scan_arrays()}


def pool_devices():
    """Map each device in an array to its pool's name."""
    devices = {}
    names = array_names()
    links = disks.by_id_links(partitions=True)
    for device, array in arrays().items():
        name = names.get(device) or os.path.basename(device)
        for member in array["members"]:
            devices[member["device"]] = name
            if member["device"] in links:
                devices[links[member["device"]]] = name
    return devices


def readonly(path):
    """Whether this mount is read-only, as /proc/mounts records it."""
    for line in read(MOUNTS).splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[1] == path:
            return "ro" in fields[3].split(",")
    return False


def health(array):
    """The word every engine uses for a pool's state."""
    if array["state"] != "active" or any(m["state"] == "failed" for m in array["members"]):
        return "DEGRADED"
    if array["sync"] and array["sync"]["function"] in ("recovery", "resync"):
        return "DEGRADED"
    return "ONLINE"


def list_pools():
    found = []
    names = array_names()
    for device, array in arrays().items():
        name = names.get(device) or os.path.basename(device)
        path = pools.mountpoint(name)
        if not os.path.ismount(path):
            continue
        stats = os.statvfs(path)
        size = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        found.append({"name": name, "size": size, "allocated": size - free, "free": free,
                      "health": health(array), "readonly": readonly(path)})
    return found


def scan_pools():
    """Arrays on the disks that no pool is using yet, which means either one
    made on another machine or one this system hasn't mounted."""
    mounted = {pool["name"] for pool in list_pools()}
    found = []
    for array in scan_arrays():
        if array["name"] in mounted:
            continue
        found.append({"pool": array["name"], "state": "ready", "uuid": array["uuid"],
                      "comment": f"an array with {array['metadata']} metadata"})
    return found


def group_usable(layout, sizes):
    """What one group of these disks holds once the layout has its parity."""
    smallest = min(sizes)
    if layout == "mirror":
        return smallest
    if layout == "raid10":
        return smallest * (len(sizes) // 2)
    return smallest * (len(sizes) - PARITY.get(layout, 0))


def plan(layout, sizes, width=None, spares=0):
    """Split disks, given by size, into groups and spares. Similar sizes go
    together so less space is lost, and the largest disks become the spares,
    since a spare can only stand in for a disk no bigger than itself.
    Returns ([[index, ...], ...], [index, ...])."""
    if layout not in LAYOUTS:
        raise FnasError(f"no such layout: {layout} (mdadm offers {', '.join(LAYOUTS)})")
    if not 0 <= spares <= MAX_SPARES:
        raise FnasError(f"an array takes 0 to {MAX_SPARES} spares")
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    members, spare = order[:len(order) - spares], order[len(order) - spares:]
    lowest, highest = WIDTHS[layout]
    if width is None:
        width = len(members)
    if not lowest <= width <= highest:
        raise FnasError(f"{layout} groups take {lowest} to {highest} disks")
    if len(members) < lowest:
        raise FnasError(f"{layout} groups take {lowest} to {highest} disks")
    if len(members) % width:
        raise FnasError(f"{len(members)} disks don't divide into {layout} groups of {width}")
    groups = [members[i:i + width] for i in range(0, len(members), width)]
    return groups, spare


def recommended(count):
    """The layout most people should use for this many disks: RAID 5 up to
    six, and RAID 6 above that, where a second failure during a rebuild is a
    real risk."""
    if count < 2:
        return None
    if count == 2:
        return ("mirror", 2, 0)
    if count <= 6:
        return ("raid5", 3 if count == 3 else count, 0)
    for spares in range(MAX_SPARES + 1):
        lowest, highest = WIDTHS["raid6"]
        for width in range(min(highest, count - spares), lowest - 1, -1):
            if (count - spares) % width == 0:
                return ("raid6", width, spares)
    return None


def layouts(sizes):
    """Every layout these disks fit, with its usable space and how many failed
    disks each group survives. The recommended one comes first."""
    count = len(sizes)
    best = recommended(count) if count else None
    options = []
    for spares in range(min(MAX_SPARES, count - 1) + 1):
        for layout in LAYOUTS:
            lowest, highest = WIDTHS[layout]
            for width in range(lowest, min(highest, count - spares) + 1):
                if (count - spares) % width:
                    continue
                groups, spare = plan(layout, sizes, width, spares)
                group_sizes = [[sizes[i] for i in group] for group in groups]
                options.append({
                    "layout": layout, "width": width, "groups": len(groups), "spares": spares,
                    "usable": sum(group_usable(layout, g) for g in group_sizes),
                    "unused": sum(sum(g) - min(g) * len(g) for g in group_sizes),
                    "survives": 1 if layout in ("mirror", "raid10") else PARITY.get(layout, 0),
                    "recommended": (layout, width, spares) == best,
                })
    options.sort(key=lambda o: (not o["recommended"], -o["survives"], -o["usable"], o["spares"]))
    return options
