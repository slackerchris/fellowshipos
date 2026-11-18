package filechild

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDeletingIntoTheBinKeepsThePathAndTheEarlierOne(t *testing.T) {
	root, dir := share(t)
	old := time.Now().Add(-90 * 24 * time.Hour)
	if err := os.Chtimes(filepath.Join(dir, "photos", "cat.png"), old, old); err != nil {
		t.Fatal(err)
	}
	to, err := recycle(root, "photos/cat.png", "kim")
	if err != nil {
		t.Fatal(err)
	}
	if to != ".recycle/kim/photos/cat.png" {
		t.Errorf("it went to %s", to)
	}
	if _, err := os.Lstat(filepath.Join(dir, "photos", "cat.png")); err == nil {
		t.Error("it is still where it was")
	}
	// The emptying goes by the time it went in the bin, not when it last changed
	info, err := os.Stat(filepath.Join(dir, to))
	if err != nil {
		t.Fatal(err)
	}
	if time.Since(info.ModTime()) > time.Hour {
		t.Errorf("its time stayed %v, so it would be emptied tonight", info.ModTime())
	}
	// A second cat.png keeps the first and comes in as a copy, as Samba names it
	if err := os.WriteFile(filepath.Join(dir, "photos", "cat.png"), []byte("cat 2"), 0o644); err != nil {
		t.Fatal(err)
	}
	if to, err = recycle(root, "photos/cat.png", "kim"); err != nil || to != ".recycle/kim/photos/Copy #1 of cat.png" {
		t.Errorf("the second went to %q, %v", to, err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, ".recycle/kim/photos/cat.png")); string(got) != "cat" {
		t.Errorf("the first one holds %q now", got)
	}
}

func TestAFolderGoesInTheBinWithEverythingInItTouched(t *testing.T) {
	root, dir := share(t)
	old := time.Now().Add(-90 * 24 * time.Hour)
	os.Chtimes(filepath.Join(dir, "photos", "cat.png"), old, old)
	if _, err := recycle(root, "photos", "kim"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filepath.Join(dir, ".recycle", "kim", "photos", "cat.png"))
	if err != nil {
		t.Fatal(err)
	}
	if time.Since(info.ModTime()) > time.Hour {
		t.Error("a file inside the folder kept its old time, so the emptying would take it early")
	}
	// The link inside the share went in as a link, and what it pointed at is untouched
	if _, err := recycle(root, "out", "kim"); err != nil {
		t.Fatal(err)
	}
	secret, _ := os.Stat(filepath.Join(filepath.Dir(dir), "secret"))
	if time.Since(secret.ModTime()) < time.Hour && secret.ModTime().After(info.ModTime().Add(time.Second)) {
		t.Error("the file a link pointed at was touched")
	}
}

func TestTheBinRefusesWhatIsAlreadyInItAndOddNames(t *testing.T) {
	root, _ := share(t)
	for _, c := range []struct{ inside, person, says string }{
		{"", "kim", "share itself"},
		{".recycle/kim/x", "kim", "already in"},
		{"photos/cat.png", "../kim", "person's name"},
		{"photos/cat.png", "", "person's name"},
	} {
		if _, err := recycle(root, c.inside, c.person); err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("recycling %q for %q answered %v, want %q", c.inside, c.person, err, c.says)
		}
	}
}

func TestPuttingBackMakesTheFolderAgainAndReplacesNothing(t *testing.T) {
	root, dir := share(t)
	if _, err := recycle(root, "photos/cat.png", "kim"); err != nil {
		t.Fatal(err)
	}
	// The folder it was in has gone since
	if err := os.Remove(filepath.Join(dir, "photos")); err != nil {
		t.Fatal(err)
	}
	if to, err := putBack(root, ".recycle/kim/photos/cat.png", "kim", "photos/cat.png"); err != nil || to != "photos/cat.png" {
		t.Fatalf("put back to %q, %v", to, err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "photos", "cat.png")); string(got) != "cat" {
		t.Errorf("it came back holding %q", got)
	}
	// Something new has the name: the one from the bin comes back beside it
	if _, err := recycle(root, "photos/cat.png", "kim"); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, "photos", "cat.png"), []byte("new cat"), 0o644)
	if to, err := putBack(root, ".recycle/kim/photos/cat.png", "kim", "photos/cat.png"); err != nil || to != "photos/cat (put back).png" {
		t.Fatalf("put back to %q, %v", to, err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "photos", "cat.png")); string(got) != "new cat" {
		t.Errorf("the new one was replaced: %q", got)
	}
}

func TestOnlyYourOwnBinCanBePutBackAndNeverOutOfTheShare(t *testing.T) {
	root, dir := share(t)
	os.MkdirAll(filepath.Join(dir, ".recycle", "sam"), 0o770)
	os.WriteFile(filepath.Join(dir, ".recycle", "sam", "diary.txt"), []byte("sam's"), 0o644)
	os.MkdirAll(filepath.Join(dir, ".recycle", "kim"), 0o770)
	os.WriteFile(filepath.Join(dir, ".recycle", "kim", "note.txt"), []byte("kim's"), 0o644)
	for _, c := range []struct{ inside, to, says string }{
		{".recycle/sam/diary.txt", "diary.txt", "your own"},
		{"photos/cat.png", "cat.png", "your own"},
		{".recycle/kim/note.txt", "../escaped.txt", "outside"},
		{".recycle/kim/note.txt", ".recycle/sam/note.txt", "can't be put back there"},
		{".recycle/kim/note.txt", "out/note.txt", "changed while"},
	} {
		if _, err := putBack(root, c.inside, "kim", c.to); err == nil || !strings.Contains(err.Error(), c.says) {
			t.Errorf("putting %q back to %q answered %v, want %q", c.inside, c.to, err, c.says)
		}
	}
	if _, err := os.Lstat(filepath.Join(filepath.Dir(dir), "escaped.txt")); err == nil {
		t.Error("something was put back outside the share")
	}
}

func TestTheBinIsLeftOutOfTheTreeAndSearch(t *testing.T) {
	root, _ := share(t)
	if _, err := recycle(root, "photos/cat.png", "kim"); err != nil {
		t.Fatal(err)
	}
	found, err := tree(root, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, folder := range found.Folders {
		if InBin(folder) {
			t.Errorf("the tree lists %s", folder)
		}
	}
	for _, hit := range search(root, "", "cat", time.Now()).Hits {
		if InBin(hit.Path) {
			t.Errorf("the search found %s", hit.Path)
		}
	}
}
