"""Who may log in over SSH, and how.

Two models, like the accounts (docs/fellowship-nas-account-separation.md):
- not yet separated: only members of nas-ssh get in, which "Shares and SSH"
  accounts join, so shares-only accounts can't open a shell or a tunnel. Root
  never logs in over SSH, and passwords can be turned off once an
  administrator has a key.
- separated: people have no shell at all. sshd serves Files over SFTP to
  nas-sftp, chrooted to their shares, with passwords checked against the NAS
  store through a PAM stack and keys read from the store. Root's SSH is
  separate: off by default, keys only, and only root turns it on, with
  fellowship-root-ssh. The desktop can read that state but never change it.
"""
import grp
import os
import pwd

from .common import FnasError, read_json, run, write_file, write_json
from . import accounts, sftp, users


SSH_GROUP = users.SSH_GROUP
SETTINGS_FILE = "/etc/fellowshipos/nas-ssh.json"
DROP_IN = "/etc/ssh/sshd_config.d/fellowship.conf"
PAM_SSHD = "/etc/pam.d/sshd"
# Root's own switch, written by fellowship-root-ssh and read here and by the
# console screen. The desktop only ever sees it through ssh show.
ROOT_SSH_FILE = "/etc/fellowshipos/root-ssh.json"
ROOT_KEYS = "/root/.ssh/authorized_keys"
UNIT = "ssh.service"
# sshd's privilege separation directory. ssh.service's RuntimeDirectory keeps it
# only while the service runs, so with SFTP off it is gone and `sshd -t` refuses
# every later settings check until something makes it again.
PRIVSEP_DIR = "/run/sshd"
DEFAULTS = {"enabled": True, "passwords": True}
KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
MAX_KEYS = 32
AUTH_KEYS_COMMAND = "/usr/sbin/fnas auth keys %u"
SFTP_AUTH_WRAPPER = "/usr/lib/fellowship-nas/sftp-auth"


def settings():
    saved = read_json(SETTINGS_FILE, {})
    return {key: saved.get(key, default) if isinstance(saved.get(key), bool) else default
            for key, default in DEFAULTS.items()}


def root_ssh():
    """Whether root has turned his own SSH on. Off unless he has."""
    saved = read_json(ROOT_SSH_FILE, {})
    return saved.get("enabled") is True


def root_has_key():
    try:
        with open(ROOT_KEYS) as file:
            return any(line.strip() and not line.strip().startswith("#") for line in file)
    except OSError:
        return False


def set_root_ssh(enabled):
    """Root's switch, from fellowship-root-ssh alone. Keys only, so turning it
    on without a key would lock root out of SSH: that is refused."""
    if not accounts.is_separated():
        raise FnasError("root SSH belongs to a separated NAS; this one still keeps people as Linux logins")
    if enabled and not root_has_key():
        raise FnasError("add a public key to /root/.ssh/authorized_keys first: root SSH is keys only, "
                        "and turning it on without a key would lock root out")
    write_json(ROOT_SSH_FILE, {"enabled": bool(enabled)}, mode=0o600)
    run("logger", "--tag", "fellowship-root-ssh", "--priority", "auth.notice",
        "root turned SSH " + ("on (keys only)" if enabled else "off"), check=False)
    refresh()
    return root_ssh()


def members():
    try:
        return sorted(grp.getgrnam(SSH_GROUP).gr_mem)
    except KeyError:
        return []


def keys_path(name):
    return os.path.join(pwd.getpwnam(name).pw_dir, ".ssh", "authorized_keys")


def check_key(line):
    """One public key line, without the options that could run a command or forward ports."""
    fields = line.split()
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise FnasError("each line must be one public key, starting with its type, such as ssh-ed25519")
    result = run("ssh-keygen", "-l", "-f", "/dev/stdin", stdin=line + "\n", check=False)
    if result.returncode != 0:
        raise FnasError(f"not a valid public key: {fields[1][:16]}…")
    return " ".join(fields[:3])


def describe_keys(lines):
    """Each key line as its type, fingerprint and comment."""
    found = []
    for line in lines:
        result = run("ssh-keygen", "-l", "-f", "/dev/stdin", stdin=line + "\n", check=False)
        fields = result.stdout.split()
        if result.returncode == 0 and len(fields) >= 2:
            found.append({"type": line.split()[0], "fingerprint": fields[1],
                          "comment": " ".join(line.split()[2:]), "line": line})
    return found


def list_keys(name):
    """Each of an account's keys: its type, fingerprint and comment."""
    users.find_user(name)
    if accounts.is_separated():
        return describe_keys(accounts.ssh_keys(name))
    try:
        with open(keys_path(name)) as file:
            lines = [line.strip() for line in file if line.strip() and not line.startswith("#")]
    except OSError:
        return []
    return describe_keys(lines)


def set_keys(name, text):
    """Replace an account's keys with these, one per line. On a separated NAS
    they live in the store, which sshd reads per connection, so a change takes
    effect at once and no old file keeps working."""
    user = users.find_user(name)
    if accounts.is_separated():
        if user["kind"] != "sftp":
            raise FnasError(f"{name} is set to shares only, and fnas keeps SSH keys for people with SFTP allowed: "
                            "allow SFTP for them first")
        lines = [check_key(line.strip()) for line in text.splitlines() if line.strip() and not line.startswith("#")]
        if len(lines) > MAX_KEYS:
            raise FnasError(f"at most {MAX_KEYS} keys per account")
        with accounts.changing_store() as store:
            store["people"][name]["keys"] = lines
        return
    if user["kind"] != "ssh":
        only = "Files over SFTP" if user["kind"] == "sftp" else "shares only"
        raise FnasError(f"{name} is set to {only}, and fnas keeps SSH keys for accounts set to Shares and SSH: "
                        "make them Shares and SSH first")
    lines = [check_key(line.strip()) for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if len(lines) > MAX_KEYS:
        raise FnasError(f"at most {MAX_KEYS} keys per account")
    account = pwd.getpwnam(name)
    folder = os.path.dirname(keys_path(name))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chown(folder, account.pw_uid, account.pw_gid)
    os.chmod(folder, 0o700)
    write_file(keys_path(name), "".join(line + "\n" for line in lines), mode=0o600)
    os.chown(keys_path(name), account.pw_uid, account.pw_gid)
    if not lines and not settings()["passwords"]:
        check_someone_gets_in()


def admins_with_keys():
    allowed = set(members())
    return [user["name"] for user in users.list_users()
            if user["admin"] and user["name"] in allowed and user["enabled"] and list_keys(user["name"])]


def check_someone_gets_in():
    if not admins_with_keys():
        raise FnasError("no administrator who may use SSH has a key, so turning passwords off would lock "
                        "everyone out: add a key in Users first")


def drop_in_old(current):
    return "\n".join([
        "# Generated by fnas from the SSH settings in the desktop: changes here are overwritten",
        # sshd keeps the first value it reads, and Debian includes this directory first
        # Files over SFTP gets in with no shell, so both groups are let through here
        f"AllowGroups {SSH_GROUP} {users.SFTP_GROUP}",
        "PermitRootLogin no",
        f"PasswordAuthentication {'yes' if current['passwords'] else 'no'}",
        "KbdInteractiveAuthentication no",
        "",
        # Everyone set to Files over SFTP is chrooted to a folder of bind mounts
        # holding just the shares they may use, and gets the sftp server instead
        # of a shell. AllowGroups is no use inside a Match block, which is why
        # both groups are above and this is only about what nas-sftp may do.
        f"Match Group {users.SFTP_GROUP}",
        *[f"    {option}" for option in sftp.MATCH_OPTIONS],
        # Debian puts the Include for this directory near the top of sshd_config,
        # so every line after it would be read inside this Match: UsePAM and
        # Subsystem are not allowed there, and sshd -t would refuse the lot.
        # Match all puts the rest of the file back in the global context.
        "",
        "Match all",
    ]) + "\n"


def drop_in_separated(current):
    root = root_ssh()
    groups = (["root"] if root else []) + ([users.SFTP_GROUP] if current["enabled"] else [])
    return "\n".join([
        "# Generated by fnas from the SSH settings in the desktop: changes here are overwritten",
        # People have no shell on a separated NAS: sshd serves Files over SFTP
        # to nas-sftp, and root's own keys-only SSH only while he has turned it
        # on. Turning SFTP off never touches root's SSH.
        f"AllowGroups {' '.join(groups)}" if groups else "DenyGroups *",
        "PermitRootLogin prohibit-password" if root else "PermitRootLogin no",
        f"PasswordAuthentication {'yes' if current['passwords'] else 'no'}",
        "KbdInteractiveAuthentication no",
        # Keys come from the NAS store, read per connection, so a key replaced
        # or removed in Users takes effect at once. Root isn't in the store:
        # the command answers nothing for him, and his own file is used.
        f"AuthorizedKeysCommand {AUTH_KEYS_COMMAND}",
        "AuthorizedKeysCommandUser root",
        "",
        f"Match Group {users.SFTP_GROUP}",
        *[f"    {option}" for option in sftp.MATCH_OPTIONS],
        # The store is the only source of keys for SFTP: a migrated person's
        # old ~/.ssh/authorized_keys must not keep working after a change.
        "    AuthorizedKeysFile none",
        "",
        "Match all",
    ]) + "\n"


def drop_in(current):
    return drop_in_separated(current) if accounts.is_separated() else drop_in_old(current)


def pam_stack():
    """sshd's PAM stack on a separated NAS: passwords are checked against the
    NAS store, and the account phase stays pam_unix so a turned-off person
    (account expiry) is refused here too, keys included."""
    return "\n".join([
        "# Generated by fnas: on a separated NAS, SFTP passwords are checked",
        "# against the NAS store, not Linux. Root isn't in the store, so his",
        "# password is refused here; his SSH is keys only, when he turns it on.",
        f"auth     requisite   pam_exec.so expose_authtok {SFTP_AUTH_WRAPPER}",
        "account  requisite   pam_unix.so",
        "session  requisite   pam_unix.so",
    ]) + "\n"


def sync_members():
    """nas-ssh holds exactly the accounts set to Shares and SSH. A separated
    NAS has no such accounts: nas-sftp is managed by sftp.sync instead."""
    if accounts.is_separated():
        return
    if run("getent", "group", SSH_GROUP, check=False).returncode != 0:
        run("groupadd", "--system", SSH_GROUP)
    wanted = {user["name"] for user in users.list_users() if user["kind"] == "ssh"}
    run("gpasswd", "-M", ",".join(sorted(wanted)), SSH_GROUP)


def refresh():
    """Bring the groups, every SFTP view, sshd's settings and the service in line
    with the saved settings."""
    if not os.path.isdir(os.path.dirname(DROP_IN)):
        return
    current = settings()
    separated = accounts.is_separated()
    if not separated:
        sync_members()
    sftp.sync()
    paths = (DROP_IN, PAM_SSHD) if separated else (DROP_IN,)
    before = {}
    for path in paths:
        try:
            with open(path) as file:
                before[path] = file.read()
        except FileNotFoundError:
            before[path] = None
    write_file(DROP_IN, drop_in(current))
    if separated:
        write_file(PAM_SSHD, pam_stack())
    os.makedirs(PRIVSEP_DIR, mode=0o755, exist_ok=True)
    checked = run("sshd", "-t", check=False)
    if checked.returncode != 0:
        # A setting sshd won't take would stop it starting, so the old files go back
        for path, text in before.items():
            if text is None:
                os.remove(path)
            else:
                write_file(path, text)
        raise FnasError(f"sshd refused its settings: {checked.stderr.strip()}")
    # Root's SSH keeps the service up even with SFTP off; the desktop's switch
    # never stops root's way in
    if current["enabled"] or (separated and root_ssh()):
        run("systemctl", "enable", "--now", UNIT, check=False)
        run("systemctl", "reload-or-restart", UNIT, check=False)
    else:
        run("systemctl", "disable", "--now", UNIT, check=False)


def set_settings(enabled=None, passwords=None):
    current = settings()
    if enabled is not None:
        current["enabled"] = enabled
    if passwords is not None:
        current["passwords"] = passwords
    # On a separated NAS nobody administers over SSH, so keys-only SFTP can't
    # lock anyone out of the desktop: the old guard is the old model's
    if not current["passwords"] and current["enabled"] and not accounts.is_separated():
        check_someone_gets_in()
    write_json(SETTINGS_FILE, current)
    refresh()
    return status()


def status():
    running = run("systemctl", "is-active", "--quiet", UNIT, check=False).returncode == 0
    if accounts.is_separated():
        return {**settings(), "root_ssh": root_ssh(), "allowed": sftp.accounts(), "running": running}
    return {**settings(), "allowed": members(), "admins_with_keys": admins_with_keys(), "running": running}
