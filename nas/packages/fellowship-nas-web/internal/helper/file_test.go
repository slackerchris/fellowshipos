// The file ops are where a session's identity decides which files can be
// reached, so these tests are about refusal as much as about success.
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

// fileFake is a helper whose fnas knows two shares, one per person, and whose
// child records what it was asked to do and as whom.
type fileFake struct {
	roots    string // what `fnas share roots USER` prints
	ran      [][]string
	childed  []string
	rootSeen string
	person   Account
	body     string
	uploaded string
}

func newFileFake(t *testing.T) (*fileFake, *Helper, string) {
	t.Helper()
	dir := t.TempDir()
	f := &fileFake{
		roots: `[{"share":"media","path":"` + dir + `","level":"read","through":"own","snapshots":true}]`,
		body:  "{\"size\":5,\"length\":5}\nhello",
	}
	h := New(Config{
		Authenticate: func(user, password string) error { return nil },
		Lookup: func(user string) (Account, error) {
			return Account{UID: 1001, GID: 1001, Groups: []uint32{1001, 1002}}, nil
		},
		Fnas: func(args []string, input string) (string, string, error) {
			f.ran = append(f.ran, args)
			return f.roots, "", nil
		},
		Child: func(account Account, body io.Reader, op, root, path string, start, length int64,
			extra ...string) (io.ReadCloser, error) {
			f.person = account
			f.rootSeen = root
			f.childed = append(f.childed, op+" "+path+" "+strings.Join(extra, " "))
			if body != nil {
				written, _ := io.ReadAll(body)
				f.uploaded = string(written)
			}
			return io.NopCloser(strings.NewReader(f.body)), nil
		},
		Now: func() time.Time { return time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC) },
	})
	return f, h, dir
}

func TestListSharesNamesNoPath(t *testing.T) {
	f, h, dir := newFileFake(t)
	token := login(t, h, "kim").Token
	response := h.Handle(protocol.Request{Op: protocol.OpFileList, Session: token})
	if response.Code != "" {
		t.Fatalf("listing shares: %s %s", response.Code, response.Error)
	}
	if strings.Contains(response.Output, dir) || strings.Contains(response.Output, "/") {
		t.Errorf("the desktop was given a path: %s", response.Output)
	}
	if !strings.Contains(response.Output, `"media"`) {
		t.Errorf("the desktop wasn't told about media: %s", response.Output)
	}
	// The roots come from fnas, asked about the session's own user
	if len(f.ran) == 0 || strings.Join(f.ran[0], " ") != "--json share roots kim" {
		t.Errorf("fnas was asked %v", f.ran)
	}
}

func TestAShareAPersonCantUseIsRefusedBeforeAnyFileWork(t *testing.T) {
	f, h, _ := newFileFake(t)
	token := login(t, h, "kim").Token
	for _, op := range []string{protocol.OpFileList, protocol.OpFileRead} {
		request := protocol.Request{Op: op, Session: token, Share: "private", Path: ""}
		response, body := h.Stream(request, nil)
		if response.Code != protocol.CodeForbidden {
			t.Errorf("%s of a share they can't use answered %q, want it refused", op, response.Code)
		}
		if !strings.Contains(response.Error, "isn't a share you can use") {
			t.Errorf("%s said %q", op, response.Error)
		}
		if body != nil {
			body.Close()
			t.Errorf("%s handed back a body for a share they can't use", op)
		}
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a share they can't use: %v", f.childed)
	}
}

func TestListingAFolderRunsAsThePerson(t *testing.T) {
	f, h, dir := newFileFake(t)
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	f.body = `{"entries":[{"name":"notes.txt","dir":false,"size":5}]}`
	token := login(t, h, "kim").Token
	response := h.Handle(protocol.Request{Op: protocol.OpFileList, Session: token,
		Share: "media", Path: ""})
	if response.Code != "" {
		t.Fatalf("listing: %s %s", response.Code, response.Error)
	}
	if !strings.Contains(response.Output, "notes.txt") {
		t.Errorf("the folder listing was %s", response.Output)
	}
	if len(f.childed) != 1 || !strings.HasPrefix(f.childed[0], "list ") {
		t.Errorf("the child was asked %v", f.childed)
	}
	if f.person.UID != 1001 || f.person.GID != 1001 || len(f.person.Groups) != 2 {
		t.Errorf("the child would have run as %+v", f.person)
	}
}

func TestReadingSendsTheHeadThenTheBytes(t *testing.T) {
	_, h, dir := newFileFake(t)
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	token := login(t, h, "kim").Token
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: "media", Path: "notes.txt"}, nil)
	if response.Code != "" {
		t.Fatalf("reading: %s %s", response.Code, response.Error)
	}
	if response.Size != 5 || response.Length != 5 {
		t.Errorf("the answer said size %d, length %d", response.Size, response.Length)
	}
	if body == nil {
		t.Fatal("no bytes followed the answer")
	}
	defer body.Close()
	sent, err := io.ReadAll(body)
	if err != nil {
		t.Fatal(err)
	}
	if string(sent) != "hello" {
		t.Errorf("the bytes were %q", sent)
	}
}

func TestAChildThatRefusesSaysWhy(t *testing.T) {
	_, h, dir := newFileFake(t)
	file := filepath.Join(dir, "notes.txt")
	if err := os.WriteFile(file, []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	h.config.Child = func(Account, io.Reader, string, string, string, int64, int64, ...string) (io.ReadCloser, error) {
		return io.NopCloser(strings.NewReader(`{"error":"you don't have permission to open that"}` + "\n")), nil
	}
	token := login(t, h, "kim").Token
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: "media", Path: "notes.txt"}, nil)
	if body != nil {
		body.Close()
	}
	if response.Code != protocol.CodeFailed {
		t.Fatalf("a refused read answered %q", response.Code)
	}
	if !strings.Contains(response.Error, "permission") {
		t.Errorf("the refusal said %q", response.Error)
	}
}

func TestAClimbOutIsRefusedBeforeAnyFileWork(t *testing.T) {
	f, h, _ := newFileFake(t)
	token := login(t, h, "kim").Token
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: "media", Path: "../../etc/shadow"}, nil)
	if body != nil {
		body.Close()
	}
	if response.Code != protocol.CodeForbidden {
		t.Fatalf("climbing out answered %q", response.Code)
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a path outside the share: %v", f.childed)
	}
}

// The write side: a share the person may only read refuses everything, and a
// name that is already there is not quietly replaced.
func TestWritingNeedsAShareThePersonCanChange(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"read","through":"own","snapshots":true}]`
	token := login(t, h, "kim").Token
	cases := []struct {
		name    string
		request protocol.Request
		body    string
	}{
		{"an upload", protocol.Request{Op: protocol.OpFileWrite, Share: "media", Path: "new.txt"}, "hello"},
		{"a new folder", protocol.Request{Op: protocol.OpFileMkdir, Share: "media", Path: "holiday"}, ""},
		{"a rename", protocol.Request{Op: protocol.OpFileRename, Share: "media", Path: "notes.txt", To: "other.txt"}, ""},
		{"a delete", protocol.Request{Op: protocol.OpFileDelete, Share: "media", Path: "notes.txt"}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			c.request.Session = token
			response, body := h.Stream(c.request, strings.NewReader(c.body))
			if body != nil {
				body.Close()
			}
			if response.Code != protocol.CodeForbidden {
				t.Fatalf("%s answered %q, want it refused", c.name, response.Code)
			}
			if !strings.Contains(response.Error, "read only") {
				t.Errorf("%s said %q", c.name, response.Error)
			}
		})
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a share that can only be read: %v", f.childed)
	}
}

func TestUploadArrivesAsThePersonsChild(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"write","through":"own","snapshots":true}]`
	f.body = `{"size":5,"length":5}` + "\n"
	token := login(t, h, "kim").Token
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileWrite, Session: token,
		Share: "media", Path: "new notes.txt"}, strings.NewReader("hello"))
	if body != nil {
		body.Close()
	}
	if response.Code != "" {
		t.Fatalf("upload: %s %s", response.Code, response.Error)
	}
	if response.Size != 5 {
		t.Errorf("the answer said %d bytes", response.Size)
	}
	if f.uploaded != "hello" {
		t.Errorf("the child was given %q", f.uploaded)
	}
	// The child gets the share's real folder and a path inside it, never a whole path to trust
	real, _ := filepath.EvalSymlinks(dir)
	if len(f.childed) != 1 || f.childed[0] != "write new notes.txt " || f.rootSeen != real {
		t.Errorf("the child was asked %v under %q", f.childed, f.rootSeen)
	}
}

func TestUploadsCannotClimbOutOrLandOnWhatIsThere(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"write","through":"own","snapshots":true}]`
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	token := login(t, h, "kim").Token
	for _, path := range []string{"../escape.txt", "notes.txt"} {
		response, body := h.Stream(protocol.Request{Op: protocol.OpFileWrite, Session: token,
			Share: "media", Path: path}, strings.NewReader("hello"))
		if body != nil {
			body.Close()
		}
		if response.Code == "" {
			t.Errorf("%q was written", path)
		}
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a refused upload: %v", f.childed)
	}
}

func TestAFolderDeleteSaysWhetherItTakesWhatIsInside(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"write","through":"own","snapshots":true}]`
	if err := os.Mkdir(filepath.Join(dir, "photos"), 0o755); err != nil {
		t.Fatal(err)
	}
	f.body = `{}` + "\n"
	token := login(t, h, "kim").Token
	response := h.Handle(protocol.Request{Op: protocol.OpFileDelete, Session: token,
		Share: "media", Path: "photos"})
	if response.Code != "" {
		t.Fatalf("delete: %s %s", response.Code, response.Error)
	}
	// The path once, then the word: before, the path went twice and "tree" never reached the child
	if got := f.childed[len(f.childed)-1]; got != "remove photos keep" {
		t.Errorf("a plain delete asked the child for %q", got)
	}
	h.Handle(protocol.Request{Op: protocol.OpFileDelete, Session: token, Share: "media",
		Path: "photos", Recursive: true})
	if got := f.childed[len(f.childed)-1]; got != "remove photos tree" {
		t.Errorf("a folder delete asked the child for %q", got)
	}
}

func TestAChildThatSaysNothingDidntFinish(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"write","through":"own","snapshots":true}]`
	// A child killed part way prints nothing at all
	f.body = ""
	token := login(t, h, "kim").Token
	response := h.Handle(protocol.Request{Op: protocol.OpFileMkdir, Session: token, Share: "media", Path: "holiday"})
	if response.Code == "" {
		t.Fatal("a child that said nothing was taken for success")
	}
	if !strings.Contains(response.Error, "stopped before it finished") {
		t.Errorf("it said %q", response.Error)
	}
}

func TestSharesAreAskedForOnceAWhile(t *testing.T) {
	f, h, _ := newFileFake(t)
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	h.config.Now = func() time.Time { return now }
	token := login(t, h, "kim").Token
	for i := 0; i < 3; i++ {
		h.Handle(protocol.Request{Op: protocol.OpFileList, Session: token})
	}
	if len(f.ran) != 1 {
		t.Errorf("three listings asked fnas %d times", len(f.ran))
	}
	// A change to who may use a share reaches Files within RootsFor
	now = now.Add(RootsFor)
	h.Handle(protocol.Request{Op: protocol.OpFileList, Session: token})
	if len(f.ran) != 2 {
		t.Errorf("after %s fnas was asked %d times in all", RootsFor, len(f.ran))
	}
}

func TestAZipChecksEveryNameBeforeTheChildRuns(t *testing.T) {
	f, h, dir := newFileFake(t)
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	token := login(t, h, "kim").Token
	for _, names := range [][]string{nil, {"../etc"}, {"notes.txt", "a/b"}, {".."}, {"gone.txt"}} {
		response, body := h.Stream(protocol.Request{Op: protocol.OpFileZip, Session: token, Share: "media",
			Names: names}, nil)
		if body != nil {
			body.Close()
		}
		if response.Code == "" {
			t.Errorf("a zip of %q was started", names)
		}
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a zip that should have been refused: %v", f.childed)
	}
	f.body = `{"size":5}` + "\nPK"
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileZip, Session: token, Share: "media",
		Names: []string{"notes.txt"}}, nil)
	if response.Code != "" || body == nil {
		t.Fatalf("a zip of notes.txt answered %q %s", response.Code, response.Error)
	}
	body.Close()
	if len(f.childed) != 1 || f.childed[0] != "zip  notes.txt" {
		t.Errorf("the child was asked %v", f.childed)
	}
}

func TestACopyIsCheckedBeforeTheChildRuns(t *testing.T) {
	f, h, dir := newFileFake(t)
	f.roots = `[{"share":"media","path":"` + dir + `","level":"write","through":"own","snapshots":true}]`
	for _, made := range []error{os.Mkdir(filepath.Join(dir, "photos"), 0o755),
		os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("x"), 0o644)} {
		if made != nil {
			t.Fatal(made)
		}
	}
	token := login(t, h, "kim").Token
	for _, c := range []struct{ from, to, says string }{
		{"photos", "photos/inside", "into itself"},
		{"photos", "notes.txt", "already here"},
		{"photos", "../elsewhere", "outside"},
		{"gone", "here", "isn't in media"},
	} {
		response := h.Handle(protocol.Request{Op: protocol.OpFileCopy, Session: token, Share: "media",
			Path: c.from, To: c.to})
		if response.Code == "" || !strings.Contains(response.Error, c.says) {
			t.Errorf("copying %s to %s answered %q %q", c.from, c.to, response.Code, response.Error)
		}
	}
	if len(f.childed) != 0 {
		t.Errorf("a child was run for a refused copy: %v", f.childed)
	}
	f.body = "{}\n"
	if response := h.Handle(protocol.Request{Op: protocol.OpFileCopy, Session: token, Share: "media",
		Path: "photos", To: "photos copy"}); response.Code != "" {
		t.Fatalf("a copy answered %q %q", response.Code, response.Error)
	}
	if len(f.childed) != 1 || f.childed[0] != "copy photos photos copy" {
		t.Errorf("the child was asked %v", f.childed)
	}
}

func TestAReadOnlyShareGivesATreeButNoCopy(t *testing.T) {
	f, h, _ := newFileFake(t)
	token := login(t, h, "kim").Token
	if response := h.Handle(protocol.Request{Op: protocol.OpFileCopy, Session: token, Share: "media",
		Path: "a", To: "b"}); !strings.Contains(response.Error, "read only") {
		t.Errorf("a copy on a read-only share answered %q", response.Error)
	}
	f.body = `{"folders":["photos","photos/2025"],"cut":false}` + "\n"
	response := h.Handle(protocol.Request{Op: protocol.OpFileTree, Session: token, Share: "media"})
	if response.Code != "" || !strings.Contains(response.Output, "photos/2025") {
		t.Errorf("the tree answered %q %q %q", response.Code, response.Error, response.Output)
	}
}
