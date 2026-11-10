"""Settings saved on every pool, so a reinstall can bring them back."""
import grp
import os
import pwd
import re
import time
import uuid

from .common import ACCOUNT_NAME, FnasError, read_json, run, write_file, write_json
from . import alerts, backup, pools, shares, snapshots, ssh, storage_checks, users


INSTALL_ID_FILE = "/etc/fellowshipos/nas-install-id"
CONFIG_VERSION = 1


def install_id():
    """Tells this install's saved settings from those of an earlier one."""
    try:
        with open(INSTALL_ID_FILE) as file:
            return file.read().strip()
    except FileNotFoundError:
        value = str(uuid.uuid4())
        os.makedirs(os.path.dirname(INSTALL_ID_FILE), exist_ok=True)
        write_file(INSTALL_ID_FILE, value + "\n")
        return value


def config_path(pool):
    directory = pools.engine_for(pool).config_dir(pool)
    return os.path.join(directory, "config.json") if directory else None


def saved_config(pool):
    path = config_path(pool)
    return read_json(path) if path else None


def foreign_config_pools():
    found = []
    for pool in pools.writable_pools():
        saved = saved_config(pool)
        if saved and saved.get("install") != install_id():
            found.append(pool)
    return found


def local_users():
    groups = {group.gr_gid: group.gr_name for group in grp.getgrall()}
    details = {user["name"]: user for user in users.list_users()}
    return [{"name": user.pw_name, "uid": user.pw_uid, "gid": user.pw_gid,
             "group": groups.get(user.pw_gid), "comment": user.pw_gecos.split(",")[0],
             "kind": details.get(user.pw_name, {}).get("kind", "ssh"),
             "admin": details.get(user.pw_name, {}).get("admin", False),
             "groups": details.get(user.pw_name, {}).get("groups", []),
             # Public keys only, so nothing secret
             "keys": [key["line"] for key in ssh.list_keys(user.pw_name)] if user.pw_name in details else []}
            for user in pwd.getpwall() if 1000 <= user.pw_uid < 60000 and user.pw_name not in users.SERVICE_ACCOUNTS]


def current_config():
    try:
        lan = shares.lan_subnet()
    except FnasError:
        lan = None
    return {"version": CONFIG_VERSION, "install": install_id(), "saved": int(time.time()),
            "lan": lan, "backup": read_json(backup.BACKUP_FILE), "alerts": read_json(alerts.ALERTS_FILE, {}),
            "shares": shares.list_shares(), "users": local_users(),
            "groups": [{"name": g["name"], "gid": g["gid"]} for g in users.list_groups()],
            "sharing": shares.sharing_settings(), "ssh": ssh.settings(), "checks": storage_checks.settings()}


def save_config(claim=()):
    """Copy settings to every pool, leaving alone any that a reinstall hasn't restored yet."""
    data = current_config()
    for pool in pools.writable_pools():
        path = config_path(pool)
        if not path:
            continue
        saved = read_json(path)
        if not saved or saved.get("install") == data["install"] or pool in claim:
            write_json(path, data, mode=0o600)


def after_change():
    shares.migrate()
    snapshots.write_sanoid_config()
    save_config()


def restore_user(user):
    """Recreate one account, reporting anything that stops it matching the old one."""
    name, uid, gid, group = user["name"], user["uid"], user["gid"], user["group"]
    if not (ACCOUNT_NAME.match(name) and group and ACCOUNT_NAME.match(group)
            and 1000 <= uid < 60000 and 1000 <= gid < 60000):
        return f"skipped {name}: its saved details look wrong"
    try:
        existing = pwd.getpwnam(name)
        if existing.pw_uid != uid:
            return f"{name} already exists with UID {existing.pw_uid}, not {uid}: files won't match"
        return None
    except KeyError:
        pass
    try:
        return f"skipped {name}: UID {uid} already belongs to {pwd.getpwuid(uid).pw_name}"
    except KeyError:
        pass
    try:
        grp.getgrgid(gid)
    except KeyError:
        run("groupadd", "--gid", str(gid), group)
    comment = re.sub(r"[:\n]", " ", user.get("comment") or "")
    shell = users.KINDS.get(user.get("kind"), users.KINDS["ssh"])
    run("useradd", "--uid", str(uid), "--gid", str(gid), "--comment", comment,
        "--create-home", "--shell", shell, name)
    return f"created {name}: set a password in Users, or with sudo fnas user password {name} --password-stdin"


def restore_group(group):
    name, gid = group.get("name"), group.get("gid")
    if not (isinstance(name, str) and ACCOUNT_NAME.match(name) and isinstance(gid, int) and 1000 <= gid < 60000):
        return f"skipped group {name}: its saved details look wrong"
    try:
        if grp.getgrnam(name).gr_gid != gid:
            return f"group {name} already exists with another ID: files won't match"
        return None
    except KeyError:
        pass
    try:
        return f"skipped group {name}: ID {gid} already belongs to {grp.getgrgid(gid).gr_name}"
    except KeyError:
        pass
    run("groupadd", "--gid", str(gid), name)
    return None


def restore_memberships(user):
    """Put a restored account back in its groups, and among the administrators if it was."""
    known = {g.gr_name for g in grp.getgrall()}
    for group in user.get("groups") or []:
        if isinstance(group, str) and group in known and ACCOUNT_NAME.match(group):
            run("gpasswd", "-a", user["name"], group, check=False)
    if user.get("admin") is True:
        run("gpasswd", "-a", user["name"], users.ADMIN_GROUP, check=False)


def restore_config(pool):
    """Bring back what a pool remembers. Passwords were never saved, so those are listed."""
    if pool not in pools.writable_pools():
        raise FnasError(f"no writable imported pool: {pool}")
    saved = saved_config(pool)
    if not saved:
        raise FnasError(f"{pool} has no saved settings")
    if saved.get("version") != CONFIG_VERSION:
        raise FnasError(f"{pool} has settings from a different version of fnas")
    # Anyone can hand over a disk, so nothing on it is trusted
    channels = saved.get("alerts", {})
    job = saved.get("backup")
    if "email" in channels:
        alerts.check_email(channels["email"])
    if "ntfy" in channels:
        alerts.check_ntfy(channels["ntfy"]["url"])
    if job:
        pools.check_pool_name(job["source"])
        pools.check_pool_name(job["target"])
    if saved.get("lan"):
        shares.check_subnet(saved["lan"])

    notes = [note for note in map(restore_group, saved.get("groups", [])) if note]
    restored = saved.get("users", [])
    notes += [note for note in map(restore_user, restored) if note]
    for user in restored:
        if isinstance(user, dict) and isinstance(user.get("name"), str) and ACCOUNT_NAME.match(user["name"]):
            try:
                pwd.getpwnam(user["name"])
            except KeyError:
                continue
            restore_memberships(user)
            if user.get("kind") == "ssh" and isinstance(user.get("keys"), list) and user["keys"]:
                try:
                    ssh.set_keys(user["name"], "\n".join(k for k in user["keys"] if isinstance(k, str)))
                except FnasError as error:
                    notes.append(f"{user['name']}'s SSH keys weren't restored: {error}")
    saved_ssh = saved.get("ssh")
    try:
        ssh.sync_members()
        if isinstance(saved_ssh, dict):
            ssh.set_settings(saved_ssh.get("enabled") if isinstance(saved_ssh.get("enabled"), bool) else None,
                             saved_ssh.get("passwords") if isinstance(saved_ssh.get("passwords"), bool) else None)
    except FnasError as error:
        notes.append(f"the SSH settings weren't restored: {error}")
    if "checks" in saved:
        notes += [f"storage checks: {problem}" for problem in storage_checks.restore(saved["checks"])]
    sharing = saved.get("sharing")
    if isinstance(sharing, dict):
        for protocol in ("smb", "nfs"):
            if isinstance(sharing.get(protocol), dict):
                try:
                    shares.set_sharing(protocol, {key: value for key, value in sharing[protocol].items()
                                                  if key in shares.DEFAULT_SHARING[protocol]})
                except FnasError as error:
                    notes.append(f"the {protocol.upper()} settings weren't restored: {error}")
    if saved.get("lan"):
        shares.set_lan(saved["lan"])
    if "email" in channels:
        alerts.set_alert_channel("email", channels["email"])
        write_file(alerts.MSMTP_FILE, alerts.msmtp_config(channels["email"], None), mode=0o600)
        if channels["email"]["user"]:
            notes.append("email alerts need their password again: fnas alert email-set")
    if "ntfy" in channels:
        alerts.set_alert_channel("ntfy", channels["ntfy"])
    if job:
        write_json(backup.BACKUP_FILE, {**job, "since": int(time.time())})
    snapshots.write_sanoid_config()
    # The old install saved to every pool, so take over all of its copies at once
    save_config(claim=[p for p in pools.writable_pools()
                       if (saved_config(p) or {}).get("install") == saved.get("install")])
    return notes


def adopt_config(pool, confirm):
    expected = f"replace saved settings on {pool}"
    if confirm != expected:
        raise FnasError(f"this replaces the settings saved on {pool}\n"
                        f'confirm by passing --confirm "{expected}"')
    if pool not in pools.writable_pools():
        raise FnasError(f"no writable imported pool: {pool}")
    save_config(claim=[pool])
