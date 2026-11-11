package layout

import "testing"

const (
	installed = 3 * GiB
	memory    = 8 * GiB
)

func TestSuggestScalesWithTheDisk(t *testing.T) {
	cases := []struct {
		disk   int64
		system int64
		data   bool
	}{
		{32 * GiB, 27 * GiB, false},
		{60 * GiB, 55 * GiB, false},
		{64 * GiB, 16 * GiB, true},
		{500 * GiB, 32 * GiB, true},
		{4096 * GiB, 48 * GiB, true},
	}
	for _, c := range cases {
		l, err := Suggest(c.disk, installed, memory)
		if err != nil {
			t.Fatalf("%s: %v", Size(c.disk), err)
		}
		if l.System != c.system || (l.Data > 0) != c.data {
			t.Errorf("%s: system %s data %s", Size(c.disk), Size(l.System), Size(l.Data))
		}
		if total := l.EFI + l.LVM() + l.Data; total > c.disk {
			t.Errorf("%s: layout of %d bytes doesn't fit", Size(c.disk), total)
		}
	}
}

func TestSmallDiskGivesEverythingToTheSystem(t *testing.T) {
	l, _ := Suggest(32*GiB, installed, memory)
	if l.Data != 0 || l.Reserve != 0 || l.Swap != 4*GiB {
		t.Fatalf("%+v", l)
	}
}

func TestWholeDiskNeverLeavesADataPartition(t *testing.T) {
	// The multi-purpose image has no pool to prepare for, so even a large disk
	// goes wholly to the system, with no data part and nothing reserved beside it
	for _, size := range []int64{64 * GiB, 500 * GiB, 4096 * GiB} {
		l, err := WholeDisk(size, installed, memory)
		if err != nil {
			t.Fatalf("%s: %v", Size(size), err)
		}
		if l.Data != 0 || l.Reserve != 0 || l.Swap != 4*GiB {
			t.Errorf("%s: %+v", Size(size), l)
		}
		if l.System < MinSystem(installed) {
			t.Errorf("%s: a system of %s is too small", Size(size), Size(l.System))
		}
		if total := l.EFI + l.LVM(); total > size {
			t.Errorf("%s: a layout of %d bytes doesn't fit", Size(size), total)
		}
	}
	if _, err := WholeDisk(10*GiB, installed, memory); err == nil {
		t.Fatal("a 10 GB disk was accepted")
	}
}

func TestTooSmall(t *testing.T) {
	if _, err := Suggest(10*GiB, installed, memory); err == nil {
		t.Fatal("a 10 GB disk was accepted")
	}
}

func TestSliderKeepsTheMinimumAndGivesTheRestToData(t *testing.T) {
	l, err := WithSystem(500*GiB, installed, memory, 1*GiB)
	if err != nil {
		t.Fatal(err)
	}
	if l.System != MinSystem(installed) {
		t.Fatalf("system %s, want the minimum %s", Size(l.System), Size(MinSystem(installed)))
	}
	big, _ := WithSystem(500*GiB, installed, memory, 200*GiB)
	if big.System != 200*GiB || big.Reserve != 24*GiB || big.Data >= l.Data {
		t.Fatalf("%+v", big)
	}
}

func TestLeftoverTooSmallForAPoolGoesToReserve(t *testing.T) {
	disk := int64(100 * GiB)
	l, _ := WithSystem(disk, installed, memory, 80*GiB)
	if l.Data != 0 || l.Reserve == 0 {
		t.Fatalf("%+v", l)
	}
	if total := l.EFI + l.LVM(); total > disk {
		t.Fatalf("doesn't fit: %d", total)
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
		for _, system := range []int64{0, 30 * GiB, 80 * GiB, 1000 * GiB} {
			l, err := WithSystem(disk, installed, memory, system)
			if err != nil {
				continue
			}
			extents := (l.LVM() - 1*MiB) / extent
			needed := (l.System+extent-1)/extent + (l.Swap+extent-1)/extent
			if needed > extents {
				t.Errorf("%s with system %s: volumes need %d extents, the group has %d", Size(disk), Size(system), needed, extents)
			}
			if total := l.EFI + l.LVM() + l.Data; total > disk-slack {
				t.Errorf("%s with system %s: layout doesn't fit", Size(disk), Size(system))
			}
		}
	}
}
