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
	"time"
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
	entries, _, err := folder(root, "photos")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name != "cat.png" || entries[0].Size != 3 {
		t.Errorf("photos held %+v", entries)
	}
	if _, _, err := folder(root, "pictures"); err == nil {
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

func TestAFolderIsCopiedWithWhatItHoldsButNoLink(t *testing.T) {
	root, dir := share(t)
	if err := os.Symlink(filepath.Join(filepath.Dir(dir), "secret"), filepath.Join(dir, "photos", "sneaky")); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(dir, "photos", "2025"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "photos", "2025", "dog.png"), []byte("dog"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := copyInto(root, "photos", "backup"); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{"backup/cat.png": "cat", "backup/2025/dog.png": "dog"} {
		got, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil || string(got) != want {
			t.Errorf("%s came out as %q, %v", name, got, err)
		}
	}
	if _, err := os.Lstat(filepath.Join(dir, "backup", "sneaky")); err == nil {
		t.Error("a link was copied")
	}
	// Nothing is left under a temporary name
	found, _ := os.ReadDir(dir)
	for _, entry := range found {
		if strings.HasPrefix(entry.Name(), workingPrefix) {
			t.Errorf("%s was left behind", entry.Name())
		}
	}
	// A file copies too, and keeps its bytes
	if err := copyInto(root, "photos/cat.png", "cat copy.png"); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "cat copy.png")); string(got) != "cat" {
		t.Errorf("the copied file held %q", got)
	}
}

func TestACopyNeverLandsOnWhatIsThereOrInsideItself(t *testing.T) {
	root, _ := share(t)
	for _, c := range []struct{ from, to, says string }{
		{"photos", "photos/inner", "into itself"},
		{"photos", "photos", "into itself"},
		{"photos/cat.png", "photos", "already here"},
		{"out", "copied", "changed while"},
	} {
		if err := copyInto(root, c.from, c.to); err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("copying %s to %s answered %v, want %q", c.from, c.to, err, c.says)
		}
	}
}

func TestTheTreeListsFoldersOnlyAndStopsInTime(t *testing.T) {
	root, dir := share(t)
	for _, folder := range []string{"photos/2025", "photos/2025/june", "docs", workingPrefix + "copy-1"} {
		if err := os.MkdirAll(filepath.Join(dir, folder), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	found, err := tree(root, "")
	if err != nil {
		t.Fatal(err)
	}
	// pictures is a link to photos, so it isn't a folder of its own
	if strings.Join(found.Folders, " ") != "docs photos photos/2025 photos/2025/june" || found.Cut {
		t.Errorf("the tree was %v, cut %v", found.Folders, found.Cut)
	}
	kept := MaxTree
	MaxTree = 2
	defer func() { MaxTree = kept }()
	found, _ = tree(root, "")
	if len(found.Folders) != 2 || !found.Cut {
		t.Errorf("a capped tree was %v, cut %v", found.Folders, found.Cut)
	}
}

// A share's folders carry the setgid bit so what is made in them keeps the share's
// group; making a folder, or copying one, must not clear it.
func TestNewFoldersKeepTheSharesGroupBit(t *testing.T) {
	root, dir := share(t)
	photos := filepath.Join(dir, "photos")
	if err := os.Chmod(photos, 0o775|os.ModeSetgid); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(photos, "2025"), 0o755); err != nil {
		t.Fatal(err)
	}
	// New folder, as the mkdir op makes it
	fresh := filepath.Join(photos, "fresh")
	if err := os.Mkdir(fresh, 0o775); err != nil {
		t.Fatal(err)
	}
	if err := forTheGroup(fresh); err != nil {
		t.Fatal(err)
	}
	// And a copied folder, made under a temporary name in the setgid folder
	if err := copyInto(root, "photos/2025", "photos/2025 copy"); err != nil {
		t.Fatal(err)
	}
	for _, folder := range []string{fresh, filepath.Join(photos, "2025 copy")} {
		info, err := os.Stat(folder)
		if err != nil || info.Mode()&os.ModeSetgid == 0 || info.Mode().Perm() != 0o775 {
			t.Errorf("%s ended up %v, %v", folder, info.Mode(), err)
		}
	}
}

func TestASearchFindsNamesAndNeverLeavesTheShare(t *testing.T) {
	root, dir := share(t)
	for _, made := range []error{
		os.MkdirAll(filepath.Join(dir, "photos", "2025", "Cats"), 0o755),
		os.WriteFile(filepath.Join(dir, "photos", "2025", "Cats", "big cat.png"), []byte("meow"), 0o644),
		os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("x"), 0o644),
	} {
		if made != nil {
			t.Fatal(made)
		}
	}
	found := search(root, "", "CAT", time.Now())
	var paths []string
	for _, hit := range found.Hits {
		paths = append(paths, hit.Path)
	}
	// Any case; folders and files; through photos but not through the pictures link,
	// and never the secret beside the share
	if strings.Join(paths, " ") != "photos/cat.png photos/2025/Cats photos/2025/Cats/big cat.png" || found.Stopped != "" {
		t.Errorf("a search for CAT found %v, stopped %q", paths, found.Stopped)
	}
	if found.Hits[2].Size != 4 || found.Hits[1].Dir != true {
		t.Errorf("the hits were %+v", found.Hits)
	}
	// A query is only compared with names, so a path in it finds nothing
	for _, query := range []string{"../secret", "photos/cat", "secret"} {
		if hits := search(root, "", query, time.Now()).Hits; len(hits) != 0 {
			t.Errorf("%q found %+v", query, hits)
		}
	}
}

func TestASearchStopsInTimeAndSaysWhy(t *testing.T) {
	root, _ := share(t)
	kept := MaxHits
	MaxHits = 1
	found := search(root, "", "c", time.Now())
	MaxHits = kept
	if len(found.Hits) != 1 || found.Stopped != "hits" {
		t.Errorf("a capped search answered %+v", found)
	}
	// One that has run out of time still answers, with what it had
	found = search(root, "", "cat", time.Now().Add(-time.Hour))
	if found.Stopped != "time" || found.Hits == nil {
		t.Errorf("a late search answered %+v", found)
	}
}

func TestPermissionsReadAsLsWritesThem(t *testing.T) {
	for mode, want := range map[uint32]string{
		0o644: "rw-r--r--", 0o2770: "rwxrws---", 0o2760: "rwxrwS---", 0o1777: "rwxrwxrwt", 0o4755: "rwsr-xr-x",
	} {
		if got := permissions(mode); got != want {
			t.Errorf("%o read as %s, want %s", mode, got, want)
		}
	}
}

// A listing says who may do what, with whether the person asking may change each
// thing coming from the kernel, so it can say why something can't be changed.
func TestAListingSaysWhoMayChangeWhat(t *testing.T) {
	root, dir := share(t)
	if err := os.WriteFile(filepath.Join(dir, "photos", "fixed.png"), []byte("x"), 0o444); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(dir, "photos"), 0o775|os.ModeSetgid); err != nil {
		t.Fatal(err)
	}
	entries, self, err := folder(root, "photos")
	if err != nil {
		t.Fatal(err)
	}
	if self.Mode != "rwxrwsr-x" || self.Owner == "" || self.Group == "" {
		t.Errorf("the folder itself was %+v", self)
	}
	byName := map[string]Entry{}
	for _, entry := range entries {
		byName[entry.Name] = entry
	}
	if byName["fixed.png"].Mode != "r--r--r--" || byName["cat.png"].Mode != "rw-r--r--" {
		t.Errorf("the files were %+v", entries)
	}
	// Whoever runs the test owns cat.png, so may change it; a read-only file is
	// still writable for root, so only the owner's own file is checked
	if !byName["cat.png"].Writable {
		t.Errorf("cat.png wasn't writable for its owner: %+v", byName["cat.png"])
	}
}
