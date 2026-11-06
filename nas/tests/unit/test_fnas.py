"""Unit tests for fnas. Nothing here touches a real disk: every external
command goes through a fake, and a test fails if fnas runs one it didn't expect."""
import importlib.machinery
import importlib.util
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

PATH = os.path.join(os.path.dirname(__file__), "../../packages/fellowship-nas-tools/fnas")
loader = importlib.machinery.SourceFileLoader("fnas", PATH)
spec = importlib.util.spec_from_loader("fnas", loader)
fnas = importlib.util.module_from_spec(spec)
loader.exec_module(fnas)

GB = 10 ** 9


def disk(name, children=(), fstype=None, mounts=(None,), serial=None):
    return {"name": name, "path": f"/dev/{name}", "type": "disk", "size": 100 * GB,
            "model": "Test", "serial": serial or name.upper(), "fstype": fstype,
            "mountpoints": list(mounts), "children": list(children)}


def part(name, fstype=None, mounts=(None,)):
    return {"name": name, "path": f"/dev/{name}", "type": "part", "size": GB, "model": None,
            "serial": None, "fstype": fstype, "mountpoints": list(mounts)}


OS_DISK = disk("vda", [part("vda1", "vfat", ["/boot/efi"]), part("vda2", "ext4", ["/"])])
EMPTY = [disk(n) for n in ("vdb", "vdc", "vdd", "vde")]


class Fake:
    """Answers the read-only commands and records everything else."""

    def __init__(self, devices, pools="", imports="", import_rc=1):
        self.devices, self.pools, self.imports, self.import_rc = devices, pools, imports, import_rc
        self.calls = []

    def __call__(self, *command, check=True):
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
        else:
            self.calls.append(command)
        return ok


def links():
    names = "vda vdb vdc vdd vde".split()
    return {f"/dev/{n}": f"/dev/disk/by-id/virtio-{n.upper()}" for n in names}


class Base(unittest.TestCase):
    def use(self, fake):
        for patcher in (mock.patch.object(fnas, "run", fake),
                        mock.patch.object(fnas, "by_id_links", links)):
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
        calls = self.create(Fake(EMPTY), "tank", "raidz1", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])
        self.assertEqual(calls[0], (
            "zpool", "create", "-o", "ashift=12", "-O", "compression=lz4", "-O", "atime=off",
            "-O", "xattr=sa", "-O", "acltype=posixacl", "-O", "dnodesize=auto", "tank", "raidz1",
            "/dev/disk/by-id/virtio-VDB", "/dev/disk/by-id/virtio-VDC", "/dev/disk/by-id/virtio-VDD"))
        self.assertEqual(calls[1:], [("zfs", "create", f"tank/{d}") for d in fnas.DEFAULT_DATASETS])

    def test_never_passes_force(self):
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
        self.refused("at least 2", "tank", "mirror", ["/dev/vdb"])
        self.refused("at least 3", "tank", "raidz1", ["/dev/vdb", "/dev/vdc"])
        self.refused("at least 4", "tank", "raidz2", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])
        self.refused("exactly 1", "tank", "single", ["/dev/vdb", "/dev/vdc"])
        self.refused("even", "tank", "mirror", ["/dev/vdb", "/dev/vdc", "/dev/vdd"])

    def test_refuses_name_of_importable_pool(self):
        self.refused("already exists", "tank", "single", ["/dev/vdb"],
                     imports="   pool: tank\n     id: 1\n  state: ONLINE\n")

    def test_refuses_bad_names(self):
        for name in ("Tank", "1tank", "-rf", "a b", "ta;nk", "mirror1", "raidz", "x" * 33, ""):
            self.refused("pool names|reserves", name, "single", ["/dev/vdb"])


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
        with mock.patch.object(fnas, "run", broken):
            with self.assertRaisesRegex(fnas.FnasError, "permission denied"):
                fnas.scan_pools()


class Import(Base):
    IMPORTS = "   pool: tank\n     id: 123\n  state: ONLINE\n"

    def test_read_only(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        fnas.import_pool("tank", True)
        self.assertEqual(fake.calls, [("zpool", "import", "-d", fnas.BY_ID_DIR, "-o", "readonly=on", "tank")])

    def test_by_id(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        fnas.import_pool("123", False)
        self.assertEqual(fake.calls, [("zpool", "import", "-d", fnas.BY_ID_DIR, "123")])

    def test_only_imports_what_a_scan_found(self):
        fake = self.use(Fake([], imports=self.IMPORTS, import_rc=0))
        with self.assertRaisesRegex(fnas.FnasError, "no importable pool"):
            fnas.import_pool("-f", False)
        self.assertEqual(fake.calls, [])


class Parser(unittest.TestCase):
    def test_rejects_unknown_layout(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            fnas.build_parser().parse_args(["pool", "create", "tank", "stripe", "/dev/vdb"])


if __name__ == "__main__":
    unittest.main()
