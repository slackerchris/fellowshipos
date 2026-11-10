"""Long jobs: a resilver, scrub, RAIDZ expansion, parity sync or backup. Each runs as its
own systemd unit and records its progress in a file, so it carries on when
the browser closes or the web server restarts."""
import contextlib
import os
import re
import time
import uuid

from .common import DAY, FnasError, STATE_DIR, read_json, run, write_json
from . import alerts, backup, pools


JOBS_DIR = os.path.join(STATE_DIR, "jobs")
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
UNIT_PREFIX = "fellowship-job-"
KINDS = ("resilver", "scrub", "expand", "sync", "backup")
RUNNING = "running"
# How long finished jobs stay in the list
KEEP = 7 * DAY
POLL = 5


def path(job_id):
    if not JOB_ID.match(job_id or ""):
        raise FnasError(f"no such job: {job_id}")
    return os.path.join(JOBS_DIR, job_id + ".json")


def load(job_id):
    job = read_json(path(job_id))
    if not job:
        raise FnasError(f"no such job: {job_id}")
    return job


def save(job):
    write_json(path(job["id"]), job)


def start(kind, title, pool=None, details=None):
    """details is the engine's own: what the job runs, for an engine whose work isn't the kernel's."""
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "title": title, "pool": pool,
           "state": RUNNING, "progress": 0.0, "started": int(time.time()), "ended": None, "error": None,
           "details": details}
    save(job)
    run("systemd-run", "--quiet", "--collect", f"--unit={UNIT_PREFIX}{job['id']}",
        f"--description={title}", "/usr/sbin/fnas", "job", "run", job["id"])
    return job


def unit_active(job_id):
    return run("systemctl", "is-active", "--quiet", f"{UNIT_PREFIX}{job_id}.service",
               check=False).returncode == 0


def checked(job):
    """A job whose unit is gone without saying it finished was stopped, as by a restart."""
    if job["state"] == RUNNING and not unit_active(job["id"]):
        job.update(state="failed", ended=int(time.time()), error="stopped before it finished")
        save(job)
    return job


def show(job_id):
    return checked(load(job_id))


def list_jobs(now=None):
    now = now or time.time()
    found = []
    if not os.path.isdir(JOBS_DIR):
        return found
    for name in os.listdir(JOBS_DIR):
        if not name.endswith(".json"):
            continue
        job = read_json(os.path.join(JOBS_DIR, name))
        if job["ended"] and now - job["ended"] > KEEP:
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(JOBS_DIR, name))
            continue
        found.append(checked(job))
    return sorted(found, key=lambda job: job["started"], reverse=True)


def work(job):
    """Carry out one step, returning (error or RUNNING or None, progress)."""
    if job["kind"] == "backup":
        try:
            backup.run_backup()
        except FnasError as error:
            return str(error), 1.0
        return None, 1.0
    return pools.engine_for(job["pool"]).follow(job)


def run_job(job_id):
    job = load(job_id)
    if job["state"] != RUNNING:
        raise FnasError(f"job {job_id} has already finished")
    while True:
        try:
            outcome, progress = work(job)
        except FnasError as error:
            outcome, progress = str(error), job["progress"]
        job["progress"] = progress
        if outcome != RUNNING:
            break
        save(job)
        time.sleep(POLL)
    job.update(state="failed" if outcome else "done", error=outcome, ended=int(time.time()))
    save(job)
    if outcome:
        alerts.send_alert("critical", f"{job['title']} failed", outcome)
    elif job["kind"] == "expand":
        # ZED already reports finished scrubs and resilvers, and backups report themselves
        alerts.send_alert("info", f"Finished: {job['title']}", "The pool has its new space.")
