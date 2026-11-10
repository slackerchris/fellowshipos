"""Every way to share files and every service the NAS runs, with whether each
is running, so the desktop can show them all, built or not."""
from .common import run
from . import shares, ssh


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
        entry = {"name": name, "title": title, "for": use, "shares": [s["name"] for s in found if s.get(name)]}
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
        if unit == ssh.UNIT and not ssh.settings()["enabled"]:
            state, detail = "off", "Turned off in Settings"
        shown.append({"unit": unit, "title": title, "does": does, "state": state, "detail": detail})
    return shown
