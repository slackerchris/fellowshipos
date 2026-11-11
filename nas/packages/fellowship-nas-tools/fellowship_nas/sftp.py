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
import sys

from .common import run, write_file
from . import alerts, shares, users


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


def unmount(point):
    """Take a share out of a view, and say whether it really went.

    A plain unmount is refused while anything still has its feet in the share —
    a client with a file open, say. That must not leave the share reachable, so
    the mount is detached instead: the client that has hold of it keeps the file
    it already opened, which nothing can take back, and every new open through
    that name fails at once. Whether it went is read from the kernel, not from
    the command's exit status, since a detach that failed leaves the share in
    place."""
    if run("umount", point, check=False).returncode == 0:
        return True
    run("umount", "-l", point, check=False)
    return not mounted(point)


def let_go(point, here=None):
    """Take one share out of somebody's view and leave the folder empty.

    Answers False when the mount would not go, so the caller can say so: a share
    that is still mounted is still reachable by an account that has lost it."""
    if os.path.normpath(point) in (mounts() if here is None else here):
        if not unmount(point):
            return False
    try:
        os.rmdir(point)
    except OSError:
        pass
    return True


def sync_view(user, wanted):
    """Build one account's view: a folder belonging to root, holding one bind
    mount per share. sshd refuses a chroot any other account could write to.

    Answers the folders that could not be put right, so a failure is not taken
    for a view that is current."""
    home = os.path.join(VIEW_ROOT, user)
    os.makedirs(home, mode=0o755, exist_ok=True)
    os.chown(home, 0, 0)
    os.chmod(home, 0o755)
    names = {one["share"] for one in wanted}
    here = mounts()
    problems = []
    for entry in sorted(os.listdir(home)):
        if entry not in names:
            if not let_go(os.path.join(home, entry), here):
                problems.append(os.path.join(home, entry))
    for one in wanted:
        if not os.path.isdir(one["path"]):
            # Its pool isn't mounted, so there is nothing to show
            continue
        point = os.path.join(home, one["share"])
        shown = os.path.normpath(point) in here
        if shown and shows(point, one["path"]):
            continue
        if shown and not unmount(point):
            problems.append(point)
            continue
        os.makedirs(point, mode=0o755, exist_ok=True)
        if run("mount", "--bind", one["path"], point, check=False).returncode != 0:
            # An empty folder in its place would look like a share they can't
            # read, so it goes: a share that failed to mount is not shown at all
            try:
                os.rmdir(point)
            except OSError:
                pass
            problems.append(point)
    return problems


def sync_views(known=None):
    """Rebuild every SFTP account's view from the shares it may use now, so a
    share turned off or an account changed takes effect at once. known is the
    share list, for a caller that has just worked it out. Answers the folders
    that could not be put right."""
    os.makedirs(VIEW_ROOT, mode=0o755, exist_ok=True)
    os.chown(VIEW_ROOT, 0, 0)
    os.chmod(VIEW_ROOT, 0o755)
    known = shares.list_shares() if known is None else known
    wanted = {name: view(name, known) for name in accounts()}
    problems = []
    for entry in sorted(os.listdir(VIEW_ROOT)):
        if entry in wanted:
            continue
        # Someone who isn't an SFTP account any more: their view goes
        home = os.path.join(VIEW_ROOT, entry)
        for inside in sorted(os.listdir(home)):
            point = os.path.join(home, inside)
            if not let_go(point):
                problems.append(point)
        try:
            os.rmdir(home)
        except OSError:
            pass
    for name, found in wanted.items():
        problems.extend(sync_view(name, found))
    return problems


def report(problems):
    """Say what could not be put right, in the journal and in Health.

    A share that would not unmount is still reachable by an account that has lost
    it, so this is not a note to keep to itself."""
    if not problems:
        return
    for point in problems:
        print(f"fnas: the SFTP view at {point} could not be rebuilt", file=sys.stderr)
    alerts.send_alert("high", "An SFTP share could not be withdrawn",
                      "These folders could not be unmounted or mounted, so an account may still reach "
                      "a share it has lost, or not reach one it has:\n" + "\n".join(problems))


def rebuild(known=None):
    """Rebuild the views after a share changed. Most machines never need this:
    one with no SFTP accounts has no views to build, and nothing is touched then."""
    if not accounts() and not os.path.isdir(VIEW_ROOT):
        return
    report(sync_views(known))


def sync(known=None):
    """Bring the group, the shell and every view in line with the shares. known
    is the share list when the caller has just read it."""
    ensure_shell()
    sync_accounts()
    report(sync_views(known))
