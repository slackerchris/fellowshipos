"""The fnas command line: parsing, carrying out and printing."""
import argparse
import contextlib
import json
import os
import sys
import time

from .common import DAY, FnasError, HOUR, human_size, later, read_json, run, when
from . import alerts, backup, config, disks, health, jobs, look, pools, services, shares, sftp, snapshots, \
    ssh, system, usage, users
from . import storage_checks
from .engines import mdadm


# Actions that change nothing worth saving
READ_ONLY_ACTIONS = {"list", "scan", "status", "show", "send", "test", "run", "scrub", "engines",
                     "layouts", "identify", "storage", "timezones", "services", "shares", "details", "keys",
                     "array-checks", "watch-events", "schedules",
                     "apply-schedules", "scrub-all", "sync-all", "sync"}
ON_OFF = ("on", "off")


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


def print_schedules(rows):
    for row in rows:
        if not row["available"]:
            state = row["reason"]
        elif not row.get("enabled"):
            state = "automatic checks off"
        else:
            state = f"checks {row['description']}" + (f", next {when(row['next'])}" if row.get("next") else "")
        print(f"{row['title']}: {state}" + (f" ({', '.join(row['pools'])})" if row["pools"] else ""))


def print_engines(engines):
    for engine in engines:
        print(f"{engine['name']} ({engine['title']})  "
              + ("available" if engine["available"] else f"unavailable: {engine['reason']}"))


def print_details(details):
    space = details["space"]
    print(f"{details['name']}: {human_size(space['used'])} used, {human_size(space['free'])} free")
    for part, used in space["parts"].items():
        print(f"    {part}: {human_size(used)}")
    for share in space["shares"]:
        print(f"    share {share['name']}: {human_size(share['files'])} files, "
              f"{human_size(share['snapshots'])} in snapshots")
    print(f"Compression {details['compression']:.2f}x, created {when(details['created'])}")
    found = details["projection"]
    print(f"Full in about {found['days']} days" if "days" in found else found["reason"])


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


def describe_access(share):
    if share["mode"] == "everyone":
        return "everyone"
    listed = [f"{name} ({level})" for name, level in share["access"]["users"].items()]
    listed += [f"group {name} ({level})" for name, level in share["access"]["groups"].items()]
    return ", ".join(listed) or "nobody yet"


def print_shares(shares):
    if not shares:
        print("No shares")
    for share in shares:
        protocols = [p for p in ("nfs", "smb", "sftp", "webdav") if share[p]]
        print(f"{share['name']}  {' '.join(protocols).upper() or 'not shared'}  {share['path']}")
        print(f"    for {describe_access(share)}")


def print_users(found):
    for user in found:
        extras = [user["kind"] == "ssh" and "SSH", user["kind"] == "sftp" and "SFTP only",
                  user["admin"] and "administrator",
                  not user["password"] and "no password yet",
                  user["password"] and not user["smb_password"] and "no SMB password", not user["enabled"] and "turned off"]
        print(f"{user['name']}  {user['full_name']}  " + ", ".join(e for e in extras if e))
        if user["groups"]:
            print(f"    groups: {', '.join(user['groups'])}")


def print_groups(found):
    if not found:
        print("No groups")
    for group in found:
        # The machine's own groups say so, since fnas can't rename or remove them
        mark = f" ({group['gid']}, the machine's)" if group.get("system") else ""
        print(f"{group['name']}{mark}: {', '.join(group['members']) or 'no members'}")


def print_sharing(settings):
    for protocol, values in settings.items():
        print(f"{protocol.upper()}: " + ", ".join(f"{key} {value}" for key, value in values.items()))


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


CHECK_STATES = {"passed": "passed", "na": "doesn't apply", "low": "LOW", "medium": "MEDIUM", "high": "HIGH",
                "critical": "CRITICAL"}


def print_health(report):
    hourly = report["hourly"]["last"]
    print("Hourly check last ran " + (when(hourly) if hourly else "never"))
    section = None
    for item in report["checks"]:
        if item["section"] != section:
            section = item["section"]
            print(f"{section}:")
        print(f"    {CHECK_STATES[item['state']]}: {item['title']}: {item['detail']}")
    print("Services:")
    for service in report["services"]:
        print(f"    {service['title']}: {service['detail']}")
    for disk in report["disks"]:
        print(f"{disk['by_id'] or disk['path']}: {disk['verdict']}")
        for note in disk["notes"]:
            print(f"    {note}")


def print_protocols(found):
    for protocol in found:
        print(f"{protocol['title']}  {protocol['detail']}  ({protocol['for']})")


def print_setup(status):
    if not status["done"]:
        print("Setup hasn't been finished: it opens when an admin logs in to the desktop")
    else:
        print(("Skipped " if status["skipped"] else "Finished ") + when(status["finished"]))


def print_config(saved):
    print(f"Saved {when(saved['saved'])}" + ("" if saved["install"] == config.install_id()
                                             else " by another install"))
    print(f"LAN subnet: {saved['lan'] or 'none'}")
    print("Shares: " + (", ".join(s["name"] for s in saved["shares"]) or "none"))
    print("Users: " + (", ".join(f"{u['name']} ({u['uid']})" for u in saved["users"]) or "none"))
    print("Alerts: " + (", ".join(saved["alerts"]) or "none"))
    print("Backup: " + (f"to {saved['backup']['target']}" if saved["backup"] else "none"))


def add_access_arguments(parser):
    parser.add_argument("--everyone", action="store_true", help="every user, writing as the share account")
    parser.add_argument("--user", action="append", default=[], metavar="NAME=read|write")
    parser.add_argument("--group", action="append", default=[], metavar="NAME=read|write")


def pairs(values, allowed):
    found = []
    for value in values or []:
        name, _, level = value.rpartition("=")
        if not name or level not in allowed:
            raise FnasError(f"expected NAME={'|'.join(allowed)}, not {value}")
        found.append((name, level))
    return found


def access_from(args):
    return {"users": dict(pairs(args.user, shares.ACCESS_LEVELS)),
            "groups": dict(pairs(args.group, shares.ACCESS_LEVELS))}


def switch(value):
    return None if value is None else value == "on"


def names(value):
    return [name for name in (value or "").split(",") if name]


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
    pool.add_parser("details", help="what uses a pool's space, its history and when it fills").add_argument("name")
    layouts = pool.add_parser("layouts", help="list the layouts these disks fit")
    layouts.add_argument("disks", nargs="+", metavar="disk")
    layouts.add_argument("--engine", default="zfs", help="the storage engine")
    create = pool.add_parser("create", help="create a pool, erasing its disks")
    create.add_argument("name")
    create.add_argument("layout", metavar="layout",
                        help="a layout the engine offers; fnas pool layouts lists the ones that fit")
    create.add_argument("disks", nargs="+", metavar="disk", help="by-id path, device path or serial")
    create.add_argument("--width", type=int, help="disks in each group (mirror: 2; RAIDZ: all of them)")
    create.add_argument("--spares", type=int, default=0, help="how many of the disks are hot spares")
    create.add_argument("--engine", default="zfs", help="the storage engine")
    create.add_argument("--filesystem", help="an array's filesystem: xfs (recommended) or ext4")
    create.add_argument("--confirm", default="", help='type "erase N disks"')
    create.add_argument("--wipe", action="store_true", help="erase disks that hold old data")
    grow = pool.add_parser("grow", help="add disks to a pool")
    grow.add_argument("name")
    grow.add_argument("how", choices=("group", "expand", "mirror", "spare", "data", "parity"),
                      help="a new group like the others, one more disk in a RAIDZ group or array, "
                           "a mirror for a disk, hot spares, or an Unraid-style pool's data or parity disks")
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
    import_.add_argument("--engine", default="zfs",
                         help="the engine whose scan didn't list it; the one that did is used instead")
    export = pool.add_parser("export", help="export a pool")
    export.add_argument("name")
    pool.add_parser("scrub", help="check every block on a pool now").add_argument("name")
    repair = pool.add_parser("repair", help="mend the blocks a check found to be wrong")
    repair.add_argument("name")
    repair.add_argument("--confirm", default="", help='type "repair NAME"')
    # Debian's mdcheck timers run these around each night's work, so Health sees their checks
    array_checks = pool.add_parser("array-checks", help="note the array checks Debian's mdcheck runs")
    array_checks.add_argument("when", choices=("starting", "resuming", "stopped"))
    pool.add_parser("watch-events", help="send mdadm's array events to alerts")
    pool.add_parser("schedules", help="when each engine checks its pools by itself")
    schedule = pool.add_parser("schedule", help="set when an engine checks its pools by itself")
    schedule.add_argument("engine")
    schedule.add_argument("--checks", choices=ON_OFF, help="check automatically")
    schedule.add_argument("--every", choices=("weekly", "monthly", "quarterly"))
    schedule.add_argument("--week", type=int, choices=range(1, 5), help="the first to fourth of that day in the month")
    schedule.add_argument("--day", choices=("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
    schedule.add_argument("--hour", type=int, choices=range(24))
    schedule.add_argument("--sync", choices=ON_OFF, help="sync parity every night")
    schedule.add_argument("--sync-hour", type=int, choices=range(24))
    schedule.add_argument("--spin-down", type=int, metavar="MINUTES", help="idle minutes before a disk spins down, 0 for never")
    pool.add_parser("apply-schedules", help="bring the check timers in line with the saved schedules")
    scrub_all = pool.add_parser("scrub-all", help="check every healthy pool on one engine now")
    scrub_all.add_argument("engine")
    scrub_all.add_argument("--scheduled", action="store_true", help="the timer's check, which may read only part")
    sync = pool.add_parser("sync", help="bring an Unraid-style pool's parity up to date now")
    sync.add_argument("name")
    sync.add_argument("--force", action="store_true", help="sync even though many files went missing")
    pool.add_parser("sync-all", help="sync parity on every healthy pool of one engine").add_argument("engine")

    share = area.add_parser("share", help="manage shares").add_subparsers(dest="action", required=True)
    share.add_parser("list", help="list shares")
    share_roots = share.add_parser("roots", help="the shares a person can use, and where each one is")
    share_roots.add_argument("user")
    create = share.add_parser("create", help="create a share in POOL/shares")
    create.add_argument("name")
    create.add_argument("pool")
    add_access_arguments(create)
    create.add_argument("--nfs", action="store_true", help="share over NFS")
    create.add_argument("--smb", action="store_true", help="share over SMB")
    create.add_argument("--webdav", action="store_true", help="share over HTTPS as a WebDAV drive")
    create.add_argument("--sftp", action="store_true", help="share over SFTP, for scripts and phones")
    access = share.add_parser("access", help="change who can use a share, for every file in it")
    access.add_argument("name")
    add_access_arguments(access)
    smb = share.add_parser("smb", help="a share's SMB settings")
    smb.add_argument("name")
    for flag, text in (("readonly", "nobody can change files over SMB"), ("hidden", "not listed when browsing"),
                       ("guest", "guests can read without a password (shares for everyone only)"),
                       ("recycle", "deleted files go to .recycle"), ("timemachine", "Mac backups to this share"),
                       ("previous-versions", "show snapshots in Windows")):
        smb.add_argument(f"--{flag}", choices=ON_OFF, help=text)
    smb.add_argument("--recycle-days", type=int, help="how long the recycle bin keeps files")
    smb.add_argument("--timemachine-size", type=int, help="the most Time Machine may use, in GB; 0 for no limit")
    nfs = share.add_parser("nfs", help="a share's NFS settings")
    nfs.add_argument("name")
    nfs.add_argument("--client", action="append", metavar="NETWORK=rw|ro",
                     help="a network or address that may mount it; repeat for more")
    nfs.add_argument("--home-network", action="store_true", help="only the LAN subnet, read-write (the default)")
    nfs.add_argument("--readonly", choices=ON_OFF, help="read-only for every client")
    nfs.add_argument("--mapping", choices=shares.NFS_MAPPINGS,
                     help="all: everyone as the share account; root: real users, root squashed; "
                          "none: real users including root; auto: follow the share's access")
    nfs.add_argument("--async", dest="async_writes", choices=ON_OFF,
                     help="faster writes that a power cut can lose")
    change = share.add_parser("set", help="turn a protocol on or off, keeping the data")
    change.add_argument("name")
    change.add_argument("--nfs", choices=("on", "off"))
    change.add_argument("--smb", choices=("on", "off"))
    change.add_argument("--webdav", choices=("on", "off"))
    change.add_argument("--sftp", choices=("on", "off"))
    share.add_parser("refresh", help="rewrite the NFS and Samba settings from each pool")
    share.add_parser("services", help="list every way to share files and whether each is running")
    delete = share.add_parser("delete", help="destroy a share, its data and its snapshots")
    delete.add_argument("name")
    delete.add_argument("--confirm", default="", help='type "delete NAME and N snapshots"')

    sharing = area.add_parser("sharing", help="NAS-wide SMB and NFS settings").add_subparsers(
        dest="action", required=True)
    sharing.add_parser("show", help="show the SMB and NFS settings")
    sharing_smb = sharing.add_parser("smb", help="change the SMB settings")
    sharing_smb.add_argument("--workgroup")
    sharing_smb.add_argument("--description", help="what computers show when browsing")
    sharing_smb.add_argument("--min-protocol", choices=tuple(shares.SMB_PROTOCOLS), help="the oldest protocol allowed")
    sharing_smb.add_argument("--encryption", choices=shares.SMB_ENCRYPTION,
                             help="desired: when the computer supports it; required: always")
    sharing_smb.add_argument("--windows-discovery", choices=ON_OFF, help="show the NAS in Windows Network")
    sharing_smb.add_argument("--mac-discovery", choices=ON_OFF, help="show the NAS in the macOS Finder")
    sharing_nfs = sharing.add_parser("nfs", help="change the NFS settings")
    sharing_nfs.add_argument("--v3", choices=ON_OFF, help="NFS version 3 for older clients")
    sharing_nfs.add_argument("--threads", type=int)
    sharing_nfs.add_argument("--fixed-ports", choices=ON_OFF, help="fixed ports for firewalls")

    user = area.add_parser("user", help="people's accounts").add_subparsers(dest="action", required=True)
    user.add_parser("list", help="list people's accounts")
    user.add_parser("shares", help="the shares a user can use").add_argument("name")
    user_create = user.add_parser("create", help="add an account; the password comes from stdin")
    user_create.add_argument("name")
    user_create.add_argument("--full-name", default="")
    user_create.add_argument("--kind", choices=tuple(users.KINDS), default="shares",
                             help="shares: shares only, no SSH; ssh: shares and SSH")
    user_create.add_argument("--admin", action="store_true", help="can manage the NAS")
    user_create.add_argument("--groups", default="", help="comma-separated")
    user_create.add_argument("--password-stdin", action="store_true", required=True)
    user_set = user.add_parser("set", help="change an account")
    user_set.add_argument("name")
    user_set.add_argument("--full-name")
    user_set.add_argument("--kind", choices=tuple(users.KINDS))
    user_set.add_argument("--admin", choices=ON_OFF)
    user_set.add_argument("--enabled", choices=ON_OFF, help="off: can't log in or reach shares")
    user_set.add_argument("--groups", help="comma-separated; replaces the account's groups")
    for action, text in (("password", "set the one password for the desktop and every way to share"),
                         ("smb-password", "the same as password, kept for older desktops")):
        found = user.add_parser(action, help=text)
        found.add_argument("name")
        found.add_argument("--password-stdin", action="store_true", required=True, help="read the password from stdin")
    user_remove = user.add_parser("remove", help="remove an account")
    user_remove.add_argument("name")
    user_remove.add_argument("--keep-home", action="store_true", help="keep the home folder")
    user_remove.add_argument("--confirm", default="", help='type "remove NAME"')

    user_keys = user.add_parser("keys", help="list an account's SSH keys, or replace them from stdin")
    user_keys.add_argument("name")
    user_keys.add_argument("--set-stdin", action="store_true", help="one public key per line; nothing removes them all")

    ssh_ = area.add_parser("ssh", help="who may log in over SSH, and how").add_subparsers(dest="action", required=True)
    ssh_.add_parser("show", help="show the SSH settings and who may log in")
    ssh_set = ssh_.add_parser("set", help="change the SSH settings")
    ssh_set.add_argument("--enabled", choices=ON_OFF, help="off: nobody can log in over SSH")
    ssh_set.add_argument("--passwords", choices=ON_OFF, help="off: keys only, once an administrator has one")
    ssh_.add_parser("refresh", help="rewrite sshd's settings and who is in nas-ssh")

    look_ = area.add_parser("look", help="how a person likes the desktop: light, dark and the theme"
                            ).add_subparsers(dest="action", required=True)
    look_.add_parser("show", help="a person's light or dark and theme").add_argument("user")
    look_set = look_.add_parser("set", help="remember a person's light or dark and theme")
    look_set.add_argument("user")
    look_set.add_argument("--mode", choices=look.MODES,
                          help="light, dark, or system to follow the device")
    look_set.add_argument("--palette", help="a theme's name, such as fellowship or highwatch")

    group = area.add_parser("group", help="groups of people").add_subparsers(dest="action", required=True)
    group_list = group.add_parser("list", help="list groups and their members")
    group_list.add_argument("--system", action="store_true",
                            help="also list the machine's own groups, such as video and wheel")
    group_create = group.add_parser("create", help="add a group")
    group_create.add_argument("name")
    group_create.add_argument("--members", default="", help="comma-separated")
    group_members = group.add_parser("members", help="replace a group's members")
    group_members.add_argument("name")
    group_members.add_argument("members", help="comma-separated, or empty for none")
    group_rename = group.add_parser("rename", help="rename a group")
    group_rename.add_argument("name")
    group_rename.add_argument("new")
    group.add_parser("remove", help="remove a group; its members keep their accounts").add_argument("name")

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
    send.add_argument("--severity", choices=alerts.SEVERITIES + tuple(alerts.OLD_SEVERITIES), default="medium",
                      help="critical: something is broken; high, medium or low: a warning; info: nothing wrong")
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

    setup = area.add_parser("setup", help="whether the desktop's first-run Setup is done").add_subparsers(
        dest="action", required=True)
    setup.add_parser("status", help="show whether Setup was finished or skipped")
    setup_done = setup.add_parser("done", help="mark Setup finished, so it stops opening by itself")
    setup_done.add_argument("--skipped", action="store_true", help="it was skipped rather than finished")
    setup.add_parser("reset", help="open Setup again at the next admin login")

    area.add_parser("check", help="look for problems and send alerts")
    area.add_parser("health", help="show every check, service, pool and disk, sending nothing")
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
        dispatch_user(args, show)
    elif args.area == "group":
        dispatch_group(args, show)
    elif args.area == "ssh":
        if args.action == "show":
            show(ssh.status(), print_ssh)
        elif args.action == "set":
            show(ssh.set_settings(switch(args.enabled), switch(args.passwords)), print_ssh)
        else:
            ssh.refresh()
    elif args.area == "look":
        dispatch_look(args, show)
    elif args.area == "sharing":
        dispatch_sharing(args, show)
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
        show(health.health_report(time.time()), print_health)
    elif args.area == "setup":
        dispatch_setup(args, show)
    elif args.area == "job":
        dispatch_job(args, show)
    else:
        dispatch_pool(args, show)


def dispatch_setup(args, show):
    if args.action == "status":
        show(system.setup_status(), print_setup)
    elif args.action == "done":
        system.finish_setup(args.skipped)
        print("Setup skipped" if args.skipped else "Setup finished")
    elif args.action == "reset":
        with contextlib.suppress(FileNotFoundError):
            os.remove(system.SETUP_FILE)
        print("Setup opens at the next admin login")


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
        names = [args.name] if args.name else [p["name"] for p in pools.list_pools()]
        if args.json and args.name:
            show(pools.engine_for(args.name).pool_status(args.name), None)
        for name in [] if args.json else names:
            if pools.find_pool(name)["engine"] == "zfs":
                print(run("zpool", "status", "-P", name).stdout, end="")
            else:
                # An array's own report, which knows its disks, spares and any rebuild
                print(run("mdadm", "--detail", pools.engine_for(name).array_device(name)).stdout, end="")
    elif args.action == "details":
        details = pools.engine_for(args.name).pool_details(args.name)
        found = usage.history(args.name)
        details.update(history=found, projection=usage.projection(found["daily"] or found["hourly"]),
                       engine=pools.find_pool(args.name)["engine"])
        details.setdefault("filesystem", None)
        show(details, print_details)
    elif args.action == "layouts":
        found = disks.list_disks()
        sizes = [disks.find_disk(found, wanted)["size"] for wanted in args.disks]
        show(pools.engine_named(args.engine).layouts(sizes), print_layouts)
    elif args.action == "create":
        job = pools.engine_named(args.engine).create_pool(args.name, args.layout, args.disks, args.confirm,
                                                          args.wipe, args.width, args.spares,
                                                          filesystem=args.filesystem)
        print(f"Created pool {args.name}")
        # An Unraid-style pool syncs its parity for the first time
        started(job)
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
        # The engine that found it knows how to import it; otherwise the one named
        engine = pools.ENGINES[found[0]["engine"]] if found else pools.engine_named(args.engine)
        engine.import_pool(args.pool, args.readonly, args.force)
        print(f"Imported {args.pool}" + (" read-only" if args.readonly else ""))
        for pool in config.foreign_config_pools():
            print(f"{pool} has saved settings: bring them back with fnas config restore {pool}")
    elif args.action == "export":
        pools.engine_for(args.name).export_pool(args.name)
        print(f"Exported {args.name}")
    elif args.action == "scrub":
        started(pools.engine_for(args.name).scrub_pool(args.name))
    elif args.action == "repair":
        started(pools.engine_for(args.name).repair_pool(args.name, args.confirm))
    elif args.action == "array-checks":
        if args.when == "stopped":
            mdadm.mdcheck_stopped()
        else:
            mdadm.mdcheck_starting(resuming=args.when == "resuming")
    elif args.action == "watch-events":
        mdadm.watch_events()
    elif args.action == "schedules":
        show(storage_checks.status(), print_schedules)
    elif args.action == "schedule":
        storage_checks.set_schedule(args.engine, None if args.checks is None else args.checks == "on",
                                    args.every, args.week, args.day, args.hour,
                                    None if args.sync is None else args.sync == "on", args.sync_hour, args.spin_down)
        print(f"{pools.engine_title(args.engine)}: " + (
            f"checks run by themselves {storage_checks.describe(args.engine)}"
            if storage_checks.describe(args.engine) else "automatic checks are off"))
    elif args.action == "apply-schedules":
        for problem in storage_checks.apply():
            print(f"fnas: {problem}", file=sys.stderr)
    elif args.action == "scrub-all":
        jobs_started, skipped = storage_checks.scrub_all(args.engine, args.scheduled)
        for job in jobs_started:
            started(job)
        for reason in skipped:
            print(f"Skipped {reason}")
        if not jobs_started and not skipped:
            print(f"No {pools.engine_title(args.engine)} pools to check")
    elif args.action == "sync":
        engine = pools.engine_for(args.name)
        if not hasattr(engine, "sync_pool"):
            raise FnasError(f"{args.name} keeps its redundancy up to date as it writes: there is nothing to sync")
        started(engine.sync_pool(args.name, args.force))
    elif args.action == "sync-all":
        jobs_started, skipped = storage_checks.sync_all(args.engine)
        for job in jobs_started:
            started(job)
        for reason in skipped:
            print(f"Skipped {reason}")


def dispatch_job(args, show):
    if args.action == "list":
        show(jobs.list_jobs(), print_jobs)
    elif args.action == "show":
        show(jobs.show(args.id), lambda job: print_jobs([job]))
    elif args.action == "run":
        jobs.run_job(args.id)


def dispatch_user(args, show):
    dispatch_user_action(args, show)
    if args.action in ("create", "set", "remove"):
        ssh.sync_members()
        # A person who is no longer set to Files over SFTP loses their view as
        # well as their place in the group
        sftp.sync()


def dispatch_user_action(args, show):
    if args.action == "list":
        show(users.list_users(), print_users)
    elif args.action == "shares":
        users.find_user(args.name)
        show(shares.shares_of(args.name), lambda found: print("\n".join(
            f"{s['name']}: {s['level']} ({s['through']})" for s in found) or "No shares"))
    elif args.action == "create":
        users.create_user(args.name, args.full_name, args.kind, args.admin, names(args.groups), read_secret(True))
        print(f"Added {args.name}")
    elif args.action == "set":
        users.change_user(args.name, args.full_name, args.kind, switch(args.admin),
                          None if args.groups is None else names(args.groups), switch(args.enabled))
        print(f"Updated {args.name}")
    elif args.action in ("password", "smb-password"):
        users.set_password(args.name, read_secret(True))
        print(f"Set the password for {args.name}")
    elif args.action == "keys":
        if args.set_stdin:
            ssh.set_keys(args.name, sys.stdin.read())
            print(f"Saved SSH keys for {args.name}")
        else:
            show(ssh.list_keys(args.name), lambda found: print("\n".join(
                f"{key['type']} {key['fingerprint']} {key['comment']}" for key in found) or "No SSH keys"))
    elif args.action == "remove":
        users.find_user(args.name)
        lost = [s["name"] for s in shares.shares_of(args.name) if s["through"] == "own"]
        users.remove_user(args.name, args.keep_home, args.confirm, lost)
        shares.forget("users", args.name)
        look.forget_look(args.name)
        print(f"Removed {args.name}" + (", keeping the home folder" if args.keep_home else ""))


def print_look(chosen):
    print(f"{chosen['user']}: {chosen['mode']}, {chosen['palette']} theme")


def dispatch_look(args, show):
    if args.action == "show":
        show(look.show(args.user), print_look)
    else:
        show(look.set_look(args.user, args.mode, args.palette), print_look)


def print_ssh(status):
    print("SSH is " + ("on" if status["enabled"] else "off") + (", running" if status["running"] else ", not running"))
    print("Passwords: " + ("allowed" if status["passwords"] else "off, keys only"))
    print("May log in: " + (", ".join(status["allowed"]) or "nobody"))


def dispatch_group(args, show):
    if args.action == "list":
        show(users.list_groups(include_system=args.system), print_groups)
    elif args.action == "create":
        users.create_group(args.name, names(args.members))
        print(f"Added group {args.name}")
    elif args.action == "members":
        users.set_members(args.name, names(args.members))
        print(f"Updated {args.name}")
    elif args.action == "rename":
        users.rename_group(args.name, args.new)
        shares.rename("groups", args.name, args.new)
        print(f"Renamed {args.name} to {args.new}")
    elif args.action == "remove":
        users.remove_group(args.name)
        shares.forget("groups", args.name)
        print(f"Removed group {args.name}")


def dispatch_sharing(args, show):
    if args.action == "show":
        show(shares.sharing_settings(), print_sharing)
    elif args.action == "smb":
        show(shares.set_sharing("smb", {
            "workgroup": args.workgroup, "description": args.description, "min_protocol": args.min_protocol,
            "encryption": args.encryption, "windows_discovery": switch(args.windows_discovery),
            "mac_discovery": switch(args.mac_discovery)}), print_sharing)
    elif args.action == "nfs":
        show(shares.set_sharing("nfs", {"v3": switch(args.v3), "threads": args.threads,
                                        "fixed_ports": switch(args.fixed_ports)}), print_sharing)


def dispatch_share(args, show):
    if args.action == "list":
        show(shares.list_shares(), print_shares)
    elif args.action == "roots":
        users.find_user(args.user)
        show(shares.roots_of(args.user), lambda found: print("\n".join(
            f"{root['share']}: {root['level']} at {root['path']}" for root in found) or
            f"{args.user} can't use any shares"))
    elif args.action == "create":
        shares.create_share(args.name, args.pool, args.everyone, access_from(args), args.nfs, args.smb,
                            args.webdav, args.sftp)
        print(f"Created share {args.name}")
    elif args.action == "access":
        shares.set_access(args.name, args.everyone, access_from(args))
        print(f"{args.name} is now for {describe_access(shares.find_share(args.name))}")
    elif args.action == "smb":
        shares.set_smb_options(args.name, {
            "readonly": switch(args.readonly), "hidden": switch(args.hidden), "guest": switch(args.guest),
            "recycle": switch(args.recycle), "recycle_days": args.recycle_days,
            "timemachine": switch(args.timemachine), "timemachine_size": args.timemachine_size,
            "previous_versions": switch(args.previous_versions)})
        print(f"Updated SMB settings for {args.name}")
    elif args.action == "nfs":
        clients = None if args.client is None else pairs(args.client, shares.NFS_ACCESS)
        shares.set_nfs_options(args.name, clients, args.home_network, switch(args.readonly), args.mapping,
                               switch(args.async_writes))
        print(f"Updated NFS settings for {args.name}")
    elif args.action == "set":
        shares.set_share(args.name, args.nfs, args.smb, args.webdav, args.sftp)
        print(f"Updated share {args.name}")
    elif args.action == "refresh":
        shares.migrate()
        shares.write_config()
    elif args.action == "services":
        show(services.protocols(), print_protocols)
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
        if args.area not in ("check", "health", "system", "job", "setup", "look") and \
                args.action not in READ_ONLY_ACTIONS:
            config.after_change()
    except FnasError as error:
        print(f"fnas: {error}", file=sys.stderr)
        return 1
    return 0
