// The child is the last word on where a file operation lands, so these tests
// run against the real kernel: a share's folder with a link out of it, a link
// inside it, and paths that try to leave.
package filechild

import (
	"archive/zip"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// share makes a share's folder holding a file, a folder, a link out to a secret
// beside the share, and a link that stays inside it. It returns the open folder.
func share(t *testing.T) (int, string) {
	t.Helper()
	base := t.TempDir()
	dir := filepath.Join(base, "media")
	for _, made := range []error{
		os.Mkdir(dir, 0o755),
		os.Mkdir(filepath.Join(dir, "photos"), 0o755),
		os.WriteFile(filepath.Join(dir, "photos", "cat.png"), []byte("cat"), 0o644),
		os.WriteFile(filepath.Join(base, "secret"), []byte("secret"), 0o644),
		os.Symlink(filepath.Join(base, "secret"), filepath.Join(dir, "out")),
		os.Symlink("photos", filepath.Join(dir, "pictures")),
	} {
		if made != nil {
			t.Fatal(made)
		}
	}
	root, err := syscall.Open(dir, oPath|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { syscall.Close(root) })
	return root, dir
}

func TestAPathInsideTheShareOpens(t *testing.T) {
	root, _ := share(t)
	fd, err := beneath(root, "photos/cat.png", syscall.O_RDONLY)
	if err != nil {
		t.Fatalf("a file in the share was refused: %v", err)
	}
	syscall.Close(fd)
	// The share itself is the empty path
	fd, err = beneath(root, "", syscall.O_RDONLY|syscall.O_DIRECTORY)
	if err != nil {
		t.Fatalf("the share itself was refused: %v", err)
	}
	syscall.Close(fd)
}

func TestNoPathLeavesTheShare(t *testing.T) {
	root, _ := share(t)
	for _, inside := range []string{"../secret", "photos/../../secret", "/etc/passwd"} {
		if fd, err := beneath(root, inside, syscall.O_RDONLY); err == nil {
			syscall.Close(fd)
			t.Errorf("%q opened", inside)
		}
	}
}

// The helper hands over paths with every link already followed, so a link the
// child meets was put there since: refused, whether it leads out or stays in.
func TestALinkMetOnTheWayIsRefused(t *testing.T) {
	root, _ := share(t)
	for _, inside := range []string{"out", "pictures/cat.png"} {
		fd, err := beneath(root, inside, syscall.O_RDONLY)
		if err == nil {
			syscall.Close(fd)
			t.Errorf("%q opened through a link", inside)
			continue
		}
		if !errors.Is(err, syscall.ELOOP) && !errors.Is(err, syscall.EXDEV) {
			t.Errorf("%q was refused with %v", inside, err)
		}
		if !strings.Contains(explain(err).Error(), "changed while it was being opened") {
			t.Errorf("%q was explained as %q", inside, explain(err))
		}
	}
}

func TestAFolderIsListedThroughItsOwnDescriptor(t *testing.T) {
	root, _ := share(t)
	entries, err := folder(root, "photos")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name != "cat.png" || entries[0].Size != 3 {
		t.Errorf("photos held %+v", entries)
	}
	if _, err := folder(root, "pictures"); err == nil {
		t.Error("a folder was listed through a link")
	}
}

func TestANameIsReachedInsideItsOpenFolder(t *testing.T) {
	root, dir := share(t)
	target, release, err := named(root, "photos/dog.png")
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	if _, err := write(target, strings.NewReader("dog"), false); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(filepath.Join(dir, "photos", "dog.png"))
	if err != nil || string(got) != "dog" {
		t.Errorf("the upload landed as %q, %v", got, err)
	}
	// A name whose folder is a link, or that climbs, never gets a folder opened
	for _, inside := range []string{"pictures/dog.png", "../escape", "photos/.."} {
		if _, release, err := named(root, inside); err == nil {
			release()
			t.Errorf("%q was given a folder to write in", inside)
		}
	}
}

func TestAZipHoldsWhatWasPickedAndNothingALinkLeadsTo(t *testing.T) {
	root, dir := share(t)
	if err := os.Symlink(filepath.Join(filepath.Dir(dir), "secret"), filepath.Join(dir, "photos", "sneaky")); err != nil {
		t.Fatal(err)
	}
	items, total, err := gather(root, "", []string{"photos"})
	if err != nil {
		t.Fatal(err)
	}
	if total != 3 {
		t.Errorf("the zip counted %d bytes, want the 3 of cat.png", total)
	}
	var made strings.Builder
	if err := zipUp(&made, root, items); err != nil {
		t.Fatal(err)
	}
	reader, err := zip.NewReader(strings.NewReader(made.String()), int64(made.Len()))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, file := range reader.File {
		names = append(names, file.Name)
		if file.Name == "photos/cat.png" {
			opened, _ := file.Open()
			got, _ := io.ReadAll(opened)
			if string(got) != "cat" {
				t.Errorf("cat.png came out as %q", got)
			}
		}
	}
	// The link inside the folder is left out rather than followed out of the share
	if strings.Join(names, " ") != "photos/ photos/cat.png" {
		t.Errorf("the zip held %v", names)
	}
	// A picked name that is itself a link is refused outright
	if _, _, err := gather(root, "", []string{"out"}); err == nil {
		t.Error("a link was put in a zip")
	}
}

func TestAZipTooBigIsRefusedBeforeAByteIsSent(t *testing.T) {
	root, _ := share(t)
	kept := MaxZip
	MaxZip = 2
	defer func() { MaxZip = kept }()
	if _, _, err := gather(root, "", []string{"photos"}); err == nil || !strings.Contains(err.Error(), "2 GB") {
		t.Errorf("a zip over the cap answered %v", err)
	}
}
