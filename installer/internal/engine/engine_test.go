package engine

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"fellowship-installer/internal/layout"
)

type call struct {
	input string
	args  []string
}

func (c call) String() string { return strings.Join(c.args, " ") }

type fake struct {
	target string
	size   int64
	calls  []call
	fail   string
}

func (f *fake) Run(input string, name string, args ...string) (string, error) {
	c := call{input, append([]string{name}, args...)}
	f.calls = append(f.calls, c)
	line := c.String()
	if f.fail != "" && strings.HasPrefix(line, f.fail) {
		return "", errors.New("exit status 1")
	}
	switch {
	case name == "blockdev":
		return strconv.FormatInt(f.size, 10), nil
	case name == "lsblk":
		return args[len(args)-1] + " disk\n" + Partition(args[len(args)-1], 1) + " part\n", nil
	case name == "pvs":
		return "  /dev/sdz2 oldvg\n", nil
	case name == "vgs":
		return "  fellowship\n", nil
	case name == "blkid":
		return "uuid-of-" + filepath.Base(args[len(args)-1]) + "\n", nil
	case name == "unsquashfs":
		os.MkdirAll(filepath.Join(f.target, "etc"), 0o755)
		os.WriteFile(filepath.Join(f.target, "etc/group"), []byte("root:x:0:\nsudo:x:27:\nvideo:x:44:\n"), 0o644)
	case name == "chroot" && len(args) > 1 && args[1] == "dpkg-query":
		if args[len(args)-1] == "cog" {
			return "", errors.New("not installed")
		}
	}
	return "", nil
}

func settings(disk string) Settings {
	return Settings{Disk: disk, System: 32 * layout.GiB, Hostname: "vault", Timezone: "Europe/London",
		User: "chris", FullName: "Chris", Password: "correct horse", Role: "nas",
		RootPassword: "root's own one", RecoveryKey: "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF-GGGGG-HHHHH"}
}

func setup(t *testing.T, size int64) (*fake, *Engine, *[]string) {
	target := t.TempDir()
	f := &fake{target: target, size: size}
	var steps []string
	e := &Engine{Run: f, Target: target, Squashfs: "/run/live/medium/live/filesystem.squashfs",
		Installed: 3 * layout.GiB, Memory: 8 * layout.GiB, EFI: true, Network: []string{"enp4s0"},
		Step: func(title string) { steps = append(steps, title) }, Log: func(string) {}}
	return f, e, &steps
}

var destructive = []string{"wipefs", "sgdisk", "mkfs.vfat", "mkfs.ext4", "pvcreate", "vgcreate", "mkswap", "partprobe"}

func TestInstallTouchesOnlyTheChosenDisk(t *testing.T) {
	f, e, steps := setup(t, 500*layout.GiB)
	if err := e.Install(settings("/dev/sdb")); err != nil {
		t.Fatal(err)
	}
	if len(*steps) != len(Steps) {
		t.Fatalf("steps %q", *steps)
	}
	for _, c := range f.calls {
		for _, name := range destructive {
			if c.args[0] == name {
				for _, arg := range c.args[1:] {
					// The new system's own volumes are fine
					if strings.HasPrefix(arg, "/dev/") && !strings.HasPrefix(arg, "/dev/sdb") && !strings.HasPrefix(arg, "/dev/fellowship2/") {
						t.Errorf("%s touched another disk: %s", name, c)
					}
				}
			}
		}
	}
}

func find(f *fake, prefix string) []call {
	var found []call
	for _, c := range f.calls {
		if strings.HasPrefix(c.String(), prefix) {
			found = append(found, c)
		}
	}
	return found
}

func TestLayoutOnALargeDisk(t *testing.T) {
	f, e, _ := setup(t, 500*layout.GiB)
	if err := e.Install(settings("/dev/sdb")); err != nil {
		t.Fatal(err)
	}
	partition := find(f, "sgdisk -n")[0].String()
	if !strings.Contains(partition, "3:fellowship-data") || !strings.Contains(partition, "2:8E00") {
		t.Errorf("partitions: %s", partition)
	}
	// The name in use is skipped for the new volume group
	if len(find(f, "vgcreate fellowship2 /dev/sdb2")) != 1 {
		t.Errorf("volume group: %v", find(f, "vgcreate"))
	}
	if len(find(f, "lvcreate -y -L 34359738368b -n root fellowship2")) != 1 {
		t.Errorf("root volume: %v", find(f, "lvcreate"))
	}
	if len(find(f, "wipefs -a /dev/sdb3")) != 1 {
		t.Error("the data partition wasn't cleaned")
	}
}

func TestSmallDiskHasNoDataPartition(t *testing.T) {
	f, e, _ := setup(t, 32*layout.GiB)
	if err := e.Install(settings("/dev/nvme0n1")); err != nil {
		t.Fatal(err)
	}
	if partition := find(f, "sgdisk -n")[0].String(); strings.Contains(partition, "fellowship-data") {
		t.Errorf("a small disk got a data partition: %s", partition)
	}
	if len(find(f, "mkfs.vfat -F 32 -n EFI /dev/nvme0n1p1")) != 1 || len(find(f, "pvcreate -ff -y /dev/nvme0n1p2")) != 1 {
		t.Error("NVMe partitions are named wrongly")
	}
}

func TestSystemIsConfigured(t *testing.T) {
	f, e, _ := setup(t, 500*layout.GiB)
	if err := e.Install(settings("/dev/sdb")); err != nil {
		t.Fatal(err)
	}
	fstab, _ := os.ReadFile(filepath.Join(e.Target, "etc/fstab"))
	for _, want := range []string{"UUID=uuid-of-root\t/\t", "UUID=uuid-of-sdb1\t/boot/efi\t", "UUID=uuid-of-swap\tnone\tswap"} {
		if !strings.Contains(string(fstab), want) {
			t.Errorf("fstab lacks %q:\n%s", want, fstab)
		}
	}
	for path, want := range map[string]string{"etc/hostname": "vault\n", "etc/fellowshipos/install-role": "nas\n",
		"etc/machine-id": ""} {
		if content, _ := os.ReadFile(filepath.Join(e.Target, path)); string(content) != want {
			t.Errorf("%s = %q", path, content)
		}
	}
	if network, _ := os.ReadFile(filepath.Join(e.Target, "etc/network/interfaces.d/enp4s0")); !strings.Contains(string(network), "iface enp4s0 inet dhcp") {
		t.Errorf("network: %q", network)
	}
	purge := find(f, "chroot "+e.Target+" env DEBIAN_FRONTEND=noninteractive LC_ALL=C apt-get purge")[0].String()
	if !strings.Contains(purge, "fellowship-installer") || strings.Contains(purge, " cog") {
		t.Errorf("purge: %s", purge)
	}
	// A separated NAS: root gets its own password, and the account asked for is
	// the first NAS admin, made by fnas in the NAS's store, with no Linux login
	in := "chroot " + e.Target + " env DEBIAN_FRONTEND=noninteractive LC_ALL=C "
	if found := find(f, in+"useradd"); len(found) != 0 {
		t.Errorf("the installer made a Linux account itself: %+v", found)
	}
	if chpasswd := find(f, in+"chpasswd"); len(chpasswd) != 1 || chpasswd[0].input != "root:root's own one\n" {
		t.Errorf("chpasswd: %+v", chpasswd)
	}
	if len(find(f, in+"fnas accounts init")) != 1 {
		t.Error("the NAS's store wasn't made")
	}
	if made := find(f, in+"fnas user create chris --admin --password-stdin --full-name Chris"); len(made) != 1 ||
		made[0].input != "correct horse\n" {
		t.Errorf("the NAS admin: %+v", made)
	}
	if key := find(f, in+"fellowship-recovery-key set"); len(key) != 1 ||
		key[0].input != "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF-GGGGG-HHHHH\n" {
		t.Errorf("the recovery key: %+v", key)
	}
	if len(find(f, "chroot "+e.Target+" env DEBIAN_FRONTEND=noninteractive LC_ALL=C grub-install")) != 2 {
		t.Error("the boot loader and its fallback copy weren't both installed")
	}
	if last := f.calls[len(f.calls)-1].String(); last != "sync" {
		t.Errorf("last command %q", last)
	}
}

// The multi-purpose image isn't separated: its first account is a Linux login
// with sudo, as before
func TestTheMultiImageMakesASudoAccount(t *testing.T) {
	f, e, _ := setup(t, 500*layout.GiB)
	e.WholeDisk = true
	multi := settings("/dev/sdb")
	multi.Role, multi.RootPassword, multi.RecoveryKey = "", "", ""
	if err := e.Install(multi); err != nil {
		t.Fatal(err)
	}
	in := "chroot " + e.Target + " env DEBIAN_FRONTEND=noninteractive LC_ALL=C "
	useradd := find(f, in+"useradd")[0].String()
	if !strings.HasSuffix(useradd, "-G sudo,video chris") {
		t.Errorf("useradd: %s", useradd)
	}
	if chpasswd := find(f, in+"chpasswd"); len(chpasswd) != 1 || chpasswd[0].input != "chris:correct horse\n" {
		t.Errorf("chpasswd: %+v", chpasswd)
	}
	if len(find(f, in+"fnas")) != 0 || len(find(f, in+"fellowship-recovery-key")) != 0 {
		t.Error("the multi-purpose image was separated")
	}
}

// A separated NAS refuses root's password when it is missing or the same as the NAS admin's
func TestRootNeedsItsOwnPassword(t *testing.T) {
	_, e, _ := setup(t, 500*layout.GiB)
	for _, c := range []struct{ root, want string }{
		{"", "at least 8"}, {"short", "at least 8"}, {"correct horse", "different"},
	} {
		s := settings("/dev/sdb")
		s.RootPassword = c.root
		if err := e.Install(s); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("root %q: %v", c.root, err)
		}
	}
	s := settings("/dev/sdb")
	s.RecoveryKey = ""
	if err := e.Install(s); err == nil || !strings.Contains(err.Error(), "recovery key") {
		t.Errorf("no recovery key: %v", err)
	}
}

func TestTheNASImageRecordsItsRole(t *testing.T) {
	_, e, _ := setup(t, 100*layout.GiB)
	if err := e.Install(settings("/dev/sdb")); err != nil {
		t.Fatal(err)
	}
	written, err := os.ReadFile(filepath.Join(e.Target, "etc/fellowshipos/install-role"))
	if err != nil || string(written) != "nas\n" {
		t.Fatalf("the NAS image recorded %q, %v", written, err)
	}
}

func TestTheMultiPurposeImageRecordsNoRole(t *testing.T) {
	// First boot asks what this machine will do, so nothing is chosen here
	_, e, _ := setup(t, 100*layout.GiB)
	which := settings("/dev/sdb")
	which.Role = ""
	if err := e.Install(which); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(e.Target, "etc/fellowshipos/install-role")); !os.IsNotExist(err) {
		t.Fatalf("the multi-purpose image wrote a role anyway: %v", err)
	}
}

func TestAWholeDiskImageKeepsNoDataPartition(t *testing.T) {
	f, e, _ := setup(t, 500*layout.GiB)
	e.WholeDisk = true
	if err := e.Install(settings("/dev/sdb")); err != nil {
		t.Fatal(err)
	}
	if partition := find(f, "sgdisk -n")[0].String(); strings.Contains(partition, "fellowship-data") {
		t.Errorf("a whole-disk image left a data partition: %s", partition)
	}
	if len(find(f, "wipefs -a /dev/sdb3")) != 0 {
		t.Error("a whole-disk image wiped a third partition that doesn't exist")
	}
}

func TestFailureStopsAndUnmounts(t *testing.T) {
	f, e, steps := setup(t, 500*layout.GiB)
	f.fail = "unsquashfs"
	err := e.Install(settings("/dev/sdb"))
	if err == nil || !strings.Contains(err.Error(), "unsquashfs") {
		t.Fatalf("error %v", err)
	}
	if (*steps)[len(*steps)-1] != "Copying FellowshipOS" {
		t.Errorf("stopped at %q", (*steps)[len(*steps)-1])
	}
	if last := f.calls[len(f.calls)-1].String(); last != "umount -R "+e.Target {
		t.Errorf("last command %q", last)
	}
	if len(find(f, "chroot")) != 0 {
		t.Error("carried on after the failure")
	}
}

func TestRefusals(t *testing.T) {
	_, e, _ := setup(t, 500*layout.GiB)
	e.EFI = false
	if err := e.Install(settings("/dev/sdb")); err == nil || !strings.Contains(err.Error(), "UEFI") {
		t.Errorf("BIOS: %v", err)
	}
	bad := []func(*Settings){
		func(s *Settings) { s.Hostname = "My NAS" },
		func(s *Settings) { s.User = "root" },
		func(s *Settings) { s.User = "Chris" },
		func(s *Settings) { s.Password = "short" },
		func(s *Settings) { s.FullName = "a:b" },
		func(s *Settings) { s.Timezone = "../../etc/passwd" },
		func(s *Settings) { s.Disk = "sdb" },
	}
	for i, change := range bad {
		s := settings("/dev/sdb")
		change(&s)
		if s.Check() == nil {
			t.Errorf("case %d accepted: %+v", i, s)
		}
	}
	f, e, _ := setup(t, 10*layout.GiB)
	if err := e.Install(settings("/dev/sdb")); err == nil || len(find(f, "wipefs")) != 0 {
		t.Errorf("a 10 GB disk: %v", err)
	}
}
