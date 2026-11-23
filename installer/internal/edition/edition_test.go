package edition

import "testing"

func TestTheImageSaysWhatItIs(t *testing.T) {
	if got := Multi.Title(); got != "FellowshipOS Multi-purpose" {
		t.Errorf("the multi-purpose image is called %q", got)
	}
}

func TestParseAcceptsWhatTheBuildWrites(t *testing.T) {
	for value, want := range map[string]Edition{
		"": Multi, "multi": Multi, "Multi": Multi, "standard": Multi,
	} {
		got, err := Parse(value)
		if err != nil || got != want {
			t.Errorf("Parse(%q) gave %q, %v", value, got, err)
		}
	}
	for _, gone := range []string{"desktop", "tablet", "nas"} {
		if _, err := Parse(gone); err == nil {
			t.Errorf("an image this installer isn't, %q, was accepted", gone)
		}
	}
}

func TestFromCmdline(t *testing.T) {
	if got := FromCmdline("BOOT_IMAGE=/live/vmlinuz boot=live components fellowship.installer=multi quiet"); got != Multi {
		t.Errorf("the multi-purpose image read as %q", got)
	}
	// A machine with no option at all, or one nobody knows, is still the
	// multi-purpose image
	for _, cmdline := range []string{"boot=live components", "fellowship.installer=tablet", "fellowship.installer.remote=:8443"} {
		if got := FromCmdline(cmdline); got != Multi {
			t.Errorf("%q read as %q", cmdline, got)
		}
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
