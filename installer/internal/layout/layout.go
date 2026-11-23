// Package layout lays out the system disk: an EFI partition, then LVM holding
// swap and the system's root volume, which takes the rest of the disk.
package layout

import "fmt"

const (
	MiB = int64(1) << 20
	GiB = int64(1) << 30

	EFI = 512 * MiB
	// Partition tables and alignment at both ends of the disk
	slack = 4 * MiB
	// LVM's own metadata and alignment inside its partition
	lvmOverhead = 16 * MiB
)

type Layout struct {
	Disk int64 `json:"disk"`
	EFI  int64 `json:"efi"`
	// The system's root volume, inside LVM
	System int64 `json:"system"`
	Swap   int64 `json:"swap"`
}

// LVM is the size of the LVM partition: system, swap and LVM's own needs
func (l Layout) LVM() int64 {
	return l.System + l.Swap + lvmOverhead
}

func roundDown(size, unit int64) int64 {
	return size / unit * unit
}

// MinSystem is the smallest system volume worth installing: twice the
// projected install, for updates and old kernels, plus room for logs
func MinSystem(installed int64) int64 {
	return max(12*GiB, roundUp(installed*2+4*GiB, GiB))
}

func roundUp(size, unit int64) int64 {
	return (size + unit - 1) / unit * unit
}

func swapFor(memory int64) int64 {
	return min(4*GiB, max(1*GiB, roundUp(memory, GiB)))
}

// WholeDisk lays out a disk: EFI, swap, and the rest as the system's root
// volume.
func WholeDisk(disk, installed, memory int64) (Layout, error) {
	usable := disk - EFI - slack - lvmOverhead
	minimum := MinSystem(installed)
	swap := swapFor(memory)
	if usable < minimum+swap {
		return Layout{}, fmt.Errorf("the disk is too small: FellowshipOS needs at least %s", Size(minimum+swap+EFI+slack))
	}
	return Layout{Disk: disk, EFI: EFI, Swap: swap, System: roundDown(usable-swap, GiB)}, nil
}

// Size writes a byte count the way disks are sold, in decimal units
func Size(bytes int64) string {
	units := []string{"B", "KB", "MB", "GB", "TB", "PB"}
	value := float64(bytes)
	unit := 0
	for value >= 1000 && unit < len(units)-1 {
		value /= 1000
		unit++
	}
	if value < 10 && unit > 0 {
		return fmt.Sprintf("%.1f %s", value, units[unit])
	}
	return fmt.Sprintf("%.0f %s", value, units[unit])
}
