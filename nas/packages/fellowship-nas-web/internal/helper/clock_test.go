package helper

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func pointClockAt(t *testing.T, link, file string, kernel func(*syscall.Timex) (int, error)) {
	oldLocal, oldZone, oldAdj := localtime, timezone, adjtimex
	t.Cleanup(func() { localtime, timezone, adjtimex = oldLocal, oldZone, oldAdj })
	dir := t.TempDir()
	localtime = filepath.Join(dir, "localtime")
	timezone = filepath.Join(dir, "timezone")
	if link != "" {
		os.Symlink(link, localtime)
	}
	if file != "" {
		os.WriteFile(timezone, []byte(file+"\n"), 0o644)
	}
	adjtimex = kernel
}

func synced(state *syscall.Timex) (int, error) { return 0, nil }

func TestClockNamesTheZoneTheLinkPointsAt(t *testing.T) {
	pointClockAt(t, "/usr/share/zoneinfo/America/New_York", "Europe/London", synced)
	clock := ReadClock()
	if clock.Zone != "America/New_York" || !clock.Synced || !clock.Known || clock.Now == 0 {
		t.Fatalf("clock: %+v", clock)
	}
}

func TestClockFallsBackToTheZoneFileThenUTC(t *testing.T) {
	pointClockAt(t, "", "Europe/London", synced)
	if zone := ReadClock().Zone; zone != "Europe/London" {
		t.Fatalf("zone: %s", zone)
	}
	pointClockAt(t, "", "", synced)
	if zone := ReadClock().Zone; zone != "UTC" {
		t.Fatalf("zone: %s", zone)
	}
}

func TestAnUnsetClockIsNotSynced(t *testing.T) {
	pointClockAt(t, "", "", func(state *syscall.Timex) (int, error) { return timeError, nil })
	if clock := ReadClock(); clock.Synced || !clock.Known {
		t.Fatalf("time error read as synced: %+v", clock)
	}
	pointClockAt(t, "", "", func(state *syscall.Timex) (int, error) { state.Status = staUnsync; return 0, nil })
	if ReadClock().Synced {
		t.Fatal("unsync flag read as synced")
	}
	pointClockAt(t, "", "", func(state *syscall.Timex) (int, error) { return 0, errors.New("denied") })
	if clock := ReadClock(); clock.Known || clock.Synced {
		t.Fatalf("a refused question claimed an answer: %+v", clock)
	}
}
