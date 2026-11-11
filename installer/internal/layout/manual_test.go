package layout

import (
	"strings"
	"testing"
)

// A plan that would install: ESP, system, and the rest for data
func good() []Part {
	return []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 0, Format: "none"},
	}
}

const size = 500 * GiB

func requireError(t *testing.T, err error, want string) {
	t.Helper()
	if err == nil {
		t.Fatalf("want an error mentioning %q, got none", want)
	}
	if !strings.Contains(err.Error(), want) {
		t.Fatalf("want an error mentioning %q, got %q", want, err)
	}
}

func TestAGoodPlanIsAccepted(t *testing.T) {
	if err := CheckManual(size, 3*GiB, good()); err != nil {
		t.Fatalf("a plan with an ESP, a system and the rest: %v", err)
	}
}

func TestTheRestOfTheDiskIsFilledIn(t *testing.T) {
	resolved, err := ResolveManual(size, 3*GiB, good())
	if err != nil {
		t.Fatal(err)
	}
	left := size - 512*MiB - 32*GiB - slack
	if resolved[2].Size != left {
		t.Fatalf("the last partition took %s, want %s", Size(resolved[2].Size), Size(left))
	}
}

func TestTheRestMustComeLast(t *testing.T) {
	parts := []Part{
		{Size: 0, Format: "ext4", Mount: "/"},
		{Size: 1 * GiB, Format: "none"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "has to be the last one")
}

func TestOnlyOneRest(t *testing.T) {
	// A partition taking the rest that isn't last is the only way to get here
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 0, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "none"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "has to be the last one")
}

func TestThereMustBeASystem(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/home"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "one partition has to be the system")
}

func TestOnlyOneSystem(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 32 * GiB, Format: "xfs", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "/ is mounted twice")
}

func TestTheSystemNeedsARealFilesystem(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "vfat", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "has to be ext4, btrfs or xfs")
}

func TestTheSystemMustBeBigEnough(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 4 * GiB, Format: "ext4", Mount: "/"},
	}
	smallest := Size(MinSystem(3 * GiB))
	requireError(t, CheckManual(size, 3*GiB, parts), smallest)
}

func TestTheEFIPartitionIsRequiredAndIsFat(t *testing.T) {
	without := []Part{{Size: 0, Format: "ext4", Mount: "/"}}
	requireError(t, CheckManual(size, 3*GiB, without), "EFI system partition")

	notFat := []Part{
		{Size: 512 * MiB, Format: "ext4", Mount: "/boot/efi"},
		{Size: 0, Format: "ext4", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, notFat), "EFI partition has to be fat")

	tiny := []Part{
		{Size: 64 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 0, Format: "ext4", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, tiny), "at least")
}

func TestSwapMatchesItsFormat(t *testing.T) {
	marked := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "ext4", Mount: "swap"},
	}
	requireError(t, CheckManual(size, 3*GiB, marked), "formatted as swap")

	unmarked := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "swap"},
	}
	requireError(t, CheckManual(size, 3*GiB, unmarked), "marked as swap")

	twice := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "swap", Mount: "swap"},
		{Size: 8 * GiB, Format: "swap", Mount: "swap"},
	}
	requireError(t, CheckManual(size, 3*GiB, twice), "swap is mounted twice")
}

func TestAPlanCannotBeBiggerThanTheDisk(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 600 * GiB, Format: "ext4", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "more than the")
}

func TestMountPointsHaveToBePaths(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "xfs", Mount: "home"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "isn't a mount point")

	nested := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 32 * GiB, Format: "ext4", Mount: "/"},
		{Size: 8 * GiB, Format: "xfs", Mount: "/var/../etc"},
	}
	requireError(t, CheckManual(size, 3*GiB, nested), "isn't a mount point")
}

func TestUnknownFormatsAreRefused(t *testing.T) {
	parts := []Part{
		{Size: 512 * MiB, Format: "vfat", Mount: "/boot/efi"},
		{Size: 0, Format: "zfs", Mount: "/"},
	}
	requireError(t, CheckManual(size, 3*GiB, parts), "isn't a filesystem this installer can make")
}

func TestAnEmptyPlanSaysWhatIsMissing(t *testing.T) {
	requireError(t, CheckManual(size, 3*GiB, nil), "at least an EFI partition and a system partition")
}
