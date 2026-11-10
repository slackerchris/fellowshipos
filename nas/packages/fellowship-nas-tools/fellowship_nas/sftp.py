"""Files over SFTP, and nothing else.

An account set to Files over SFTP joins nas-sftp. sshd forces everyone in that
group into internal-sftp, so there is no shell and nothing can be tunnelled, and
chroots them to a folder of bind mounts: one per share they may use. They see
those shares and nothing else of the NAS, at the paths their own account can
already reach, so the kernel decides what may be read and written exactly as it
does for Files and WebDAV.
"""
import grp
import os

from .common import run, write_file
from . import shares, users


VIEW_ROOT = "/var/lib/fellowship-nas/sftp"
# The kernel's own list of what is mounted, which is the only thing that can tell
# a bind mount from the folder it is mounted over
MOUNTINFO = "/proc/self/mountinfo"
# What sshd does with everyone in nas-sftp: a chroot at their own view, the sftp
# server instead of a shell, and no way to forward anything through it
MATCH_OPTIONS = (f"ChrootDirectory {VIEW_ROOT}/%u",
                 "ForceCommand internal-sftp",
                 "AllowTcpForwarding no",
                 "AllowAgentForwarding no",
                 "X11Forwarding no",
                 "PermitTunnel no")


def accounts():
    try:
        return sorted(grp.getgrnam(users.SFTP_GROUP).gr_mem)
    except KeyError:
        return []


def shell():
    """The shell an SFTP account carries.

    sshd never runs it, because internal-sftp is forced instead. It is a shell
    that refuses anyway, so a machine whose sshd settings went astray still
    gives nobody a way in."""
    return users.SFTP_SHELL


def ensure_shell():
    if os.path.exists(shell()):
        return
    folder = os.path.dirname(shell())
    os.makedirs(folder, mode=0o755, exist_ok=True)
    write_file(shell(), "#!/bin/sh\n"
                        "# Written by fnas: this account is for SFTP file transfer only\n"
                        'echo "This account is for SFTP file transfer only." >&2\n'
                        "exit 1\n", mode=0o755)


def view(user, known=None):
    """The shares one account sees over SFTP: the ones it may use, and of those
    only the ones shared that way."""
    found = shares.list_shares() if known is None else known
    by_name = {share["name"]: share for share in found}
    seen = []
    for used in shares.shares_of(user, list(by_name.values())):
        share = by_name.get(used["name"])
        if share and share["sftp"] and share["path"]:
            seen.append({"share": share["name"], "path": share["path"], "level": used["level"]})
    return seen


def sync_accounts():
    """nas-sftp holds exactly the accounts set to Files over SFTP."""
    if run("getent", "group", users.SFTP_GROUP, check=False).returncode != 0:
        run("groupadd", "--system", users.SFTP_GROUP)
    wanted = {user["name"] for user in users.list_users() if user["kind"] == "sftp"}
    run("gpasswd", "-M", ",".join(sorted(wanted)), users.SFTP_GROUP)


def mounts():
    """Every path mounted right now, from the kernel's own list.

    os.path.ismount can't answer this: it compares device numbers, and a bind
    mount of a folder on the same filesystem has the device of the folder above
    it, so a share in somebody's view looks like an ordinary folder and stays
    behind when it should go. Reading the list also catches a mount that was
    made before this process started."""
    found = set()
    try:
        with open(MOUNTINFO) as file:
            for line in file:
                fields = line.split()
                if len(fields) > 4:
                    found.add(os.path.normpath(unescaped(fields[4])))
    except OSError:
        pass
    return found


def unescaped(path):
    """A mount point as the kernel writes it, with spaces and the rest put back."""
    for written, plain in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        path = path.replace(written, plain)
    return path


def mounted(path):
    return os.path.normpath(path) in mounts()


def shows(point, path):
    """Whether point already is the share at path, rather than some older mount."""
    try:
        here, there = os.stat(point), os.stat(path)
    except OSError:
        return False
    return (here.st_dev, here.st_ino) == (there.st_dev, there.st_ino)


def let_go(point, here=None):
    """Take one share out of somebody's view and leave the folder empty."""
    if os.path.normpath(point) in (mounts() if here is None else here):
        run("umount", point, check=False)
    try:
        os.rmdir(point)
    except OSError:
        pass


def sync_view(user, wanted):
    """Build one account's view: a folder belonging to root, holding one bind
    mount per share. sshd refuses a chroot any other account could write to."""
    home = os.path.join(VIEW_ROOT, user)
    os.makedirs(home, mode=0o755, exist_ok=True)
    os.chown(home, 0, 0)
    os.chmod(home, 0o755)
    names = {one["share"] for one in wanted}
    here = mounts()
    for entry in sorted(os.listdir(home)):
        if entry not in names:
            let_go(os.path.join(home, entry), here)
    for one in wanted:
        if not os.path.isdir(one["path"]):
            # Its pool isn't mounted, so there is nothing to show
            continue
        point = os.path.join(home, one["share"])
        shown = os.path.normpath(point) in here
        if shown and shows(point, one["path"]):
            continue
        if shown:
            run("umount", point, check=False)
        os.makedirs(point, mode=0o755, exist_ok=True)
        run("mount", "--bind", one["path"], point)


def sync_views():
    """Rebuild every SFTP account's view from the shares it may use now, so a
    share turned off or an account changed takes effect at once."""
    os.makedirs(VIEW_ROOT, mode=0o755, exist_ok=True)
    os.chown(VIEW_ROOT, 0, 0)
    os.chmod(VIEW_ROOT, 0o755)
    known = shares.list_shares()
    wanted = {name: view(name, known) for name in accounts()}
    for entry in sorted(os.listdir(VIEW_ROOT)):
        if entry in wanted:
            continue
        # Someone who isn't an SFTP account any more: their view goes
        home = os.path.join(VIEW_ROOT, entry)
        for inside in sorted(os.listdir(home)):
            let_go(os.path.join(home, inside))
        try:
            os.rmdir(home)
        except OSError:
            pass
    for name, found in wanted.items():
        sync_view(name, found)


def sync():
    """Bring the group, the shell and every view in line with the shares."""
    ensure_shell()
    sync_accounts()
    sync_views()
