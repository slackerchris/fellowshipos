"""Every way to share files and every service the NAS runs, with whether each
is running, so the desktop can show them all, built or not."""
import os

from .common import STATE_DIR, FnasError, read_json, run, write_json
from . import accounts, shares, ssh


# Ways to share, in the order the desktop lists them: name, title, what it's
# for, its systemd unit, and the plan phase that builds it (None when built,
# "later" when it isn't planned yet)
PROTOCOLS = [
    ("smb", "SMB", "Windows, Mac and phones", "smbd.service", None),
    ("nfs", "NFS", "Linux computers, Proxmox and other servers", "nfs-server.service", None),
    ("sftp", "SFTP", "File transfer over SSH: scripts, FileZilla, phone apps",
     "ssh.service", None),
    ("ftp", "FTP", "Cameras, scanners and older devices", None, 13),
    # Served by the desktop's own web server, so there is no unit of its own
    ("webdav", "WebDAV", "A network drive over HTTPS, and phone apps",
     "fellowship-nas-web.service", None),
    ("dlna", "DLNA", "TVs and consoles finding media by themselves", None, "later"),
    ("iscsi", "iSCSI", "Part of a pool as a disk on another machine", None, "later"),
]

# The services behind the NAS itself: unit, title, and what it does
SYSTEM_SERVICES = [
    ("fellowship-nas-web.service", "Web server", "Serves this desktop"),
    ("fellowship-nas-helper.service", "Desktop helper", "Checks logins and makes changes for the desktop"),
    ("fellowship-nas-check.timer", "Hourly check", "Runs the checks on this page and sends alerts"),
    ("fellowship-nas-backup.timer", "Backup schedule", "Starts the nightly backup at 03:00"),
    ("sanoid.timer", "Snapshot schedule", "Takes and prunes snapshots every 15 minutes"),
    ("zfs-zed.service", "ZFS events", "Alerts when a ZFS disk fails or a scrub finds errors"),
    ("smartmontools.service", "Disk health", "Watches each disk's SMART data"),
    ("ssh.service", "SSH", "Logins over SSH, for accounts set to Shares and SSH or Files over SFTP"),
]


def ssh_expected():
    """The shared SSH daemon is meant to run while SFTP is on or root's own SSH
    is on. With both off, stopped is the intended state, not a failure."""
    return ssh.settings()["enabled"] or (accounts.is_separated() and ssh.root_ssh())


# Services the owner may start, stop and restart from the desktop. The web
# server and the helper that serve the click are deliberately not here.
CONTROLLED = {"smb": ("smbd.service", "Samba"), "nfs": ("nfs-server.service", "NFS"),
              "ssh": ("ssh.service", "SSH daemon")}
SERVICE_STATE = os.path.join(STATE_DIR, "services.json")


def owner_stopped(unit):
    """Whether the owner stopped this on purpose, so Health stays quiet about it."""
    return bool(read_json(SERVICE_STATE, {}).get(unit))


def controlled():
    """The services the desktop can control, with their live state. A stop the
    owner asked for reads as off, not as a failure."""
    shown = []
    for name, (unit, title) in CONTROLLED.items():
        state = unit_state(unit)
        detail = {"running": "Running", "stopped": "Stopped", "failed": "Failed",
                  "missing": "Not installed"}[state]
        if state == "stopped" and (owner_stopped(unit) or (unit == ssh.UNIT and not ssh_expected())):
            state, detail = "off", "Stopped on purpose"
        entry = {"name": name, "unit": unit, "title": title, "state": state, "detail": detail}
        if name == "ssh":
            # One daemon serves both: stopping it takes share SFTP offline too
            entry["also_stops"] = ["sftp"]
        shown.append(entry)
    return shown


def control(name, action):
    """Start, stop or restart one of the controlled services, remembering a stop
    the owner asked for so Health can tell it from a crash."""
    if name not in CONTROLLED:
        raise FnasError(f"no such service here: {name}")
    if action not in ("start", "stop", "restart"):
        raise FnasError(f"unknown service action: {action}")
    unit = CONTROLLED[name][0]
    run("systemctl", action, unit)
    marks = read_json(SERVICE_STATE, {})
    if action == "stop":
        marks[unit] = True
    else:
        marks.pop(unit, None)
    write_json(SERVICE_STATE, marks)
    return next(found for found in controlled() if found["name"] == name)


def unit_state(unit):
    """running, stopped, failed or missing, read from systemd. A service turned off on purpose is "off"."""
    shown = run("systemctl", "show", "-p", "LoadState,ActiveState,Result", unit, check=False).stdout
    fields = dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)
    if fields.get("LoadState") != "loaded":
        return "missing"
    if fields.get("ActiveState") in ("active", "reloading", "activating"):
        return "running"
    return "failed" if fields.get("ActiveState") == "failed" or fields.get("Result", "success") != "success" \
        else "stopped"


def protocols():
    found = shares.list_shares()
    shown = []
    for name, title, use, unit, phase in PROTOCOLS:
        entry = {"name": name, "title": title, "for": use, "unit": unit,
                 "shares": [s["name"] for s in found if s.get(name)]}
        if phase == "later":
            entry.update(state="later", detail="Planned for after the storage engines")
        elif phase:
            entry.update(state="not-built", detail=f"Not built yet (Phase {phase})")
        else:
            state = unit_state(unit)
            count = len(entry["shares"])
            used = f"{count} share" + ("" if count == 1 else "s") if count else "no shares use it yet"
            entry.update(state=state, detail={"running": f"Running, {used}", "stopped": f"Stopped, {used}",
                                              "failed": f"Failed, {used}",
                                              "missing": "Not installed"}[state])
            if name == "webdav" and state == "running":
                entry["detail"] = f"Running, read-only for now, {used}"
            # The daemon may be up for root's SSH while SFTP itself is off
            if name == "sftp" and not ssh.settings()["enabled"]:
                entry.update(state="off", detail="Turned off in Settings")
            if state == "stopped" and owner_stopped(unit):
                entry.update(state="off", detail="Stopped on purpose")
        shown.append(entry)
    return shown


def system_services():
    shown = []
    for unit, title, does in SYSTEM_SERVICES:
        state = unit_state(unit)
        detail = {"running": "Running", "stopped": "Stopped", "failed": "Failed", "missing": "Not installed"}[state]
        # smartd stops by itself when no disk reports SMART data, as in most VMs
        if unit == "smartmontools.service" and state == "stopped":
            detail = "Not running. It stops by itself when no disk reports SMART data"
        if unit == ssh.UNIT and (not ssh_expected() or owner_stopped(unit)):
            state, detail = "off", "Turned off in Settings: SFTP and root SSH are both off"
        shown.append({"unit": unit, "title": title, "does": does, "state": state, "detail": detail})
    return shown
