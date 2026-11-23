package layout

import "testing"

const (
	installed = 3 * GiB
	memory    = 8 * GiB
)

func TestWholeDiskGoesToTheSystem(t *testing.T) {
	for _, size := range []int64{20 * GiB, 32 * GiB, 64 * GiB, 500 * GiB, 4096 * GiB} {
		l, err := WholeDisk(size, installed, memory)
		if err != nil {
			t.Fatalf("%s: %v", Size(size), err)
		}
		if l.Swap != 4*GiB {
			t.Errorf("%s: %+v", Size(size), l)
		}
		if l.System < MinSystem(installed) {
			t.Errorf("%s: a system of %s is too small", Size(size), Size(l.System))
		}
		if total := l.EFI + l.LVM(); total > size-slack {
			t.Errorf("%s: a layout of %d bytes doesn't fit", Size(size), total)
		}
	}
}

func TestTooSmall(t *testing.T) {
	if _, err := WholeDisk(10*GiB, installed, memory); err == nil {
		t.Fatal("a 10 GB disk was accepted")
	}
}

func TestMinSystemFollowsTheInstall(t *testing.T) {
	if MinSystem(1*GiB) != 12*GiB || MinSystem(6*GiB) != 16*GiB {
		t.Fatal(MinSystem(1*GiB), MinSystem(6*GiB))
	}
}

func TestSize(t *testing.T) {
	if Size(500*1000*1000*1000) != "500 GB" || Size(4*1000*1000*1000*1000) != "4.0 TB" {
		t.Fatal(Size(500e9), Size(4e12))
	}
}

// LVM uses 4 MiB extents after about 1 MiB of metadata; every volume must fit
func TestVolumesFitInTheVolumeGroup(t *testing.T) {
	extent := 4 * MiB
	for _, disk := range []int64{20 * GiB, 32 * GiB, 63 * GiB, 64 * GiB, 100 * GiB, 500 * GiB, 4096 * GiB} {
		l, err := WholeDisk(disk, installed, memory)
		if err != nil {
			t.Fatalf("%s: %v", Size(disk), err)
		}
		extents := (l.LVM() - 1*MiB) / extent
		needed := (l.System+extent-1)/extent + (l.Swap+extent-1)/extent
		if needed > extents {
			t.Errorf("%s: volumes need %d extents, the group has %d", Size(disk), needed, extents)
		}
	}
}
