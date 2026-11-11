package edition

import "testing"

func TestEachImageSaysWhatItInstalls(t *testing.T) {
	for _, c := range []struct {
		which     Edition
		title     string
		role      string
		wholeDisk bool
	}{
		{NAS, "FellowshipOS NAS", "nas", false},
		{Multi, "FellowshipOS Multi-purpose", "", true},
	} {
		if got := c.which.Title(); got != c.title {
			t.Errorf("%s is called %q", c.which, got)
		}
		if got := c.which.Role(); got != c.role {
			t.Errorf("%s records role %q", c.which, got)
		}
		if got := c.which.WholeDisk(); got != c.wholeDisk {
			t.Errorf("%s whole disk is %v", c.which, got)
		}
	}
}

func TestParseAcceptsWhatTheBuildWrites(t *testing.T) {
	for value, want := range map[string]Edition{
		"nas": NAS, "NAS": NAS, "": NAS, "multi": Multi, "Multi": Multi, "standard": Multi,
	} {
		got, err := Parse(value)
		if err != nil || got != want {
			t.Errorf("Parse(%q) gave %q, %v", value, got, err)
		}
	}
	if _, err := Parse("desktop"); err == nil {
		t.Error("an image that doesn't exist was accepted")
	}
}

func TestFromCmdline(t *testing.T) {
	if got := FromCmdline("BOOT_IMAGE=/live/vmlinuz boot=live components fellowship.installer=multi quiet"); got != Multi {
		t.Errorf("the multi-purpose image read as %q", got)
	}
	if got := FromCmdline("boot=live fellowship.installer=nas"); got != NAS {
		t.Errorf("the NAS image read as %q", got)
	}
	// A machine with no option at all is the NAS image, as it always was
	if got := FromCmdline("boot=live components"); got != NAS {
		t.Errorf("no option read as %q", got)
	}
	// A value nobody knows doesn't leave the installer without an image
	if got := FromCmdline("fellowship.installer=desktop"); got != NAS {
		t.Errorf("an unknown image read as %q", got)
	}
	// The remote option is a different key and must not be read as the image
	if got := FromCmdline("fellowship.installer.remote=:8443"); got != NAS {
		t.Errorf("the remote option read as the image %q", got)
	}
}

func TestRemoteFromCmdline(t *testing.T) {
	if got := RemoteFromCmdline("boot=live fellowship.installer=nas fellowship.installer.remote=:8443"); got != ":8443" {
		t.Errorf("the remote address read as %q", got)
	}
	if got := RemoteFromCmdline("boot=live fellowship.installer=nas"); got != "" {
		t.Errorf("with no remote option the address is %q", got)
	}
}
