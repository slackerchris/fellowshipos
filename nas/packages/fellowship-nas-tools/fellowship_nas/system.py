"""The NAS's name, time zone, HTTPS certificate and power."""
import contextlib
import datetime
import grp
import os
import re
import socket
import tempfile
import time

from .common import FnasError, later, run, write_file


WEB_DIR = "/etc/fellowship-nas/web"
WEB_CERT = os.path.join(WEB_DIR, "cert.pem")
WEB_KEY = os.path.join(WEB_DIR, "key.pem")
# Present while the certificate is one fnas made
WEB_SELF_SIGNED = os.path.join(WEB_DIR, "self-signed")
WEB_ACCOUNT = "fnas-web"
WEB_SERVICE = "fellowship-nas-web.service"
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z ]+)-----\r?\n.*?-----END \1-----", re.S)
POWER = {"restart": ("reboot", "restart the NAS"), "shutdown": ("poweroff", "shut down the NAS")}


def openssl(*args, stdin=None):
    return run("openssl", *args, stdin=stdin).stdout


def certificate_info():
    if not os.path.exists(WEB_CERT):
        return None
    lines = openssl("x509", "-in", WEB_CERT, "-noout", "-subject", "-issuer", "-enddate",
                    "-fingerprint", "-sha256", "-nameopt", "RFC2253").splitlines()
    fields = dict(line.split("=", 1) for line in lines if "=" in line)
    expires = datetime.datetime.strptime(fields.get("notAfter", ""), "%b %d %H:%M:%S %Y %Z")
    return {"subject": fields.get("subject", ""), "issuer": fields.get("issuer", ""),
            "expires": int(expires.replace(tzinfo=datetime.timezone.utc).timestamp()),
            "fingerprint": next((v for k, v in fields.items() if k.lower().endswith("fingerprint")), ""),
            "self_signed": os.path.exists(WEB_SELF_SIGNED)}


def system_info():
    shown = run("timedatectl", "show", check=False).stdout
    settings = dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)
    with open("/proc/uptime") as file:
        uptime = int(float(file.read().split()[0]))
    return {"hostname": socket.gethostname(), "timezone": settings.get("Timezone", "UTC"),
            "time": int(time.time()), "ntp": settings.get("NTPSynchronized") == "yes",
            "uptime": uptime, "certificate": certificate_info()}


def timezones():
    return run("timedatectl", "list-timezones").stdout.split()


def set_timezone(zone):
    if zone not in timezones():
        raise FnasError(f"unknown time zone: {zone}")
    run("timedatectl", "set-timezone", zone)


def install_certificate(cert, key, self_signed):
    os.makedirs(WEB_DIR, mode=0o755, exist_ok=True)
    try:
        group = grp.getgrnam(WEB_ACCOUNT).gr_gid
    except KeyError:
        group = 0
    write_file(WEB_KEY + ".tmp", key, 0o640)
    os.chown(WEB_KEY + ".tmp", 0, group)
    os.replace(WEB_KEY + ".tmp", WEB_KEY)
    write_file(WEB_CERT, cert)
    if self_signed:
        write_file(WEB_SELF_SIGNED, "")
    else:
        with contextlib.suppress(FileNotFoundError):
            os.remove(WEB_SELF_SIGNED)


def make_self_signed():
    name = socket.gethostname()
    names = f"subjectAltName=DNS:{name},DNS:{name}.local,DNS:localhost"
    with tempfile.TemporaryDirectory() as directory:
        key, cert = os.path.join(directory, "key.pem"), os.path.join(directory, "cert.pem")
        openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                "-days", "3650", "-subj", f"/CN={name}", "-addext", names, "-keyout", key, "-out", cert)
        with open(key) as k, open(cert) as c:
            install_certificate(c.read(), k.read(), True)


def set_certificate(pem):
    """Use a certificate from a PEM bundle: the certificate, any intermediates, and its key."""
    blocks = [(match.group(1), match.group(0) + "\n") for match in PEM_BLOCK.finditer(pem)]
    certs = [block for kind, block in blocks if kind == "CERTIFICATE"]
    keys = [block for kind, block in blocks if kind.endswith("PRIVATE KEY")]
    if not certs or len(keys) != 1:
        raise FnasError("paste the certificate, any intermediate certificates, and one unencrypted private key, "
                        "all in PEM form")
    if "ENCRYPTED" in keys[0]:
        raise FnasError("the private key has a passphrase; remove it first")
    try:
        cert_key = openssl("x509", "-noout", "-pubkey", stdin=certs[0])
        key_key = openssl("pkey", "-pubout", stdin=keys[0])
    except FnasError:
        raise FnasError("the certificate or key couldn't be read") from None
    if cert_key.strip() != key_key.strip():
        raise FnasError("the private key doesn't belong to the certificate")
    install_certificate("".join(certs), keys[0], False)


def set_hostname(name):
    if not HOSTNAME.match(name):
        raise FnasError("use lowercase letters, numbers and -, up to 63 characters, "
                        "not starting or ending with -")
    run("hostnamectl", "set-hostname", name)
    with open("/etc/hosts") as file:
        lines = file.read().splitlines()
    lines = [line for line in lines if not line.startswith("127.0.1.1")]
    lines.insert(1 if lines else 0, f"127.0.1.1\t{name}")
    write_file("/etc/hosts", "\n".join(lines) + "\n")
    # Samba and Avahi announce the name they started with
    run("systemctl", "try-restart", "smbd", "nmbd", "avahi-daemon", check=False)
    if os.path.exists(WEB_SELF_SIGNED):
        make_self_signed()
        later("systemctl", "restart", WEB_SERVICE)


def power(action, confirm):
    target, expected = POWER[action]
    if confirm != expected:
        raise FnasError(f'confirm by passing --confirm "{expected}"')
    later("systemctl", target, seconds=3)


def system_storage():
    """The system volume, and the room the installer left for it to grow into."""
    source = run("findmnt", "-n", "-o", "SOURCE", "/").stdout.strip()
    usage = os.statvfs("/")
    shown = {"device": source, "size": usage.f_blocks * usage.f_frsize,
             "used": (usage.f_blocks - usage.f_bfree) * usage.f_frsize, "lvm": False, "room": 0}
    result = run("lvs", "--noheadings", "--units", "b", "--nosuffix", "--separator", "\t",
                 "-o", "vg_name,lv_name,lv_size,vg_free,vg_extent_size", source, check=False)
    fields = result.stdout.split()
    if result.returncode == 0 and len(fields) == 5:
        group, volume, size, free, extent = fields
        shown.update(lvm=True, group=group, volume=volume, volume_size=int(size),
                     room=int(free), extent=int(extent))
    return shown


def grow_system(amount):
    """Give the system volume more of its volume group's free space, while it runs.
    It can't shrink again, so the room is used a little at a time."""
    storage = system_storage()
    if not storage["lvm"]:
        raise FnasError("the system isn't on LVM, so it already uses its whole disk")
    if amount == "all":
        extents = storage["room"] // storage["extent"]
    else:
        match = re.fullmatch(r"(\d+)([GT]?)", amount)
        if not match:
            raise FnasError(f"not an amount: {amount} (use a number of bytes, 8G, 1T or all)")
        size = int(match.group(1)) * {"": 1, "G": 1 << 30, "T": 1 << 40}[match.group(2)]
        extents = size // storage["extent"]
        if size > storage["room"]:
            raise FnasError(f"only {storage['room'] >> 20} MiB is free to grow into")
    if extents < 1:
        raise FnasError("there is no room left to grow into")
    # -r grows the ext4 file system along with the volume, while it is mounted
    run("lvextend", "-r", "-l", f"+{extents}", f"{storage['group']}/{storage['volume']}")
    return system_storage()

