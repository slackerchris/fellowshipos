"""Unit tests for fnas. Nothing here touches a real disk: every external
command goes through a fake, and a test fails if fnas runs one it didn't expect."""
import contextlib
import importlib
import json
import os
import pkgutil
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
                        # These tests are the ZFS world; the array engine has its
                        # own class, and would ask the fake about arrays otherwise
                        mock.patch.object(mdadm, "unavailable", lambda: "mdadm isn't installed"),
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
            "-O", "xattr=sa", "-O", "acltype=posixacl", "-O", "dnodesize=auto", "tank", "raidz1",
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
                                      ("zfs", "mount", "-a")])

    def test_a_pool_made_here_keeps_its_place(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0, mounted_by="local"))
        fnas.import_pool("tank", False)
        self.assertEqual(fake.calls, [(*self.IMPORT, "-N", "tank"), ("zfs", "mount", "-a")])

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
            "mode": "everyone", "access": self.everyone(), "nfs": True, "smb": False, "policy": fnas.DEFAULT_POLICY,
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
            "policy": fnas.DEFAULT_POLICY, "smb_options": fnas.DEFAULT_SMB, "nfs_options": fnas.DEFAULT_NFS,
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
        self.assertIn("delete media and 2 snapshots", str(refused.exception))
        self.assertEqual(fake.calls, [])

    def test_delete_with_confirmation(self):
        self.write_settings(media={"mode": "everyone", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"), snapshots="tank/shares/media@a\n")
        fnas.delete_share("media", "delete media and 1 snapshot")
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
        self.assertEqual(fnas.scrub_findings(["tank"], expected + fnas.SCRUB_MAX_AGE - 1), [])
        self.assertEqual(len(fnas.scrub_findings(["tank"], expected + fnas.SCRUB_MAX_AGE + 1)), 1)

    def test_a_pool_never_checked_falls_due_after_it_was_made(self):
        engine = SimpleNamespace(CHECK_WORD="check", CHECK_SCHEDULE="monthly", last_scrub=lambda pool: 0,
                                 created=lambda pool: 1000, damage=lambda pool: None)
        with patch("engine_of", lambda pool: engine):
            self.assertEqual(fnas.scrub_findings(["vault"], 1000 + fnas.SCRUB_MAX_AGE - 1), [])
            found = fnas.scrub_findings(["vault"], 1000 + fnas.SCRUB_MAX_AGE + 1)
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
                         [("zfs", "ZFS", True), ("mergerfs", "Unraid-style", False),
                          ("mdadm", "Traditional RAID", True), ("btrfs", "Btrfs", False)])
        self.assertEqual(found[3]["reason"], "Not built yet (Phase 14)")

    def test_an_unbuilt_engine_says_so(self):
        with patch("unavailable", lambda: ""), self.assertRaisesRegex(fnas.FnasError, "Btrfs can't be used "
                                                                       r"here: Not built yet \(Phase 14\)"):
            fnas.engine_named("btrfs")
        with self.assertRaisesRegex(fnas.FnasError, "no such engine"):
            fnas.engine_named("lvm")

    def test_an_array_engine_says_why_it_cant_be_used(self):
        with mock.patch.object(mdadm, "unavailable", lambda: "mdadm isn't installed"), \
                self.assertRaisesRegex(fnas.FnasError, "Traditional RAID can't be used here: "
                                                            "mdadm isn't installed"):
            fnas.engine_named("mdadm")

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
        with mock.patch.object(zfs, "unavailable", lambda: ""), \
                mock.patch.object(self.engine, "unavailable", lambda: "mdadm isn't installed"):
            shown = {engine["name"]: engine for engine in pools.engines()}
        self.assertEqual(list(shown), ["zfs", "mergerfs", "mdadm", "btrfs"])
        self.assertTrue(all(engine["summary"] for engine in shown.values()))
        self.assertEqual(shown["mdadm"]["check_button"], "Check array")
        self.assertEqual([f["name"] for f in shown["mdadm"]["filesystems"] if f["recommended"]], ["xfs"])
        # An engine not built yet has only its name, state and summary
        self.assertNotIn("grow", shown["mergerfs"])
        self.assertEqual(pools.engine_title("mdadm"), "Traditional RAID")

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
        with mock.patch.object(self.engine, "detail", lambda name: detail), \
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
        # A recovery is a degraded array, and a failed disk certainly is
        self.assertEqual(self.engine.health(raid), "DEGRADED")
        self.assertEqual(found["/dev/md0"]["members"][0]["state"], "failed")
        self.assertIsNone(found["/dev/md0"]["sync"])
        self.assertEqual(self.engine.health(found["/dev/md0"]), "DEGRADED")

    def test_a_healthy_array(self):
        mdstat = "md0 : active raid1 sdb1[1] sda1[0]\n  1953382464 blocks super 1.2 [2/2] [UU]\n"
        with mock.patch.object(self.engine, "read", lambda path: mdstat):
            raid = self.engine.arrays()["/dev/md0"]
        self.assertEqual(self.engine.health(raid), "ONLINE")

    def test_reading_what_mdadm_can_assemble(self):
        output = SimpleNamespace(stdout=self.SCAN, stderr="", returncode=0)
        with mock.patch.object(self.engine, "run", lambda *command, check=True: output):
            found = self.engine.scan_arrays()
        self.assertEqual([(a["name"], a["device"], a["uuid"]) for a in found],
                         [("tank", "/dev/md/tank", "1a2b:3c4d"), ("old", "/dev/md/old", "5e6f:7a8b")])

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


class Users(unittest.TestCase):
    def setUp(self):
        account = lambda name, uid, shell="/bin/bash": SimpleNamespace(
            pw_name=name, pw_uid=uid, pw_gid=uid, pw_gecos=f"{name.title()},,,", pw_shell=shell)
        group = lambda name, gid, members=(): SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=list(members))
        self.accounts = [account("root", 0), account("tester", 1000), account("kim", 1001, "/usr/sbin/nologin"),
                         account("nas-share", 3000, "/usr/sbin/nologin")]
        self.groups = [group("tester", 1000), group("kim", 1001), group("nas-share", 3000),
                       group("family", 1002, ["kim"]), group("nas-admins", 999, ["tester"])]
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
        with open(shadow, "w") as file:
            file.write("tester:$y$hash:20000:0:99999:7:::\nkim:!:20000:0:99999:7:::\n"
                       "sam:!$y$hash:20000:0:99999:7::1:\n")
        for patcher in (patch("run", run), patch("SHADOW", shadow),
                        mock.patch.object(fnas.pwd, "getpwall", lambda: self.accounts),
                        mock.patch.object(fnas.grp, "getgrall", lambda: self.groups),
                        mock.patch.object(fnas.grp, "getgrnam",
                                          lambda n: next(g for g in self.groups if g.gr_name == n)),
                        mock.patch.object(fnas.grp, "getgrgid",
                                          lambda i: next(g for g in self.groups if g.gr_gid == i))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def commands(self):
        return [command for command, _ in self.calls]

    def test_lists_people_but_not_the_nas_itself(self):
        found = {u["name"]: u for u in fnas.list_users()}
        self.assertEqual(list(found), ["kim", "tester"])
        self.assertEqual((found["kim"]["kind"], found["kim"]["groups"], found["kim"]["smb_password"]),
                         ("shares", ["family"], False))
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
        for patcher in (patch("run", run), patch("SETTINGS_FILE", os.path.join(self.dir, "ssh.json")),
                        patch("DROP_IN", os.path.join(self.dir, "fellowship.conf"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_nas_ssh_and_never_root(self):
        text = fnas.drop_in({"enabled": True, "passwords": False})
        self.assertIn("AllowGroups nas-ssh\n", text)
        self.assertIn("PermitRootLogin no\n", text)
        self.assertIn("PasswordAuthentication no\n", text)

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
