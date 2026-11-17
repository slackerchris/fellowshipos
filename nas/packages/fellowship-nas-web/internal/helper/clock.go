package helper

import (
	"os"
	"strings"
	"syscall"
	"time"

	"fellowship-nas-web/internal/protocol"
)

// Where the time zone and the kernel's view of the clock come from; tests point them elsewhere
var (
	localtime = "/etc/localtime"
	timezone  = "/etc/timezone"
	adjtimex  = syscall.Adjtimex
)

// The kernel's answer for a clock no time server has set, and the flag that says so
const (
	timeError = 5
	staUnsync = 0x40
)

// ReadClock is the NAS's time, its zone as timedatectl names it, and whether
// chrony has the clock synchronised. The web front can't ask the kernel that
// itself: its sandbox keeps it away from the clock calls.
func ReadClock() protocol.Clock {
	clock := protocol.Clock{Now: time.Now().UnixMilli(), Zone: localZone()}
	// Modes 0 only reads: nothing about the clock changes
	var state syscall.Timex
	result, err := adjtimex(&state)
	if err == nil {
		clock.Known = true
		clock.Synced = result != timeError && state.Status&staUnsync == 0
	}
	return clock
}

func localZone() string {
	if target, err := os.Readlink(localtime); err == nil {
		if _, zone, found := strings.Cut(target, "zoneinfo/"); found && zone != "" {
			return zone
		}
	}
	if data, err := os.ReadFile(timezone); err == nil {
		if zone := strings.TrimSpace(string(data)); zone != "" {
			return zone
		}
	}
	return "UTC"
}
