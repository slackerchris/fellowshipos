"""Unit tests for fnas. Nothing here touches a real disk: every external
command goes through a fake, and a test fails if fnas runs one it didn't expect."""
import contextlib
import importlib
import io
import ipaddress
import json
import os
import pkgutil
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../packages/fellowship-nas-tools"))
import fellowship_nas  # noqa: E402

MODULES = [importlib.import_module(info.name)
           for info in pkgutil.walk_packages(fellowship_nas.__path__, "fellowship_nas.")]


class Package:
    """Every name in the package, wherever it lives, as if fnas were one file.

    Engines share function names by design, so a name they disagree about
    resolves to ZFS, the engine fnas started with. A test for another engine
    reaches for that engine's module by name instead."""

    def __getattr__(self, name):
        for module in MODULES:
            if module.__name__.startswith("fellowship_nas.engines."):
                continue
            if name in vars(module):
                return vars(module)[name]
        for module in MODULES:
            if module.__name__ == "fellowship_nas.engines.zfs" and name in vars(module):
                return vars(module)[name]
        raise AttributeError(name)


fnas = Package()
zfs = importlib.import_module("fellowship_nas.engines.zfs")
mdadm = importlib.import_module("fellowship_nas.engines.mdadm")
mergerfs = importlib.import_module("fellowship_nas.engines.mergerfs")
fnas_checks = importlib.import_module("fellowship_nas.storage_checks")


class patch:
    """mock.patch.object for a name in every module that has it, since modules
    import shared helpers like run by name."""

    def __init__(self, name, new=mock.DEFAULT, **kwargs):
        if new is mock.DEFAULT:
            new = mock.MagicMock(**kwargs)
        self.new = new
        self.patchers = [mock.patch.object(module, name, new) for module in MODULES if name in vars(module)]
        assert self.patchers, name

    def start(self):
        for patcher in self.patchers:
            patcher.start()
        return self.new

    def stop(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *error):
        self.stop()


GB = 10 ** 9


def disk(name, children=(), fstype=None, mounts=(None,), serial=None):
    return {"name": name, "path": f"/dev/{name}", "type": "disk", "size": 100 * GB,
            "model": "Test", "serial": serial or name.upper(), "fstype": fstype,
            "mountpoints": list(mounts), "children": list(children)}


def part(name, fstype=None, mounts=(None,), label=None):
    return {"name": name, "path": f"/dev/{name}", "type": "part", "size": GB, "model": None,
            "serial": None, "fstype": fstype, "mountpoints": list(mounts), "partlabel": label}


OS_DISK = disk("vda", [part("vda1", "vfat", ["/boot/efi"]), part("vda2", "ext4", ["/"])])
EMPTY = [disk(n) for n in ("vdb", "vdc", "vdd", "vde")]


class Fake:
    """Answers the read-only commands and records everything else."""

    def __init__(self, devices, pools="", imports="", import_rc=1, mounted_by="default"):
        self.devices, self.pools, self.imports, self.import_rc = devices, pools, imports, import_rc
        # Where an imported pool's mountpoint comes from: "default" (/NAME) or "local"
        self.mounted_by = mounted_by
        self.calls = []

    def __call__(self, *command, check=True, stdin=None, timeout=None):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[0] == "lsblk":
            ok.stdout = json.dumps({"blockdevices": self.devices})
        elif command[:2] == ("zpool", "list") and "-vHPL" in command:
            ok.stdout = self.pools
        elif command[:2] == ("zpool", "list"):
            pass
        elif command[:5] == ("zfs", "list", "-Hp", "-d", "0"):
            pass  # the usable room of each pool, read alongside zpool list
        elif command == ("zpool", "import", "-d", fnas.BY_ID_DIR):
            ok.stdout, ok.returncode = self.imports, self.import_rc
            ok.stderr = "" if self.imports else "no pools available to import"
        elif command[:2] == ("zfs", "get") and "value,source" in command:
            ok.stdout = f"/{command[-1]}\t{self.mounted_by}\n"
        else:
            self.calls.append(command)
        return ok


def links(partitions=False):
    names = "vda vdb vdc vdd vde vdf vdg vdh vdi".split()
    if partitions:
        return {f"/dev/{n}{i}": f"/dev/disk/by-id/virtio-{n.upper()}-part{i}" for n in names for i in (1, 2, 3)}
    return {f"/dev/{n}": f"/dev/disk/by-id/virtio-{n.upper()}" for n in names}


# What the FellowshipOS NAS installer leaves: EFI, LVM for the system, and a data partition
INSTALLED_DISK = disk("vda", [part("vda1", "vfat", ["/boot/efi"]), part("vda2", "LVM2_member", [None], "fellowship-system"),
                              part("vda3", None, [None], "fellowship-data")])


class Base(unittest.TestCase):
    def setUp(self):
        # Nothing here may write the real NFS or Samba configuration, and pools
        # are checked against an empty root, not this machine's folders
        for name, value in (("EXPORTS_DIR", "/nonexistent"), ("SMB_DIR", "/nonexistent"),
                            ("ROOT", "/nonexistent"), ("SMB_CONF", "/nonexistent/smb.conf"),
                            ("NFS_CONF", "/nonexistent/nfs.conf.d/fellowship.conf"),
                            ("NFS_SYSCTL", "/nonexistent/sysctl.d/fellowship.conf"),
                            ("SHARING_FILE", "/nonexistent/nas-sharing.json")):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use(self, fake):
        for patcher in (patch("run", fake),
                        patch("by_id_links", links),
                        patch("unavailable", lambda: ""),
                        # These tests are the ZFS world; the array and Unraid-style engines
                        # have their own classes, and would ask the fake about theirs otherwise
                        mock.patch.object(mdadm, "unavailable", lambda: "mdadm isn't installed"),
                        mock.patch.object(mergerfs, "unavailable", lambda: "mergerfs isn't installed"),
                        patch("bays", dict)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake


class Disks(Base):
    def state(self, device, pools=""):
        self.use(Fake([device], pools))
        return fnas.list_disks()[0]["state"]

    def test_os_disk(self):
        self.assertEqual(self.state(OS_DISK), "os")

    def test_empty(self):
        self.assertEqual(self.state(disk("vdb")), "empty")

    def test_partitions_without_filesystem(self):
        self.assertEqual(self.state(disk("vdb", [part("vdb1")])), "has-data")

    def test_zfs_label(self):
        device = disk("vdb", [part("vdb1", "zfs_member")])
        self.assertEqual(self.state(device), "has-data")

    def test_mounted_is_in_use(self):
        device = disk("vdb", [part("vdb1", "ext4", ["/mnt/old"])])
        self.assertEqual(self.state(device), "in-use")

    def test_imported_pool_member_is_in_use(self):
        device = disk("vdb", [part("vdb1", "zfs_member")])
        pools = "tank\t1\t-\n\tmirror-0\t1\t-\n\t/dev/vdb1\t1\t-\n"
        self.assertEqual(self.state(device, pools), "in-use")

    def test_offers_the_rest_of_the_system_disk(self):
        INSTALLED_DISK["children"][1]["children"] = [
            {"name": "fellowship-root", "path": "/dev/mapper/fellowship-root", "type": "lvm", "size": GB,
             "fstype": "ext4", "mountpoints": ["/"]}]
        self.use(Fake([INSTALLED_DISK, disk("vdb")]))
        disks = fnas.list_disks()
        self.assertEqual([(d["path"], d["state"], d["kind"]) for d in disks],
                         [("/dev/vda", "os", "disk"), ("/dev/vda3", "empty", "partition"), ("/dev/vdb", "empty", "disk")])
        self.assertEqual(disks[1]["by_id"], "/dev/disk/by-id/virtio-VDA-part3")
        self.assertEqual(disks[1]["serial"], "")

    def test_skips_empty_readers_zvols_and_floppies(self):
        reader = disk("sdz")
        reader["size"] = 0
        self.use(Fake([reader, disk("zd0"), disk("fd0"), disk("vdb")]))
        self.assertEqual([d["path"] for d in fnas.list_disks()], ["/dev/vdb"])


class Create(Base):
    def create(self, fake, *args, **kwargs):
        self.use(fake)
        name, layout, wanted = args
        fnas.create_pool(name, layout, wanted, kwargs.get("confirm", fnas.confirmation_for(len(wanted))),
                         kwargs.get("wipe", False))
        return fake.calls

    def refused(self, message, *args, **kwargs):
        fake = Fake(kwargs.pop("devices", [OS_DISK] + EMPTY), kwargs.pop("pools", ""),
                    kwargs.pop("imports", ""))
        with self.assertRaisesRegex(fnas.FnasError, message):
            self.create(fake, *args, **kwargs)
        self.assertEqual(fake.calls, [], "a refused create must change nothing")

    def test_uses_by_id_paths_and_plan_defaults(self):
        with patch("ROOT", "/mnt"):
            calls = self.create(Fake(EMPTY), "tank", "raidz1", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])
        self.assertEqual(calls[0], (
            "zpool", "create", "-m", "/mnt/tank", "-o", "ashift=12", "-O", "compression=lz4", "-O", "atime=off",
            "-O", "xattr=sa", "-O", "acltype=posixacl", "-O", "dnodesize=auto",
            "-O", "org.debian:periodic-scrub=disable", "tank", "raidz1",
            "/dev/disk/by-id/virtio-VDB", "/dev/disk/by-id/virtio-VDC", "/dev/disk/by-id/virtio-VDD"))
        self.assertEqual(calls[1:], [("zfs", "create", f"tank/{d}") for d in fnas.DEFAULT_DATASETS])

    def test_pool_on_the_rest_of_the_system_disk(self):
        INSTALLED_DISK["children"][1]["children"] = [
            {"name": "fellowship-root", "path": "/dev/mapper/fellowship-root", "type": "lvm", "size": GB,
             "fstype": "ext4", "mountpoints": ["/"]}]
        calls = self.create(Fake([INSTALLED_DISK]), "tank", "single", ["/dev/vda3"])
        create = next(c for c in calls if c[:2] == ("zpool", "create"))
        self.assertEqual(create[-1], "/dev/disk/by-id/virtio-VDA-part3")
        self.assertFalse([c for c in calls if "/dev/vda" in c or "/dev/vda1" in c or "/dev/vda2" in c],
                         "only the data partition may be touched")

    def test_never_passes_force_for_old_data(self):
        device = disk("vdb", [part("vdb1", "zfs_member")])
        calls = self.create(Fake([device]), "tank", "single", ["/dev/vdb"], wipe=True)
        create = next(c for c in calls if c[:2] == ("zpool", "create"))
        self.assertNotIn("-f", create)

    def test_four_disk_mirror_is_two_pairs(self):
        calls = self.create(Fake(EMPTY), "tank", "mirror", [d["path"] for d in EMPTY])
        self.assertEqual([a for a in calls[0] if a == "mirror"], ["mirror", "mirror"])

    def test_accepts_serial(self):
        calls = self.create(Fake(EMPTY), "tank", "single", ["VDB"])
        self.assertIn("/dev/disk/by-id/virtio-VDB", calls[0])

    def test_wipes_before_creating(self):
        device = disk("vdb", [part("vdb1", "zfs_member")])
        calls = self.create(Fake([device]), "tank", "single", ["/dev/vdb"], wipe=True)
        self.assertEqual(calls[:3], [("zpool", "labelclear", "-f", "/dev/vdb1"),
                                     ("wipefs", "-a", "/dev/vdb1"), ("wipefs", "-a", "/dev/vdb")])
        self.assertEqual(calls[3][:2], ("zpool", "create"))

    def test_refuses_os_disk_even_with_wipe(self):
        self.refused("operating system", "tank", "single", ["/dev/vda"], wipe=True)

    def test_refuses_imported_pool_disk_even_with_wipe(self):
        devices = [disk("vdb", [part("vdb1", "zfs_member")])]
        self.refused("in pool live", "tank", "single", ["/dev/vdb"], wipe=True, devices=devices,
                     pools="live\t1\t-\n\t/dev/vdb1\t1\t-\n")

    def test_refuses_old_data_without_wipe(self):
        devices = [disk("vdb", [part("vdb1", "zfs_member")])]
        self.refused("--wipe", "tank", "single", ["/dev/vdb"], devices=devices)

    def test_refuses_wrong_confirmation(self):
        self.refused("erase 2 disks", "tank", "mirror", ["/dev/vdb", "/dev/vdc"], confirm="erase 1 disk")
        self.refused("erase 2 disks", "tank", "mirror", ["/dev/vdb", "/dev/vdc"], confirm="")

    def test_refuses_same_disk_by_two_names(self):
        self.refused("twice", "tank", "mirror", ["/dev/vdb", "VDB"])

    def test_refuses_unknown_disk(self):
        self.refused("no such disk", "tank", "single", ["/dev/nope"])

    def test_refuses_a_name_zfs_reserves(self):
        # "mirror" is a fine pool name on another engine; only ZFS can't have it
        self.refused("word ZFS reserves", "mirror", "mirror", ["/dev/vdb", "/dev/vdc"])
        self.refused("no such disk", "tank", "single", [""])

    def test_refuses_disk_without_stable_name(self):
        self.refused("stable name", "tank", "single", ["/dev/sdq"], devices=[disk("sdq")])

    def test_layout_disk_counts(self):
        self.refused("take 2 to 3", "tank", "mirror", ["/dev/vdb"])
        self.refused("take 3 to 8", "tank", "raidz1", ["/dev/vdb", "/dev/vdc"])
        self.refused("take 4 to 12", "tank", "raidz2", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])
        self.refused("exactly 1", "tank", "single", ["/dev/vdb", "/dev/vdc"])
        self.refused("don't divide", "tank", "mirror", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])

    def test_refuses_name_of_importable_pool(self):
        self.refused("already exists", "tank", "single", ["/dev/vdb"],
                     imports="   pool: tank\n     id: 1\n  state: ONLINE\n")

    def test_refuses_a_name_already_in_use_under_mnt(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "home", "tester"))
            os.mkdir(os.path.join(root, "tank"))
            with patch("ROOT", root):
                self.refused("already in use", "home", "single", ["/dev/vdb"])
                calls = self.create(Fake(EMPTY), "tank", "single", ["/dev/vdb"])
            self.assertEqual(calls[0][:2], ("zpool", "create"))

    def test_refuses_bad_names(self):
        for name in ("Tank", "1tank", "-rf", "a b", "ta;nk", "mirror1", "raidz", "x" * 33, ""):
            self.refused("pool names|reserves", name, "single", ["/dev/vdb"])


class Layouts(unittest.TestCase):
    def options(self, count, size=4 * GB):
        return zfs.layouts([size] * count)

    def best(self, count):
        option = self.options(count)[0]
        self.assertTrue(option["recommended"])
        return option["layout"], option["width"], option["groups"], option["spares"]

    def test_recommendations_for_any_number_of_disks(self):
        self.assertEqual(self.best(1), ("single", 1, 1, 0))
        self.assertEqual(self.best(2), ("mirror", 2, 1, 0))
        self.assertEqual(self.best(3), ("raidz1", 3, 1, 0))
        self.assertEqual(self.best(6), ("raidz2", 6, 1, 0))
        self.assertEqual(self.best(11), ("raidz2", 10, 1, 1))
        self.assertEqual(self.best(12), ("raidz2", 6, 2, 0))
        self.assertEqual(self.best(15), ("raidz2", 7, 2, 1))
        self.assertEqual(self.best(16), ("raidz2", 8, 2, 0))

    def test_only_layouts_that_fit_are_offered(self):
        for count in range(1, 17):
            for option in self.options(count):
                with self.subTest(count=count, option=option):
                    self.assertEqual(option["width"] * option["groups"] + option["spares"], count)
                    low, high = zfs.WIDTHS[option["layout"]]
                    self.assertTrue(low <= option["width"] <= high)
        self.assertEqual({o["layout"] for o in self.options(1)}, {"single"})
        self.assertNotIn("raidz2", {o["layout"] for o in self.options(3)})

    def test_usable_space_and_survival(self):
        option = next(o for o in self.options(16) if o["recommended"])
        self.assertEqual(option["usable"], 2 * 6 * 4 * GB)
        self.assertEqual(option["survives"], 2)
        mirror = next(o for o in self.options(4) if o["layout"] == "mirror" and o["width"] == 2 and not o["spares"])
        self.assertEqual((mirror["usable"], mirror["survives"]), (2 * 4 * GB, 1))

    def test_mirrors_are_pairs(self):
        for count in range(2, 9):
            with self.subTest(count=count):
                self.assertEqual({o["width"] for o in self.options(count) if o["layout"] == "mirror"} - {2}, set())
        self.assertNotIn("mirror", {o["layout"] for o in self.options(3) if not o["spares"]})

    def test_mixed_sizes_group_alike_and_spare_the_largest(self):
        sizes = [4 * GB, 8 * GB, 4 * GB, 8 * GB, 8 * GB]
        groups, spares = zfs.plan("mirror", sizes, 2, 1)
        self.assertEqual([[sizes[i] for i in g] for g in groups], [[4 * GB, 4 * GB], [8 * GB, 8 * GB]])
        self.assertEqual(sizes[spares[0]], 8 * GB)
        option = next(o for o in zfs.layouts(sizes[:4]) if o["layout"] == "raidz1" and o["width"] == 4)
        self.assertEqual(option["unused"], 2 * 4 * GB)

def vdev(name, kind="disk", state="ONLINE", children=(), size=4 * GB, **extra):
    shown = {"name": name, "vdev_type": kind, "state": state, "read_errors": 0, "write_errors": 0,
             "checksum_errors": 0, **extra}
    if kind == "disk":
        shown.update(path=name, phys_space=size)
    if children:
        shown["vdevs"] = {child["name"]: child for child in children}
    return shown


def by_id(name):
    return f"/dev/disk/by-id/virtio-{name.upper()}-part1"


def status_json(groups, spares=(), state="ONLINE", scan=None, expand=None):
    pool = {"name": "tank", "state": state, "vdevs": {"tank": vdev("tank", "root", state, groups)},
            "spares": {s["name"]: s for s in spares}}
    if scan:
        pool["scan_stats"] = scan
    if expand:
        pool["raidz_expand_stats"] = expand
    return json.dumps({"pools": {"tank": pool}})


class PoolFake(Fake):
    """A pool called tank with the given status, and free disks to add."""

    def __init__(self, devices, status, feature="enabled"):
        super().__init__(devices)
        self.status, self.feature = status, feature

    def __call__(self, *command, check=True, stdin=None, timeout=None):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ("zpool", "list") and "-Hp" in command:
            ok.stdout = "tank\t100\t1\t99\tONLINE\toff\n"
        elif command[:3] == ("zpool", "status", "-j"):
            ok.stdout = self.status
        elif command[:2] == ("zpool", "get"):
            ok.stdout = self.feature + "\n"
        elif command[0] == "systemd-run":
            self.calls.append(command)
        else:
            return super().__call__(*command, check=check)
        return ok


RAIDZ2 = [vdev("raidz2-0", "raidz", children=[vdev(by_id(n)) for n in ("vdb", "vdc", "vdd", "vde")])]


class CreateGroups(Base):
    def test_create_with_groups_and_spares(self):
        devices = [disk(f"vd{c}") for c in "bcdefg"]
        fake = self.use(Fake(devices))
        fnas.create_pool("tank", "mirror", [d["path"] for d in devices], "erase 6 disks", False, 2, 2)
        create = fake.calls[0]
        self.assertEqual(create.count("mirror"), 2)
        self.assertEqual(create[create.index("spare"):], ("spare", "/dev/disk/by-id/virtio-VDF",
                                                         "/dev/disk/by-id/virtio-VDG"))

    def sizes_fake(self, stderr):
        devices = [disk(f"vd{c}") for c in "bcd"]
        fake = Fake(devices)
        real = fake.__class__.__call__

        def create(*command, check=True, stdin=None):
            if command[:2] == ("zpool", "create") and "-f" not in command:
                fake.calls.append(command)
                return SimpleNamespace(stdout="", stderr=stderr, returncode=1)
            return real(fake, *command, check=check, stdin=stdin)
        self.use(create)
        return fake, devices

    def test_different_sizes_are_accepted_once_shown(self):
        fake, devices = self.sizes_fake("invalid vdev specification\nuse '-f' to override the following errors:\n"
                                        "raidz contains devices of different sizes\n")
        fnas.create_pool("tank", "raidz1", [d["path"] for d in devices], "erase 3 disks", False)
        self.assertEqual(fake.calls[1][:3], ("zpool", "create", "-f"))

    def test_other_objections_are_never_forced(self):
        fake, devices = self.sizes_fake("invalid vdev specification\nuse '-f' to override the following errors:\n"
                                        "/dev/vdc1 is part of active pool 'other'\n"
                                        "raidz contains devices of different sizes\n")
        with self.assertRaisesRegex(fnas.FnasError, "active pool"):
            fnas.create_pool("tank", "raidz1", [d["path"] for d in devices], "erase 3 disks", False)
        self.assertFalse([c for c in fake.calls if "-f" in c])

    def test_raidz3(self):
        devices = [disk(f"vd{c}") for c in "bcdefgh"]
        fake = self.use(Fake(devices))
        fnas.create_pool("tank", "raidz3", [d["path"] for d in devices], "erase 7 disks", False)
        self.assertEqual(fake.calls[0].count("raidz3"), 1)


class Grow(Base):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("JOBS_DIR", directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pool(self, groups=RAIDZ2, spares=(), devices=None, **kwargs):
        return self.use(PoolFake(devices or [disk(f"vd{c}") for c in "fgh"], status_json(groups, spares, **kwargs)))

    def test_status_reads_groups_spares_and_progress(self):
        scan = {"function": "RESILVER", "state": "SCANNING", "to_examine": 200, "issued": 50,
                "start_time": 5, "end_time": 0, "errors": 0}
        failed = vdev("replacing-1", "replacing", "DEGRADED",
                      [vdev(by_id("vdc"), state="UNAVAIL"), vdev(by_id("vdf"))])
        groups = [vdev("raidz2-0", "raidz", "DEGRADED", [vdev(by_id("vdb")), failed])]
        self.pool(groups, [vdev(by_id("vdg"), state="AVAIL")], state="DEGRADED", scan=scan)
        status = fnas.pool_status("tank")
        self.assertEqual(status["groups"][0]["type"], "raidz2")
        self.assertEqual([d["state"] for d in fnas.leaves(status["groups"][0])], ["ONLINE", "UNAVAIL", "ONLINE"])
        self.assertEqual(status["spares"][0]["state"], "AVAIL")
        self.assertEqual((status["scan"]["function"], status["scan"]["progress"]), ("resilver", 0.25))

    def test_a_lone_disk_is_a_single_group(self):
        self.pool([vdev(by_id("vdb"))])
        self.assertEqual(fnas.pool_status("tank")["groups"][0]["type"], "single")

    def test_adding_a_group_matches_the_others(self):
        fake = self.pool(devices=[disk(f"vd{c}") for c in "fghi"])
        self.assertIsNone(fnas.grow("tank", "group", ["/dev/vdf", "/dev/vdg", "/dev/vdh", "/dev/vdi"],
                                    "erase 4 disks", False))
        self.assertIn(("zpool", "add", "tank", "raidz2", "/dev/disk/by-id/virtio-VDF", "/dev/disk/by-id/virtio-VDG",
                       "/dev/disk/by-id/virtio-VDH", "/dev/disk/by-id/virtio-VDI"), fake.calls)

    def test_group_refusals(self):
        fake = self.pool()
        with self.assertRaisesRegex(fnas.FnasError, "so add 4"):
            fnas.grow("tank", "group", ["/dev/vdf"], "erase 1 disk", False)
        self.pool([vdev(by_id("vdb"))])
        with self.assertRaisesRegex(fnas.FnasError, "add a mirror"):
            fnas.grow("tank", "group", ["/dev/vdf"], "erase 1 disk", False)
        self.assertFalse([c for c in fake.calls if c[0] == "zpool"])

    def test_expanding_a_raidz_group_follows_the_reflow(self):
        fake = self.pool()
        job = fnas.grow("tank", "expand", ["/dev/vdf"], "erase 1 disk", False, "raidz2-0")
        self.assertIn(("zpool", "attach", "tank", "raidz2-0", "/dev/disk/by-id/virtio-VDF"), fake.calls)
        self.assertEqual((job["kind"], job["pool"]), ("expand", "tank"))
        self.assertTrue([c for c in fake.calls if c[0] == "systemd-run" and job["id"] in c[3]])

    def test_expanding_needs_the_feature(self):
        self.use(PoolFake([disk("vdf")], status_json(RAIDZ2), feature="disabled"))
        with self.assertRaisesRegex(fnas.FnasError, "older ZFS"):
            fnas.grow("tank", "expand", ["/dev/vdf"], "erase 1 disk", False, "raidz2-0")

    def test_mirroring_a_lone_disk(self):
        fake = self.pool([vdev(by_id("vdb"))])
        job = fnas.grow("tank", "mirror", ["/dev/vdf"], "erase 1 disk", False, by_id("vdb"))
        self.assertIn(("zpool", "attach", "tank", by_id("vdb"), "/dev/disk/by-id/virtio-VDF"), fake.calls)
        self.assertEqual(job["kind"], "resilver")

    def test_a_smaller_disk_cant_join(self):
        small = disk("vdf")
        small["size"] = GB
        self.pool([vdev(by_id("vdb"), size=100 * GB)], devices=[small])
        with self.assertRaisesRegex(fnas.FnasError, "smaller"):
            fnas.grow("tank", "mirror", ["/dev/vdf"], "erase 1 disk", False, by_id("vdb"))

    def test_spares_need_redundancy_and_size(self):
        self.pool([vdev(by_id("vdb"))])
        with self.assertRaisesRegex(fnas.FnasError, "has none"):
            fnas.grow("tank", "spare", ["/dev/vdf"], "erase 1 disk", False)
        fake = self.pool(RAIDZ2)
        fnas.grow("tank", "spare", ["/dev/vdf"], "erase 1 disk", False)
        self.assertIn(("zpool", "add", "tank", "spare", "/dev/disk/by-id/virtio-VDF"), fake.calls)

    def test_replace_a_missing_disk_by_its_name(self):
        groups = [vdev("raidz2-0", "raidz", "DEGRADED",
                       [vdev(by_id("vdb")), vdev("9876543210", state="UNAVAIL", path="")])]
        groups[0]["vdevs"]["9876543210"]["path"] = ""
        fake = self.pool(groups, state="DEGRADED")
        job = fnas.replace("tank", "9876543210", "/dev/vdf", "erase 1 disk", False)
        self.assertIn(("zpool", "replace", "tank", "9876543210", "/dev/disk/by-id/virtio-VDF"), fake.calls)
        self.assertEqual(job["kind"], "resilver")

    def test_replace_refusals(self):
        fake = self.pool()
        with self.assertRaisesRegex(fnas.FnasError, "isn't a disk"):
            fnas.replace("tank", "/dev/disk/by-id/nope", "/dev/vdf", "erase 1 disk", False)
        with self.assertRaisesRegex(fnas.FnasError, "confirm"):
            fnas.replace("tank", by_id("vdc"), "/dev/vdf", "", False)
        self.assertFalse([c for c in fake.calls if c[0] == "zpool"])

    def test_a_spare_in_use_stays(self):
        self.pool(RAIDZ2, [vdev(by_id("vdg"), state="INUSE")])
        with self.assertRaisesRegex(fnas.FnasError, "standing in"):
            fnas.remove_spare("tank", by_id("vdg"))

    def test_following_a_resilver(self):
        running = {"function": "RESILVER", "state": "SCANNING", "to_examine": 100, "issued": 40}
        self.pool(scan=running)
        job = {"kind": "resilver", "pool": "tank"}
        self.assertEqual(fnas.follow(job), (fnas.RUNNING, 0.4))
        self.pool(scan={**running, "state": "FINISHED"})
        self.assertEqual(fnas.follow(job), (None, 1.0))
        self.pool(scan={**running, "state": "FINISHED"}, state="DEGRADED")
        self.assertIn("still degraded", fnas.follow(job)[0])
        self.pool(scan={"function": "SCRUB", "state": "FINISHED", "errors": 3})
        self.assertIn("3 errors", fnas.follow({"kind": "scrub", "pool": "tank"})[0])


class Jobs(Base):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in (("JOBS_DIR", directory.name), ("POLL", 0)):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = []
        patcher = patch("send_alert", lambda *a: self.sent.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_job_runs_in_its_own_unit(self):
        fake = self.use(Fake([]))
        job = fnas.start("scrub", "Checking tank", pool="tank")
        self.assertEqual(fake.calls, [("systemd-run", "--quiet", "--collect", f"--unit=fellowship-job-{job['id']}",
                                       "--description=Checking tank", "/usr/sbin/fnas", "job", "run", job["id"])])

    def test_run_reports_progress_until_done(self):
        self.use(Fake([]))
        job = fnas.start("expand", "Adding a disk to tank", pool="tank")
        steps = iter([(fnas.RUNNING, 0.5), (None, 1.0)])
        seen = []

        def follow(running):
            seen.append(fnas.load(running["id"])["progress"])
            return next(steps)

        engine = mock.Mock(follow=follow)
        with patch("engine_for", lambda name: engine):
            fnas.run_job(job["id"])
        done = fnas.load(job["id"])
        self.assertEqual(seen, [0.0, 0.5])
        self.assertEqual((done["state"], done["progress"], done["error"]), ("done", 1.0, None))
        self.assertEqual(self.sent[0][0], "info")

    def test_a_failed_job_alerts(self):
        self.use(Fake([]))
        job = fnas.start("scrub", "Checking tank", pool="tank")
        with patch("engine_for", lambda name: mock.Mock(follow=lambda j: ("the scrub found 2 errors", 1.0))):
            fnas.run_job(job["id"])
        self.assertEqual(fnas.load(job["id"])["state"], "failed")
        self.assertEqual(self.sent[0][:2], ("critical", "Checking tank failed"))

    def test_a_job_whose_unit_vanished_failed(self):
        fake = Fake([])
        self.use(lambda *c, **k: SimpleNamespace(stdout="", stderr="", returncode=3)
                 if c[0] == "systemctl" else fake(*c, **k))
        job = fnas.start("scrub", "Checking tank", pool="tank")
        self.assertEqual(fnas.show(job["id"])["error"], "stopped before it finished")

    def test_old_jobs_are_forgotten_and_ids_are_checked(self):
        self.use(Fake([]))
        job = fnas.start("scrub", "Checking tank", pool="tank")
        fnas.save({**job, "state": "done", "ended": 1})
        self.assertEqual(fnas.list_jobs(now=10 * fnas.DAY), [])
        for bad in ("../../etc/passwd", "", "ABC"):
            with self.subTest(bad), self.assertRaisesRegex(fnas.FnasError, "no such job"):
                fnas.load(bad)


class Bays(unittest.TestCase):
    def test_disks_are_found_by_bay(self):
        with tempfile.TemporaryDirectory() as root:
            for slot, device, label in (("Slot 07", "sdb", "7"), ("Slot 08", None, "8")):
                folder = os.path.join(root, "0:0:1:0", slot)
                os.makedirs(os.path.join(folder, "device", "block", device) if device else folder)
                with open(os.path.join(folder, "slot"), "w") as file:
                    file.write(label + "\n")
            with patch("ENCLOSURE_DIR", root):
                found = fnas.bays()
        self.assertEqual(list(found), ["/dev/sdb"])
        self.assertEqual(found["/dev/sdb"]["bay"], "Bay 7")

    def test_no_enclosure_means_no_bays(self):
        with patch("ENCLOSURE_DIR", "/nonexistent"):
            self.assertEqual(fnas.bays(), {})


class GrowSystem(unittest.TestCase):
    def fake(self, lvs):
        calls = []

        def run(*command, check=True, stdin=None):
            calls.append(command)
            if command[0] == "findmnt":
                return SimpleNamespace(stdout="/dev/mapper/fellowship-root\n", stderr="", returncode=0)
            if command[0] == "lvs":
                return SimpleNamespace(stdout=lvs, stderr="", returncode=0 if lvs else 5)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        patcher = patch("run", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_grows_by_whole_extents(self):
        calls = self.fake(f"  fellowship\troot\t{16 << 30}\t{8 << 30}\t{4 << 20}\n")
        self.assertEqual(fnas.system_storage()["room"], 8 << 30)
        fnas.grow_system("2G")
        self.assertIn(("lvextend", "-r", "-l", "+512", "fellowship/root"), calls)
        fnas.grow_system("all")
        self.assertIn(("lvextend", "-r", "-l", "+2048", "fellowship/root"), calls)

    def test_refusals(self):
        self.fake(f"  fellowship\troot\t{16 << 30}\t{8 << 30}\t{4 << 20}\n")
        for amount, message in (("9G", "only"), ("lots", "not an amount"), ("1", "no room")):
            with self.subTest(amount), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.grow_system(amount)
        self.fake("")
        with self.assertRaisesRegex(fnas.FnasError, "isn't on LVM"):
            fnas.grow_system("all")


class Scan(Base):
    OUTPUT = """   pool: tank
     id: 123
  state: ONLINE
 status: The pool can only be accessed in read-only mode on this system. It
\tcannot be accessed in read-write mode because it uses features.
 action: The pool can be imported in read-only mode.
 config:

\ttank        ONLINE
\t  mirror-0  ONLINE
\t    vdb     ONLINE

   pool: old
     id: 456
  state: UNAVAIL
 config:

\told  UNAVAIL
"""

    def test_parses_pools_and_folds_long_lines(self):
        self.use(Fake([], imports=self.OUTPUT, import_rc=0))
        tank, old = zfs.scan_pools()
        self.assertEqual((tank["pool"], tank["id"], tank["state"]), ("tank", "123", "ONLINE"))
        self.assertIn("read-only mode on this system. It cannot be accessed", tank["status"])
        self.assertEqual((old["pool"], old["state"]), ("old", "UNAVAIL"))

    def test_nothing_to_import_is_not_an_error(self):
        self.use(Fake([]))
        self.assertEqual(zfs.scan_pools(), [])

    def test_real_failure_is_reported(self):
        fake = self.use(Fake([]))
        real = fake.__call__

        def broken(*command, check=True):
            result = real(*command, check=check)
            result.stderr = "permission denied"
            return result
        with patch("run", broken):
            with self.assertRaisesRegex(fnas.FnasError, "permission denied"):
                zfs.scan_pools()


class Import(Base):
    IMPORTS = "   pool: tank\n     id: 123\n  state: ONLINE\n"
    IMPORT = ("zpool", "import", "-d", fnas.BY_ID_DIR)

    def setUp(self):
        super().setUp()
        patcher = patch("ROOT", "/mnt")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_moves_a_pool_made_elsewhere_under_mnt(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        fnas.import_pool("tank", False)
        self.assertEqual(fake.calls, [(*self.IMPORT, "-N", "tank"), ("zfs", "set", "mountpoint=/mnt/tank", "tank"),
                                      ("zfs", "mount", "-a"), ("zfs", "set", "org.debian:periodic-scrub=disable", "tank")])

    def test_a_pool_made_here_keeps_its_place(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0, mounted_by="local"))
        fnas.import_pool("tank", False)
        # fnas scrubs it on its own schedule from now on, rather than Debian's cron job
        self.assertEqual(fake.calls, [(*self.IMPORT, "-N", "tank"), ("zfs", "mount", "-a"),
                                      ("zfs", "set", "org.debian:periodic-scrub=disable", "tank")])

    def test_read_only_goes_under_mnt_without_writing(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        fnas.import_pool("tank", True)
        self.assertEqual(fake.calls, [(*self.IMPORT, "-o", "readonly=on", "-N", "tank"), ("zpool", "export", "tank"),
                                      (*self.IMPORT, "-o", "readonly=on", "-N", "-o", "altroot=/mnt", "tank"),
                                      ("zfs", "mount", "-a")])

    def test_by_id(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0, mounted_by="local"))
        fnas.import_pool("123", False)
        self.assertEqual(fake.calls[0], (*self.IMPORT, "-N", "123"))

    def test_only_imports_what_a_scan_found(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        with self.assertRaisesRegex(fnas.FnasError, "no importable pool"):
            fnas.import_pool("-f", False)
        self.assertEqual(fake.calls, [])

    FOREIGN = (IMPORTS + " status: The pool was last accessed by another system.\n"
               " action: The pool can be imported using its name or numeric identifier and\n"
               "\tthe '-f' flag.\n")
    ACTIVE = IMPORTS + " status: The pool is currently imported by another system.\n"

    def test_foreign_pool_needs_force(self):
        fake = self.use(Fake([], imports=self.FOREIGN, import_rc=0))
        with self.assertRaisesRegex(fnas.FnasError, "pass --force"):
            fnas.import_pool("tank", False)
        self.assertEqual(fake.calls, [])

    def test_foreign_pool_with_force(self):
        fake = self.use(Fake([], imports=self.FOREIGN, import_rc=0, mounted_by="local"))
        fnas.import_pool("tank", False, force=True)
        self.assertEqual(fake.calls[0], (*self.IMPORT, "-f", "-N", "tank"))

    def test_never_forces_a_pool_in_use(self):
        fake = self.use(Fake([], imports=self.ACTIVE, import_rc=0))
        with self.assertRaisesRegex(fnas.FnasError, "in use by another system"):
            fnas.import_pool("tank", False, force=True)
        self.assertEqual(fake.calls, [])

    def test_force_is_not_used_when_not_needed(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0, mounted_by="local"))
        fnas.import_pool("tank", False, force=True)
        self.assertEqual(fake.calls[0], (*self.IMPORT, "-N", "tank"))


def volume_line(name, pool="tank", readonly="off", sharenfs="off", mode="-", smb="-", policy="-"):
    dataset = f"{pool}/shares/{name}"
    return f"{dataset}\t/{dataset}\t{readonly}\t{sharenfs}\t{mode}\t{smb}\t{policy}\n"


def share_line(name, mode="shared", sharenfs="off", smb="off", pool="tank", policy="-", readonly="off"):
    """A share whose settings an older fnas kept in properties on its dataset."""
    return volume_line(name, pool, readonly, sharenfs, mode, smb, policy)


class ShareFake(Fake):
    """Adds the pools and datasets that sharing commands read, and makes and
    destroys share datasets as asked."""

    def __init__(self, datasets="", snapshots="", existing=False, config_dir=None,
                 pools="tank\t100\t1\t99\tONLINE\toff\n"):
        super().__init__([])
        self.datasets, self.snapshots, self.existing = datasets, snapshots, existing
        self.config_dir, self.pool_lines = config_dir, pools

    def __call__(self, *command, check=True, stdin=None, timeout=None):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ("zpool", "list") and "-Hp" in command:
            ok.stdout = self.pool_lines
        elif command[:3] == ("zfs", "list", "-H") and "-d" in command:
            ok.stdout = "".join(line + "\n" for line in self.datasets.splitlines()
                                if line.startswith(command[-1] + "/"))
        elif command[:3] == ("zfs", "list", "-H") and "snapshot" in command:
            ok.stdout = self.snapshots
        elif command[:3] == ("zfs", "list", "-H"):
            ok.returncode = 0 if self.existing else 1
        elif command[:2] == ("zfs", "get") and command[-1].endswith("/fellowship-config"):
            ok.stdout, ok.returncode = f"{self.config_dir}\n", 0 if self.config_dir else 1
        elif command[:2] == ("zfs", "get"):
            ok.stdout = "/" + command[-1] + "\n"
        else:
            if command[:3] == ("zfs", "create", "-p"):
                pool, _, name = command[3].split("/")
                self.datasets += volume_line(name, pool)
            elif command[:3] == ("zfs", "destroy", "-r"):
                self.datasets = "".join(line + "\n" for line in self.datasets.splitlines()
                                        if not line.startswith(command[3] + "\t"))
            return super().__call__(*command, check=check)
        return ok


class Sharing(Base):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.smb_dir = os.path.join(self.root, "samba")
        self.config_dir = os.path.join(self.root, "config")
        self.exports_dir = os.path.join(self.root, "exports.d")
        self.nfs_dir = os.path.join(self.root, "nfs.conf.d")
        for folder in (self.smb_dir, self.config_dir, self.exports_dir, self.nfs_dir):
            os.mkdir(folder)
        lan_file = os.path.join(self.root, "nas-lan")
        with open(lan_file, "w") as file:
            file.write("192.168.1.0/24\n")
        self.smb_conf = os.path.join(self.root, "smb.conf")
        with open(self.smb_conf, "w") as file:
            file.write("[global]\n   workgroup = WORKGROUP\n\n[homes]\n   browseable = no\n")
        people = {"tester": SimpleNamespace(pw_uid=1000, pw_gid=1000), "kim": SimpleNamespace(pw_uid=1001, pw_gid=1001)}
        self.chown, self.chmod = mock.Mock(), mock.Mock()
        for patcher in (patch("SMB_DIR", self.smb_dir),
                        patch("SMB_CONF", self.smb_conf),
                        patch("LAN_FILE", lan_file),
                        patch("EXPORTS_DIR", self.exports_dir),
                        patch("EXPORTS_FILE", os.path.join(self.exports_dir, "fellowship.exports")),
                        patch("NFS_CONF", os.path.join(self.nfs_dir, "fellowship.conf")),
                        patch("NFS_SYSCTL", os.path.join(self.nfs_dir, "sysctl.conf")),
                        patch("SHARING_FILE", os.path.join(self.root, "nas-sharing.json")),
                        mock.patch.object(fnas.pwd, "getpwnam", side_effect=lambda n: people[n]),
                        mock.patch.object(fnas.grp, "getgrnam", side_effect=lambda n: {"family": None}[n]),
                        mock.patch.object(fnas.os, "chown", self.chown),
                        mock.patch.object(fnas.os, "chmod", self.chmod)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, datasets="", **kwargs):
        return self.use(ShareFake(datasets, config_dir=self.config_dir, **kwargs))

    def write_settings(self, **shares):
        fnas.write_json(os.path.join(self.config_dir, "shares.json"), {"version": 1, "shares": shares})

    def settings(self):
        return fnas.read_json(os.path.join(self.config_dir, "shares.json"))["shares"]

    def read(self, *path):
        with open(os.path.join(*path)) as file:
            return file.read()

    def smb_file(self, *path):
        return self.read(self.smb_dir, *path)

    def exports(self):
        return [line for line in self.read(self.exports_dir, "fellowship.exports").splitlines()
                if not line.startswith("#")]

    def everyone(self):
        return {"users": {}, "groups": {}}

    def test_everyone_nfs_squashes_to_the_share_account(self):
        fake = self.fake()
        fnas.create_share("media", "tank", True, self.everyone(), True, False)
        self.assertIn(("zfs", "create", "-p", "tank/shares/media"), fake.calls)
        self.assertEqual(self.exports(), ['"/tank/shares/media" 192.168.1.0/24'
                                          '(rw,sync,no_subtree_check,all_squash,anonuid=3000,anongid=3000)'])
        self.assertEqual(self.settings(), {"media": {
            "mode": "everyone", "access": self.everyone(), "nfs": True, "smb": False,
            # A new share is never reachable over HTTPS or SFTP until it is asked for
            "webdav": False, "sftp": False, "policy": fnas.DEFAULT_POLICY,
            "smb_options": fnas.DEFAULT_SMB, "nfs_options": fnas.DEFAULT_NFS}})
        self.assertIn(("chown", "-R", "3000:3000", "/tank/shares/media"), fake.calls)
        self.chmod.assert_called_once_with("/tank/shares/media", 0o2775)
        # ZFS's own sharing stays off, so NFS has one owner
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "set")])

    def test_chosen_users_and_groups_get_acls(self):
        fake = self.fake()
        fnas.create_share("docs", "tank", False, {"users": {"tester": "write", "kim": "read"},
                                                  "groups": {"family": "read"}}, True, True)
        self.assertEqual(self.exports(), ['"/tank/shares/docs" 192.168.1.0/24(rw,sync,no_subtree_check,root_squash)'])
        self.chown.assert_called_once_with("/tank/shares/docs", 0, 0)
        self.chmod.assert_called_once_with("/tank/shares/docs", 0o2770)
        acl = [c for c in fake.calls if c[:3] == ("setfacl", "-R", "-m")][0][3]
        self.assertIn("user:tester:rwX", acl)
        self.assertIn("user:kim:rX", acl)
        self.assertIn("group:family:rX", acl)
        self.assertIn("default:user:tester:rwX", [c for c in fake.calls if c[0] == "find"][0][7])
        docs = self.smb_file("shares", "docs.conf")
        self.assertIn("valid users = kim tester @family\n", docs)
        self.assertIn("write list = tester\n", docs)
        self.assertIn("read only = yes\n", docs)
        self.assertNotIn("force user", docs)

    def test_older_matched_shares_belong_to_their_folder_owner(self):
        self.write_settings(chris={"mode": "matched", "nfs": False, "smb": True})
        self.fake(volume_line("chris"))
        real_stat = os.stat
        stat = lambda path, *args, **kwargs: SimpleNamespace(st_uid=1000) if path == "/tank/shares/chris" \
            else real_stat(path, *args, **kwargs)
        with mock.patch.object(fnas.os, "stat", side_effect=stat), \
                mock.patch.object(fnas.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="tester")):
            [chris] = fnas.list_shares()
        self.assertEqual((chris["mode"], chris["access"]), ("chosen", {"users": {"tester": "write"}, "groups": {}}))

    def test_smb_only_share_is_not_exported(self):
        self.fake()
        fnas.create_share("media", "tank", True, self.everyone(), False, True)
        self.assertEqual(self.exports(), [])
        self.assertIn("[media]\n", self.smb_file("shares", "media.conf"))

    def test_share_refusals(self):
        cases = [
            (("../x", "tank", True, self.everyone(), True, False), "share names"),
            (("a;b", "tank", True, self.everyone(), True, False), "share names"),
            (("Global", "tank", True, self.everyone(), True, False), "share names"),
            (("media", "tank", True, self.everyone(), False, False), "at least one"),
            (("media", "tank", False, self.everyone(), True, False), "say who"),
            (("media", "tank", True, {"users": {"tester": "write"}, "groups": {}}, True, False), "not both"),
            (("media", "tank", False, {"users": {"nobody2": "write"}, "groups": {}}, True, False), "no such user"),
            (("media", "tank", False, {"users": {}, "groups": {"strangers": "read"}}, True, False), "no such group"),
            (("media", "tank", False, {"users": {"tester": "own"}, "groups": {}}, True, False), "read or write"),
            (("media", "other", True, self.everyone(), True, False), "no imported pool"),
        ]
        for args, message in cases:
            fake = self.fake()
            with self.subTest(args), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.create_share(*args)
            self.assertEqual(fake.calls, [])

    def test_read_only_pool_takes_no_new_shares(self):
        fake = self.fake(pools="tank\t100\t1\t99\tONLINE\ton\n")
        with self.assertRaisesRegex(fnas.FnasError, "read-only"):
            fnas.create_share("media", "tank", True, self.everyone(), True, False)
        self.assertEqual(fake.calls, [])

    def test_names_differing_only_by_case_clash(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"))
        with self.assertRaisesRegex(fnas.FnasError, "already exists"):
            fnas.create_share("Media", "tank", True, self.everyone(), True, False)
        self.assertEqual(fake.calls, [])

    def test_never_takes_over_an_existing_dataset(self):
        fake = self.fake(existing=True)
        with self.assertRaisesRegex(fnas.FnasError, "isn't a share"):
            fnas.create_share("media", "tank", True, self.everyone(), True, False)
        self.assertEqual(fake.calls, [])
        self.chown.assert_not_called()

    def test_needs_a_lan_subnet(self):
        fake = self.fake()
        with patch("LAN_FILE", "/nonexistent/nas-lan"):
            with self.assertRaisesRegex(fnas.FnasError, "no LAN subnet"):
                fnas.create_share("media", "tank", True, self.everyone(), True, False)
        self.assertEqual(fake.calls, [])

    def test_smb_needs_its_package(self):
        self.fake()
        with patch("SMB_DIR", "/nonexistent"):
            with self.assertRaisesRegex(fnas.FnasError, "fellowship-nas-smb"):
                fnas.create_share("media", "tank", True, self.everyone(), False, True)

    def test_export_unshares_first_and_shares_again_if_it_fails(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        fake = ShareFake(volume_line("media"), config_dir=self.config_dir)
        seen = []

        def busy(*command, check=True):
            if command[:2] == ("zpool", "export"):
                seen.append(self.exports())
                fake.calls.append(command)
                return SimpleNamespace(stdout="", stderr="pool or dataset is busy", returncode=1)
            return fake(*command, check=check)

        self.use(busy)
        with self.assertRaisesRegex(fnas.FnasError, "disconnect any clients"):
            fnas.export_pool("tank")
        self.assertEqual(seen, [[]])
        self.assertEqual(len(self.exports()), 1)
        self.assertEqual(os.listdir(os.path.join(self.smb_dir, "shares")), ["media.conf"])

    def test_list_needs_both_a_volume_and_its_settings(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": True},
                            gone={"mode": "shared", "nfs": True, "smb": False})
        nested = "tank/shares/media/old\t/tank/shares/media/old\toff\toff\t-\t-\t-\n"
        self.fake(volume_line("media") + volume_line("plain") + nested)
        self.assertEqual(fnas.list_shares(), [{
            "name": "media", "pool": "tank", "engine": "zfs", "snapshots": True, "dataset": "tank/shares/media",
            "path": "/tank/shares/media", "mode": "everyone", "access": self.everyone(), "nfs": True, "smb": True,
            "webdav": False, "sftp": False, "policy": fnas.DEFAULT_POLICY,
            "smb_options": fnas.DEFAULT_SMB, "nfs_options": fnas.DEFAULT_NFS,
            "readonly": False}])

    def test_damaged_settings_are_ignored(self):
        self.write_settings(media={"mode": "evil", "nfs": True},
                            docs={"mode": "chosen", "nfs": "yes", "smb": True,
                                  "access": {"users": {"Bad Name": "write", "kim": "all"}, "groups": {"fam": "read"}},
                                  "policy": {"hourly": -5, "daily": 7, "monthly": "x"},
                                  "smb_options": {"recycle_days": -1, "guest": "yes"},
                                  "nfs_options": {"clients": [{"host": "*", "access": "rw"},
                                                              {"host": "8.8.8.8", "access": "rw"},
                                                              {"host": "10.0.0.5", "access": "ro"}],
                                                  "mapping": "evil"}})
        self.fake(volume_line("media") + volume_line("docs"))
        [docs] = fnas.list_shares()
        self.assertEqual((docs["name"], docs["nfs"], docs["smb"]), ("docs", False, True))
        self.assertEqual(docs["policy"], {"hourly": 24, "daily": 7, "monthly": 6})
        self.assertEqual(docs["access"], {"users": {}, "groups": {"fam": "read"}})
        self.assertEqual((docs["smb_options"]["recycle_days"], docs["smb_options"]["guest"]), (30, False))
        self.assertEqual(docs["nfs_options"]["clients"], [{"host": "10.0.0.5", "access": "ro"}])
        self.assertEqual(docs["nfs_options"]["mapping"], "auto")

    def test_old_settings_move_into_the_pool(self):
        fake = self.fake(share_line("media", sharenfs="rw=@x", smb="on", policy="0,7,1"))
        [media] = fnas.list_shares()
        self.assertEqual((media["mode"], media["nfs"], media["smb"]), ("everyone", True, True))
        fnas.migrate()
        saved = self.settings()["media"]
        self.assertEqual((saved["mode"], saved["policy"]), ("everyone", {"hourly": 0, "daily": 7, "monthly": 1}))
        self.assertIn(("zfs", "set", "sharenfs=off", "tank/shares/media"), fake.calls)
        self.assertIn(("zfs", "inherit", fnas.LEGACY_MODE, "tank/shares/media"), fake.calls)

    def test_smb_files_follow_the_settings(self):
        self.write_settings(media={"mode": "everyone", "nfs": False, "smb": True},
                            chris={"mode": "chosen", "access": {"users": {"tester": "write"}}, "nfs": False, "smb": True},
                            nfsonly={"mode": "everyone", "nfs": True, "smb": False})
        self.fake(volume_line("media") + volume_line("chris") + volume_line("nfsonly"))
        fnas.write_config()
        self.assertEqual(sorted(os.listdir(os.path.join(self.smb_dir, "shares"))),
                         ["chris.conf", "media.conf"])
        self.assertEqual(self.smb_file("shares.conf").count("include = "), 2)
        media = self.smb_file("shares", "media.conf")
        self.assertIn("[media]\npath = /tank/shares/media\n", media)
        self.assertIn("hosts allow = 192.168.1.0/24 127.0.0.1\n", media)
        self.assertIn("force user = nas-share\n", media)
        self.assertIn("shadow:snapdir = .zfs/snapshot\n", media)
        self.assertNotIn("shadow:localtime", media)
        self.assertNotIn("force user", self.smb_file("shares", "chris.conf"))
        self.assertEqual(len(self.exports()), 1)

    def test_smb_options(self):
        self.write_settings(media={"mode": "everyone", "nfs": False, "smb": True})
        self.fake(volume_line("media"))
        fnas.set_smb_options("media", {"hidden": True, "guest": True, "recycle": True, "recycle_days": 14,
                                       "timemachine": True, "timemachine_size": 500, "previous_versions": False})
        media = self.smb_file("shares", "media.conf")
        self.assertIn("browseable = no\n", media)
        self.assertIn("vfs objects = fruit streams_xattr recycle\n", media)
        self.assertIn("recycle:repository = .recycle/%U\n", media)
        self.assertIn("fruit:time machine max size = 500G\n", media)
        self.assertIn("guest ok = yes\nread list = nobody\n", media)
        self.assertNotIn("shadow_copy2", media)
        settings = self.smb_file("global.conf")
        self.assertIn("map to guest = Bad User\n", settings)
        self.assertIn("fruit:metadata = stream\n", settings)
        self.assertEqual(self.settings()["media"]["smb_options"]["recycle_days"], 14)

    def test_guests_and_required_encryption_exclude_each_other(self):
        self.write_settings(media={"mode": "everyone", "nfs": False, "smb": True})
        self.fake(volume_line("media"))
        fnas.set_sharing("smb", {"encryption": "required"})
        with self.assertRaisesRegex(fnas.FnasError, "encryption is required"):
            fnas.set_smb_options("media", {"guest": True})
        fnas.set_sharing("smb", {"encryption": "desired"})
        fnas.set_smb_options("media", {"guest": True})
        with self.assertRaisesRegex(fnas.FnasError, "turn guests off first on media"):
            fnas.set_sharing("smb", {"encryption": "required"})

    def test_guests_only_on_shares_for_everyone(self):
        self.write_settings(docs={"mode": "chosen", "access": {"users": {"tester": "write"}}, "nfs": False, "smb": True})
        self.fake(volume_line("docs"))
        with self.assertRaisesRegex(fnas.FnasError, "for everyone"):
            fnas.set_smb_options("docs", {"guest": True})

    def test_nfs_options(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False})
        self.fake(volume_line("media"))
        fnas.set_nfs_options("media", [("192.168.1.0/24", "ro"), ("192.168.1.20", "rw")], False, None, "none", True)
        self.assertEqual(self.exports(), ['"/tank/shares/media" 192.168.1.0/24(ro,async,no_subtree_check,no_root_squash)'
                                          ' 192.168.1.20(rw,async,no_subtree_check,no_root_squash)'])
        fnas.set_nfs_options("media", None, True, True, "auto", False)
        self.assertEqual(self.exports(), ['"/tank/shares/media" 192.168.1.0/24'
                                          '(ro,sync,no_subtree_check,all_squash,anonuid=3000,anongid=3000)'])
        for host in ("*", "0.0.0.0/0", "8.8.8.8", "example.com", "10.0.0.0/7"):
            with self.subTest(host), self.assertRaises(fnas.FnasError):
                fnas.set_nfs_options("media", [(host, "rw")], False, None, None, None)

    def test_access_change_rewrites_permissions(self):
        self.write_settings(media={"mode": "everyone", "nfs": False, "smb": True,
                                   "smb_options": {"guest": True}})
        fake = self.fake(volume_line("media"))
        fnas.set_access("media", False, {"users": {"kim": "write"}, "groups": {}})
        saved = self.settings()["media"]
        self.assertEqual((saved["mode"], saved["access"]["users"], saved["smb_options"]["guest"]),
                         ("chosen", {"kim": "write"}, False))
        self.assertIn(("setfacl", "-R", "-b", "/tank/shares/media"), fake.calls)

    def test_sharing_settings(self):
        self.fake()
        fnas.set_sharing("smb", {"workgroup": "HOBBITON", "min_protocol": "SMB3", "encryption": "required",
                                 "mac_discovery": False, "windows_discovery": False})
        settings = self.smb_file("global.conf")
        self.assertIn("workgroup = HOBBITON\n", settings)
        self.assertIn("server min protocol = SMB3_00\n", settings)
        self.assertIn("server smb encrypt = required\n", settings)
        self.assertIn("multicast dns register = no\n", settings)
        # Ours comes last in [global], so it wins over Debian's own lines
        conf = self.read(self.smb_conf)
        self.assertIn(f"workgroup = WORKGROUP\ninclude = {self.smb_dir}/global.conf\n\n[homes]", conf)
        fnas.write_config()
        self.assertEqual(self.read(self.smb_conf).count("include ="), 1)
        fnas.set_sharing("nfs", {"v3": True, "threads": 16, "fixed_ports": True})
        nfs = self.read(self.nfs_dir, "fellowship.conf")
        self.assertIn("vers3=y\nthreads=16\n", nfs)
        self.assertIn("[mountd]\nport=20048\n", nfs)
        self.assertIn("fs.nfs.nlm_tcpport = 32768\n", self.read(self.nfs_dir, "sysctl.conf"))
        for protocol, change in (("smb", {"workgroup": "two words"}), ("smb", {"description": "a\nb"}),
                                 ("nfs", {"threads": 0})):
            with self.subTest(change), self.assertRaises(fnas.FnasError):
                fnas.set_sharing(protocol, change)

    def test_read_only_pool_shares_read_only(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        self.fake(volume_line("media"), pools="tank\t100\t1\t99\tONLINE\ton\n")
        fnas.write_config()
        self.assertIn("(ro,", self.exports()[0])
        self.assertIn("read only = yes\n", self.smb_file("shares", "media.conf"))

    def test_reloads_are_asked_for(self):
        fake = self.fake()
        fnas.write_config()
        self.assertIn(("exportfs", "-ra"), fake.calls)
        self.assertIn(("smbcontrol", "smbd", "reload-config"), fake.calls)
        # The NFS server reads its own settings only when it starts
        self.assertIn(("systemctl", "try-restart", "rpc-statd.service", "nfs-server.service"), fake.calls)
        fake.calls.clear()
        fnas.write_config()
        self.assertNotIn(("systemctl", "try-restart", "rpc-statd.service", "nfs-server.service"), fake.calls)

    def test_set_turns_protocols_on_and_off(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        fake = self.fake(volume_line("media"))
        fnas.write_config()
        fnas.set_share("media", "off", "off")
        self.assertEqual((self.settings()["media"]["nfs"], self.settings()["media"]["smb"]), (False, False))
        self.assertEqual(self.exports(), [])
        self.assertEqual(os.listdir(os.path.join(self.smb_dir, "shares")), [])
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "destroy")])

    def test_set_needs_a_change(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        self.fake(volume_line("media"))
        with self.assertRaisesRegex(fnas.FnasError, "nothing to change"):
            fnas.set_share("media", None, None)

    def test_a_share_can_be_shared_over_webdav_alone(self):
        """A share nobody mounts over SMB or NFS but a phone app reaches over
        HTTPS is a real thing, so WebDAV counts as a way to share."""
        self.fake(volume_line("photos"))
        fnas.create_share("photos", "tank", False, {"users": {"kim": "read"}, "groups": {}},
                          False, False, webdav=True)
        self.assertEqual({key: self.settings()["photos"][key] for key in ("nfs", "smb", "webdav")},
                         {"nfs": False, "smb": False, "webdav": True})
        # It is written into what the desktop reads, so the row can show it
        self.assertEqual([root["share"] for root in fnas.roots_of("kim")], ["photos"])
        self.assertTrue(fnas.roots_of("kim")[0]["webdav"])

    def test_a_share_needs_at_least_one_way_in(self):
        self.fake(volume_line("photos"))
        with self.assertRaisesRegex(fnas.FnasError, "at least one of --nfs, --smb, --webdav and --sftp"):
            fnas.create_share("photos", "tank", True, self.everyone(), False, False)

    def test_a_share_change_rebuilds_the_sftp_views(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        self.fake(volume_line("media"))
        seen = []
        sftp = {m.__name__: m for m in MODULES}["fellowship_nas.sftp"]
        with mock.patch.object(sftp, "rebuild", lambda known=None: seen.append(known)):
            fnas.set_share("media", None, None, None, "on")
        # The views are rebuilt from the same list NFS and Samba are written
        # from, so turning a way in on or off takes effect in one command
        self.assertEqual([(one["name"], one["sftp"]) for one in seen[0]], [("media", True)])

    def test_a_share_can_be_shared_over_sftp_alone(self):
        fake = self.fake()
        fnas.create_share("photos", "tank", True, self.everyone(), False, False, False, True)
        self.assertIn(("zfs", "create", "-p", "tank/shares/photos"), fake.calls)
        self.assertEqual({key: self.settings()["photos"][key] for key in ("nfs", "smb", "webdav", "sftp")},
                         {"nfs": False, "smb": False, "webdav": False, "sftp": True})
        # And it can be turned off again without disturbing anything else
        fnas.set_share("photos", None, None, None, "off")
        self.assertFalse(self.settings()["photos"]["sftp"])

    def test_webdav_is_off_until_it_is_asked_for(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        self.fake(volume_line("media"))
        # An older shares.json has no such key at all, and still reads as off
        self.assertFalse(fnas.list_shares()[0]["webdav"])
        self.assertFalse(fnas.roots_of("anyone")[0]["webdav"])

    def test_webdav_can_be_turned_on_and_off(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": True})
        self.fake(volume_line("media"))
        fnas.set_share("media", None, None, "on")
        self.assertTrue(self.settings()["media"]["webdav"])
        fnas.set_share("media", None, None, "off")
        self.assertFalse(self.settings()["media"]["webdav"])

    def test_forgetting_and_renaming_people(self):
        self.write_settings(docs={"mode": "chosen", "nfs": False, "smb": True,
                                  "access": {"users": {"kim": "read", "tester": "write"}, "groups": {"fam": "read"}}})
        self.fake(volume_line("docs"))
        fnas.forget("users", "kim")
        fnas.rename("groups", "fam", "family")
        self.assertEqual(self.settings()["docs"]["access"], {"users": {"tester": "write"}, "groups": {"family": "read"}})

    def test_delete_lists_what_is_lost(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"), snapshots="tank/shares/media@a\ntank/shares/media@b\n")
        with self.assertRaises(fnas.FnasError) as refused:
            fnas.delete_share("media", "delete media")
        self.assertIn("tank/shares/media@a", str(refused.exception))
        self.assertIn('--confirm "media"', str(refused.exception))
        self.assertEqual(fake.calls, [])

    def test_delete_with_confirmation(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"), snapshots="tank/shares/media@a\n")
        fnas.delete_share("media", "media")
        self.assertIn(("zfs", "destroy", "-r", "tank/shares/media"), fake.calls)
        self.assertEqual(self.settings(), {})
        self.assertEqual(self.exports(), [])

    def test_lan_change_rewrites_both_protocols(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False},
                            smbonly={"mode": "everyone", "nfs": False, "smb": True})
        self.fake(volume_line("media") + volume_line("smbonly"))
        self.assertEqual(fnas.set_lan("10.1.2.3/16"), "10.1.0.0/16")
        self.assertIn(" 10.1.0.0/16(", self.exports()[0])
        self.assertEqual(fnas.lan_subnet(), "10.1.0.0/16")
        self.assertIn("hosts allow = 10.1.0.0/16 ", self.smb_file("shares", "smbonly.conf"))

    def test_subnets_must_be_private_lans(self):
        for value in ("0.0.0.0/0", "*", "8.8.8.0/24", "10.0.0.0/7", "192.168.1.1/32", "fd00::/8", "a,no_root_squash"):
            with self.subTest(value), self.assertRaises(fnas.FnasError):
                fnas.check_subnet(value)
        self.assertEqual(fnas.check_subnet("192.168.1.77/24"), "192.168.1.0/24")


class ProtectionFake(ShareFake):
    """Adds what snapshots, backups, checks and saved settings read."""

    def __init__(self, datasets="", pools="tank\t100\t1\t99\tONLINE\toff\n", snapshots="",
                 status="", created=0, sanoid_failed=False, syncoid_error="", config_dir=None):
        super().__init__(datasets, config_dir=config_dir, pools=pools)
        self.dated_snapshots, self.status = snapshots, status
        self.created, self.sanoid_failed = created, sanoid_failed
        self.syncoid_error = syncoid_error
        self.backups, self.mail = "", None

    def __call__(self, *command, check=True, stdin=None, timeout=None):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ("zpool", "status") and "-j" in command:
            # A pool of one disk with no errors
            name = command[-1]
            ok.stdout = json.dumps({"pools": {name: {"state": "ONLINE", "vdevs": {name: {"vdevs": {
                "disk1": {"name": "disk1", "vdev_type": "disk", "state": "ONLINE"}}}}}}})
        elif command[:2] == ("zpool", "status"):
            ok.stdout = self.status
        elif command[:3] == ("zfs", "list", "-Hp"):
            ok.stdout = self.dated_snapshots
        elif command[:2] == ("zfs", "get") and "creation" in command:
            ok.stdout = f"{self.created}\n"
        elif "-r" in command and command[:2] == ("zfs", "list"):
            ok.stdout = self.backups
            ok.returncode = 0 if self.backups else 1
        elif command[0] == "systemctl":
            ok.returncode = 0 if self.sanoid_failed else 1
        elif command[0] == "syncoid":
            self.calls.append(command)
            ok.stderr, ok.returncode = self.syncoid_error, 1 if self.syncoid_error else 0
        elif command[0] == "msmtp":
            self.mail = stdin
        else:
            return super().__call__(*command, check=check)
        return ok


class Protection(Base):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.pool_dir = os.path.join(self.root, "pool-config")
        os.mkdir(self.pool_dir)
        os.mkdir(os.path.join(self.root, "modules"))
        lan_file = self.path("nas-lan")
        with open(lan_file, "w") as file:
            file.write("192.168.1.0/24\n")
        paths = {"LAN_FILE": lan_file, "SANOID_FILE": self.path("sanoid", "sanoid.conf"),
                 "SMB_DIR": "/nonexistent", "MODULES_DIR": self.path("modules")}
        for name in ("BACKUP_FILE", "BACKUP_STATE", "BACKUP_LOCK", "ALERTS_FILE", "SECRETS_FILE",
                     "MSMTP_FILE", "ALERT_LOG", "ALERT_LOCK", "CHECK_STATE", "INSTALL_ID_FILE", "USAGE_FILE"):
            paths[name] = self.path(name)
        for name, value in paths.items():
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The system disk is this machine's own, so it is given a size here
        self.system = {"size": 100, "used": 10}
        patcher = patch("system_disk", lambda: self.system)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def fake(self, *args, **kwargs):
        return self.use(ProtectionFake(*args, config_dir=self.pool_dir, **kwargs))

    def alerts(self):
        return fnas.read_json(fnas.ALERT_LOG, [])

    def titles(self):
        return [alert["title"] for alert in self.alerts()]

    def saved(self):
        return fnas.read_json(os.path.join(self.pool_dir, "config.json"))

    def write_saved(self, **fields):
        data = {"version": 1, "install": "old", "saved": 1, "lan": None, "backup": None,
                "alerts": {}, "shares": [], "users": [], **fields}
        fnas.write_json(os.path.join(self.pool_dir, "config.json"), data)

    # Snapshots

    def test_sanoid_keeps_each_period_on_its_own_run(self):
        config = fnas.sanoid_config([], None)
        self.assertIn("hourly_min = 0\n", config)
        self.assertIn("daily_min = 30\n", config)
        self.assertIn("monthly_min = 15\n", config)

    def test_sanoid_follows_each_share(self):
        self.fake(share_line("media") + share_line("docs", policy="1,2,3")
                  + share_line("off", policy="0,0,0") + share_line("old", readonly="on"))
        fnas.write_sanoid_config()
        with open(fnas.SANOID_FILE) as file:
            config = file.read()
        self.assertIn("[tank/shares/media]\nuse_template = fellowship\nhourly = 24\ndaily = 30\n", config)
        self.assertIn("[tank/shares/docs]\nuse_template = fellowship\nhourly = 1\ndaily = 2\n", config)
        self.assertNotIn("tank/shares/off", config)
        self.assertNotIn("tank/shares/old", config)

    def test_sanoid_only_prunes_a_connected_backup_pool(self):
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        self.fake(pools="tank\t100\t1\t99\tONLINE\toff\nusb\t100\t1\t99\tONLINE\toff\n")
        fnas.write_sanoid_config()
        with open(fnas.SANOID_FILE) as file:
            self.assertIn("[usb/fellowship-backup]\nuse_template = fellowship\nrecursive = yes\n"
                          "autosnap = no\n", file.read())
        self.fake()
        fnas.write_sanoid_config()
        with open(fnas.SANOID_FILE) as file:
            self.assertNotIn("usb", file.read())

    def test_policy_changes_only_what_was_asked(self):
        self.fake(share_line("media", policy="1,2,3"))
        fnas.set_policy("media", None, 9, None)
        saved = fnas.read_json(os.path.join(self.pool_dir, "shares.json"))["shares"]["media"]
        self.assertEqual(saved["policy"], {"hourly": 1, "daily": 9, "monthly": 3})

    def test_policy_refusals(self):
        fake = self.fake(share_line("media"))
        for args, message in ((("media", None, None, None), "nothing to change"),
                              (("media", -1, None, None), "between"),
                              (("media", None, 1001, None), "between")):
            with self.subTest(args), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.set_policy(*args)
        self.assertEqual(fake.calls, [])

    # Backup

    def two_pools(self):
        return self.fake(pools="tank\t100\t1\t99\tONLINE\toff\nusb\t100\t1\t99\tONLINE\toff\n")

    def test_backup_needs_a_second_writable_pool(self):
        fake = self.fake(pools="tank\t100\t1\t99\tONLINE\toff\nold\t100\t1\t99\tONLINE\ton\n")
        for source, target, message in (("tank", "tank", "second pool"), ("tank", "usb", "usb"),
                                        ("tank", "old", "old")):
            with self.subTest(target), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.set_backup(source, target)
        self.assertEqual(fake.calls, [])
        self.assertIsNone(fnas.read_json(fnas.BACKUP_FILE))

    def test_backup_dataset_is_never_mounted(self):
        fake = self.two_pools()
        fnas.set_backup("tank", "usb")
        self.assertIn(("zfs", "create", "-o", "mountpoint=none", "usb/fellowship-backup"), fake.calls)

    def test_backup_keeps_properties_and_its_own_snapshot(self):
        fake = self.two_pools()
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        fnas.run_backup()
        syncoid = [c for c in fake.calls if c[0] == "syncoid"][0]
        self.assertIn("--sendoptions=p", syncoid)
        self.assertNotIn("--no-sync-snap", syncoid)
        self.assertEqual(syncoid[-2:], ("tank/shares", "usb/fellowship-backup/tank/shares"))
        self.assertIn("last_success", fnas.read_json(fnas.BACKUP_STATE))

    def test_failed_backup_alerts_and_keeps_the_last_success(self):
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        fnas.write_json(fnas.BACKUP_STATE, {"last_success": 5})
        self.use(ProtectionFake(pools="tank\t100\t1\t99\tONLINE\toff\nusb\t100\t1\t99\tONLINE\toff\n",
                                syncoid_error="CRITICAL: no space", created=int(fnas.time.time())))
        with self.assertRaisesRegex(fnas.FnasError, "no space"):
            fnas.run_backup()
        self.assertEqual(fnas.read_json(fnas.BACKUP_STATE)["last_success"], 5)
        self.assertEqual(self.titles(), ["Backup failed"])

    def test_missing_backup_pool_fails_without_running_syncoid(self):
        fake = self.fake(created=int(fnas.time.time()))
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        with self.assertRaisesRegex(fnas.FnasError, "usb isn't imported"):
            fnas.run_backup()
        self.assertFalse([c for c in fake.calls if c[0] == "syncoid"])

    def backup_fake(self, datasets=""):
        fake = self.use(ProtectionFake(
            datasets, pools="tank\t100\t1\t99\tONLINE\toff\nusb\t100\t1\t99\tONLINE\toff\n",
            config_dir=self.pool_dir))
        fake.backups = ("usb/fellowship-backup\nusb/fellowship-backup/tank\n"
                        "usb/fellowship-backup/tank/shares\nusb/fellowship-backup/tank/shares/media\n")
        return fake

    def test_restore_never_replaces_a_share(self):
        fake = self.backup_fake(share_line("Media"))
        with self.assertRaisesRegex(fnas.FnasError, "never replaces"):
            fnas.restore_backup("media", "usb", None)
        self.assertEqual(fake.calls, [])

    def test_restore_brings_back_how_the_share_was_shared(self):
        fnas.write_json(os.path.join(self.pool_dir, "backup-of-tank.json"),
                        {"shares": {"media": {"mode": "shared", "nfs": True, "smb": False}}})
        fake = self.backup_fake()
        real = fake.__class__.__call__

        def restored(*command, check=True, stdin=None, timeout=None):
            if command[0] == "syncoid":
                fake.datasets = volume_line("media")
                fake.existing = False
            return real(fake, *command, check=check, stdin=stdin, timeout=timeout)

        self.use(restored)
        fnas.restore_backup("media", "usb", None)
        self.assertIn(("syncoid", "--quiet", "--no-sync-snap", "--sendoptions=p",
                       "usb/fellowship-backup/tank/shares/media", "tank/shares/media"), fake.calls)
        [media] = fnas.list_shares()
        self.assertEqual((media["mode"], media["nfs"], media["smb"]), ("everyone", True, False))

    def test_backup_carries_the_share_settings(self):
        self.two_pools().datasets = volume_line("media")
        fnas.write_json(os.path.join(self.pool_dir, "shares.json"),
                        {"version": 1, "shares": {"media": {"mode": "matched", "nfs": False, "smb": True}}})
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        fnas.run_backup()
        copied = fnas.read_json(os.path.join(self.pool_dir, "backup-of-tank.json"))
        self.assertEqual(copied["shares"]["media"]["mode"], "chosen")

    def test_restore_of_an_unknown_backup(self):
        self.backup_fake()
        with self.assertRaisesRegex(fnas.FnasError, "no backup of docs"):
            fnas.restore_backup("docs", "usb", None)

    # Alerts

    def test_alerts_are_kept_with_no_channels(self):
        self.assertEqual(fnas.send_alert("warning", "Two\nlines", "body"), [])
        self.assertEqual(self.titles(), ["Two lines"])

    def test_alert_log_stays_bounded(self):
        with patch("MAX_ALERTS", 3):
            for number in range(5):
                fnas.send_alert("info", str(number), "body")
        self.assertEqual(self.titles(), ["2", "3", "4"])

    def test_email_reaches_msmtp(self):
        fake = self.fake()
        fnas.set_email("me@example.com", None, "mail.example.com", 587, "me", "starttls", 'pa"ss')
        self.assertEqual(fnas.send_alert("critical", "Pool bad", "body"), [])
        self.assertIn("Subject: [", fake.mail)
        self.assertIn("To: me@example.com", fake.mail)

    def test_undelivered_alert_is_still_kept(self):
        fnas.set_alert_channel("ntfy", {"url": "http://127.0.0.1:9/topic"})
        failures = fnas.send_alert("critical", "Pool bad", "body")
        self.assertIn("ntfy", failures[0])
        self.assertEqual(self.alerts()[0]["undelivered"], failures)

    def test_undelivered_alerts_are_retried_where_they_failed(self):
        fnas.set_alert_channel("email", {"to": "a@b.c", "from": "a@b.c"})
        fnas.set_alert_channel("ntfy", {"url": "http://x/y"})
        sent = []
        down = mock.Mock(side_effect=fnas.FnasError("ntfy: unreachable"))
        with patch("send_email", lambda *a: sent.append("email")), \
                patch("send_ntfy", down):
            fnas.send_alert("critical", "Pool bad", "body")
        self.assertEqual(sent, ["email"])

        with patch("send_email", lambda *a: sent.append("email")), \
                patch("send_ntfy", lambda *a: sent.append("ntfy")):
            fnas.retry_alerts(fnas.time.time())
            fnas.retry_alerts(fnas.time.time())
        self.assertEqual(sent, ["email", "ntfy"])
        self.assertEqual(self.alerts()[0]["undelivered"], [])

    def test_old_undelivered_alerts_are_dropped(self):
        fnas.set_alert_channel("ntfy", {"url": "http://x/y"})
        with patch("send_ntfy", side_effect=fnas.FnasError("ntfy: unreachable")):
            fnas.send_alert("critical", "Pool bad", "body")
        with patch("send_ntfy") as ntfy:
            fnas.retry_alerts(fnas.time.time() + fnas.RETRY_FOR + 1)
        ntfy.assert_not_called()

    def test_mail_password_stays_private_and_escaped(self):
        fnas.set_email("me@example.com", None, "mail.example.com", 587, "me", "starttls", 'a"b\\c')
        for path in (fnas.MSMTP_FILE, fnas.SECRETS_FILE):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(fnas.MSMTP_FILE) as file:
            self.assertIn('password "a\\"b\\\\c"\n', file.read())
        with open(fnas.ALERTS_FILE) as file:
            self.assertNotIn('a\\"b', file.read())

    def test_mail_settings_cant_add_config_lines(self):
        cases = [("a@b.c\npasswordeval x", "mail.example.com", ""),
                 ("me@example.com", "mail\neval x", ""),
                 ("me@example.com", "mail.example.com", "me\npasswordeval x")]
        for to, host, user in cases:
            with self.subTest(to), self.assertRaises(fnas.FnasError):
                fnas.set_email(to, None, host, 587, user, "starttls", None)
        with self.assertRaises(fnas.FnasError):
            fnas.set_email("me@example.com", None, "m.example.com", 587, "me", "on", "a\nb")
        self.assertFalse(os.path.exists(fnas.MSMTP_FILE))

    def test_ntfy_needs_a_web_address(self):
        for url in ("ftp://x/y", "file:///etc/passwd", "ntfy.sh/topic", "http://a b/c"):
            with self.subTest(url), self.assertRaises(fnas.FnasError):
                fnas.set_ntfy(url, None)

    def test_turning_email_off_removes_its_password(self):
        fnas.set_email("me@example.com", None, "m.example.com", 587, "me", "on", "secret")
        fnas.turn_off_email()
        self.assertEqual(fnas.read_json(fnas.SECRETS_FILE), {})
        self.assertFalse(os.path.exists(fnas.MSMTP_FILE))
        self.assertEqual(fnas.alert_channels(), {})

    # Checks

    def healthy(self, **kwargs):
        kwargs.setdefault("created", int(fnas.time.time()))
        return self.fake(**kwargs)

    def test_quiet_when_all_is_well(self):
        self.healthy()
        self.assertEqual(fnas.run_checks(), [])
        self.assertEqual(self.alerts(), [])

    def test_the_hourly_check_records_the_system_for_the_health_charts(self):
        self.healthy()
        fnas.run_checks()
        [point] = fnas.system_history()["hourly"]
        self.assertEqual(len(point), 7)
        # The system disk is the one the test gave it
        self.assertEqual(point[4:6], [10, 100])

    def test_health_report_carries_the_history(self):
        self.healthy(pools="tank\t100\t10\t90\tONLINE\toff\n")
        fnas.run_checks()
        report = fnas.health_report(fnas.time.time())
        self.assertEqual(len(report["history"]["system"]["hourly"]), 1)
        self.assertIn("cpu", report["system"])
        [pool] = report["pools"]
        [recorded] = pool["history"]["hourly"]
        self.assertEqual(recorded[1], pool["allocated"])
        self.assertLess(abs(recorded[0] - report["time"]), 5)
        # One point can't show a rate yet, and the report says so
        self.assertIn("reason", pool["projection"])

    def test_a_full_pool_alerts_once_then_daily_then_clears(self):
        self.healthy(pools="tank\t100\t85\t15\tONLINE\toff\n")
        fnas.run_checks()
        fnas.run_checks()
        self.assertEqual(self.titles(), ["Pool tank is 85% full"])

        state = fnas.read_json(fnas.CHECK_STATE)
        state["pool-full-medium-tank"]["sent"] -= fnas.REMIND_AFTER
        fnas.write_json(fnas.CHECK_STATE, state)
        fnas.run_checks()
        self.assertEqual(len(self.alerts()), 2)

        self.healthy()
        fnas.run_checks()
        self.assertEqual(self.titles()[-1], "Resolved: Pool tank is 85% full")
        self.assertEqual(fnas.read_json(fnas.CHECK_STATE), {})

    def checks(self, **kwargs):
        return {c["key"]: c for c in fnas.all_checks(fnas.time.time(), found_disks=[], **kwargs)}

    def test_every_check_is_listed_with_no_pool(self):
        self.healthy(pools="")
        checks = self.checks()
        self.assertEqual(list(checks), ["pool-state", "pool-space", "scrub", "snapshots", "backup", "settings",
                                        "system-disk", "zfs-module", "services", "disks"])
        self.assertEqual(checks["pool-state"]["state"], "na")
        self.assertEqual(checks["pool-state"]["detail"], "No pool yet")
        self.assertEqual(checks["backup"]["detail"], "No backup job. Set one up in Snapshots and backup")
        self.assertEqual(checks["disks"]["state"], "na")
        self.assertEqual(checks["system-disk"]["state"], "passed")
        self.assertEqual(checks["system-disk"]["detail"], "10% full")

    def test_checks_pass_with_a_healthy_pool(self):
        self.healthy()
        checks = self.checks()
        self.assertEqual(checks["pool-state"]["state"], "passed")
        self.assertEqual(checks["pool-space"]["detail"], "Every pool under 80% full")

    def test_a_full_system_disk_alerts(self):
        self.healthy()
        self.system = {"size": 100, "used": 96}
        self.assertEqual(self.checks()["system-disk"]["state"], "high")
        self.assertEqual(self.titles() + [p["title"] for p in fnas.run_checks()], ["The system disk is 96% full"])

    def test_a_stopped_server_that_shares_need_is_critical(self):
        self.healthy()
        protocols = [{"name": "smb", "title": "SMB", "state": "stopped", "shares": ["media"]},
                     {"name": "nfs", "title": "NFS", "state": "stopped", "shares": []}]
        with patch("protocols", lambda: protocols):
            found = fnas.service_findings()
        self.assertEqual([(f["title"], f["severity"]) for f in found], [("SMB isn't running", "critical")])

    def test_failing_disks_are_shown_but_not_alerted(self):
        self.healthy()
        failing = [{"path": "/dev/vdb", "by_id": "/dev/disk/by-id/ata-WDC_X_SN1", "verdict": "failing",
                    "notes": ["12 sectors reallocated"]}]
        checks = {c["key"]: c for c in fnas.all_checks(fnas.time.time(), found_disks=failing)}
        self.assertEqual(checks["disks"]["state"], "critical")
        self.assertEqual(fnas.find_problems(fnas.time.time(), list(checks.values())), [])

    def test_a_nearly_full_pool_is_a_high_warning_not_critical(self):
        found = fnas.pool_findings([{"name": "tank", "allocated": 95, "size": 100}])
        self.assertEqual(found[0]["severity"], "high")

    def test_broken_things_are_critical(self):
        self.healthy()
        stopped = [{"unit": "sanoid.timer", "title": "Snapshot schedule", "state": "stopped", "detail": "Stopped"}]
        with patch("system_services", lambda: stopped), patch("protocols", lambda: []):
            self.assertEqual([f["severity"] for f in fnas.service_findings()], ["critical"])
        self.assertEqual(fnas.kernel_findings(), [])

    def test_older_warning_alerts_become_medium(self):
        self.healthy()
        fnas.send_alert("warning", "Old hook", "body")
        self.assertEqual(self.alerts()[-1]["severity"], "medium")
        self.assertEqual(fnas.NTFY_PRIORITY["critical"], "urgent")
        self.assertEqual(fnas.NTFY_PRIORITY["low"], "low")

    def test_scrub_age_comes_from_zpool_status(self):
        status = ("  pool: tank\n state: ONLINE\n  scan: scrub repaired 0B in 00:00:01 with 0 errors "
                  "on Sun Nov  8 00:24:01 2026\nconfig:\n")
        self.healthy(status=status)
        expected = fnas.datetime.datetime(2026, 11, 8, 0, 24, 1).timestamp()
        self.assertEqual(fnas.last_scrub("tank"), expected)
        self.assertEqual(fnas.scrub_findings(["tank"], expected + fnas.MAX_AGE["monthly"] - 1), [])
        self.assertEqual(len(fnas.scrub_findings(["tank"], expected + fnas.MAX_AGE["monthly"] + 1)), 1)

    def test_a_pool_never_checked_falls_due_after_it_was_made(self):
        engine = SimpleNamespace(CHECK_WORD="check", last_scrub=lambda pool: 0,
                                 created=lambda pool: 1000, damage=lambda pool: None, problems=lambda pool, now: [])
        with patch("engine_of", lambda pool: engine), patch("find_pool", lambda pool: {"engine": "mdadm"}):
            self.assertEqual(fnas.scrub_findings(["vault"], 1000 + fnas.MAX_AGE["monthly"] - 1), [])
            found = fnas.scrub_findings(["vault"], 1000 + fnas.MAX_AGE["monthly"] + 1)
        self.assertEqual(len(found), 1)
        self.assertIn("since it was made", found[0]["message"])

    def test_zfs_errors_on_disks_still_in_the_pool_are_a_finding(self):
        zfs = importlib.import_module("fellowship_nas.engines.zfs")
        disk = {"name": "a", "type": "disk", "state": "ONLINE", "errors": 0}
        status = {"groups": [{"name": "mirror-0", "type": "mirror", "state": "ONLINE", "errors": 0,
                              "disks": [dict(disk), dict(disk, name="b")]}]}
        with mock.patch.object(zfs, "pool_status", lambda pool: status):
            self.assertIsNone(zfs.damage("tank"))
            status["groups"][0]["disks"][1]["errors"] = 3
            title, message = zfs.damage("tank")
        self.assertEqual(title, "Pool tank: 3 read, write or checksum errors")
        self.assertIn("sudo fnas pool status tank", message)

    def test_resilver_is_not_a_scrub(self):
        self.healthy(status="  scan: resilvered 1G in 00:01:00 with 0 errors on Sun Nov  8 00:24:01 2026\n",
                     created=77)
        self.assertEqual(fnas.last_scrub("tank"), 77)

    def test_a_never_scrubbed_pool_counts_from_its_creation(self):
        self.healthy(status="  scan: none requested\n", created=77)
        self.assertEqual(fnas.last_scrub("tank"), 77)

    def share(self, policy=None, readonly=False):
        return {"name": "media", "dataset": "tank/shares/media", "readonly": readonly,
                "policy": policy or dict(fnas.DEFAULT_POLICY)}

    def test_snapshots_that_stop_are_noticed(self):
        now = 1000 * fnas.DAY
        self.fake(snapshots=f"tank/shares/media@autosnap_a\t{now - 4 * fnas.HOUR}\t0\n")
        self.assertEqual(len(fnas.snapshot_findings([self.share()], now)), 1)
        self.assertEqual(fnas.snapshot_findings([self.share()], now - 2 * fnas.HOUR), [])

    def test_syncoids_snapshots_dont_count_as_sanoid_working(self):
        now = 1000 * fnas.DAY
        self.fake(snapshots=f"tank/shares/media@syncoid_fellowship_x\t{now}\t0\n")
        self.assertEqual(len(fnas.snapshot_findings([self.share()], now)), 1)

    def test_a_new_share_gets_time_for_its_first_snapshot(self):
        now = 1000 * fnas.DAY
        self.fake(created=now - fnas.HOUR)
        self.assertEqual(fnas.snapshot_findings([self.share()], now), [])

    def test_shares_without_snapshots_are_left_alone(self):
        self.fake(created=0)
        off = self.share({"hourly": 0, "daily": 0, "monthly": 0})
        self.assertEqual(fnas.snapshot_findings([off, self.share(readonly=True)], 1000 * fnas.DAY), [])

    def test_a_failed_snapshot_service_is_critical(self):
        self.fake(sanoid_failed=True)
        self.assertEqual(fnas.snapshot_findings([], 0)[0]["key"], "sanoid-failed")

    def test_backup_overdue(self):
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        self.assertEqual(fnas.backup_findings(fnas.BACKUP_MAX_AGE - 1), [])
        self.assertEqual(fnas.backup_findings(fnas.BACKUP_MAX_AGE + 1)[0]["key"], "backup-overdue")
        fnas.write_json(fnas.BACKUP_STATE, {"last_success": fnas.BACKUP_MAX_AGE})
        self.assertEqual(fnas.backup_findings(fnas.BACKUP_MAX_AGE + 1), [])

    def test_no_backup_job_means_no_backup_alerts(self):
        self.assertEqual(fnas.backup_findings(10 ** 10), [])

    def test_newest_kernel_needs_a_zfs_module(self):
        for kernel in ("6.12.9-amd64", "6.12.10-amd64"):
            os.mkdir(self.path("modules", kernel))
        self.assertIn("6.12.10-amd64", fnas.kernel_findings()[0]["title"])
        os.makedirs(self.path("modules", "6.12.10-amd64", "updates", "dkms"))
        with open(self.path("modules", "6.12.10-amd64", "updates", "dkms", "zfs.ko.xz"), "w"):
            pass
        self.assertEqual(fnas.kernel_findings(), [])

    # Saved settings

    def test_settings_reach_the_pool_without_secrets(self):
        self.fake(share_line("media"))
        fnas.set_email("me@example.com", None, "m.example.com", 587, "me", "on", "hunter2")
        fnas.set_secret("ntfy_token", "tk_private")
        fnas.save_config()
        with open(os.path.join(self.pool_dir, "config.json")) as file:
            text = file.read()
        self.assertNotIn("hunter2", text)
        self.assertNotIn("tk_private", text)
        self.assertEqual(self.saved()["lan"], "192.168.1.0/24")
        self.assertEqual(self.saved()["shares"][0]["name"], "media")

    def test_a_fresh_install_cant_overwrite_what_it_hasnt_restored(self):
        self.fake()
        self.write_saved(lan="10.0.0.0/24")
        fnas.after_change()
        self.assertEqual(self.saved()["lan"], "10.0.0.0/24")
        self.assertEqual(fnas.foreign_config_pools(), ["tank"])

    def test_read_only_pools_are_left_alone(self):
        self.fake(pools="tank\t100\t1\t99\tONLINE\ton\n")
        fnas.save_config()
        self.assertIsNone(self.saved())

    def test_unrestored_settings_raise_an_alert(self):
        self.healthy()
        self.write_saved()
        self.assertEqual(fnas.run_checks()[0]["key"], "config-tank")

    def test_restore_brings_settings_back_and_claims_the_pool(self):
        self.fake()
        self.write_saved(lan="10.0.0.0/24", backup={"source": "tank", "target": "usb", "since": 1},
                         alerts={"email": {"to": "me@example.com", "from": "me@example.com",
                                           "host": "m.example.com", "port": 587, "user": "me",
                                           "tls": "on"},
                                 "ntfy": {"url": "https://ntfy.sh/topic"}})
        notes = fnas.restore_config("tank")
        self.assertEqual(fnas.lan_subnet(), "10.0.0.0/24")
        self.assertEqual(fnas.read_json(fnas.BACKUP_FILE)["target"], "usb")
        self.assertIn("password", notes[0])
        self.assertTrue(fnas.alert_channels()["email"]["needs_password"])
        self.assertEqual(fnas.foreign_config_pools(), [])

    def test_restore_claims_every_pool_the_old_install_saved_to(self):
        self.fake(pools="".join(f"{p}\t100\t1\t99\tONLINE\toff\n" for p in ("tank", "usb", "spare")))
        paths = {}
        for pool, install in (("tank", "old"), ("usb", "old"), ("spare", "someone-else")):
            paths[pool] = os.path.join(self.root, f"{pool}.json")
            fnas.write_json(paths[pool], {"version": 1, "install": install, "saved": 1, "lan": None,
                                          "backup": None, "alerts": {}, "shares": [], "users": []})
        with patch("config_path", side_effect=paths.get):
            fnas.restore_config("tank")
            self.assertEqual(fnas.foreign_config_pools(), ["spare"])

    def test_restore_trusts_nothing_on_the_disk(self):
        email_settings = {"to": "a@b.c", "from": "a@b.c", "host": "m\npasswordeval evil", "port": 25,
                          "user": "", "tls": "on"}
        cases = [{"alerts": {"email": email_settings}},
                 {"alerts": {"ntfy": {"url": "file:///etc/shadow"}}},
                 {"backup": {"source": "../x", "target": "usb"}},
                 {"lan": "0.0.0.0/0"}]
        user = {"name": "sam", "uid": 1001, "gid": 1001, "group": "sam", "comment": ""}
        for fields in cases:
            self.fake()
            self.write_saved(users=[user], **fields)
            with self.subTest(fields), self.assertRaises(fnas.FnasError), \
                    patch("restore_user") as restore_user:
                fnas.restore_config("tank")
            restore_user.assert_not_called()
            self.assertFalse(os.path.exists(fnas.MSMTP_FILE))

    def test_restore_needs_saved_settings_it_understands(self):
        self.fake()
        with self.assertRaisesRegex(fnas.FnasError, "no saved settings"):
            fnas.restore_config("tank")
        self.write_saved(version=99)
        with self.assertRaisesRegex(fnas.FnasError, "different version"):
            fnas.restore_config("tank")

    def test_restored_users_keep_their_ids(self):
        fake = self.fake()
        user = {"name": "sam", "uid": 1001, "gid": 1001, "group": "sam", "comment": "Sam"}
        with mock.patch.object(fnas.pwd, "getpwnam", side_effect=KeyError), \
                mock.patch.object(fnas.pwd, "getpwuid", side_effect=KeyError), \
                mock.patch.object(fnas.grp, "getgrgid", side_effect=KeyError):
            self.assertIn("set a password", fnas.restore_user(user))
        self.assertIn(("groupadd", "--gid", "1001", "sam"), fake.calls)
        self.assertIn(("useradd", "--uid", "1001", "--gid", "1001", "--comment", "Sam",
                       "--create-home", "--shell", "/bin/bash", "sam"), fake.calls)

    def test_user_clashes_are_reported_and_nothing_changes(self):
        fake = self.fake()
        user = {"name": "sam", "uid": 1001, "gid": 1001, "group": "sam", "comment": ""}
        other = SimpleNamespace(pw_uid=1000, pw_name="tester")
        with mock.patch.object(fnas.pwd, "getpwnam", return_value=other):
            self.assertIn("files won't match", fnas.restore_user(user))
        with mock.patch.object(fnas.pwd, "getpwnam", side_effect=KeyError), \
                mock.patch.object(fnas.pwd, "getpwuid", return_value=other):
            self.assertIn("belongs to tester", fnas.restore_user(user))
        self.assertEqual(fake.calls, [])

    def test_saved_users_are_checked(self):
        fake = self.fake()
        for user in ({"name": "root", "uid": 0, "gid": 0, "group": "root"},
                     {"name": "a;b", "uid": 1001, "gid": 1001, "group": "a"},
                     {"name": "sam", "uid": 1001, "gid": 1001, "group": None}):
            with self.subTest(user):
                self.assertIn("skipped", fnas.restore_user(user))
        self.assertEqual(fake.calls, [])

    def test_adopt_needs_confirmation(self):
        self.fake()
        self.write_saved()
        with self.assertRaisesRegex(fnas.FnasError, "replace saved settings on tank"):
            fnas.adopt_config("tank", "yes")
        self.assertEqual(self.saved()["install"], "old")
        fnas.adopt_config("tank", "replace saved settings on tank")
        self.assertEqual(self.saved()["install"], fnas.install_id())


class Parser(unittest.TestCase):
    def test_takes_any_layout_name(self):
        # The engine decides which layouts exist, so a new engine needs no
        # change to the parser
        args = fnas.build_parser().parse_args(["pool", "create", "tank", "raid5", "--engine", "mdadm", "/dev/vdb"])
        self.assertEqual((args.layout, args.engine), ("raid5", "mdadm"))

    def test_a_layout_the_engine_lacks_is_refused(self):
        with self.assertRaisesRegex(fnas.FnasError, r"no such layout: stripe \(ZFS offers single, mirror"):
            zfs.plan("stripe", [GB])


class Command(unittest.TestCase):
    def test_a_stuck_command_is_stopped(self):
        # A hung tool would otherwise hold a root process and the browser's request
        with self.assertRaisesRegex(fnas.FnasError, "still running after 0.1 seconds"):
            fnas.run(sys.executable, "-c", "import time; time.sleep(5)", timeout=0.1)


if __name__ == "__main__":
    unittest.main()


class Unraid(unittest.TestCase):
    """Unraid-style pools: whole files on each disk, joined by mergerfs, with SnapRAID parity."""

    engine = mergerfs

    def pool(self):
        disk = lambda role, name, serial: {"role": role, "name": name, "by_id": f"/dev/disk/by-id/ata-{serial}",
                                           "serial": serial, "uuid": f"uuid-{serial}",
                                           "mount": f"/srv/disks/{serial}"}
        return {"name": "media", "id": "0123456789abcdef", "created": 1000,
                "disks": [disk("data", "d1", "A1"), disk("data", "d2", "B2"), disk("parity", "parity", "P1")]}

    def test_the_largest_disks_hold_parity(self):
        self.assertEqual(self.engine.plan("parity1", [4, 8, 6]), ([[0, 2]], [1]))
        self.assertEqual(self.engine.plan("parity2", [4, 8, 6, 8]), ([[0, 2]], [1, 3]))
        with self.assertRaisesRegex(fnas.FnasError, "at least one data disk"):
            self.engine.plan("parity2", [4, 8])
        with self.assertRaisesRegex(fnas.FnasError, "parity disks rather than spares"):
            self.engine.plan("parity1", [4, 8], spares=1)

    def test_one_parity_disk_up_to_five_data_disks_then_two(self):
        self.assertIsNone(self.engine.recommended(1))
        self.assertEqual(self.engine.recommended(2), ("parity1", 1, 0))
        self.assertEqual(self.engine.recommended(6), ("parity1", 5, 0))
        # Seven disks would be six data disks with one parity, so the second parity comes in
        self.assertEqual(self.engine.recommended(7), ("parity2", 5, 0))
        self.assertEqual(self.engine.recommended(8), ("parity2", 6, 0))
        options = self.engine.layouts([4 * GB, 4 * GB, 8 * GB])
        best = options[0]
        self.assertEqual((best["layout"], best["width"], best["usable"], best["survives"]), ("parity1", 2, 8 * GB, 1))
        # Mixed sizes waste nothing on the data disks; a parity disk larger than any data disk does
        self.assertEqual(best["unused"], 4 * GB)

    def test_snapraid_conf_names_every_disk(self):
        with mock.patch.object(self.engine, "CONTENT_DIR", "/var/lib/fellowship-nas/snapraid"):
            conf = self.engine.snapraid_conf(self.pool())
        self.assertIn("parity /srv/disks/P1/snapraid.parity\n", conf)
        self.assertIn("content /var/lib/fellowship-nas/snapraid/media.content\n", conf)
        self.assertIn("content /srv/disks/A1/snapraid.content\n", conf)
        self.assertIn("data d2 /srv/disks/B2/files/\n", conf)
        self.assertNotIn("/srv/disks/P1/snapraid.content", conf)

    def test_writing_the_mounts_twice_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            fstab = os.path.join(directory, "fstab")
            with open(fstab, "w") as file:
                file.write("UUID=root / ext4 defaults 0 1\n")
            ran = []
            with mock.patch.object(self.engine, "FSTAB", fstab), \
                    patch("run", lambda *command, check=True, timeout=None: ran.append(command)
                          or SimpleNamespace(stdout="", stderr="", returncode=0)):
                self.engine.write_fstab(self.pool())
                with open(fstab) as file:
                    first = file.read()
                reloads = len(ran)
                self.engine.write_fstab(self.pool())
                with open(fstab) as file:
                    self.assertEqual(file.read(), first)
                # Nothing changed, so systemd isn't asked to read it again
                self.assertEqual(len(ran), reloads)
                self.engine.write_fstab(self.pool(), remove=True)
                with open(fstab) as file:
                    self.assertEqual(file.read(), "UUID=root / ext4 defaults 0 1\n")
        lines = first.splitlines()
        self.assertEqual(lines[1], "UUID=uuid-A1 /srv/disks/A1 xfs noatime,nofail,x-systemd.device-timeout=15s 0 2")
        pool_line = lines[-1].split()
        self.assertEqual(pool_line[:3], ["/srv/disks/A1/files:/srv/disks/B2/files", "/mnt/media", "fuse.mergerfs"])
        self.assertIn("posix_acl=true", pool_line[3])
        self.assertIn("x-systemd.after=srv-disks-P1.mount", pool_line[3])
        self.assertIn("nofail", pool_line[3])

    def test_the_pool_mounts_through_systemd(self):
        # mergerfs keeps running while mounted; started by fnas itself, it would stop
        # whenever the service that ran fnas restarted, as the desktop's helper does on updates
        ran = []
        def fake(*command, check=True, timeout=None):
            ran.append(command)
            return SimpleNamespace(stdout="mnt-my\\x2dpool.mount\n" if command[0] == "systemd-escape" else "",
                                   stderr="", returncode=0)
        with patch("run", fake):
            self.engine.mount_pool("my-pool")
        self.assertIn(("systemctl", "start", "mnt-my\\x2dpool.mount"), ran)
        self.assertFalse(any(command[0] == "mount" for command in ran))

    def test_disks_are_found_by_their_label(self):
        output = ("DEVNAME=/dev/sdb\nLABEL=fn-012345678\nUUID=1\nTYPE=xfs\n\n"
                  "DEVNAME=/dev/sdc\nLABEL=photos\nTYPE=xfs\n\n"
                  "DEVNAME=/dev/sdd\nLABEL=fn-012345678\nTYPE=ext4\n")
        with patch("run", lambda *command, check=True: SimpleNamespace(stdout=output, stderr="", returncode=0)):
            self.assertEqual(self.engine.labelled(), ["/dev/sdb"])

    def test_a_sync_stops_when_many_files_went_missing(self):
        diff = "Comparing...\n     120 equal\n       3 added\n      80 removed\n       0 updated\n"
        sent = []
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.engine, "STATE_FILE", os.path.join(directory, "state.json")), \
                mock.patch.object(self.engine, "record", lambda name: self.pool()), \
                mock.patch.object(self.engine, "present", lambda disk: True), \
                mock.patch.object(self.engine.alerts, "send_alert", lambda *alert: sent.append(alert)), \
                patch("run", lambda *command, check=True, timeout=None: SimpleNamespace(stdout=diff, stderr="",
                                                                                     returncode=2)):
            self.assertEqual(self.engine.deleted_since_sync("media"), 80)
            with self.assertRaisesRegex(fnas.FnasError, "80 files went missing"):
                self.engine.sync_pool("media")
            self.assertEqual(sent[0][:2], ("high", "Pool media: parity sync skipped"))
            [problem] = self.engine.problems("media", 2000)
            self.assertEqual(problem[:2], ("sync-skipped", "high"))

    def test_a_late_sync_is_a_health_finding(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.engine, "STATE_FILE", os.path.join(directory, "state.json")), \
                mock.patch.object(self.engine, "record", lambda name: self.pool()), \
                mock.patch.object(fnas_checks, "SETTINGS_FILE", os.path.join(directory, "checks.json")):
            self.engine.set_state("media", last_sync=1000)
            self.assertEqual(self.engine.problems("media", 1000 + 29 * 3600), [])
            [problem] = self.engine.problems("media", 1000 + 31 * 3600)
            self.assertEqual(problem[:2], ("sync-late", "medium"))
            fnas.write_json(fnas_checks.SETTINGS_FILE, {"mergerfs": {**fnas_checks.DEFAULTS["mergerfs"], "sync": False}})
            # With the nightly sync turned off, an old sync is a choice
            self.assertEqual(self.engine.problems("media", 1000 + 31 * 3600), [])

    def test_files_changed_during_a_sync_dont_fail_it(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(self.engine, "LOG_DIR", directory):
            job = {"id": "abc", "kind": "sync"}
            with open(self.engine.log_path(job), "w") as file:
                file.write("WARNING! You cannot modify data disk during a sync.\n  1 file errors\n"
                           "       0 io errors\n       0 data errors\n")
            self.assertTrue(self.engine.changed_during_sync(job))
            with open(self.engine.log_path(job), "a") as file:
                file.write("       2 io errors\n")
            # A disk that failed to read is a real failure, whatever else changed
            self.assertFalse(self.engine.changed_during_sync(job))
        with mock.patch.object(self.engine, "CONTENT_DIR", "/c"):
            self.assertIn("exclude /fellowship-config/\n", self.engine.snapraid_conf(self.pool()))

    def test_a_scrub_reports_damage_from_its_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "scrub.log")
            with open(log, "w") as file:
                file.write("Scrubbing...\n 50%\r100% completed\n     3 file errors\n     1 io error\n")
            self.assertEqual(self.engine.scrub_errors(log), 4)

    def test_nfs_gets_a_fixed_id_for_a_fuse_pool(self):
        share = {"path": "/mnt/media/shares/films", "engine": "mergerfs", "readonly": False,
                 "nfs_options": {**fnas.DEFAULT_NFS, "clients": [{"host": "192.168.1.0/24", "access": "rw"}]},
                 "mode": "everyone", "access": {"users": {}, "groups": {}}}
        first = fnas.nfs_clients(share, "192.168.1.0/24")
        self.assertRegex(first, r"fsid=[0-9a-f-]{36}")
        self.assertEqual(fnas.nfs_clients(share, "192.168.1.0/24"), first)
        self.assertNotIn("fsid", fnas.nfs_clients({**share, "engine": "zfs"}, "192.168.1.0/24"))


class StorageChecks(unittest.TestCase):
    """When each engine checks its pools by itself, from Settings → Storage checks."""

    checks = importlib.import_module("fellowship_nas.storage_checks")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.ran = []
        for name, value in (("SETTINGS_FILE", os.path.join(self.dir, "checks.json")),
                            ("DROP_IN_DIR", os.path.join(self.dir, "{engine}.d")),
                            ("SYNC_DROP_IN_DIR", os.path.join(self.dir, "sync-{engine}.d"))):
            patcher = mock.patch.object(self.checks, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(self.checks, "run", lambda *command, check=True: self.ran.append(command)
                                          or SimpleNamespace(stdout="", stderr="", returncode=0)),
                        mock.patch.object(self.checks.pools, "usable_engines", lambda: {"zfs": None, "mdadm": None}),
                        mock.patch.object(self.checks.pools, "list_pools", lambda: [])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_defaults_keep_debians_schedule(self):
        current = self.checks.settings()
        self.assertEqual(self.checks.calendar(current["zfs"]), "Sun *-*-08..14 00:00:00")
        self.assertEqual(self.checks.describe("zfs"), "on the second Sunday of each month, at 00:00")
        self.assertEqual(self.checks.max_age("zfs"), 40 * fnas.DAY)
        self.assertEqual(self.checks.max_age("mdadm"), 40 * fnas.DAY)

    def test_a_quarterly_schedule(self):
        self.checks.set_schedule("zfs", every="quarterly", week=1, day="sat", hour=3)
        current = self.checks.settings()["zfs"]
        self.assertEqual(self.checks.calendar(current), "Sat *-01,04,07,10-01..07 03:00:00")
        with open(os.path.join(self.dir, "zfs.d", "schedule.conf")) as file:
            self.assertIn("OnCalendar=\nOnCalendar=Sat *-01,04,07,10-01..07 03:00:00\n", file.read())
        self.assertIn(("systemctl", "enable", "--now", "fellowship-nas-scrub@zfs.timer"), self.ran)
        self.assertEqual(self.checks.max_age("zfs"), 100 * fnas.DAY)

    def test_turned_off_is_a_choice_not_a_late_check(self):
        self.checks.set_schedule("zfs", enabled=False)
        self.checks.set_schedule("mdadm", enabled=False)
        self.assertIsNone(self.checks.max_age("zfs"))
        self.assertIn("turned off", self.checks.sentence("mdadm"))
        self.assertIn(("systemctl", "disable", "--now", "fellowship-nas-scrub@zfs.timer"), self.ran)
        # Masked, so mdadm's own preset can't turn the check back on
        self.assertIn(("systemctl", "mask", "--now", *self.checks.MDCHECK_TIMERS), self.ran)

    def test_health_shows_turned_off_checks_as_a_choice(self):
        found = [{"engine": "zfs"}, {"engine": "mdadm"}]
        self.checks.set_schedule("mdadm", enabled=False)
        self.assertEqual(fnas.checks_not_applicable(found), "")
        self.assertEqual(fnas.checks_passed(found),
                         "Every pool checked on schedule; automatic checks turned off in Settings for Traditional RAID")
        self.checks.set_schedule("zfs", enabled=False)
        self.assertEqual(fnas.checks_not_applicable(found), "Automatic checks are turned off in Settings → Storage checks")

    def test_debians_array_check_only_turns_on_or_off(self):
        with self.assertRaisesRegex(fnas.FnasError, "only be turned on or off"):
            self.checks.set_schedule("mdadm", every="quarterly")
        for wrong in ({"week": 5}, {"day": "funday"}, {"hour": 24}, {"every": "weekly"}):
            with self.subTest(wrong=wrong), self.assertRaises(fnas.FnasError):
                self.checks.set_schedule("zfs", **wrong)

    def test_a_broken_settings_file_falls_back_to_the_defaults(self):
        fnas.write_json(self.checks.SETTINGS_FILE, {"zfs": {"every": "weekly", "hour": 3}, "mdadm": "yes"})
        self.assertEqual(self.checks.settings(), self.checks.DEFAULTS)


class DiskHealth(unittest.TestCase):
    DISK = {"path": "/dev/sda", "by_id": "/dev/disk/by-id/ata-X", "model": "X", "serial": "1",
            "size": 1, "state": "free"}

    def health(self, data, disk=None):
        output = SimpleNamespace(stdout=json.dumps(data) if data is not None else "", returncode=0)
        with patch("run", return_value=output):
            return fnas.disk_health(disk or self.DISK)

    def ata(self, passed=True, reallocated=0, temperature=35):
        return {"smart_status": {"passed": passed}, "temperature": {"current": temperature},
                "power_on_time": {"hours": 1200},
                "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": reallocated}},
                                                   {"id": 9, "raw": {"value": 1200}}]}}

    def test_healthy_disk(self):
        health = self.health(self.ata())
        self.assertEqual((health["verdict"], health["notes"], health["hours"]), ("healthy", [], 1200))

    def test_counters_and_heat_need_watching(self):
        health = self.health(self.ata(reallocated=8, temperature=58))
        self.assertEqual(health["verdict"], "watch")
        self.assertEqual(health["notes"], ["8 reallocated sectors", "running hot at 58 °C"])

    def test_failing_disk(self):
        health = self.health(self.ata(passed=False))
        self.assertEqual(health["verdict"], "failing")
        self.assertIn("replace it soon", health["notes"][0])

    def test_nvme(self):
        health = self.health({"smart_status": {"passed": True},
                              "nvme_smart_health_information_log": {"media_errors": 2,
                                                                    "percentage_used": 95}})
        self.assertEqual(health["notes"], ["2 media errors", "has used 95% of its rated life"])
        health = self.health({"smart_status": {"passed": True},
                              "nvme_smart_health_information_log": {"critical_warning": 4}})
        self.assertEqual(health["verdict"], "failing")

    def test_disk_without_smart(self):
        for data in (None, {"smartctl": {"exit_status": 4}}):
            with self.subTest(data):
                self.assertEqual(self.health(data)["verdict"], "unknown")

    def test_bay_number_is_carried(self):
        # The desktop's drive bay map names each slot after the bay it is in
        self.assertIsNone(self.health(self.ata())["bay"])
        self.assertEqual(self.health(self.ata(), dict(self.DISK, bay="Bay 3"))["bay"], "Bay 3")


class SmbPassword(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("SMB_DIR", directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run_mock = patch("run").start()
        self.addCleanup(mock.patch.stopall)

    def account(self, uid, shell="/bin/bash"):
        return mock.patch.object(fnas.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=uid, pw_shell=shell))

    def test_sets_the_password_through_stdin(self):
        with self.account(1000):
            fnas.set_smb_password("sam", "s3cret word")
        self.run_mock.assert_called_once_with("smbpasswd", "-s", "-a", "sam",
                                              stdin="s3cret word\ns3cret word\n")

    def test_only_peoples_accounts(self):
        with self.account(0), self.assertRaisesRegex(fnas.FnasError, "isn't a person's account"):
            fnas.set_smb_password("root", "x")
        with self.account(3000, "/usr/sbin/nologin"), self.assertRaisesRegex(fnas.FnasError, "isn't a person's"):
            fnas.set_smb_password("nas-share", "x")
        with mock.patch.object(fnas.pwd, "getpwnam", side_effect=KeyError), \
                self.assertRaisesRegex(fnas.FnasError, "no such user"):
            fnas.set_smb_password("nobody-here", "x")
        self.run_mock.assert_not_called()

    def test_password_must_be_one_line(self):
        for password in ("", "a\nb"):
            with self.subTest(password), self.account(1000), self.assertRaises(fnas.FnasError):
                fnas.set_smb_password("sam", password)
        self.run_mock.assert_not_called()


class System(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        for name, value in {"WEB_DIR": self.dir, "WEB_CERT": f"{self.dir}/cert.pem",
                            "WEB_KEY": f"{self.dir}/key.pem", "WEB_SELF_SIGNED": f"{self.dir}/self-signed"}.items():
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.chown = mock.patch.object(fnas.os, "chown").start()
        self.addCleanup(mock.patch.stopall)

    def make_pair(self, name):
        key, cert = f"{self.dir}/{name}.key", f"{self.dir}/{name}.crt"
        fnas.run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-days", "30", "-subj", f"/CN={name}", "-keyout", key, "-out", cert)
        with open(cert) as c, open(key) as k:
            return c.read(), k.read()

    def test_accepts_a_matching_certificate_and_key(self):
        cert, key = self.make_pair("nas.example")
        open(fnas.WEB_SELF_SIGNED, "w").close()
        fnas.set_certificate(key + cert)
        info = fnas.certificate_info()
        self.assertIn("nas.example", info["subject"])
        self.assertFalse(info["self_signed"])
        self.assertEqual(os.stat(fnas.WEB_KEY).st_mode & 0o777, 0o640)

    def test_refuses_a_key_from_another_certificate(self):
        cert, _ = self.make_pair("one")
        _, other_key = self.make_pair("two")
        with self.assertRaisesRegex(fnas.FnasError, "doesn't belong"):
            fnas.set_certificate(cert + other_key)
        self.assertFalse(os.path.exists(fnas.WEB_CERT))

    def test_refuses_incomplete_bundles(self):
        cert, key = self.make_pair("one")
        for pem in ("", cert, key, cert + key + key):
            with self.subTest(pem[:20]), self.assertRaisesRegex(fnas.FnasError, "PEM"):
                fnas.set_certificate(pem)

    def test_self_signed_names_the_host(self):
        with mock.patch.object(fnas.socket, "gethostname", return_value="vault"):
            fnas.make_self_signed()
        info = fnas.certificate_info()
        self.assertIn("CN=vault", info["subject"])
        self.assertTrue(info["self_signed"])

    def test_hostname_is_checked(self):
        with patch("run") as run:
            for name in ("", "-nas", "nas-", "NAS", "my_nas", "a" * 64, "nas.local"):
                with self.subTest(name), self.assertRaises(fnas.FnasError):
                    fnas.set_hostname(name)
            run.assert_not_called()

    def test_unknown_time_zone(self):
        with patch("run", return_value=SimpleNamespace(stdout="Europe/London\nUTC\n")) as run, \
                self.assertRaisesRegex(fnas.FnasError, "unknown time zone"):
            fnas.set_timezone("Mars/Olympus")
        self.assertEqual(run.call_count, 1)

    def test_power_needs_the_phrase(self):
        with patch("run") as run:
            with self.assertRaisesRegex(fnas.FnasError, '"restart the NAS"'):
                fnas.power("restart", "yes")
            run.assert_not_called()
            fnas.power("shutdown", "shut down the NAS")
        self.assertEqual(run.call_args.args[-2:], ("systemctl", "poweroff"))


class Engines(unittest.TestCase):
    def test_every_engine_is_listed_built_or_not(self):
        with patch("unavailable", lambda: ""):
            found = fnas.engines()
        self.assertEqual([(e["name"], e["title"], e["available"]) for e in found],
                         [("zfs", "ZFS", True), ("mergerfs", "Unraid-style", True),
                          ("mdadm", "Traditional RAID", True), ("btrfs", "Btrfs", False)])
        self.assertEqual(found[3]["reason"], "Not built yet (Phase 17)")

    def test_an_unbuilt_engine_says_so(self):
        with patch("unavailable", lambda: ""), self.assertRaisesRegex(fnas.FnasError, "Btrfs can't be used "
                                                                       r"here: Not built yet \(Phase 17\)"):
            fnas.engine_named("btrfs")
        with self.assertRaisesRegex(fnas.FnasError, "no such engine"):
            fnas.engine_named("lvm")

    def test_an_array_engine_says_why_it_cant_be_used(self):
        with mock.patch.object(mdadm, "unavailable", lambda: "mdadm isn't installed"), \
                self.assertRaisesRegex(fnas.FnasError, "Traditional RAID can't be used here: "
                                                            "mdadm isn't installed"):
            fnas.engine_named("mdadm")

    def test_zfs_pools_show_the_room_files_can_use(self):
        # A RAIDZ2 of four 4 GiB disks: zpool counts 16 GiB, files get about 8
        def run(*command, check=True, stdin=None, timeout=None):
            if command[:2] == ("zpool", "list"):
                return SimpleNamespace(stdout="tank\t16000\t40\t15960\tONLINE\toff\n", returncode=0)
            if command[:2] == ("zfs", "list"):
                return SimpleNamespace(stdout="tank\t20\t7980\n", returncode=0)
            raise AssertionError(command)
        with mock.patch.object(zfs, "run", run):
            pool, = zfs.list_pools()
        self.assertEqual((pool["size"], pool["allocated"], pool["free"]), (8000, 20, 7980))

    def test_zfs_says_why_it_cant_be_used(self):
        with patch("unavailable", lambda: "Secure Boot is on"):
            self.assertEqual(fnas.engines()[0]["reason"], "Secure Boot is on")

    def test_only_zfs_rejects_its_reserved_words(self):
        # Other engines may happily have a pool called "mirror"
        fnas.check_pool_name("mirror")
        with self.assertRaisesRegex(fnas.FnasError, "starts with a word ZFS reserves"):
            fnas.check_reserved_name("mirror")


class Mdadm(unittest.TestCase):
    """Traditional RAID: the layout maths, and reading what the kernel says."""

    engine = importlib.import_module("fellowship_nas.engines.mdadm")

    MDSTAT = """Personalities : [raid1] [raid5] [raid6]
md127 : active raid5 sdd1[3] sdc1[1] sdb1[0]
      7813772288 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/2] [UU_]
      [=>...................]  recovery = 8.3% (652144896/7813772288) finish=120.4min

md0 : inactive sda1[0](F)
      1953382464 blocks super 1.2

unused devices: <none>
"""

    SCAN = """ARRAY /dev/md/tank metadata=1.2 name=gandolf:tank UUID=1a2b:3c4d
ARRAY /dev/md/old metadata=1.2 name=gandolf:old UUID=5e6f:7a8b
"""

    def options(self, count, size=4 * GB):
        return self.engine.layouts([size] * count)

    def test_mdadm_and_the_kernel_name_an_array_differently(self):
        # mdadm --detail --scan says /dev/md/tank, a link to the /dev/md127 that /proc/mdstat lists
        output = SimpleNamespace(stdout=self.SCAN, stderr="", returncode=0)
        links = {"/dev/md/tank": "/dev/md127", "/dev/md/old": "/dev/md0"}
        with patch("run", lambda *command, check=True: output), \
                mock.patch.object(self.engine.os.path, "realpath", side_effect=lambda path: links.get(path, path)):
            self.assertEqual(self.engine.array_names(), {"/dev/md127": "tank", "/dev/md0": "old"})

    def test_checks_before_the_first_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.engine, "CHECKS", os.path.join(directory, "checks.json")):
            self.assertEqual(self.engine.last_scrub("vault"), 0)
            self.engine.record_check("vault", 1234)
            self.assertEqual(self.engine.last_scrub("vault"), 1234)

    def test_an_arrays_shares_are_its_share_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory, "vault", "shares", "docs"))
            open(os.path.join(directory, "vault", "shares", "stray-file"), "w").close()
            with patch("ROOT", directory):
                found = self.engine.share_volumes("vault")
        self.assertEqual(found, {"docs": {"dataset": "vault/shares/docs", "readonly": False,
                                          "path": os.path.join(directory, "vault", "shares", "docs")}})

    def test_every_engine_describes_itself_for_the_desktop(self):
        pools = importlib.import_module("fellowship_nas.pools")
        zfs = importlib.import_module("fellowship_nas.engines.zfs")
        keys = set(zfs.PRESENTATION)
        self.assertEqual(set(self.engine.PRESENTATION), keys)
        self.assertEqual(set(mergerfs.PRESENTATION), keys)
        with mock.patch.object(zfs, "unavailable", lambda: ""), \
                mock.patch.object(self.engine, "unavailable", lambda: "mdadm isn't installed"):
            shown = {engine["name"]: engine for engine in pools.engines()}
        self.assertEqual(list(shown), ["zfs", "mergerfs", "mdadm", "btrfs"])
        self.assertTrue(all(engine["summary"] for engine in shown.values()))
        self.assertEqual(shown["mdadm"]["check_button"], "Check array")
        self.assertEqual([f["name"] for f in shown["mdadm"]["filesystems"] if f["recommended"]], ["xfs"])
        # An engine not built yet has only its name, state and summary
        self.assertNotIn("grow", shown["btrfs"])
        self.assertEqual(pools.engine_title("mdadm"), "Traditional RAID")

    def test_an_array_missing_disks_is_degraded(self):
        # A disk that has gone isn't listed as a member at all, failed or not; only the count shows it
        mdstat = ("Personalities : [raid6]\nmd127 : active raid6 vdd[2] vdb[0]\n"
                  "      8378368 blocks super 1.2 level 6, 512k chunk, algorithm 2 [4/2] [U_U_]\n"
                  "      bitmap: 0/1 pages [0KB], 65536KB chunk\n\nunused devices: <none>\n")
        with mock.patch.object(self.engine, "read", lambda path: mdstat):
            array = self.engine.arrays()["/dev/md127"]
        self.assertEqual((array["wanted"], array["working"]), (4, 2))
        self.assertEqual(self.engine.health(array), "DEGRADED")
        self.assertEqual(self.engine.health({**array, "working": 4}), "ONLINE")

    def test_a_gone_disk_is_named_by_what_was_in_its_slot(self):
        links = {"/dev/vdb": "/dev/disk/by-id/virtio-DATA-1", "/dev/vdc": "/dev/disk/by-id/virtio-DATA-2"}
        present = [{"slot": 0, "raid_device": 0, "state": "active sync", "path": "/dev/vdb"},
                   {"slot": 1, "raid_device": 1, "state": "active sync", "path": "/dev/vdc"}]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.engine, "SLOTS", os.path.join(directory, "slots.json")):
            self.engine.remember_slots("r6", present, links)
            gone = [present[0], {"slot": None, "raid_device": 1, "state": "removed", "path": ""}]
            self.engine.remember_slots("r6", gone, links)
            shown = self.engine.describe_disk(gone[1], links)
        self.assertEqual((shown["name"], shown["path"], shown["state"]), ("/dev/disk/by-id/virtio-DATA-2", "", "FAULTED"))

    def test_the_notice_says_how_much_protection_is_left(self):
        self.assertEqual(self.engine.broken_status("raid6", 4, 0), "")
        self.assertIn("less protection, until it is replaced", self.engine.broken_status("raid6", 4, 1))
        two = self.engine.broken_status("raid6", 4, 2)
        self.assertIn("no protection left: one more failure loses it. Replace them now", two)
        self.assertIn("no protection left", self.engine.broken_status("raid5", 4, 1))
        self.assertIn("less protection", self.engine.broken_status("mirror", 3, 1))

    def test_saved_arrays_carry_no_spare_count(self):
        scan = "ARRAY /dev/md/vault metadata=1.2 spares=1 UUID=d6a1:3cf5\n"
        with tempfile.TemporaryDirectory() as directory:
            conf = os.path.join(directory, "mdadm.conf")
            with open(conf, "w") as file:
                file.write("MAILADDR root\nARRAY /dev/md/gone UUID=1\n")
            with patch("run", lambda *command, check=True: SimpleNamespace(stdout=scan, stderr="", returncode=0)), \
                    mock.patch.object(self.engine, "MDADM_CONF", conf):
                self.engine.save_conf()
            with open(conf) as file:
                self.assertEqual(file.read(), "MAILADDR root\nPROGRAM /usr/lib/fellowship-nas/md-alert\n"
                                              "ARRAY /dev/md/vault metadata=1.2 UUID=d6a1:3cf5\n")

    def test_array_events_reach_alerts_after_an_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = os.path.join(directory, "mdadm.conf")
            with open(conf, "w") as file:
                file.write("MAILADDR root\nPROGRAM /bin/old\nARRAY /dev/md/vault UUID=1\n")
            ran = []
            with patch("run", lambda *command, check=True: ran.append(command) or SimpleNamespace(
                        stdout="", stderr="", returncode=0)), \
                    mock.patch.object(self.engine, "MDADM_CONF", conf), \
                    mock.patch.object(self.engine, "mdadm_installed", lambda: True):
                self.engine.watch_events()
                with open(conf) as file:
                    self.assertEqual(file.read(), "MAILADDR root\nPROGRAM /usr/lib/fellowship-nas/md-alert\n"
                                                  "ARRAY /dev/md/vault UUID=1\n")
                self.assertIn(("systemctl", "try-restart", "mdmonitor.service"), ran)
                # Already set up: nothing is rewritten or restarted
                ran.clear()
                self.engine.watch_events()
                self.assertEqual(ran, [])

    def mdcheck(self, directory, state):
        """Run Debian's mdcheck hooks against arrays in these states: name -> (sync_action, paused)."""
        def md_file(name, field):
            action, _ = state[name]
            return {"sync_action": action, "last_sync_action": "check", "mismatch_cnt": "0"}[field]
        sent = []
        return sent, contextlib.ExitStack(), [
            mock.patch.object(self.engine, "CHECKS", os.path.join(directory, "checks.json")),
            mock.patch.object(self.engine, "array_names", lambda: {f"/dev/{n}": n for n in state}),
            mock.patch.object(self.engine, "md_file", md_file),
            mock.patch.object(self.engine, "mdcheck_paused", lambda name: state[name][1]),
            mock.patch.object(self.engine.alerts, "send_alert", lambda *alert: sent.append(alert)),
        ]

    def test_health_sees_the_checks_debian_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {"vault": ("idle", False), "busy": ("resync", False)}
            sent, stack, patches = self.mdcheck(directory, state)
            with stack:
                for each in patches:
                    stack.enter_context(each)
                # The first night: mdcheck takes every idle array, then pauses after six hours
                self.engine.mdcheck_starting()
                state["vault"] = ("idle", True)
                self.engine.mdcheck_stopped(now=100)
                self.assertEqual(self.engine.last_scrub("vault"), 0)
                self.assertEqual(sent, [])
                # The next night it carries on and finishes
                self.engine.mdcheck_starting(resuming=True)
                state["vault"] = ("idle", False)
                self.engine.mdcheck_stopped(now=200)
                self.assertEqual(self.engine.last_scrub("vault"), 200)
                self.assertEqual(self.engine.last_scrub("busy"), 0)
                self.assertEqual([alert[0] for alert in sent], ["info"])
                # A later continuation with nothing paused records nothing new
                self.engine.mdcheck_starting(resuming=True)
                self.engine.mdcheck_stopped(now=300)
                self.assertEqual(self.engine.last_scrub("vault"), 200)

    def test_only_a_checks_mismatches_are_a_problem(self):
        values = {"last_sync_action": "check", "mismatch_cnt": "128"}
        with mock.patch.object(self.engine, "md_file", lambda name, field: values[field]):
            self.assertEqual(self.engine.mismatches("vault"), 128)
            values["last_sync_action"] = "repair"
            self.assertEqual(self.engine.mismatches("vault"), 0)

    def best(self, count):
        option = self.options(count)[0]
        self.assertTrue(option["recommended"])
        return option["layout"], option["width"], option["groups"], option["spares"]

    def test_recommendations_for_any_number_of_disks(self):
        self.assertEqual(self.best(2), ("mirror", 2, 1, 0))
        self.assertEqual(self.best(3), ("raid5", 3, 1, 0))
        self.assertEqual(self.best(6), ("raid5", 6, 1, 0))
        self.assertEqual(self.best(8), ("raid6", 8, 1, 0))
        self.assertEqual(self.best(16), ("raid6", 16, 1, 0))
        self.assertIsNone(self.engine.recommended(1))
        self.assertEqual(self.options(1), [])

    def test_an_array_is_one_group_of_every_disk(self):
        for count in range(2, 17):
            for option in self.options(count):
                with self.subTest(count=count, option=option):
                    self.assertEqual(option["groups"], 1)
                    self.assertEqual(option["width"] + option["spares"], count)
                    low, high = self.engine.WIDTHS[option["layout"]]
                    self.assertTrue(low <= option["width"] <= high)
        self.assertNotIn("raid6", {o["layout"] for o in self.options(3)})
        self.assertNotIn("raid10", {o["layout"] for o in self.options(2)})

    def test_usable_space_and_survival(self):
        four = self.options(4)
        for layout, usable, survives in (("raid5", 3 * 4 * GB, 1), ("raid6", 2 * 4 * GB, 2),
                                         ("raid10", 2 * 4 * GB, 1)):
            option = next(o for o in four if o["layout"] == layout and not o["spares"])
            with self.subTest(layout=layout):
                self.assertEqual((option["usable"], option["survives"]), (usable, survives))
        # A mirror of two disks with the other two waiting as spares
        mirror = next(o for o in four if o["layout"] == "mirror")
        self.assertEqual((mirror["width"], mirror["spares"], mirror["usable"]), (2, 2, 4 * GB))

    def test_mixed_sizes_keep_alike_disks_together_and_spare_the_largest(self):
        sizes = [4 * GB, 8 * GB, 4 * GB, 8 * GB]
        groups, spares = self.engine.plan("mirror", sizes, 2, 2)
        self.assertEqual([[sizes[i] for i in g] for g in groups], [[4 * GB, 4 * GB]])
        self.assertEqual(sorted(sizes[i] for i in spares), [8 * GB, 8 * GB])
        option = next(o for o in self.options(4) if o["layout"] == "raid5" and not o["spares"])
        self.assertEqual(option["unused"], 0)
        mixed = next(o for o in self.engine.layouts([4 * GB, 8 * GB, 4 * GB, 8 * GB])
                     if o["layout"] == "raid5" and not o["spares"])
        self.assertEqual(mixed["unused"], 2 * 4 * GB)

    def test_a_layout_that_does_not_fit_says_so(self):
        with self.assertRaisesRegex(fnas.FnasError, "mdadm offers"):
            self.engine.plan("raidz2", [4 * GB] * 4)
        with self.assertRaisesRegex(fnas.FnasError, "raid6 takes 4 to 16 disks"):
            self.engine.plan("raid6", [4 * GB] * 3)
        with self.assertRaisesRegex(fnas.FnasError, "an array uses every disk it has"):
            self.engine.plan("mirror", [4 * GB] * 5, 2)

    def test_creating_an_array(self):
        commands = []

        def fake(*command, check=True, stdin=None, timeout=None):
            commands.append(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        disks_in = [{"by_id": f"/dev/disk/by-id/ata-{n}-part1", "path": f"/dev/sd{n}1",
                     "size": 4 * GB, "state": "empty", "notes": []} for n in "bcd"]
        with mock.patch.object(self.engine, "run", fake), \
                mock.patch.object(self.engine.disks, "choose", lambda wanted, wipe: disks_in), \
                mock.patch.object(self.engine.disks, "check_confirmation", lambda chosen, confirm: None), \
                mock.patch.object(self.engine.disks, "prepare", lambda chosen, wipe: None), \
                mock.patch.object(self.engine.pools, "check_new_name", lambda name: None), \
                mock.patch.object(self.engine, "save_conf", lambda: None), \
                mock.patch.object(self.engine, "mount_pool", lambda *a: None), \
                mock.patch.object(self.engine.os, "makedirs", lambda *a, **k: None):
            self.engine.create_pool("tank", "raid5", ["a", "b", "c"], "erase 3 disks", False)
        create = next(c for c in commands if c[0] == "mdadm")
        self.assertEqual(create[:2], ("mdadm", "--create"))
        self.assertIn("--level=5", create)
        self.assertIn("--raid-devices=3", create)
        self.assertNotIn("--layout=n2", create)
        # The array is made, then XFS is put on it
        self.assertEqual(next(c for c in commands if c[0] == "mkfs.xfs")[-1], "/dev/md/tank")

    def test_raid10_uses_two_copies_and_ext4_when_asked(self):
        commands = []

        def fake(*command, check=True, stdin=None, timeout=None):
            commands.append(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        disks_in = [{"by_id": f"/dev/disk/by-id/ata-{n}-part1", "path": f"/dev/sd{n}1",
                     "size": 4 * GB, "state": "empty", "notes": []} for n in "bcde"]
        with mock.patch.object(self.engine, "run", fake), \
                mock.patch.object(self.engine.disks, "choose", lambda wanted, wipe: disks_in), \
                mock.patch.object(self.engine.disks, "check_confirmation", lambda chosen, confirm: None), \
                mock.patch.object(self.engine.disks, "prepare", lambda chosen, wipe: None), \
                mock.patch.object(self.engine.pools, "check_new_name", lambda name: None), \
                mock.patch.object(self.engine, "save_conf", lambda: None), \
                mock.patch.object(self.engine, "mount_pool", lambda *a: None), \
                mock.patch.object(self.engine.os, "makedirs", lambda *a, **k: None):
            self.engine.create_pool("tank", "raid10", ["a", "b", "c", "d"], "erase 4 disks",
                                    False, filesystem="ext4")
        create = next(c for c in commands if c[0] == "mdadm")
        self.assertIn("--level=10", create)
        self.assertIn("--layout=n2", create)
        self.assertTrue(any(c[0] == "mkfs.ext4" for c in commands))

    def test_a_filesystem_must_be_one_we_can_make(self):
        with self.assertRaisesRegex(fnas.FnasError, "no such filesystem: btrfs"):
            self.engine.check_filesystem("btrfs")
        self.assertEqual(self.engine.check_filesystem(None), "xfs")

    def test_reading_an_array_detail(self):
        detail = """/dev/md127:
           Version : 1.2
        Raid Level : raid5
     Raid Devices : 3
            State : clean, degraded
            Name : gandolf:tank
            UUID : 1a2b:3c4d

    Number   Major   Minor   RaidDevice State
       0       8       17        0      active sync   /dev/sdb1
       1       8       33        1      active sync   /dev/sdc1
       -       0        0        2      removed
       2       8       49        -      spare   /dev/sdd1
"""
        output = SimpleNamespace(stdout=detail, stderr="", returncode=0)
        with mock.patch.object(self.engine, "array_device", lambda name: "/dev/md127"), \
                mock.patch.object(self.engine, "run", lambda *c, check=True: output):
            found = self.engine.detail("tank")
        self.assertEqual(found["fields"]["raid level"], "raid5")
        self.assertEqual([(r["raid_device"], r["state"], r["path"]) for r in found["rows"]],
                         [(0, "active sync", "/dev/sdb1"), (1, "active sync", "/dev/sdc1"),
                          (2, "removed", ""), (None, "spare", "/dev/sdd1")])

    def test_a_failed_disk_shows_in_its_slot_not_as_a_spare(self):
        # What mdadm --detail says after mdadm --fail: the slot is "removed", the disk "faulty" outside it
        rows = [{"slot": 0, "raid_device": 0, "state": "active sync", "path": "/dev/vdf"},
                {"slot": None, "raid_device": 2, "state": "removed", "path": ""},
                {"slot": 2, "raid_device": None, "state": "faulty", "path": "/dev/vdh"},
                {"slot": 3, "raid_device": None, "state": "spare", "path": "/dev/vdj"}]
        detail = {"fields": {"raid level": "raid5"}, "rows": rows}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with mock.patch.object(self.engine, "SLOTS", os.path.join(directory.name, "slots.json")), \
                mock.patch.object(self.engine, "detail", lambda name: detail), \
                mock.patch.object(self.engine, "array_device", lambda name: "/dev/md127"), \
                mock.patch.object(self.engine, "sync_from", lambda device: None), \
                mock.patch.object(self.engine, "missed", lambda device: 0), \
                patch("find_pool", lambda name: {"name": name}), patch("by_id_links", lambda partitions=False: {}):
            status = self.engine.pool_status("vault")
        self.assertEqual([(d["path"], d["state"]) for d in status["groups"][0]["disks"]],
                         [("/dev/vdf", "ONLINE"), ("/dev/vdh", "FAULTED")])
        self.assertEqual([d["path"] for d in status["spares"]], ["/dev/vdj"])
        self.assertIn("1 disk failed", status["status"])

    def test_a_pool_status_names_its_disks_and_spares(self):
        status = {"groups": [{"disks": [{"path": "/dev/sdb1"}, {"path": "/dev/sdc1"}]}],
                  "spares": [{"path": "/dev/sdd1"}]}
        self.assertEqual([d["path"] for d in self.engine.member_disks(status)],
                         ["/dev/sdb1", "/dev/sdc1"])

    def test_a_repair_needs_asking_for_it(self):
        with self.assertRaisesRegex(fnas.FnasError, "confirm by passing --confirm \"repair tank\""):
            self.engine.repair_pool("tank", "")

    def test_growing_an_array_by_one_disk(self):
        status = {"groups": [{"type": "raid5",
                            "disks": [{"path": f"/dev/sd{n}1", "size": 4 * GB} for n in "bcd"]}],
                  "spares": []}
        commands = []

        def fake(*command, check=True, stdin=None, timeout=None):
            commands.append(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        disks_in = [{"by_id": "/dev/disk/by-id/ata-e-part1", "path": "/dev/sde1",
                     "size": 4 * GB, "state": "empty", "notes": []}]
        with mock.patch.object(self.engine, "pool_status", lambda name: status), \
                mock.patch.object(self.engine, "array_device", lambda name: "/dev/md/tank"), \
                mock.patch.object(self.engine, "run", fake), \
                mock.patch.object(self.engine.disks, "choose", lambda wanted, wipe: disks_in), \
                mock.patch.object(self.engine.disks, "check_confirmation", lambda chosen, confirm: None), \
                mock.patch.object(self.engine.disks, "prepare", lambda chosen, wipe: None), \
                mock.patch.object(self.engine.jobs, "start", lambda kind, title, pool=None:
                                  {"kind": kind, "title": title, "pool": pool}):
            job = self.engine.grow("tank", "expand", ["e"], "erase 1 disk", False)
        self.assertEqual(job["kind"], "expand")
        self.assertIn(("mdadm", "--grow", "/dev/md/tank", "--raid-devices=4"), commands)

    def test_an_array_has_no_groups_to_add(self):
        status = {"groups": [{"type": "raid5", "disks": [{"path": "/dev/sdb1"}]}], "spares": []}
        with mock.patch.object(self.engine, "pool_status", lambda name: status), \
                mock.patch.object(self.engine, "array_device", lambda name: "/dev/md/tank"), \
                mock.patch.object(self.engine.disks, "choose", lambda wanted, wipe: []), \
                self.assertRaisesRegex(fnas.FnasError, "an array is a single group"):
            self.engine.grow("tank", "group", [], "", False)

    def test_reading_the_kernel_arrays(self):
        with mock.patch.object(self.engine, "read", lambda path: self.MDSTAT):
            found = self.engine.arrays()
        self.assertEqual(sorted(found), ["/dev/md0", "/dev/md127"])
        raid = found["/dev/md127"]
        self.assertEqual((raid["state"], raid["members"][0]["device"]), ("active", "/dev/sdd1"))
        self.assertEqual([m["slot"] for m in raid["members"]], [3, 1, 0])
        self.assertEqual(raid["sync"]["function"], "recovery")
        self.assertAlmostEqual(raid["sync"]["progress"], 0.083)
        # A recovery with every disk present is building; a failed disk is degraded
        self.assertEqual(self.engine.health(raid), "BUILDING")
        self.assertEqual(found["/dev/md0"]["members"][0]["state"], "failed")
        self.assertIsNone(found["/dev/md0"]["sync"])
        self.assertEqual(self.engine.health(found["/dev/md0"]), "DEGRADED")

    def test_a_healthy_array(self):
        mdstat = "md0 : active raid1 sdb1[1] sda1[0]\n  1953382464 blocks super 1.2 [2/2] [UU]\n"
        with mock.patch.object(self.engine, "read", lambda path: mdstat):
            raid = self.engine.arrays()["/dev/md0"]
        self.assertEqual(self.engine.health(raid), "ONLINE")

    def test_a_new_array_writing_parity_is_building_not_degraded(self):
        # What the kernel shows for a freshly created raid5: [4/3] while it
        # writes parity, every disk present, none failed. mdadm calls that
        # degraded and its monitor says DegradedArray; nobody should be woken.
        mdstat = ("md127 : active raid5 sde[4] sdd[2] sdc[1] sdb[0]\n"
                  "      6282240 blocks super 1.2 level 5, 512k chunk, algorithm 2 [4/3] [UUU_]\n"
                  "      [=>...................]  resync = 8.3% (522/6282) finish=1.2min speed=1024K/sec\n")
        with mock.patch.object(self.engine, "read", lambda path: mdstat):
            array = self.engine.arrays()["/dev/md127"]
        self.assertEqual((array["wanted"], array["working"]), (4, 3))
        self.assertEqual(self.engine.health(array), "BUILDING")
        done = "\n".join(line for line in mdstat.replace("[4/3] [UUU_]", "[4/4] [UUUU]").splitlines()
                         if "resync =" not in line)
        with mock.patch.object(self.engine, "read", lambda path: done):
            self.assertEqual(self.engine.health(self.engine.arrays()["/dev/md127"]), "ONLINE")

    def test_a_check_running_leaves_a_healthy_array_online(self):
        mdstat = ("md0 : active raid5 sdc[2] sdb[1] sda[0]\n"
                  "      1953382464 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/3] [UUU]\n"
                  "      [==>..................]  check = 12.0% (234/1953) finish=2.0min speed=2048K/sec\n")
        with mock.patch.object(self.engine, "read", lambda path: mdstat):
            self.assertEqual(self.engine.health(self.engine.arrays()["/dev/md0"]), "ONLINE")

    def test_building_raises_no_finding_but_a_lost_disk_raises_critical(self):
        self.assertEqual(fnas.health_findings([{"name": "tank", "engine": "mdadm", "health": "BUILDING"}]), [])
        engine = SimpleNamespace(pool_status=lambda name: {},
                                 member_disks=lambda status: [{"state": "FAULTED", "path": "/dev/sdb"}])
        with mock.patch.object(fnas.pools, "ENGINES", {"mdadm": engine}), \
                mock.patch.object(fnas.disks, "bays", lambda: {}):
            found = fnas.health_findings([{"name": "tank", "engine": "mdadm", "health": "DEGRADED"}])
        self.assertEqual([(f["severity"], f["title"]) for f in found],
                         [("critical", "Pool tank is degraded")])

    def test_reading_what_mdadm_can_assemble(self):
        output = SimpleNamespace(stdout=self.SCAN, stderr="", returncode=0)
        with mock.patch.object(self.engine, "run", lambda *command, check=True: output):
            found = self.engine.scan_arrays()
        self.assertEqual([(a["name"], a["device"], a["uuid"]) for a in found],
                         [("tank", "/dev/md/tank", "1a2b:3c4d"), ("old", "/dev/md/old", "5e6f:7a8b")])

    def test_a_building_array_keeps_its_name_when_scan_leaves_it_out(self):
        # While a new array writes parity, --detail --scan counts the disk being
        # written as a spare and prints no name; --detail still has it
        scan = "ARRAY /dev/md0 metadata=1.2 spares=1 UUID=1a2b:3c4d\n"
        detail = ("/dev/md0:\n   Name : fellowship:tank (local to host fellowship)\n"
                  "   State : clean, degraded, recovering\n")

        def run(*command, check=True, stdin=None):
            return SimpleNamespace(stdout=scan if "--scan" in command else detail, stderr="", returncode=0)
        with mock.patch.object(self.engine, "run", run):
            found = self.engine.scan_arrays()
        self.assertEqual([(a["name"], a["device"]) for a in found], [("tank", "/dev/md0")])

    def test_each_disk_is_mapped_to_its_array(self):
        links = {"/dev/sdb1": "/dev/disk/by-id/ata-B_1-part1"}
        with mock.patch.object(self.engine, "read", lambda path: self.MDSTAT), \
                mock.patch.object(self.engine, "scan_arrays", lambda: [{"device": "/dev/md127", "name": "tank"},
                                                                  {"device": "/dev/md0", "name": "old"}]), \
                mock.patch.object(self.engine.disks, "by_id_links", lambda partitions=False: links):
            devices = self.engine.pool_devices()
        self.assertEqual(devices["/dev/sdb1"], "tank")
        self.assertEqual(devices["/dev/disk/by-id/ata-B_1-part1"], "tank")
        self.assertEqual(devices["/dev/sda1"], "old")

    def test_says_why_it_cant_be_used(self):
        with mock.patch.object(self.engine, "mdadm_installed", lambda: False), \
                mock.patch.object(self.engine, "md_driver", lambda: True):
            self.assertEqual(self.engine.unavailable(), "mdadm isn't installed")
        with mock.patch.object(self.engine, "mdadm_installed", lambda: True), \
                mock.patch.object(self.engine, "md_driver", lambda: False):
            self.assertEqual(self.engine.unavailable(), "this kernel has no md driver")
        with mock.patch.object(self.engine, "mdadm_installed", lambda: True), \
                mock.patch.object(self.engine, "md_driver", lambda: True):
            self.assertEqual(self.engine.unavailable(), "")


class Services(unittest.TestCase):
    def state(self, shown):
        with patch("run", lambda *command, check=True: SimpleNamespace(stdout=shown, stderr="", returncode=0)):
            return fnas.unit_state("smbd.service")

    def test_unit_states(self):
        self.assertEqual(self.state("LoadState=loaded\nActiveState=active\nResult=success\n"), "running")
        self.assertEqual(self.state("LoadState=loaded\nActiveState=inactive\nResult=success\n"), "stopped")
        self.assertEqual(self.state("LoadState=loaded\nActiveState=inactive\nResult=exit-code\n"), "failed")
        self.assertEqual(self.state("LoadState=loaded\nActiveState=failed\nResult=exit-code\n"), "failed")
        self.assertEqual(self.state("LoadState=not-found\nActiveState=inactive\n"), "missing")

    def test_every_protocol_is_listed(self):
        share = {"name": "media", "smb": True, "nfs": False}
        with patch("list_shares", lambda: [share]), patch("unit_state", lambda unit: "running"):
            found = {p["name"]: p for p in fnas.protocols()}
        self.assertEqual(list(found), ["smb", "nfs", "sftp", "ftp", "webdav", "dlna", "iscsi"])
        self.assertEqual(found["smb"]["detail"], "Running, 1 share")
        self.assertEqual(found["nfs"]["detail"], "Running, no shares use it yet")
        self.assertEqual(found["ftp"]["state"], "not-built")
        self.assertEqual(found["dlna"]["state"], "later")


class Setup(unittest.TestCase):
    def test_setup_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory, patch("SETUP_FILE", f"{directory}/setup.json"):
            self.assertEqual(fnas.setup_status(), {"done": False})
            fnas.finish_setup(skipped=True)
            status = fnas.setup_status()
            self.assertTrue(status["done"])
            self.assertTrue(status["skipped"])


class Separation(unittest.TestCase):
    """Pieces of the migration to a separated NAS; the whole of it runs against
    real packages in nas/tests/integration/separate.sh."""

    def setUp(self):
        self.separate = {m.__name__: m for m in MODULES}["fellowship_nas.separate"]

    def test_a_shadow_hash_is_copied_as_it_is_and_a_lock_comes_off(self):
        usable = self.separate.usable_hash
        self.assertEqual(usable("$y$j9T$salt$hash"), "$y$j9T$salt$hash")
        self.assertEqual(usable("!$6$salt$hash"), "$6$salt$hash")
        for nothing in ("", "!", "*", "!*", "x"):
            self.assertEqual(usable(nothing), "")

    def test_samba_stops_syncing_to_linux_in_global_only(self):
        text = ("[global]\n   workgroup = WORKGROUP\n   unix password sync = yes\n"
                "[homes]\n   unix password sync = yes\n")
        out = self.separate.set_smb_option(text, "unix password sync", "no")
        self.assertEqual(out, "[global]\n   workgroup = WORKGROUP\n   unix password sync = no\n"
                              "[homes]\n   unix password sync = yes\n")
        missing = self.separate.set_smb_option("[global]\n   workgroup = W\n[t]\n", "pam password change", "no")
        self.assertIn("   pam password change = no\n[t]", missing)

    def test_a_retry_in_the_same_second_gets_its_own_backup(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with mock.patch.object(self.separate, "BACKUP_ROOT", directory.name):
            first = self.separate.new_stamp()
            os.makedirs(os.path.join(directory.name, first))
            second = self.separate.new_stamp()
            self.assertNotEqual(first, second)
            self.assertTrue(second.startswith(first))
            # Undo takes the newest: the suffixed retry sorts after its base
            self.assertEqual(sorted([first, second])[-1], second)

    def test_the_dry_run_says_what_needs_setting_and_that_dates_prove_nothing(self):
        found = {"runner": "chris", "root_has_password": False, "people": [
            {"name": "chris", "uid": 1000, "gid": 1000, "admin": True, "enabled": True, "groups": ["sudo"],
             "desktop": "carries over", "hash_method": "y", "smb": "needs setting", "leaves": ["sudo", "nas-admins"],
             "notes": []},
            {"name": "anna", "uid": 1001, "gid": 1001, "admin": False, "enabled": True, "groups": [],
             "desktop": "carries over", "hash_method": "y", "smb": "carries over", "leaves": [],
             "notes": ["possibly different passwords: the Linux one was changed after the Samba one"]}]}
        out = io.StringIO()
        self.separate.print_plan(found, out)
        text = out.getvalue()
        self.assertIn("SMB password      NEEDS SETTING: no Samba password exists", text)
        self.assertIn("(sudo ends for this account)", text)
        self.assertIn("set a root password (asked next)", text)
        self.assertIn("set a password for chris (you; asked next)", text)
        self.assertIn("can't prove two passwords match", text)
        self.assertIn("Nothing has changed", text)


class RecoveryKey(unittest.TestCase):
    """The recovery key: saved offline at install, opening FellowshipOS recovery
    with both admin passwords lost, and GRUB's password, so physical access
    alone can't edit a boot entry (docs/fellowship-nas-account-separation.md)."""

    DEBIAN_10_LINUX = """\
function gfxmode {
	set gfxpayload="${1}"
}
menuentry 'Debian GNU/Linux' --class debian --class gnu-linux --class gnu --class os $menuentry_id_option 'gnulinux-simple-abc' {
	load_video
	echo	'Loading Linux 6.12.117+deb13-amd64 ...'
	linux	/vmlinuz-6.12.117+deb13-amd64 root=/dev/mapper/fellowship-root ro  quiet
	initrd	/initrd.img-6.12.117+deb13-amd64
}
submenu 'Advanced options for Debian GNU/Linux' $menuentry_id_option 'gnulinux-advanced-abc' {
	menuentry 'Debian GNU/Linux, with Linux 6.12.117+deb13-amd64' --class debian $menuentry_id_option 'gnulinux-6.12-advanced-abc' {
		linux	/vmlinuz-6.12.117+deb13-amd64 root=/dev/mapper/fellowship-root ro  quiet
	}
	menuentry 'Debian GNU/Linux, with Linux 6.12.117+deb13-amd64 (recovery mode)' --class debian $menuentry_id_option 'gnulinux-6.12-recovery-abc' {
		linux	/vmlinuz-6.12.117+deb13-amd64 root=/dev/mapper/fellowship-root ro single
	}
}
"""

    def setUp(self):
        self.recovery = {m.__name__: m for m in MODULES}["fellowship_nas.recovery"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.calls = []

        def run(*command, check=True, stdin=None, **kw):
            self.calls.append((command, stdin))
            out = ""
            if command[0] == "grub-mkpasswd-pbkdf2":
                out = "Enter password:\nReenter password:\nPBKDF2 hash of your password is grub.pbkdf2.sha512.10000.AB.CD\n"
            if command[0] == "grub-probe":
                return SimpleNamespace(stdout="", stderr="", returncode=1)
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        paths = {"RECOVERY_HASH": "recovery-key.hash", "GRUB_USERS": "01_fellowship_recovery",
                 "GRUB_LINUX": "10_linux", "GRUB_LINUX_DEBIAN": "debian/10_linux.debian",
                 "USED": "recovery-used.json", "USED_LOG": "recovery-log.json"}
        for name, path in paths.items():
            setattr(self, name.lower(), os.path.join(self.dir, path))
        for patcher in [mock.patch.object(self.recovery, "run", run)] + \
                [mock.patch.object(self.recovery, name, os.path.join(self.dir, path)) for name, path in paths.items()]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_new_key_is_eight_groups_of_five_that_cant_be_misread(self):
        key = self.recovery.generate()
        self.assertRegex(key, r"^[A-Z2-9]{5}(-[A-Z2-9]{5}){7}$")
        self.assertFalse(set(key) & set("ILOU01"))
        self.assertNotEqual(key, self.recovery.generate())

    def test_a_key_is_read_however_it_was_typed(self):
        key = self.recovery.generate()
        for typed in (key, key.lower(), key.replace("-", ""), key.replace("-", " "), f"  {key}\n"):
            self.assertEqual(self.recovery.normalise(typed), key)
        for wrong in ("", key[:-1], key + "A", key.replace(key[0], "0", 1)):
            self.assertIsNone(self.recovery.normalise(wrong))

    def test_setting_a_key_keeps_only_its_hash_and_locks_grub_with_it(self):
        key = self.recovery.generate()
        self.recovery.set_key(key)
        with open(self.recovery_hash) as file:
            stored = file.read().strip()
        self.assertNotIn(key, stored)
        self.assertEqual(os.stat(self.recovery_hash).st_mode & 0o777, 0o600)
        self.assertTrue(self.recovery.check(key))
        self.assertTrue(self.recovery.check(key.lower().replace("-", " ")))
        self.assertFalse(self.recovery.check(self.recovery.generate()))
        # GRUB's password is the key, in GRUB's own PBKDF2 form
        self.assertIn(((("grub-mkpasswd-pbkdf2",), f"{key}\n{key}\n")), self.calls)
        with open(self.grub_users) as file:
            users = file.read()
        self.assertIn('set superusers="recovery"', users)
        self.assertIn("password_pbkdf2 recovery grub.pbkdf2.sha512.10000.AB.CD", users)
        self.assertNotIn(key, users)
        # Debian's 10_linux is moved aside, so an upgrade of grub keeps the wrapper
        self.assertIn(("dpkg-divert", "--local", "--rename", "--divert", self.grub_linux_debian, "--add",
                       self.grub_linux), [c for c, _ in self.calls])

    def wrapped(self):
        """What the wrapper makes of Debian's own entries, run for real."""
        os.makedirs(os.path.dirname(self.grub_linux_debian), exist_ok=True)
        with open(self.grub_linux_debian, "w") as file:
            file.write("#!/bin/sh\ncat << 'EOF'\n" + self.DEBIAN_10_LINUX + "EOF\n")
        os.chmod(self.grub_linux_debian, 0o755)
        self.recovery.divert_linux()
        import subprocess
        return subprocess.run(["sh", self.grub_linux], capture_output=True, text=True, check=True).stdout

    def test_every_normal_entry_starts_without_the_key(self):
        out = self.wrapped()
        normal = [line for line in out.splitlines() if re.match(r"\s*(menuentry|submenu) ", line)
                  and "FellowshipOS recovery" not in line]
        self.assertEqual(len(normal), 4)
        for line in normal:
            self.assertTrue(line.endswith(" --unrestricted {"), line)

    def test_recovery_is_an_entry_of_its_own_that_only_the_key_opens(self):
        out = self.wrapped()
        start = out.index("menuentry 'FellowshipOS recovery'")
        entry = out[start:out.index("\n}", start) + 2]
        first = entry.splitlines()[0]
        self.assertTrue(first.endswith(" --users recovery {"), first)
        self.assertNotIn("--unrestricted", first)
        self.assertIn("'fellowship-recovery-abc'", first)
        self.assertIn("ro  quiet systemd.unit=fellowship-recovery.target", entry)
        # Only one, and the normal entry is still there as it was
        self.assertEqual(out.count("FellowshipOS recovery"), 1)
        self.assertIn("menuentry 'Debian GNU/Linux' --class debian", out)

    def test_the_console_asks_for_the_key_and_waits_longer_after_each_wrong_one(self):
        key = self.recovery.generate()
        self.recovery.set_key(key)
        typed = iter(["WRONG", self.recovery.generate(), key])
        said, slept = [], []
        result = self.recovery.console_main(ask=lambda prompt: "5", secret=lambda prompt: next(typed),
                                            say=said.append, sleep=slept.append)
        self.assertEqual(result, "restart")
        self.assertEqual(slept, [2, 4])
        with open(self.used) as file:
            self.assertEqual([u["what"] for u in json.load(file)], ["opened recovery with the key"])

    def test_the_console_sets_roots_password_with_the_key(self):
        key = self.recovery.generate()
        self.recovery.set_key(key)
        answers = iter(["1", "5"])
        secrets = iter([key, "new root pw", "new root pw"])
        self.recovery.console_main(ask=lambda prompt: next(answers), secret=lambda prompt: next(secrets),
                                   say=lambda text: None, sleep=lambda seconds: None)
        self.assertIn((("chpasswd",), "root:new root pw\n"), self.calls)
        with open(self.used) as file:
            self.assertIn("set root's password", [u["what"] for u in json.load(file)])

    def test_without_a_key_recovery_opens_nothing(self):
        said = []
        result = self.recovery.console_main(ask=lambda prompt: self.fail("asked"),
                                            secret=lambda prompt: self.fail("asked for a key"),
                                            say=said.append, sleep=lambda seconds: None)
        self.assertEqual(result, "restart")
        self.assertIn("no recovery key", " ".join(said))

    def test_using_recovery_is_announced_at_the_next_start(self):
        alerted = []
        self.recovery.note_recovery("set root's password", now=1000)
        with mock.patch.object(fnas.alerts, "send_alert", lambda s, t, b: alerted.append((s, t))):
            self.recovery.announce_used()
        self.assertEqual(alerted, [("high", "Recovery was used at the console")])
        self.assertFalse(os.path.exists(self.used))
        with open(self.used_log) as file:
            self.assertEqual(len(json.load(file)), 1)


class MaintenanceInterface(unittest.TestCase):
    """With --caller (a NAS person, through the helper) fnas refuses root's
    commands itself and writes maintenance to the journal: the second lock
    behind the helper's list (docs/fellowship-nas-account-separation.md)."""

    HELPER = os.path.join(os.path.dirname(__file__), "..", "..", "packages", "fellowship-nas-web",
                          "internal", "helper", "helper.go")

    def setUp(self):
        self.maintenance = {m.__name__: m for m in MODULES}["fellowship_nas.maintenance"]
        self.cli = {m.__name__: m for m in MODULES}["fellowship_nas.cli"]
        self.calls, self.alerts = [], []

        def run(*command, check=True, stdin=None, **kw):
            self.calls.append(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        for patcher in (mock.patch.object(self.maintenance, "run", run),
                        mock.patch.object(fnas.alerts, "send_alert",
                                          lambda severity, title, body: self.alerts.append(title))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def every_command(self):
        """Every area and action the parser knows."""
        found = []
        parser = self.cli.build_parser()
        areas = next(a for a in parser._actions if a.dest == "area")
        for area, sub in areas.choices.items():
            actions = [a for a in sub._actions if getattr(a, "dest", None) == "action"]
            if not actions:
                found.append((area, ""))
            for action in (actions[0].choices if actions else []):
                found.append((area, action))
        return found

    def helper_list(self):
        """The helper's own list of what NAS admins may run, read from its source."""
        import re
        with open(self.HELPER) as file:
            text = file.read()
        block = text[text.index("var commands = map[string][]string{"):]
        block = block[:block.index("\n}\n")]
        found = set()
        for area, actions in re.findall(r'"([a-z-]+)":\s*(nil|\{[^}]*\})', block):
            if actions == "nil":
                found.add((area, ""))
            else:
                found.update((area, a) for a in re.findall(r'"([a-z-]+)"', actions))
        return found

    def test_what_the_helper_lets_through_is_never_root_only_here(self):
        for area, action in sorted(self.helper_list()):
            with self.subTest(f"{area} {action}"):
                self.assertNotEqual(self.maintenance.kind(area, action), "root")

    def test_what_fnas_lets_a_caller_reach_the_helper_also_lists(self):
        # The other way round: nothing here is open to a caller that the helper
        # wouldn't send, so the two lists can't drift apart unnoticed
        listed = self.helper_list()
        for area, action in self.every_command():
            if self.maintenance.kind(area, action) != "root":
                with self.subTest(f"{area} {action}"):
                    self.assertIn((area, action), listed)

    def test_root_only_commands_are_refused_to_a_caller(self):
        for argv in (["--caller", "kim", "auth", "check", "kim"], ["--caller", "kim", "auth", "keys", "kim"],
                     ["--caller", "kim", "job", "run", "0123456789ab"],
                     ["--caller", "kim", "alert", "send", "x", "y"]):
            with self.subTest(argv), mock.patch.object(fnas.os, "geteuid", return_value=0), \
                    contextlib.redirect_stderr(io.StringIO()) as err, \
                    mock.patch.object(self.cli, "dispatch") as dispatched:
                self.assertEqual(self.cli.main(argv), 1)
                self.assertIn("for root alone", err.getvalue())
                dispatched.assert_not_called()

    def test_root_itself_is_not_held_back(self):
        with mock.patch.object(fnas.os, "geteuid", return_value=0), \
                mock.patch.object(self.cli, "dispatch") as dispatched, \
                mock.patch.object(self.cli.config, "after_change"):
            self.assertEqual(self.cli.main(["auth", "check", "kim"]), 0)
        dispatched.assert_called_once()

    def test_maintenance_is_written_down_under_the_callers_name(self):
        with mock.patch.object(fnas.os, "geteuid", return_value=0), \
                mock.patch.object(self.cli, "dispatch"), mock.patch.object(self.cli.config, "after_change"):
            self.assertEqual(self.cli.main(["--caller", "kim", "system", "hostname", "nas2"]), 0)
            self.assertEqual(self.cli.main(["--caller", "kim", "system", "restart", "--confirm",
                                            "restart the NAS"]), 0)
            self.assertEqual(self.cli.main(["--caller", "kim", "--json", "pool", "list"]), 0)
        logged = [c for c in self.calls if c[0] == "logger"]
        self.assertEqual([c[-1] for c in logged],
                         ["kim asked for: system hostname nas2",
                          "kim asked for: system restart --confirm restart the NAS"])
        # A restart is one every admin hears about; a new name is in the journal only
        self.assertEqual(self.alerts, ["Maintenance by kim: system restart"])


class Network(unittest.TestCase):
    """The LAN address changes with a way back (phase 6): the waiting state,
    the rollback unit's work, the LAN subnet following, and ifupdown files
    that keep their IPv6 stanza exactly."""

    OLD = ("# Written by the FellowshipOS installer\nallow-hotplug veth1\n"
           "iface veth1 inet dhcp\niface veth1 inet6 auto\n")

    def setUp(self):
        self.network = {m.__name__: m for m in MODULES}["fellowship_nas.network"]
        self.shares = {m.__name__: m for m in MODULES}["fellowship_nas.shares"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.interfaces = os.path.join(self.root, "interfaces.d")
        os.makedirs(self.interfaces)
        with open(os.path.join(self.interfaces, "veth1"), "w") as file:
            file.write(self.OLD)
        self.lan_path = os.path.join(self.root, "nas-lan")
        with open(self.lan_path, "w") as file:
            file.write("10.99.0.0/24\n")
        self.resolv = os.path.join(self.root, "resolv.conf")
        with open(self.resolv, "w") as file:
            file.write("nameserver 10.99.0.1\n")
        self.state = os.path.join(self.root, "network-rollback")
        self.calls, self.alerts, self.configs = [], [], []
        self.addr = ("2: veth1    inet 10.99.0.2/24 brd 10.99.0.255 scope global veth1"
                     "\\       valid_lft forever preferred_lft forever")
        self.routes = "default via 10.99.0.1 dev veth1 proto dhcp metric 100"
        self.no_address = False
        self.lease_after = None
        self.polls = 0
        self.ifup_fails = False

        def run(*command, check=True, stdin=None, **kw):
            self.calls.append(command)
            ok = SimpleNamespace(stdout="", stderr="", returncode=0)
            if command[:4] == ("ip", "-4", "-o", "addr"):
                if self.no_address:
                    ok.stdout = ""
                elif self.lease_after is not None:
                    self.polls += 1
                    ok.stdout = self.addr if self.polls > self.lease_after else ""
                else:
                    ok.stdout = self.addr
            elif command == ("ip", "-4", "route", "show", "default"):
                ok.stdout = self.routes
            elif command[:5] == ("ip", "-4", "route", "show", "default"):
                ok.stdout = "default via 10.99.0.1 dev veth1"
            elif command[:3] == ("ip", "link", "show"):
                ok.returncode = 0 if command[3] in ("veth0", "veth1", "eth0") else 1
            elif command[0] == "ifup" and self.ifup_fails:
                ok.returncode, ok.stderr = 1, "ifup: failed to bring up veth1"
            if check and ok.returncode != 0:
                raise fnas.FnasError(ok.stderr.strip() or f"{command[0]} failed")
            return ok

        for name, value in (("INTERFACES_DIR", self.interfaces), ("RESOLV", self.resolv),
                            ("CONFIG_FILE", os.path.join(self.root, "nas-network.json")),
                            ("STATE", self.state), ("PENDING", os.path.join(self.state, "pending.json")),
                            ("BACKUP", os.path.join(self.state, "backup.json")),
                            ("LOCK", os.path.join(self.state, "lock")),
                            ("CONSOLE", os.path.join(self.root, "no-console")), ("LEASE_WAIT", 3)):
            patcher = mock.patch.object(self.network, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(self.network, "run", run),
                        mock.patch.object(self.shares, "LAN_FILE", self.lan_path),
                        mock.patch.object(self.shares, "write_config", lambda *a, **kw: self.configs.append(a)),
                        mock.patch.object(fnas.alerts, "send_alert",
                                          lambda severity, title, body: self.alerts.append(title)),
                        mock.patch.object(self.network.time, "sleep", lambda seconds: None),
                        # The harness world uses isc-dhcp-client; the image's
                        # dhcpcd has its own test below
                        mock.patch.object(self.network.shutil, "which",
                                          lambda name: "/sbin/dhclient" if name == "dhclient" else None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def file(self):
        with open(os.path.join(self.interfaces, "veth1")) as file:
            return file.read()

    def lan(self):
        with open(self.lan_path) as file:
            return file.read().strip()

    def apply_static(self, address="10.99.0.50/24"):
        gateway = str(next(ipaddress.ip_network(address, strict=False).hosts()))
        return self.network.apply("static", address, gateway, [gateway])

    def test_make_config_keeps_the_ipv6_stanza_exactly(self):
        with open(os.path.join(self.interfaces, "veth1"), "w") as file:
            file.write(self.OLD.replace("iface veth1 inet6 auto", "iface veth1 inet6 static\n    address fd00::5/64"))
        for mode, args in (("static", ("10.99.0.50/24", "10.99.0.1")), ("dhcp", ())):
            made = self.network.make_config("veth1", mode, *args)
            self.assertIn("iface veth1 inet6 static\n    address fd00::5/64", made)
            self.assertIn(f"iface veth1 inet {mode}", made)
        # A file with no IPv6 stanza gains none
        with open(os.path.join(self.interfaces, "veth1"), "w") as file:
            file.write("allow-hotplug veth1\niface veth1 inet dhcp\n")
        self.assertNotIn("inet6", self.network.make_config("veth1", "static", "10.99.0.50/24", "10.99.0.1"))

    def test_check_static_refuses_what_isnt_a_lan_address(self):
        for address, gateway, dns in (
                ("8.8.8.8/24", "8.8.8.1", ["8.8.8.1"]),          # public
                ("10.99.0.50/31", "10.99.0.49", ["10.99.0.1"]),  # prefix out of bounds
                ("10.99.0.0/24", "10.99.0.1", ["10.99.0.1"]),    # the network address
                ("10.99.0.255/24", "10.99.0.1", ["10.99.0.1"]),  # the broadcast address
                ("10.99.0.50/24", "10.50.0.1", ["10.99.0.1"]),   # gateway outside
                ("10.99.0.50/24", "10.99.0.50", ["10.99.0.1"]),  # gateway is the NAS
                ("10.99.0.50/24", "10.99.0.1", ["224.0.0.1"]),   # multicast DNS
                ("10.99.0.50/24", "10.99.0.1", []),              # no DNS
                ("nonsense", "10.99.0.1", ["10.99.0.1"])):
            with self.subTest(address=address, gateway=gateway, dns=dns), self.assertRaises(fnas.FnasError):
                self.network.check_static(address, gateway, dns)

    def test_apply_static_writes_the_file_and_waits(self):
        found = self.apply_static()
        self.assertEqual(found["address"], "10.99.0.50")
        self.assertIn("iface veth1 inet static", self.file())
        self.assertIn("    address 10.99.0.50/24", self.file())
        self.assertIn("iface veth1 inet6 auto", self.file())
        self.assertIn(("systemctl", "start", "fellowship-network-rollback.timer"), self.calls)
        self.assertIn(("ifup", "veth1"), self.calls)
        pending = fnas.read_json(self.network.PENDING)
        self.assertEqual(pending["address"], "10.99.0.50")
        self.assertGreater(pending["deadline"], fnas.time.time())
        with open(self.resolv) as file:
            self.assertEqual(file.read(), "nameserver 10.99.0.1\n")
        # Same subnet: the LAN setting is left alone
        self.assertEqual(self.lan(), "10.99.0.0/24")
        self.assertEqual(self.configs, [])

    def test_apply_follows_the_lan_and_rollback_restores_it(self):
        self.apply_static("10.50.0.50/24")
        self.assertEqual(self.lan(), "10.50.0.0/24")
        self.assertTrue(self.configs)
        self.assertTrue(self.network.rollback())
        self.assertEqual(self.lan(), "10.99.0.0/24")
        self.assertEqual(self.file(), self.OLD)
        with open(self.resolv) as file:
            self.assertEqual(file.read(), "nameserver 10.99.0.1\n")
        self.assertFalse(os.path.exists(self.network.PENDING))
        self.assertEqual(self.alerts, ["Network change rolled back"])
        # Nothing pending: the unit's work is a no-op, however often it runs
        self.assertFalse(self.network.rollback())
        self.assertEqual(self.alerts, ["Network change rolled back"])

    def test_a_second_change_is_refused_while_one_waits(self):
        self.apply_static()
        with self.assertRaises(fnas.FnasError):
            self.apply_static("10.99.0.51/24")

    def test_confirm_keeps_the_change_and_clears_the_wait(self):
        self.apply_static()
        self.network.confirm()
        self.assertFalse(os.path.exists(self.network.PENDING))
        self.assertIn(("systemctl", "stop", "fellowship-network-rollback.timer"), self.calls)
        self.assertIn("iface veth1 inet static", self.file())
        with self.assertRaises(fnas.FnasError):
            self.network.confirm()

    def test_a_failed_apply_puts_everything_back(self):
        self.ifup_fails = True
        with self.assertRaises(fnas.FnasError):
            self.apply_static("10.50.0.50/24")
        self.assertEqual(self.file(), self.OLD)
        self.assertEqual(self.lan(), "10.99.0.0/24")
        self.assertFalse(os.path.exists(self.network.PENDING))
        self.assertIn(("systemctl", "stop", "fellowship-network-rollback.timer"), self.calls)

    def test_dhcp_waits_for_the_lease_and_reports_it(self):
        self.addr = self.addr.replace("10.99.0.2/24", "10.99.0.100/24")
        self.lease_after = 2
        found = self.network.apply("dhcp")
        self.assertEqual(found["address"], "10.99.0.100")
        self.assertIn("iface veth1 inet dhcp", self.file())
        self.assertIn("iface veth1 inet6 auto", self.file())
        self.assertEqual(fnas.read_json(self.network.PENDING)["address"], "10.99.0.100")
        self.network.confirm()
        self.assertIn("iface veth1 inet dhcp", self.file())

    def test_dhcp_with_no_lease_restores_at_once(self):
        self.no_address = True
        with self.assertRaises(fnas.FnasError):
            self.network.apply("dhcp")
        self.assertEqual(self.file(), self.OLD)
        self.assertFalse(os.path.exists(self.network.PENDING))

    def test_the_lan_interface_is_picked_and_pinned(self):
        self.assertEqual(self.network.lan_interface(), ("veth1", "default-route"))
        self.routes = ("default via 10.99.0.1 dev veth1 proto dhcp metric 100\n"
                       "default via 10.99.0.1 dev veth0 proto dhcp metric 50")
        self.assertEqual(self.network.lan_interface(), ("veth0", "default-route"))
        self.routes = ""
        self.assertEqual(self.network.lan_interface(), ("veth1", "lan-subnet"))
        self.network.set_interface("veth0")
        self.assertEqual(self.network.lan_interface(), ("veth0", "override"))
        self.addr = self.addr.replace("10.99.0.2/24", "172.16.0.2/24")
        os.remove(os.path.join(self.root, "nas-network.json"))
        with self.assertRaises(fnas.FnasError):
            self.network.lan_interface()
        with self.assertRaises(fnas.FnasError):
            self.network.set_interface("no such name!")
        with self.assertRaises(fnas.FnasError):
            self.network.set_interface("veth9")

    def test_show_reports_the_change_that_waits(self):
        self.apply_static()
        shown = self.network.show()
        self.assertEqual(shown["interface"], "veth1")
        self.assertEqual(shown["mode"], "static")
        self.assertEqual(shown["pending"]["address"], "10.99.0.50")
        self.assertEqual(shown["lan"], "10.99.0.0/24")

    def test_retire_releases_the_lease_and_flushes_only_this_ports_ipv4(self):
        self.apply_static()
        self.assertIn(("dhclient", "-r", "-pf", "/run/dhclient.veth1.pid", "veth1"), self.calls)
        self.assertIn(("ip", "-4", "addr", "flush", "dev", "veth1"), self.calls)
        # Never IPv6, and never another port
        self.assertFalse([c for c in self.calls if c[:2] == ("ip", "-6")])
        self.assertFalse([c for c in self.calls if "flush" in c and c[-1] != "veth1"])
        self.assertFalse([c for c in self.calls if c[0] == "dhclient" and c[-1] != "veth1"])

    def test_rollback_retires_the_abandoned_method_too(self):
        self.apply_static()
        self.calls.clear()
        self.assertTrue(self.network.rollback())
        self.assertIn(("dhclient", "-r", "-pf", "/run/dhclient.veth1.pid", "veth1"), self.calls)
        self.assertIn(("ip", "-4", "addr", "flush", "dev", "veth1"), self.calls)

    def test_dhcp_apply_retires_before_bringing_the_port_up(self):
        self.addr = self.addr.replace("10.99.0.2/24", "10.99.0.100/24")
        self.network.apply("dhcp")
        flush = self.calls.index(("ip", "-4", "addr", "flush", "dev", "veth1"))
        ifup = self.calls.index(("ifup", "veth1"))
        self.assertLess(flush, ifup)
        self.network.confirm()
        self.assertIsNone(self.network.show()["pending"])

    def test_retire_releases_dhcpcd_when_that_is_the_client(self):
        with mock.patch.object(self.network.shutil, "which",
                               lambda name: "/sbin/dhcpcd" if name == "dhcpcd" else None):
            self.apply_static()
        self.assertIn(("dhcpcd", "-k", "veth1"), self.calls)
        self.assertFalse([c for c in self.calls if c[0] == "dhclient"])
        self.assertIn(("ip", "-4", "addr", "flush", "dev", "veth1"), self.calls)

    def test_no_dhcp_client_at_all_still_flushes_and_rolls_back(self):
        with mock.patch.object(self.network.shutil, "which", lambda name: None):
            self.apply_static()
            self.calls.clear()
            self.assertTrue(self.network.rollback())
        self.assertIn(("ip", "-4", "addr", "flush", "dev", "veth1"), self.calls)
        self.assertFalse(os.path.exists(self.network.PENDING))

    def test_the_locks_sort_the_network_commands(self):
        maintenance = {m.__name__: m for m in MODULES}["fellowship_nas.maintenance"]
        self.assertEqual(maintenance.kind("network", "show"), "read")
        self.assertEqual(maintenance.kind("network", "apply"), "maintenance")
        self.assertEqual(maintenance.kind("network", "confirm"), "maintenance")
        self.assertEqual(maintenance.kind("network", "rollback"), "root")
        self.assertEqual(maintenance.kind("network", "interface"), "root")
        self.assertIn(("network", "apply"), maintenance.ANNOUNCED)
        self.assertIn(("network", "confirm"), maintenance.ANNOUNCED)


class Updates(unittest.TestCase):
    """Updates are looked for every day and announced, and install only when asked."""

    SIMULATION = (
        "Reading package lists...\n"
        "Inst libc6 [2.41-12] (2.41-12+deb13u1 Debian-Security:13/stable-security [amd64])\n"
        "Inst zfs-dkms [2.3.9-0+deb13u1] (2.3.9-0+deb13u2 Debian:13.4/stable [all])\n"
        "Inst linux-image-6.12.120 (6.12.120-1 Debian-Security:13/stable-security [amd64])\n"
        "Conf libc6 (2.41-12+deb13u1 Debian-Security:13/stable-security [amd64])\n")

    def setUp(self):
        self.updates = {m.__name__: m for m in MODULES}["fellowship_nas.updates"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.calls, self.alerts = [], []
        self.simulation = self.SIMULATION

        def run(*command, check=True, stdin=None, **kw):
            self.calls.append(command)
            out = self.simulation if command[:2] == ("apt-get", "-s") else ""
            return SimpleNamespace(stdout=out, stderr="", returncode=0)
        for patcher in (mock.patch.object(self.updates, "run", run),
                        mock.patch.object(self.updates, "STATE", os.path.join(self.dir, "updates.json")),
                        mock.patch.object(self.updates, "REBOOT_FLAG", os.path.join(self.dir, "reboot-required")),
                        mock.patch.object(self.updates, "REBOOT_PACKAGES", os.path.join(self.dir, "reboot.pkgs")),
                        mock.patch.object(self.updates.alerts, "send_alert",
                                          lambda severity, title, body: self.alerts.append((severity, title)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_what_apt_would_install_is_read_from_its_simulation(self):
        found = self.updates.waiting(self.SIMULATION)
        self.assertEqual([(p["name"], p["from"], p["to"], p["security"]) for p in found],
                         [("libc6", "2.41-12", "2.41-12+deb13u1", True),
                          ("zfs-dkms", "2.3.9-0+deb13u1", "2.3.9-0+deb13u2", False),
                          ("linux-image-6.12.120", None, "6.12.120-1", True)])

    def test_checking_installs_nothing(self):
        self.updates.check(now=1000)
        self.assertFalse([c for c in self.calls if c[0] == "apt-get" and "-s" not in c and "update" not in c])

    def test_a_new_set_of_updates_is_announced_once(self):
        self.updates.check(now=1000)
        self.assertEqual(self.alerts, [("medium", "3 updates waiting, 2 for security")])
        self.updates.check(now=2000)
        self.assertEqual(len(self.alerts), 1)
        self.simulation = self.SIMULATION.replace("deb13u2", "deb13u3")
        self.updates.check(now=3000)
        self.assertEqual(len(self.alerts), 2)

    def test_nothing_waiting_is_not_announced(self):
        self.simulation = ""
        found = self.updates.check(now=1000)
        self.assertEqual((found["packages"], self.alerts), ([], []))

    def test_installing_upgrades_without_removing_anything(self):
        self.updates.install()
        installs = [c for c in self.calls if "upgrade" in c and "-s" not in c]
        self.assertEqual(len(installs), 1)
        self.assertIn("--with-new-pkgs", installs[0])
        self.assertNotIn("dist-upgrade", installs[0])
        self.assertEqual(self.alerts[-1], ("info", "Updates installed"))

    def test_a_needed_restart_is_said_and_never_done(self):
        with open(os.path.join(self.dir, "reboot-required"), "w"):
            pass
        with open(os.path.join(self.dir, "reboot.pkgs"), "w") as file:
            file.write("linux-image-6.12.120\nlibc6\n")
        found = self.updates.status()
        self.assertEqual(found["restart"], {"needed": True, "packages": ["libc6", "linux-image-6.12.120"]})
        self.updates.install()
        self.assertFalse([c for c in self.calls if c[0] in ("reboot", "shutdown", "systemctl")])


class ZfsSecureBoot(unittest.TestCase):
    """Whether ZFS is usable mustn't depend on mokutil, which nothing installs."""

    def setUp(self):
        self.zfs = {m.__name__: m for m in MODULES}["fellowship_nas.engines.zfs"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.var = os.path.join(directory.name, "SecureBoot")
        for patcher in (mock.patch.object(self.zfs, "SECURE_BOOT_VAR", self.var),
                        mock.patch.object(self.zfs, "MODULE_DIR", os.path.join(directory.name, "no-module")),
                        mock.patch.object(self.zfs, "run",
                                          lambda *c, check=True, stdin=None: SimpleNamespace(stdout="", returncode=1))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_secure_boot_is_read_from_the_firmware(self):
        for data, want in ((b"\x06\x00\x00\x00\x01", "Secure Boot is on"),
                           (b"\x06\x00\x00\x00\x00", "isn't installed")):
            with self.subTest(data), open(self.var, "wb") as file:
                file.write(data)
            self.assertIn(want, self.zfs.unavailable())

    def test_a_machine_without_uefi_just_has_no_module(self):
        self.assertIn("isn't installed", self.zfs.unavailable())


class SeparatedUsers(unittest.TestCase):
    """A separated NAS (docs/fellowship-nas-account-separation.md): people keep a
    UID for owning files, but their password lives in the NAS's store and Samba,
    never in Linux, and nobody gets a shell. Hashing goes through the real
    libcrypt, so these also check the ctypes route."""

    def setUp(self):
        Users.setUp(self)
        self.accounts_module = {m.__name__: m for m in MODULES}["fellowship_nas.accounts"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = os.path.join(directory.name, "accounts.json")
        tester_hash = self.accounts_module.hash_password("tester password")
        with open(self.store, "w") as file:
            json.dump({"version": 1, "people": {
                "tester": {"uid": 1000, "full_name": "Tester", "admin": True, "enabled": True, "sftp": False,
                           "hash": tester_hash, "password_set": 1, "must_change": False, "keys": []},
                "kim": {"uid": 1001, "full_name": "Kim", "admin": False, "enabled": True, "sftp": False,
                        "hash": "", "password_set": None, "must_change": False, "keys": []}}}, file)

        def by_name(name):
            for found in self.accounts:
                if found.pw_name == name:
                    return found
            raise KeyError(name)
        base_run = fnas.run

        def run(*command, check=True, stdin=None):
            # useradd here names the shell after --shell, not at a fixed place
            if command[0] == "useradd":
                self.calls.append((command, stdin))
                shell = command[command.index("--shell") + 1]
                self.accounts.append(SimpleNamespace(pw_name=command[-1], pw_uid=1003, pw_gid=1003,
                                                     pw_gecos="", pw_shell=shell))
                self.groups.append(SimpleNamespace(gr_name=command[-1], gr_gid=1003, gr_mem=[]))
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            return base_run(*command, check=check, stdin=stdin)
        for patcher in (mock.patch.object(self.accounts_module, "STORE", self.store),
                        mock.patch.object(self.accounts_module, "STORE_LOCK", self.store + ".lock"),
                        mock.patch.object(fnas.pwd, "getpwnam", by_name),
                        patch("run", run)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def commands(self):
        return [command for command, _ in self.calls]

    def stored(self, name):
        with open(self.store) as file:
            return json.load(file)["people"][name]

    def test_a_store_is_what_makes_the_nas_separated(self):
        self.assertTrue(self.accounts_module.is_separated())
        os.remove(self.store)
        self.assertFalse(self.accounts_module.is_separated())

    def test_people_come_from_the_store_with_their_unix_side_from_the_system(self):
        found = {u["name"]: u for u in fnas.list_users()}
        self.assertEqual(list(found), ["kim", "tester"])
        self.assertEqual((found["tester"]["kind"], found["tester"]["admin"], found["tester"]["password"]),
                         ("shares", True, True))
        self.assertEqual((found["kim"]["groups"], found["kim"]["password"]), (["family", "video"], False))

    def test_create_makes_a_uid_with_no_login_and_a_password_in_the_store_and_samba(self):
        with mock.patch.object(fnas.os.path, "isdir", return_value=True):
            fnas.create_user("sam", "Sam", "shares", False, ["family"], "long enough")
        commands = self.commands()
        self.assertIn(("useradd", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin",
                       "--comment", "Sam", "sam"), commands)
        # Nothing gives sam a Linux password
        self.assertFalse([c for c in commands if c[0] == "chpasswd"])
        self.assertIn((("smbpasswd", "-s", "-a", "sam"), "long enough\nlong enough\n"), self.calls)
        entry = self.stored("sam")
        self.assertEqual((entry["uid"], entry["admin"], entry["sftp"]), (1003, False, False))
        self.assertTrue(self.accounts_module.verify_password("long enough", entry["hash"]))
        self.assertTrue(entry["hash"].startswith("$y$"))

    def test_nobody_gets_a_shell(self):
        with self.assertRaisesRegex(fnas.FnasError, "shell access is root's"):
            fnas.create_user("sam", "", "ssh", False, [], "long enough")
        with self.assertRaisesRegex(fnas.FnasError, "shell access is root's"):
            fnas.change_user("kim", kind="ssh")

    def test_sftp_is_a_permission_kept_in_the_store(self):
        fnas.change_user("kim", kind="sftp")
        self.assertIn(("usermod", "--shell", fnas.users.SFTP_SHELL, "kim"), self.commands())
        self.assertTrue(self.stored("kim")["sftp"])
        self.assertEqual({u["name"]: u["kind"] for u in fnas.list_users()}["kim"], "sftp")

    def test_a_new_password_goes_to_the_store_and_samba_together_and_ends_sessions(self):
        with mock.patch.object(fnas.os.path, "isdir", return_value=True):
            fnas.set_password("kim", "kims new one")
        self.assertFalse([c for c in self.commands() if c[0] == "chpasswd"])
        self.assertIn((("smbpasswd", "-s", "-a", "kim"), "kims new one\nkims new one\n"), self.calls)
        self.assertTrue(self.accounts_module.verify_password("kims new one", self.stored("kim")["hash"]))
        with open(self.signed_out) as file:
            self.assertIn("kim", json.load(file))

    def test_setting_a_password_clears_must_change(self):
        with self.accounts_module.changing_store() as store:
            store["people"]["kim"]["must_change"] = True
        fnas.set_password("kim", "kims new one")
        self.assertFalse(self.stored("kim")["must_change"])

    def test_admins_come_from_the_store_and_the_last_one_stays(self):
        self.assertEqual(fnas.admins(), {"tester"})
        with self.assertRaisesRegex(fnas.FnasError, "only administrator"):
            fnas.change_user("tester", admin=False)
        fnas.change_user("kim", admin=True)
        self.assertTrue(self.stored("kim")["admin"])
        fnas.change_user("tester", admin=False)
        self.assertEqual(fnas.admins(), {"kim"})
        # Admin rights never come from a Unix group on a separated NAS
        self.assertFalse([c for c in self.commands() if c[0] == "gpasswd" and "nas-admins" in c])

    def test_turning_someone_off_uses_expiry_and_never_touches_their_locked_password(self):
        fnas.change_user("kim", enabled=False)
        commands = self.commands()
        self.assertIn(("usermod", "--expiredate", "1", "kim"), commands)
        self.assertFalse([c for c in commands if "--lock" in c or "--unlock" in c])
        self.assertFalse(self.stored("kim")["enabled"])
        fnas.change_user("kim", enabled=True)
        self.assertIn(("usermod", "--expiredate", "", "kim"), self.commands())
        self.assertFalse([c for c in self.commands() if "--unlock" in c])
        self.assertTrue(self.stored("kim")["enabled"])

    def test_login_check_says_no_the_same_way_for_a_stranger_and_a_wrong_password(self):
        check = self.accounts_module.check_login
        self.assertIsNone(check("tester", "tester password"))
        self.assertEqual(check("tester", "nope"), "wrong")
        self.assertEqual(check("nobody", "tester password"), "wrong")
        # kim has no password yet, so nothing matches
        self.assertEqual(check("kim", ""), "wrong")
        with self.accounts_module.changing_store() as store:
            store["people"]["tester"]["enabled"] = False
        self.assertEqual(check("tester", "tester password"), "off")
        with self.accounts_module.changing_store() as store:
            store["people"]["tester"].update(enabled=True, must_change=True)
        self.assertEqual(check("tester", "tester password"), "expired")

    def test_a_copied_shadow_hash_works_as_it_is(self):
        # What migration copies across: the yescrypt and SHA-512 hashes chpasswd wrote
        lib = self.accounts_module.libcrypt()
        for prefix in (b"$y$", b"$6$"):
            with self.subTest(prefix):
                hashed = lib.crypt(b"old password", lib.crypt_gensalt(prefix, 0, None, 0)).decode()
                self.assertTrue(self.accounts_module.verify_password("old password", hashed))
                self.assertFalse(self.accounts_module.verify_password("old password", "!" + hashed))

    def test_removing_someone_takes_them_out_of_the_store(self):
        fnas.remove_user("kim", False, "remove kim", [])
        with open(self.store) as file:
            self.assertNotIn("kim", json.load(file)["people"])

    def test_the_store_is_readable_by_root_alone(self):
        fnas.set_password("kim", "kims new one")
        self.assertEqual(os.stat(self.store).st_mode & 0o777, 0o600)


class Users(unittest.TestCase):
    def setUp(self):
        account = lambda name, uid, shell="/bin/bash": SimpleNamespace(
            pw_name=name, pw_uid=uid, pw_gid=uid, pw_gecos=f"{name.title()},,,", pw_shell=shell)
        group = lambda name, gid, members=(): SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=list(members))
        self.accounts = [account("root", 0), account("tester", 1000), account("kim", 1001, "/usr/sbin/nologin"),
                         account("nas-share", 3000, "/usr/sbin/nologin")]
        self.groups = [group("tester", 1000), group("kim", 1001), group("nas-share", 3000),
                       group("family", 1002, ["kim"]), group("nas-admins", 999, ["tester"]),
                       # What Debian and its packages leave behind
                       group("video", 44, ["kim"]), group("wheel", 998, ["tester"]),
                       group("docker", 997, ["plex"])]

        def by_gid(gid):
            for found in self.groups:
                if found.gr_gid == gid:
                    return found
            raise KeyError(gid)

        def by_name(name):
            for found in self.groups:
                if found.gr_name == name:
                    return found
            raise KeyError(name)
        self.calls = []

        def run(*command, check=True, stdin=None):
            self.calls.append((command, stdin))
            if command[0] == "useradd":
                self.accounts.append(account(command[-1], 1003, command[3]))
                self.groups.append(group(command[-1], 1003))
            elif command[0] == "groupadd":
                self.groups.append(group(command[-1], 1004))
            if command[0] == "pdbedit":
                return SimpleNamespace(stdout="tester:1000:X:X:[U          ]:LCT-0:\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        shadow = os.path.join(directory.name, "shadow")
        signed_out = os.path.join(directory.name, "signed-out.json")
        self.signed_out = signed_out
        with open(shadow, "w") as file:
            file.write("tester:$y$hash:20000:0:99999:7:::\nkim:!:20000:0:99999:7:::\n"
                       "sam:!$y$hash:20000:0:99999:7::1:\n")
        for patcher in (patch("run", run), patch("SHADOW", shadow),
                        patch("SIGNED_OUT_FILE", signed_out),
                        patch("SIGNED_OUT_LOCK", signed_out + ".lock"),
                        mock.patch.object(fnas.pwd, "getpwall", lambda: self.accounts),
                        mock.patch.object(fnas.grp, "getgrall", lambda: self.groups),
                        mock.patch.object(fnas.grp, "getgrnam", by_name),
                        mock.patch.object(fnas.grp, "getgrgid", by_gid)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def commands(self):
        return [command for command, _ in self.calls]

    def test_lists_people_but_not_the_nas_itself(self):
        found = {u["name"]: u for u in fnas.list_users()}
        self.assertEqual(list(found), ["kim", "tester"])
        self.assertEqual((found["kim"]["kind"], found["kim"]["groups"], found["kim"]["smb_password"]),
                         ("shares", ["family", "video"], False))
        self.assertEqual((found["tester"]["kind"], found["tester"]["admin"]), ("ssh", True))
        # No password yet isn't the same as turned off
        self.assertEqual((found["kim"]["password"], found["kim"]["enabled"]), (False, True))
        self.assertEqual((found["tester"]["password"], found["tester"]["enabled"]), (True, True))
        self.assertEqual([g["name"] for g in fnas.list_groups()], ["family"])

    def test_create_sets_one_password_for_linux_and_samba(self):
        with mock.patch.object(fnas.os.path, "isdir", return_value=True):
            fnas.create_user("sam", "Sam", "shares", False, ["family"], "long enough")
        commands = self.commands()
        self.assertIn(("useradd", "--create-home", "--shell", "/usr/sbin/nologin", "--comment", "Sam", "sam"),
                      commands)
        self.assertIn((("chpasswd",), "sam:long enough\n"), self.calls)
        self.assertIn((("smbpasswd", "-s", "-a", "sam"), "long enough\nlong enough\n"), self.calls)
        self.assertIn(("gpasswd", "-a", "sam", "family"), commands)

    def test_changing_a_password_ends_every_session(self):
        with mock.patch.object(fnas.os.path, "isdir", return_value=True):
            fnas.set_password("tester", "another long one")
        # The helper reads this and refuses a session that started before it
        with open(self.signed_out) as file:
            self.assertIn("tester", json.load(file))

    def test_turning_an_account_off_or_removing_it_ends_its_sessions(self):
        fnas.change_user("kim", enabled=False)
        with open(self.signed_out) as file:
            self.assertIn("kim", json.load(file))
        fnas.remove_user("kim", False, "remove kim", ["docs"])
        with open(self.signed_out) as file:
            self.assertIn("kim", json.load(file))

    def test_refusals(self):
        cases = [(("tester", "", "shares", False, [], "long enough"), "already used"),
                 (("family", "", "shares", False, [], "long enough"), "already used"),
                 (("Sam", "", "shares", False, [], "long enough"), "lowercase"),
                 (("sam", "a:b", "shares", False, [], "long enough"), "full names"),
                 (("sam", "", "shares", False, [], "short"), "8 characters"),
                 (("sam", "", "shares", False, [], "two\nlines"), "span lines"),
                 (("sam", "", "shares", False, ["strangers"], "long enough"), "no such group")]
        for args, message in cases:
            with self.subTest(args), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.create_user(*args)
        self.assertEqual(self.calls, [])

    def test_the_last_administrator_stays(self):
        for change in ({"admin": False}, {"enabled": False}):
            with self.subTest(change), self.assertRaisesRegex(fnas.FnasError, "only administrator"):
                fnas.change_user("tester", **change)
        with self.assertRaisesRegex(fnas.FnasError, "only administrator"):
            fnas.remove_user("tester", False, "remove tester", [])

    def test_remove_needs_confirmation(self):
        with self.assertRaisesRegex(fnas.FnasError, 'loses access to 1 share\n    docs\nconfirm by passing --confirm "remove kim"'):
            fnas.remove_user("kim", False, "", ["docs"])
        fnas.remove_user("kim", True, "remove kim", ["docs"])
        self.assertIn(("userdel", "kim"), self.commands())

    def test_turning_off_locks_linux_and_samba(self):
        fnas.change_user("kim", enabled=False)
        self.assertIn(("usermod", "--lock", "--expiredate", "1", "kim"), self.commands())
        self.assertNotIn(("smbpasswd", "-d", "kim"), self.commands())

    def test_groups(self):
        fnas.create_group("friends", ["kim", "tester"])
        self.assertIn(("gpasswd", "-M", "kim,tester", "friends"), self.commands())
        with self.assertRaisesRegex(fnas.FnasError, "already used"):
            fnas.create_group("kim", [])
        with self.assertRaisesRegex(fnas.FnasError, "no such user"):
            fnas.set_members("family", ["nobody2"])
        fnas.rename_group("family", "household")
        self.assertIn(("groupmod", "--new-name", "household", "family"), self.commands())

    def test_the_machines_own_groups_are_offered_but_not_removed(self):
        # An app such as Plex or a *arr runs as a system account in a group that
        # already exists, so a share can be given to that group instead. Nothing
        # is filtered: every group on the machine can be chosen.
        found = {g["name"]: g for g in fnas.list_groups(include_system=True)}
        self.assertEqual(sorted(found),
                         ["docker", "family", "kim", "nas-admins", "nas-share", "tester", "video", "wheel"])
        self.assertEqual((found["video"]["system"], found["video"]["members"]), (True, ["kim"]))
        self.assertFalse(found["family"]["system"])
        # A group named after an account is flagged, since its members follow the account
        self.assertEqual((found["kim"]["system"], found["kim"]["private"]), (True, True))
        self.assertEqual((found["family"]["system"], found["family"]["private"]), (False, False))
        # nas-admins has a switch of its own, but it is still a group and can be shared to
        self.assertTrue(found["nas-admins"]["system"])
        self.assertEqual([g["name"] for g in fnas.list_groups()], ["family"])
        with self.assertRaisesRegex(fnas.FnasError, "video belongs to the machine"):
            fnas.rename_group("video", "films")
        with self.assertRaisesRegex(fnas.FnasError, "wheel belongs to the machine"):
            fnas.remove_group("wheel")

    def test_a_person_may_join_a_machine_group_without_losing_the_app(self):
        fnas.set_members("docker", ["kim"])
        self.assertIn(("gpasswd", "-a", "kim", "docker"), self.commands())
        # plex, the app the group exists for, is never replaced
        self.assertNotIn(("gpasswd", "-M", "kim", "docker"), self.commands())
        self.calls.clear()
        # kim already has video, and joining it again from a person's form is quiet,
        # apart from the Samba account list that every lookup reads
        fnas.set_groups("kim", ["family", "video"])
        self.assertEqual([command for command in self.commands() if command[0] != "pdbedit"], [])


class Usage(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch("USAGE_FILE", os.path.join(directory.name, "usage.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keeps_one_point_an_hour_and_one_a_day(self):
        start = 1_800_000_000
        for minute in range(0, 3 * 24 * 60, 20):
            fnas.record([{"name": "tank", "allocated": minute, "size": 10 ** 6}], start + minute * 60)
        found = fnas.history("tank")
        self.assertTrue(70 <= len(found["hourly"]) <= 73)
        self.assertIn(len(found["daily"]), (3, 4))

    def test_projection(self):
        day = fnas.DAY
        growing = [[day * n, 100 * n, 1000] for n in range(5)]
        self.assertEqual(fnas.projection(growing), {"days": 6, "per_day": 100})
        self.assertIn("three days", fnas.projection(growing[:2])["reason"])
        steady = [[day * n, 400, 1000] for n in range(5)]
        self.assertIn("Not filling", fnas.projection(steady)["reason"])

    SAMPLE = {"memory_used": 2, "memory_total": 8, "load": 0.5, "disk_used": 3, "disk_size": 20,
              "temperature": 41.0}

    def test_records_the_system_beside_the_pools(self):
        start = 1_800_000_000
        fnas.record([{"name": "tank", "allocated": 10, "size": 100}], start, system=self.SAMPLE)
        self.assertEqual(fnas.system_history()["hourly"][0], [start, 2, 8, 0.5, 3, 20, 41.0])
        self.assertEqual(fnas.history("tank")["hourly"][0], [start, 10, 100])

    def test_a_sensor_that_reports_nothing_is_recorded_as_nothing(self):
        fnas.record([], 1_800_000_000, system={**self.SAMPLE, "temperature": None})
        self.assertIsNone(fnas.system_history()["hourly"][0][6])

    def test_a_pool_called_system_is_not_the_system(self):
        # The system's history sits under a name no pool can have
        fnas.record([{"name": "system", "allocated": 1, "size": 2}], 1_800_000_000, system=self.SAMPLE)
        self.assertEqual(fnas.history("system")["hourly"][0], [1_800_000_000, 1, 2])
        self.assertEqual(len(fnas.system_history()["hourly"][0]), 7)

    def test_an_older_file_without_the_system_still_reads(self):
        fnas.write_json(fnas.USAGE_FILE, {"tank": {"hourly": [], "daily": []}})
        self.assertEqual(fnas.system_history(), {"hourly": [], "daily": []})
        self.assertEqual(fnas.history("tank"), {"hourly": [], "daily": []})


class Cpu(unittest.TestCase):
    def test_usage_is_a_percentage(self):
        # Sampled from this machine's own counters, so only the bounds are known
        value = fnas.cpu_usage()
        self.assertGreaterEqual(value, 0)
        self.assertLessEqual(value, 100)


class Space(unittest.TestCase):
    def test_parts_add_up_to_what_is_used(self):
        lines = ("tank\t1000\t0\t100\t900\t5000\n"
                 "tank/shares\t700\t0\t10\t690\t5000\n"
                 "tank/shares/media\t500\t200\t300\t0\t5000\n"
                 "tank/shares/docs\t190\t40\t150\t0\t5000\n"
                 "tank/fellowship-backup\t150\t0\t0\t150\t5000\n"
                 "tank/fellowship-backup/other/shares/x\t150\t50\t100\t0\t5000\n")
        with patch("run", lambda *command, check=True: SimpleNamespace(stdout=lines, stderr="", returncode=0)):
            found = fnas.space("tank")
        self.assertEqual(found["parts"], {"shares": 450, "snapshots": 240, "backups": 150, "other": 160})
        self.assertEqual(sum(found["parts"].values()), found["used"])
        self.assertEqual([s["name"] for s in found["shares"]], ["media", "docs"])


class Ssh(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.calls = []

        def run(*command, check=True, stdin=None):
            self.calls.append(command)
            return SimpleNamespace(stdout="256 SHA256:abc kim@laptop (ED25519)\n", stderr="", returncode=0)
        sftp = {module.__name__: module for module in MODULES}["fellowship_nas.sftp"]
        for patcher in (patch("run", run), patch("SETTINGS_FILE", os.path.join(self.dir, "ssh.json")),
                        patch("DROP_IN", os.path.join(self.dir, "fellowship.conf")),
                        # The SFTP views are built by their own module and tested there
                        mock.patch.object(sftp, "sync", lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_nas_ssh_and_never_root(self):
        text = fnas.drop_in({"enabled": True, "passwords": False})
        # SFTP accounts come in through sshd too, but into internal-sftp
        self.assertIn("AllowGroups nas-ssh nas-sftp\n", text)
        self.assertIn("PermitRootLogin no\n", text)
        self.assertIn("PasswordAuthentication no\n", text)
        # Debian's Include sits near the top of the main file, so everything
        # after it would be inside the Match unless the block is closed
        self.assertTrue(text.rstrip().endswith("Match all"))

    def test_keys_can_carry_no_options(self):
        for line in ('command="rm -rf /" ssh-ed25519 AAAA kim', "no-pty ssh-ed25519 AAAA", "hello", "ssh-dss AAAA x"):
            with self.subTest(line), self.assertRaisesRegex(fnas.FnasError, "public key"):
                fnas.check_key(line)
        self.assertEqual(fnas.check_key("ssh-ed25519 AAAA kim@laptop extra"), "ssh-ed25519 AAAA kim@laptop")

    def test_passwords_stay_on_until_an_administrator_has_a_key(self):
        with patch("admins_with_keys", lambda: []):
            with self.assertRaisesRegex(fnas.FnasError, "lock everyone out"):
                fnas.set_settings(passwords=False)
        self.assertTrue(fnas.settings()["passwords"])
        with patch("admins_with_keys", lambda: ["tester"]), patch("refresh", lambda: None), \
                patch("members", lambda: ["tester"]):
            self.assertFalse(fnas.set_settings(passwords=False)["passwords"])

    def test_a_setting_sshd_refuses_is_put_back(self):
        with open(fnas.DROP_IN, "w") as file:
            file.write("old\n")

        def refuse(*command, check=True, stdin=None):
            failed = command[:2] == ("sshd", "-t")
            return SimpleNamespace(stdout="", stderr="bad" if failed else "", returncode=1 if failed else 0)
        with patch("run", refuse), patch("list_users", lambda: []), \
                mock.patch.object(fnas.os.path, "isdir", return_value=True):
            with self.assertRaisesRegex(fnas.FnasError, "refused"):
                fnas.refresh()
        with open(fnas.DROP_IN) as file:
            self.assertEqual(file.read(), "old\n")


class SeparatedSsh(unittest.TestCase):
    """Phase 7 (docs/fellowship-nas-ssh-samba-plan.md): on a separated NAS sshd
    serves Files over SFTP to nas-sftp, passwords are checked against the store
    through PAM, keys come from the store, and root's SSH is his own keys-only
    switch that the desktop can only read."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.calls = []
        by_module = {m.__name__: m for m in MODULES}
        self.accounts_module = by_module["fellowship_nas.accounts"]
        self.ssh_module = by_module["fellowship_nas.ssh"]
        self.sftp_module = by_module["fellowship_nas.sftp"]
        self.store = os.path.join(self.dir, "accounts.json")
        self.people = {
            "kim": {"uid": 1001, "full_name": "Kim", "admin": True, "enabled": True, "sftp": True,
                    "hash": "", "password_set": None, "must_change": False, "keys": []},
            "ann": {"uid": 1002, "full_name": "Ann", "admin": False, "enabled": True, "sftp": False,
                    "hash": "", "password_set": None, "must_change": False, "keys": []}}
        with open(self.store, "w") as file:
            json.dump({"version": 1, "people": self.people}, file)
        self.root_ssh_file = os.path.join(self.dir, "root-ssh.json")
        self.root_keys = os.path.join(self.dir, "root_keys")
        self.pam_file = os.path.join(self.dir, "sshd.pam")
        self.drop_in_file = os.path.join(self.dir, "fellowship.conf")
        self.settings_file = os.path.join(self.dir, "ssh.json")

        def run(*command, check=True, stdin=None):
            self.calls.append(command)
            return SimpleNamespace(stdout="256 SHA256:abc kim@laptop (ED25519)\n", stderr="", returncode=0)

        def by_name(name):
            if name in self.people:
                person = self.people[name]
                return SimpleNamespace(pw_name=name, pw_uid=person["uid"], pw_gid=person["uid"], pw_gecos="",
                                       pw_shell=fnas.users.SFTP_SHELL if person["sftp"] else "/usr/sbin/nologin",
                                       pw_dir="/nonexistent")
            raise KeyError(name)

        for patcher in (patch("run", run),
                        patch("SETTINGS_FILE", self.settings_file),
                        patch("DROP_IN", self.drop_in_file),
                        mock.patch.object(self.accounts_module, "STORE", self.store),
                        mock.patch.object(self.accounts_module, "STORE_LOCK", self.store + ".lock"),
                        mock.patch.object(self.ssh_module, "PAM_SSHD", self.pam_file),
                        mock.patch.object(self.ssh_module, "ROOT_SSH_FILE", self.root_ssh_file),
                        mock.patch.object(self.ssh_module, "ROOT_KEYS", self.root_keys),
                        mock.patch.object(fnas.pwd, "getpwnam", by_name),
                        mock.patch.object(self.sftp_module, "sync", lambda: None),
                        mock.patch.object(self.sftp_module, "accounts", lambda: ["kim"])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def root_on(self):
        with open(self.root_ssh_file, "w") as file:
            json.dump({"enabled": True}, file)

    def stored(self, name):
        with open(self.store) as file:
            return json.load(file)["people"][name]

    def test_sftp_gets_in_and_root_does_not_until_he_says_so(self):
        text = self.ssh_module.drop_in({"enabled": True, "passwords": True})
        self.assertIn("AllowGroups nas-sftp\n", text)
        self.assertIn("PermitRootLogin no\n", text)
        self.assertIn("AuthorizedKeysCommand /usr/sbin/fnas auth keys %u\n", text)
        self.assertIn("AuthorizedKeysCommandUser root\n", text)
        # The store is the only source of keys for SFTP people
        self.assertIn("    AuthorizedKeysFile none\n", text)
        self.assertNotIn("nas-ssh", text)
        self.assertTrue(text.rstrip().endswith("Match all"))

    def test_root_ssh_is_keys_only_and_joins_allowgroups(self):
        self.root_on()
        text = self.ssh_module.drop_in({"enabled": True, "passwords": False})
        self.assertIn("AllowGroups root nas-sftp\n", text)
        self.assertIn("PermitRootLogin prohibit-password\n", text)
        self.assertIn("PasswordAuthentication no\n", text)

    def test_sftp_off_never_touches_root_ssh(self):
        self.root_on()
        text = self.ssh_module.drop_in({"enabled": False, "passwords": True})
        self.assertIn("AllowGroups root\n", text)
        self.assertNotIn("AllowGroups root nas-sftp", text)

    def test_both_off_denies_everyone(self):
        text = self.ssh_module.drop_in({"enabled": False, "passwords": True})
        self.assertIn("DenyGroups *\n", text)
        self.assertNotIn("AllowGroups", text)

    def test_the_settings_check_remakes_the_privsep_dir_the_service_took_away(self):
        # ssh.service's RuntimeDirectory removes /run/sshd when it stops, and
        # sshd -t refuses without it: the switch must still turn SFTP back on
        privsep = os.path.join(self.dir, "run-sshd")
        with mock.patch.object(self.ssh_module, "PRIVSEP_DIR", privsep):
            self.ssh_module.refresh()
            self.assertEqual(os.stat(privsep).st_mode & 0o777, 0o755)
            os.rmdir(privsep)
            self.ssh_module.refresh()
            self.assertTrue(os.path.isdir(privsep))
            self.assertIn(("sshd", "-t"), self.calls)

    def test_root_ssh_keeps_the_service_up_with_sftp_off(self):
        self.root_on()
        with open(self.settings_file, "w") as file:
            json.dump({"enabled": False, "passwords": True}, file)
        self.ssh_module.refresh()
        self.assertIn(("systemctl", "enable", "--now", "ssh.service"), self.calls)
        self.assertNotIn(("systemctl", "disable", "--now", "ssh.service"), self.calls)
        with open(self.drop_in_file) as file:
            self.assertIn("AllowGroups root\n", file.read())
        with open(self.pam_file) as file:
            pam = file.read()
        self.assertIn("pam_exec.so expose_authtok /usr/lib/fellowship-nas/sftp-auth", pam)
        # The account phase is what refuses a turned-off person, keys included
        self.assertIn("account  requisite   pam_unix.so", pam)

    def test_both_off_stops_the_service(self):
        with open(self.settings_file, "w") as file:
            json.dump({"enabled": False, "passwords": True}, file)
        self.ssh_module.refresh()
        self.assertIn(("systemctl", "disable", "--now", "ssh.service"), self.calls)
        self.assertNotIn(("systemctl", "enable", "--now", "ssh.service"), self.calls)

    def test_passwords_off_needs_no_admin_key_on_a_separated_nas(self):
        found = self.ssh_module.set_settings(passwords=False)
        self.assertFalse(found["passwords"])
        with open(self.drop_in_file) as file:
            self.assertIn("PasswordAuthentication no\n", file.read())

    def test_keys_go_to_the_store_and_read_back(self):
        self.ssh_module.set_keys("kim", "ssh-ed25519 AAAA kim@laptop\n")
        self.assertEqual(self.stored("kim")["keys"], ["ssh-ed25519 AAAA kim@laptop"])
        found = self.ssh_module.list_keys("kim")
        self.assertEqual(found[0]["fingerprint"], "SHA256:abc")
        self.assertEqual(found[0]["line"], "ssh-ed25519 AAAA kim@laptop")

    def test_shares_only_is_refused_keys(self):
        with self.assertRaisesRegex(fnas.FnasError, "SFTP allowed"):
            self.ssh_module.set_keys("ann", "ssh-ed25519 AAAA ann@laptop")

    def test_keys_are_validated_and_limited(self):
        with self.assertRaisesRegex(fnas.FnasError, "public key"):
            self.ssh_module.set_keys("kim", "hello")
        with self.assertRaisesRegex(fnas.FnasError, "at most 32"):
            self.ssh_module.set_keys("kim", "\n".join(f"ssh-ed25519 AAAA k{i}" for i in range(33)))

    def test_empty_text_clears_the_keys(self):
        self.ssh_module.set_keys("kim", "ssh-ed25519 AAAA kim@laptop")
        self.ssh_module.set_keys("kim", "")
        self.assertEqual(self.stored("kim")["keys"], [])

    def test_auth_keys_answers_from_the_store_and_nothing_for_root(self):
        self.ssh_module.set_keys("kim", "ssh-ed25519 AAAA kim@laptop")
        self.assertEqual(fnas.accounts.ssh_keys("kim"), ["ssh-ed25519 AAAA kim@laptop"])
        self.assertEqual(fnas.accounts.ssh_keys("root"), [])
        self.assertEqual(fnas.accounts.ssh_keys("nobody"), [])

    def test_root_ssh_needs_a_key_first(self):
        with self.assertRaisesRegex(fnas.FnasError, "key"):
            self.ssh_module.set_root_ssh(True)
        with open(self.root_keys, "w") as file:
            file.write("# a comment\n\nssh-ed25519 AAAA root@desk\n")
        self.assertTrue(self.ssh_module.set_root_ssh(True))
        self.assertTrue(self.ssh_module.root_ssh())
        with open(self.drop_in_file) as file:
            self.assertIn("PermitRootLogin prohibit-password\n", file.read())
        self.assertTrue([c for c in self.calls if c[0] == "logger" and "fellowship-root-ssh" in c])
        self.assertFalse(self.ssh_module.set_root_ssh(False))
        with open(self.drop_in_file) as file:
            self.assertIn("PermitRootLogin no\n", file.read())

    def test_root_ssh_belongs_to_a_separated_nas(self):
        os.remove(self.store)
        with self.assertRaisesRegex(fnas.FnasError, "separated"):
            self.ssh_module.set_root_ssh(True)

    def test_a_setting_sshd_refuses_puts_both_files_back(self):
        with open(self.drop_in_file, "w") as file:
            file.write("old conf\n")
        with open(self.pam_file, "w") as file:
            file.write("old pam\n")

        def refuse(*command, check=True, stdin=None):
            failed = command[:2] == ("sshd", "-t")
            return SimpleNamespace(stdout="", stderr="bad" if failed else "", returncode=1 if failed else 0)
        with patch("run", refuse):
            with self.assertRaisesRegex(fnas.FnasError, "refused"):
                self.ssh_module.refresh()
        with open(self.drop_in_file) as file:
            self.assertEqual(file.read(), "old conf\n")
        with open(self.pam_file) as file:
            self.assertEqual(file.read(), "old pam\n")

    def test_status_shows_root_ssh_and_the_sftp_people(self):
        found = self.ssh_module.status()
        self.assertEqual(found["allowed"], ["kim"])
        self.assertFalse(found["root_ssh"])
        self.assertNotIn("admins_with_keys", found)


class SambaPasswordPolicy(unittest.TestCase):
    """Phase 7, ruling 1: a separated NAS keeps Debian's sync on (the locked
    Linux password is what refuses a Windows change) and denies the PAM
    password path outright. Both off was verified to drift the SMB password."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        by_module = {m.__name__: m for m in MODULES}
        self.accounts_module = by_module["fellowship_nas.accounts"]
        self.shares_module = by_module["fellowship_nas.shares"]
        self.separate_module = by_module["fellowship_nas.separate"]
        self.store = os.path.join(self.dir, "accounts.json")
        with open(self.store, "w") as file:
            json.dump({"version": 1, "people": {}}, file)
        self.conf = os.path.join(self.dir, "smb.conf")
        self.pam = os.path.join(self.dir, "samba.pam")
        for patcher in (mock.patch.object(self.accounts_module, "STORE", self.store),
                        mock.patch.object(self.shares_module, "SMB_CONF", self.conf),
                        mock.patch.object(self.shares_module, "SAMBA_PAM", self.pam)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_sync_stays_on_and_the_password_path_is_denied(self):
        with open(self.conf, "w") as file:
            file.write("[global]\n   workgroup = WORKGROUP\n   unix password sync = no\n"
                       "   pam password change = no\n[homes]\n   unix password sync = no\n")
        self.shares_module.samba_password_policy()
        with open(self.conf) as file:
            text = file.read()
        self.assertIn("   unix password sync = yes\n", text)
        self.assertIn("   pam password change = yes\n", text)
        # Only [global] is touched
        self.assertIn("[homes]\n   unix password sync = no\n", text)
        with open(self.pam) as file:
            pam = file.read()
        self.assertIn("@include common-auth", pam)
        self.assertIn("@include common-account", pam)
        self.assertIn("@include common-session-noninteractive", pam)
        self.assertIn("password   requisite   pam_deny.so", pam)

    def test_the_old_model_is_untouched(self):
        os.remove(self.store)
        with open(self.conf, "w") as file:
            file.write("[global]\n   unix password sync = yes\n")
        self.shares_module.samba_password_policy()
        with open(self.conf) as file:
            self.assertEqual(file.read(), "[global]\n   unix password sync = yes\n")
        self.assertFalse(os.path.exists(self.pam))

    def test_migration_backs_up_the_pam_files_it_changes(self):
        self.assertIn("/etc/pam.d/sshd", self.separate_module.SAVED_FILES)
        self.assertIn("/etc/pam.d/samba", self.separate_module.SAVED_FILES)


class LookTests(unittest.TestCase):
    """A person's light or dark and theme are kept on the NAS, not in the browser
    they happened to choose them in."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.look = importlib.import_module("fellowship_nas.look")
        people = {"kim", "tester"}

        def known(name):
            if name not in people:
                raise fnas.FnasError(f"no such user: {name}")
            return {"name": name}
        for patcher in (mock.patch.object(self.look, "LOOK_FILE", os.path.join(self.dir, "look.json")),
                        mock.patch.object(self.look, "LOOK_LOCK", os.path.join(self.dir, "look.lock")),
                        mock.patch.object(self.look, "check_user", known)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_person_who_has_not_chosen_gets_the_desktop_as_it_ships(self):
        self.assertEqual(self.look.show("kim"),
                         {"user": "kim", "mode": "system", "palette": "fellowship"})

    def test_one_change_keeps_the_other(self):
        self.look.set_look("kim", palette="highwatch")
        expected = {"user": "kim", "mode": "system", "palette": "highwatch"}
        self.assertEqual(self.look.show("kim"), expected)
        self.look.set_look("kim", mode="dark")
        self.assertEqual(self.look.show("kim"), {**expected, "mode": "dark"})

    def test_each_person_keeps_their_own(self):
        self.look.set_look("kim", mode="light", palette="silverwater")
        self.assertEqual(self.look.show("tester")["mode"], "system")
        self.assertEqual(self.look.show("kim")["palette"], "silverwater")

    def test_only_a_real_mode_or_theme_name_is_kept(self):
        for mode in ("bright", "", "Dark"):
            with self.subTest(mode), self.assertRaisesRegex(fnas.FnasError, "mode must be"):
                self.look.set_look("kim", mode=mode)
        for palette in ("../../etc/passwd", "High Watch", "", "-rf"):
            with self.subTest(palette), self.assertRaisesRegex(fnas.FnasError, "theme name"):
                self.look.set_look("kim", palette=palette)

    def test_nothing_to_change_is_refused(self):
        with self.assertRaisesRegex(fnas.FnasError, "nothing to change"):
            self.look.set_look("kim")

    def test_a_stranger_has_no_look_to_read_or_write(self):
        with self.assertRaisesRegex(fnas.FnasError, "no such user"):
            self.look.show("sam")
        with self.assertRaisesRegex(fnas.FnasError, "no such user"):
            self.look.set_look("sam", mode="dark")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "look.json")))

    def test_a_removed_person_leaves_nothing_behind(self):
        self.look.set_look("kim", mode="dark", palette="hearthfield")
        self.look.forget_look("kim")
        self.assertEqual(self.look.show("kim")["mode"], "system")
        # Forgetting someone who never chose is not an error
        self.look.forget_look("kim")


class FileRoots(unittest.TestCase):
    """Where Files may look: the shares a person can use, by path, so neither the
    desktop nor WebDAV works out a path for itself."""

    shares = [
        {"name": "media", "path": "/mnt/tank/shares/media", "snapshots": True, "mode": "chosen",
         "webdav": True, "sftp": True, "access": {"users": {"kim": "write"}, "groups": {"family": "read"}}},
        {"name": "docs", "path": "/mnt/tank/shares/docs", "snapshots": False, "mode": "chosen",
         "webdav": False, "sftp": False, "access": {"users": {}, "groups": {"family": "read"}}},
        {"name": "private", "path": "/mnt/tank/shares/private", "snapshots": True, "mode": "chosen",
         "webdav": True, "sftp": False, "access": {"users": {"tester": "write"}, "groups": {}}},
    ]

    def setUp(self):
        family = SimpleNamespace(gr_name="family", gr_mem=["kim"])
        for patcher in (patch("list_shares", lambda: self.shares),
                        mock.patch.object(fnas.grp, "getgrall", lambda: [family])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_person_gets_their_shares_with_where_each_one_is(self):
        self.assertEqual(fnas.roots_of("kim"), [
            {"share": "media", "path": "/mnt/tank/shares/media", "level": "write",
             "through": "own", "snapshots": True, "webdav": True, "sftp": True},
            {"share": "docs", "path": "/mnt/tank/shares/docs", "level": "read",
             "through": "group family", "snapshots": False, "webdav": False, "sftp": False},
        ])

    def test_a_share_someone_cannot_use_is_not_a_root(self):
        self.assertEqual([root["share"] for root in fnas.roots_of("tester")], ["private"])


class SftpViews(unittest.TestCase):
    """Files over SFTP: an account that gets files and nothing else.

    What it may reach is a folder of bind mounts built from the shares it can
    already use, and of those only the ones shared that way."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.paths = {}
        for name in ("media", "docs", "private"):
            self.paths[name] = os.path.join(self.root, "shares", name)
            os.makedirs(self.paths[name])
        self.shares = [
            {"name": "media", "path": self.paths["media"], "sftp": True, "mode": "everyone",
             "access": {"users": {}, "groups": {}}},
            # Shared over SMB, but nobody agreed it should be reachable over SFTP
            {"name": "docs", "path": self.paths["docs"], "sftp": False, "mode": "everyone",
             "access": {"users": {}, "groups": {}}},
            {"name": "private", "path": self.paths["private"], "sftp": True, "mode": "chosen",
             "access": {"users": {"kim": "read"}, "groups": {}}},
        ]
        self.calls = []

        def run(*command, check=True, stdin=None):
            self.calls.append(command)
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        self.sftp = {module.__name__: module for module in MODULES}["fellowship_nas.sftp"]
        for patcher in (patch("run", run), patch("list_shares", lambda: self.shares),
                        mock.patch.object(self.sftp, "VIEW_ROOT", os.path.join(self.root, "view")),
                        mock.patch.object(self.sftp, "shell", lambda: os.path.join(self.root, "sftp-only")),
                        mock.patch.object(fnas.os, "chown"), mock.patch.object(fnas.os, "chmod")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_account_sees_only_the_shares_shared_that_way_and_theirs(self):
        self.assertEqual([one["share"] for one in self.sftp.view("kim")], ["media", "private"])
        self.assertEqual([one["share"] for one in self.sftp.view("sam")], ["media"])
        self.assertEqual(self.sftp.view("kim")[1]["level"], "read")

    def test_a_view_is_one_bind_mount_per_share_in_a_folder_of_root(self):
        self.sftp.sync_view("kim", self.sftp.view("kim"))
        home = os.path.join(self.root, "view", "kim")
        self.assertEqual(os.stat(home).st_mode & 0o777, 0o755)
        self.assertIn(("mount", "--bind", self.paths["media"], os.path.join(home, "media")), self.calls)
        # kim may only read private, so it is mounted read-only
        self.assertIn(("mount", "--bind", "-o", "ro", self.paths["private"], os.path.join(home, "private")),
                      self.calls)
        # The share that isn't shared over SFTP is nowhere in the view
        self.assertFalse(os.path.exists(os.path.join(home, "docs")))

    def test_a_share_whose_access_changes_is_mounted_again_to_match(self):
        home = os.path.join(self.root, "view", "kim")
        point = os.path.join(home, "private")
        os.makedirs(point)
        # Mounted read-write from before kim's access became read-only
        with mock.patch.object(self.sftp, "mounts", lambda: {point}), \
                mock.patch.object(self.sftp, "read_only_mounts", lambda: set()), \
                mock.patch.object(self.sftp, "shows", lambda point, path: True):
            self.sftp.sync_view("kim", [one for one in self.sftp.view("kim") if one["share"] == "private"])
        self.assertIn(("umount", point), self.calls)
        self.assertIn(("mount", "--bind", "-o", "ro", self.paths["private"], point), self.calls)

    def test_a_share_already_mounted_as_it_should_be_is_left_alone(self):
        home = os.path.join(self.root, "view", "kim")
        point = os.path.join(home, "private")
        os.makedirs(point)
        with mock.patch.object(self.sftp, "mounts", lambda: {point}), \
                mock.patch.object(self.sftp, "read_only_mounts", lambda: {point}), \
                mock.patch.object(self.sftp, "shows", lambda point, path: True):
            self.sftp.sync_view("kim", [one for one in self.sftp.view("kim") if one["share"] == "private"])
        self.assertFalse([c for c in self.calls if c[0] in ("mount", "umount")])

    def test_a_pool_that_is_not_mounted_shows_nothing(self):
        self.shares[0]["path"] = os.path.join(self.root, "gone")
        self.sftp.sync_view("kim", self.sftp.view("kim"))
        home = os.path.join(self.root, "view", "kim")
        self.assertFalse(os.path.exists(os.path.join(home, "media")))
        self.assertFalse([c for c in self.calls if c[:2] == ("mount", "--bind") and "media" in c[2]])

    def test_a_share_taken_out_of_the_view_is_unmounted_and_emptied(self):
        home = os.path.join(self.root, "view", "kim")
        os.makedirs(os.path.join(home, "old"))
        with mock.patch.object(self.sftp, "mounts", lambda: {os.path.join(home, "old")}):
            self.sftp.sync_view("kim", [one for one in self.sftp.view("kim") if one["share"] == "media"])
        self.assertIn(("umount", os.path.join(home, "old")), self.calls)
        self.assertFalse(os.path.exists(os.path.join(home, "old")))

    def test_a_share_something_still_holds_is_detached(self):
        home = os.path.join(self.root, "view", "kim")
        point = os.path.join(home, "media")
        os.makedirs(point)
        # What the kernel still has mounted, which a detach does take away
        held = {point}

        def stubborn(*command, check=True, stdin=None):
            self.calls.append(command)
            if command == ("umount", "-l", point):
                held.clear()
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            busy = command == ("umount", point)
            return SimpleNamespace(stdout="", stderr="busy" if busy else "",
                                   returncode=1 if busy else 0)
        with mock.patch.object(self.sftp, "run", stubborn), \
                mock.patch.object(self.sftp, "mounts", lambda: set(held)):
            problems = self.sftp.sync_view("kim", [one for one in self.sftp.view("kim")
                                                   if one["share"] == "private"])
        # A client with a file open keeps the file it opened and nothing more
        self.assertIn(("umount", point), self.calls)
        self.assertIn(("umount", "-l", point), self.calls)
        self.assertFalse(os.path.exists(point))
        self.assertFalse(problems)

    def test_a_share_that_will_not_go_is_reported_rather_than_forgotten(self):
        home = os.path.join(self.root, "view", "kim")
        point = os.path.join(home, "media")
        os.makedirs(point)

        def stuck(*command, check=True, stdin=None):
            self.calls.append(command)
            refused = command[0] == "umount"
            return SimpleNamespace(stdout="", stderr="busy" if refused else "",
                                   returncode=1 if refused else 0)
        with mock.patch.object(self.sftp, "run", stuck), \
                mock.patch.object(self.sftp, "mounts", lambda: {point}):
            problems = self.sftp.sync_view("kim", [one for one in self.sftp.view("kim")
                                                   if one["share"] == "private"])
        # The way in was not taken away, so that is said rather than assumed
        self.assertEqual(problems, [point])
        self.assertTrue(os.path.isdir(point))

    def test_a_share_that_will_not_mount_is_not_shown_empty(self):
        def refuse_mount(*command, check=True, stdin=None):
            self.calls.append(command)
            broken = command[:2] == ("mount", "--bind")
            return SimpleNamespace(stdout="", stderr="no such device" if broken else "",
                                   returncode=1 if broken else 0)
        with mock.patch.object(self.sftp, "run", refuse_mount):
            problems = self.sftp.sync_view("sam", self.sftp.view("sam"))
        point = os.path.join(self.root, "view", "sam", "media")
        # An empty folder would look like a share they can't read, so it isn't there
        self.assertFalse(os.path.exists(point))
        self.assertEqual(problems, [point])

    def test_a_view_that_would_not_rebuild_raises_an_alert(self):
        said = []
        with mock.patch.object(self.sftp.alerts, "send_alert",
                               lambda severity, title, body: said.append((severity, title))), \
                contextlib.redirect_stderr(io.StringIO()):
            self.sftp.report([os.path.join(self.root, "view", "kim", "media")])
        self.assertEqual(said, [("high", "An SFTP share could not be withdrawn")])

    def test_an_account_no_longer_set_to_sftp_loses_its_view(self):
        os.makedirs(os.path.join(self.root, "view", "sam"))
        with mock.patch.object(self.sftp, "accounts", lambda: []):
            self.sftp.sync_views()
        self.assertFalse(os.path.exists(os.path.join(self.root, "view", "sam")))

    def test_the_shell_an_sftp_account_carries_refuses(self):
        self.sftp.ensure_shell()
        shell = self.sftp.shell()
        with open(shell) as file:
            text = file.read()
        self.assertIn("exit 1", text)
        self.assertTrue(os.access(shell, os.X_OK))
        # A second run leaves it alone
        self.sftp.ensure_shell()
        self.assertTrue(os.path.exists(shell))

    def test_the_group_holds_exactly_the_accounts_set_to_files_over_sftp(self):
        people = [{"name": "kim", "kind": "sftp"}, {"name": "sam", "kind": "shares"},
                  {"name": "ada", "kind": "sftp"}]
        with mock.patch.object(fnas.users, "list_users", lambda: people):
            self.sftp.sync_accounts()
        self.assertIn(("gpasswd", "-M", "ada,kim", "nas-sftp"), self.calls)

    def test_an_account_kind_comes_from_its_shell(self):
        for shell, kind in (("/bin/bash", "ssh"), ("/usr/sbin/nologin", "shares"),
                            (fnas.users.SFTP_SHELL, "sftp")):
            with self.subTest(shell), \
                    mock.patch.object(fnas.users, "KINDS", dict(fnas.users.KINDS)):
                self.assertEqual(fnas.users.kind_of(SimpleNamespace(pw_shell=shell)), kind)

    def test_sshd_gives_an_sftp_account_a_chroot_and_no_shell(self):
        text = fnas.drop_in({"enabled": True, "passwords": True})
        self.assertIn("Match Group nas-sftp\n", text)
        self.assertIn("    ChrootDirectory /var/lib/fellowship-nas/sftp/%u\n", text)
        self.assertIn("    ForceCommand internal-sftp\n", text)
        self.assertIn("    AllowTcpForwarding no\n", text)
        self.assertIn("    PermitTunnel no\n", text)


class AlertHistory(unittest.TestCase):
    """The record can be cleared and notices dismissed; neither may hide a
    problem Health can still see."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.alerts_module = {m.__name__: m for m in MODULES}["fellowship_nas.alerts"]
        for name in ("ALERT_LOG", "ALERT_LOCK"):
            patcher = mock.patch.object(self.alerts_module, name, os.path.join(directory.name, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dismiss_hides_from_the_widget_and_clear_empties_the_record(self):
        self.alerts_module.send_alert("critical", "Pool tank is degraded", "a disk is gone")
        self.alerts_module.send_alert("info", "Network change rolled back", "the old settings are back")
        when = self.alerts_module.list_alerts()[0]["time"]
        self.alerts_module.dismiss(when, "Pool tank is degraded")
        self.assertEqual([a["title"] for a in self.alerts_module.list_alerts()],
                         ["Network change rolled back"])
        everything = self.alerts_module.list_alerts(everything=True)
        self.assertEqual(len(everything), 2)
        self.assertTrue(everything[0]["dismissed"])
        self.alerts_module.clear()
        self.assertEqual(self.alerts_module.list_alerts(everything=True), [])

    def test_clearing_the_record_leaves_current_findings_alone(self):
        self.alerts_module.send_alert("critical", "Pool tank is degraded", "a disk is gone")
        pools = [{"name": "tank", "engine": "mdadm", "health": "DEGRADED"}]
        engine = SimpleNamespace(pool_status=lambda name: {},
                                 member_disks=lambda status: [{"state": "FAULTED", "path": "/dev/sdb"}])
        with mock.patch.object(fnas.pools, "ENGINES", {"mdadm": engine}), \
                mock.patch.object(fnas.disks, "bays", lambda: {}):
            before = fnas.health_findings(pools)
            self.alerts_module.clear()
            self.assertEqual(fnas.health_findings(pools), before)
        self.assertTrue(before)


class SshExpectation(unittest.TestCase):
    """The shared SSH daemon is meant to run while SFTP or root SSH is on; with
    both off, stopped is intended and Health must not call it a failure."""

    def setUp(self):
        by_module = {m.__name__: m for m in MODULES}
        self.services = by_module["fellowship_nas.services"]

    def shown(self, sftp, root_ssh, separated, state):
        with mock.patch.object(self.services.ssh, "settings", lambda: {"enabled": sftp, "passwords": True}), \
                mock.patch.object(self.services.ssh, "root_ssh", lambda: root_ssh), \
                mock.patch.object(self.services.accounts, "is_separated", lambda: separated), \
                mock.patch.object(self.services, "unit_state", lambda unit: state), \
                mock.patch.object(self.services.shares, "list_shares",
                                  lambda: [{"name": "docs", "sftp": True, "smb": True}]):
            return self.services.system_services(), self.services.protocols(), fnas.service_findings()

    def entry(self, services, unit):
        return next(s for s in services if s["unit"] == unit)

    def test_both_off_means_stopped_is_intended(self):
        services, protocols, findings = self.shown(False, False, True, "stopped")
        self.assertEqual(self.entry(services, "ssh.service")["state"], "off")
        self.assertEqual(next(p for p in protocols if p["name"] == "sftp")["state"], "off")
        self.assertFalse([f for f in findings if f["key"] in ("service-ssh.service", "service-sftp")])

    def test_root_ssh_alone_keeps_the_daemon_expected(self):
        services, protocols, findings = self.shown(False, True, True, "running")
        self.assertEqual(self.entry(services, "ssh.service")["state"], "running")
        # SFTP itself is off even though the daemon is up for root
        self.assertEqual(next(p for p in protocols if p["name"] == "sftp")["state"], "off")
        self.assertFalse([f for f in findings if f["key"] in ("service-ssh.service", "service-sftp")])

    def test_sftp_on_with_the_daemon_down_is_a_failure(self):
        services, protocols, findings = self.shown(True, False, True, "stopped")
        self.assertEqual(self.entry(services, "ssh.service")["state"], "stopped")
        self.assertIn("service-ssh.service", [f["key"] for f in findings])
        self.assertIn("service-sftp", [f["key"] for f in findings])

    def test_the_old_model_ignores_root_ssh(self):
        services, _, findings = self.shown(False, True, False, "stopped")
        self.assertEqual(self.entry(services, "ssh.service")["state"], "off")
        self.assertFalse([f for f in findings if f["key"] == "service-ssh.service"])


class JobReasons(unittest.TestCase):
    """A job that dies must say why, and a check the kernel refuses must say so
    at the click, not as a traceback or a silent "stopped"."""

    def setUp(self):
        by_module = {m.__name__: m for m in MODULES}
        self.jobs = by_module["fellowship_nas.jobs"]
        self.mdadm = by_module["fellowship_nas.engines.mdadm"]

    def test_an_unexpected_error_is_recorded_as_the_jobs_reason(self):
        job = {"id": "abc", "kind": "scrub", "title": "Checking tank", "pool": "tank",
               "state": self.jobs.RUNNING, "progress": 0.2, "started": 1, "ended": None, "error": None}
        saved = []
        with mock.patch.object(self.jobs, "load", lambda job_id: dict(job)), \
                mock.patch.object(self.jobs, "save", lambda found: saved.append(dict(found))), \
                mock.patch.object(self.jobs, "work", side_effect=TypeError("bad device")), \
                mock.patch.object(self.jobs.alerts, "send_alert", lambda *args: None):
            self.jobs.run_job("abc")
        self.assertEqual(saved[-1]["state"], "failed")
        self.assertIn("TypeError: bad device", saved[-1]["error"])

    def test_a_refused_check_raises_with_the_kernels_reason(self):
        with mock.patch.object(self.mdadm, "array_device", lambda name: "/dev/md0"), \
                mock.patch.object(self.mdadm, "sync_from", lambda device: None), \
                mock.patch.object(self.mdadm, "kernel_name", lambda name: "md0"), \
                mock.patch("builtins.open", side_effect=OSError(22, "Invalid argument")):
            with self.assertRaisesRegex(fnas.FnasError, "kernel refused to start the check on tank"):
                self.mdadm.scrub_pool("tank")


class PoolDelete(unittest.TestCase):
    """Deleting an array refuses while shares live on it, names the disks it
    would wipe, and needs the pool's own name typed back."""

    def setUp(self):
        self.mdadm = {m.__name__: m for m in MODULES}["fellowship_nas.engines.mdadm"]
        self.status = {"groups": [{"disks": [{"path": "/dev/sdb"}, {"path": "/dev/sdc"}]}], "spares": []}

    def guarded(self, shares):
        return (mock.patch.object(self.mdadm.pools, "find_pool", lambda name: {"name": name}),
                mock.patch.object(self.mdadm.shares, "list_shares", lambda: shares),
                mock.patch.object(self.mdadm, "pool_status", lambda name: self.status),
                mock.patch.object(self.mdadm, "member_disks", lambda status: status["groups"][0]["disks"]))

    def test_delete_refuses_while_shares_live_on_the_pool(self):
        patches = self.guarded([{"name": "docs", "pool": "tank"}, {"name": "media", "pool": "tank"}])
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaisesRegex(fnas.FnasError,
                                        "still holds 2 shares: docs, media. Remove them in Shares first"):
                self.mdadm.delete_pool("tank", "tank")

    def test_delete_needs_the_pools_name_and_lists_the_disks(self):
        patches = self.guarded([])
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            with self.assertRaisesRegex(fnas.FnasError, "wipes /dev/sdb, /dev/sdc"):
                self.mdadm.delete_pool("tank", "wrong")

    def test_delete_stops_the_array_and_wipes_its_disks(self):
        calls = []
        patches = self.guarded([]) + (
            mock.patch.object(self.mdadm, "run",
                              lambda *c, **kw: calls.append(c) or SimpleNamespace(stdout="", stderr="", returncode=0)),
            mock.patch.object(self.mdadm.shares, "write_config", lambda **kw: None),
            mock.patch.object(self.mdadm, "array_device", lambda name: "/dev/md0"),
            mock.patch.object(self.mdadm, "forget_mount", lambda name: calls.append(("forget_mount", name))),
            mock.patch.object(self.mdadm, "save_conf", lambda: None))
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            self.mdadm.delete_pool("tank", "tank")
        self.assertIn(("umount", "/mnt/tank"), calls)
        self.assertIn(("mdadm", "--stop", "/dev/md0"), calls)
        self.assertIn(("mdadm", "--zero-superblock", "/dev/sdb"), calls)
        self.assertIn(("mdadm", "--zero-superblock", "/dev/sdc"), calls)
        self.assertIn(("forget_mount", "tank"), calls)


class ServiceControl(unittest.TestCase):
    """Start, stop and restart for the services an owner manages; a stop they
    asked for is off, not a failure, and a crash without a mark still is one."""

    def setUp(self):
        by_module = {m.__name__: m for m in MODULES}
        self.services = by_module["fellowship_nas.services"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = mock.patch.object(self.services, "SERVICE_STATE", os.path.join(directory.name, "services.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def shown(self, state, sftp=True):
        return (mock.patch.object(self.services, "unit_state", lambda unit: state),
                mock.patch.object(self.services.ssh, "settings", lambda: {"enabled": sftp, "passwords": True}),
                mock.patch.object(self.services.ssh, "root_ssh", lambda: False),
                mock.patch.object(self.services.accounts, "is_separated", lambda: True),
                mock.patch.object(self.services, "run",
                                  lambda *c, **kw: self.calls.append(c) or SimpleNamespace(stdout="", stderr="",
                                                                                           returncode=0)),
                mock.patch.object(self.services.shares, "list_shares",
                                  lambda: [{"name": "docs", "smb": True, "sftp": True}]))

    def entry(self, name):
        return next(s for s in self.services.controlled() if s["name"] == name)

    def test_a_stop_is_remembered_and_health_stays_quiet(self):
        with contextlib.ExitStack() as stack:
            for patcher in self.shown("stopped"):
                stack.enter_context(patcher)
            self.services.control("smb", "stop")
            self.assertIn(("systemctl", "stop", "smbd.service"), self.calls)
            self.assertTrue(self.services.owner_stopped("smbd.service"))
            self.assertEqual(self.entry("smb")["state"], "off")
            self.assertEqual(next(p for p in self.services.protocols() if p["name"] == "smb")["state"], "off")
            self.assertFalse([f for f in fnas.service_findings() if f["key"] == "service-smb"])

    def test_starting_clears_the_mark(self):
        with contextlib.ExitStack() as stack:
            for patcher in self.shown("stopped"):
                stack.enter_context(patcher)
            self.services.control("smb", "stop")
            self.services.control("smb", "start")
            self.assertFalse(self.services.owner_stopped("smbd.service"))
            self.assertIn(("systemctl", "start", "smbd.service"), self.calls)

    def test_stopping_the_daemon_says_sftp_goes_with_it(self):
        with contextlib.ExitStack() as stack:
            for patcher in self.shown("stopped"):
                stack.enter_context(patcher)
            self.services.control("ssh", "stop")
            self.assertEqual(self.entry("ssh")["also_stops"], ["sftp"])
            self.assertEqual(self.entry("ssh")["state"], "off")
            self.assertEqual(next(p for p in self.services.protocols() if p["name"] == "sftp")["state"], "off")
            self.assertFalse([f for f in fnas.service_findings()
                              if f["key"] in ("service-ssh.service", "service-sftp")])

    def test_a_crash_without_a_mark_is_still_a_failure(self):
        with contextlib.ExitStack() as stack:
            for patcher in self.shown("stopped"):
                stack.enter_context(patcher)
            self.assertEqual(self.entry("smb")["state"], "stopped")
            self.assertIn("service-smb", [f["key"] for f in fnas.service_findings()])

    def test_only_the_listed_services_and_actions(self):
        with self.assertRaisesRegex(fnas.FnasError, "no such service"):
            self.services.control("web", "stop")
        with self.assertRaisesRegex(fnas.FnasError, "unknown service action"):
            self.services.control("smb", "reload")
