"""Shares over NFS and SMB, and the LAN subnet they are limited to. Each pool
keeps its shares' settings in shares.json in its config folder, the same on
every engine, and fnas writes both protocols' configuration from them."""
import ipaddress
import os
import pwd
import re

from .common import ACCOUNT_NAME, FnasError, read_json, run, write_file, write_json
from . import pools, snapshots


LAN_FILE = "/etc/fellowshipos/nas-lan"
SMB_DIR = "/etc/samba/fellowship"
EXPORTS_DIR = "/etc/exports.d"
EXPORTS_FILE = os.path.join(EXPORTS_DIR, "fellowship.exports")
SETTINGS_FILE = "shares.json"
SETTINGS_VERSION = 1
SHARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
# Samba's own sections, which a share can't reuse
RESERVED_SHARE_NAMES = {"global", "homes", "printers", "print$"}
SHARE_MODES = ("shared", "matched")
# Fixed so that files on a moved pool keep their owner
SHARE_ACCOUNT = "nas-share"
SHARE_ID = 3000


def check_subnet(value):
    """Return a private IPv4 subnet in canonical form, so nothing is exported to the world."""
    try:
        subnet = ipaddress.IPv4Network(value, strict=False)
    except ValueError:
        raise FnasError(f"not a subnet: {value} (expected something like 192.168.1.0/24)") from None
    if not subnet.is_private or subnet.prefixlen < 8 or subnet.prefixlen > 30:
        raise FnasError(f"{subnet} isn't a private LAN subnet between /8 and /30")
    return str(subnet)


def lan_subnet():
    try:
        with open(LAN_FILE) as file:
            return check_subnet(file.read().strip())
    except FileNotFoundError:
        raise FnasError("no LAN subnet set: run fnas lan set 192.168.1.0/24") from None


def settings_path(pool):
    directory = pools.ENGINES[pool["engine"]].config_dir(pool["name"])
    return os.path.join(directory, SETTINGS_FILE) if directory else None


def check_settings(name, settings):
    """A pool can come from anywhere, so its saved settings are checked before use."""
    if not SHARE_NAME.match(name) or name.lower() in RESERVED_SHARE_NAMES:
        return None
    if settings.get("mode") not in SHARE_MODES:
        return None
    policy = settings.get("policy") or {}
    return {"mode": settings["mode"], "nfs": settings.get("nfs") is True, "smb": settings.get("smb") is True,
            "policy": {period: policy[period] if isinstance(policy.get(period), int)
                       and 0 <= policy[period] <= snapshots.MAX_KEPT else kept
                       for period, kept in snapshots.DEFAULT_POLICY.items()}}


def saved_settings(pool):
    path = settings_path(pool)
    saved = read_json(path, {}) if path else {}
    return {name: checked for name, settings in (saved.get("shares") or {}).items()
            if (checked := check_settings(name, settings))}


def list_shares():
    found = []
    for pool in pools.list_pools():
        engine = pools.ENGINES[pool["engine"]]
        saved = saved_settings(pool)
        for name, volume in engine.share_volumes(pool["name"]).items():
            # Shares made before revision 3 kept their settings on the volume
            settings = saved.get(name) or volume.pop("legacy", None)
            volume.pop("legacy", None)
            readonly = volume.pop("readonly", False)
            if settings:
                found.append({"name": name, "pool": pool["name"], "engine": pool["engine"],
                              "readonly": pool["readonly"] or readonly, **volume, **settings})
    return sorted(found, key=lambda share: share["name"].lower())


def find_share(name):
    for share in list_shares():
        if share["name"] == name:
            return share
    raise FnasError(f"no such share: {name}")


def save_settings(pool_name, changed=None, removed=None):
    """Write a pool's shares.json from its shares, with one share changed or removed."""
    pool = pools.find_pool(pool_name)
    if pool["readonly"]:
        raise FnasError(f"{pool_name} is imported read-only")
    path = settings_path(pool)
    if not path:
        raise FnasError(f"{pool_name} has no config folder")
    current = {s["name"]: {key: s[key] for key in ("mode", "nfs", "smb", "policy")}
               for s in list_shares() if s["pool"] == pool_name}
    if changed:
        current[changed["name"]] = {key: changed[key] for key in ("mode", "nfs", "smb", "policy")}
    current.pop(removed, None)
    write_json(path, {"version": SETTINGS_VERSION, "shares": current})


def migrate():
    """Move settings kept on ZFS properties into shares.json, once per pool."""
    for pool in pools.list_pools():
        if pool["readonly"]:
            continue
        engine = pools.ENGINES[pool["engine"]]
        legacy = [name for name, volume in engine.share_volumes(pool["name"]).items() if volume.get("legacy")]
        if legacy:
            save_settings(pool["name"])
            for name in legacy:
                engine.clear_legacy(pool["name"], name)


def nfs_options(share, subnet):
    options = ["ro" if share["readonly"] else "rw", "sync", "no_subtree_check"]
    if share["mode"] == "shared":
        options += ["all_squash", f"anonuid={SHARE_ID}", f"anongid={SHARE_ID}"]
    else:
        options.append("root_squash")
    return f"{subnet}({','.join(options)})"


def exports_config(shares, subnet):
    lines = ["# Generated by fnas from each pool's shares.json: changes here are overwritten"]
    lines += [f'"{share["path"]}" {nfs_options(share, subnet)}' for share in shares]
    return "\n".join(lines) + "\n"


def write_nfs_config(shares):
    if not os.path.isdir(EXPORTS_DIR):
        return
    try:
        subnet = lan_subnet()
    except FnasError:
        shares = subnet = None
    write_file(EXPORTS_FILE, exports_config([s for s in shares or [] if s["nfs"]], subnet))
    run("exportfs", "-ra", check=False)


def smb_share_config(share, subnet):
    lines = ["# Generated by fnas from the pool's shares.json: changes here are overwritten",
             f"[{share['name']}]",
             f"path = {share['path']}",
             "read only = " + ("yes" if share["readonly"] else "no"),
             f"hosts allow = {subnet} 127.0.0.1"]
    if share["engine"] == "zfs":
        lines += ["vfs objects = shadow_copy2",
                  "shadow:snapdir = .zfs/snapshot",
                  # Sanoid ends each name with its period. The prefix setting makes Samba
                  # keep the real names, which it couldn't rebuild from the format alone.
                  # Its service runs in UTC, so shadow:localtime must stay off.
                  "shadow:snapprefix = ^autosnap$",
                  "shadow:delimiter = _",
                  "shadow:format = _%Y-%m-%d_%H:%M:%S",
                  "shadow:sort = desc"]
    if share["mode"] == "shared":
        lines += [f"force user = {SHARE_ACCOUNT}",
                  f"force group = {SHARE_ACCOUNT}",
                  "create mask = 0664",
                  "directory mask = 2775"]
    return "\n".join(lines) + "\n"


def write_smb_config(shares):
    if not os.path.isdir(SMB_DIR):
        return
    shared = [s for s in shares if s["smb"]]
    directory = os.path.join(SMB_DIR, "shares")
    os.makedirs(directory, exist_ok=True)
    wanted = {s["name"] + ".conf" for s in shared}
    for name in set(os.listdir(directory)) - wanted:
        os.remove(os.path.join(directory, name))
    for share in shared:
        write_file(os.path.join(directory, share["name"] + ".conf"), smb_share_config(share, lan_subnet()))
    write_file(os.path.join(SMB_DIR, "shares.conf"),
               "".join(f"include = {os.path.join(directory, name)}\n" for name in sorted(wanted)))
    run("smbcontrol", "smbd", "reload-config", check=False)


def write_config(leaving=None):
    """Rebuild the NFS exports and every Samba share file from what the pools
    say now, leaving out a pool about to be exported."""
    shares = [s for s in list_shares() if s["pool"] != leaving]
    write_nfs_config(shares)
    write_smb_config(shares)


def check_name(name):
    if not SHARE_NAME.match(name) or name.lower() in RESERVED_SHARE_NAMES:
        raise FnasError("share names use letters, numbers, - and _, start with a letter "
                        "or number, and are at most 32 characters")
    # Samba share names ignore case, so two shares can't differ only by it
    if name.lower() in [s["name"].lower() for s in list_shares()]:
        raise FnasError(f"a share named {name} already exists")


def create_share(name, pool, mode, owner, nfs, smb):
    if not nfs and not smb:
        raise FnasError("choose at least one of --nfs and --smb")
    if smb and not os.path.isdir(SMB_DIR):
        raise FnasError("SMB needs the fellowship-nas-smb package")
    if mode == "matched" and not owner:
        raise FnasError("matched shares need an --owner")
    if mode == "shared" and owner:
        raise FnasError(f"shared folders belong to {SHARE_ACCOUNT}, so --owner doesn't apply")
    found = pools.find_pool(pool)
    if found["readonly"]:
        raise FnasError(f"{pool} is imported read-only")
    check_name(name)
    uid, gid = SHARE_ID, SHARE_ID
    if owner:
        try:
            account = pwd.getpwnam(owner)
        except KeyError:
            raise FnasError(f"no such user: {owner}") from None
        uid, gid = account.pw_uid, account.pw_gid
    lan_subnet()

    path = pools.ENGINES[found["engine"]].create_volume(pool, name)
    os.chown(path, uid, gid)
    # chown clears the setgid bit, so the mode comes second
    os.chmod(path, 0o2775 if mode == "shared" else 0o2770)
    save_settings(pool, {"name": name, "mode": mode, "nfs": nfs, "smb": smb,
                         "policy": dict(snapshots.DEFAULT_POLICY)})
    write_config()


def set_share(name, nfs, smb):
    if nfs is None and smb is None:
        raise FnasError("nothing to change: pass --nfs or --smb")
    share = find_share(name)
    if smb == "on" and not os.path.isdir(SMB_DIR):
        raise FnasError("SMB needs the fellowship-nas-smb package")
    if nfs is not None:
        share["nfs"] = nfs == "on"
    if smb is not None:
        share["smb"] = smb == "on"
    save_settings(share["pool"], share)
    write_config()


def delete_share(name, confirm):
    share = find_share(name)
    if share["readonly"]:
        raise FnasError(f"{share['pool']} is imported read-only")
    engine = pools.ENGINES[share["engine"]]
    kept = engine.volume_snapshots(share)
    count = len(kept)
    expected = f"delete {name} and {count} snapshot" + ("" if count == 1 else "s")
    if confirm != expected:
        lost = "".join(f"\n    {snapshot}" for snapshot in kept)
        raise FnasError(f"this destroys all data in {share['path']}{lost}\n"
                        f'confirm by passing --confirm "{expected}"')
    engine.destroy_volume(share)
    save_settings(share["pool"], removed=name)
    write_config()


def set_smb_password(name, password):
    """Let a local account log in to SMB shares; Samba keeps its own password list."""
    if not os.path.isdir(SMB_DIR):
        raise FnasError("SMB needs the fellowship-nas-smb package")
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        raise FnasError(f"no such user: {name}") from None
    if not (ACCOUNT_NAME.match(name) and 1000 <= account.pw_uid < 60000
            and not account.pw_shell.endswith(("nologin", "false"))):
        raise FnasError(f"{name} isn't a person's account")
    if not password or "\n" in password:
        raise FnasError("the password can't be empty or span lines")
    run("smbpasswd", "-s", "-a", name, stdin=f"{password}\n{password}\n")


def set_lan(value):
    subnet = check_subnet(value)
    os.makedirs(os.path.dirname(LAN_FILE), exist_ok=True)
    write_file(LAN_FILE, subnet + "\n")
    write_config()
    return subnet
