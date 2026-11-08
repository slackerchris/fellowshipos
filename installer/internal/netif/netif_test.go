package netif

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestWired(t *testing.T) {
	root := t.TempDir()
	make := func(name string, parts ...string) {
		os.MkdirAll(filepath.Join(root, name), 0o755)
		for _, part := range parts {
			os.MkdirAll(filepath.Join(root, name, part), 0o755)
		}
	}
	make("lo")
	make("docker0")
	make("veth1a2b")
	make("ens3", "device")
	make("enp4s0", "device")
	make("wlp2s0", "device", "wireless")
	if got := Wired(root); !slices.Equal(got, []string{"enp4s0", "ens3"}) {
		t.Fatalf("got %v", got)
	}
}
