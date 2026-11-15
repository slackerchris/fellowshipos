"""Migration of a NAS whose people are Linux logins to a separated NAS, and back.

docs/fellowship-nas-account-separation.md, phase 5. Chris's rules for it:
- Credentials that work today keep working: each desktop hash is copied as it
  is, and SMB's hashes aren't touched. Two passwords that already differ both
  keep working, until the person's next new NAS password makes them one.
- Password dates can flag a *possible* difference; they can't prove two
  passwords match. The dry run says exactly that.
- Anything that has to be re-entered is shown in the dry run, and set, before
  the old admin's sudo goes.
- Before sudo goes, the new NAS admin login (through the real web server) and
  root's console credentials are both proved to work; if either isn't, the
  change is undone and nothing is locked.
- Nothing is chowned: UIDs and GIDs don't change, so file owners don't either.
"""
import getpass
import grp
import hashlib
import json
import os
import pwd
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request

from . import accounts, recovery, sftp, ssh, users
from .common import DAY, STATE_DIR, FnasError, read_json, run, write_file, write_json


BACKUP_ROOT = os.path.join(STATE_DIR, "accounts-before-separate")
PASSDB = "/var/lib/samba/private/passdb.tdb"
SAMBA_CONF = "/etc/samba/smb.conf"
# What the backup keeps: the account files, sudo's rules, sshd's and Samba's
# settings, and the GRUB pieces the recovery key changes
SAVED_FILES = ["/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow", "/etc/sudoers", SAMBA_CONF]
DIRECTORIES = ["/etc/sudoers.d", "/etc/ssh/sshd_config.d"]
# Groups that give power over the system rather than access to files
POWER_GROUPS = ("sudo", "adm", "disk", "lxd", "docker", "wheel", "shadow", "systemd-journal")
LOGIN_URL = "https://127.0.0.1/api/login"


# What each person has now -----------------------------------------------------

def shadow_entries():
    found = {}
    with open(users.SHADOW) as file:
        for line in file:
            fields = line.rstrip("\n").split(":")
            if len(fields) > 7:
                found[fields[0]] = {"hash": fields[1], "changed_day": int(fields[2]) if fields[2].isdigit() else None,
                                    "off": fields[7] == users.OFF_EXPIRY}
    return found


def usable_hash(raw):
    """The crypt hash in a shadow field, or "" when there isn't one to copy. A
    turned-off account's hash starts with "!", which is taken off: the account
    stays off through the store's own flag."""
    hashed = (raw or "").lstrip("!")
    return hashed if hashed.startswith("$") and hashed.count("$") >= 3 else ""


def samba_set_day(name):
    """The day Samba's password was last set, from pdbedit, or None."""
    result = run("pdbedit", "-v", name, check=False)
    for line in result.stdout.splitlines():
        if line.lower().startswith("password last set:"):
            stamp = line.split(":", 1)[1].strip()
            try:
                return int(time.mktime(time.strptime(stamp, "%a, %d %b %Y %H:%M:%S %Z")) // DAY)
            except ValueError:
                return None
    return None


def authorized_keys(account):
    try:
        with open(os.path.join(account.pw_dir, ".ssh", "authorized_keys")) as file:
            return [line.strip() for line in file if line.strip() and not line.startswith("#")]
    except OSError:
        return []


def separation_plan():
    """What migration would do for each person, changing nothing."""
    if accounts.is_separated():
        raise FnasError("this NAS is already separated")
    shadow = shadow_entries()
    smb = users.smb_accounts()
    people = []
    for user in users.list_users():
        name = user["name"]
        account = pwd.getpwnam(name)
        entry = shadow.get(name, {})
        hashed = usable_hash(entry.get("hash"))
        groups = sorted(g.gr_name for g in grp.getgrall() if name in g.gr_mem)
        person = {
            "name": name, "uid": account.pw_uid, "gid": account.pw_gid, "full_name": user["full_name"],
            "admin": user["admin"], "enabled": user["enabled"], "groups": groups,
            "kind": user["kind"], "sftp": user["kind"] in ("sftp", "ssh"),
            "desktop": "carries over" if hashed else "needs setting",
            "hash_method": hashed.split("$")[1] if hashed else None,
            "smb": "carries over" if name in smb else "needs setting",
            "leaves": [g for g in groups if g in POWER_GROUPS] + (["nas-admins"] if user["admin"] else []),
            "notes": [],
        }
        if user["kind"] == "ssh":
            person["notes"].append("had a shell; becomes Files over SFTP (shell access is root's)")
        linux_day, smb_day = entry.get("changed_day"), samba_set_day(name) if name in smb else None
        if hashed and linux_day is not None and smb_day is not None and linux_day > smb_day:
            person["possibly_different"] = {"linux": linux_day, "smb": smb_day}
            person["notes"].append(
                f"possibly different passwords: the Linux one was changed after the Samba one "
                f"({day_text(linux_day)} vs {day_text(smb_day)}). Both keep working as they do now. "
                f"They become one again the next time {name}'s password is set.")
        people.append(person)
    root = shadow.get("root", {})
    return {"people": people,
            "root_has_password": bool(usable_hash(root.get("hash"))) and not root.get("hash", "").startswith("!"),
            "runner": os.environ.get("SUDO_USER") or None}


def day_text(day):
    return time.strftime("%Y-%m-%d", time.gmtime(day * DAY))


def print_plan(found, out=sys.stdout, going_ahead=False):
    say = lambda text="": print(text, file=out)
    say("Accounts that move to the NAS store (UIDs and GIDs unchanged):")
    say()
    for p in found["people"]:
        say(f"  {p['name']:<8} uid {p['uid']}  gid {p['gid']}" + ("  admin" if p["admin"] else "")
            + ("  (turned off)" if not p["enabled"] else "") + (f"  groups: {','.join(p['groups'])}" if p["groups"] else ""))
        desktop = f"carries over ({p['hash_method'] and '$' + p['hash_method'] + '$'})" \
            if p["desktop"] == "carries over" else "NEEDS SETTING: no usable Linux password"
        say(f"    desktop password  {desktop}")
        say("    SMB password      " + ("carries over" if p["smb"] == "carries over"
                                       else "NEEDS SETTING: no Samba password exists"))
        if p["leaves"]:
            say(f"    leaves            {', '.join(p['leaves'])}"
                + ("  (sudo ends for this account)" if "sudo" in p["leaves"] else ""))
        for note in p["notes"]:
            say(f"    note              {note}")
        say()
    needs = [p["name"] for p in found["people"] if "needs setting" in (p["desktop"], p["smb"])]
    say("Must be done before any sudo access ends:")
    if not found["root_has_password"]:
        say("  - set a root password (asked next)")
    else:
        say("  - root already has a password; it is checked before anything changes")
    for name in needs:
        if name == found["runner"]:
            say(f"  - set a password for {name} (you; asked next)")
        else:
            say(f"  - set a password for {name} (asked next, or name them with --later)")
    say("  - a recovery key is made and shown once")
    say()
    say("Password dates only flag *possible* differences between a desktop and an SMB\n"
        "password. They can't prove two passwords match, and a change made on the same\n"
        "day as the other isn't seen. Nothing here changes a password that works today:\n"
        "whatever works for the desktop and for shares now keeps working after this.")
    say()
    say("Going ahead." if going_ahead else "Nothing has changed. Run again with --yes to separate.")


# Asking -----------------------------------------------------------------------

def ask_secret(prompt):
    """A password, from the terminal, or a line of stdin when there is none
    (for a scripted run)."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    print(prompt, file=sys.stderr)
    return sys.stdin.readline().rstrip("\n")


def ask_new(label, ask=ask_secret):
    for _ in range(3):
        first = ask(f"New password for {label}: ")
        if len(first) < 8:
            print("At least 8 characters.", file=sys.stderr)
            continue
        if first != ask("The same again: "):
            print("Those don't match.", file=sys.stderr)
            continue
        return first
    raise FnasError(f"no password for {label}: nothing was changed")


# The backup -------------------------------------------------------------------

def checksum(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def back_up(stamp):
    """Copy what migration changes, with a manifest of checksums, and answer the folder."""
    folder = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(folder, mode=0o700)
    os.chmod(BACKUP_ROOT, 0o700)
    manifest = {}

    def keep(path):
        target = os.path.join(folder, "files", path.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
        manifest[path] = checksum(target)
    for path in SAVED_FILES:
        if os.path.exists(path):
            keep(path)
    for directory in DIRECTORIES:
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                if os.path.isfile(path):
                    keep(path)
    if os.path.exists(PASSDB):
        # tdbbackup takes a consistent copy while smbd is running
        target = os.path.join(folder, "files", PASSDB.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        run("tdbbackup", "-s", ".fnas", PASSDB)
        shutil.move(PASSDB + ".fnas", target)
        manifest[PASSDB] = checksum(target)
    write_json(os.path.join(folder, "manifest.json"), {"time": int(time.time()), "files": manifest}, mode=0o600)
    return folder


def backups():
    return sorted(os.listdir(BACKUP_ROOT)) if os.path.isdir(BACKUP_ROOT) else []


# Proving the ways in ----------------------------------------------------------

def desktop_login(name, password, url=None):
    """Log in the way the desktop does, through the real web server and helper,
    and answer the session it gives, or raise why not."""
    url = url or LOGIN_URL
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    origin = url.split("/api/")[0]
    request = urllib.request.Request(url, data=json.dumps({"user": name, "password": password}).encode(),
                                     headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, context=context, timeout=15) as answer:
                return json.load(answer)
        except urllib.error.HTTPError as error:
            raise FnasError(f"the desktop refused {name}: {error.read().decode(errors='replace').strip()}") from None
        except OSError:
            # The helper is restarting
            time.sleep(1)
    raise FnasError("the desktop's web server didn't answer")


def root_usable(password):
    """Root can log in at the console: the same hash check pam_unix makes, an
    account that isn't locked or expired, a shell, and a login prompt on tty1."""
    entry = shadow_entries().get("root", {})
    raw = entry.get("hash", "")
    if raw.startswith("!") or not accounts.verify_password(password, usable_hash(raw)):
        return "root's password doesn't match"
    if entry.get("off"):
        return "root's account has expired"
    shell = pwd.getpwnam("root").pw_shell
    if not os.access(shell, os.X_OK) or shell.endswith(("nologin", "false")):
        return f"root's shell ({shell}) can't log in"
    getty = run("systemctl", "is-enabled", "getty@tty1.service", check=False)
    if getty.returncode != 0 and "static" not in getty.stdout and "alias" not in getty.stdout:
        return "no login prompt is enabled on the console"
    return None


# Separating -------------------------------------------------------------------

def write_store(found):
    accounts.create_store()
    with accounts.changing_store() as store:
        for p in found["people"]:
            raw = shadow_entries().get(p["name"], {})
            entry = accounts.new_person(p["uid"], p["full_name"], admin=p["admin"], sftp=p["sftp"])
            entry["enabled"] = p["enabled"]
            hashed = usable_hash(raw.get("hash"))
            if hashed:
                entry["hash"] = hashed
                entry["password_set"] = (raw.get("changed_day") or 0) * DAY or None
            entry["keys"] = authorized_keys(pwd.getpwnam(p["name"]))
            if p.get("possibly_different"):
                entry["possibly_different"] = True
            store["people"][p["name"]] = entry


def set_smb_option(text, option, value):
    """smb.conf with option set in [global], replacing Debian's own line."""
    lines, done, in_global = [], False, False
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("["):
            if in_global and not done:
                lines.append(f"   {option} = {value}")
                done = True
            in_global = stripped == "[global]"
        elif in_global and stripped.split("=")[0].strip() == option:
            if not done:
                lines.append(f"   {option} = {value}")
                done = True
            continue
        lines.append(line)
    if not done:
        lines.append(f"   {option} = {value}")
    return "\n".join(lines) + "\n"


def lock_down(found):
    """The Unix side of each person: no password, no shell, no power."""
    for p in found["people"]:
        name = p["name"]
        run("usermod", "--lock", name)
        run("usermod", "--shell", users.KINDS["sftp" if p["sftp"] else "shares"], name)
        for group in p["leaves"]:
            run("gpasswd", "-d", name, group, check=False)
    if os.path.exists(SAMBA_CONF):
        with open(SAMBA_CONF) as file:
            text = file.read()
        for option in ("unix password sync", "pam password change"):
            text = set_smb_option(text, option, "no")
        write_file(SAMBA_CONF, text)
    sftp.sync()
    ssh.refresh()
    for unit in ("smbd.service", "ssh.service", "fellowship-nas-helper.service"):
        run("systemctl", "try-restart", unit, check=False)


def owners(found, limit=20):
    """A sample of each person's files, and who owns them, to check afterwards."""
    sample = {}
    for p in found["people"]:
        home = pwd.getpwnam(p["name"]).pw_dir
        for top in (home,):
            if not os.path.isdir(top):
                continue
            for folder, _, names in os.walk(top):
                for name in names[:limit]:
                    path = os.path.join(folder, name)
                    try:
                        status = os.lstat(path)
                    except OSError:
                        continue
                    sample[path] = (status.st_uid, status.st_gid)
                    if len(sample) >= limit * len(found["people"]):
                        return sample
    return sample


def separate(found, ask=ask_secret, later=(), url=None, say=print):
    """Separate the NAS, proving both ways in before any sudo goes."""
    runner = found["runner"]
    needs = [p for p in found["people"] if "needs setting" in (p["desktop"], p["smb"])]
    for name in later:
        if name == runner:
            raise FnasError("you can't leave your own password for later: your sudo ends at the end of this")
        if name not in [p["name"] for p in needs]:
            raise FnasError(f"{name} needs nothing set")
    admins = [p for p in found["people"] if p["admin"] and p["enabled"]]
    if not admins:
        raise FnasError("nobody is a NAS admin (in nas-admins) and turned on: make someone one first")
    prover = next((p for p in admins if p["name"] == runner), admins[0])

    # Asked for before anything changes
    root_password = None if found["root_has_password"] else ask_new("root", ask)
    if root_password is None:
        root_password = ask("Root's password (to check it works at the console): ")
    passwords = {}
    for p in needs:
        if p["name"] not in later:
            passwords[p["name"]] = ask_new(p["name"], ask)
    prover_password = passwords.get(prover["name"]) or ask(f"{prover['name']}'s password, to prove the new login: ")
    before_owners = owners(found)
    before_ids = {p["name"]: (p["uid"], p["gid"]) for p in found["people"]}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = back_up(stamp)
    say(f"Saved what this changes to {folder}")
    try:
        if not found["root_has_password"]:
            run("chpasswd", stdin=f"root:{root_password}\n")
            run("usermod", "--unlock", "--expiredate", "", "root", check=False)
        write_store(found)
        for name, password in passwords.items():
            users.set_password(name, password)
        missing = [p["name"] for p in found["people"] if p["name"] not in accounts.store_people()]
        if missing:
            raise FnasError("the store is missing " + ", ".join(missing))
        run("systemctl", "try-restart", "fellowship-nas-helper.service", check=False)
        # The two ways in, before anything is taken away
        session = desktop_login(prover["name"], prover_password, url)
        if not session.get("admin"):
            raise FnasError(f"{prover['name']} logged in, but not as a NAS admin")
        say(f"Checked: {prover['name']} logs in to the desktop as a NAS admin.")
        why = root_usable(root_password)
        if why:
            raise FnasError(why)
        say("Checked: root can log in at the console.")
    except (FnasError, OSError) as error:
        say(f"Stopped before anything was locked: {error}")
        undo(stamp, say=say, keep_root=True)
        raise FnasError("the NAS is as it was; nothing was separated") from None

    if not recovery.exists():
        key = recovery.generate()
        recovery.set_key(key)
        say("\nYour recovery key. It is shown only now: write it down or print it, and keep it\n"
            "somewhere other than this NAS. With it, FellowshipOS recovery in the boot menu\n"
            f"gets you back in if root's password and the NAS admin's are both lost.\n\n    {key}\n")
    lock_down(found)

    # Afterwards: the same IDs, the same owners, and the login still works
    problems = []
    for name, ids in before_ids.items():
        account = pwd.getpwnam(name)
        if (account.pw_uid, account.pw_gid) != ids:
            problems.append(f"{name}'s IDs changed")
    for path, ids in before_owners.items():
        try:
            status = os.lstat(path)
        except OSError:
            continue
        if (status.st_uid, status.st_gid) != ids:
            problems.append(f"{path} changed owner")
    try:
        desktop_login(prover["name"], prover_password, url)
    except FnasError as error:
        problems.append(str(error))
    if problems:
        say("Something isn't right after separating, so it is being undone:\n  " + "\n  ".join(problems))
        undo(stamp, say=say)
        raise FnasError("the NAS is back as it was before")
    count = len(found["people"])
    say(f"Separated. {count} {'person now logs' if count == 1 else 'people now log'} in with their NAS password; "
        f"checked {len(before_owners)} files keep their owners.")
    if later:
        say("Still to set, in the desktop's Users: " + ", ".join(later))
    return stamp


# Undoing ----------------------------------------------------------------------

def undo(stamp=None, say=print, keep_root=True):
    """Put back what a migration saved. Root's current password stays, so
    there is still a way into the OS; the recovery key stays too."""
    found = backups()
    if not found:
        raise FnasError("there is no saved state to go back to")
    stamp = stamp or found[-1]
    folder = os.path.join(BACKUP_ROOT, stamp)
    manifest = read_json(os.path.join(folder, "manifest.json"), None)
    if not manifest:
        raise FnasError(f"{folder} has no manifest")
    for path, digest in manifest["files"].items():
        if checksum(os.path.join(folder, "files", path.lstrip("/"))) != digest:
            raise FnasError(f"the saved copy of {path} doesn't match its checksum; nothing was restored")
    root_now = shadow_entries().get("root", {}).get("hash") if keep_root else None
    run("systemctl", "stop", "smbd.service", check=False)
    for path in manifest["files"]:
        saved = os.path.join(folder, "files", path.lstrip("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy2(saved, path)
    # Files the migration added to a saved folder go, so it is as it was
    for directory in DIRECTORIES:
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                path = os.path.join(directory, name)
                if os.path.isfile(path) and path not in manifest["files"]:
                    os.remove(path)
    if root_now:
        run("usermod", "--password", root_now, "root")
    if accounts.is_separated():
        os.replace(accounts.STORE, f"{accounts.STORE}.undone-{stamp}")
    for unit in ("smbd.service", "ssh.service", "fellowship-nas-helper.service"):
        run("systemctl", "try-restart" if unit != "smbd.service" else "start", unit, check=False)
    say(f"Put back the accounts as they were at {stamp}: Linux passwords, groups and sudo, "
        "SSH and Samba settings, and Samba's passwords. Root's password and the recovery key stay as they are.")
    return stamp
