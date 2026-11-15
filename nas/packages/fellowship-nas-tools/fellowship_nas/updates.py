"""Updates: checked and announced by themselves, installed only when asked.

Chris's rule (docs/fellowship-nas-account-separation.md): the NAS looks for
updates every day and says when new ones are waiting, but nothing installs and
nothing restarts until a NAS admin (or root) asks. There is no
unattended-upgrades.

Installing takes only what the configured sources offer for what is already
installed: `apt-get upgrade --with-new-pkgs`, which may add the new packages an
update needs (a new kernel, say) but never removes one, and never adds a source.
"""
import os
import re
import time

from . import alerts
from .common import STATE_DIR, FnasError, read_json, run, write_json


STATE = os.path.join(STATE_DIR, "updates.json")
REBOOT_FLAG = "/run/reboot-required"
REBOOT_PACKAGES = "/run/reboot-required.pkgs"
# What apt prints for each package an upgrade would install, in a simulation:
#   Inst libc6 [2.41-12] (2.41-12+deb13u1 Debian-Security:13/stable-security [amd64])
INST = re.compile(r"^Inst (\S+) (?:\[(\S+)\] )?\((\S+) (.*?)\)")
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}
# A big upgrade on a slow mirror takes a while; the job waits for it
INSTALL_TIMEOUT = 2 * 3600
UPGRADE = ("apt-get", "-y", "-q", "--with-new-pkgs",
           # A changed configuration file keeps the NAS's own version
           "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold", "upgrade")


def waiting(simulation):
    """The packages an upgrade would install, from apt's simulation."""
    found = []
    for line in simulation.splitlines():
        match = INST.match(line)
        if match:
            name, old, new, origin = match.groups()
            found.append({"name": name, "from": old, "to": new, "security": "security" in origin.lower()})
    return found


def simulate():
    result = run("apt-get", "-s", "-q", "--with-new-pkgs", "upgrade", check=False)
    if result.returncode != 0:
        raise FnasError("apt couldn't work out the updates: " + (result.stderr.strip() or "no reason given"))
    return waiting(result.stdout)


def restart_needed():
    """Whether something installed only takes effect after a restart, and what."""
    if not os.path.exists(REBOOT_FLAG):
        return {"needed": False, "packages": []}
    try:
        with open(REBOOT_PACKAGES) as file:
            packages = sorted({line.strip() for line in file if line.strip()})
    except OSError:
        packages = []
    return {"needed": True, "packages": packages}


def fingerprint(packages):
    return sorted(f"{p['name']}={p['to']}" for p in packages)


def check(now=None):
    """Fetch the package lists, work out what is waiting, and say so once for
    each new set of updates. Installs nothing."""
    now = int(time.time() if now is None else now)
    state = read_json(STATE, {}) or {}
    fetched = run("apt-get", "update", "-q", check=False, timeout=600)
    error = None if fetched.returncode == 0 else (fetched.stderr.strip() or "apt-get update failed")
    packages = simulate()
    state.update(checked=now, packages=packages, error=error)
    new = fingerprint(packages)
    if packages and new != state.get("announced"):
        security = sum(p["security"] for p in packages)
        alerts.send_alert("medium" if security else "low",
                          f"{len(packages)} update{'s' if len(packages) != 1 else ''} waiting"
                          + (f", {security} for security" if security else ""),
                          "Nothing installs until you ask: Settings, Updates, Install updates.")
        state["announced"] = new
    elif not packages:
        state["announced"] = []
    write_json(STATE, state)
    return status(state)


def status(state=None):
    state = read_json(STATE, {}) or {} if state is None else state
    return {"checked": state.get("checked"), "packages": state.get("packages", []),
            "error": state.get("error"), "installed": state.get("installed"),
            "restart": restart_needed()}


def install():
    """The work of an update job: install what the configured sources offer for
    what is here, then look again so the list is current."""
    result = run("apt-get", "update", "-q", check=False, timeout=600)
    if result.returncode != 0:
        raise FnasError("couldn't fetch the package lists: " + (result.stderr.strip() or "no reason given"))
    result = run(*UPGRADE, check=False, env=APT_ENV, timeout=INSTALL_TIMEOUT)
    if result.returncode != 0:
        raise FnasError("installing updates failed: " + (result.stderr.strip()[-2000:] or "no reason given"))
    state = read_json(STATE, {}) or {}
    state.update(installed=int(time.time()), packages=simulate(), announced=[])
    write_json(STATE, state)
    restart = restart_needed()
    alerts.send_alert("info", "Updates installed",
                      "A restart is needed to finish: Settings, Updates, Restart now." if restart["needed"]
                      else "Nothing needs a restart.")
