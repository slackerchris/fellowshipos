"""Storing alerts and sending them by email and ntfy."""
import contextlib
import os
import re
import socket
import time
import email.message
import email.utils
import urllib.parse
import urllib.request

from .common import DAY, FnasError, STATE_DIR, locked, read_json, run, write_file, write_json


ALERTS_FILE = "/etc/fellowshipos/nas-alerts.json"
SECRETS_FILE = "/etc/fellowshipos/nas-alerts.secrets"
MSMTP_FILE = "/etc/fellowshipos/nas-msmtprc"
ALERT_LOG = os.path.join(STATE_DIR, "alerts.json")
ALERT_LOCK = os.path.join(STATE_DIR, "alerts.lock")
MAX_ALERTS = 500
RETRY_FOR = DAY
# critical: something is broken now. high, medium and low: a warning, by how
# soon it needs attention. info: nothing wrong, such as a resolved problem.
SEVERITIES = ("info", "low", "medium", "high", "critical")
RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}
# What older versions sent, and what older hooks may still send
OLD_SEVERITIES = {"warning": "medium"}
NTFY_PRIORITY = {"info": "low", "low": "low", "medium": "default", "high": "high", "critical": "urgent"}
MAIL_ADDRESS = re.compile(r"^[^@\s\"]+@[^@\s\"]+$")
MAIL_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
MAIL_USER = re.compile(r"^[^\s\"]+$")


def alert_channels():
    settings = read_json(ALERTS_FILE, {})
    secrets = read_json(SECRETS_FILE, {})
    shown = {}
    if "email" in settings:
        email_settings = settings["email"]
        needs_password = bool(email_settings["user"]) and "smtp_password" not in secrets
        shown["email"] = {**email_settings, "needs_password": needs_password}
    if "ntfy" in settings:
        shown["ntfy"] = {**settings["ntfy"], "token": "ntfy_token" in secrets}
    return shown


def send_email(settings, severity, subject, body):
    mail = email.message.EmailMessage()
    mail["From"] = settings["from"]
    mail["To"] = settings["to"]
    mail["Subject"] = subject
    mail["Date"] = email.utils.formatdate(localtime=True)
    mail.set_content(body)
    result = run("msmtp", "-C", MSMTP_FILE, settings["to"], check=False, stdin=mail.as_string())
    if result.returncode != 0:
        raise FnasError(f"email: {result.stderr.strip() or 'msmtp failed'}")


def send_ntfy(settings, severity, subject, body):
    headers = {"Title": subject.encode("ascii", "replace").decode(),
               "Priority": NTFY_PRIORITY[severity]}
    token = read_json(SECRETS_FILE, {}).get("ntfy_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(settings["url"], data=body.encode(), headers=headers)
    try:
        urllib.request.urlopen(request, timeout=20).close()
    except (OSError, ValueError) as error:
        raise FnasError(f"ntfy: {error}") from None


def deliver(severity, title, body, channels=("email", "ntfy")):
    """Send to each channel in use, returning what couldn't be reached."""
    severity = OLD_SEVERITIES.get(severity, severity)
    subject = f"[{socket.gethostname()}] " + (f"{severity.capitalize()}: " if severity != "info" else "") + title
    settings = read_json(ALERTS_FILE, {})
    failures = []
    for channel, sender in (("email", send_email), ("ntfy", send_ntfy)):
        if channel in settings and channel in channels:
            try:
                sender(settings[channel], severity, subject, body)
            except FnasError as error:
                failures.append(str(error))
    return failures


def send_alert(severity, title, body):
    """Store an alert and send it, returning what couldn't be reached."""
    title = " ".join(title.split())
    severity = OLD_SEVERITIES.get(severity, severity)
    failures = deliver(severity, title, body)
    with locked(ALERT_LOCK):
        alerts = read_json(ALERT_LOG, [])
        alerts.append({"time": int(time.time()), "severity": severity, "title": title,
                       "message": body, "undelivered": failures})
        write_json(ALERT_LOG, alerts[-MAX_ALERTS:])
    return failures


def retry_alerts(now):
    """Try again on the channels that failed. A disk can fail before the network is up."""
    retried = {}
    for alert in read_json(ALERT_LOG, []):
        if alert["undelivered"] and now - alert["time"] < RETRY_FOR:
            channels = [failure.split(":")[0] for failure in alert["undelivered"]]
            retried[alert["time"], alert["title"]] = deliver(
                alert["severity"], alert["title"], alert["message"], channels)
    if retried:
        with locked(ALERT_LOCK):
            alerts = read_json(ALERT_LOG, [])
            for alert in alerts:
                alert["undelivered"] = retried.get((alert["time"], alert["title"]), alert["undelivered"])
            write_json(ALERT_LOG, alerts)


def check_delivered(failures):
    if failures:
        raise FnasError("saved, but not delivered: " + "; ".join(failures))


def check_email(settings):
    if not MAIL_ADDRESS.match(settings["to"]) or not MAIL_ADDRESS.match(settings["from"]):
        raise FnasError("not an email address")
    if not MAIL_HOST.match(settings["host"]):
        raise FnasError(f"not a mail server name: {settings['host']}")
    if not 1 <= settings["port"] <= 65535:
        raise FnasError(f"not a port: {settings['port']}")
    if settings["tls"] not in ("starttls", "on", "off"):
        raise FnasError(f"not a TLS mode: {settings['tls']}")
    if settings["user"] and not MAIL_USER.match(settings["user"]):
        raise FnasError("mail user names can't contain spaces or quotes")


def check_ntfy(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or re.search(r"\s", url):
        raise FnasError(f"not a web address: {url}")


def msmtp_config(settings, password):
    lines = ["# Generated by fnas: changes here are overwritten",
             "account default", f"host {settings['host']}", f"port {settings['port']}",
             f"from {settings['from']}", "timeout 20",
             f"tls {'off' if settings['tls'] == 'off' else 'on'}",
             f"tls_starttls {'on' if settings['tls'] == 'starttls' else 'off'}",
             f"auth {'on' if settings['user'] else 'off'}"]
    if settings["user"]:
        lines.append(f"user {settings['user']}")
    if password:
        escaped = password.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'password "{escaped}"')
    return "\n".join(lines) + "\n"


def set_secret(key, value):
    secrets = read_json(SECRETS_FILE, {})
    if value:
        secrets[key] = value
    else:
        secrets.pop(key, None)
    write_json(SECRETS_FILE, secrets, mode=0o600)


def set_alert_channel(channel, value):
    settings = read_json(ALERTS_FILE, {})
    if value:
        settings[channel] = value
    else:
        settings.pop(channel, None)
    write_json(ALERTS_FILE, settings)


def set_email(to, sender, host, port, user, tls, password):
    settings = {"to": to, "from": sender or to, "host": host, "port": port, "user": user, "tls": tls}
    check_email(settings)
    if password and "\n" in password:
        raise FnasError("mail passwords can't span lines")
    set_secret("smtp_password", password)
    set_alert_channel("email", settings)
    write_file(MSMTP_FILE, msmtp_config(settings, password), mode=0o600)


def set_ntfy(url, token):
    check_ntfy(url)
    set_secret("ntfy_token", token)
    set_alert_channel("ntfy", {"url": url})


def turn_off_email():
    set_secret("smtp_password", None)
    set_alert_channel("email", None)
    with contextlib.suppress(FileNotFoundError):
        os.remove(MSMTP_FILE)


def turn_off_ntfy():
    set_secret("ntfy_token", None)
    set_alert_channel("ntfy", None)
