// Package edition says which image the installer is running from. This copy
// of the installer belongs to FellowshipOS Multi-purpose and knows only that
// image; FellowshipOS Desktop and FellowshipNAS keep their own copies. The
// type stays so the boot menu's fellowship.installer= value is still read and
// a wrong one is reported, and so the page keeps its edition field.
package edition

import (
	"fmt"
	"strings"
)

// Edition is one of the images the boot menu can start.
type Edition string

// Multi is the FellowshipOS Multi-purpose image.
const Multi Edition = "multi"

// Parse reads an edition name. An empty value is the multi-purpose image.
func Parse(value string) (Edition, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "multi", "multi-purpose", "multipurpose", "standard":
		return Multi, nil
	}
	return "", fmt.Errorf("there is no %q image: this installer is FellowshipOS Multi-purpose's", value)
}

// FromCmdline reads fellowship.installer=<edition> from a kernel command line,
// which is how the boot menu says which image was started. Anything it can't
// read is still the multi-purpose image, so a machine booted without the
// option still installs something that works.
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
func (e Edition) Title() string { return "FellowshipOS Multi-purpose" }

// String is the name the command line and the page use.
func (e Edition) String() string { return string(e) }
