// Package layout suggests how to split the system disk for a NAS: a system
// volume sized from the disk and the projected install, room for it to grow,
// swap, and the rest as a data partition for a pool set up later.
package layout

import "fmt"

const (
	MiB = int64(1) << 20
	GiB = int64(1) << 30

	EFI = 512 * MiB
	// Below this a split makes no sense: the system gets the whole disk
	SplitFrom = 64 * GiB
	// A data partition smaller than this isn't worth a pool
	MinData = 8 * GiB
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
	// Free space left in LVM so the system can grow later
	Reserve int64 `json:"reserve"`
	Swap    int64 `json:"swap"`
	// The plain partition for a data pool; zero for none
	Data int64 `json:"data"`
}

// LVM is the size of the LVM partition: system, swap, room to grow and LVM's own needs
func (l Layout) LVM() int64 {
	return l.System + l.Swap + l.Reserve + lvmOverhead
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

// suggestedSystem scales with the disk: small disks keep more for data,
// large disks can afford headroom
func suggestedSystem(disk int64) int64 {
	switch {
	case disk < 128*GiB:
		return 16 * GiB
	case disk < 256*GiB:
		return 24 * GiB
	case disk < 1024*GiB:
		return 32 * GiB
	case disk < 2048*GiB:
		return 40 * GiB
	default:
		return 48 * GiB
	}
}

func swapFor(memory int64) int64 {
	return min(4*GiB, max(1*GiB, roundUp(memory, GiB)))
}

func reserveFor(system int64) int64 {
	return min(24*GiB, roundDown(system/2, GiB))
}

// Suggest picks a layout for a disk, the projected install size and the
// machine's memory
func Suggest(disk, installed, memory int64) (Layout, error) {
	return WithSystem(disk, installed, memory, max(suggestedSystem(disk), MinSystem(installed)))
}

// WithSystem lays the disk out around a chosen system size, as when the
// person moves the slider. Everything left becomes data.
func WithSystem(disk, installed, memory, system int64) (Layout, error) {
	usable := disk - EFI - slack - lvmOverhead
	minimum := MinSystem(installed)
	swap := swapFor(memory)
	if usable < minimum+swap {
		return Layout{}, fmt.Errorf("the disk is too small: FellowshipOS needs at least %s", Size(minimum+swap+EFI+slack))
	}
	l := Layout{Disk: disk, EFI: EFI, Swap: swap}
	if disk < SplitFrom {
		// Too small to split: the system takes everything
		l.System = roundDown(usable-swap, GiB)
		return l, nil
	}
	// Keep the chosen size where it fits; the room to grow gives way first
	system = roundDown(max(min(system, usable-swap), minimum), GiB)
	reserve := max(0, min(reserveFor(system), roundDown(usable-system-swap, GiB)))
	l.System, l.Reserve = system, reserve
	if data := usable - (l.LVM() - lvmOverhead); data >= MinData {
		l.Data = roundDown(data, MiB)
	} else {
		// Too little left for a pool: give it to the system's room to grow
		l.Reserve = roundDown(usable-system-swap, GiB)
	}
	return l, nil
}

// WholeDisk lays out a disk for a machine that keeps nothing back for a pool:
// EFI, swap, and the rest as the system's root volume, with its room to grow
// inside the same volume group. The multi-purpose image uses this, and it is
// what a NAS already gets on a disk too small to split.
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
