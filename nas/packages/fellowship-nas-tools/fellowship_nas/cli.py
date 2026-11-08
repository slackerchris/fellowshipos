"""The fnas command line: parsing, carrying out and printing."""
import argparse
import contextlib
import json
import os
import sys
import time

from .common import DAY, FnasError, HOUR, human_size, later, read_json, run, when
from . import alerts, backup, config, disks, health, jobs, pools, shares, snapshots, system
from .engines import zfs


# Actions that change nothing worth saving
READ_ONLY_ACTIONS = {"list", "scan", "status", "show", "send", "test", "run", "scrub", "engines",
                     "layouts", "identify", "storage", "timezones"}


def dispatch_system(args, show):
    if args.action == "show":
        show(system.system_info(), print_system)
    elif args.action == "timezones":
        show(system.timezones(), lambda zones: print("\n".join(zones)))
    elif args.action == "hostname":
        system.set_hostname(args.name)
        print(f"This NAS is now called {args.name}")
    elif args.action == "timezone":
        system.set_timezone(args.zone)
        print(f"The time zone is now {args.zone}")
    elif args.action == "certificate":
        if args.if_missing:
            if not os.path.exists(system.WEB_CERT):
                system.make_self_signed()
            return
        if args.self_signed:
            system.make_self_signed()
        else:
            system.set_certificate(sys.stdin.read())
        later("systemctl", "restart", system.WEB_SERVICE)
        print("The desktop restarts with the new certificate in a moment: reload the page")
    elif args.action == "storage":
        show(system.system_storage(), print_storage)
    elif args.action == "grow":
        show(system.grow_system(args.amount), print_storage)
    elif args.action in system.POWER:
        system.power(args.action, args.confirm)
        print("Restarting in a few seconds" if args.action == "restart" else "Shutting down in a few seconds")


def print_system(info):
    print(f"Name: {info['hostname']}")
    print(f"Time zone: {info['timezone']}" + ("" if info["ntp"] else " (clock not synchronised)"))
    print(f"Up for {info['uptime'] // DAY} days, {info['uptime'] % DAY // HOUR} hours")
    cert = info["certificate"]
    if cert:
        print(f"Certificate: {cert['subject']}, expires {when(cert['expires'])}"
              + (" (self-signed)" if cert["self_signed"] else ""))


def print_disks(disks, show_all):
    for disk in disks:
        if disk["state"] == "os" and not show_all:
            continue
        print(f"{disk['by_id'] or disk['path']}")
        print(f"    {human_size(disk['size'])}  {disk['model'] or 'unknown model'}  "
              f"serial {disk['serial'] or 'unknown'}")
        print(f"    {disk['state']}" + (f": {disk['reason']}" if disk["reason"] else ""))


def print_pools(pools):
    if not pools:
        print("No pools imported")
    for pool in pools:
        mode = "  read-only" if pool["readonly"] else ""
        print(f"{pool['name']}  {pool['health']}  {human_size(pool['allocated'])} used "
              f"of {human_size(pool['size'])}{mode}")


def print_engines(engines):
    for engine in engines:
        print(f"{engine['name']}  " + ("available" if engine["available"] else f"unavailable: {engine['reason']}"))


def print_layouts(options):
    for option in options:
        groups = f"{option['groups']} group" + ("" if option["groups"] == 1 else "s")
        spares = f", {option['spares']} spare" + ("" if option["spares"] == 1 else "s") if option["spares"] else ""
        print(f"{'* ' if option['recommended'] else '  '}{option['layout']} --width {option['width']} "
              f"--spares {option['spares']}: {groups}{spares}, {human_size(option['usable'])} usable, "
              f"each group survives {option['survives']} failed disk" + ("" if option["survives"] == 1 else "s"))


def print_jobs(found):
    if not found:
        print("No jobs")
    for job in found:
        state = f"{job['progress'] * 100:.0f}%" if job["state"] == "running" else job["state"]
        print(f"{job['id']}  {when(job['started'])}  {state}  {job['title']}")
        if job["error"]:
            print(f"    {job['error']}")


def print_storage(storage):
    print(f"System: {human_size(storage['used'])} used of {human_size(storage['size'])} on {storage['device']}")
    if storage["lvm"]:
        print(f"Room to grow: {human_size(storage['room'])}")


def print_scan(pools):
    if not pools:
        print("No importable pools found")
    for pool in pools:
        print(f"{pool.get('pool')}  id {pool.get('id')}  {pool.get('state')}")
        for key in ("status", "action"):
            if key in pool:
                print(f"    {key}: {pool[key]}")


def print_shares(shares):
    if not shares:
        print("No shares")
    for share in shares:
        protocols = [p for p in ("nfs", "smb") if share[p]]
        print(f"{share['name']}  {share['mode']}  {' '.join(protocols).upper() or 'not shared'}  "
              f"{share['path']}")


def print_snapshots(snapshots):
    if not snapshots:
        print("No snapshots")
    for snapshot in snapshots:
        print(f"{when(snapshot['created'])}  {human_size(snapshot['used'])}  {snapshot['name']}")


def print_backup(status):
    job = status["job"]
    if not job:
        print("No backup job")
        return
    print(f"{job['source']}/shares to {job['target']}, every night")
    print("Last backup: " + (when(status["last_success"]) if "last_success" in status else "none yet"))
    if status.get("error"):
        print(f"Last attempt failed: {status['error']}")


def print_backups(backups):
    if not backups:
        print("No backups")
    for found in backups:
        print(f"{found['name']}  from {found['pool']}  {found['snapshots']} snapshots")


def print_alerts(alerts):
    if not alerts:
        print("No alerts")
    for alert in alerts:
        print(f"{when(alert['time'])}  {alert['severity']}  {alert['title']}")
        print(f"    {alert['message']}")
        for failure in alert["undelivered"]:
            print(f"    not delivered: {failure}")


def print_channels(channels):
    if not channels:
        print("Alerts are only kept on this machine")
    if "email" in channels:
        email_settings = channels["email"]
        print(f"Email to {email_settings['to']} through {email_settings['host']}:{email_settings['port']}")
        if email_settings["needs_password"]:
            print("    needs its password: fnas alert email-set")
    if "ntfy" in channels:
        print(f"ntfy at {channels['ntfy']['url']}")


def print_findings(found):
    if not found:
        print("All checks passed")
    for item in found:
        print(f"{item['severity']}: {item['title']}")
        print(f"    {item['message']}")


def print_health(health):
    print_findings(health["problems"])
    for disk in health["disks"]:
        print(f"{disk['by_id'] or disk['path']}: {disk['verdict']}")
        for note in disk["notes"]:
            print(f"    {note}")


def print_config(saved):
    print(f"Saved {when(saved['saved'])}" + ("" if saved["install"] == config.install_id()
                                             else " by another install"))
    print(f"LAN subnet: {saved['lan'] or 'none'}")
    print("Shares: " + (", ".join(s["name"] for s in saved["shares"]) or "none"))
    print("Users: " + (", ".join(f"{u['name']} ({u['uid']})" for u in saved["users"]) or "none"))
    print("Alerts: " + (", ".join(saved["alerts"]) or "none"))
    print("Backup: " + (f"to {saved['backup']['target']}" if saved["backup"] else "none"))


def build_parser():
    parser = argparse.ArgumentParser(prog="fnas", description="FellowshipNAS storage and protection helper")
    parser.add_argument("--json", action="store_true", help="print lists as JSON")
    area = parser.add_subparsers(dest="area", required=True)

    disk = area.add_parser("disk", help="show disks").add_subparsers(dest="action", required=True)
    disk_list = disk.add_parser("list", help="list disks, hiding the OS disk")
    disk_list.add_argument("--all", action="store_true", help="include the OS disk")
    identify = disk.add_parser("identify", help="light a disk's bay, or make its activity light flicker")
    identify.add_argument("disk")
    identify.add_argument("--off", action="store_true", help="stop")

    pool = area.add_parser("pool", help="manage pools").add_subparsers(dest="action", required=True)
    pool.add_parser("list", help="list imported pools")
    pool.add_parser("scan", help="find pools that can be imported")
    pool.add_parser("engines", help="list storage engines and whether each works here")
    status = pool.add_parser("status", help="show pool health in detail")
    status.add_argument("name", nargs="?")
    layouts = pool.add_parser("layouts", help="list the layouts these disks fit")
    layouts.add_argument("disks", nargs="+", metavar="disk")
    create = pool.add_parser("create", help="create a pool, erasing its disks")
    create.add_argument("name")
    create.add_argument("layout", choices=zfs.LAYOUTS)
    create.add_argument("disks", nargs="+", metavar="disk", help="by-id path, device path or serial")
    create.add_argument("--width", type=int, help="disks in each group (mirror: 2; RAIDZ: all of them)")
    create.add_argument("--spares", type=int, default=0, help="how many of the disks are hot spares")
    create.add_argument("--engine", default="zfs", help="the storage engine")
    create.add_argument("--confirm", default="", help='type "erase N disks"')
    create.add_argument("--wipe", action="store_true", help="erase disks that hold old data")
    grow = pool.add_parser("grow", help="add disks to a pool")
    grow.add_argument("name")
    grow.add_argument("how", choices=("group", "expand", "mirror", "spare"),
                      help="a new group like the others, one more disk in a RAIDZ group, "
                           "a mirror for a disk, or hot spares")
    grow.add_argument("disks", nargs="+", metavar="disk")
    grow.add_argument("--target", help="the group (expand) or disk (mirror) the new disk joins")
    grow.add_argument("--confirm", default="", help='type "erase N disks"')
    grow.add_argument("--wipe", action="store_true", help="erase disks that hold old data")
    replace = pool.add_parser("replace", help="swap a failed disk for a new one")
    replace.add_argument("name")
    replace.add_argument("old", help="the disk's path or name in the pool's status")
    replace.add_argument("new")
    replace.add_argument("--confirm", default="", help='type "erase 1 disk"')
    replace.add_argument("--wipe", action="store_true", help="erase the new disk if it holds old data")
    remove_spare = pool.add_parser("remove-spare", help="take a hot spare out of a pool")
    remove_spare.add_argument("name")
    remove_spare.add_argument("disk")
    import_ = pool.add_parser("import", help="import a pool by name or id")
    import_.add_argument("pool")
    import_.add_argument("--readonly", action="store_true", help="import without writing to the pool")
    import_.add_argument("--force", action="store_true",
                         help="import a pool last used by another system, as after a reinstall")
    export = pool.add_parser("export", help="export a pool")
    export.add_argument("name")
    pool.add_parser("scrub", help="check every block on a pool now").add_argument("name")

    share = area.add_parser("share", help="manage shares").add_subparsers(dest="action", required=True)
    share.add_parser("list", help="list shares")
    create = share.add_parser("create", help="create a share in POOL/shares")
    create.add_argument("name")
    create.add_argument("pool")
    create.add_argument("--mode", required=True, choices=shares.SHARE_MODES,
                        help="shared: everyone writes as one account; matched: user IDs must match")
    create.add_argument("--owner", help="the user who owns a matched share")
    create.add_argument("--nfs", action="store_true", help="share over NFS")
    create.add_argument("--smb", action="store_true", help="share over SMB")
    change = share.add_parser("set", help="turn a protocol on or off, keeping the data")
    change.add_argument("name")
    change.add_argument("--nfs", choices=("on", "off"))
    change.add_argument("--smb", choices=("on", "off"))
    share.add_parser("refresh", help="rewrite the NFS and Samba settings from each pool")
    delete = share.add_parser("delete", help="destroy a share, its data and its snapshots")
    delete.add_argument("name")
    delete.add_argument("--confirm", default="", help='type "delete NAME and N snapshots"')

    user = area.add_parser("user", help="accounts that use shares").add_subparsers(
        dest="action", required=True)
    smb_password = user.add_parser("smb-password", help="set the password a user types for SMB shares")
    smb_password.add_argument("name")
    smb_password.add_argument("--password-stdin", action="store_true", required=True,
                              help="read the password from stdin")

    lan = area.add_parser("lan", help="the subnet shares are limited to").add_subparsers(
        dest="action", required=True)
    lan.add_parser("show", help="show the LAN subnet")
    lan_set = lan.add_parser("set", help="change the LAN subnet and update every share")
    lan_set.add_argument("subnet")

    snapshot = area.add_parser("snapshot", help="snapshots of shares").add_subparsers(
        dest="action", required=True)
    snapshot_list = snapshot.add_parser("list", help="list a share's snapshots")
    snapshot_list.add_argument("name")
    policy = snapshot.add_parser("policy", help="choose how many snapshots a share keeps")
    policy.add_argument("name")
    for period in snapshots.DEFAULT_POLICY:
        policy.add_argument(f"--{period}", type=int, help="how many to keep; 0 for none")

    backup_ = area.add_parser("backup", help="nightly backup to a second pool").add_subparsers(
        dest="action", required=True)
    backup_.add_parser("show", help="show the backup job")
    backup_set = backup_.add_parser("set", help="back up POOL's shares to TARGET every night")
    backup_set.add_argument("pool")
    backup_set.add_argument("target")
    backup_.add_parser("off", help="stop backing up, keeping what's there")
    backup_.add_parser("run", help="back up now").add_argument(
        "--background", action="store_true", help="run as a job and return at once")
    backup_list = backup_.add_parser("list", help="list shares that have a backup")
    backup_list.add_argument("--from", dest="from_pool", help="the backup pool")
    restore = backup_.add_parser("restore", help="bring back a share that no longer exists")
    restore.add_argument("name")
    restore.add_argument("--from", dest="from_pool", help="the backup pool")
    restore.add_argument("--to", dest="to_pool", help="the pool to restore to")

    alert = area.add_parser("alert", help="alerts and where they go").add_subparsers(
        dest="action", required=True)
    alert_list = alert.add_parser("list", help="show recent alerts")
    alert_list.add_argument("--limit", type=int, default=20)
    alert.add_parser("show", help="show where alerts go")
    send = alert.add_parser("send", help="send an alert")
    send.add_argument("--severity", choices=alerts.SEVERITIES, default="warning")
    send.add_argument("title")
    send.add_argument("message")
    alert.add_parser("test", help="send a test alert")
    email_set = alert.add_parser("email-set", help="send alerts by email")
    email_set.add_argument("--to", required=True)
    email_set.add_argument("--from", dest="sender", help="defaults to the --to address")
    email_set.add_argument("--host", required=True, help="the mail server")
    email_set.add_argument("--port", type=int, default=587)
    email_set.add_argument("--user", default="")
    email_set.add_argument("--tls", choices=("starttls", "on", "off"), default="starttls")
    email_set.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    alert.add_parser("email-off", help="stop sending email")
    ntfy_set = alert.add_parser("ntfy-set", help="send alerts to an ntfy topic")
    ntfy_set.add_argument("url", help="the topic's address, such as https://ntfy.sh/my-topic")
    ntfy_set.add_argument("--token-stdin", action="store_true", help="read an access token from stdin")
    alert.add_parser("ntfy-off", help="stop sending to ntfy")

    config_ = area.add_parser("config", help="the settings saved on each pool").add_subparsers(
        dest="action", required=True)
    for action, text in (("show", "show the settings saved on a pool"),
                         ("restore", "bring back settings after a reinstall")):
        config_.add_parser(action, help=text).add_argument("pool")
    adopt = config_.add_parser("adopt", help="replace a pool's saved settings with this install's")
    adopt.add_argument("pool")
    adopt.add_argument("--confirm", default="", help='type "replace saved settings on POOL"')

    system_ = area.add_parser("system", help="name, time, certificate and power").add_subparsers(
        dest="action", required=True)
    system_.add_parser("show", help="show the name, time zone, uptime and certificate")
    system_.add_parser("timezones", help="list time zones")
    system_.add_parser("hostname", help="rename this NAS").add_argument("name")
    system_.add_parser("timezone", help="change the time zone").add_argument("zone")
    system_.add_parser("storage", help="show the system volume and its room to grow")
    system_.add_parser("grow", help="give the system volume more room").add_argument(
        "amount", help="bytes, 8G, 1T, or all")
    certificate = system_.add_parser("certificate", help="the desktop's HTTPS certificate")
    choice = certificate.add_mutually_exclusive_group(required=True)
    choice.add_argument("--stdin", action="store_true", help="read a PEM certificate and key from stdin")
    choice.add_argument("--self-signed", action="store_true", help="make a new self-signed certificate")
    choice.add_argument("--if-missing", action="store_true", help="make one only if there is none")
    for action, (_, phrase) in system.POWER.items():
        system_.add_parser(action, help=phrase).add_argument("--confirm", default="", help=f'type "{phrase}"')

    job = area.add_parser("job", help="long jobs and their progress").add_subparsers(
        dest="action", required=True)
    job.add_parser("list", help="list running and recent jobs")
    job.add_parser("show", help="show one job").add_argument("id")
    job.add_parser("run", help=argparse.SUPPRESS).add_argument("id")

    area.add_parser("check", help="look for problems and send alerts")
    area.add_parser("health", help="show problems and each disk's SMART health, sending nothing")
    return parser


def dispatch(args):
    def show(data, printer, *extra):
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            printer(data, *extra)

    if args.area == "disk" and args.action == "identify":
        print(disks.identify(args.disk, not args.off))
    elif args.area == "disk":
        show(disks.list_disks(), print_disks, args.all)
    elif args.area == "share":
        dispatch_share(args, show)
    elif args.area == "user":
        shares.set_smb_password(args.name, read_secret(True))
        print(f"{args.name} can log in to SMB shares")
    elif args.area == "lan":
        print(shares.set_lan(args.subnet) if args.action == "set" else shares.lan_subnet())
    elif args.area == "snapshot":
        dispatch_snapshot(args, show)
    elif args.area == "backup":
        dispatch_backup(args, show)
    elif args.area == "alert":
        dispatch_alert(args, show)
    elif args.area == "config":
        dispatch_config(args, show)
    elif args.area == "system":
        dispatch_system(args, show)
    elif args.area == "check":
        show(health.run_checks(), print_findings)
    elif args.area == "health":
        show({"problems": health.find_problems(time.time()),
              "disks": [disks.disk_health(d) for d in disks.list_disks() if d["kind"] == "disk"]}, print_health)
    elif args.area == "job":
        dispatch_job(args, show)
    else:
        dispatch_pool(args, show)


def started(job):
    if job:
        print(f"{job['title']}: follow it with fnas job show {job['id']}")


def dispatch_pool(args, show):
    if args.action == "list":
        show(pools.list_pools(), print_pools)
    elif args.action == "scan":
        show(pools.scan_pools(), print_scan)
    elif args.action == "engines":
        show(pools.engines(), print_engines)
    elif args.action == "status":
        if not args.name:
            print(run("zpool", "status", "-P").stdout, end="")
        elif args.json:
            show(pools.engine_for(args.name).pool_status(args.name), None)
        else:
            pools.check_pool_name(args.name)
            print(run("zpool", "status", "-P", args.name).stdout, end="")
    elif args.action == "layouts":
        found = disks.list_disks()
        sizes = [disks.find_disk(found, wanted)["size"] for wanted in args.disks]
        show(zfs.layouts(sizes), print_layouts)
    elif args.action == "create":
        pools.engine_named(args.engine).create_pool(args.name, args.layout, args.disks, args.confirm,
                                                    args.wipe, args.width, args.spares)
        print(f"Created pool {args.name}")
    elif args.action == "grow":
        job = pools.engine_for(args.name).grow(args.name, args.how, args.disks, args.confirm,
                                               args.wipe, args.target)
        print(f"Added {len(args.disks)} disk" + ("" if len(args.disks) == 1 else "s") + f" to {args.name}")
        started(job)
    elif args.action == "replace":
        started(pools.engine_for(args.name).replace(args.name, args.old, args.new, args.confirm, args.wipe))
    elif args.action == "remove-spare":
        pools.engine_for(args.name).remove_spare(args.name, args.disk)
        print(f"Removed the spare from {args.name}")
    elif args.action == "import":
        found = [p for p in pools.scan_pools() if args.pool in (p.get("pool"), p.get("id"))]
        engine = pools.ENGINES[found[0]["engine"]] if found else zfs
        engine.import_pool(args.pool, args.readonly, args.force)
        print(f"Imported {args.pool}" + (" read-only" if args.readonly else ""))
        for pool in config.foreign_config_pools():
            print(f"{pool} has saved settings: bring them back with fnas config restore {pool}")
    elif args.action == "export":
        pools.engine_for(args.name).export_pool(args.name)
        print(f"Exported {args.name}")
    elif args.action == "scrub":
        started(pools.engine_for(args.name).scrub_pool(args.name))


def dispatch_job(args, show):
    if args.action == "list":
        show(jobs.list_jobs(), print_jobs)
    elif args.action == "show":
        show(jobs.show(args.id), lambda job: print_jobs([job]))
    elif args.action == "run":
        jobs.run_job(args.id)


def dispatch_share(args, show):
    if args.action == "list":
        show(shares.list_shares(), print_shares)
    elif args.action == "create":
        shares.create_share(args.name, args.pool, args.mode, args.owner, args.nfs, args.smb)
        print(f"Created share {args.name}")
    elif args.action == "set":
        shares.set_share(args.name, args.nfs, args.smb)
        print(f"Updated share {args.name}")
    elif args.action == "refresh":
        shares.migrate()
        shares.write_config()
    elif args.action == "delete":
        shares.delete_share(args.name, args.confirm)
        print(f"Deleted share {args.name}")


def dispatch_snapshot(args, show):
    if args.action == "list":
        show(snapshots.list_snapshots(shares.find_share(args.name)["dataset"]), print_snapshots)
    elif args.action == "policy":
        policy = snapshots.set_policy(args.name, args.hourly, args.daily, args.monthly)
        print(f"{args.name} keeps " + ", ".join(f"{kept} {period}" for period, kept in policy.items()))


def dispatch_backup(args, show):
    if args.action == "show":
        show(backup.backup_status(), print_backup)
    elif args.action == "set":
        backup.set_backup(args.pool, args.target)
        print(f"Backing up {args.pool} to {args.target} every night")
    elif args.action == "off":
        with contextlib.suppress(FileNotFoundError):
            os.remove(backup.BACKUP_FILE)
        print("Backups are off. What's on the backup pool stays there")
    elif args.action == "run" and args.background:
        if not backup.backup_status()["job"]:
            raise FnasError("no backup job: run fnas backup set POOL TARGET")
        started(jobs.start("backup", "Backing up now"))
    elif args.action == "run":
        backup.run_backup()
        print("Backup finished")
    elif args.action == "list":
        show(backup.list_backups(args.from_pool), print_backups)
    elif args.action == "restore":
        backup.restore_backup(args.name, args.from_pool, args.to_pool)
        print(f"Restored share {args.name}")


def read_secret(wanted):
    return sys.stdin.read().rstrip("\n") if wanted else None


def dispatch_alert(args, show):
    if args.action == "list":
        show(read_json(alerts.ALERT_LOG, [])[-args.limit:], print_alerts)
    elif args.action == "show":
        show(alerts.alert_channels(), print_channels)
    elif args.action == "send":
        alerts.check_delivered(alerts.send_alert(args.severity, args.title, args.message))
    elif args.action == "test":
        alerts.check_delivered(alerts.send_alert("info", "Test alert", "Alerts from this NAS are reaching you."))
        print("Sent a test alert")
    elif args.action == "email-set":
        alerts.set_email(args.to, args.sender, args.host, args.port, args.user, args.tls,
                  read_secret(args.password_stdin))
        print(f"Sending alerts to {args.to}")
    elif args.action == "email-off":
        alerts.turn_off_email()
        print("Email alerts are off")
    elif args.action == "ntfy-set":
        alerts.set_ntfy(args.url, read_secret(args.token_stdin))
        print(f"Sending alerts to {args.url}")
    elif args.action == "ntfy-off":
        alerts.turn_off_ntfy()
        print("ntfy alerts are off")


def dispatch_config(args, show):
    if args.action == "show":
        saved = config.saved_config(args.pool)
        if not saved:
            raise FnasError(f"{args.pool} has no saved settings")
        show({**saved, "from_this_install": saved.get("install") == config.install_id()}, print_config)
    elif args.action == "restore":
        notes = config.restore_config(args.pool)
        print(f"Restored settings from {args.pool}")
        for note in notes:
            print(f"    {note}")
    elif args.action == "adopt":
        config.adopt_config(args.pool, args.confirm)
        print(f"{args.pool} now holds this install's settings")


def main():
    args = build_parser().parse_args()
    try:
        if os.geteuid() != 0:
            raise FnasError("run as root: sudo fnas ...")
        dispatch(args)
        if args.area not in ("check", "health", "system", "job") and args.action not in READ_ONLY_ACTIONS:
            config.after_change()
    except FnasError as error:
        print(f"fnas: {error}", file=sys.stderr)
        return 1
    return 0
