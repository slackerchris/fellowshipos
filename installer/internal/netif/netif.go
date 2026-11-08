// Package netif finds the machine's wired network ports, leaving out
// virtual ones such as Docker's bridge, which the live system also has.
package netif

import (
	"os"
	"path/filepath"
	"sort"
)

// Wired lists the ethernet ports backed by real (or virtual machine)
// hardware, under a /sys/class/net directory
func Wired(sysClassNet string) []string {
	entries, err := os.ReadDir(sysClassNet)
	if err != nil {
		return nil
	}
	var found []string
	for _, entry := range entries {
		base := filepath.Join(sysClassNet, entry.Name())
		// Hardware has a device link; bridges, veths and loopback don't
		if _, err := os.Stat(filepath.Join(base, "device")); err != nil {
			continue
		}
		// Wi-Fi needs a network name and password, which the desktop will ask for
		if _, err := os.Stat(filepath.Join(base, "wireless")); err == nil {
			continue
		}
		found = append(found, entry.Name())
	}
	sort.Strings(found)
	return found
}
