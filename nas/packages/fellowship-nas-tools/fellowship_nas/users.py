"""People's accounts and groups. Every account has one password for the desktop
and every way to share.

Two models, chosen by whether the NAS has an account store (accounts.py):
- separated: people's passwords live in the store and Samba, and their Linux
  side is a UID with a locked password and no shell;
- not yet separated: people are Linux logins, their password set for Linux and
  Samba together, as before.
"""
import grp
import os
import pwd
import re
import time

from . import accounts
from .common import ACCOUNT_NAME, STATE_DIR, FnasError, locked, read_json, run, write_json


ADMIN_GROUP = "nas-admins"
SSH_GROUP = "nas-ssh"
SFTP_GROUP = "nas-sftp"
FIRST_ID, LAST_ID = 1000, 60000
# The shell an account with no terminal gets: for SFTP it is a script that
# refuses, which sshd never runs because internal-sftp is forced instead
SFTP_SHELL = "/usr/local/lib/fellowship-nas/sftp-only"
# What an account may do besides using shares
KINDS = {"shares": "/usr/sbin/nologin", "sftp": SFTP_SHELL, "ssh": "/bin/bash"}
# On a separated NAS shell access is root's alone
SEPARATED_KINDS = ("shares", "sftp")
FULL_NAME = re.compile(r"^[^:,\n\x00]{0,64}$")
# Accounts in the people range that belong to the NAS itself; nas-share is the
# share account's old name, in case a rename hasn't happened yet
SERVICE_ACCOUNTS = {"file-share", "nas-share"}
# The share account's group: being in it is what lets a person use shares for
# everyone in Files. Everybody joins once; taking someone out stays that way.
EVERYONE_GROUP = "file-share"
# Who has been put in it already, so a refresh adds newcomers and leaves alone
# the people an administrator took out
EVERYONE_FILE = os.path.join(STATE_DIR, "everyone-shares.json")
EVERYONE_LOCK = os.path.join(STATE_DIR, "everyone-shares.lock")


def is_person(account):
    return (FIRST_ID <= account.pw_uid < LAST_ID and ACCOUNT_NAME.match(account.pw_name)
            and account.pw_name not in SERVICE_ACCOUNTS)


def kind_of(account):
    """What an account may do besides using shares, read from its shell: the sftp
    one refuses, and it is the only thing that tells Files over SFTP apart from
    shares alone."""
    for kind, shell in KINDS.items():
        if account.pw_shell == shell:
            return kind
    # An account whose shell refuses is one that may only use shares
    if account.pw_shell.endswith(("nologin", "false")):
        return "shares"
    return "ssh"


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
# When every session an account had was ended. The desktop's helper reads it, so
# a password that has been changed can't leave somebody else signed in.
SIGNED_OUT_FILE = os.path.join(STATE_DIR, "signed-out.json")
SIGNED_OUT_LOCK = os.path.join(STATE_DIR, "signed-out.lock")


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
    """Every group on the machine that fnas didn't make.

    That covers the packages' groups (video, render, wheel, docker and the
    rest), the groups belonging to accounts fnas doesn't manage (Plex, the
    *arr services, a backup tool), `nas-admins`, and each account's own group.
    A share can be given to any of them, so software that already runs here
    reaches the files it needs without a second account. Nothing is filtered
    out: if the group exists, it can be chosen, and fnas never renames or
    removes it."""
    return {g.gr_name for g in grp.getgrall()} - managed_group_names()


def private_group_names():
    """Each account's own group, named after the account, which can't be shared
    on purpose because membership follows the account's primary group."""
    return {private_group(account) for account in pwd.getpwall()} - {None}


def list_groups(include_system=False):
    """Groups people manage, and with include_system everything else on the
    machine as well, each flagged so a panel can tell them apart."""
    managed = managed_group_names()
    wanted = managed | (system_group_names() if include_system else set())
    own = private_group_names()
    return sorted(({"name": g.gr_name, "gid": g.gr_gid, "members": sorted(g.gr_mem),
                    "system": g.gr_name not in managed, "private": g.gr_name in own}
                   for g in grp.getgrall() if g.gr_name in wanted),
                  key=lambda group: group["name"])


def list_users():
    if accounts.is_separated():
        return list_separated()
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
                      "kind": kind_of(account),
                      "admin": name in admin_names,
                      "groups": sorted(g.gr_name for g in grp.getgrall() if name in g.gr_mem and g.gr_name in mine),
                      "password": passwords.get(name, {}).get("password", False),
                      "smb_password": name in smb, "enabled": not passwords.get(name, {}).get("off", False)})
    return found


def list_separated():
    """People from the store, with their Unix side read from the system, which is
    where their UID and groups really are."""
    smb = smb_accounts()
    mine = managed_group_names() | system_group_names()
    found = []
    for name, entry in sorted(accounts.store_people().items()):
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            continue
        found.append({"name": name, "uid": account.pw_uid, "full_name": entry.get("full_name", ""),
                      "kind": "sftp" if entry.get("sftp") else "shares",
                      "admin": bool(entry.get("admin")),
                      "groups": sorted(g.gr_name for g in grp.getgrall() if name in g.gr_mem and g.gr_name in mine),
                      "password": bool(entry.get("hash")), "smb_password": name in smb,
                      "enabled": entry.get("enabled", True), "must_change": bool(entry.get("must_change"))})
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


def end_sessions(name):
    """Say that every session this account had is over.

    A password change is what somebody does when they think another person has
    had it, so it signs them out everywhere as well: the helper reads this and
    refuses any session that started before it. A damaged file is started over
    rather than left to stop a password from being set."""
    with locked(SIGNED_OUT_LOCK):
        try:
            stamps = read_json(SIGNED_OUT_FILE, {})
        except FnasError:
            stamps = {}
        stamps[name] = int(time.time())
        write_json(SIGNED_OUT_FILE, stamps)


def set_password(name, password):
    """One password for the desktop and Samba: in the store on a separated NAS,
    and for Linux otherwise. Setting it is also what brings a desktop and an SMB
    password that drifted apart back together."""
    find_user(name)
    check_password(password)
    if accounts.is_separated():
        hashed = accounts.hash_password(password)
        with accounts.changing_store() as store:
            accounts.set_password_hash(store["people"][name], hashed)
    else:
        run("chpasswd", stdin=f"{name}:{password}\n")
    if os.path.isdir("/etc/samba"):
        run("smbpasswd", "-s", "-a", name, stdin=f"{password}\n{password}\n")
    end_sessions(name)


def admins():
    if accounts.is_separated():
        return {name for name, entry in accounts.store_people().items() if entry.get("admin")}
    try:
        return set(grp.getgrnam(ADMIN_GROUP).gr_mem)
    except KeyError:
        return set()


def check_not_last_admin(name):
    if admins() == {name}:
        raise FnasError(f"{name} is the only administrator, so the NAS would have nobody to manage it")


def set_admin(name, admin):
    if accounts.is_separated():
        if not admin and name in admins():
            check_not_last_admin(name)
        with accounts.changing_store() as store:
            store["people"][name]["admin"] = bool(admin)
        return
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
    if EVERYONE_GROUP in set(wanted) ^ current:
        # Chosen by hand, so a refresh mustn't undo it
        join_everyone([name], join=False)


def _everyone_group_exists():
    try:
        grp.getgrnam(EVERYONE_GROUP)
        return True
    except KeyError:
        return False


def offered_everyone():
    return set(read_json(EVERYONE_FILE, {}).get("offered", []))


def join_everyone(names, join=True):
    """Put people in the group for shares for everyone, or note that they were
    left out on purpose, so a refresh never puts them back."""
    with locked(EVERYONE_LOCK):
        offered = offered_everyone()
        for name in names:
            if join:
                run("gpasswd", "-a", name, EVERYONE_GROUP)
            offered.add(name)
        write_json(EVERYONE_FILE, {"offered": sorted(offered)})


def offer_everyone():
    """Every person not yet offered shares for everyone joins: everybody on a NAS
    that predates the group, and anyone made without fnas. Run at every refresh."""
    if not _everyone_group_exists():
        return
    waiting = [a.pw_name for a in pwd.getpwall() if is_person(a) and a.pw_name not in offered_everyone()]
    if waiting:
        join_everyone(waiting)


def check_kind(kind):
    if accounts.is_separated() and kind not in SEPARATED_KINDS:
        raise FnasError("on this NAS people can't have a shell: shell access is root's. "
                        "Use shares, or allow SFTP for file access")


def create_user(name, full_name, kind, admin, groups, password, everyone=True):
    check_new_name(name, "user")
    check_full_name(full_name)
    check_password(password)
    check_kind(kind)
    for group in groups:
        find_group(group)
    if accounts.is_separated():
        # A UID and group for owning files, and nothing to log in with: useradd
        # leaves the password locked, and no home is made in /home
        run("useradd", "--no-create-home", "--home-dir", "/nonexistent", "--shell", KINDS[kind],
            "--comment", full_name or "", name)
        with accounts.changing_store() as store:
            store["people"][name] = accounts.new_person(pwd.getpwnam(name).pw_uid, full_name or "",
                                                    sftp=kind == "sftp")
    else:
        run("useradd", "--create-home", "--shell", KINDS[kind], "--comment", full_name or "", name)
    set_password(name, password)
    set_groups(name, [g for g in groups if g != EVERYONE_GROUP])
    set_admin(name, admin)
    join_everyone([name], join=everyone and _everyone_group_exists())


def change_user(name, full_name=None, kind=None, admin=None, groups=None, enabled=None):
    user = find_user(name)
    if accounts.is_separated():
        change_separated(user, full_name, kind, admin, groups, enabled)
        return
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
        end_sessions(name)
    elif enabled is True:
        run("usermod", "--unlock", "--expiredate", "", name)
        if user["smb_password"]:
            run("smbpasswd", "-e", name)


def change_separated(user, full_name, kind, admin, groups, enabled):
    """change_user on a separated NAS. Turning someone off never touches their
    Linux password, which stays locked either way: `usermod --unlock` on it would
    try to make a passwordless account. The account's expiry is what makes SMB
    and SFTP refuse them, through PAM's account check."""
    name = user["name"]
    if full_name is not None:
        check_full_name(full_name)
        run("usermod", "--comment", full_name, name)
    if kind is not None:
        check_kind(kind)
        run("usermod", "--shell", KINDS[kind], name)
    if groups is not None:
        set_groups(name, groups)
    if admin is not None:
        set_admin(name, admin)
    if enabled is False and user["admin"]:
        check_not_last_admin(name)
    with accounts.changing_store() as store:
        entry = store["people"][name]
        if full_name is not None:
            entry["full_name"] = full_name
        if kind is not None:
            entry["sftp"] = kind == "sftp"
        if enabled is not None:
            entry["enabled"] = enabled
    if enabled is False:
        run("usermod", "--expiredate", OFF_EXPIRY, name)
        if user["smb_password"]:
            run("smbpasswd", "-d", name)
        end_sessions(name)
    elif enabled is True:
        run("usermod", "--expiredate", "", name)
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
    if accounts.is_separated():
        with accounts.changing_store() as store:
            store["people"].pop(name, None)
    end_sessions(name)


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
