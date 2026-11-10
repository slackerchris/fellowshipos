"""Unraid-style pools: each disk keeps its own XFS filesystem and whole files,
mergerfs shows the data disks as one folder at /mnt/NAME, and SnapRAID keeps
parity on one or two disks of their own. Parity is brought up to date by a
sync, nightly by default, so files changed since the last sync aren't
protected yet; with more disks lost than parity, only those disks' files go.

Each disk is mounted at /srv/disks/SERIAL and carries a fellowship-disk.json
naming its pool and role, so a pool can be found again after a reinstall. A
data disk's files sit in its files/ folder, which is the mergerfs branch: a
disk that isn't mounted has no such folder, so mergerfs never writes onto the
system disk in its place."""
import os
import re
import shutil
import subprocess
import time
import uuid

from ..common import STATE_DIR, FnasError, read_json, run, write_json
from .. import alerts, disks, jobs, pools, shares


LAYOUTS = ("parity1", "parity2")
PARITY = {"parity1": 1, "parity2": 2}
# SnapRAID's names for its parity levels, in order
PARITY_NAMES = ("parity", "2-parity")
CHECK_WORD = "scrub"
# How the desktop presents a pool on this engine; see the ZFS engine's
PRESENTATION = {
    "snapshots": False,
    "backup": False,
    "compresses": False,
    # A scrub only reports damage; fixing it from parity is started by hand
    "repair": True,
    "grow": ["data", "parity"],
    "grow_text": {
        "data": "Add data disks. Each joins the pool empty and holds whole files; none can be larger than "
                "the smallest parity disk",
        "parity": "Add a second parity disk, so any two disks can fail. It must be at least as large as "
                  "the largest data disk",
    },
    "expand_text": "",
    "check_button": "Check for damage",
    "check_name": "check for damage",
    "check_text": ("Check for damage (a SnapRAID scrub) reads files on {pool} and checks them against their "
                   "checksums and parity. It changes nothing: damage is reported, and a repair, which you "
                   "start yourself, rebuilds it from parity."),
    "errors_label": "Damaged blocks found by the last check",
    "repair_notice": ("The last check found {count} damaged blocks on {pool}. A repair rebuilds each from "
                      "parity, and their checksums confirm the result."),
    "repair_text": ("The repair rebuilds every damaged block from parity and the other disks, then checks "
                    "them again. Files changed since the last sync can't be rebuilt."),
    "replace_text": ("The new disk is filled from the old one if it can still be read, or else rebuilt from "
                     "parity and the other disks; the pool stays in use meanwhile."),
    "filesystems": [],
}
FILESYSTEM = "xfs"
DISKS_DIR = "/srv/disks"
POOLS_DIR = "/etc/fellowshipos/unraid"
CONF_DIR = "/etc/snapraid"
CONTENT_DIR = os.path.join(STATE_DIR, "snapraid")
LOG_DIR = os.path.join(STATE_DIR, "snapraid", "logs")
# Last sync, last scrub and what it found, by pool
STATE_FILE = os.path.join(STATE_DIR, "snapraid.json")
FSTAB = "/etc/fstab"
MOUNTS = "/proc/mounts"
MARKER = "fellowship-disk.json"
# Each disk's XFS label: this and the start of its pool's id, so a scan finds a
# pool's disks without mounting every disk on the machine. XFS allows 12 characters.
LABEL_PREFIX = "fn-"
# The folder on each data disk that mergerfs joins
FILES = "files"
DEFAULT_FOLDERS = ("shares", "home", "backup", "fellowship-config")
# A sync is skipped when this many files went missing since the last one, a
# sign of a disk that dropped out rather than files someone deleted
DELETED_LIMIT = 50
# New files go to the disk with the most free space; a disk with less than
# this left takes no new files, so it never fills to the brim
MIN_FREE = "1G"
MERGERFS_OPTIONS = ("allow_other,posix_acl=true,category.create=mfs,moveonenospc=true,"
                    f"minfreespace={MIN_FREE},cache.files=off,dropcacheonclose=true,"
                    # NFS needs inode numbers that stay put while a file is open
                    "inodecalc=path-hash,noforget")
# A scheduled scrub reads this much of the pool, oldest first, so the whole
# pool is read every couple of months
SCHEDULED_SCRUB = ("-p", "12", "-o", "10")
PERCENT = re.compile(rb"(\d+)%")
# Running SnapRAID processes, by job, for the process that follows them
PROCESSES = {}


def unavailable():
    """Why this engine can't be used on this machine, or "" when it can."""
    missing = [name for name in ("mergerfs", "snapraid") if not shutil.which(name)]
    if missing:
        return " and ".join(missing) + (" isn't" if len(missing) == 1 else " aren't") + " installed"
    if not os.path.exists("/dev/fuse"):
        return "this kernel has no FUSE, which mergerfs needs"
    return ""


def read(path):
    with open(path) as file:
        return file.read()


# --------------------------------------------------------------- records

def record_path(name):
    return os.path.join(POOLS_DIR, name + ".json")


def records():
    if not os.path.isdir(POOLS_DIR):
        return {}
    found = {}
    for entry in sorted(os.listdir(POOLS_DIR)):
        if entry.endswith(".json"):
            record = read_json(os.path.join(POOLS_DIR, entry))
            if record:
                found[record["name"]] = record
    return found


def record(name):
    found = records().get(name)
    if not found:
        raise FnasError(f"no Unraid-style pool called {name}")
    return found


def data_disks(found):
    return [disk for disk in found["disks"] if disk["role"] == "data"]


def parity_disks(found):
    return [disk for disk in found["disks"] if disk["role"] == "parity"]


def mount_dir(disk):
    """Where a disk is mounted, named by its serial in characters a mount unit's name keeps as they are."""
    return os.path.join(DISKS_DIR, re.sub(r"[^A-Za-z0-9_]", "_", disk["serial"] or os.path.basename(disk["by_id"])))


def mounted(path):
    return any(len(fields) > 1 and fields[1] == path
               for fields in (line.split() for line in read(MOUNTS).splitlines()))


def present(disk):
    return mounted(disk["mount"])


def content_path(name):
    return os.path.join(CONTENT_DIR, name + ".content")


def conf_path(name):
    return os.path.join(CONF_DIR, name + ".conf")


def snapraid_conf(found):
    """SnapRAID's settings for a pool: its parity files, a content file on every
    data disk and one on the system disk, and its data disks by their names."""
    lines = ["# Written by fnas for the Unraid-style pool " + found["name"] + ": changes here are overwritten"]
    for disk in parity_disks(found):
        lines.append(f"{disk['name']} {disk['mount']}/snapraid.{disk['name']}")
    lines.append(f"content {content_path(found['name'])}")
    for disk in data_disks(found):
        lines.append(f"content {disk['mount']}/snapraid.content")
    for disk in data_disks(found):
        lines.append(f"data {disk['name']} {disk['mount']}/{FILES}/")
    # The settings folder changes whenever fnas saves, which would stop any sync it
    # overlapped; it is copied to every data disk after each sync instead
    lines += ["exclude /fellowship-config/", "exclude *.unrecoverable", "exclude /lost+found/", "exclude .Trash-*/",
              # A long sync saves its progress every this many gigabytes
              "autosave 500"]
    return "\n".join(lines) + "\n"


def fstab_lines(found):
    """Each disk by filesystem UUID, then the pool on top. A missing disk mustn't
    stop the machine starting, and the pool comes up without it."""
    lines = [f"UUID={disk['uuid']} {disk['mount']} {FILESYSTEM} noatime,nofail,x-systemd.device-timeout=15s 0 2"
             for disk in found["disks"]]
    after = ",".join("x-systemd.after=" + disk["mount"].strip("/").replace("/", "-") + ".mount"
                     for disk in found["disks"])
    branches = ":".join(f"{disk['mount']}/{FILES}" for disk in data_disks(found))
    lines.append(f"{branches} {pools.mountpoint(found['name'])} fuse.mergerfs "
                 f"{MERGERFS_OPTIONS},fsname={found['name']},nofail,{after} 0 0")
    return [line + "\n" for line in lines]


def our_lines(found):
    """The mount points a pool's fstab lines use."""
    return {disk["mount"] for disk in found["disks"]} | {pools.mountpoint(found["name"])}


def write_fstab(found, remove=False):
    """Bring the pool's lines in /etc/fstab in line with its record. Writes only when something changed."""
    places = our_lines(found)
    with open(FSTAB) as file:
        rows = file.readlines()
    kept = [row for row in rows if len(row.split()) < 2 or row.split()[1] not in places]
    wanted = kept + ([] if remove else fstab_lines(found))
    if wanted != rows:
        with open(FSTAB, "w") as file:
            file.writelines(wanted)
        run("systemctl", "daemon-reload", check=False)


def save(found):
    """Record a pool and write everything that follows from it, each only if it changed."""
    os.makedirs(POOLS_DIR, exist_ok=True)
    write_json(record_path(found["name"]), found)
    os.makedirs(CONF_DIR, exist_ok=True)
    os.makedirs(CONTENT_DIR, exist_ok=True)
    shares.changed(conf_path(found["name"]), snapraid_conf(found))
    write_fstab(found)
    write_spin_down()


def forget(found):
    write_fstab(found, remove=True)
    for path in (record_path(found["name"]), conf_path(found["name"])):
        if os.path.exists(path):
            os.remove(path)
    write_spin_down()


# --------------------------------------------------------------- state

def state(name):
    return read_json(STATE_FILE, {}).get(name, {})


def set_state(name, **values):
    everything = read_json(STATE_FILE, {})
    everything.setdefault(name, {}).update(values)
    write_json(STATE_FILE, everything)


def last_sync(name):
    return state(name).get("last_sync") or 0


def last_scrub(name):
    """When a check last finished, in seconds since the epoch, or 0."""
    return state(name).get("last_scrub") or 0


def created(name):
    return record(name).get("created", 0)


def damage(name):
    """Blocks the last check found damaged, as a Health finding's title and message, or None."""
    count = state(name).get("damaged") or 0
    if not count:
        return None
    return (f"Pool {name}: {count} damaged blocks",
            f"The last check found them. Repair them from Storage, or sudo fnas pool repair {name}. "
            "Files changed since the last sync can't be rebuilt.")


# --------------------------------------------------------------- listing

def disk_size(path):
    try:
        return int(read(os.path.join("/sys/class/block", os.path.basename(os.path.realpath(path)), "size"))) * 512
    except (OSError, ValueError):
        return 0


def health(found):
    return "ONLINE" if all(present(disk) for disk in found["disks"]) else "DEGRADED"


def list_pools():
    shown = []
    for name, found in records().items():
        path = pools.mountpoint(name)
        if not mounted(path):
            continue
        stats = os.statvfs(path)
        size = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        readonly = any(len(fields) > 3 and fields[1] == path and "ro" in fields[3].split(",")
                       for fields in (line.split() for line in read(MOUNTS).splitlines()))
        shown.append({"name": name, "size": size, "allocated": size - free, "free": free,
                      "health": health(found), "readonly": readonly})
    return shown


def pool_devices():
    """Map each disk in a pool, by by-id path and kernel name, to its pool's name."""
    devices = {}
    for name, found in records().items():
        for disk in found["disks"]:
            devices[disk["by_id"]] = name
            devices[os.path.realpath(disk["by_id"])] = name
    return devices


def describe(disk):
    """A disk as the desktop shows it."""
    state_word = "ONLINE" if present(disk) else "UNAVAIL"
    return {"name": disk["by_id"], "type": "disk", "state": state_word, "path": disk["by_id"],
            "size": disk_size(disk["by_id"]), "errors": 0,
            "note": disk["name"] + ("" if state_word == "ONLINE" else ", not mounted")}


def running_job(name):
    return next((job for job in jobs.list_jobs() if job["pool"] == name and job["state"] == jobs.RUNNING), None)


def pool_status(name):
    """The data disks and the parity disks, whether parity is up to date, and any sync or check running."""
    found = record(name)
    missing = [disk for disk in found["disks"] if not present(disk)]
    state_word = "DEGRADED" if missing else "ONLINE"
    synced = last_sync(name)
    notes = []
    if missing:
        notes.append(f"{len(missing)} disk{'' if len(missing) == 1 else 's'} missing. "
                     + ("Their files can't be read until replaced; every other disk's files are fine."
                        if any(d["role"] == "data" for d in missing)
                        else "Every file can be read, but parity can't protect them until the disk is replaced."))
    if not synced:
        notes.append("Parity hasn't been synced yet, so nothing is protected.")
    elif time.time() - synced > 30 * 3600:
        notes.append(f"Parity was last synced {time.strftime('%Y-%m-%d %H:%M', time.localtime(synced))}: "
                     "files changed since then aren't protected yet.")
    job = running_job(name)
    data, parity = data_disks(found), parity_disks(found)
    return {"name": name, "state": state_word, "status": " ".join(notes), "action": "",
            "groups": [{"name": "data", "type": "data", "title": f"Data disks: {len(data)}", "state": state_word,
                        "path": "", "size": 0, "errors": state(name).get("damaged") or 0, "note": "",
                        "disks": [describe(disk) for disk in data]},
                       {"name": "parity", "type": "parity", "title": f"Parity disks: {len(parity)}",
                        "state": state_word, "path": "", "size": 0, "errors": 0, "note": "",
                        "disks": [describe(disk) for disk in parity]}],
            "spares": [],
            "scan": ({"function": job["kind"], "state": "scanning", "progress": job["progress"]} if job else None),
            "expand": None, "expandable": False, "last_sync": synced or None,
            # Ways to grow open right now: a second parity disk only while there is one
            "growable": ["data"] + (["parity"] if len(parity) < len(PARITY_NAMES) else [])}


def member_disks(status):
    return [disk for group in status["groups"] for disk in group.get("disks", [])]


# --------------------------------------------------------------- layouts

def group_usable(layout, sizes):
    """Every data disk's space counts; the parity disks hold none of it."""
    return sum(sorted(sizes)[:len(sizes) - PARITY[layout]])


def plan(layout, sizes, width=None, spares=0):
    """Split disks, given by size, into data disks and parity disks. The largest
    disks hold parity, which must be at least as large as any data disk.
    Returns ([[data index, ...]], [parity index, ...]), in the shape every
    engine's plan has, with parity where spares would be."""
    if layout not in LAYOUTS:
        raise FnasError(f"no such layout: {layout} (Unraid-style offers {', '.join(LAYOUTS)})")
    if spares:
        raise FnasError("Unraid-style pools have parity disks rather than spares")
    count = PARITY[layout]
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    data, parity = order[:len(order) - count], order[len(order) - count:]
    if not data:
        raise FnasError(f"{layout} needs at least one data disk beside its {count} parity "
                        + ("disk" if count == 1 else "disks"))
    if width is not None and width != len(data):
        raise FnasError(f"{layout} on {len(sizes)} disks has {len(data)} data disks")
    return [data], parity


def recommended(count):
    """One parity disk up to five data disks, two from six on."""
    if count < 2:
        return None
    return ("parity2", count - 2, 0) if count - 1 >= 6 else ("parity1", count - 1, 0)


def layouts(sizes):
    count = len(sizes)
    best = recommended(count)
    options = []
    for layout in LAYOUTS:
        if count <= PARITY[layout]:
            continue
        groups, parity = plan(layout, sizes)
        data = [sizes[i] for i in groups[0]]
        options.append({"layout": layout, "width": len(data), "groups": 1, "spares": 0,
                        "parity": PARITY[layout], "usable": sum(data),
                        # Parity larger than the largest data disk holds nothing more
                        "unused": sum(sizes[i] - max(data) for i in parity),
                        "survives": PARITY[layout],
                        "recommended": best is not None and (layout, len(data)) == best[:2]})
    options.sort(key=lambda o: (not o["recommended"], -o["survives"]))
    return options


# --------------------------------------------------------------- disks

def format_disk(disk, role, name, pool):
    """Give a disk its own XFS filesystem, mount it, and mark whose it is."""
    run("mkfs.xfs", "-f", "-L", LABEL_PREFIX + pool["id"][:9], disk["by_id"])
    entry = {"role": role, "name": name, "by_id": disk["by_id"], "serial": disk.get("serial") or "",
             "uuid": run("blkid", "-s", "UUID", "-o", "value", disk["by_id"]).stdout.strip()}
    entry["mount"] = mount_dir(entry)
    os.makedirs(entry["mount"], exist_ok=True)
    run("mount", "-o", "noatime", f"UUID={entry['uuid']}", entry["mount"])
    write_json(os.path.join(entry["mount"], MARKER), {"pool": pool["name"], "id": pool["id"], "role": role,
                                                        "name": name})
    if role == "data":
        os.makedirs(os.path.join(entry["mount"], FILES), exist_ok=True)
    return entry


def next_data_name(found):
    taken = {int(disk["name"][1:]) for disk in data_disks(found)}
    return f"d{max(taken, default=0) + 1}"


def mount_unit(path):
    """systemd's name for the mount at this path, as in mnt-media.mount; a "-" in a pool's name is escaped."""
    return run("systemd-escape", "--path", "--suffix=mount", path).stdout.strip()


def mount_pool(name):
    """Start the pool's mount through systemd, from its /etc/fstab line. mergerfs
    keeps running as long as the pool is mounted, and a process fnas started
    itself would belong to whichever service called fnas, the desktop's helper
    among them, and stop when that service restarts, as on every update."""
    run("systemctl", "daemon-reload", check=False)
    run("systemctl", "start", mount_unit(pools.mountpoint(name)))


def branches(name):
    return os.path.join(pools.mountpoint(name), ".mergerfs")


def change_branches(name, change):
    """Add or take away a data disk while the pool stays mounted, through mergerfs's control file."""
    os.setxattr(branches(name), "user.mergerfs.branches", change.encode())


# --------------------------------------------------------------- creating

def create_pool(name, layout, wanted, confirm, wipe_disks, width=None, spares=0, filesystem=None):
    if filesystem and filesystem != FILESYSTEM:
        raise FnasError("Unraid-style disks are XFS, which holds SnapRAID's parity on any size of disk")
    chosen = disks.choose(wanted, wipe_disks)
    groups, parity = plan(layout, [d["size"] for d in chosen], width, spares)
    pools.check_new_name(name)
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    found = {"name": name, "id": uuid.uuid4().hex, "created": int(time.time()), "disks": []}
    for number, index in enumerate(groups[0], 1):
        found["disks"].append(format_disk(chosen[index], "data", f"d{number}", found))
    for level, index in enumerate(parity):
        found["disks"].append(format_disk(chosen[index], "parity", PARITY_NAMES[level], found))
    save(found)
    path = pools.mountpoint(name)
    os.makedirs(path, exist_ok=True)
    mount_pool(name)
    for folder in DEFAULT_FOLDERS:
        os.makedirs(os.path.join(path, folder), exist_ok=True)
    # The first sync is quick on empty disks, and from then on parity is in step
    return start(name, "sync", f"Syncing parity on {name}", [snapraid(name, "sync")])


# --------------------------------------------------------------- jobs

def snapraid(name, *words):
    return ["snapraid", "-c", conf_path(name), *words]


def start(name, kind, title, steps):
    if running_job(name):
        raise FnasError(f"{name} is already busy: wait for its job to finish")
    return jobs.start(kind, title, pool=name, details={"steps": steps, "step": 0})


def log_path(job):
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, job["id"] + ".log")


def progress_of(job):
    """How far the current step is, from the last percentage SnapRAID printed."""
    try:
        with open(log_path(job), "rb") as file:
            file.seek(max(os.path.getsize(log_path(job)) - 4096, 0))
            found = PERCENT.findall(file.read())
    except OSError:
        found = []
    within = min(int(found[-1]) / 100, 1.0) if found else 0.0
    details = job["details"]
    return (details["step"] + within) / len(details["steps"])


def follow(job):
    """Run a job's steps one after another, reporting progress as they go."""
    details = job["details"]
    process = PROCESSES.get(job["id"])
    if process is None:
        log = open(log_path(job), "ab")
        PROCESSES[job["id"]] = subprocess.Popen(details["steps"][details["step"]], stdout=log,
                                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        return jobs.RUNNING, progress_of(job)
    code = process.poll()
    if code is None:
        return jobs.RUNNING, progress_of(job)
    del PROCESSES[job["id"]]
    if code != 0 and not (job["kind"] == "sync" and changed_during_sync(job)):
        return finished(job, f"{' '.join(details['steps'][details['step']][3:5])} stopped with an error "
                             f"(code {code}): see {log_path(job)}")
    details["step"] += 1
    if details["step"] < len(details["steps"]):
        return jobs.RUNNING, progress_of(job)
    return finished(job, None)


def changed_during_sync(job):
    """Whether a sync stopped only because files changed while it ran. Parity for
    everything else is saved, and those files are protected by the next sync,
    like any file written after one."""
    try:
        text = read(log_path(job))
    except OSError:
        return False
    counts = re.findall(r"^\s*(\d+) (?:io|data) errors?$", text, re.M)
    return "You cannot modify data disk during a sync" in text and bool(counts) and not any(int(c) for c in counts)


def copy_config(name):
    """Put a copy of the pool's settings on every data disk, so a reinstall finds
    them on whichever disks survive. Each file's newest copy is the one fnas last wrote."""
    folders = [os.path.join(disk["mount"], FILES, "fellowship-config")
               for disk in data_disks(record(name)) if present(disk)]
    newest = {}
    for folder in folders:
        for entry in os.listdir(folder) if os.path.isdir(folder) else []:
            path = os.path.join(folder, entry)
            if os.path.isfile(path) and os.path.getmtime(path) > newest.get(entry, ("", -1))[1]:
                newest[entry] = (path, os.path.getmtime(path))
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
        for entry, (path, _) in newest.items():
            if os.path.dirname(path) != folder:
                run("cp", "-a", path, os.path.join(folder, entry), check=False)


def finished(job, error):
    name, now = job["pool"], int(time.time())
    if job["kind"] == "sync" and not error:
        set_state(name, last_sync=now, skipped=None)
        copy_config(name)
    if job["kind"] == "scrub":
        damaged = scrub_errors(log_path(job))
        set_state(name, last_scrub=now, damaged=damaged)
        if damaged and not error:
            error = f"the check found {damaged} damaged blocks: repair them from Storage"
    if job["kind"] == "resilver" and not error:
        set_state(name, damaged=0)
    return error, 1.0


def scrub_errors(path):
    """Damaged blocks a scrub reported, from its log."""
    try:
        text = read(path)
    except OSError:
        return 0
    return sum(int(count) for count in re.findall(r"(\d+) (?:file|io|data) errors?", text))


def deleted_since_sync(name):
    """How many files went missing since the last sync, from snapraid diff."""
    result = run(*snapraid(name, "diff"), check=False, timeout=None)
    match = re.search(r"^\s*(\d+) removed$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else 0


def sync_pool(name, forced=False):
    """Bring parity up to date, unless so many files went missing that a disk
    probably dropped out, which a sync would make permanent."""
    found = record(name)
    if not all(present(disk) for disk in found["disks"]):
        raise FnasError(f"{name} has a disk missing: replace it before syncing, or its files would be "
                        "dropped from parity")
    deleted = deleted_since_sync(name)
    if deleted > DELETED_LIMIT and not forced:
        set_state(name, skipped=f"{deleted} files went missing since the last sync")
        alerts.send_alert("high", f"Pool {name}: parity sync skipped",
                          f"{deleted} files went missing since the last sync, more than {DELETED_LIMIT}. "
                          "If you deleted them, sync from Storage; if not, check the disks first.")
        raise FnasError(f"{deleted} files went missing since the last sync: check the disks, then sync "
                        "from Storage to keep the change")
    return start(name, "sync", f"Syncing parity on {name}", [snapraid(name, "sync")])


def scrub_pool(name, scheduled=False):
    """Read files and check them against their checksums and parity, changing nothing."""
    amount = SCHEDULED_SCRUB if scheduled else ("-p", "full")
    return start(name, "scrub", f"Checking {name}", [snapraid(name, "scrub", *amount)])


def repair_pool(name, confirm=""):
    """Rebuild the blocks a check found damaged, then check them again."""
    expected = f"repair {name}"
    if confirm != expected:
        raise FnasError(f'confirm by passing --confirm "{expected}"')
    return start(name, "resilver", f"Repairing {name}",
                 [snapraid(name, "-e", "fix"), snapraid(name, "scrub", "-p", "bad")])


def problems(name, now):
    """What the Health page says about this pool beyond its disks: a sync that
    was skipped or is overdue."""
    from .. import storage_checks
    found = []
    skipped = state(name).get("skipped")
    if skipped:
        found.append(("sync-skipped", "high", f"Pool {name}: parity sync skipped",
                      f"{skipped}. If they were deleted on purpose, sync from Storage; if not, check the disks."))
    elif storage_checks.settings()["mergerfs"]["sync"]:
        synced = last_sync(name) or created(name)
        if now - synced > 30 * 3600:
            found.append(("sync-late", "medium", f"Pool {name}: parity isn't up to date",
                          "The nightly sync hasn't finished since "
                          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(synced))}, so newer files aren't "
                          "protected. Sync from Storage, or check Storage for a failed job."))
    return found


# --------------------------------------------------------------- changing

def grow(name, how, wanted, confirm, wipe_disks, target=None):
    """Add data disks, which join the pool empty, or a second parity disk."""
    found = record(name)
    chosen = disks.choose(wanted, wipe_disks)
    if how == "data":
        smallest_parity = min(disk_size(d["by_id"]) for d in parity_disks(found))
        for disk in chosen:
            if disk["size"] > smallest_parity:
                raise FnasError(f"{disk['by_id']} is larger than the smallest parity disk, which couldn't cover it")
        disks.check_confirmation(chosen, confirm)
        disks.prepare(chosen, wipe_disks)
        for disk in chosen:
            entry = format_disk(disk, "data", next_data_name(found), found)
            found["disks"].append(entry)
            save(found)
            change_branches(name, f"+>{entry['mount']}/{FILES}")
        return start(name, "sync", f"Syncing parity on {name}", [snapraid(name, "sync")])
    if how == "parity":
        levels = len(parity_disks(found))
        if levels >= len(PARITY_NAMES) or len(chosen) != 1:
            raise FnasError(f"{name} can take {len(PARITY_NAMES) - levels} more parity disk, one at a time")
        largest = max(disk_size(d["by_id"]) for d in data_disks(found))
        if chosen[0]["size"] < largest:
            raise FnasError(f"{chosen[0]['by_id']} is smaller than the largest data disk, so it can't hold its parity")
        disks.check_confirmation(chosen, confirm)
        disks.prepare(chosen, wipe_disks)
        found["disks"].append(format_disk(chosen[0], "parity", PARITY_NAMES[levels], found))
        save(found)
        # The new level starts empty, so every block's parity is worked out again
        return start(name, "sync", f"Building the second parity on {name}", [snapraid(name, "sync", "-F")])
    raise FnasError(f"an Unraid-style pool grows by data disks or a second parity disk, not by {how}")


def replace(name, old, wanted, confirm, wipe_disks):
    """Swap a disk for a new one: copied from the old disk while it can still be
    read, or else rebuilt from parity and the other disks."""
    found = record(name)
    disk = next((d for d in found["disks"] if old in (d["by_id"], d["name"], d["mount"])), None)
    if not disk:
        raise FnasError(f"{old} isn't a disk in {name}")
    chosen = disks.choose([wanted], wipe_disks)
    if disk["role"] == "parity":
        if chosen[0]["size"] < max(disk_size(d["by_id"]) for d in data_disks(found)):
            raise FnasError("a parity disk must be at least as large as the largest data disk")
    elif chosen[0]["size"] > min(disk_size(d["by_id"]) for d in parity_disks(found)):
        raise FnasError("a data disk can't be larger than the smallest parity disk")
    disks.check_confirmation(chosen, confirm)
    disks.prepare(chosen, wipe_disks)
    readable = present(disk)
    entry = format_disk(chosen[0], disk["role"], disk["name"], found)
    if disk["role"] == "data":
        change_branches(name, f"+>{entry['mount']}/{FILES}")
        change_branches(name, f"-{disk['mount']}/{FILES}")
    if readable and disk["role"] == "data":
        # Everything, including files changed since the last sync, comes across as it is
        steps = [["cp", "-a", "--reflink=never", f"{disk['mount']}/{FILES}/.", f"{entry['mount']}/{FILES}/"],
                 ["umount", disk["mount"]], snapraid(name, "sync")]
    else:
        if readable:
            run("umount", disk["mount"], check=False)
        steps = [snapraid(name, "fix", "-d", disk["name"]), snapraid(name, "sync")]
    found["disks"] = [entry if d is disk else d for d in found["disks"]]
    write_fstab({**found, "disks": [disk]}, remove=True)
    save(found)
    return start(name, "resilver", f"Replacing a disk in {name}", steps)


def remove_spare(name, spare):
    raise FnasError("an Unraid-style pool has no spares")


# --------------------------------------------------------------- moving

def labelled():
    """Devices whose XFS label says an Unraid-style pool made them, from one blkid call."""
    found, current = [], {}
    for line in run("blkid", "-o", "export", check=False).stdout.splitlines() + [""]:
        if "=" in line:
            key, _, value = line.partition("=")
            current[key] = value
            continue
        if current.get("TYPE") == FILESYSTEM and current.get("LABEL", "").startswith(LABEL_PREFIX):
            found.append(current["DEVNAME"])
        current = {}
    return found


def read_markers():
    """Every labelled disk that isn't in use, with the marker it carries, read
    from a read-only mount that is taken away again."""
    candidates = {os.path.realpath(device) for device in labelled()}
    if not candidates:
        return []
    taken = pools.pool_devices()
    found = []
    for disk in disks.list_disks():
        if not disk["by_id"] or disk["by_id"] in taken or disk["path"] in taken \
                or os.path.realpath(disk["by_id"]) not in candidates:
            continue
        place = os.path.join(DISKS_DIR, ".scan")
        os.makedirs(place, exist_ok=True)
        if run("mount", "-o", "ro,norecovery", disk["by_id"], place, check=False).returncode != 0:
            continue
        try:
            marker = read_json(os.path.join(place, MARKER))
        finally:
            run("umount", place, check=False)
        if isinstance(marker, dict) and marker.get("pool"):
            found.append({**marker, "disk": disk})
    return found


def scan_pools():
    """Unraid-style pools on attached disks that this system hasn't mounted."""
    by_pool = {}
    for marker in read_markers():
        by_pool.setdefault(marker["id"], []).append(marker)
    shown = []
    for pool_id, members in by_pool.items():
        name = members[0]["pool"]
        data = sum(1 for m in members if m["role"] == "data")
        parity = sum(1 for m in members if m["role"] == "parity")
        shown.append({"pool": name, "state": "ready", "uuid": pool_id,
                      "comment": f"Unraid-style: {data} data and {parity} parity "
                                 + ("disk" if parity == 1 else "disks") + " found"})
    return shown


def import_pool(pool, readonly, force=False):
    members = [m for m in read_markers() if pool in (m["pool"], m["id"])]
    if not members:
        raise FnasError(f"no importable Unraid-style pool: {pool}")
    name = members[0]["pool"]
    pools.check_pool_name(name)
    if name in records():
        raise FnasError(f"there is already a pool called {name}")
    found = {"name": name, "id": members[0]["id"], "created": int(time.time()), "disks": []}
    for marker in sorted(members, key=lambda m: (m["role"] != "data", m["name"])):
        disk = marker["disk"]
        entry = {"role": marker["role"], "name": marker["name"], "by_id": disk["by_id"],
                 "serial": disk.get("serial") or "",
                 "uuid": run("blkid", "-s", "UUID", "-o", "value", disk["by_id"]).stdout.strip()}
        entry["mount"] = mount_dir(entry)
        os.makedirs(entry["mount"], exist_ok=True)
        run("mount", "-o", "ro,noatime" if readonly else "noatime", f"UUID={entry['uuid']}", entry["mount"])
        found["disks"].append(entry)
    path = pools.mountpoint(name)
    os.makedirs(path, exist_ok=True)
    if readonly:
        # Nothing is recorded, so it lasts as long as it stays mounted
        branches_list = ":".join(f"{d['mount']}/{FILES}" for d in data_disks(found))
        os.makedirs(POOLS_DIR, exist_ok=True)
        write_json(record_path(name), found)
        # A transient mount unit, for the same reason as mount_pool's
        run("systemd-mount", "-t", "fuse.mergerfs", "-o", f"ro,{MERGERFS_OPTIONS},fsname={name}", branches_list, path)
    else:
        save(found)
        mount_pool(name)
    shares.migrate()
    shares.write_config()


def export_pool(name):
    found = record(name)
    pools.find_pool(name)
    if running_job(name):
        raise FnasError(f"{name} is busy: wait for its job to finish")
    # NFS and Samba hold the shares open, so they let go first
    shares.write_config(leaving=name)
    result = run("umount", pools.mountpoint(name), check=False)
    if result.returncode != 0:
        shares.write_config()
        raise FnasError(f"{result.stderr.strip()}: disconnect any clients and try again")
    for disk in found["disks"]:
        run("umount", disk["mount"], check=False)
    forget(found)


# --------------------------------------------------------------- spin-down

HD_IDLE = "/etc/default/hd-idle"


def write_spin_down():
    """Let hd-idle spin down every disk in an Unraid-style pool after the idle time
    in Settings. Other engines touch every disk on every write, so theirs stay up."""
    from .. import storage_checks
    if not shutil.which("hd-idle"):
        return
    minutes = storage_checks.settings()["mergerfs"]["spin_down"]
    # By stable name: hd-idle finds the kernel's name for each when it starts, which can change between boots
    members = sorted(d["by_id"] for found in records().values() for d in found["disks"])
    options = ["-i", "0"] + [word for device in members for word in ("-a", device, "-i", str(minutes * 60))]
    wanted = ("# Written by fnas from Settings → Storage checks: changes here are overwritten\n"
              f"START_HD_IDLE={'true' if minutes and members else 'false'}\n"
              f'HD_IDLE_OPTS="{" ".join(options)}"\n')
    if shares.changed(HD_IDLE, wanted):
        if minutes and members:
            run("systemctl", "enable", "hd-idle.service", check=False)
            run("systemctl", "restart", "hd-idle.service", check=False)
        else:
            run("systemctl", "disable", "--now", "hd-idle.service", check=False)


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
            result = run("du", "-sb", folder, check=False, timeout=None)
            words = result.stdout.split()
            found.append({"name": entry, "snapshots": 0,
                          "files": int(words[0]) if result.returncode == 0 and words else 0})
    files = sum(share["files"] for share in found)
    return {"used": used, "free": free, "shares": sorted(found, key=lambda s: -s["files"]),
            "parts": {"shares": files, "snapshots": 0, "backups": 0, "other": max(used - files, 0)}}


def pool_details(name):
    """Everything the pool's details view shows, besides its history."""
    pools.find_pool(name)
    stats = os.statvfs(pools.mountpoint(name))
    size = stats.f_blocks * stats.f_frsize
    free = stats.f_bavail * stats.f_frsize
    return {"name": name, "size": size, "allocated": size - free, "fragmentation": None,
            "compression": 1.0, "logical": size - free, "created": created(name),
            "last_scrub": last_scrub(name) or None, "last_sync": last_sync(name) or None,
            "errors": state(name).get("damaged") or 0, "space": space(name), "filesystem": FILESYSTEM}


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
    run("rm", "-rf", share["path"], timeout=None)


def volume_snapshots(share):
    """No snapshots, so there is nothing to count or keep."""
    return []


def share_volumes(name):
    """Shares are plain folders in /mnt/NAME/shares, as on an array."""
    folder = os.path.join(pools.mountpoint(name), "shares")
    if not os.path.isdir(folder):
        return {}
    return {entry: {"dataset": f"{name}/shares/{entry}", "path": os.path.join(folder, entry), "readonly": False}
            for entry in sorted(os.listdir(folder)) if os.path.isdir(os.path.join(folder, entry))}


def clear_legacy(name, share):
    return None


def legacy_policy(value):
    return None

