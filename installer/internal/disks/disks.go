// Package disks finds the disks FellowshipOS could be installed on, and
// says plainly what is on each one now.
package disks

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

type device struct {
	Name        string    `json:"name"`
	Path        string    `json:"path"`
	Size        int64     `json:"size"`
	Model       *string   `json:"model"`
	Serial      *string   `json:"serial"`
	Type        string    `json:"type"`
	Tran        *string   `json:"tran"`
	ReadOnly    bool      `json:"ro"`
	FSType      *string   `json:"fstype"`
	Label       *string   `json:"label"`
	Mountpoints []*string `json:"mountpoints"`
	Children    []device  `json:"children"`
}

type Disk struct {
	Path   string `json:"path"`
	Size   int64  `json:"size"`
	Model  string `json:"model"`
	Serial string `json:"serial"`
	// sata, nvme, usb, virtio...
	Bus string `json:"bus"`
	// What is on it now, in plain words; empty for a blank disk
	Contents []string `json:"contents"`
	// Why it can't be chosen; empty when it can
	Refused string `json:"refused,omitempty"`
}

func text(value *string) string {
	if value == nil {
		return ""
	}
	return strings.TrimSpace(*value)
}

// describe lists what a disk holds, from its partitions' file systems
func describe(d device) []string {
	var found []string
	var walk func(device)
	walk = func(node device) {
		if fs := text(node.FSType); fs != "" {
			what := fs
			switch fs {
			case "zfs_member":
				what = "part of a ZFS pool"
				if label := text(node.Label); label != "" {
					what = fmt.Sprintf("part of ZFS pool %q", label)
				}
			case "LVM2_member":
				what = "LVM volumes"
			case "linux_raid_member":
				what = "part of a Linux RAID"
			case "swap":
				what = "swap space"
			case "vfat":
				what = "a FAT file system"
			case "ntfs":
				what = "a Windows (NTFS) file system"
			default:
				what = "a " + fs + " file system"
			}
			if label := text(node.Label); label != "" && fs != "zfs_member" {
				what += fmt.Sprintf(" labelled %q", label)
			}
			found = append(found, what)
		}
		for _, child := range node.Children {
			walk(child)
		}
	}
	walk(d)
	if len(found) == 0 && len(d.Children) > 0 {
		found = append(found, fmt.Sprintf("%d partition(s) with nothing recognisable", len(d.Children)))
	}
	return found
}

func mounted(d device) bool {
	for _, point := range d.Mountpoints {
		if point != nil && *point != "" {
			return true
		}
	}
	for _, child := range d.Children {
		if mounted(child) {
			return true
		}
	}
	return false
}

// Parse reads lsblk's JSON. The disk holding the running live system is
// refused, as are read-only and mounted disks.
func Parse(output []byte, liveDisk string, minimum int64) ([]Disk, error) {
	var listing struct {
		Devices []device `json:"blockdevices"`
	}
	if err := json.Unmarshal(output, &listing); err != nil {
		return nil, fmt.Errorf("reading lsblk: %w", err)
	}
	var found []Disk
	for _, d := range listing.Devices {
		// Floppy drives and compressed swap in memory aren't disks to install on
		if d.Type != "disk" || strings.HasPrefix(d.Name, "zram") || strings.HasPrefix(d.Name, "fd") {
			continue
		}
		disk := Disk{Path: d.Path, Size: d.Size, Model: text(d.Model), Serial: text(d.Serial),
			Bus: text(d.Tran), Contents: describe(d)}
		switch {
		case d.Path == liveDisk:
			disk.Refused = "This is the USB stick or disc FellowshipOS is running from."
		case d.ReadOnly:
			disk.Refused = "The disk is read-only."
		case mounted(d):
			disk.Refused = "Something on this disk is in use right now."
		case d.Size < minimum:
			disk.Refused = "Too small for FellowshipOS."
		}
		found = append(found, disk)
	}
	return found, nil
}

// List asks lsblk for every disk
func List(liveDisk string, minimum int64) ([]Disk, error) {
	output, err := exec.Command("lsblk", "--json", "--bytes", "--tree",
		"-o", "NAME,PATH,SIZE,MODEL,SERIAL,TYPE,TRAN,RO,FSTYPE,LABEL,MOUNTPOINTS").Output()
	if err != nil {
		return nil, fmt.Errorf("lsblk: %w", err)
	}
	return Parse(output, liveDisk, minimum)
}

// LiveDisk finds the whole disk the live system booted from, so it is
// never offered
func LiveDisk() string {
	source, err := exec.Command("findmnt", "-n", "-o", "SOURCE", "/run/live/medium").Output()
	if err != nil {
		return ""
	}
	parent, err := exec.Command("lsblk", "-n", "-d", "-o", "PKNAME", strings.TrimSpace(string(source))).Output()
	if err != nil || strings.TrimSpace(string(parent)) == "" {
		// A CD drive has no parent: the source is the disk itself
		return strings.TrimSpace(string(source))
	}
	return "/dev/" + strings.TrimSpace(string(parent))
}
