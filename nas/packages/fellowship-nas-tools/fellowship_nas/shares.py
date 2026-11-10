"""Shares over NFS and SMB, who can use them, and the LAN subnet they are
limited to. Each pool keeps its shares' settings in shares.json in its config
folder, the same on every engine, and fnas writes both protocols'
configuration from them."""
import grp
import hashlib
import ipaddress
import os
import pwd
import re
import uuid

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
# everyone: every user and every allowed NFS client, writing as the share
# account. chosen: the users and groups in the share's access list.
SHARE_MODES = ("everyone", "chosen")
# What shares.json said before Phase 9; a "matched" share belonged to its folder's owner
OLD_MODES = {"shared": "everyone", "matched": "chosen"}
ACCESS_LEVELS = ("read", "write")
# Fixed so that files on a moved pool keep their owner
SHARE_ACCOUNT = "nas-share"
SHARE_ID = 3000
SETTING_KEYS = ("mode", "access", "nfs", "smb", "webdav", "sftp", "policy", "smb_options", "nfs_options")
DEFAULT_SMB = {"readonly": False, "hidden": False, "guest": False, "recycle": False, "recycle_days": 30,
               "timemachine": False, "timemachine_size": 0, "previous_versions": True}
# An empty client list means the home network, read-write, following the LAN subnet
DEFAULT_NFS = {"clients": [], "readonly": False, "mapping": "auto", "async": False}
NFS_MAPPINGS = ("auto", "all", "root", "none")
NFS_ACCESS = ("rw", "ro")
MAX_RECYCLE_DAYS = 3650
MAX_TIMEMACHINE_GB = 1_000_000
RECYCLE_DIR = ".recycle"
# NAS-wide SMB and NFS settings
SHARING_FILE = "/etc/fellowshipos/nas-sharing.json"
SMB_CONF = "/etc/samba/smb.conf"
SMB_GLOBAL_FILE = "global.conf"
NFS_CONF = "/etc/nfs.conf.d/fellowship.conf"
# lockd is in the kernel, so its fixed port is a sysctl rather than an nfs.conf setting
NFS_SYSCTL = "/etc/sysctl.d/60-fellowship-nfs.conf"
DEFAULT_SHARING = {
    "smb": {"workgroup": "WORKGROUP", "description": "FellowshipNAS", "min_protocol": "SMB2",
            "encryption": "desired", "windows_discovery": True, "mac_discovery": True},
    "nfs": {"v3": False, "threads": 8, "fixed_ports": False},
}
SMB_PROTOCOLS = {"SMB2": "SMB2_02", "SMB3": "SMB3_00"}
SMB_ENCRYPTION = ("desired", "required")
WORKGROUP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,14}$")
DESCRIPTION = re.compile(r"^[^\n\x00%\\]{0,64}$")
# Fixed ports, so a firewall can let NFS version 3 through
NFS_PORTS = {"mountd": 20048, "statd": 32765, "statd_out": 32766, "lockd": 32768}


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


def check_client(host):
    """A network or one address that may reach an NFS share: private, and never everyone."""
    try:
        network = ipaddress.ip_network(host, strict=False)
    except ValueError:
        raise FnasError(f"not a network or address: {host} (expected 192.168.1.0/24 or 192.168.1.20)") from None
    if not network.is_private or network.prefixlen < (8 if network.version == 4 else 48):
        raise FnasError(f"{network} isn't a private network on your LAN")
    return str(network.network_address) if network.num_addresses == 1 else str(network)


def checked_access(access):
    access = access if isinstance(access, dict) else {}
    return {kind: {name: level for name, level in (access.get(kind) or {}).items()
                   if isinstance(name, str) and ACCOUNT_NAME.match(name) and level in ACCESS_LEVELS}
            for kind in ("users", "groups")}


def checked_smb(options):
    options = options if isinstance(options, dict) else {}
    found = {}
    for key, default in DEFAULT_SMB.items():
        value = options.get(key, default)
        limit = MAX_RECYCLE_DAYS if key == "recycle_days" else MAX_TIMEMACHINE_GB
        if isinstance(default, bool):
            found[key] = value if isinstance(value, bool) else default
        else:
            found[key] = value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit \
                else default
    found["recycle_days"] = found["recycle_days"] or DEFAULT_SMB["recycle_days"]
    return found


def checked_nfs(options):
    options = options if isinstance(options, dict) else {}
    clients = []
    for client in options.get("clients") or []:
        try:
            if isinstance(client, dict) and client.get("access") in NFS_ACCESS:
                clients.append({"host": check_client(client.get("host", "")), "access": client["access"]})
        except FnasError:
            continue
    return {"clients": clients, "readonly": options.get("readonly") is True,
            "mapping": options.get("mapping") if options.get("mapping") in NFS_MAPPINGS else "auto",
            "async": options.get("async") is True}


def check_settings(name, settings):
    """A pool can come from anywhere, so its saved settings are checked before use."""
    if not SHARE_NAME.match(name) or name.lower() in RESERVED_SHARE_NAMES:
        return None
    mode = OLD_MODES.get(settings.get("mode"), settings.get("mode"))
    if mode not in SHARE_MODES:
        return None
    policy = settings.get("policy") or {}
    return {"mode": mode,
            # An older "matched" share has no list yet: its folder's owner is filled in when it's read
            "access": checked_access(settings.get("access")) if settings.get("access") is not None or mode == "everyone"
            else None,
            "nfs": settings.get("nfs") is True, "smb": settings.get("smb") is True,
            # Off unless it is asked for: a share reached over HTTPS is a new way in,
            # so turning it on is a decision rather than a default
            "webdav": settings.get("webdav") is True,
            # The same for SFTP, which is the only way in that comes with a login
            # of its own rather than a drive the desktop hands out
            "sftp": settings.get("sftp") is True,
            "policy": {period: policy[period] if isinstance(policy.get(period), int)
                       and 0 <= policy[period] <= snapshots.MAX_KEPT else kept
                       for period, kept in snapshots.DEFAULT_POLICY.items()},
            "smb_options": checked_smb(settings.get("smb_options")),
            "nfs_options": checked_nfs(settings.get("nfs_options"))}


def folder_owner(path):
    """Whoever owns a share's folder, as the one user of an older "matched" share."""
    try:
        name = pwd.getpwuid(os.stat(path).st_uid).pw_name
    except (OSError, KeyError):
        return {"users": {}, "groups": {}}
    return {"users": {name: "write"} if ACCOUNT_NAME.match(name) and name != SHARE_ACCOUNT else {}, "groups": {}}


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
                settings = check_settings(name, settings)
            if settings:
                if settings["access"] is None:
                    settings["access"] = folder_owner(volume["path"])
                found.append({"name": name, "pool": pool["name"], "engine": pool["engine"],
                              # So the desktop needn't know which engines keep earlier versions
                              "snapshots": engine.PRESENTATION["snapshots"],
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
    current = {s["name"]: {key: s[key] for key in SETTING_KEYS}
               for s in list_shares() if s["pool"] == pool_name}
    if changed:
        current[changed["name"]] = {key: changed[key] for key in SETTING_KEYS}
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


def sharing_settings():
    """The NAS-wide SMB and NFS settings, with defaults for anything unset."""
    saved = read_json(SHARING_FILE, {})
    return {protocol: {**defaults, **{k: v for k, v in (saved.get(protocol) or {}).items() if k in defaults}}
            for protocol, defaults in DEFAULT_SHARING.items()}


def nfs_mapping(share):
    mapping = share["nfs_options"]["mapping"]
    if mapping == "auto":
        mapping = "all" if share["mode"] == "everyone" else "root"
    return {"all": ["all_squash", f"anonuid={SHARE_ID}", f"anongid={SHARE_ID}"],
            "root": ["root_squash"], "none": ["no_root_squash"]}[mapping]


def nfs_clients(share, subnet):
    options = share["nfs_options"]
    clients = options["clients"] or [{"host": subnet, "access": "rw"}]
    lines = []
    for client in clients:
        writable = client["access"] == "rw" and not options["readonly"] and not share["readonly"]
        flags = ["rw" if writable else "ro", "async" if options["async"] else "sync", "no_subtree_check"]
        if share.get("engine") == "mergerfs":
            # A FUSE filesystem has no device number for NFS to name it by, so it gets a fixed one
            flags.append("fsid=" + str(uuid.UUID(hashlib.md5(share["path"].encode()).hexdigest())))
        lines.append(f"{client['host']}({','.join(flags + nfs_mapping(share))})")
    return " ".join(lines)


def exports_config(shares, subnet):
    lines = ["# Generated by fnas from each pool's shares.json: changes here are overwritten"]
    lines += [f'"{share["path"]}" {nfs_clients(share, subnet)}' for share in shares]
    return "\n".join(lines) + "\n"


def nfs_server_config(settings):
    lines = ["# Generated by fnas from the NFS settings in the desktop: changes here are overwritten",
             "[nfsd]", f"vers3={'y' if settings['v3'] else 'n'}", f"threads={settings['threads']}"]
    if settings["fixed_ports"]:
        lines += ["", "[mountd]", f"port={NFS_PORTS['mountd']}",
                  "", "[statd]", f"port={NFS_PORTS['statd']}", f"outgoing-port={NFS_PORTS['statd_out']}",
                  "", "[lockd]", f"port={NFS_PORTS['lockd']}", f"udp-port={NFS_PORTS['lockd']}"]
    return "\n".join(lines) + "\n"


def changed(path, wanted):
    """Write a file only if it would change, and say whether it did."""
    try:
        with open(path) as file:
            if file.read() == wanted:
                return False
    except FileNotFoundError:
        if wanted is None:
            return False
    if wanted is None:
        os.remove(path)
    else:
        write_file(path, wanted)
    return True


def write_nfs_server_config():
    """Returns whether anything changed, since the servers only read these when they start."""
    if not os.path.isdir(os.path.dirname(NFS_CONF)):
        return False
    settings = sharing_settings()["nfs"]
    ports = (f"fs.nfs.nlm_tcpport = {NFS_PORTS['lockd']}\nfs.nfs.nlm_udpport = {NFS_PORTS['lockd']}\n"
             if settings["fixed_ports"] else None)
    server = changed(NFS_CONF, nfs_server_config(settings))
    lockd = os.path.isdir(os.path.dirname(NFS_SYSCTL)) and changed(NFS_SYSCTL, ports)
    if lockd:
        for key in ("fs.nfs.nlm_tcpport", "fs.nfs.nlm_udpport"):
            run("sysctl", "-q", "-w", f"{key}={NFS_PORTS['lockd'] if ports else 0}", check=False)
    return server or lockd


def write_nfs_config(shares):
    if not os.path.isdir(EXPORTS_DIR):
        return
    try:
        subnet = lan_subnet()
    except FnasError:
        shares = subnet = None
    write_file(EXPORTS_FILE, exports_config([s for s in shares or [] if s["nfs"]], subnet))
    if write_nfs_server_config():
        # statd and lockd only take a new port when they start again
        run("systemctl", "try-restart", "rpc-statd.service", "nfs-server.service", check=False)
    run("exportfs", "-ra", check=False)


def smb_names(access, level=None):
    """Samba's spelling of the users and groups with access, or with one level of it."""
    return " ".join([name for name, found in sorted(access["users"].items()) if level in (None, found)]
                    + [f"@{name}" for name, found in sorted(access["groups"].items()) if level in (None, found)])


def smb_share_config(share, subnet):
    options = share["smb_options"]
    readonly = share["readonly"] or options["readonly"]
    lines = ["# Generated by fnas from the pool's shares.json: changes here are overwritten",
             f"[{share['name']}]",
             f"path = {share['path']}",
             "read only = " + ("yes" if readonly or share["mode"] == "chosen" else "no"),
             f"hosts allow = {subnet} 127.0.0.1"]
    if options["hidden"]:
        lines.append("browseable = no")
    vfs = []
    if options["timemachine"]:
        vfs += ["fruit", "streams_xattr"]
    if share["engine"] == "zfs" and options["previous_versions"]:
        vfs.append("shadow_copy2")
    if options["recycle"] and not readonly:
        vfs.append("recycle")
    if vfs:
        lines.append("vfs objects = " + " ".join(vfs))
    if "shadow_copy2" in vfs:
        lines += ["shadow:snapdir = .zfs/snapshot",
                  # Sanoid ends each name with its period. The prefix setting makes Samba
                  # keep the real names, which it couldn't rebuild from the format alone.
                  # Its service runs in UTC, so shadow:localtime must stay off.
                  "shadow:snapprefix = ^autosnap$",
                  "shadow:delimiter = _",
                  "shadow:format = _%Y-%m-%d_%H:%M:%S",
                  "shadow:sort = desc"]
    if "recycle" in vfs:
        lines += [f"recycle:repository = {RECYCLE_DIR}/%U", "recycle:keeptree = yes", "recycle:versions = yes",
                  "recycle:touch_mtime = yes", "recycle:directory_mode = 0770"]
    if options["timemachine"]:
        lines.append("fruit:time machine = yes")
        if options["timemachine_size"]:
            lines.append(f"fruit:time machine max size = {options['timemachine_size']}G")
    if share["mode"] == "everyone":
        lines += [f"force user = {SHARE_ACCOUNT}",
                  f"force group = {SHARE_ACCOUNT}",
                  "create mask = 0664",
                  "directory mask = 2775"]
        if options["guest"]:
            # Guests read; the guest account isn't the share account, so it can't write
            lines += ["guest ok = yes", "read list = nobody"]
    else:
        lines += [f"valid users = {smb_names(share['access']) or SHARE_ACCOUNT}",
                  "inherit acls = yes"]
        writers = smb_names(share["access"], "write")
        if writers and not readonly:
            lines.append(f"write list = {writers}")
    return "\n".join(lines) + "\n"


def smb_global_config(settings, shares):
    lines = ["# Generated by fnas from the SMB settings in the desktop: changes here are overwritten",
             f"workgroup = {settings['workgroup']}",
             f"server string = {settings['description']}",
             f"server min protocol = {SMB_PROTOCOLS[settings['min_protocol']]}",
             f"server smb encrypt = {settings['encryption']}",
             "multicast dns register = " + ("yes" if settings["mac_discovery"] else "no")]
    if any(s["smb"] and s["mode"] == "everyone" and s["smb_options"]["guest"] for s in shares):
        lines.append("map to guest = Bad User")
    if any(s["smb"] and s["smb_options"]["timemachine"] for s in shares):
        lines += ["fruit:metadata = stream", "fruit:model = MacSamba"]
    return "\n".join(lines) + "\n"


def include_global(path):
    """Put the include for our global settings at the end of smb.conf's [global]
    section, after Debian's own lines, so ours win."""
    include = f"include = {path}"
    try:
        with open(SMB_CONF) as file:
            lines = [line for line in file.read().splitlines() if line.strip() != include]
    except FileNotFoundError:
        return
    start = next((i for i, line in enumerate(lines) if line.strip().lower() == "[global]"), None)
    if start is None:
        lines = ["[global]", include] + lines
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, include)
    write_file(SMB_CONF, "\n".join(lines) + "\n")


def write_smb_config(shares):
    if not os.path.isdir(SMB_DIR):
        return
    settings = sharing_settings()["smb"]
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
    global_path = os.path.join(SMB_DIR, SMB_GLOBAL_FILE)
    write_file(global_path, smb_global_config(settings, shares))
    include_global(global_path)
    run("systemctl", "enable" if settings["windows_discovery"] else "disable", "--now", "wsdd2.service",
        check=False)
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


def check_access(access):
    """Users and groups for a share, as {"users": {name: level}, "groups": {...}}, all of which must exist."""
    for name, level in access["users"].items():
        if level not in ACCESS_LEVELS:
            raise FnasError(f"{name}: access is read or write, not {level}")
        try:
            pwd.getpwnam(name)
        except KeyError:
            raise FnasError(f"no such user: {name}") from None
    for name, level in access["groups"].items():
        if level not in ACCESS_LEVELS:
            raise FnasError(f"{name}: access is read or write, not {level}")
        try:
            grp.getgrnam(name)
        except KeyError:
            raise FnasError(f"no such group: {name}") from None
    return access


def acl_entries(access, default=False):
    prefix = "default:" if default else ""
    entries = [f"{prefix}user::rwx", f"{prefix}group::---", f"{prefix}other::---", f"{prefix}mask::rwx"]
    entries += [f"{prefix}user:{name}:{'rwX' if level == 'write' else 'rX'}" for name, level in access["users"].items()]
    entries += [f"{prefix}group:{name}:{'rwX' if level == 'write' else 'rX'}"
                for name, level in access["groups"].items()]
    return ",".join(entries)


def apply_access(share):
    """Set the folder's owner, mode and ACLs from the share's access, for every file in it.
    Everyone: the share account owns it all. Chosen: root owns the folder and the
    ACL lists who reads and writes, with default ACLs so new files inherit it."""
    path = share["path"]
    run("setfacl", "-R", "-b", path)
    if share["mode"] == "everyone":
        run("chown", "-R", f"{SHARE_ID}:{SHARE_ID}", path)
        run("chmod", "-R", "u+rwX,g+rwX,o+rX", path)
        os.chmod(path, 0o2775)
    else:
        os.chown(path, 0, 0)
        os.chmod(path, 0o2770)
        run("setfacl", "-R", "-m", acl_entries(share["access"]), path)
        # Default entries only exist on folders
        run("find", path, "-type", "d", "-exec", "setfacl", "-m", acl_entries(share["access"], default=True),
            "{}", "+")


def new_settings(mode, access, nfs, smb, webdav=False, sftp=False):
    return {"mode": mode, "access": access, "nfs": nfs, "smb": smb, "webdav": webdav,
            "sftp": sftp, "policy": dict(snapshots.DEFAULT_POLICY),
            "smb_options": dict(DEFAULT_SMB), "nfs_options": dict(DEFAULT_NFS)}


def create_share(name, pool, everyone, access, nfs, smb, webdav=False, sftp=False):
    if not nfs and not smb and not webdav and not sftp:
        raise FnasError("choose at least one of --nfs, --smb, --webdav and --sftp")
    if smb and not os.path.isdir(SMB_DIR):
        raise FnasError("SMB needs the fellowship-nas-smb package")
    if everyone and (access["users"] or access["groups"]):
        raise FnasError("a share is either for --everyone or for chosen --user and --group, not both")
    if not everyone and not (access["users"] or access["groups"]):
        raise FnasError("say who can use the share: --everyone, or --user NAME=read|write and --group NAME=read|write")
    check_access(access)
    found = pools.find_pool(pool)
    if found["readonly"]:
        raise FnasError(f"{pool} is imported read-only")
    check_name(name)
    lan_subnet()

    path = pools.ENGINES[found["engine"]].create_volume(pool, name)
    share = {"name": name, "path": path,
             **new_settings("everyone" if everyone else "chosen", access, nfs, smb, webdav, sftp)}
    apply_access(share)
    save_settings(pool, share)
    write_config()


def set_share(name, nfs, smb, webdav=None, sftp=None):
    if nfs is None and smb is None and webdav is None and sftp is None:
        raise FnasError("nothing to change: pass --nfs, --smb, --webdav or --sftp")
    share = find_share(name)
    if smb == "on" and not os.path.isdir(SMB_DIR):
        raise FnasError("SMB needs the fellowship-nas-smb package")
    if nfs is not None:
        share["nfs"] = nfs == "on"
    if smb is not None:
        share["smb"] = smb == "on"
    if webdav is not None:
        share["webdav"] = webdav == "on"
    if sftp is not None:
        share["sftp"] = sftp == "on"
    save_settings(share["pool"], share)
    write_config()


def writable_share(name):
    share = find_share(name)
    if share["readonly"]:
        raise FnasError(f"{share['pool']} is imported read-only")
    return share


def set_access(name, everyone, access):
    """Change who can use a share, rewriting the permissions of every file in it."""
    share = writable_share(name)
    if everyone and (access["users"] or access["groups"]):
        raise FnasError("a share is either for --everyone or for chosen --user and --group, not both")
    if not everyone and not (access["users"] or access["groups"]):
        raise FnasError("say who can use the share: --everyone, or --user and --group")
    share["mode"] = "everyone" if everyone else "chosen"
    share["access"] = {"users": {}, "groups": {}} if everyone else check_access(access)
    if not everyone:
        # A guest has no account to put on the list
        share["smb_options"]["guest"] = False
    apply_access(share)
    save_settings(share["pool"], share)
    write_config()


def set_smb_options(name, changes):
    share = writable_share(name)
    options = share["smb_options"]
    for key, value in changes.items():
        if value is not None:
            options[key] = value
    if options["guest"] and share["mode"] != "everyone":
        raise FnasError("guests can only use shares that are for everyone: a guest has no account to list")
    if options["guest"] and sharing_settings()["smb"]["encryption"] == "required":
        raise FnasError("guests can't connect while SMB encryption is required, since a guest has no password to "
                        "encrypt with: set encryption to when supported first")
    if not 1 <= options["recycle_days"] <= MAX_RECYCLE_DAYS:
        raise FnasError(f"the recycle bin keeps files for 1 to {MAX_RECYCLE_DAYS} days")
    if not 0 <= options["timemachine_size"] <= MAX_TIMEMACHINE_GB:
        raise FnasError("the Time Machine limit is in GB, or 0 for none")
    if options["previous_versions"] and share["engine"] not in ("zfs", "btrfs"):
        options["previous_versions"] = False
    save_settings(share["pool"], share)
    write_config()


def set_nfs_options(name, clients, home_network, readonly, mapping, async_writes):
    share = writable_share(name)
    options = share["nfs_options"]
    if clients is not None and home_network:
        raise FnasError("choose --client or --home-network, not both")
    if clients is not None:
        options["clients"] = [{"host": check_client(host), "access": access} for host, access in clients]
    if home_network:
        options["clients"] = []
    if readonly is not None:
        options["readonly"] = readonly
    if mapping is not None:
        options["mapping"] = mapping
    if async_writes is not None:
        options["async"] = async_writes
    save_settings(share["pool"], share)
    write_config()


def set_sharing(protocol, changes):
    """Change NAS-wide SMB or NFS settings."""
    settings = sharing_settings()
    current = settings[protocol]
    for key, value in changes.items():
        if value is not None:
            current[key] = value
    if protocol == "smb":
        if not WORKGROUP.match(current["workgroup"]):
            raise FnasError("workgroups use letters, numbers, - and _, and are at most 15 characters")
        if not DESCRIPTION.match(current["description"]):
            raise FnasError("the description is one line of at most 64 characters, without % or \\")
        if current["min_protocol"] not in SMB_PROTOCOLS or current["encryption"] not in SMB_ENCRYPTION:
            raise FnasError("unknown SMB protocol or encryption setting")
        if not os.path.isdir(SMB_DIR):
            raise FnasError("SMB needs the fellowship-nas-smb package")
        guests = [s["name"] for s in list_shares() if s["mode"] == "everyone" and s["smb_options"]["guest"]]
        if current["encryption"] == "required" and guests:
            raise FnasError("guests can't connect while encryption is required, since a guest has no password to "
                            f"encrypt with: turn guests off first on {', '.join(guests)}")
    elif not 1 <= current["threads"] <= 64:
        raise FnasError("NFS uses 1 to 64 server threads")
    write_json(SHARING_FILE, settings)
    write_config()
    return settings


def forget(kind, name):
    """Take a removed user or group off every share's access list."""
    for share in list_shares():
        if not share["readonly"] and name in share["access"][kind]:
            del share["access"][kind][name]
            save_settings(share["pool"], share)
    write_config()


def rename(kind, old, new):
    for share in list_shares():
        if not share["readonly"] and old in share["access"][kind]:
            share["access"][kind][new] = share["access"][kind].pop(old)
            save_settings(share["pool"], share)
    write_config()


def shares_of(user, known=None):
    """The shares a user can use, and how, through their own entry, a group, or
    everyone. known is list_shares() when the caller already has it."""
    groups = {g.gr_name for g in grp.getgrall() if user in g.gr_mem}
    found = []
    for share in (list_shares() if known is None else known):
        if share["mode"] == "everyone":
            found.append({"name": share["name"], "level": "write", "through": "everyone"})
            continue
        levels = [(level, "own") for name, level in share["access"]["users"].items() if name == user]
        levels += [(level, f"group {name}") for name, level in share["access"]["groups"].items() if name in groups]
        if levels:
            level, through = max(levels, key=lambda found: found[0] == "write")
            found.append({"name": share["name"], "level": level, "through": through})
    return found


def roots_of(user):
    """The shares a person can use, where each one is on disk, and whether it has
    snapshots. Files and WebDAV both ask this rather than working out paths."""
    known = list_shares()
    by_name = {share["name"]: share for share in known}
    found = []
    for used in shares_of(user, known):
        share = by_name.get(used["name"])
        if not share:
            continue
        found.append({"share": share["name"], "path": share["path"], "level": used["level"],
                      "through": used["through"], "snapshots": share["snapshots"],
                      "webdav": share["webdav"], "sftp": share["sftp"]})
    return found


def empty_recycle_bins():
    """Clear files out of each recycle bin once they've been there the share's number of days."""
    for share in list_shares():
        options = share["smb_options"]
        folder = os.path.join(share["path"], RECYCLE_DIR)
        if options["recycle"] and not share["readonly"] and os.path.isdir(folder):
            run("find", folder, "-mindepth", "1", "-mtime", f"+{options['recycle_days']}", "-delete", check=False)
            run("find", folder, "-mindepth", "1", "-type", "d", "-empty", "-delete", check=False)


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
    if not (ACCOUNT_NAME.match(name) and 1000 <= account.pw_uid < 60000 and name != SHARE_ACCOUNT):
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
