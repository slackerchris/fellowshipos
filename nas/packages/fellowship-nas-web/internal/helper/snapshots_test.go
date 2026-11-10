// Snapshots are read by the helper as root, so these tests are about the
// boundary that stands in for the person's own permissions: which versions are
// offered, and which of them a person can actually get at.
package helper

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"fellowship-nas-web/internal/protocol"
)

// snapshotFake is a helper over one real folder, with .zfs trees written the way
// ZFS arranges them, so a restore can be checked by reading what landed.
type snapshotFake struct {
	dir     string // the share's own folder
	level   string // what fnas says the person may do in it
	childed []string
	roots   []string
	person  Account
	written string
	asked   [][]string
}

func newSnapshotFake(t *testing.T) (*snapshotFake, *Helper) {
	t.Helper()
	f := &snapshotFake{dir: t.TempDir(), level: "write"}
	h := New(Config{
		Authenticate: func(user, password string) error { return nil },
		Lookup: func(user string) (Account, error) {
			return Account{UID: 1001, GID: 1001, Groups: []uint32{1001}}, nil
		},
		Fnas: func(args []string, input string) (string, string, error) {
			f.asked = append(f.asked, args)
			for _, one := range args {
				if one == "roots" {
					return `[{"share":"media","path":"` + f.dir + `","level":"` + f.level +
						`","through":"own","snapshots":true}]`, "", nil
				}
			}
			return `[{"name":"hourly_1","created":1760000000,"used":0},
                {"name":"hourly_2","created":1760003600,"used":0}]`, "", nil
		},
		Child: func(account Account, body io.Reader, op, root, path string, start, length int64,
			extra ...string) (io.ReadCloser, error) {
			f.person = account
			f.childed = append(f.childed, op+" "+path)
			f.roots = append(f.roots, root)
			if body != nil {
				written, _ := io.ReadAll(body)
				f.written = string(written)
			}
			if op == "list" {
				return io.NopCloser(strings.NewReader(`{"entries":[],"self":{}}`)), nil
			}
			return io.NopCloser(strings.NewReader("{}")), nil
		},
		Now: func() time.Time { return time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC) },
	})
	return f, h
}

// version puts one file in one snapshot of the fake share.
func (f *snapshotFake) version(t *testing.T, snapshot, name, text string) {
	t.Helper()
	dir := filepath.Join(f.dir, ".zfs", "snapshot", snapshot, filepath.Dir(name))
	if err := os.MkdirAll(dir, 0o775); err != nil {
		t.Fatalf("making %s: %v", dir, err)
	}
	if err := os.WriteFile(filepath.Join(f.dir, ".zfs", "snapshot", snapshot, name), []byte(text),
		0o664); err != nil {
		t.Fatalf("writing %s: %v", name, err)
	}
}

// current puts a file in the share as it is now.
func (f *snapshotFake) current(t *testing.T, name, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(f.dir, filepath.Dir(name)), 0o775); err != nil {
		t.Fatalf("making the folder: %v", err)
	}
	if err := os.WriteFile(filepath.Join(f.dir, name), []byte(text), 0o664); err != nil {
		t.Fatalf("writing %s: %v", name, err)
	}
}

func TestEarlierVersionsAreCanonOffer(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	f.version(t, "hourly_1", "notes.txt", "hourly one\n")
	f.version(t, "hourly_2", "notes.txt", "hourly two\n")
	f.version(t, "hourly_1", "other.txt", "a different file\n")

	answer := h.fileSnapshots("clare", protocol.Request{Share: "media", Path: "notes.txt"})
	if answer.Error != "" {
		t.Fatalf("listing versions said %q", answer.Error)
	}
	for _, want := range []string{"hourly_1", "hourly_2"} {
		if !strings.Contains(answer.Output, want) {
			t.Errorf("%s is missing from %s", want, answer.Output)
		}
	}
	if strings.Contains(answer.Output, "other.txt") {
		t.Errorf("a version of a different file was offered: %s", answer.Output)
	}
}

func TestAVersionOfAFileNoSnapshotHolds(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	f.version(t, "hourly_1", "notes.txt", "hourly one\n")

	answer := h.fileSnapshots("clare", protocol.Request{Share: "media", Path: "other.txt"})
	if answer.Output != "[]" {
		t.Errorf("a file no snapshot holds was given versions: %q", answer.Output)
	}
	if answer.Error != "" {
		t.Errorf("asking for versions said %q", answer.Error)
	}
}

func TestReadingAVersionRefusesWhatItCannotName(t *testing.T) {
	cases := []struct {
		name    string
		request protocol.Request
		says    string
	}{
		{name: "a snapshot nobody made", request: protocol.Request{Share: "media", Path: "notes.txt",
			Snapshot: "hourly_99"}, says: "there is no snapshot called hourly_99"},
		{name: "no snapshot named at all", request: protocol.Request{Share: "media",
			Path: "notes.txt"}, says: "say which earlier version"},
		{name: "a share the person cannot use", request: protocol.Request{Share: "family",
			Path: "notes.txt", Snapshot: "hourly_1"}, says: "isn't a share you can use"},
		{name: "a climb out of the snapshot", request: protocol.Request{Share: "media",
			Path: "../../etc/passwd", Snapshot: "hourly_1"}, says: "outside media"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f, h := newSnapshotFake(t)
			f.current(t, "notes.txt", "as it is now\n")
			f.version(t, "hourly_1", "notes.txt", "hourly one\n")
			answer, body := h.readSnapshot("clare", c.request)
			if body != nil {
				body.Close()
			}
			if answer.Code == "" || !strings.Contains(answer.Error, c.says) {
				t.Errorf("reading answered %q %q", answer.Code, answer.Error)
			}
		})
	}
}

func TestAVersionWithNoCopyInASnapshotSaysSo(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	// hourly_2 exists, but has never held this file, so its own folder is absent

	answer, body := h.readSnapshot("clare", protocol.Request{Share: "media",
		Path: "notes.txt", Snapshot: "hourly_2"})
	if body != nil {
		body.Close()
	}
	if !strings.Contains(answer.Error, "hourly_2 has no copy of that file") {
		t.Errorf("reading answered %q %q", answer.Code, answer.Error)
	}
}

func TestAVersionIsReadWhenTheFolderCanBeListed(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	f.version(t, "hourly_1", "notes.txt", "hourly one\n")

	answer, body := h.readSnapshot("clare", protocol.Request{Share: "media",
		Path: "notes.txt", Snapshot: "hourly_1"})
	if answer.Error != "" {
		t.Fatalf("reading an earlier version said %q", answer.Error)
	}
	defer body.Close()
	held, _ := io.ReadAll(body)
	if string(held) != "hourly one\n" {
		t.Errorf("the version held %q", held)
	}
	// The child is given the share's folder and a name inside it, never the
	// whole path, so listing the file's own folder is "list " with nothing after
	listed := false
	for i, one := range f.childed {
		if one == "list " && f.roots[i] == f.dir {
			listed = true
		}
	}
	if !listed {
		t.Errorf("the person's own permission was never tried: %q", f.childed)
	}
}

func TestAVersionIsNotReadWhenTheFolderCannotBeListed(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	f.version(t, "hourly_1", "notes.txt", "hourly one\n")
	h.config.Child = func(account Account, body io.Reader, op, root, path string,
		start, length int64, extra ...string) (io.ReadCloser, error) {
		return io.NopCloser(strings.NewReader(`{"error":"permission denied","entries":[]}`)), nil
	}

	answer, body := h.readSnapshot("clare", protocol.Request{Share: "media",
		Path: "notes.txt", Snapshot: "hourly_1"})
	if body != nil {
		body.Close()
	}
	if !strings.Contains(answer.Error, "permission denied") {
		t.Errorf("reading answered %q %q", answer.Code, answer.Error)
	}
	_ = f
}

func TestRestoringWritesACopyAsThePerson(t *testing.T) {
	f, h := newSnapshotFake(t)
	f.current(t, "notes.txt", "as it is now\n")
	f.version(t, "hourly_1", "notes.txt", "hourly one\n")

	answer := h.fileRestore("clare", protocol.Request{Share: "media", Path: "notes.txt",
		Snapshot: "hourly_1", To: "notes (from before).txt"})
	if answer.Error != "" {
		t.Fatalf("restoring said %q", answer.Error)
	}
	if f.written != "hourly one\n" {
		t.Errorf("the child was handed %q", f.written)
	}
	found := false
	for i, one := range f.childed {
		if one == "write notes (from before).txt" && f.roots[i] == f.dir {
			found = true
		}
	}
	if !found {
		t.Errorf("the copy was not written by its own path: %q", f.childed)
	}
	if f.person.UID != 1001 {
		t.Errorf("the copy would have been written as uid %d", f.person.UID)
	}
	if now, err := os.ReadFile(filepath.Join(f.dir, "notes.txt")); err != nil ||
		string(now) != "as it is now\n" {
		t.Errorf("the file itself changed: %q %v", now, err)
	}
}

func TestRestoringRefuses(t *testing.T) {
	cases := []struct {
		name    string
		level   string
		request protocol.Request
		says    string
	}{
		{name: "a name already in the folder", request: protocol.Request{Share: "media",
			Path: "notes.txt", Snapshot: "hourly_1", To: "notes.txt"}, says: "is already here"},
		{name: "a climb out of the share", request: protocol.Request{Share: "media",
			Path: "notes.txt", Snapshot: "hourly_1", To: "../../escaped.txt"}, says: "outside media"},
		{name: "no name for the copy", request: protocol.Request{Share: "media",
			Path: "notes.txt", Snapshot: "hourly_1"}, says: "say what to call the restored copy"},
		{name: "a share that is read only", level: "read", request: protocol.Request{Share: "media",
			Path: "notes.txt", Snapshot: "hourly_1", To: "copy.txt"}, says: "read only for you"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f, h := newSnapshotFake(t)
			if c.level != "" {
				f.level = c.level
			}
			f.current(t, "notes.txt", "as it is now\n")
			f.version(t, "hourly_1", "notes.txt", "hourly one\n")
			answer := h.fileRestore("clare", c.request)
			if answer.Code == "" || !strings.Contains(answer.Error, c.says) {
				t.Errorf("restoring answered %q %q", answer.Code, answer.Error)
			}
			if f.written != "" {
				t.Errorf("a copy was written anyway: %q", f.written)
			}
		})
	}
}
