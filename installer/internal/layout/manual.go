package layout

import (
	"fmt"
	"strings"
)

// Part is one partition of a plan the person laid out by hand. A size of zero
// means "the rest of the disk", which only the last partition may be.
type Part struct {
	Size   int64  `json:"size"`
	Format string `json:"format"` // vfat, ext4, btrfs, xfs, swap, or none
	Mount  string `json:"mount"`  // "/", "/boot/efi", "/home", "swap", or "" for no mount
}

// MinEFI is the smallest EFI system partition worth making: firmware writes
// kernels and firmware updates there
const MinEFI = 300 * MiB

// MinPart is the smallest partition that can hold a filesystem
const MinPart = 16 * MiB

// mounts are the things a hand-made plan may mount
var systemFormats = map[string]bool{"ext4": true, "btrfs": true, "xfs": true}

var formats = map[string]bool{"vfat": true, "ext4": true, "btrfs": true, "xfs": true, "swap": true, "none": true}

// CheckManual reports what is wrong with a hand-made plan, before anything on
// the disk is touched
func CheckManual(disk, installed int64, parts []Part) error {
	if len(parts) == 0 {
		return fmt.Errorf("the disk needs at least an EFI partition and a system partition")
	}
	var system, efi int
	mounts := map[string]bool{}
	for index, part := range parts {
		number := index + 1
		if !formats[part.Format] {
			return fmt.Errorf("partition %d: %q isn't a filesystem this installer can make", number, part.Format)
		}
		if part.Size < 0 {
			return fmt.Errorf("partition %d: the size can't be negative", number)
		}
		if part.Size == 0 {
			if index != len(parts)-1 {
				return fmt.Errorf("partition %d takes the rest of the disk, so it has to be the last one", number)
			}
		} else if part.Size < MinPart {
			return fmt.Errorf("partition %d: %s is too small for a filesystem", number, Size(part.Size))
		}
		switch part.Mount {
		case "":
			if part.Format == "swap" {
				return fmt.Errorf("partition %d: a swap partition has to be marked as swap", number)
			}
		case "swap":
			if part.Format != "swap" {
				return fmt.Errorf("partition %d: swap is formatted as swap, not %s", number, part.Format)
			}
		case "/":
			system++
			if !systemFormats[part.Format] {
				return fmt.Errorf("partition %d: the system partition has to be ext4, btrfs or xfs, not %s", number, part.Format)
			}
		case "/boot/efi":
			efi++
			if part.Format != "vfat" {
				return fmt.Errorf("partition %d: the EFI partition has to be fat, not %s", number, part.Format)
			}
			if part.Size != 0 && part.Size < MinEFI {
				return fmt.Errorf("partition %d: the EFI partition has to be at least %s", number, Size(MinEFI))
			}
		default:
			if !strings.HasPrefix(part.Mount, "/") || strings.Contains(part.Mount, "..") || strings.HasSuffix(part.Mount, "/") {
				return fmt.Errorf("partition %d: %q isn't a mount point", number, part.Mount)
			}
			if part.Format == "swap" || part.Format == "vfat" {
				return fmt.Errorf("partition %d: a %s partition can't be mounted at %s", number, part.Format, part.Mount)
			}
		}
		if part.Mount != "" {
			if mounts[part.Mount] {
				return fmt.Errorf("%s is mounted twice", part.Mount)
			}
			mounts[part.Mount] = true
		}
	}
	if system == 0 {
		return fmt.Errorf("one partition has to be the system, mounted at /")
	}
	if efi == 0 {
		return fmt.Errorf("one partition has to be the EFI system partition, mounted at /boot/efi")
	}
	used := int64(0)
	for _, part := range parts {
		used += part.Size
	}
	if used > disk {
		return fmt.Errorf("the partitions add up to %s, more than the %s the disk has", Size(used), Size(disk))
	}
	if err := systemSized(installed, parts); err != nil {
		return err
	}
	return nil
}

// systemSized refuses a system partition too small for the install, unless it
// is the one taking the rest of the disk
func systemSized(installed int64, parts []Part) error {
	for index, part := range parts {
		if part.Mount != "/" || part.Size == 0 {
			continue
		}
		if smallest := MinSystem(installed); part.Size < smallest {
			return fmt.Errorf("partition %d: the system needs at least %s", index+1, Size(smallest))
		}
	}
	return nil
}

// ResolveManual checks a plan and fills in the partition that takes the rest of
// the disk, so the result has a real size for every partition
func ResolveManual(disk, installed int64, parts []Part) ([]Part, error) {
	if err := CheckManual(disk, installed, parts); err != nil {
		return nil, err
	}
	used := int64(0)
	for _, part := range parts {
		used += part.Size
	}
	resolved := make([]Part, len(parts))
	copy(resolved, parts)
	for index, part := range resolved {
		if part.Size != 0 {
			continue
		}
		left := disk - used - slack
		if left < MinPart {
			return nil, fmt.Errorf("partition %d: there is only %s left on the disk", index+1, Size(left))
		}
		if part.Mount == "/" {
			if smallest := MinSystem(installed); left < smallest {
				return nil, fmt.Errorf("partition %d: the system needs at least %s", index+1, Size(smallest))
			}
		}
		resolved[index].Size = left
	}
	return resolved, nil
}
