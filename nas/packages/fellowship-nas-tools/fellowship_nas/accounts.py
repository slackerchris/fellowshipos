"""The NAS's own account store, for a NAS whose people aren't Linux logins.

On a separated NAS (docs/fellowship-nas-account-separation.md) each person keeps
a Unix UID and group, because files are owned by number, but has no Linux
password and no shell. Their desktop password lives here as a crypt(3) hash,
and Samba keeps its own NT hash beside it; fnas always sets the two together.

A NAS is separated when this store exists. Without it, people are Linux
accounts checked through PAM, as before, until the NAS is migrated.

Python 3.13 has no crypt module, so hashing goes straight to libcrypt (the
same library /etc/shadow's hashes come from), which also checks the yescrypt
and SHA-512 hashes a migration copies across unchanged.
"""
import contextlib
import ctypes
import ctypes.util
import os
import time

from .common import STATE_DIR, FnasError, locked, read_json, write_json


STORE = os.path.join(STATE_DIR, "accounts.json")
STORE_LOCK = os.path.join(STATE_DIR, "accounts.lock")
VERSION = 1
# What a new password is hashed with: yescrypt, as Debian's chpasswd writes
NEW_HASH_PREFIX = b"$y$"

_libcrypt = None


def libcrypt():
    global _libcrypt
    if _libcrypt is None:
        name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
        lib = ctypes.CDLL(name, use_errno=True)
        lib.crypt.restype = ctypes.c_char_p
        lib.crypt.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
        lib.crypt_gensalt.restype = ctypes.c_char_p
        lib.crypt_gensalt.argtypes = (ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_int)
        _libcrypt = lib
    return _libcrypt


def hash_password(password):
    """A new crypt(3) hash for password, with a fresh salt from the kernel."""
    lib = libcrypt()
    salt = lib.crypt_gensalt(NEW_HASH_PREFIX, 0, None, 0)
    if not salt:
        raise FnasError("libcrypt can't make a salt")
    hashed = lib.crypt(password.encode(), salt)
    if not hashed or hashed.startswith(b"*"):
        raise FnasError("libcrypt can't hash the password")
    return hashed.decode()


def verify_password(password, hashed):
    """Whether password is the one hashed. A hash that is empty, locked (!) or
    marked unusable (*) matches nothing."""
    if not hashed or hashed[0] in "!*" or not password:
        return False
    result = libcrypt().crypt(password.encode(), hashed.encode())
    return bool(result) and not result.startswith(b"*") and result.decode() == hashed


def is_separated():
    """Whether this NAS keeps its people in the store rather than as Linux logins."""
    return os.path.exists(STORE)


def load_store():
    data = read_json(STORE, None)
    if data is None:
        raise FnasError("this NAS has no account store yet")
    if data.get("version") != VERSION or not isinstance(data.get("people"), dict):
        raise FnasError(f"{STORE} isn't an account store this fnas can read")
    return data


def store_people():
    return load_store()["people"] if is_separated() else {}


@contextlib.contextmanager
def changing_store():
    """The store, to change and save in one go, with nobody else changing it meanwhile."""
    with locked(STORE_LOCK):
        data = load_store()
        yield data
        write_json(STORE, data, mode=0o600)


def create_store():
    """An empty store, which is what makes this NAS separated. Only the installer
    and the migration call this."""
    if is_separated():
        raise FnasError("this NAS already has an account store")
    with locked(STORE_LOCK):
        write_json(STORE, {"version": VERSION, "people": {}}, mode=0o600)


def new_person(uid, full_name="", admin=False, sftp=False):
    """A new person's entry, before a password is set."""
    return {"uid": uid, "full_name": full_name, "admin": admin, "enabled": True, "sftp": sftp,
            "hash": "", "password_set": None, "must_change": False, "keys": []}


def set_password_hash(entry, hashed, now=None):
    entry["hash"] = hashed
    entry["password_set"] = int(time.time() if now is None else now)
    entry["must_change"] = False


def ssh_keys(name):
    """The keys sshd may use for this person, from the store: nothing for root
    or a name the NAS doesn't keep. AllowGroups and PAM's account check do the
    gating, so this only answers what the store holds."""
    return store_people().get(name, {}).get("keys", [])


def check_login(name, password):
    """Why name can't log in with password, or None when they can. One answer
    for an unknown name and a wrong password, so a guess learns nothing."""
    entry = store_people().get(name)
    if entry is None or not verify_password(password, entry.get("hash", "")):
        return "wrong"
    if not entry.get("enabled", True):
        return "off"
    if entry.get("must_change"):
        return "expired"
    return None
