package main

import (
	"time"

	"fellowship-installer/internal/disks"
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
	"fellowship-installer/internal/server"
)

// demoMachine has sample disks and pretends to install, touching nothing,
// for working on the install page away from a real machine
func demoMachine() server.Machine {
	gb := int64(1000 * 1000 * 1000)
	return server.Machine{
		Installed: 3 * layout.GiB, Memory: 8 * layout.GiB,
		Disks: func() ([]disks.Disk, error) {
			return []disks.Disk{
				{Path: "/dev/nvme0n1", Size: 500 * gb, Model: "Samsung SSD 980", Serial: "S64DNX0R", Bus: "nvme"},
				{Path: "/dev/sda", Size: 8000 * gb, Model: "WDC WD80EFZZ", Serial: "VGH4KZ2G", Bus: "sata",
					Contents: []string{`part of ZFS pool "tank"`}},
				{Path: "/dev/sdb", Size: 64 * gb, Model: "SanDisk Ultra", Serial: "4C53000", Bus: "usb"},
				{Path: "/dev/sdc", Size: 16 * gb, Model: "USB Stick", Bus: "usb",
					Refused: "This is the USB stick or disc FellowshipOS is running from."},
			}, nil
		},
		Timezones: func() ([]string, error) {
			return []string{"America/Chicago", "America/New_York", "Europe/London", "UTC"}, nil
		},
		Addresses: func() []string { return []string{"192.168.1.40"} },
		Install: func(s engine.Settings, step, logf func(string)) error {
			for _, title := range engine.Steps {
				step(title)
				time.Sleep(1500 * time.Millisecond)
			}
			logf("Demo only: nothing was written")
			return nil
		},
		Reboot: func() error { return nil },
	}
}
