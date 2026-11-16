"""The LAN interface's address: DHCP or a fixed one, with a way back.

docs/fellowship-nas-network-rollback-plan.md (phase 6 of the account
separation): a NAS admin changes the address from the desktop, and the change
rolls back by itself unless it is confirmed from the new address. The rollback
is a systemd unit, so it happens even if the web server or the helper died in
the change.

The interface keeps ifupdown, as the installer set it up, and its IPv6 stanza
is preserved exactly. The LAN subnet follows the change through set_lan, so
NFS exports and share access stay with the live network, both directions.
"""
import contextlib
import ipaddress
import os
import re
import time

from . import alerts, shares
from .common import FnasError, STATE_DIR, locked, read_json, run, write_file, write_json


INTERFACES_DIR = "/etc/network/interfaces.d"
RESOLV = "/etc/resolv.conf"
# Root's pinned interface, for a machine with several ports
CONFIG_FILE = "/etc/fellowshipos/nas-network.json"
STATE = os.path.join(STATE_DIR, "network-rollback")
PENDING = os.path.join(STATE, "pending.json")
BACKUP = os.path.join(STATE, "backup.json")
LOCK = os.path.join(STATE, "lock")
TIMER = "fellowship-network-rollback.timer"
CONSOLE = "/usr/local/sbin/fellowship-nas-issue"
WINDOW = 120
LEASE_WAIT = 30
IFACE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")


def interface_file(name):
    return os.path.join(INTERFACES_DIR, name)


def read_interface(name):
    try:
        with open(interface_file(name)) as file:
            return file.read()
    except FileNotFoundError:
        return ""


def addresses():
    """Every interface's IPv4 address and prefix, as (name, address, prefix)."""
    found = []
    for line in run("ip", "-4", "-o", "addr", "show").stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or "inet" not in fields:
            continue
        address, _, prefix = fields[fields.index("inet") + 1].partition("/")
        found.append((fields[1], address, int(prefix)))
    return found


def current_address(name):
    for interface, address, prefix in addresses():
        if interface == name:
            return address, prefix
    return None, None


def lan_interface():
    """The port the LAN is on: root's pin, else the default route's port (the
    convention first boot uses), else the port in the configured LAN subnet."""
    saved = read_json(CONFIG_FILE, {}) or {}
    if saved.get("interface"):
        name = saved["interface"]
        if run("ip", "link", "show", name, check=False).returncode != 0:
            raise FnasError(f"the pinned network interface {name} doesn't exist")
        return name, "override"
    best = None
    for line in run("ip", "-4", "route", "show", "default").stdout.splitlines():
        fields = line.split()
        if "dev" not in fields:
            continue
        metric = int(fields[fields.index("metric") + 1]) if "metric" in fields else 0
        if best is None or metric < best[0]:
            best = (metric, fields[fields.index("dev") + 1])
    if best:
        return best[1], "default-route"
    try:
        lan = ipaddress.ip_network(shares.lan_subnet())
    except FnasError:
        lan = None
    if lan:
        for name, address, prefix in addresses():
            if ipaddress.ip_network(f"{address}/{prefix}", strict=False) == lan:
                return name, "lan-subnet"
    raise FnasError("no LAN interface found: no default route, and no interface in the LAN subnet. "
                    "Root can pin one with: fnas network interface NAME")


def set_interface(name):
    if not IFACE_NAME.match(name):
        raise FnasError(f"not an interface name: {name}")
    if run("ip", "link", "show", name, check=False).returncode != 0:
        raise FnasError(f"no such interface: {name}")
    write_json(CONFIG_FILE, {"interface": name})
    return name


def check_static(address, gateway, dns):
    """A private address with a usable prefix, a gateway inside its subnet,
    and DNS servers that are addresses."""
    try:
        net = ipaddress.IPv4Network(address, strict=False)
        addr = ipaddress.IPv4Address(address.split("/")[0])
    except ValueError:
        raise FnasError(f"not an address and prefix: {address} (expected something like 192.168.1.50/24)") from None
    if not addr.is_private:
        raise FnasError(f"{addr} isn't a private address")
    if not 8 <= net.prefixlen <= 30:
        raise FnasError(f"{net} isn't a LAN subnet between /8 and /30")
    if addr in (net.network_address, net.broadcast_address):
        raise FnasError(f"{addr} is the network or broadcast address of {net}, not an address for the NAS")
    try:
        gw = ipaddress.IPv4Address(gateway)
    except ValueError:
        raise FnasError(f"not a gateway address: {gateway}") from None
    if gw not in net or gw == addr or gw in (net.network_address, net.broadcast_address):
        raise FnasError(f"the gateway {gw} must be a different address inside {net}")
    servers = []
    for value in dns:
        try:
            server = ipaddress.IPv4Address(value)
        except ValueError:
            raise FnasError(f"not a DNS server address: {value}") from None
        if server.is_multicast or server.is_unspecified or server.is_reserved:
            raise FnasError(f"{server} can't be a DNS server")
        servers.append(str(server))
    if not servers:
        raise FnasError("give at least one DNS server")
    return net, addr, gw, servers


def make_config(name, mode, address=None, gateway=None):
    """The ifupdown file for the change, keeping the IPv6 stanza exactly,
    its options included."""
    kept, inside = [], False
    for line in read_interface(name).splitlines():
        if re.match(rf"^iface\s+{re.escape(name)}\s+inet6\b", line):
            inside = True
        elif inside and re.match(r"^(iface|auto|allow-hotplug|allow-auto|mapping|source)\b", line):
            inside = False
        if inside:
            kept.append(line)
    lines = ["# Written by fnas network apply", f"allow-hotplug {name}"]
    if mode == "dhcp":
        lines.append(f"iface {name} inet dhcp")
    else:
        lines += [f"iface {name} inet static", f"    address {address}", f"    gateway {gateway}"]
    return "\n".join(lines + kept) + "\n"


def retire_addresses(name):
    """Release this interface's DHCP lease and remove its IPv4 addresses, so
    the next method starts from a clean port. Scoped to this interface alone:
    other ports keep their addresses, and IPv6 is never touched.

    Without this, the boot dhclient survives a method change (ifdown reads the
    rewritten stanza, so it never releases the old method's client), and its
    lease lingers beside the new address or blocks the new client's flush: a
    static change kept the old lease reachable, a DHCP change reported the
    leftover static address as its lease, and a rollback left the abandoned
    address live. dhclient -r kills the client but keeps the address, so the
    flush is what actually removes it."""
    run("dhclient", "-r", "-pf", f"/run/dhclient.{name}.pid", name, check=False)
    run("ip", "-4", "addr", "flush", "dev", name, check=False)


def restart_interface(name):
    retire_addresses(name)
    run("ifdown", name, check=False)
    run("ifup", name)


def wait_for_address(name):
    for _ in range(LEASE_WAIT):
        address, prefix = current_address(name)
        if address:
            return address, prefix
        time.sleep(1)
    return None, None


def follow_lan(net):
    """Keep the subnet shares are limited to with the live network."""
    if net is None:
        return
    try:
        if shares.lan_subnet() == str(net):
            return
    except FnasError:
        pass
    shares.set_lan(str(net))


def put_back(backup, name):
    """The saved files, and the interface up on them."""
    if backup.get("interface") is not None:
        write_file(interface_file(name), backup["interface"])
    else:
        with contextlib.suppress(FileNotFoundError):
            os.remove(interface_file(name))
    if backup.get("lan") is not None:
        shares.set_lan(backup["lan"].strip())
    else:
        with contextlib.suppress(FileNotFoundError):
            os.remove(shares.LAN_FILE)
    if backup.get("resolv") is not None:
        write_resolv(backup["resolv"])
    # Best effort: the saved files are back either way, and the state must be
    # cleared even if the interface won't come up on them
    retire_addresses(name)
    run("ifdown", name, check=False)
    run("ifup", name, check=False)


def clear_state():
    for path in (PENDING, BACKUP):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


def refresh_console():
    if os.path.exists(CONSOLE):
        run(CONSOLE, check=False)


def read_saved(path):
    try:
        with open(path) as file:
            return file.read()
    except FileNotFoundError:
        return None


def write_resolv(content):
    # In place, not a replaced file: resolv.conf can be a bind mount (a
    # container) or a symlink, and dhclient writes it this way too
    with open(RESOLV, "w") as file:
        file.write(content)


def apply(mode, address=None, gateway=None, dns=None):
    """Change the LAN interface's address, with 120 seconds to confirm it from
    the new address before the rollback unit puts the old settings back."""
    if mode not in ("dhcp", "static"):
        raise FnasError(f"not a way to set the address: {mode}")
    net = addr = None
    servers = []
    if mode == "static":
        net, addr, _, servers = check_static(address, gateway, dns)
    name, _ = lan_interface()
    with locked(LOCK):
        if os.path.exists(PENDING):
            raise FnasError("a network change is already waiting to be confirmed")
        backup = {"interface": read_interface(name), "lan": read_saved(shares.LAN_FILE),
                  "resolv": read_saved(RESOLV)}
        write_json(BACKUP, backup, mode=0o600)
        deadline = int(time.time()) + WINDOW
        pending = {"interface": name, "mode": mode, "address": str(addr) if addr else None,
                   "prefix": net.prefixlen if net else None, "deadline": deadline}
        write_json(PENDING, pending, mode=0o600)
        try:
            run("systemctl", "start", TIMER)
            write_file(interface_file(name), make_config(name, mode, address, gateway))
            if mode == "static":
                write_resolv("".join(f"nameserver {server}\n" for server in servers))
            restart_interface(name)
            if mode == "dhcp":
                found, prefix = wait_for_address(name)
                if not found:
                    raise FnasError(f"no DHCP lease arrived within {LEASE_WAIT} seconds; "
                                    "the previous settings are back")
                addr = ipaddress.IPv4Address(found)
                net = ipaddress.ip_network(f"{found}/{prefix}", strict=False)
                pending.update(address=found, prefix=prefix)
                write_json(PENDING, pending, mode=0o600)
            follow_lan(net)
        except BaseException:
            run("systemctl", "stop", TIMER, check=False)
            put_back(backup, name)
            clear_state()
            raise
    refresh_console()
    return {"mode": mode, "address": str(addr), "deadline": deadline}


def confirm():
    """Keep the change. The helper has already proved this arrived at the new
    address; here it only has to be a change that is waiting."""
    with locked(LOCK):
        if not os.path.exists(PENDING):
            raise FnasError("there is no network change waiting to be confirmed")
        run("systemctl", "stop", TIMER, check=False)
        clear_state()
    refresh_console()


def rollback():
    """The rollback unit's work: put the saved settings back. With nothing
    pending there is nothing to do, however often it runs."""
    with locked(LOCK):
        pending = read_json(PENDING)
        if not pending:
            return False
        put_back(read_json(BACKUP, {}) or {}, pending["interface"])
        run("systemctl", "stop", TIMER, check=False)
        clear_state()
    alerts.send_alert("medium", "Network change rolled back",
                      "The network change wasn't confirmed in time, so the previous settings are back.")
    refresh_console()
    return True


def show():
    try:
        name, how = lan_interface()
    except FnasError:
        name, how = None, None
    mode = address = prefix = gateway = None
    if name:
        config = read_interface(name)
        if re.search(rf"^iface\s+{re.escape(name)}\s+inet\s+dhcp\b", config, re.M):
            mode = "dhcp"
        elif re.search(rf"^iface\s+{re.escape(name)}\s+inet\s+static\b", config, re.M):
            mode = "static"
        address, prefix = current_address(name)
        route = run("ip", "-4", "route", "show", "default", "dev", name).stdout.split()
        if "via" in route:
            gateway = route[route.index("via") + 1]
    dns = []
    for line in (read_saved(RESOLV) or "").splitlines():
        fields = line.split()
        if fields and fields[0] == "nameserver":
            dns.append(fields[1])
    try:
        lan = shares.lan_subnet()
    except FnasError:
        lan = None
    pending = read_json(PENDING)
    return {"interface": name, "chosen": how, "mode": mode, "address": address, "prefix": prefix,
            "gateway": gateway, "dns": dns, "lan": lan,
            "pending": {"mode": pending["mode"], "address": pending.get("address"),
                        "deadline": pending["deadline"]} if pending else None}
