"""Unit tests for fnas. Nothing here touches a real disk: every external
command goes through a fake, and a test fails if fnas runs one it didn't expect."""
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
    """Every name in the package, wherever it lives, as if fnas were one file."""

    def __getattr__(self, name):
        for module in MODULES:
            if name in vars(module):
                return vars(module)[name]
        raise AttributeError(name)


fnas = Package()


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

    def __call__(self, *command, check=True, stdin=None):
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
                            ("ROOT", "/nonexistent")):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use(self, fake):
        for patcher in (patch("run", fake),
                        patch("by_id_links", links),
                        patch("unavailable", lambda: ""),
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
        return fnas.layouts([size] * count)

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
                    low, high = fnas.WIDTHS[option["layout"]]
                    self.assertTrue(low <= option["width"] <= high)
        self.assertEqual({o["layout"] for o in self.options(1)}, {"single"})
        self.assertNotIn("raidz2", {o["layout"] for o in self.options(3)})

    def test_usable_space_and_survival(self):
        option = next(o for o in self.options(16) if o["recommended"])
        self.assertEqual(option["usable"], 2 * 6 * 4 * GB)
        self.assertEqual(option["survives"], 2)
        mirror = next(o for o in self.options(4) if o["layout"] == "mirror" and o["width"] == 2 and not o["spares"])
        self.assertEqual((mirror["usable"], mirror["survives"]), (2 * 4 * GB, 1))

    def test_mixed_sizes_group_alike_and_spare_the_largest(self):
        sizes = [4 * GB, 8 * GB, 4 * GB, 8 * GB, 8 * GB]
        groups, spares = fnas.plan("mirror", sizes, 2, 1)
        self.assertEqual([[sizes[i] for i in g] for g in groups], [[4 * GB, 4 * GB], [8 * GB, 8 * GB]])
        self.assertEqual(sizes[spares[0]], 8 * GB)
        option = next(o for o in fnas.layouts(sizes[:4]) if o["layout"] == "raidz1" and o["width"] == 4)
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

    def __call__(self, *command, check=True, stdin=None):
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
        tank, old = fnas.scan_pools()
        self.assertEqual((tank["pool"], tank["id"], tank["state"]), ("tank", "123", "ONLINE"))
        self.assertIn("read-only mode on this system. It cannot be accessed", tank["status"])
        self.assertEqual((old["pool"], old["state"]), ("old", "UNAVAIL"))

    def test_nothing_to_import_is_not_an_error(self):
        self.use(Fake([]))
        self.assertEqual(fnas.scan_pools(), [])

    def test_real_failure_is_reported(self):
        fake = self.use(Fake([]))
        real = fake.__call__

        def broken(*command, check=True):
            result = real(*command, check=check)
            result.stderr = "permission denied"
            return result
        with patch("run", broken):
            with self.assertRaisesRegex(fnas.FnasError, "permission denied"):
                fnas.scan_pools()


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

    def __call__(self, *command, check=True, stdin=None):
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
        for folder in (self.smb_dir, self.config_dir, self.exports_dir):
            os.mkdir(folder)
        lan_file = os.path.join(self.root, "nas-lan")
        with open(lan_file, "w") as file:
            file.write("192.168.1.0/24\n")
        owner = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        self.chown, self.chmod = mock.Mock(), mock.Mock()
        for patcher in (patch("SMB_DIR", self.smb_dir),
                        patch("LAN_FILE", lan_file),
                        patch("EXPORTS_DIR", self.exports_dir),
                        patch("EXPORTS_FILE", os.path.join(self.exports_dir, "fellowship.exports")),
                        mock.patch.object(fnas.pwd, "getpwnam", side_effect=lambda n: {"tester": owner}[n]),
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

    def smb_file(self, *path):
        with open(os.path.join(self.smb_dir, *path)) as file:
            return file.read()

    def exports(self):
        with open(os.path.join(self.exports_dir, "fellowship.exports")) as file:
            return [line for line in file.read().splitlines() if not line.startswith("#")]

    def test_shared_nfs_squashes_to_the_share_account(self):
        fake = self.fake()
        fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertIn(("zfs", "create", "-p", "tank/shares/media"), fake.calls)
        self.assertEqual(self.exports(), ['"/tank/shares/media" 192.168.1.0/24'
                                          '(rw,sync,no_subtree_check,all_squash,anonuid=3000,anongid=3000)'])
        self.assertEqual(self.settings(), {"media": {"mode": "shared", "nfs": True, "smb": False,
                                                     "policy": fnas.DEFAULT_POLICY}})
        self.chown.assert_called_once_with("/tank/shares/media", 3000, 3000)
        self.chmod.assert_called_once_with("/tank/shares/media", 0o2775)
        # ZFS's own sharing stays off, so NFS has one owner
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "set")])

    def test_matched_nfs_belongs_to_its_owner(self):
        self.fake()
        fnas.create_share("chris", "tank", "matched", "tester", True, False)
        self.assertEqual(self.exports(), ['"/tank/shares/chris" 192.168.1.0/24(rw,sync,no_subtree_check,root_squash)'])
        self.chown.assert_called_once_with("/tank/shares/chris", 1000, 1000)
        self.chmod.assert_called_once_with("/tank/shares/chris", 0o2770)

    def test_smb_only_share_is_not_exported(self):
        self.fake()
        fnas.create_share("media", "tank", "shared", None, False, True)
        self.assertEqual(self.exports(), [])
        self.assertIn("[media]\n", self.smb_file("shares", "media.conf"))

    def test_share_refusals(self):
        cases = [
            (("../x", "tank", "shared", None, True, False), "share names"),
            (("a;b", "tank", "shared", None, True, False), "share names"),
            (("Global", "tank", "shared", None, True, False), "share names"),
            (("media", "tank", "shared", None, False, False), "at least one"),
            (("media", "tank", "matched", None, True, False), "need an --owner"),
            (("media", "tank", "shared", "tester", True, False), "doesn't apply"),
            (("media", "tank", "matched", "nobody2", True, False), "no such user"),
            (("media", "other", "shared", None, True, False), "no imported pool"),
        ]
        for args, message in cases:
            fake = self.fake()
            with self.subTest(args), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.create_share(*args)
            self.assertEqual(fake.calls, [])

    def test_read_only_pool_takes_no_new_shares(self):
        fake = self.fake(pools="tank\t100\t1\t99\tONLINE\ton\n")
        with self.assertRaisesRegex(fnas.FnasError, "read-only"):
            fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])

    def test_names_differing_only_by_case_clash(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"))
        with self.assertRaisesRegex(fnas.FnasError, "already exists"):
            fnas.create_share("Media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])

    def test_never_takes_over_an_existing_dataset(self):
        fake = self.fake(existing=True)
        with self.assertRaisesRegex(fnas.FnasError, "isn't a share"):
            fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])
        self.chown.assert_not_called()

    def test_needs_a_lan_subnet(self):
        fake = self.fake()
        with patch("LAN_FILE", "/nonexistent/nas-lan"):
            with self.assertRaisesRegex(fnas.FnasError, "no LAN subnet"):
                fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])

    def test_smb_needs_its_package(self):
        self.fake()
        with patch("SMB_DIR", "/nonexistent"):
            with self.assertRaisesRegex(fnas.FnasError, "fellowship-nas-smb"):
                fnas.create_share("media", "tank", "shared", None, False, True)

    def test_export_unshares_first_and_shares_again_if_it_fails(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": True})
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
            "name": "media", "pool": "tank", "engine": "zfs", "dataset": "tank/shares/media",
            "path": "/tank/shares/media", "mode": "shared", "nfs": True, "smb": True,
            "policy": fnas.DEFAULT_POLICY, "readonly": False}])

    def test_damaged_settings_are_ignored(self):
        self.write_settings(media={"mode": "evil", "nfs": True},
                            docs={"mode": "matched", "nfs": "yes", "smb": True,
                                  "policy": {"hourly": -5, "daily": 7, "monthly": "x"}})
        self.fake(volume_line("media") + volume_line("docs"))
        [docs] = fnas.list_shares()
        self.assertEqual((docs["name"], docs["nfs"], docs["smb"]), ("docs", False, True))
        self.assertEqual(docs["policy"], {"hourly": 24, "daily": 7, "monthly": 6})

    def test_old_settings_move_into_the_pool(self):
        fake = self.fake(share_line("media", sharenfs="rw=@x", smb="on", policy="0,7,1"))
        [media] = fnas.list_shares()
        self.assertEqual((media["mode"], media["nfs"], media["smb"]), ("shared", True, True))
        fnas.migrate()
        self.assertEqual(self.settings(), {"media": {"mode": "shared", "nfs": True, "smb": True,
                                                     "policy": {"hourly": 0, "daily": 7, "monthly": 1}}})
        self.assertIn(("zfs", "set", "sharenfs=off", "tank/shares/media"), fake.calls)
        self.assertIn(("zfs", "inherit", fnas.LEGACY_MODE, "tank/shares/media"), fake.calls)

    def test_smb_files_follow_the_settings(self):
        self.write_settings(media={"mode": "shared", "nfs": False, "smb": True},
                            chris={"mode": "matched", "nfs": False, "smb": True},
                            nfsonly={"mode": "shared", "nfs": True, "smb": False})
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

    def test_read_only_pool_shares_read_only(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": True})
        self.fake(volume_line("media"), pools="tank\t100\t1\t99\tONLINE\ton\n")
        fnas.write_config()
        self.assertIn("(ro,", self.exports()[0])
        self.assertIn("read only = yes\n", self.smb_file("shares", "media.conf"))

    def test_reloads_are_asked_for(self):
        fake = self.fake()
        fnas.write_config()
        self.assertEqual(fake.calls, [("exportfs", "-ra"), ("smbcontrol", "smbd", "reload-config")])

    def test_set_turns_protocols_on_and_off(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": True})
        fake = self.fake(volume_line("media"))
        fnas.write_config()
        fnas.set_share("media", "off", "off")
        self.assertEqual((self.settings()["media"]["nfs"], self.settings()["media"]["smb"]), (False, False))
        self.assertEqual(self.exports(), [])
        self.assertEqual(os.listdir(os.path.join(self.smb_dir, "shares")), [])
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "destroy")])

    def test_set_needs_a_change(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": True})
        self.fake(volume_line("media"))
        with self.assertRaisesRegex(fnas.FnasError, "nothing to change"):
            fnas.set_share("media", None, None)

    def test_delete_lists_what_is_lost(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"), snapshots="tank/shares/media@a\ntank/shares/media@b\n")
        with self.assertRaises(fnas.FnasError) as refused:
            fnas.delete_share("media", "delete media")
        self.assertIn("tank/shares/media@a", str(refused.exception))
        self.assertIn("delete media and 2 snapshots", str(refused.exception))
        self.assertEqual(fake.calls, [])

    def test_delete_with_confirmation(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": False})
        fake = self.fake(volume_line("media"), snapshots="tank/shares/media@a\n")
        fnas.delete_share("media", "delete media and 1 snapshot")
        self.assertIn(("zfs", "destroy", "-r", "tank/shares/media"), fake.calls)
        self.assertEqual(self.settings(), {})
        self.assertEqual(self.exports(), [])

    def test_lan_change_rewrites_both_protocols(self):
        self.write_settings(media={"mode": "shared", "nfs": True, "smb": False},
                            smbonly={"mode": "shared", "nfs": False, "smb": True})
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

    def __call__(self, *command, check=True, stdin=None):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ("zpool", "status"):
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
                     "MSMTP_FILE", "ALERT_LOG", "ALERT_LOCK", "CHECK_STATE", "INSTALL_ID_FILE"):
            paths[name] = self.path(name)
        for name, value in paths.items():
            patcher = patch(name, value)
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

        def restored(*command, check=True, stdin=None):
            if command[0] == "syncoid":
                fake.datasets = volume_line("media")
                fake.existing = False
            return real(fake, *command, check=check, stdin=stdin)

        self.use(restored)
        fnas.restore_backup("media", "usb", None)
        self.assertIn(("syncoid", "--quiet", "--no-sync-snap", "--sendoptions=p",
                       "usb/fellowship-backup/tank/shares/media", "tank/shares/media"), fake.calls)
        [media] = fnas.list_shares()
        self.assertEqual((media["mode"], media["nfs"], media["smb"]), ("shared", True, False))

    def test_backup_carries_the_share_settings(self):
        self.two_pools().datasets = volume_line("media")
        fnas.write_json(os.path.join(self.pool_dir, "shares.json"),
                        {"version": 1, "shares": {"media": {"mode": "matched", "nfs": False, "smb": True}}})
        fnas.write_json(fnas.BACKUP_FILE, {"source": "tank", "target": "usb", "since": 0})
        fnas.run_backup()
        copied = fnas.read_json(os.path.join(self.pool_dir, "backup-of-tank.json"))
        self.assertEqual(copied["shares"]["media"]["mode"], "matched")

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

    def test_a_full_pool_alerts_once_then_daily_then_clears(self):
        self.healthy(pools="tank\t100\t85\t15\tONLINE\toff\n")
        fnas.run_checks()
        fnas.run_checks()
        self.assertEqual(self.titles(), ["Pool tank is 85% full"])

        state = fnas.read_json(fnas.CHECK_STATE)
        state["pool-full-warning-tank"]["sent"] -= fnas.REMIND_AFTER
        fnas.write_json(fnas.CHECK_STATE, state)
        fnas.run_checks()
        self.assertEqual(len(self.alerts()), 2)

        self.healthy()
        fnas.run_checks()
        self.assertEqual(self.titles()[-1], "Resolved: Pool tank is 85% full")
        self.assertEqual(fnas.read_json(fnas.CHECK_STATE), {})

    def test_a_nearly_full_pool_is_critical(self):
        found = fnas.pool_findings([{"name": "tank", "allocated": 95, "size": 100}])
        self.assertEqual(found[0]["severity"], "critical")

    def test_scrub_age_comes_from_zpool_status(self):
        status = ("  pool: tank\n state: ONLINE\n  scan: scrub repaired 0B in 00:00:01 with 0 errors "
                  "on Sun Nov  8 00:24:01 2026\nconfig:\n")
        self.healthy(status=status)
        expected = fnas.datetime.datetime(2026, 11, 8, 0, 24, 1).timestamp()
        self.assertEqual(fnas.last_scrub("tank"), expected)
        self.assertEqual(fnas.scrub_findings(["tank"], expected + fnas.SCRUB_MAX_AGE - 1), [])
        self.assertEqual(len(fnas.scrub_findings(["tank"], expected + fnas.SCRUB_MAX_AGE + 1)), 1)

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
    def test_rejects_unknown_layout(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            fnas.build_parser().parse_args(["pool", "create", "tank", "stripe", "/dev/vdb"])


if __name__ == "__main__":
    unittest.main()


class DiskHealth(unittest.TestCase):
    DISK = {"path": "/dev/sda", "by_id": "/dev/disk/by-id/ata-X", "model": "X", "serial": "1",
            "size": 1, "state": "free"}

    def health(self, data):
        output = SimpleNamespace(stdout=json.dumps(data) if data is not None else "", returncode=0)
        with patch("run", return_value=output):
            return fnas.disk_health(self.DISK)

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
