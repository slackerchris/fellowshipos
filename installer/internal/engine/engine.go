// Package engine installs FellowshipOS onto one disk: it partitions only
// that disk, puts the system on LVM, copies the system from the live image
// the same way Debian's live installer does, and makes it bootable. Every
// command goes through a Runner, so tests can see exactly what would run.
package engine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"

	"fellowship-installer/internal/layout"
)

type Runner interface {
	// Run runs a command, feeding it input, and returns what it printed
	Run(input string, name string, args ...string) (string, error)
}

type Settings struct {
	Disk     string `json:"disk"`
	System   int64  `json:"system"`
	Hostname string `json:"hostname"`
	Timezone string `json:"timezone"`
	User     string `json:"user"`
	FullName string `json:"full_name"`
	Password string `json:"password"`
	Role     string `json:"role"`
}

var (
	hostName = regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)
	userName = regexp.MustCompile(`^[a-z_][a-z0-9_-]{0,31}$`)
	zoneName = regexp.MustCompile(`^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$`)
	reserved = map[string]bool{"root": true, "daemon": true, "bin": true, "sys": true, "nobody": true,
		"nas-share": true, "fnas-web": true}
)

// Check refuses settings that would install something broken
func (s Settings) Check() error {
	switch {
	case !hostName.MatchString(s.Hostname):
		return errors.New("the name may use lowercase letters, numbers and -, up to 63 characters")
	case !userName.MatchString(s.User) || reserved[s.User]:
		return errors.New("the user name may use lowercase letters, numbers, - and _, starting with a letter, and can't be a system account")
	case strings.ContainsAny(s.FullName, ":\n"):
		return errors.New("the full name can't contain : or a line break")
	case len(s.Password) < 8:
		return errors.New("the password needs at least 8 characters")
	case strings.ContainsAny(s.Password, "\n"):
		return errors.New("the password can't contain a line break")
	case !zoneName.MatchString(s.Timezone) || strings.Contains(s.Timezone, ".."):
		return errors.New("unknown time zone")
	case !strings.HasPrefix(s.Disk, "/dev/") || strings.ContainsFunc(s.Disk, unicode.IsSpace):
		return errors.New("choose a disk")
	}
	return nil
}

type Engine struct {
	Run Runner
	// Where the new system is assembled
	Target string
	// The live system's image, copied onto the disk
	Squashfs string
	// Projected install size and memory, for the layout
	Installed, Memory int64
	// UEFI firmware; legacy BIOS machines need the regular installer for now
	EFI bool
	// The wired network ports, each set up for DHCP as Debian's installer does
	Network []string
	// Reports each step's title as it starts, and lines worth showing
	Step func(title string)
	Log  func(line string)
}

// Steps names what Install does, in order, for the progress list
var Steps = []string{
	"Clearing the chosen disk",
	"Creating partitions",
	"Creating the system volume",
	"Copying FellowshipOS",
	"Setting up the system",
	"Creating your account",
	"Making the disk bootable",
	"Finishing",
}

// LivePackages leave the installed system: the live boot scripts and this installer
var LivePackages = []string{"live-boot", "live-boot-initramfs-tools", "live-config", "live-config-systemd",
	"user-setup", "fellowship-installer", "cage", "cog"}

// Keep these when the live packages and their dependencies go
var bootPackages = []string{"lvm2", "grub-efi-amd64", "grub-efi-amd64-signed", "shim-signed", "efibootmgr",
	"dosfstools", "gdisk"}

// Partition gives the device name of a disk's nth partition
func Partition(disk string, n int) string {
	if last := disk[len(disk)-1]; last >= '0' && last <= '9' {
		return fmt.Sprintf("%sp%d", disk, n)
	}
	return fmt.Sprintf("%s%d", disk, n)
}

func (e *Engine) run(name string, args ...string) (string, error) {
	output, err := e.Run.Run("", name, args...)
	if err != nil {
		return output, fmt.Errorf("%s %s: %w", name, strings.Join(args, " "), err)
	}
	return output, nil
}

func (e *Engine) chroot(input string, args ...string) error {
	command := append([]string{"env", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C"}, args...)
	if _, err := e.Run.Run(input, "chroot", append([]string{e.Target}, command...)...); err != nil {
		return fmt.Errorf("in the new system, %s: %w", strings.Join(args, " "), err)
	}
	return nil
}

func (e *Engine) write(path, content string, mode os.FileMode) error {
	full := filepath.Join(e.Target, path)
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		return err
	}
	return os.WriteFile(full, []byte(content), mode)
}

// volumeGroup picks a volume group name that no other disk already uses
func (e *Engine) volumeGroup() string {
	output, _ := e.run("vgs", "--noheadings", "-o", "vg_name")
	taken := map[string]bool{}
	for _, name := range strings.Fields(output) {
		taken[name] = true
	}
	name := "fellowship"
	for i := 2; taken[name]; i++ {
		name = fmt.Sprintf("fellowship%d", i)
	}
	return name
}

// Install runs every step. It stops at the first failure, leaving other
// disks untouched.
func (e *Engine) Install(s Settings) error {
	if err := s.Check(); err != nil {
		return err
	}
	if !e.EFI {
		return errors.New("this machine started in legacy BIOS mode; the NAS installer needs UEFI. Switch the firmware to UEFI, or use the regular installer")
	}
	size, err := e.diskSize(s.Disk)
	if err != nil {
		return err
	}
	plan, err := layout.WithSystem(size, e.Installed, e.Memory, s.System)
	if err != nil {
		return err
	}
	var group string
	steps := []func() error{
		func() error { return e.clear(s.Disk) },
		func() error { return e.partition(s.Disk, plan) },
		func() error {
			group = e.volumeGroup()
			return e.volumes(s.Disk, group, plan)
		},
		func() error {
			if err := e.mount(s.Disk, group); err != nil {
				return err
			}
			return e.copy()
		},
		func() error { return e.configure(s, group) },
		func() error { return e.account(s) },
		func() error { return e.boot() },
		func() error { return e.finish() },
	}
	for i, step := range steps {
		e.Step(Steps[i])
		if err := step(); err != nil {
			// Nothing may still be mounted; that error doesn't matter
			e.unmount()
			return err
		}
	}
	return nil
}

func (e *Engine) diskSize(disk string) (int64, error) {
	output, err := e.run("blockdev", "--getsize64", disk)
	if err != nil {
		return 0, err
	}
	var size int64
	if _, err := fmt.Sscan(output, &size); err != nil {
		return 0, fmt.Errorf("reading the size of %s", disk)
	}
	return size, nil
}

// clear removes what was on the disk, so stale signatures (an old ZFS label,
// an old LVM volume) can't reappear inside the new partitions
func (e *Engine) clear(disk string) error {
	output, err := e.run("lsblk", "-ln", "-o", "PATH,TYPE", disk)
	if err != nil {
		return err
	}
	var parts []string
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && fields[1] == "part" {
			parts = append(parts, fields[0])
		}
	}
	// Old LVM volumes on this disk must let go of it first
	pvs, _ := e.run("pvs", "--noheadings", "-o", "pv_name,vg_name")
	for _, line := range strings.Split(pvs, "\n") {
		fields := strings.Fields(line)
		if len(fields) == 2 && strings.HasPrefix(fields[0], disk) {
			e.Log("Closing old LVM volume group " + fields[1])
			if _, err := e.run("vgchange", "-an", fields[1]); err != nil {
				return err
			}
		}
	}
	for _, part := range parts {
		if _, err := e.run("wipefs", "-a", part); err != nil {
			return err
		}
	}
	if _, err := e.run("sgdisk", "--zap-all", disk); err != nil {
		return err
	}
	_, err = e.run("wipefs", "-a", disk)
	return err
}

func (e *Engine) partition(disk string, plan layout.Layout) error {
	args := []string{
		"-n", fmt.Sprintf("1:0:+%dK", plan.EFI/1024), "-t", "1:EF00", "-c", "1:EFI",
		"-n", fmt.Sprintf("2:0:+%dK", plan.LVM()/1024), "-t", "2:8E00", "-c", "2:fellowship-system",
	}
	if plan.Data > 0 {
		args = append(args, "-n", "3:0:0", "-t", "3:8300", "-c", "3:fellowship-data")
		e.Log(fmt.Sprintf("Leaving %s for data, to be set up later from the desktop", layout.Size(plan.Data)))
	}
	if _, err := e.run("sgdisk", append(args, disk)...); err != nil {
		return err
	}
	if _, err := e.run("partprobe", disk); err != nil {
		return err
	}
	_, err := e.run("udevadm", "settle")
	return err
}

func (e *Engine) volumes(disk, group string, plan layout.Layout) error {
	commands := [][]string{
		{"mkfs.vfat", "-F", "32", "-n", "EFI", Partition(disk, 1)},
		{"pvcreate", "-ff", "-y", Partition(disk, 2)},
		{"vgcreate", group, Partition(disk, 2)},
		{"lvcreate", "-y", "-L", fmt.Sprintf("%db", plan.System), "-n", "root", group},
		{"lvcreate", "-y", "-L", fmt.Sprintf("%db", plan.Swap), "-n", "swap", group},
		{"mkfs.ext4", "-F", "-L", "fellowship-root", "/dev/" + group + "/root"},
		{"mkswap", "-L", "fellowship-swap", "/dev/" + group + "/swap"},
	}
	if plan.Data > 0 {
		// Left blank for a pool, without anything an old disk left behind
		commands = append(commands, []string{"wipefs", "-a", Partition(disk, 3)})
	}
	for _, command := range commands {
		if _, err := e.run(command[0], command[1:]...); err != nil {
			return err
		}
	}
	e.Log(fmt.Sprintf("System %s, room to grow %s, swap %s", layout.Size(plan.System), layout.Size(plan.Reserve), layout.Size(plan.Swap)))
	return nil
}

func (e *Engine) mount(disk, group string) error {
	if err := os.MkdirAll(e.Target, 0o755); err != nil {
		return err
	}
	if _, err := e.run("mount", "/dev/"+group+"/root", e.Target); err != nil {
		return err
	}
	efi := filepath.Join(e.Target, "boot/efi")
	if err := os.MkdirAll(efi, 0o755); err != nil {
		return err
	}
	_, err := e.run("mount", Partition(disk, 1), efi)
	return err
}

func (e *Engine) copy() error {
	_, err := e.run("unsquashfs", "-f", "-q", "-n", "-d", e.Target, e.Squashfs)
	return err
}

func (e *Engine) uuid(device string) (string, error) {
	output, err := e.run("blkid", "-s", "UUID", "-o", "value", device)
	return strings.TrimSpace(output), err
}

// binds are the live system's mounts the new system needs while it is set up
var binds = []string{"dev", "dev/pts", "proc", "sys", "run"}

func (e *Engine) configure(s Settings, group string) error {
	root, err := e.uuid("/dev/" + group + "/root")
	if err != nil {
		return err
	}
	efi, err := e.uuid(Partition(s.Disk, 1))
	if err != nil {
		return err
	}
	swap, err := e.uuid("/dev/" + group + "/swap")
	if err != nil {
		return err
	}
	fstab := fmt.Sprintf("# Written by the FellowshipOS installer\n"+
		"UUID=%s\t/\text4\terrors=remount-ro\t0\t1\n"+
		"UUID=%s\t/boot/efi\tvfat\tumask=0077\t0\t1\n"+
		"UUID=%s\tnone\tswap\tsw\t0\t0\n", root, efi, swap)
	if err := e.write("etc/fstab", fstab, 0o644); err != nil {
		return err
	}
	for _, bind := range binds {
		if _, err := e.run("mount", "--bind", "/"+bind, filepath.Join(e.Target, bind)); err != nil {
			return err
		}
	}
	if _, err := e.run("mount", "-t", "efivarfs", "efivarfs", filepath.Join(e.Target, "sys/firmware/efi/efivars")); err != nil {
		e.Log("EFI variables weren't writable; the disk will still boot through the fallback loader")
	}

	// Keep the boot tools, then drop the live system and this installer
	for _, name := range bootPackages {
		e.chroot("", "apt-mark", "manual", name)
	}
	if err := e.chroot("", append([]string{"apt-get", "purge", "-y"}, e.installedOf(LivePackages)...)...); err != nil {
		return err
	}
	if err := e.chroot("", "apt-get", "autoremove", "--purge", "-y"); err != nil {
		return err
	}

	// The live system set its network up itself; the installed one needs settings
	for _, port := range e.Network {
		config := fmt.Sprintf("# Written by the FellowshipOS installer\nallow-hotplug %[1]s\niface %[1]s inet dhcp\niface %[1]s inet6 auto\n", port)
		if err := e.write("etc/network/interfaces.d/"+port, config, 0o644); err != nil {
			return err
		}
	}
	if len(e.Network) == 0 {
		e.Log("No wired network port found; set up the network after the first start")
	}

	files := map[string]string{
		"etc/hostname":                  s.Hostname + "\n",
		"etc/hosts":                     fmt.Sprintf("127.0.0.1\tlocalhost\n127.0.1.1\t%s\n\n::1\tlocalhost ip6-localhost ip6-loopback\nff02::1\tip6-allnodes\nff02::2\tip6-allrouters\n", s.Hostname),
		"etc/timezone":                  s.Timezone + "\n",
		"etc/fellowshipos/install-role": s.Role + "\n",
		"etc/machine-id":                "",
	}
	for path, content := range files {
		if err := e.write(path, content, 0o644); err != nil {
			return err
		}
	}
	os.Remove(filepath.Join(e.Target, "var/lib/dbus/machine-id"))
	if err := e.chroot("", "ln", "-sf", "/usr/share/zoneinfo/"+s.Timezone, "/etc/localtime"); err != nil {
		return err
	}
	// The live image only lists itself as a package source
	if err := e.chroot("", "cp", "/usr/share/fellowshipos/sources.list", "/etc/apt/sources.list"); err != nil {
		return err
	}
	keys, _ := filepath.Glob(filepath.Join(e.Target, "etc/ssh/ssh_host_*"))
	for _, key := range keys {
		os.Remove(key)
	}
	return e.chroot("", "ssh-keygen", "-A")
}

// installedOf keeps the packages actually present, since apt-get purge
// refuses unknown names
func (e *Engine) installedOf(names []string) []string {
	var present []string
	for _, name := range names {
		if _, err := e.Run.Run("", "chroot", e.Target, "dpkg-query", "-W", "-f=${Status}", name); err == nil {
			present = append(present, name)
		}
	}
	return present
}

// The groups Debian's installer gives its first user, where they exist
var userGroups = []string{"sudo", "adm", "cdrom", "dip", "plugdev", "users", "audio", "video", "netdev"}

func (e *Engine) account(s Settings) error {
	var groups []string
	content, _ := os.ReadFile(filepath.Join(e.Target, "etc/group"))
	for _, line := range strings.Split(string(content), "\n") {
		name, _, _ := strings.Cut(line, ":")
		for _, wanted := range userGroups {
			if name == wanted {
				groups = append(groups, name)
			}
		}
	}
	if err := e.chroot("", "useradd", "-m", "-s", "/bin/bash", "-c", s.FullName, "-G", strings.Join(groups, ","), s.User); err != nil {
		return err
	}
	return e.chroot(s.User+":"+s.Password+"\n", "chpasswd")
}

func (e *Engine) boot() error {
	if err := e.chroot("", "update-initramfs", "-u", "-k", "all"); err != nil {
		return err
	}
	install := []string{"grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=debian", "--recheck"}
	if err := e.chroot("", install...); err != nil {
		return err
	}
	// A copy at the fallback path boots even when the firmware forgets its boot entries
	if err := e.chroot("", append(install, "--removable")...); err != nil {
		return err
	}
	return e.chroot("", "update-grub")
}

func (e *Engine) finish() error {
	if err := e.unmount(); err != nil {
		return err
	}
	_, err := e.run("sync")
	return err
}

// unmount undoes every mount under the target, deepest first
func (e *Engine) unmount() error {
	_, err := e.run("umount", "-R", e.Target)
	return err
}
