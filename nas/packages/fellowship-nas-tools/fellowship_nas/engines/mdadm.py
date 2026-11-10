"""The mdadm engine: traditional Linux RAID 1, 5, 6 and 10, on XFS or ext4.

An mdadm pool is one array of every disk it has, plus any spares, mounted at
/mnt/NAME like every other engine's pool. It has no snapshots, so a share on
one can't offer Previous Versions, and the backup engine doesn't cover it.

RAID 10 uses the standard layout with two copies of every block (n2). A check
only reports the blocks that don't match; repairing them is a separate action
someone asks for, because a repair can keep the wrong copy.
"""
import os
import re
import time

from ..common import STATE_DIR, FnasError, read_json, run, write_json
from .. import alerts, disks, jobs, pools, shares


LAYOUTS = ("mirror", "raid5", "raid6", "raid10")
# What the kernel calls each layout
LEVELS = {"mirror": 1, "raid5": 5, "raid6": 6, "raid10": 10}
# An array is one group of every disk it has, so the width is the disk count
WIDTHS = {"mirror": (2, 2), "raid5": (3, 16), "raid6": (4, 16), "raid10": (4, 16)}
PARITY = {"raid5": 1, "raid6": 2}
MAX_SPARES = 2
FILESYSTEMS = ("xfs", "ext4")
# What this engine calls its monthly check, for Health's wording
CHECK_WORD = "check"
# When Debian's mdcheck timers check every array by themselves
CHECK_SCHEDULE = "from the first Sunday of each month, six hours a night until done"
# XFS is the recommendation: it grows while mounted, and mends itself
RECOMMENDED_FILESYSTEM = "xfs"
MKFS = {"xfs": ("mkfs.xfs", "-f"), "ext4": ("mkfs.ext4", "-F")}
GROW_FILESYSTEM = {"xfs": "xfs_growfs", "ext4": "resize2fs"}
# The folders every pool carries, so the rest of fnas finds what it expects
DEFAULT_FOLDERS = ("shares", "home", "backup", "fellowship-config")
MDSTAT = "/proc/mdstat"
MOUNTS = "/proc/mounts"
FSTAB = "/etc/fstab"
MDADM_CONF = "/etc/mdadm/mdadm.conf"
SYS_BLOCK = "/sys/block"
CHECKS = os.path.join(STATE_DIR, "checks.json")
# Arrays Debian's mdcheck is part way through, kept in CHECKS under a key no
# pool name can take, since a pool name holds no "$"
WATCHING = "$mdcheck"
# Where mdcheck keeps its place in a check it paused for the night
MDCHECK_DIR = "/var/lib/mdcheck"
# mdadm --monitor runs this for each array event
MD_PROGRAM = "/usr/lib/fellowship-nas/md-alert"
# "md127 : active raid5 sdd1[3] sdc1[1] sdb1[0]"
ARRAY_LINE = re.compile(r"^(md\w+)\s*:\s*(\S+)\s*(.*)$")
# "sdd1[3]" or "sdc1[1](F)" for a disk that has failed
MEMBER = re.compile(r"^(\S+)\[(\d+)\](?:\((.)\))?$")
# "  [=>...................]  recovery = 8.3% (65/781) finish=120.4min"
PROGRESS = re.compile(r"^\s*\[[=>.]+\]\s*(\w+)\s*=\s*([\d.]+)%")
# "       0       8       17        0      active sync   /dev/sdb1"
DETAIL_DISK = re.compile(r"^\s*(?P<slot>\d+|-)\s+\d+\s+\d+\s+(?P<raid>\d+|-)\s+"
                         r"(?P<state>[a-z][a-z ]*?)\s*(?P<path>/dev/\S*)?$")
# What the kernel is doing, and what we call that
SYNC_ACTIONS = {"recovery": "resilver", "resync": "resilver", "reshape": "expand",
                "check": "scrub", "repair": "scrub"}


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


def rows_of(path):
    try:
        with open(path) as file:
            return file.readlines()
    except FileNotFoundError:
        return []


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
            current["sync"] = {"function": progress.group(1).lower(), "state": "running",
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


def resolve(device):
    """The kernel's name for an array: mdadm says /dev/md/NAME, /proc/mdstat says /dev/md127."""
    return os.path.realpath(device)


def array_names():
    return {resolve(array["device"]): array["name"] for array in scan_arrays()}


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


def array_device(name):
    """The device of the array called this, whether or not a pool uses it."""
    for device, array_name in array_names().items():
        if array_name == name:
            return device
    plain = f"/dev/md/{name}"
    if os.path.exists(plain):
        return plain
    for device in arrays():
        if os.path.basename(device) == name:
            return device
    raise FnasError(f"no array called {name}")


def kernel_name(name):
    """What the kernel calls the array, as in md127, for the files in /sys."""
    return os.path.basename(array_device(name))


def check_filesystem(filesystem):
    chosen = filesystem or RECOMMENDED_FILESYSTEM
    if chosen not in FILESYSTEMS:
        raise FnasError(f"no such filesystem: {chosen} "
                        f"(an array takes {', '.join(FILESYSTEMS)})")
    return chosen


def filesystem_of(name):
    """Which filesystem a pool was made with, from what it is mounted as."""
    for line in read(MOUNTS).splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[1] == pools.mountpoint(name):
            return fields[2]
    return RECOMMENDED_FILESYSTEM


# --------------------------------------------------------------- layouts

def group_usable(layout, sizes):
    """What one array of these disks holds once the layout has its parity."""
    smallest = min(sizes)
    if layout == "mirror":
        return smallest
    if layout == "raid10":
        return smallest * (len(sizes) // 2)
    return smallest * (len(sizes) - PARITY.get(layout, 0))


def plan(layout, sizes, width=None, spares=0):
    """Split disks, given by size, into the array's disks and its spares. The
    largest disks become the spares, since a spare can only stand in for a
    disk no bigger than itself. An array is one group of every disk it has,
    so its width is however many are left once the spares are taken.
    Returns ([[index, ...]], [index, ...])."""
    if layout not in LAYOUTS:
        raise FnasError(f"no such layout: {layout} (mdadm offers {', '.join(LAYOUTS)})")
    if not 0 <= spares <= MAX_SPARES:
        raise FnasError(f"an array takes 0 to {MAX_SPARES} spares")
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    members, spare = order[:len(order) - spares], order[len(order) - spares:]
    lowest, highest = WIDTHS[layout]
    if width is None:
        width = len(members)
    if width != len(members):
        raise FnasError(f"an array uses every disk it has: {layout} on {len(sizes)} disks "
                        f"with {spares} spare" + ("" if spares == 1 else "s")
                        + f" is {len(members)}")
    if not lowest <= width <= highest:
        raise FnasError(f"{layout} takes {lowest} to {highest} disks")
    return [members], spare


def recommended(count):
    """The layout most people should use for this many disks: RAID 5 up to
    six, and RAID 6 above that, where a second failure during a rebuild would
    lose the array."""
    if count < 2:
        return None
    if count == 2:
        return ("mirror", 2, 0)
    if count <= 6:
        return ("raid5", count, 0)
    if count <= WIDTHS["raid6"][1]:
        return ("raid6", count, 0)
    for spares in range(MAX_SPARES + 1):
        if count - spares <= WIDTHS["raid6"][1]:
            return ("raid6", count - spares, spares)
    return None


def layouts(sizes):
    """Every layout these disks fit, with its usable space and how many failed
    disks the array survives. The recommended one comes first."""
    count = len(sizes)
    best = recommended(count) if count else None
    options = []
    for spares in range(min(MAX_SPARES, count - 1) + 1):
        width = count - spares
        for layout in LAYOUTS:
            lowest, highest = WIDTHS[layout]
            if not lowest <= width <= highest:
                continue
            groups, spare = plan(layout, sizes, width, spares)
            group_sizes = [[sizes[i] for i in group] for group in groups]
            options.append({
                "layout": layout, "width": width, "groups": len(groups), "spares": spares,
                "usable": sum(group_usable(layout, g) for g in group_sizes),
                "unused": sum(sum(g) - min(g) * len(g) for g in group_sizes),
                "survives": 1 if layout in ("mirror", "raid5", "raid10") else 2,
                "recommended": (layout, width, spares) == best,
            })
    options.sort(key=lambda o: (not o["recommended"], -o["survives"], -o["usable"], o["spares"]))
    return options


# --------------------------------------------------------------- creating

def create_pool(name, layout, wanted, confirm, wipe_disks, width=None, spares=0,
                filesystem=None):
    chosen = disks.choose(wanted, wipe_disks)
    groups, spare = plan(layout, [d["size"] for d in chosen], width, spares)
    pools.check_new_name(name)
    disks.check_confirmation(chosen, confirm)
    filesystem = check_filesystem(filesystem)
    paths = [d["by_id"] for d in chosen]
    disks.prepare(chosen, wipe_disks)
    device = f"/dev/md/{name}"
    command = ["mdadm", "--create", device, "--name", name, f"--level={LEVELS[layout]}",
               f"--raid-devices={len(groups[0])}", "--metadata=1.2", "--bitmap=internal",
               "--run"]
    if layout == "raid10":
        # The standard layout: two copies of every block, near each other
        command.append("--layout=n2")
    if spare:
        command.append(f"--spare-devices={len(spare)}")
    run(*command, *[paths[i] for i in groups[0]], *[paths[i] for i in spare])
    run(*MKFS[filesystem], device)
    save_conf()
    mount_pool(name, device, filesystem)
    for folder in DEFAULT_FOLDERS:
        os.makedirs(os.path.join(pools.mountpoint(name), folder), exist_ok=True)


def mount_pool(name, device, filesystem):
    path = pools.mountpoint(name)
    os.makedirs(path, exist_ok=True)
    run("mount", "-o", "noatime", device, path)
    remember_mount(name, device, filesystem)


def remember_mount(name, device, filesystem):
    """Add the pool to /etc/fstab so it comes back at boot, once only."""
    line = (f"UUID={filesystem_uuid(device)} {pools.mountpoint(name)} {filesystem} "
            "defaults,noatime 0 2\n")
    kept = [row for row in rows_of(FSTAB) if pools.mountpoint(name) not in row]
    with open(FSTAB, "w") as file:
        file.writelines(kept + [line])


def forget_mount(name):
    kept = [row for row in rows_of(FSTAB) if pools.mountpoint(name) not in row]
    with open(FSTAB, "w") as file:
        file.writelines(kept)


def filesystem_uuid(device):
    uuid = run("blkid", "-s", "UUID", "-o", "value", device).stdout.strip()
    if not uuid:
        raise FnasError(f"{device} has no filesystem UUID to put in {FSTAB}")
    return uuid


def write_conf(arrays_rows):
    """Write mdadm.conf with these ARRAY lines, keeping its other settings, and
    point mdadm --monitor at our alerts."""
    kept = [row for row in rows_of(MDADM_CONF) if not row.startswith(("ARRAY ", "PROGRAM "))]
    os.makedirs(os.path.dirname(MDADM_CONF), exist_ok=True)
    with open(MDADM_CONF, "w") as file:
        file.writelines(kept + [f"PROGRAM {MD_PROGRAM}\n"] + arrays_rows)
    # The monitor reads its settings once, when it starts
    run("systemctl", "try-restart", "mdmonitor.service", check=False)


def save_conf():
    """Record every array in mdadm.conf, so the kernel can assemble them at
    boot, before anything is mounted."""
    scanned = run("mdadm", "--detail", "--scan", check=False).stdout
    # A new array counts the disk it is still building onto as a spare; a spares=
    # count recorded then would have mdadm --monitor report a spare missing forever
    write_conf([" ".join(word for word in row.split() if not word.startswith("spares=")) + "\n"
                for row in scanned.splitlines() if row.startswith("ARRAY ")])
    run("update-initramfs", "-u", "-k", "all", check=False)


def watch_events():
    """Send mdadm's array events to alerts, as an upgrade from a release that
    didn't. Leaves mdadm.conf alone when it already does."""
    if not mdadm_installed() or f"PROGRAM {MD_PROGRAM}\n" in rows_of(MDADM_CONF):
        return
    write_conf([row for row in rows_of(MDADM_CONF) if row.startswith("ARRAY ")])


# --------------------------------------------------------------- status

def detail(name):
    """What mdadm says about an array, field by field and disk by disk."""
    output = run("mdadm", "--detail", array_device(name)).stdout
    fields, rows = {}, []
    for line in output.splitlines():
        row = DETAIL_DISK.match(line)
        if row:
            rows.append({"slot": None if row.group("slot") == "-" else int(row.group("slot")),
                         "raid_device": None if row.group("raid") == "-" else int(row.group("raid")),
                         "state": row.group("state").strip(), "path": row.group("path") or ""})
            continue
        field = re.match(r"^\s*([A-Za-z][\w ]*?)\s*:\s*(.*)$", line)
        if field:
            fields[field.group(1).strip().lower()] = field.group(2).strip()
    return {"fields": fields, "rows": rows}


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def disk_state(words):
    """mdadm's own words for a disk, in the vocabulary the other engine uses."""
    if "faulty" in words or "removed" in words:
        return "FAULTED"
    if "spare" in words:
        return "SPARE"
    if "active" in words or "sync" in words:
        return "ONLINE"
    return "DEGRADED"


def disk_size(device):
    """A disk's size in bytes, from the kernel's count of 512-byte sectors."""
    try:
        return int(read(os.path.join("/sys/class/block", os.path.basename(device), "size")).strip()) * 512
    except (OSError, ValueError):
        return 0


def describe_disk(row, links=None):
    """A disk in the array as the desktop shows it, by its stable by-id name rather than the
    /dev/vdX name mdadm reports, which can change between boots."""
    links = links if links is not None else {**disks.by_id_links(), **disks.by_id_links(partitions=True)}
    path = links.get(row["path"], row["path"])
    return {"name": path or f"slot {row['slot']}", "type": "disk",
            "state": disk_state(row["state"]), "path": path,
            "size": disk_size(row["path"]) if row["path"] else 0, "errors": 0, "note": row["state"]}


def sync_from(device):
    """What the kernel is doing to this array now, if anything."""
    entry = arrays().get(device)
    return entry["sync"] if entry else None


def missed(device):
    """Blocks a check found that don't match across the array."""
    try:
        return int(read(os.path.join(SYS_BLOCK, os.path.basename(device), "md",
                                     "mismatch_cnt")).strip())
    except (FileNotFoundError, ValueError):
        return 0


def level_of(words):
    """The level mdadm reports, in our words: raid1 is a mirror."""
    return {"raid1": "mirror"}.get(words, words) or "raid5"


def pool_status(name):
    """An mdadm pool is one array: its disks, its spares, and whatever the
    kernel is doing to it."""
    pools.find_pool(name)
    device = array_device(name)
    found = detail(name)
    fields, rows = found["fields"], found["rows"]
    members = [row for row in rows if row["raid_device"] is not None]
    spares = [row for row in rows if row["raid_device"] is None and "spare" in row["state"]]
    # mdadm lists a failed disk outside the array, with its old slot left "removed":
    # show it in that slot, so the disk to replace is named, not "missing"
    for failed in [row for row in rows if row["raid_device"] is None and "faulty" in row["state"]]:
        empty = next((row for row in members if not row["path"]), None)
        if empty:
            empty.update(path=failed["path"], state=failed["state"])
        else:
            members.append(failed)
    level = level_of(fields.get("raid level", "").lower())
    broken = [row for row in members if disk_state(row["state"]) == "FAULTED"]
    state = "ONLINE" if not broken else "DEGRADED"
    links = {**disks.by_id_links(), **disks.by_id_links(partitions=True)}
    # mdadm's own state words ("clean, checking") say nothing a person needs unless a disk is gone
    status = (f"{plural(len(broken), 'disk')} failed. The array still works, with less protection, "
              "until the disk is replaced." if broken else "")
    return {"name": name, "state": state, "status": status, "action": "",
            "groups": [{"name": f"{level}-0", "type": level, "state": state, "path": "",
                        "size": 0, "errors": missed(device), "note": "",
                        "disks": [describe_disk(row, links) for row in members]}],
            "spares": [describe_disk(row, links) for row in spares],
            "scan": sync_from(device), "expand": None, "expandable": True}


def member_disks(status):
    """Every disk in the array, without the groups ZFS needs and an array
    doesn't have."""
    return [disk for group in status["groups"] for disk in group.get("disks", [])]


def last_scrub(name):
    """When a check last finished, in seconds since the epoch, or 0."""
    return int(read_json(CHECKS, {}).get(name, 0))


def created(name):
    """When the array was made, which is when its first check falls due."""
    return created_at(detail(name)["fields"])


def md_file(name, field):
    with open(os.path.join(SYS_BLOCK, kernel_name(name), "md", field)) as file:
        return file.read().strip()


def mismatches(name):
    """Blocks the last check found that don't match. A repair rewrites them and
    counts them the same way, so only a check's count is a problem still there."""
    try:
        return int(md_file(name, "mismatch_cnt")) if md_file(name, "last_sync_action") == "check" else 0
    except (OSError, ValueError, FnasError):
        return 0


def mdcheck_paused(name):
    """Whether Debian's mdcheck has paused a check of this array for the night."""
    fields = dict(line.split("=", 1) for line in run(
        "mdadm", "--detail", "--export", array_device(name), check=False).stdout.split() if "=" in line)
    return bool(fields.get("MD_UUID")) and os.path.exists(os.path.join(MDCHECK_DIR, "MD_UUID_" + fields["MD_UUID"]))


def mdcheck_starting(resuming=False):
    """Before Debian's mdcheck runs, note the arrays it is about to check, since
    only fnas's own checks are otherwise recorded. Starting, it checks every idle
    array; resuming, only those it paused."""
    checks = read_json(CHECKS, {})
    watching = set(checks.get(WATCHING, []))
    for name in array_names().values():
        try:
            idle = md_file(name, "sync_action") == "idle"
        except (OSError, FnasError):
            continue
        if idle and (not resuming or mdcheck_paused(name)):
            watching.add(name)
    checks[WATCHING] = sorted(watching)
    write_json(CHECKS, checks)


def mdcheck_stopped(now=None):
    """After mdcheck stops: an array it was checking that is idle, with no place
    saved for tomorrow, has finished. Record it and say what it found."""
    checks = read_json(CHECKS, {})
    still = []
    for name in checks.get(WATCHING, []):
        try:
            if md_file(name, "sync_action") == "check" or mdcheck_paused(name):
                still.append(name)
                continue
        except (OSError, FnasError):
            # The array has gone; there is nothing to record
            continue
        checks[name] = int(now or time.time())
        found = mismatches(name)
        if found:
            alerts.send_alert("medium", f"Pool {name}: the monthly check found {found} blocks that don't match",
                              f"Repair them from Storage in the desktop, or sudo fnas pool repair {name}. "
                              "A repair can keep the wrong copy, so back up what matters first.")
        else:
            alerts.send_alert("info", f"Pool {name}: monthly check finished", "No problems found.")
    checks[WATCHING] = still
    write_json(CHECKS, checks)


def record_check(name, when=None):
    checks = read_json(CHECKS, {})
    checks[name] = int(when or time.time())
    write_json(CHECKS, checks)


# --------------------------------------------------------------- jobs

def follow(job):
    """Carry the kernel's work forward: a rebuild, a reshape or a check. The
    kernel does the work whether or not anything is watching."""
    device = array_device(job["pool"])
    sync = sync_from(device)
    wanted = {"resilver": ("recovery", "resync"), "expand": ("reshape",),
              "scrub": ("check", "repair")}.get(job["kind"], ())
    if sync and sync["function"] in wanted:
        return jobs.RUNNING, sync["progress"]
    if job["kind"] == "expand":
        # The array has its new space; the filesystem needs telling
        grow_filesystem(job["pool"])
    if job["kind"] == "scrub":
        record_check(job["pool"])
        if missed(device):
            return (f"the check found {missed(device)} blocks that don't match across the "
                    "array; run a repair to mend them"), 1.0
    return None, 1.0


def scrub_pool(name):
    """Check every block against the array's parity, changing nothing."""
    return start_sync(name, "check", "Checking {name}")


def repair_pool(name, confirm):
    """Rewrite the blocks a check found to be wrong. A repair can keep the
    wrong copy, so it is never automatic."""
    expected = f"repair {name}"
    if confirm != expected:
        raise FnasError("a repair rewrites the blocks that don't match, and can keep the "
                        f'wrong copy\nconfirm by passing --confirm "{expected}"')
    return start_sync(name, "repair", "Repairing {name}")


def start_sync(name, action, title):
    device = array_device(name)
    sync = sync_from(device)
    if sync and sync["function"] in SYNC_ACTIONS:
        raise FnasError(f"{name} is already busy with a {SYNC_ACTIONS[sync['function']]}")
    with open(os.path.join(SYS_BLOCK, kernel_name(name), "md", "sync_action"), "w") as file:
        file.write(action)
    return jobs.start("scrub", title.format(name=name), pool=name)


# --------------------------------------------------------------- changing

def grow(name, how, wanted, confirm, wipe_disks, target=None):
    """Add disks to an array: one more disk in it, which reshapes it, or a
    spare, which waits for a disk to fail."""
    status = pool_status(name)
    chosen = disks.choose(wanted, wipe_disks)
    paths = [d["by_id"] for d in chosen]
    device = array_device(name)
    if how == "spare":
        biggest = max([d["size"] for d in member_disks(status)] or [0])
        for disk in chosen:
            if biggest and disk["size"] < biggest:
                raise FnasError(f"{disk['by_id']} is smaller than the array's largest disk, "
                                "so it couldn't stand in for it")
        disks.check_confirmation(chosen, confirm)
        disks.prepare(chosen, wipe_disks)
        run("mdadm", "--manage", device, "--add", *paths)
        return None
    if how in ("group", "mirror"):
        raise FnasError("an array is a single group: add a disk with --how expand, or a spare")
    if how != "expand":
        raise FnasError(f"no such way to grow a pool: {how}")
    if len(chosen) != 1:
        raise FnasError("expand takes one disk at a time")
    count = len(member_disks(status))
    if count + 1 > WIDTHS[status["groups"][0]["type"]][1]:
        raise FnasError(f"an array of {count} disks is as wide as this layout allows")
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    run("mdadm", "--manage", device, "--add", paths[0])
    run("mdadm", "--grow", device, f"--raid-devices={count + 1}")
    return jobs.start("expand", f"Adding a disk to {name}", pool=name)


def grow_filesystem(name):
    """Give the filesystem the space a reshape added."""
    command = GROW_FILESYSTEM.get(filesystem_of(name))
    if command:
        run(command, pools.mountpoint(name), check=False)


def replace(name, old, wanted, confirm, wipe_disks):
    """Swap a failed or failing disk for a new one, and follow the rebuild."""
    status = pool_status(name)
    members = member_disks(status)
    by_path = {disk["path"]: disk for disk in members if disk["path"]}
    by_name = {disk["name"]: disk for disk in members}
    disk = by_path.get(old) or by_name.get(old)
    if not disk:
        raise FnasError(f"{old} isn't a disk in {name}")
    chosen = disks.choose([wanted], wipe_disks)
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    device = array_device(name)
    replaced = False
    if disk["path"] and disk["state"] == "FAULTED":
        # A failed disk can't hand its data over; take it out, and the new one is rebuilt
        run("mdadm", "--manage", device, "--remove", disk["path"], check=False)
    elif disk["path"]:
        replaced = run("mdadm", "--manage", device, "--replace", disk["path"],
                       "--with", chosen[0]["by_id"], check=False).returncode == 0
    if not replaced:
        # A disk that is gone has no path any more, only the slot it left
        run("mdadm", "--manage", device, "--add", chosen[0]["by_id"])
    return jobs.start("resilver", f"Replacing a disk in {name}", pool=name)


def remove_spare(name, spare):
    for disk in pool_status(name)["spares"]:
        if spare in (disk["path"], disk["name"]):
            run("mdadm", "--manage", array_device(name), "--remove", disk["path"])
            return
    raise FnasError(f"{spare} isn't a spare in {name}")


def export_pool(name):
    pools.find_pool(name)
    # NFS and Samba hold the shares open, so they let go first
    shares.write_config(leaving=name)
    result = run("umount", pools.mountpoint(name), check=False)
    if result.returncode != 0:
        shares.write_config()
        raise FnasError(f"{result.stderr.strip()}: disconnect any clients and try again")
    forget_mount(name)
    run("mdadm", "--stop", array_device(name), check=False)
    save_conf()


def import_pool(pool, readonly, force=False):
    found = [p for p in scan_pools() if pool in (p.get("pool"), p.get("uuid"))]
    if not found:
        raise FnasError(f"no importable array: {pool}")
    name = found[0]["pool"]
    pools.check_pool_name(name)
    device = f"/dev/md/{name}"
    args = ["mdadm", "--assemble", device]
    if found[0]["uuid"]:
        args += ["--uuid", found[0]["uuid"]]
    if force:
        args.append("--force")
    run(*args)
    path = pools.mountpoint(name)
    os.makedirs(path, exist_ok=True)
    if readonly:
        # Nothing will be written to it, so it lasts as long as it is mounted
        run("mount", "-o", "ro,noatime", device, path)
    else:
        filesystem = filesystem_of(name)
        run("mount", "-o", "noatime", device, path)
        remember_mount(name, device, filesystem)
    save_conf()
    shares.migrate()
    shares.write_config()


# --------------------------------------------------------------- details

def space(name):
    """What uses a pool's space, in parts that add up to what's used."""
    path = pools.mountpoint(name)
    stats = os.statvfs(path)
    size = stats.f_blocks * stats.f_frsize
    free = stats.f_bavail * stats.f_frsize
    used = size - free
    found = []
    shares_dir = os.path.join(path, "shares")
    if os.path.isdir(shares_dir):
        for entry in sorted(os.listdir(shares_dir)):
            folder = os.path.join(shares_dir, entry)
            if not os.path.isdir(folder):
                continue
            result = run("du", "-sbx", folder, check=False)
            words = result.stdout.split()
            found.append({"name": entry, "snapshots": 0,
                          "files": int(words[0]) if result.returncode == 0 and words else 0})
    files = sum(share["files"] for share in found)
    return {"used": used, "free": free, "shares": sorted(found, key=lambda s: -s["files"]),
            "parts": {"shares": files, "snapshots": 0, "backups": 0,
                      "other": max(used - files, 0)}}


def created_at(fields):
    value = fields.get("creation time")
    if not value:
        return 0
    result = run("date", "-d", value, "+%s", check=False)
    stamp = result.stdout.strip()
    return int(stamp) if result.returncode == 0 and stamp.isdigit() else 0


def pool_details(name):
    """Everything the pool's details view shows, besides its history."""
    pools.find_pool(name)
    device = array_device(name)
    stats = os.statvfs(pools.mountpoint(name))
    size = stats.f_blocks * stats.f_frsize
    free = stats.f_bavail * stats.f_frsize
    return {"name": name, "size": size, "allocated": size - free,
            # A plain filesystem has no fragmentation or compression to report
            "fragmentation": None, "compression": 1.0, "logical": size - free,
            "created": created_at(detail(name)["fields"]), "last_scrub": last_scrub(name),
            "errors": missed(device), "space": space(name), "filesystem": filesystem_of(name)}


# --------------------------------------------------------------- shares

def config_dir(name):
    path = os.path.join(pools.mountpoint(name), "fellowship-config")
    return path if os.path.isdir(path) else None


def create_volume(name, share):
    path = os.path.join(pools.mountpoint(name), "shares", share)
    if os.path.exists(path):
        raise FnasError(f"{path} already exists and isn't a share")
    os.makedirs(path)
    return path


def destroy_volume(share):
    # A large share takes as long as it takes; the typed confirmation already
    # covers the decision
    run("rm", "-rf", share["path"], timeout=None)


def volume_snapshots(share):
    """An array has no snapshots, so there is nothing to count or keep."""
    return []


def share_volumes(name):
    """An array keeps its shares as plain folders in /mnt/NAME/shares, with their
    settings in shares.json; "dataset" names the folder the way ZFS names a dataset."""
    folder = os.path.join(pools.mountpoint(name), "shares")
    if not os.path.isdir(folder):
        return {}
    return {entry: {"dataset": f"{name}/shares/{entry}", "path": os.path.join(folder, entry), "readonly": False}
            for entry in sorted(os.listdir(folder)) if os.path.isdir(os.path.join(folder, entry))}


def clear_legacy(name, share):
    """Nothing to clear: an array never carried per-share dataset settings."""
    return None


def legacy_policy(value):
    return None
