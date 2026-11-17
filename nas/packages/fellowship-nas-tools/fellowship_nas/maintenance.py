"""What a NAS person may reach through the desktop, command by command.

docs/fellowship-nas-account-separation.md: the NAS admin has no shell, and
reaches the operating system only through the maintenance interface, a fixed
list. The helper passes the person's name as --caller on every fnas it runs for
the desktop; with a caller, fnas refuses anything root-only itself, so a mistake
in the helper's list is still caught here, and writes every maintenance
operation to the journal under the caller's name.

Anything not named below is root-only: a command added later stays closed to
the desktop until somebody decides which kind it is.
"""
from .common import run

# The NAS's own work: storage, shares, people, protection. Listed action by
# action, the same as the helper's list: some actions in these areas are for
# timers and package scripts alone (pool sync, share roots and the like)
NAS = {
    "disk": ("list", "identify"),
    "pool": ("list", "scan", "engines", "status", "details", "layouts", "create", "grow", "replace", "remove-spare",
             "import", "export", "delete", "scrub", "repair", "schedules", "schedule", "scrub-all"),
    "share": ("list", "create", "set", "access", "smb", "nfs", "refresh", "services", "delete"),
    "sharing": ("show", "smb", "nfs"),
    "user": ("list", "shares", "create", "set", "password", "smb-password", "keys", "remove"),
    "look": ("show", "set"),
    "service": ("list", "start", "stop", "restart"),
    "group": ("list", "create", "members", "rename", "remove"),
    "lan": ("show", "set"),
    "snapshot": ("list", "policy"),
    "backup": ("show", "set", "off", "run", "list", "restore"),
    "alert": ("list", "show", "test", "email-set", "email-off", "ntfy-set", "ntfy-off", "clear", "dismiss"),
    "config": ("show", "restore", "adopt"),
    "job": ("list", "show"),
    "setup": ("status", "done", "reset"),
    "accounts": ("status",),
    # Areas with no action
    "health": ("",),
    "check": ("",),
}
# Looking at the system without changing it
READ = {("system", "show"), ("system", "timezones"), ("system", "storage"), ("ssh", "show"), ("updates", "show"),
        ("network", "show")}
# The maintenance interface: the only changes to the system a NAS admin can ask for
MAINTENANCE = {("system", a) for a in ("hostname", "timezone", "certificate", "grow", "restart", "shutdown")} | \
    {("ssh", "set"), ("updates", "check"), ("updates", "install"), ("network", "apply"), ("network", "confirm")}
# Of those, the ones every NAS admin should hear about, not just the journal
ANNOUNCED = {("system", "restart"), ("system", "shutdown"), ("system", "certificate"), ("updates", "install"),
             ("network", "apply"), ("network", "confirm")}


def kind(area, action):
    """nas, read, maintenance or root."""
    if action in NAS.get(area, ()):
        return "nas"
    if (area, action) in READ:
        return "read"
    if (area, action) in MAINTENANCE:
        return "maintenance"
    return "root"


def audit(caller, area, action, argv):
    """Say in the journal who asked for a maintenance operation, and tell the
    alert channels about the ones that matter. Secrets come in on stdin, never
    in argv, so the command line is safe to keep."""
    from . import alerts
    what = " ".join([area, action] + [a for a in argv])
    run("logger", "--tag", "fellowship-nas-maintenance", "--priority", "auth.notice",
        f"{caller} asked for: {what}", check=False)
    if (area, action) in ANNOUNCED:
        alerts.send_alert("info", f"Maintenance by {caller}: {area} {action}",
                          f"{caller} asked for this from the NAS desktop.")
