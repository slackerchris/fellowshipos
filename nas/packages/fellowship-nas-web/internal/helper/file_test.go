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
	roots   string // what `fnas share roots USER` prints
	ran     [][]string
	childed []string
	person  Account
	body    string
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
		Child: func(account Account, op, path string, start, length int64) (io.ReadCloser, error) {
			f.person = account
			f.childed = append(f.childed, op+" "+path)
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
		response, body := h.Stream(request)
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
		Share: "media", Path: "notes.txt"})
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
	h.config.Child = func(Account, string, string, int64, int64) (io.ReadCloser, error) {
		return io.NopCloser(strings.NewReader(`{"error":"you don't have permission to open that"}` + "\n")), nil
	}
	token := login(t, h, "kim").Token
	response, body := h.Stream(protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: "media", Path: "notes.txt"})
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
		Share: "media", Path: "../../etc/shadow"})
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
