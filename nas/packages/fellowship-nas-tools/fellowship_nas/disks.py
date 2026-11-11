"""Finding disks and saying what is on each, so nothing in use is offered."""
import json
import os
import re

from .common import FnasError, run
from . import pools


BY_ID_DIR = "/dev/disk/by-id"
OS_MOUNTS = {"/", "/boot", "/boot/efi", "/usr", "/var"}
SYSTEM_DATA_LABEL = "fellowship-data"
# Stable names first: wwn and eui links exist alongside these on most disks
BY_ID_PREFERENCE = ("ata-", "nvme-", "scsi-", "virtio-", "usb-", "wwn-")
# SMART counters that should stay at zero on a healthy disk
SMART_COUNTERS = {5: "reallocated sectors", 197: "sectors waiting to be reallocated",
                  198: "unreadable sectors"}
HOT_DISK = 55
ENCLOSURE_DIR = "/sys/class/enclosure"
SYS_BLOCK = "/sys/class/block"
IDENTIFY_SECONDS = 60


def by_id_links(partitions=False):
    """Map each whole-disk device (or each partition) to its preferred /dev/disk/by-id path."""
    links = {}
    if not os.path.isdir(BY_ID_DIR):
        return links
    for name in sorted(os.listdir(BY_ID_DIR)):
        if ("-part" in name) != partitions or not name.startswith(BY_ID_PREFERENCE):
            continue
        rank = next(i for i, p in enumerate(BY_ID_PREFERENCE) if name.startswith(p))
        path = os.path.join(BY_ID_DIR, name)
        device = os.path.realpath(path)
        if device not in links or rank < links[device][0]:
            links[device] = (rank, path)
    return {device: path for device, (_, path) in links.items()}


def walk(device):
    yield device
    for child in device.get("children", []):
        yield from walk(child)


def classify(device, pool_devices):
    """Return a disk's state: os, in-use, has-data or empty, plus the reason."""
    tree = list(walk(device))
    mounts = {m for d in tree for m in d.get("mountpoints") or [] if m}
    if mounts & OS_MOUNTS:
        return "os", "holds the operating system"
    for d in tree:
        if d["path"] in pool_devices:
            return "in-use", f"in pool {pool_devices[d['path']]}"
    if mounts:
        return "in-use", "mounted at " + ", ".join(sorted(mounts))
    fstypes = sorted({d["fstype"] for d in tree if d.get("fstype")})
    if "zfs_member" in fstypes:
        return "has-data", "has a ZFS label"
    if fstypes:
        return "has-data", "has a filesystem: " + ", ".join(fstypes)
    if len(tree) > 1:
        return "has-data", "has partitions"
    return "empty", ""


def system_data_part(device, pool_devices, part_links):
    """The FellowshipOS installer leaves the rest of the system disk as a
    partition labelled fellowship-data, so a one-disk machine can hold a pool."""
    for part in device.get("children") or []:
        if part.get("partlabel") != SYSTEM_DATA_LABEL:
            continue
        state, reason = classify(part, pool_devices)
        return {
            "path": part["path"],
            "by_id": part_links.get(part["path"]),
            "size": part["size"],
            "model": (device.get("model") or "").strip(),
            # Left out so the system disk's serial can't select this by mistake
            "serial": "",
            "state": state,
            "reason": reason or "the free part of the system disk",
            "pool": pool_name(part, pool_devices),
            "partitions": [],
            "kind": "partition",
            "bay": None,
            "disk": device["path"],
        }
    return None


def whole_disk(path):
    """The disk a partition, or a by-id link to one, belongs to."""
    name = os.path.basename(os.path.realpath(path))
    parent = os.path.basename(os.path.dirname(os.path.realpath(os.path.join(SYS_BLOCK, name))))
    return "/dev/" + (parent if os.path.exists(os.path.join(SYS_BLOCK, parent, name)) else name)


def bays():
    """Map each disk in an enclosure that reports its slots (SES) to its bay and
    the sysfs folder of that slot. Without SES there are none, and disks go by serial."""
    found = {}
    if not os.path.isdir(ENCLOSURE_DIR):
        return found
    enclosures = sorted(os.listdir(ENCLOSURE_DIR))
    for number, enclosure in enumerate(enclosures, 1):
        folder = os.path.join(ENCLOSURE_DIR, enclosure)
        for slot in sorted(os.listdir(folder)):
            block = os.path.join(folder, slot, "device", "block")
            if not os.path.isdir(block):
                continue
            try:
                with open(os.path.join(folder, slot, "slot")) as file:
                    label = file.read().strip()
            except OSError:
                label = re.sub(r"\D+", " ", slot).strip() or slot
            name = f"Bay {label}" if len(enclosures) == 1 else f"Enclosure {number}, bay {label}"
            for device in os.listdir(block):
                found["/dev/" + device] = {"bay": name, "slot": os.path.join(folder, slot)}
    return found


def identify(wanted, on=True):
    """Light a disk's locate LED where its bay has one; otherwise read from it for
    a minute, so its activity light flickers."""
    disk = find_disk(list_disks(), wanted)
    bay = bays().get(disk.get("disk") or disk["path"])
    if bay and os.path.exists(os.path.join(bay["slot"], "locate")):
        with open(os.path.join(bay["slot"], "locate"), "w") as file:
            file.write("1" if on else "0")
        return f"The locate light of {bay['bay']} is " + ("on" if on else "off")
    unit = "fellowship-identify-" + os.path.basename(disk["path"])
    run("systemctl", "stop", unit, check=False)
    if on:
        run("systemd-run", "--quiet", "--collect", f"--unit={unit}", "timeout", str(IDENTIFY_SECONDS),
            "dd", f"if={disk['path']}", "of=/dev/null", "bs=1M", "iflag=direct")
        return f"Reading from {disk['path']} for a minute: its activity light is the one that stays lit"
    return f"Stopped reading from {disk['path']}"


def pool_name(device, pool_devices):
    """The pool holding a disk or one of its partitions, if one does."""
    for part in walk(device):
        if part["path"] in pool_devices:
            return pool_devices[part["path"]]
    return None


def list_disks():
    output = run("lsblk", "-J", "-b", "-o",
                 "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS,PARTLABEL").stdout
    links = by_id_links()
    part_links = by_id_links(partitions=True)
    pool_devices = pools.pool_devices()
    slots = bays()
    disks = []
    for device in json.loads(output)["blockdevices"]:
        # zd is a ZFS volume, fd a floppy drive, and an empty card reader has size 0
        if device["type"] != "disk" or device["name"].startswith(("zd", "fd")) or not device["size"]:
            continue
        state, reason = classify(device, pool_devices)
        disks.append({
            "path": device["path"],
            "by_id": links.get(device["path"]),
            "size": device["size"],
            "model": (device.get("model") or "").strip(),
            "serial": (device.get("serial") or "").strip(),
            "state": state,
            "reason": reason,
            "pool": pool_name(device, pool_devices),
            "partitions": [d["path"] for d in walk(device)][1:],
            "kind": "disk",
            "bay": slots.get(device["path"], {}).get("bay"),
        })
        if state == "os":
            data = system_data_part(device, pool_devices, part_links)
            if data:
                disks.append(data)
    return disks


def find_disk(disks, wanted):
    matches = [d for d in disks if wanted and wanted in (d["path"], d["by_id"], d["serial"])]
    if len(matches) != 1:
        raise FnasError(f"no such disk: {wanted}")
    return matches[0]


def confirmation_for(count):
    return f"erase {count} disk" + ("" if count == 1 else "s")


def choose(wanted, wipe_disks):
    """The disks a new pool, group, spare or replacement will use, after checking
    each is free and has a stable name. Old data needs --wipe."""
    found = list_disks()
    chosen = [find_disk(found, w) for w in wanted]
    if len({d["path"] for d in chosen}) != len(chosen):
        raise FnasError("the same disk is listed twice")
    for disk in chosen:
        if disk["state"] in ("os", "in-use"):
            raise FnasError(f"{disk['path']} can't be used: {disk['reason']}")
        if disk["state"] == "has-data" and not wipe_disks:
            raise FnasError(f"{disk['path']} {disk['reason']}: check it, then pass --wipe to erase it")
        if not disk["by_id"]:
            raise FnasError(f"{disk['path']} has no stable name in {BY_ID_DIR}")
    return chosen


def check_confirmation(chosen, confirm):
    expected = confirmation_for(len(chosen))
    if confirm != expected:
        raise FnasError(f'confirm by passing --confirm "{expected}"')


def wipe(disk):
    """Clear old signatures, so zpool create doesn't need -f."""
    for partition in disk["partitions"]:
        run("zpool", "labelclear", "-f", partition, check=False)
        run("wipefs", "-a", partition)
    run("wipefs", "-a", disk["path"])


def prepare(chosen, wipe_disks):
    if wipe_disks:
        for disk in chosen:
            wipe(disk)


def disk_health(disk):
    """Sum up what a disk's SMART data says, in words."""
    result = run("smartctl", "--json", "-a", disk["path"], check=False)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        data = {}
    passed = data.get("smart_status", {}).get("passed")
    temperature = data.get("temperature", {}).get("current")
    notes = []
    for attribute in data.get("ata_smart_attributes", {}).get("table", []):
        count = attribute.get("raw", {}).get("value", 0)
        if attribute.get("id") in SMART_COUNTERS and count:
            notes.append(f"{count} {SMART_COUNTERS[attribute['id']]}")
    nvme = data.get("nvme_smart_health_information_log", {})
    if nvme.get("media_errors"):
        notes.append(f"{nvme['media_errors']} media errors")
    if nvme.get("percentage_used", 0) >= 90:
        notes.append(f"has used {nvme['percentage_used']}% of its rated life")
    if temperature is not None and temperature >= HOT_DISK:
        notes.append(f"running hot at {temperature} °C")
    if passed is None:
        verdict = "unknown"
        notes.insert(0, "doesn't report SMART health")
    elif not passed or nvme.get("critical_warning"):
        verdict = "failing"
        notes.insert(0, "reports that it is failing: replace it soon")
    else:
        verdict = "watch" if notes else "healthy"
    return {"path": disk["path"], "by_id": disk["by_id"], "model": disk["model"],
            "serial": disk["serial"], "size": disk["size"], "state": disk["state"],
            "bay": disk.get("bay"),
            "verdict": verdict, "notes": notes, "temperature": temperature,
            "hours": data.get("power_on_time", {}).get("hours")}
