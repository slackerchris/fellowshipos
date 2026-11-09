"""Running commands and keeping files, shared by every part of fnas."""
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess


HOUR = 3600
DAY = 24 * HOUR
STATE_DIR = "/var/lib/fellowship-nas"
ACCOUNT_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# Anything that can take longer than this belongs in a job, which runs as its
# own systemd unit. A command still running after this long is stuck, and
# stopping it beats holding a root process and the browser's request forever.
TIMEOUT = 300


class FnasError(Exception):
    pass


def run(*command, check=True, stdin=None, timeout=TIMEOUT):
    try:
        result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/root"})
    except FileNotFoundError:
        raise FnasError(f"{command[0]} is not installed") from None
    except subprocess.TimeoutExpired:
        raise FnasError(f"{command[0]} was still running after {timeout} seconds and was stopped") from None
    if check and result.returncode != 0:
        raise FnasError(result.stderr.strip() or f"{command[0]} failed")
    return result


def human_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} PB"


def write_file(path, content, mode=0o644):
    descriptor = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as file:
        os.fchmod(descriptor, mode)
        file.write(content)
    os.replace(path + ".new", path)


def read_json(path, default=None):
    try:
        with open(path) as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except ValueError:
        raise FnasError(f"{path} is damaged") from None


def write_json(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_file(path, json.dumps(data, indent=2) + "\n", mode)


@contextlib.contextmanager


def locked(path, wait=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise FnasError("another fnas is already doing this") from None
        yield


def later(*command, seconds=2):
    """Run a command shortly after fnas answers, so the desktop hears the answer first."""
    run("systemd-run", "--quiet", "--collect", f"--on-active={seconds}", *command)


def when(stamp):
    return datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
