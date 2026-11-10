"""People's accounts and groups. Every account has one password, set for Linux
and Samba together, so the desktop and every way to share take the same one."""
import grp
import os
import pwd
import re

from .common import ACCOUNT_NAME, FnasError, run


ADMIN_GROUP = "nas-admins"
FIRST_ID, LAST_ID = 1000, 60000
# What an account may do besides using shares
KINDS = {"shares": "/usr/sbin/nologin", "ssh": "/bin/bash"}
FULL_NAME = re.compile(r"^[^:,\n\x00]{0,64}$")
# Accounts in the people range that belong to the NAS itself
SERVICE_ACCOUNTS = {"nas-share"}


def is_person(account):
    return (FIRST_ID <= account.pw_uid < LAST_ID and ACCOUNT_NAME.match(account.pw_name)
            and account.pw_name not in SERVICE_ACCOUNTS)


def smb_accounts():
    """Accounts with a Samba password, and whether each is turned off."""
    result = run("pdbedit", "-L", "-w", check=False)
    found = {}
    for line in result.stdout.splitlines():
        fields = line.split(":")
        if len(fields) > 4:
            found[fields[0]] = "D" in fields[4]
    return found


SHADOW = "/etc/shadow"
# Turning an account off sets its expiry to this day, the day after 1970 began
OFF_EXPIRY = "1"


def shadow():
    """Each account's password state: whether it has a password, and whether it's turned off."""
    found = {}
    try:
        with open(SHADOW) as file:
            for line in file:
                fields = line.rstrip("\n").split(":")
                if len(fields) > 7:
                    secret = fields[1].lstrip("!")
                    found[fields[0]] = {"password": bool(secret) and secret != "*", "off": fields[7] == OFF_EXPIRY}
    except OSError:
        pass
    return found


def private_group(account):
    """The group Debian makes for each account, which isn't one people manage."""
    try:
        group = grp.getgrgid(account.pw_gid)
    except KeyError:
        return None
    return group.gr_name if group.gr_name == account.pw_name else None


def managed_group_names():
    """Groups in the account range that aren't an account's own group."""
    own = {private_group(a) for a in pwd.getpwall() if is_person(a)}
    return {g.gr_name for g in grp.getgrall()
            if FIRST_ID <= g.gr_gid < LAST_ID and g.gr_name not in own | SERVICE_ACCOUNTS
            and ACCOUNT_NAME.match(g.gr_name)}


def system_group_names():
    """The machine's own groups: video, render, wheel, docker and the rest.

    A share can be given to one, so an app that already runs here (Plex, the
    *arr apps, a backup tool) reads what it needs without a second account, and
    a person can join one. fnas never renames or removes them, because the
    packages that made them expect them to stay."""
    own = {private_group(account) for account in pwd.getpwall()}
    return {g.gr_name for g in grp.getgrall()
            if not FIRST_ID <= g.gr_gid < LAST_ID and ACCOUNT_NAME.match(g.gr_name)
            and g.gr_name not in own | SERVICE_ACCOUNTS | {ADMIN_GROUP}}


def list_groups(include_system=False):
    """Groups people manage, and with include_system the machine's own too."""
    managed = managed_group_names()
    wanted = managed | (system_group_names() if include_system else set())
    return sorted(({"name": g.gr_name, "gid": g.gr_gid, "members": sorted(g.gr_mem),
                    "system": g.gr_name not in managed}
                   for g in grp.getgrall() if g.gr_name in wanted),
                  key=lambda group: group["name"])


def list_users():
    smb = smb_accounts()
    passwords = shadow()
    mine = managed_group_names() | system_group_names()
    admin_names = admins()
    found = []
    for account in sorted(pwd.getpwall(), key=lambda a: a.pw_name):
        if not is_person(account):
            continue
        name = account.pw_name
        found.append({"name": name, "uid": account.pw_uid, "full_name": account.pw_gecos.split(",")[0],
                      "kind": "shares" if account.pw_shell.endswith(("nologin", "false")) else "ssh",
                      "admin": name in admin_names,
                      "groups": sorted(g.gr_name for g in grp.getgrall() if name in g.gr_mem and g.gr_name in mine),
                      "password": passwords.get(name, {}).get("password", False),
                      "smb_password": name in smb, "enabled": not passwords.get(name, {}).get("off", False)})
    return found


def find_user(name):
    for user in list_users():
        if user["name"] == name:
            return user
    raise FnasError(f"no such user: {name}")


def find_group(name, system=False):
    """A group people manage, with system=True also accepting the machine's own."""
    for group in list_groups(include_system=system):
        if group["name"] == name:
            return group
    if not system and name in system_group_names():
        raise FnasError(f"{name} belongs to the machine, so it can't be renamed or removed here")
    raise FnasError(f"no such group: {name}")


def check_new_name(name, what):
    if not ACCOUNT_NAME.match(name):
        raise FnasError(f"{what} names use lowercase letters, numbers, - and _, start with a letter, "
                        "and are at most 32 characters")
    taken = {a.pw_name for a in pwd.getpwall()} | {g.gr_name for g in grp.getgrall()}
    if name in taken:
        raise FnasError(f"{name} is already used by an account or group on this NAS")


def check_password(password):
    if not password or "\n" in password or ":" in password:
        raise FnasError("the password can't be empty, contain a colon, or span lines")
    if len(password) < 8:
        raise FnasError("the password needs at least 8 characters")


def check_full_name(full_name):
    if not FULL_NAME.match(full_name or ""):
        raise FnasError("full names can't contain : or , and are at most 64 characters")


def set_password(name, password):
    """One password for Linux and Samba."""
    find_user(name)
    check_password(password)
    run("chpasswd", stdin=f"{name}:{password}\n")
    if os.path.isdir("/etc/samba"):
        run("smbpasswd", "-s", "-a", name, stdin=f"{password}\n{password}\n")


def admins():
    try:
        return set(grp.getgrnam(ADMIN_GROUP).gr_mem)
    except KeyError:
        return set()


def check_not_last_admin(name):
    if admins() == {name}:
        raise FnasError(f"{name} is the only administrator, so the NAS would have nobody to manage it")


def set_admin(name, admin):
    if admin:
        run("gpasswd", "-a", name, ADMIN_GROUP)
    elif name in admins():
        check_not_last_admin(name)
        run("gpasswd", "-d", name, ADMIN_GROUP)


def set_groups(name, wanted):
    current = set(find_user(name)["groups"])
    for group in wanted:
        # A person may join the machine's own group, such as video, because that
        # is how an app's files reach them
        find_group(group, system=True)
    for group in set(wanted) - current:
        run("gpasswd", "-a", name, group)
    for group in current - set(wanted):
        run("gpasswd", "-d", name, group)


def create_user(name, full_name, kind, admin, groups, password):
    check_new_name(name, "user")
    check_full_name(full_name)
    check_password(password)
    for group in groups:
        find_group(group)
    run("useradd", "--create-home", "--shell", KINDS[kind], "--comment", full_name or "", name)
    set_password(name, password)
    set_groups(name, groups)
    set_admin(name, admin)


def change_user(name, full_name=None, kind=None, admin=None, groups=None, enabled=None):
    user = find_user(name)
    if full_name is not None:
        check_full_name(full_name)
        run("usermod", "--comment", full_name, name)
    if kind is not None:
        run("usermod", "--shell", KINDS[kind], name)
    if groups is not None:
        set_groups(name, groups)
    if admin is not None:
        set_admin(name, admin)
    if enabled is False:
        if user["admin"]:
            check_not_last_admin(name)
        run("usermod", "--lock", "--expiredate", OFF_EXPIRY, name)
        if user["smb_password"]:
            run("smbpasswd", "-d", name)
    elif enabled is True:
        run("usermod", "--unlock", "--expiredate", "", name)
        if user["smb_password"]:
            run("smbpasswd", "-e", name)


def remove_user(name, keep_home, confirm, shares_lost):
    user = find_user(name)
    expected = f"remove {name}"
    if confirm != expected:
        lost = "".join(f"\n    {share}" for share in shares_lost)
        raise FnasError(f"{name} loses access to {len(shares_lost)} share"
                        + ("" if len(shares_lost) == 1 else "s") + f"{lost}\n"
                        f'confirm by passing --confirm "{expected}"')
    if user["admin"]:
        check_not_last_admin(name)
    if user["smb_password"]:
        run("smbpasswd", "-x", name, check=False)
    run("userdel", *([] if keep_home else ["--remove"]), name)


def create_group(name, members):
    check_new_name(name, "group")
    for member in members:
        find_user(member)
    run("groupadd", name)
    set_members(name, members)


def set_members(name, members):
    group = find_group(name, system=True)
    for member in members:
        find_user(member)
    if not group["system"]:
        # A group fnas made holds only the people it put there, so replace the lot
        run("gpasswd", "-M", ",".join(members), name)
        return
    # The machine's own group has members fnas didn't add, such as the app the
    # group exists for, so only add and remove the people it manages
    current = set(group["members"])
    for member in sorted(set(members) - current):
        run("gpasswd", "-a", member, name)
    people = {account.pw_name for account in pwd.getpwall() if is_person(account)}
    for member in sorted((current - set(members)) & people):
        run("gpasswd", "-d", member, name)


def rename_group(name, new):
    find_group(name)
    check_new_name(new, "group")
    run("groupmod", "--new-name", new, name)


def remove_group(name):
    find_group(name)
    run("groupdel", name)
