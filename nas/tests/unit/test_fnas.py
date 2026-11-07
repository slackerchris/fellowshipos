"""Unit tests for fnas. Nothing here touches a real disk: every external
command goes through a fake, and a test fails if fnas runs one it didn't expect."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
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


def share_line(name, mode="shared", sharenfs="off", smb="off", pool="tank"):
    dataset = f"{pool}/shares/{name}"
    return f"{dataset}\t/{dataset}\t{sharenfs}\t{mode}\t{smb}\n"


class ShareFake(Fake):
    """Adds the pools and datasets that sharing commands read."""

    def __init__(self, datasets="", snapshots="", existing=False):
        super().__init__([])
        self.datasets, self.snapshots, self.existing = datasets, snapshots, existing

    def __call__(self, *command, check=True):
        ok = SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:2] == ("zpool", "list") and "-Hp" in command:
            ok.stdout = "tank\t100\t1\t99\tONLINE\toff\n"
        elif command[:3] == ("zfs", "list", "-H") and "filesystem" in command:
            ok.stdout = self.datasets
        elif command[:3] == ("zfs", "list", "-H") and "snapshot" in command:
            ok.stdout = self.snapshots
        elif command[:3] == ("zfs", "list", "-H"):
            ok.returncode = 0 if self.existing else 1
        elif command[:2] == ("zfs", "get"):
            ok.stdout = "/" + command[-1] + "\n"
        else:
            return super().__call__(*command, check=check)
        return ok


class Sharing(Base):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.smb_dir = os.path.join(directory.name, "samba")
        os.mkdir(self.smb_dir)
        lan_file = os.path.join(directory.name, "nas-lan")
        with open(lan_file, "w") as file:
            file.write("192.168.1.0/24\n")
        owner = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        self.chown, self.chmod = mock.Mock(), mock.Mock()
        for patcher in (mock.patch.object(fnas, "SMB_DIR", self.smb_dir),
                        mock.patch.object(fnas, "LAN_FILE", lan_file),
                        mock.patch.object(fnas.pwd, "getpwnam", side_effect=lambda n: {"tester": owner}[n]),
                        mock.patch.object(fnas.os, "chown", self.chown),
                        mock.patch.object(fnas.os, "chmod", self.chmod)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def smb_file(self, *path):
        with open(os.path.join(self.smb_dir, *path)) as file:
            return file.read()

    def test_shared_nfs_squashes_to_the_share_account(self):
        fake = self.use(ShareFake())
        fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertIn(("zfs", "set", "sharenfs=rw=@192.168.1.0/24,all_squash,"
                       "anonuid=3000,anongid=3000", "tank/shares/media"), fake.calls)
        self.chown.assert_called_once_with("/tank/shares/media", 3000, 3000)
        self.chmod.assert_called_once_with("/tank/shares/media", 0o2775)

    def test_matched_nfs_belongs_to_its_owner(self):
        fake = self.use(ShareFake())
        fnas.create_share("chris", "tank", "matched", "tester", True, False)
        self.assertIn(("zfs", "set", "sharenfs=rw=@192.168.1.0/24,root_squash",
                       "tank/shares/chris"), fake.calls)
        self.chown.assert_called_once_with("/tank/shares/chris", 1000, 1000)
        self.chmod.assert_called_once_with("/tank/shares/chris", 0o2770)

    def test_smb_only_share_is_not_exported(self):
        fake = self.use(ShareFake())
        fnas.create_share("media", "tank", "shared", None, False, True)
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "set")])
        self.assertIn(("zfs", "create", "-p", "-o", f"{fnas.MODE_PROPERTY}=shared",
                       "-o", f"{fnas.SMB_PROPERTY}=on", "tank/shares/media"), fake.calls)

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
            fake = self.use(ShareFake())
            with self.subTest(args), self.assertRaisesRegex(fnas.FnasError, message):
                fnas.create_share(*args)
            self.assertEqual(fake.calls, [])

    def test_names_differing_only_by_case_clash(self):
        fake = self.use(ShareFake(share_line("media")))
        with self.assertRaisesRegex(fnas.FnasError, "already exists"):
            fnas.create_share("Media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])

    def test_never_takes_over_an_existing_dataset(self):
        fake = self.use(ShareFake(existing=True))
        with self.assertRaisesRegex(fnas.FnasError, "isn't a share"):
            fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])
        self.chown.assert_not_called()

    def test_needs_a_lan_subnet(self):
        fake = self.use(ShareFake())
        with mock.patch.object(fnas, "LAN_FILE", "/nonexistent/nas-lan"):
            with self.assertRaisesRegex(fnas.FnasError, "no LAN subnet"):
                fnas.create_share("media", "tank", "shared", None, True, False)
        self.assertEqual(fake.calls, [])

    def test_smb_needs_its_package(self):
        self.use(ShareFake())
        with mock.patch.object(fnas, "SMB_DIR", "/nonexistent"):
            with self.assertRaisesRegex(fnas.FnasError, "fellowship-nas-smb"):
                fnas.create_share("media", "tank", "shared", None, False, True)

    def test_failed_export_brings_shares_back(self):
        fake = ShareFake()

        def busy(*command, check=True):
            if command[:2] == ("zpool", "export"):
                fake.calls.append(command)
                return SimpleNamespace(stdout="", stderr="pool or dataset is busy", returncode=1)
            return fake(*command, check=check)

        self.use(busy)
        with self.assertRaisesRegex(fnas.FnasError, "disconnect any clients"):
            fnas.export_pool("tank")
        self.assertEqual(fake.calls, [("zpool", "export", "tank"), ("zfs", "share", "-a")])

    def test_list_ignores_other_datasets(self):
        other = "tank/shares\t/tank/shares\toff\t-\t-\ntank/home\t/tank/home\toff\t-\t-\n"
        nested = share_line("media/old")
        unmanaged = share_line("plain", mode="-")
        self.use(ShareFake(other + nested + unmanaged + share_line("media", sharenfs="rw=@x", smb="on")))
        self.assertEqual(fnas.list_shares(), [{
            "name": "media", "pool": "tank", "dataset": "tank/shares/media",
            "path": "/tank/shares/media", "mode": "shared", "nfs": True, "smb": True}])

    def test_smb_files_follow_zfs(self):
        self.use(ShareFake(share_line("media", smb="on") + share_line("chris", "matched", smb="on")
                           + share_line("nfsonly")))
        fnas.write_smb_config()
        self.assertEqual(sorted(os.listdir(os.path.join(self.smb_dir, "shares"))),
                         ["chris.conf", "media.conf"])
        master = self.smb_file("shares.conf")
        self.assertEqual(master.count("include = "), 2)
        media = self.smb_file("shares", "media.conf")
        self.assertIn("[media]\npath = /tank/shares/media\n", media)
        self.assertIn("hosts allow = 192.168.1.0/24 127.0.0.1\n", media)
        self.assertIn("force user = nas-share\n", media)
        self.assertNotIn("shadow:localtime", media)
        self.assertNotIn("force user", self.smb_file("shares", "chris.conf"))

    def test_smb_file_goes_when_share_stops(self):
        self.use(ShareFake(share_line("media", smb="on")))
        fnas.write_smb_config()
        self.use(ShareFake(share_line("media")))
        fnas.write_smb_config()
        self.assertEqual(os.listdir(os.path.join(self.smb_dir, "shares")), [])
        self.assertEqual(self.smb_file("shares.conf"), "")

    def test_smb_reload_is_asked_for(self):
        fake = self.use(ShareFake())
        fnas.write_smb_config()
        self.assertEqual(fake.calls, [("smbcontrol", "smbd", "reload-config")])

    def test_set_turns_protocols_on_and_off(self):
        fake = self.use(ShareFake(share_line("media", sharenfs="rw=@x", smb="on")))
        fnas.set_share("media", "off", "off")
        self.assertIn(("zfs", "set", "sharenfs=off", "tank/shares/media"), fake.calls)
        self.assertIn(("zfs", "set", f"{fnas.SMB_PROPERTY}=off", "tank/shares/media"), fake.calls)
        self.assertFalse([c for c in fake.calls if c[:2] == ("zfs", "destroy")])

    def test_set_needs_a_change(self):
        self.use(ShareFake(share_line("media")))
        with self.assertRaisesRegex(fnas.FnasError, "nothing to change"):
            fnas.set_share("media", None, None)

    def test_delete_lists_what_is_lost(self):
        snapshots = "tank/shares/media@a\ntank/shares/media@b\n"
        fake = self.use(ShareFake(share_line("media"), snapshots))
        with self.assertRaises(fnas.FnasError) as refused:
            fnas.delete_share("media", "delete media")
        self.assertIn("tank/shares/media@a", str(refused.exception))
        self.assertIn("delete media and 2 snapshots", str(refused.exception))
        self.assertEqual(fake.calls, [])

    def test_delete_with_confirmation(self):
        fake = self.use(ShareFake(share_line("media"), "tank/shares/media@a\n"))
        fnas.delete_share("media", "delete media and 1 snapshot")
        self.assertIn(("zfs", "destroy", "-r", "tank/shares/media"), fake.calls)

    def test_lan_change_updates_nfs_shares_only(self):
        fake = self.use(ShareFake(share_line("media", sharenfs="rw=@x") + share_line("smbonly", smb="on")))
        self.assertEqual(fnas.set_lan("10.1.2.3/16"), "10.1.0.0/16")
        changed = [c for c in fake.calls if c[:2] == ("zfs", "set")]
        self.assertEqual(len(changed), 1)
        self.assertIn("rw=@10.1.0.0/16,", changed[0][2])
        self.assertEqual(fnas.lan_subnet(), "10.1.0.0/16")
        self.assertIn("hosts allow = 10.1.0.0/16 ", self.smb_file("shares", "smbonly.conf"))

    def test_subnets_must_be_private_lans(self):
        for value in ("0.0.0.0/0", "*", "8.8.8.0/24", "10.0.0.0/7", "192.168.1.1/32", "fd00::/8", "a,no_root_squash"):
            with self.subTest(value), self.assertRaises(fnas.FnasError):
                fnas.check_subnet(value)
        self.assertEqual(fnas.check_subnet("192.168.1.77/24"), "192.168.1.0/24")


class Parser(unittest.TestCase):
    def test_rejects_unknown_layout(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            fnas.build_parser().parse_args(["pool", "create", "tank", "stripe", "/dev/vdb"])


if __name__ == "__main__":
    unittest.main()
