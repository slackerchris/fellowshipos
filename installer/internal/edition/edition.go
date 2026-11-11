// Package edition says which image the installer is running from. One
// installer serves both: the NAS image records the NAS role and leaves a data
// partition for a pool, the multi-purpose image chooses no role and gives the
// system the whole disk.
package edition

import (
	"fmt"
	"strings"
)

// Edition is one of the images the boot menu can start.
type Edition string

const (
	// NAS is the FellowshipOS NAS image.
	NAS Edition = "nas"
	// Multi is the FellowshipOS Multi-purpose image.
	Multi Edition = "multi"
)

// Parse reads an edition name. An empty value is the NAS image, which is what
// every image was before there were two.
func Parse(value string) (Edition, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "nas":
		return NAS, nil
	case "multi", "multi-purpose", "multipurpose", "standard":
		return Multi, nil
	}
	return "", fmt.Errorf("there is no %q image: this installer has nas and multi", value)
}

// FromCmdline reads fellowship.installer=<edition> from a kernel command line,
// which is how the boot menu says which image was started. Anything it can't
// read is the NAS image, so a machine booted without the option still installs
// something that works.
func FromCmdline(cmdline string) Edition {
	for _, field := range strings.Fields(cmdline) {
		if value, found := strings.CutPrefix(field, "fellowship.installer="); found {
			if which, err := Parse(value); err == nil {
				return which
			}
		}
	}
	return NAS
}

// RemoteFromCmdline reads fellowship.installer.remote=<address>, the way a
// machine with no screen asks for its page to be served to another computer.
// It is empty unless somebody typed it at the boot menu.
func RemoteFromCmdline(cmdline string) string {
	for _, field := range strings.Fields(cmdline) {
		if value, found := strings.CutPrefix(field, "fellowship.installer.remote="); found {
			return value
		}
	}
	return ""
}

// Title is the image's name, as the boot menu and the console print it.
func (e Edition) Title() string {
	if e == Multi {
		return "FellowshipOS Multi-purpose"
	}
	return "FellowshipOS NAS"
}

// Subtitle is the second line of the page's heading.
func (e Edition) Subtitle() string {
	if e == Multi {
		return "installer"
	}
	return "NAS installer"
}

// Role is what first boot should install: the NAS image asks for the NAS role;
// the multi-purpose image chooses nothing, so the usual choices are offered.
func (e Edition) Role() string {
	if e == Multi {
		return ""
	}
	return "nas"
}

// WholeDisk is whether the system takes the whole disk. The NAS image leaves
// the rest as a data partition for a pool; the multi-purpose image has no pool
// to prepare for.
func (e Edition) WholeDisk() bool {
	return e == Multi
}

// String is the name the command line and the page use.
func (e Edition) String() string { return string(e) }
