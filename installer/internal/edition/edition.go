// Package edition says which image the installer is running from. One
// installer serves both: the multi-purpose and desktop images install the same
// way, giving the system the whole disk and choosing no role, so the first
// start offers the usual choices.
package edition

import (
	"fmt"
	"strings"
)

// Edition is one of the images the boot menu can start.
type Edition string

const (
	// Multi is the FellowshipOS Multi-purpose image.
	Multi Edition = "multi"
	// Desktop is the FellowshipOS Desktop image: the same live system with
	// the Fellowship desktop, its portals and its apps in it.
	Desktop Edition = "desktop"
)

// Parse reads an edition name. An empty value is the multi-purpose image.
func Parse(value string) (Edition, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "multi", "multi-purpose", "multipurpose", "standard":
		return Multi, nil
	case "desktop":
		return Desktop, nil
	}
	return "", fmt.Errorf("there is no %q image: this installer has multi and desktop", value)
}

// FromCmdline reads fellowship.installer=<edition> from a kernel command line,
// which is how the boot menu says which image was started. Anything it can't
// read is the multi-purpose image, so a machine booted without the option
// still installs something that works.
func FromCmdline(cmdline string) Edition {
	for _, field := range strings.Fields(cmdline) {
		if value, found := strings.CutPrefix(field, "fellowship.installer="); found {
			if which, err := Parse(value); err == nil {
				return which
			}
		}
	}
	return Multi
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
	if e == Desktop {
		return "FellowshipOS Desktop"
	}
	return "FellowshipOS Multi-purpose"
}

// String is the name the command line and the page use.
func (e Edition) String() string { return string(e) }
