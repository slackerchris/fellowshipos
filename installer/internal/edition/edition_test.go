package edition

import "testing"

func TestEachImageSaysWhatItIs(t *testing.T) {
	for which, title := range map[Edition]string{
		Multi:   "FellowshipOS Multi-purpose",
		Desktop: "FellowshipOS Desktop",
	} {
		if got := which.Title(); got != title {
			t.Errorf("%s is called %q", which, got)
		}
	}
}

func TestParseAcceptsWhatTheBuildWrites(t *testing.T) {
	for value, want := range map[string]Edition{
		"": Multi, "multi": Multi, "Multi": Multi, "standard": Multi,
		"desktop": Desktop, "Desktop": Desktop,
	} {
		got, err := Parse(value)
		if err != nil || got != want {
			t.Errorf("Parse(%q) gave %q, %v", value, got, err)
		}
	}
	for _, gone := range []string{"tablet", "nas"} {
		if _, err := Parse(gone); err == nil {
			t.Errorf("an image that doesn't exist, %q, was accepted", gone)
		}
	}
}

func TestFromCmdline(t *testing.T) {
	if got := FromCmdline("BOOT_IMAGE=/live/vmlinuz boot=live components fellowship.installer=multi quiet"); got != Multi {
		t.Errorf("the multi-purpose image read as %q", got)
	}
	if got := FromCmdline("boot=live fellowship.installer=desktop"); got != Desktop {
		t.Errorf("the desktop image read as %q", got)
	}
	// A machine with no option at all is the multi-purpose image
	if got := FromCmdline("boot=live components"); got != Multi {
		t.Errorf("no option read as %q", got)
	}
	// A value nobody knows doesn't leave the installer without an image
	if got := FromCmdline("fellowship.installer=tablet"); got != Multi {
		t.Errorf("an unknown image read as %q", got)
	}
	// The remote option is a different key and must not be read as the image
	if got := FromCmdline("fellowship.installer.remote=:8443"); got != Multi {
		t.Errorf("the remote option read as the image %q", got)
	}
}

func TestRemoteFromCmdline(t *testing.T) {
	if got := RemoteFromCmdline("boot=live fellowship.installer=multi fellowship.installer.remote=:8443"); got != ":8443" {
		t.Errorf("the remote address read as %q", got)
	}
	if got := RemoteFromCmdline("boot=live fellowship.installer=multi"); got != "" {
		t.Errorf("with no remote option the address is %q", got)
	}
}
