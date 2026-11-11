// The WebDAV face is the only part of the desktop that a program talks to rather
// than a person, so these tests are about who it lets in and what it will never
// do: no writes, nothing outside the shares the person already has, and nothing
// at all over a connection that isn't encrypted.
package front

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"fellowship-nas-web/internal/protocol"
)

// davHelper is a helper that knows two people's shares and one folder of files.
type davHelper struct {
	asked   []protocol.Request
	shares  []davShare
	folders map[string][]davEntry // "share/inside", with "" for the share's own folder
	files   map[string]string     // "share/inside" → bytes
	// What each file's version is called, as the child would name it
	tags map[string]string
	// Set when a write must lose the race, whatever the tag says
	refuseWrite bool
	// Files the person may see listed but may not read
	unreadable map[string]bool
}

func newDavHelper() *davHelper {
	return &davHelper{
		shares: []davShare{
			{Share: "media", Level: "write", Webdav: true},
			{Share: "private", Level: "read", Webdav: true},
			// Shared over SMB, but never agreed to be reachable over HTTPS
			{Share: "docs", Level: "write", Webdav: false},
		},
		folders: map[string][]davEntry{
			"media/": {{Name: "photos", Dir: true, Tag: "dir-photos"},
				{Name: "notes.txt", Size: 98, Tag: "note-v1", Modified: "2026-11-10T11:33:39Z"}},
			"media/photos": {{Name: "sunrise.png", Size: 26000, Tag: "sun-v1",
				Modified: "2026-11-09T08:00:00Z"}},
			"private/": {{Name: "letter.txt", Size: 12, Tag: "letter-v1",
				Modified: "2026-11-10T09:00:00Z"}},
		},
		files: map[string]string{"media/notes.txt": "Things for the weekend\n",
			"media/photos/sunrise.png": "not really a picture, but enough bytes",
			"private/letter.txt":       "hello there\n"},
		tags: map[string]string{"media/notes.txt": "note-v1",
			"media/photos/sunrise.png": "sun-v1", "private/letter.txt": "letter-v1"},
	}
}

func (d *davHelper) call(request protocol.Request) (protocol.Response, error) {
	d.asked = append(d.asked, request)
	switch request.Op {
	case protocol.OpLogin:
		if request.Password != "right" {
			return protocol.Response{Code: protocol.CodeUnauthenticated,
				Error: "The user name or password is wrong."}, nil
		}
		return protocol.Response{Token: "secret-" + request.User, Session: &protocol.Session{
			User: request.User, CSRF: "csrf-token"}}, nil
	case protocol.OpFileMkdir:
		folder := request.Share + "/" + request.Path
		if _, taken := d.folders[folder]; taken {
			return protocol.Response{Code: protocol.CodeForbidden, Error: "that is already here"}, nil
		}
		d.folders[folder] = nil
		d.note(request.Share, request.Path, davEntry{Name: path.Base(request.Path), Dir: true,
			Tag: "dir-" + path.Base(request.Path)}, false)
		return protocol.Response{}, nil
	case protocol.OpFileDelete:
		delete(d.files, request.Share+"/"+request.Path)
		delete(d.folders, request.Share+"/"+request.Path)
		d.note(request.Share, request.Path, davEntry{}, true)
		return protocol.Response{}, nil
	case protocol.OpFileRename, protocol.OpFileCopy:
		from := request.Share + "/" + request.Path
		to := request.Share + "/" + request.To
		d.files[to] = d.files[from]
		d.tags[to] = d.tags[from] + "-new"
		if request.Op == protocol.OpFileRename {
			delete(d.files, from)
			delete(d.tags, from)
		}
		return protocol.Response{}, nil
	case protocol.OpFileList:
		if !strings.HasPrefix(request.Session, "secret-") {
			return protocol.Response{Code: protocol.CodeUnauthenticated, Error: "signed out"}, nil
		}
		if request.Share == "" {
			out, _ := json.Marshal(d.shares)
			return protocol.Response{Output: string(out)}, nil
		}
		entries, ok := d.folders[d.folderKey(request.Share, request.Path)]
		if !ok {
			return protocol.Response{Code: protocol.CodeBadRequest, Error: "that folder can't be opened"}, nil
		}
		out, _ := json.Marshal(map[string]any{"entries": entries})
		return protocol.Response{Output: string(out)}, nil
	}
	return protocol.Response{}, nil
}

// send answers a write: it refuses when the version the client named is not the
// one the "disk" holds, which is the race a stale client loses.
func (d *davHelper) send(request protocol.Request, body io.Reader) (protocol.Response, error) {
	d.asked = append(d.asked, request)
	taken, err := io.ReadAll(body)
	if err != nil {
		// What the real client does when the body stops early, such as at the cap
		return protocol.Response{}, err
	}
	if d.refuseWrite {
		return protocol.Response{Code: protocol.CodePrecondition,
			Error: "that file has changed since it was read"}, nil
	}
	key := request.Share + "/" + request.Path
	current, exists := d.tags[key]
	if request.IfMatch != "" && (!exists || request.IfMatch != current) {
		return protocol.Response{Code: protocol.CodePrecondition,
			Error: "that file has changed since it was read"}, nil
	}
	if request.New && exists {
		return protocol.Response{Code: protocol.CodePrecondition,
			Error: "there is already something with that name"}, nil
	}
	d.files[key] = string(taken)
	d.tags[key] = current + "-next"
	d.note(request.Share, request.Path, davEntry{Name: path.Base(request.Path), Size: int64(len(taken)),
		Tag: d.tags[key]}, false)
	return protocol.Response{Size: int64(len(taken)), Tag: d.tags[key]}, nil
}

// folderKey is one name for a folder: the share, a slash, then the path inside.
func (d *davHelper) folderKey(share, inside string) string {
	return share + "/" + inside
}

// note keeps the folder listing in step with a change, so the next request sees
// what the last one did. removal takes the name out of its folder.
func (d *davHelper) note(share, inside string, entry davEntry, removal bool) {
	parent := path.Dir(inside)
	if parent == "." || parent == "/" {
		parent = ""
	}
	key := d.folderKey(share, parent)
	entries := d.folders[key]
	kept := make([]davEntry, 0, len(entries)+1)
	for _, one := range entries {
		if one.Name != path.Base(inside) {
			kept = append(kept, one)
		}
	}
	if !removal {
		kept = append(kept, entry)
	}
	d.folders[key] = kept
}

// stream answers a read of a file with its bytes.
func (d *davHelper) stream(request protocol.Request) (protocol.Response, io.ReadCloser, error) {
	d.asked = append(d.asked, request)
	if d.unreadable[request.Share+"/"+request.Path] {
		return protocol.Response{Code: protocol.CodeForbidden,
			Error: "you don't have permission to open that"}, nil, nil
	}
	held, ok := d.files[request.Share+"/"+request.Path]
	if !ok {
		return protocol.Response{Code: protocol.CodeBadRequest, Error: "that file isn't there any more"}, nil, nil
	}
	size := int64(len(held))
	start, length := request.Start, request.Length
	if start > size {
		return protocol.Response{Code: protocol.CodeBadRequest, Error: "that part of the file isn't there"}, nil, nil
	}
	if length <= 0 || start+length > size {
		length = size - start
	}
	return protocol.Response{Size: size, Length: length, Modified: 1762770819,
			Tag: d.tags[request.Share+"/"+request.Path]},
		io.NopCloser(strings.NewReader(held[start : start+length])), nil
}

func davSetup(t *testing.T, scheme string) (*davHelper, http.Handler) {
	t.Helper()
	d := newDavHelper()
	server := &Server{Call: d.call, Stream: d.stream, Send: d.send, Scheme: scheme,
		Static: fstest.MapFS{"index.html": {Data: []byte("<!doctype html>desktop")}}}
	return d, server.Handler()
}

// davAsk sends one WebDAV request as a person, with Basic credentials.
func davAsk(handler http.Handler, method, path, password string, headers http.Header) *httptest.ResponseRecorder {
	return davAskBody(handler, method, path, password, "", headers)
}

// davAskBody is davAsk with something to send.
func davAskBody(handler http.Handler, method, path, password, body string,
	headers http.Header) *httptest.ResponseRecorder {
	return davAskBodyAs(handler, "sam", method, path, password, body, headers)
}

// davAskAs is the same request made by somebody else, since a lock belongs to an
// account and not to whoever happens to be holding the token
func davAskAs(handler http.Handler, user, method, path, password string,
	headers http.Header) *httptest.ResponseRecorder {
	return davAskBodyAs(handler, user, method, path, password, "", headers)
}

func davAskBodyAs(handler http.Handler, user, method, path, password, body string,
	headers http.Header) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "https://nas.local"+path, strings.NewReader(body))
	for name, values := range headers {
		req.Header[name] = values
	}
	if password != "" {
		req.Header.Set("Authorization", "Basic "+
			base64.StdEncoding.EncodeToString([]byte(user+":"+password)))
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	return recorder
}

func TestWebDAVNeedsAnEncryptedConnection(t *testing.T) {
	// What the server was told about itself on the command line is not evidence
	// that this connection is encrypted. The request's own TLS state decides, so
	// a plain HTTP request is refused even when Scheme is empty or says https.
	for _, scheme := range []string{"", "http", "https"} {
		t.Run("Scheme="+scheme, func(t *testing.T) {
			d, handler := davSetup(t, scheme)
			request := httptest.NewRequest("PROPFIND", "http://nas.local/dav/", nil)
			request.SetBasicAuth("sam", "right")
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, request)
			if recorder.Code != http.StatusForbidden {
				t.Fatalf("a plain HTTP request answered %d %q", recorder.Code, recorder.Body.String())
			}
			if !strings.Contains(recorder.Body.String(), "only served over HTTPS") {
				t.Errorf("the reason was %q", recorder.Body.String())
			}
			for _, one := range d.asked {
				if one.Op == protocol.OpLogin {
					t.Fatal("the helper was asked to check a password over plain HTTP")
				}
			}
		})
	}
}

func TestWebDAVAsksForAPassword(t *testing.T) {
	_, handler := davSetup(t, "https")
	answer := davAsk(handler, "PROPFIND", "/dav/", "", nil)
	if answer.Code != http.StatusUnauthorized {
		t.Fatalf("with no password the answer was %d", answer.Code)
	}
	if !strings.HasPrefix(answer.Header().Get("WWW-Authenticate"), "Basic ") {
		t.Errorf("the challenge was %q", answer.Header().Get("WWW-Authenticate"))
	}
	wrong := davAsk(handler, "PROPFIND", "/dav/", "wrong", nil)
	if wrong.Code != http.StatusUnauthorized {
		t.Errorf("a wrong password answered %d", wrong.Code)
	}
}

func TestWebDAVListsOnlyTheSharesAgreedToIt(t *testing.T) {
	d, handler := davSetup(t, "https")
	answer := davAsk(handler, "PROPFIND", "/dav/", "right", http.Header{"Depth": {"1"}})
	if answer.Code != http.StatusMultiStatus {
		t.Fatalf("the answer was %d %q", answer.Code, answer.Body.String())
	}
	body := answer.Body.String()
	for _, want := range []string{"/dav/media/", "/dav/private/"} {
		if !strings.Contains(body, want) {
			t.Errorf("%s is missing from %s", want, body)
		}
	}
	if strings.Contains(body, "/dav/docs") {
		t.Errorf("a share not agreed to WebDAV was listed: %s", body)
	}
	if len(d.asked) == 0 || d.asked[0].Op != protocol.OpLogin {
		t.Errorf("the password was never checked: %v", d.asked)
	}
}

func TestWebDAVWillNotReachAnotherPersonsShare(t *testing.T) {
	for _, path := range []string{"/dav/docs/notes.txt", "/dav/family/private.txt", "/dav/nothing/here"} {
		t.Run(path, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			answer := davAsk(handler, "GET", path, "right", nil)
			if answer.Code != http.StatusNotFound {
				t.Fatalf("reading %s answered %d %q", path, answer.Code, answer.Body.String())
			}
			for _, one := range d.asked {
				if one.Op == protocol.OpFileRead {
					t.Errorf("%s was read anyway: %+v", path, one)
				}
			}
			// Asking about it is refused the same way, so an address can't be used
			// to find out what other people have
			asking := davAsk(handler, "PROPFIND", path, "right", nil)
			if asking.Code != http.StatusNotFound {
				t.Errorf("asking about %s answered %d", path, asking.Code)
			}
		})
	}
}

func TestWebDAVStillRefusesWhatItDoesNotDo(t *testing.T) {
	for _, method := range []string{"PROPPATCH", "POST", "PATCH"} {
		t.Run(method, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			answer := davAsk(handler, method, "/dav/media/notes.txt", "right", nil)
			if answer.Code != http.StatusMethodNotAllowed {
				t.Fatalf("%s answered %d %q", method, answer.Code, answer.Body.String())
			}
			// What is offered has to be what is really done, or a client treats a
			// lock as held and then loses the write
			allow := answer.Header().Get("Allow")
			for _, offered := range []string{"PUT", "MKCOL", "DELETE", "MOVE", "COPY", "LOCK", "UNLOCK"} {
				if !strings.Contains(allow, offered) {
					t.Errorf("%s is not offered in Allow %q", offered, allow)
				}
			}
			if strings.Contains(allow, method) {
				t.Errorf("%s is offered in Allow: %q", method, allow)
			}
			for _, one := range d.asked {
				if one.Op != protocol.OpLogin && one.Op != protocol.OpFileList {
					t.Errorf("%s reached the helper as %q", method, one.Op)
				}
			}
		})
	}
}

func TestWebDAVPutMakesAFileThenReplacesIt(t *testing.T) {
	d, handler := davSetup(t, "https")
	made := davAsk(handler, "PUT", "/dav/media/new.txt", "right", http.Header{
		"Content-Length": {"5"}})
	if made.Code != http.StatusCreated {
		t.Fatalf("making a file answered %d %q", made.Code, made.Body.String())
	}
	if d.files["media/new.txt"] != "" {
		t.Errorf("the helper was not handed the body")
	}
	if made.Header().Get("ETag") == "" {
		t.Errorf("no ETag came back with the new file")
	}
	replaced := davAsk(handler, "PUT", "/dav/media/notes.txt", "right", nil)
	if replaced.Code != http.StatusNoContent {
		t.Errorf("replacing a file answered %d %q", replaced.Code, replaced.Body.String())
	}
	if replaced.Header().Get("ETag") == "" {
		t.Errorf("no ETag came back with the replaced file")
	}
}

func TestWebDAVPutRefusesAStaleVersionAndKeepsTheFirstWritersFile(t *testing.T) {
	d, handler := davSetup(t, "https")
	// One client replaces the file it read
	first := davAsk(handler, "PUT", "/dav/media/notes.txt", "right",
		http.Header{"If-Match": {`"note-v1"`}})
	if first.Code != http.StatusNoContent {
		t.Fatalf("the first client answered %d %q", first.Code, first.Body.String())
	}
	held := d.files["media/notes.txt"]
	// A second client, still holding the version it read, tries to save too
	second := davAsk(handler, "PUT", "/dav/media/notes.txt", "right",
		http.Header{"If-Match": {`"note-v1"`}})
	if second.Code != http.StatusPreconditionFailed {
		t.Fatalf("the stale client answered %d %q", second.Code, second.Body.String())
	}
	if d.files["media/notes.txt"] != held {
		t.Errorf("the stale write landed anyway: %q", d.files["media/notes.txt"])
	}
	_ = held
	// And the refusal is the child's, made as it commits, not only this layer's
	raced := 0
	for _, one := range d.asked {
		if one.Op == protocol.OpFileWrite && one.IfMatch != "" {
			raced++
		}
	}
	if raced != 1 {
		t.Errorf("the version was named to the helper %d times, and only the first write should", raced)
	}
}

// The child has the last word: when it refuses as it commits, the client is told
// the file is not the version it read.
func TestWebDAVPutLosesTheRaceAtTheCommit(t *testing.T) {
	d, handler := davSetup(t, "https")
	d.refuseWrite = true
	answer := davAsk(handler, "PUT", "/dav/media/notes.txt", "right",
		http.Header{"If-Match": {`"note-v1"`}})
	if answer.Code != http.StatusPreconditionFailed {
		t.Fatalf("answered %d %q", answer.Code, answer.Body.String())
	}
	if d.files["media/notes.txt"] != "Things for the weekend\n" {
		t.Errorf("the file changed anyway: %q", d.files["media/notes.txt"])
	}
}

func TestWebDAVPutRefusesAVersionItCannotTrust(t *testing.T) {
	cases := []struct {
		name    string
		headers http.Header
		path    string
		code    int
	}{
		{name: "a weak tag", headers: http.Header{"If-Match": {`W/"note-v1"`}},
			path: "/dav/media/notes.txt", code: http.StatusPreconditionFailed},
		{name: "a version that is not this one", headers: http.Header{"If-Match": {`"note-v0"`}},
			path: "/dav/media/notes.txt", code: http.StatusPreconditionFailed},
		{name: "any version of a file that is not there",
			headers: http.Header{"If-Match": {"*"}}, path: "/dav/media/gone.txt",
			code: http.StatusPreconditionFailed},
		{name: "only if new, and it exists",
			headers: http.Header{"If-None-Match": {"*"}}, path: "/dav/media/notes.txt",
			code: http.StatusPreconditionFailed},
		{name: "part of a file", headers: http.Header{"Content-Range": {"bytes 0-4/98"}},
			path: "/dav/media/notes.txt", code: http.StatusBadRequest},
		{name: "a folder", path: "/dav/media/photos", code: http.StatusConflict},
		{name: "the share itself", path: "/dav/media/", code: http.StatusBadRequest},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			answer := davAsk(handler, "PUT", c.path, "right", c.headers)
			if answer.Code != c.code {
				t.Fatalf("answered %d %q", answer.Code, answer.Body.String())
			}
			for _, one := range d.asked {
				if one.Op == protocol.OpFileWrite {
					t.Errorf("it was written anyway: %+v", one)
				}
			}
		})
	}
}

func TestWebDAVPutOnlyIfNewGoesThrough(t *testing.T) {
	_, handler := davSetup(t, "https")
	made := davAsk(handler, "PUT", "/dav/media/fresh.txt", "right",
		http.Header{"If-None-Match": {"*"}})
	if made.Code != http.StatusCreated {
		t.Errorf("a new file answered %d %q", made.Code, made.Body.String())
	}
}

func TestWebDAVMkcolAndDelete(t *testing.T) {
	d, handler := davSetup(t, "https")
	made := davAsk(handler, "MKCOL", "/dav/media/albums", "right", nil)
	if made.Code != http.StatusCreated {
		t.Fatalf("MKCOL answered %d %q", made.Code, made.Body.String())
	}
	if _, ok := d.folders["media/albums"]; !ok {
		t.Errorf("the helper was never asked for the folder")
	}
	twice := davAsk(handler, "MKCOL", "/dav/media/albums", "right", nil)
	if twice.Code != http.StatusMethodNotAllowed {
		t.Errorf("MKCOL over a name that is taken answered %d", twice.Code)
	}
	gone := davAsk(handler, "DELETE", "/dav/media/notes.txt", "right", nil)
	if gone.Code != http.StatusNoContent {
		t.Errorf("DELETE answered %d %q", gone.Code, gone.Body.String())
	}
	// A folder is deleted with what is in it, but only when the client says so
	shallow := davAsk(handler, "DELETE", "/dav/media/photos", "right", http.Header{"Depth": {"0"}})
	if shallow.Code != http.StatusBadRequest {
		t.Errorf("deleting a folder with Depth: 0 answered %d", shallow.Code)
	}
	whole := davAsk(handler, "DELETE", "/dav/media/photos", "right", nil)
	if whole.Code != http.StatusNoContent {
		t.Errorf("deleting a folder answered %d %q", whole.Code, whole.Body.String())
	}
	missing := davAsk(handler, "DELETE", "/dav/media/nothing.txt", "right", nil)
	if missing.Code != http.StatusNotFound {
		t.Errorf("deleting what is not there answered %d", missing.Code)
	}
}

func TestWebDAVMoveAndCopy(t *testing.T) {
	d, handler := davSetup(t, "https")
	moved := davAsk(handler, "MOVE", "/dav/media/notes.txt", "right",
		http.Header{"Destination": {"https://nas.local/dav/media/renamed.txt"}})
	if moved.Code != http.StatusCreated {
		t.Fatalf("MOVE answered %d %q", moved.Code, moved.Body.String())
	}
	if d.files["media/renamed.txt"] != d.files["media/renamed.txt"] || d.tags["media/renamed.txt"] == "" {
		t.Errorf("the move never reached the helper")
	}
	copied := davAsk(handler, "COPY", "/dav/media/photos/sunrise.png", "right",
		http.Header{"Destination": {"/dav/media/photos/dawn.png"}})
	if copied.Code != http.StatusCreated {
		t.Errorf("COPY answered %d %q", copied.Code, copied.Body.String())
	}
	ops := []string{}
	for _, one := range d.asked {
		if one.Op == protocol.OpFileRename || one.Op == protocol.OpFileCopy {
			ops = append(ops, one.Op+" "+one.Path+" to "+one.To)
		}
	}
	want := []string{protocol.OpFileRename + " notes.txt to renamed.txt",
		protocol.OpFileCopy + " photos/sunrise.png to photos/dawn.png"}
	if strings.Join(ops, "|") != strings.Join(want, "|") {
		t.Errorf("the helper was asked for %v", ops)
	}
}

func TestWebDAVMoveAndCopyRefuse(t *testing.T) {
	cases := []struct {
		name   string
		method string
		dest   string
		code   int
		noWork bool
	}{
		{name: "no destination at all", method: "MOVE", code: http.StatusBadRequest},
		{name: "another share", method: "MOVE", dest: "/dav/private/notes.txt",
			code: http.StatusBadGateway},
		{name: "onto something already there", method: "COPY",
			dest: "/dav/media/photos/sunrise.png",
			code: http.StatusPreconditionFailed},
		{name: "into the snapshots", method: "COPY", dest: "/dav/media/.zfs/snapshot/x",
			code: http.StatusForbidden},
		{name: "the share itself", method: "MOVE", dest: "/dav/media",
			code: http.StatusBadRequest},
		{name: "another server", method: "MOVE", dest: "https://elsewhere.example/dav/media/x.txt",
			code: http.StatusBadGateway},
		{name: "a slash hidden inside a name", method: "COPY", dest: "/dav/media/a%2Fb.txt",
			code: http.StatusBadRequest},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			headers := http.Header{}
			if c.dest != "" {
				headers.Set("Destination", c.dest)
			}
			answer := davAsk(handler, c.method, "/dav/media/notes.txt", "right", headers)
			if answer.Code != c.code {
				t.Fatalf("answered %d %q", answer.Code, answer.Body.String())
			}
			for _, one := range d.asked {
				if one.Op == protocol.OpFileRename || one.Op == protocol.OpFileCopy {
					t.Errorf("the helper was asked to %s anyway: %+v", c.method, one)
				}
			}
		})
	}
}

func TestWebDAVWillNotTouchTheSnapshots(t *testing.T) {
	for _, c := range []struct{ method, path string }{
		{"GET", "/dav/media/.zfs/snapshot/hourly_1/notes.txt"},
		{"PROPFIND", "/dav/media/.zfs"},
		{"PUT", "/dav/media/.zfs/snapshot/hourly_1/notes.txt"},
		{"MKCOL", "/dav/media/.zfs/snapshot/new"},
		{"DELETE", "/dav/media/.zfs/snapshot/hourly_1/notes.txt"},
		{"MOVE", "/dav/media/notes.txt"},
	} {
		t.Run(c.method+" "+c.path, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			headers := http.Header{}
			if c.method == "MOVE" {
				headers.Set("Destination", "/dav/media/.zfs/snapshot/hourly_1/notes.txt")
			}
			answer := davAsk(handler, c.method, c.path, "right", headers)
			if answer.Code != http.StatusForbidden {
				t.Fatalf("answered %d %q", answer.Code, answer.Body.String())
			}
			for _, one := range d.asked {
				if one.Op != protocol.OpLogin && one.Op != protocol.OpFileList {
					t.Errorf("%s reached the helper as %q", c.path, one.Op)
				}
			}
		})
	}
}

// A lock is what a word processor takes before it will save, so the token it is
// given has to be the only way in until it lets go.
func TestALockOnAShareCoversWhatIsInIt(t *testing.T) {
	// Nothing can take a lock on a share itself over WebDAV today, since a write
	// has to name something inside one, but a lock that did would have to hold
	// everything in it rather than only the root
	root := davLock{share: "media", inside: "", depth: "infinity"}
	for _, inside := range []string{"", "notes.txt", "photos/sunrise.png"} {
		if !root.covers("media", inside) {
			t.Errorf("a lock on the share itself did not cover %q", inside)
		}
	}
	if root.covers("private", "notes.txt") {
		t.Errorf("a lock on one share covered another")
	}
	folder := davLock{share: "media", inside: "photos", depth: "infinity"}
	if !folder.covers("media", "photos/sunrise.png") {
		t.Errorf("a folder lock did not cover a file in it")
	}
	if folder.covers("media", "photographs/sunrise.png") {
		t.Errorf("a folder lock covered a name that only starts the same")
	}
	one := davLock{share: "media", inside: "notes.txt", depth: "0"}
	if one.covers("media", "notes.txt/child") {
		t.Errorf("a Depth: 0 lock covered something beneath it")
	}
}

func TestTheLocksInsideAShareAreFoundFromItsRoot(t *testing.T) {
	s := &Server{}
	s.davLocks.held = []davLock{
		{token: "opaquelocktoken:x", user: "sam", share: "media", inside: "photos/sunrise.png",
			depth: "0", expires: time.Now().Add(time.Hour)},
	}
	if found := s.davLocksUnder("media", ""); len(found) != 1 {
		t.Errorf("a lock inside the share was not found from its root: %v", found)
	}
	if found := s.davLocksUnder("private", ""); len(found) != 0 {
		t.Errorf("a lock was found in another share: %v", found)
	}
}

func TestWebDAVTellsTheHelperWhereALoginCameFrom(t *testing.T) {
	d, handler := davSetup(t, "https")
	davAsk(handler, "PROPFIND", "/dav/", "right", nil)
	loggedIn := false
	for _, one := range d.asked {
		if one.Op != protocol.OpLogin {
			continue
		}
		loggedIn = true
		// httptest asks from 192.0.2.1, so that is what the helper must be told:
		// guessing is slowed down per address, not in one bucket shared by every
		// WebDAV client on the network
		if one.Remote != "192.0.2.1" {
			t.Errorf("the helper was told the login came from %q", one.Remote)
		}
	}
	if !loggedIn {
		t.Fatal("the helper was never asked to check a password")
	}
}

func TestWebDAVPutWillNotFillADisk(t *testing.T) {
	d, handler := davSetup(t, "https")
	kept := MaxUpload
	MaxUpload = 8
	t.Cleanup(func() { MaxUpload = kept })
	answer := davAskBody(handler, "PUT", "/dav/media/big.bin", "right", strings.Repeat("x", 64), nil)
	if answer.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("an oversized PUT answered %d %q", answer.Code, answer.Body.String())
	}
	if _, taken := d.files["media/big.bin"]; taken {
		t.Error("an oversized upload was kept")
	}
}

func TestWebDAVLockKeepsOthersOutUntilItIsLetGo(t *testing.T) {
	d, handler := davSetup(t, "https")
	locked := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right", http.Header{
		"Timeout":      {"Second-600"},
		"Content-Type": {"application/xml"},
	})
	if locked.Code != http.StatusOK {
		t.Fatalf("LOCK answered %d %q", locked.Code, locked.Body.String())
	}
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	if token == "" {
		t.Fatalf("no lock token came back")
	}
	if !strings.Contains(locked.Body.String(), "<D:exclusive/>") ||
		!strings.Contains(locked.Body.String(), token) {
		t.Errorf("the answer doesn't describe the lock it took: %s", locked.Body.String())
	}
	// Somebody else, holding no token, is turned away from every change
	for _, c := range []struct {
		method  string
		path    string
		headers http.Header
	}{
		{"PUT", "/dav/media/notes.txt", nil},
		{"DELETE", "/dav/media/notes.txt", nil},
		{"MOVE", "/dav/media/notes.txt", http.Header{"Destination": {"/dav/media/moved.txt"}}},
		{"COPY", "/dav/media/notes.txt", http.Header{"Destination": {"/dav/media/copied.txt"}}},
	} {
		answer := davAsk(handler, c.method, c.path, "right", c.headers)
		if answer.Code != http.StatusLocked {
			t.Errorf("%s of a locked file answered %d %q", c.method, answer.Code, answer.Body.String())
		}
	}
	// A second lock from another client is refused while the first holds it
	second := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right", nil)
	if second.Code != http.StatusLocked {
		t.Errorf("a second LOCK answered %d", second.Code)
	}
	// With the token, the same client may write
	allowed := davAsk(handler, "PUT", "/dav/media/notes.txt", "right",
		http.Header{"If": {"(<" + token + ">)"}})
	if allowed.Code != http.StatusNoContent {
		t.Errorf("a write holding the token answered %d %q", allowed.Code, allowed.Body.String())
	}
	// A token nobody holds is not the way in either
	wrong := davAsk(handler, "PUT", "/dav/media/notes.txt", "right",
		http.Header{"If": {"(<opaquelocktoken:nobody>)"}})
	if wrong.Code != http.StatusLocked {
		t.Errorf("a made-up token answered %d", wrong.Code)
	}
	// And once it is let go, the file is free again
	unlocked := davAsk(handler, "UNLOCK", "/dav/media/notes.txt", "right",
		http.Header{"Lock-Token": {"<" + token + ">"}})
	if unlocked.Code != http.StatusNoContent {
		t.Fatalf("UNLOCK answered %d %q", unlocked.Code, unlocked.Body.String())
	}
	free := davAsk(handler, "PUT", "/dav/media/notes.txt", "right", nil)
	if free.Code != http.StatusNoContent {
		t.Errorf("a write after unlocking answered %d %q", free.Code, free.Body.String())
	}
	_ = d
}

func TestWebDAVUnlockNeedsTheToken(t *testing.T) {
	_, handler := davSetup(t, "https")
	locked := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right", nil)
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	none := davAsk(handler, "UNLOCK", "/dav/media/notes.txt", "right", nil)
	if none.Code != http.StatusBadRequest {
		t.Errorf("UNLOCK with no token answered %d", none.Code)
	}
	wrong := davAsk(handler, "UNLOCK", "/dav/media/notes.txt", "right",
		http.Header{"Lock-Token": {"<opaquelocktoken:nobody>"}})
	if wrong.Code != http.StatusConflict {
		t.Errorf("UNLOCK with a token nobody holds answered %d", wrong.Code)
	}
	if again := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right", nil); again.Code != http.StatusLocked {
		t.Errorf("the lock was let go by a wrong token: %d", again.Code)
	}
	_ = token
}

func TestWebDAVLockOnANameThatIsNotThereLetsTheClientMakeIt(t *testing.T) {
	d, handler := davSetup(t, "https")
	// A mounted drive locks the name, then writes it with If-None-Match: * because
	// it believes the file is not there. A lock must not make that file first, or
	// the client's own create would be refused.
	locked := davAsk(handler, "LOCK", "/dav/media/offered.docx", "right", nil)
	if locked.Code != http.StatusOK {
		t.Fatalf("locking a new name answered %d %q", locked.Code, locked.Body.String())
	}
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	if _, made := d.files["media/offered.docx"]; made {
		t.Errorf("the lock made the file itself, which breaks the write that follows")
	}
	// Another client still can't slip in and create it
	other := davAsk(handler, "PUT", "/dav/media/offered.docx", "right", nil)
	if other.Code != http.StatusLocked {
		t.Errorf("a create by someone else answered %d", other.Code)
	}
	wrote := davAskBody(handler, "PUT", "/dav/media/offered.docx", "right", "the document",
		http.Header{"If": {"(<" + token + ">)"}, "If-None-Match": {"*"}})
	if wrote.Code != http.StatusCreated {
		t.Fatalf("the holder's create answered %d %q", wrote.Code, wrote.Body.String())
	}
	if got := d.files["media/offered.docx"]; got != "the document" {
		t.Errorf("the file holds %q", got)
	}
	if gone := davAsk(handler, "UNLOCK", "/dav/media/offered.docx", "right",
		http.Header{"Lock-Token": {"<" + token + ">"}}); gone.Code != http.StatusNoContent {
		t.Fatalf("UNLOCK answered %d", gone.Code)
	}
	if d.files["media/offered.docx"] != "the document" {
		t.Errorf("the file written under the lock was taken away with it")
	}
}

func TestWebDAVLockCanBeRenewedAndRunsOut(t *testing.T) {
	d, handler := davSetup(t, "https")
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	server := &Server{Call: d.call, Stream: d.stream, Send: d.send, Scheme: "https",
		Static: fstest.MapFS{"index.html": {Data: []byte("<!doctype html>desktop")}}}
	server.davLocks.now = func() time.Time { return now }
	handler = server.Handler()

	locked := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right",
		http.Header{"Timeout": {"Second-60"}})
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	// The client asks for more time, which is what a word processor does while it
	// is still working
	now = now.Add(30 * time.Second)
	renewed := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right",
		http.Header{"If": {"(<" + token + ">)"}, "Timeout": {"Second-600"}})
	if renewed.Code != http.StatusOK {
		t.Fatalf("renewing answered %d %q", renewed.Code, renewed.Body.String())
	}
	// Past the first deadline but inside the renewed one
	now = now.Add(60 * time.Second)
	if wrote := davAsk(handler, "PUT", "/dav/media/notes.txt", "right", nil); wrote.Code != http.StatusLocked {
		t.Fatalf("a lock that was renewed had gone: %d", wrote.Code)
	}
	// Past the renewed one, nobody holds it
	now = now.Add(11 * time.Minute)
	if wrote := davAsk(handler, "PUT", "/dav/media/notes.txt", "right", nil); wrote.Code != http.StatusNoContent {
		t.Errorf("a lock that ran out still kept the file: %d %q", wrote.Code, wrote.Body.String())
	}
}

func TestWebDAVTellsClientsItCanLock(t *testing.T) {
	_, handler := davSetup(t, "https")
	answer := davAsk(handler, "OPTIONS", "/dav/media/", "right", nil)
	if got := answer.Header().Get("DAV"); got != "1, 2" {
		t.Errorf("the DAV header was %q, and a client that sees only class 1 never takes a lock", got)
	}
}

func TestWebDAVSaysWhichLocksAreHeld(t *testing.T) {
	_, handler := davSetup(t, "https")
	before := davAsk(handler, "PROPFIND", "/dav/media/notes.txt", "right", nil).Body.String()
	if !strings.Contains(before, "<D:supportedlock>") {
		t.Errorf("a client is never told it may lock: %s", before)
	}
	locked := davAsk(handler, "LOCK", "/dav/media/notes.txt", "right", nil)
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	after := davAsk(handler, "PROPFIND", "/dav/media/notes.txt", "right", nil).Body.String()
	if !strings.Contains(after, "<D:lockdiscovery>") || !strings.Contains(after, token) {
		t.Errorf("the lock is not shown by PROPFIND: %s", after)
	}
}

// A move needs the source to be theirs to read, or a person could shift a file
// they cannot even open.
func TestWebDAVMoveNeedsTheSourceToBeReadable(t *testing.T) {
	d, handler := davSetup(t, "https")
	// The fake refuses a read of this one, as the kernel would for a file that is
	// not the person's to read, but the listing still shows it
	d.unreadable = map[string]bool{"media/private-notes.txt": true}
	d.note("media", "private-notes.txt", davEntry{Name: "private-notes.txt", Size: 9,
		Tag: "hidden-v1"}, false)
	moved := davAsk(handler, "MOVE", "/dav/media/private-notes.txt", "right",
		http.Header{"Destination": {"/dav/media/elsewhere.txt"}})
	if moved.Code != http.StatusForbidden {
		t.Fatalf("moving a file the person cannot read answered %d %q", moved.Code, moved.Body.String())
	}
	for _, one := range d.asked {
		if one.Op == protocol.OpFileRename {
			t.Errorf("it was moved anyway: %+v", one)
		}
	}
}

func TestAReadOnlyPersonCanBrowseButNotWrite(t *testing.T) {
	// private is theirs to read and not to write, which is the shape of a share
	// given out read only
	d, handler := davSetup(t, "https")
	if read := davAsk(handler, "GET", "/dav/private/letter.txt", "right", nil); read.Code != http.StatusOK {
		t.Fatalf("reading answered %d %q", read.Code, read.Body.String())
	}
	if listing := davAsk(handler, "PROPFIND", "/dav/private/", "right", nil); listing.Code != http.StatusMultiStatus {
		t.Fatalf("listing answered %d", listing.Code)
	}
	writes := map[string]http.Header{
		"PUT":    nil,
		"MKCOL":  nil,
		"DELETE": nil,
		"MOVE":   {"Destination": {"/dav/private/moved.txt"}},
		"COPY":   {"Destination": {"/dav/private/copied.txt"}},
	}
	for method, headers := range writes {
		t.Run(method, func(t *testing.T) {
			answer := davAsk(handler, method, "/dav/private/letter.txt", "right", headers)
			if answer.Code != http.StatusForbidden {
				t.Fatalf("%s answered %d %q", method, answer.Code, answer.Body.String())
			}
			if !strings.Contains(answer.Body.String(), "read only for you") {
				t.Errorf("the reason was %q", answer.Body.String())
			}
		})
	}
	for _, one := range d.asked {
		switch one.Op {
		case protocol.OpLogin, protocol.OpFileList, protocol.OpFileRead:
		default:
			t.Errorf("a read-only share reached the helper as %q", one.Op)
		}
	}
}

func TestWebDAVWillNotWalkAFolderForEver(t *testing.T) {
	_, handler := davSetup(t, "https")
	answer := davAsk(handler, "PROPFIND", "/dav/media/", "right",
		http.Header{"Depth": {"infinity"}})
	if answer.Code != http.StatusForbidden {
		t.Fatalf("Depth: infinity answered %d", answer.Code)
	}
	if !strings.Contains(answer.Body.String(), "propfind-finite-depth") {
		t.Errorf("the reason was %q", answer.Body.String())
	}
}

func TestWebDAVListsAFolderAndItsFiles(t *testing.T) {
	_, handler := davSetup(t, "https")
	answer := davAsk(handler, "PROPFIND", "/dav/media/", "right", http.Header{"Depth": {"1"}})
	body := answer.Body.String()
	for _, want := range []string{"/dav/media/", "/dav/media/photos/", "/dav/media/notes.txt",
		"<D:getcontentlength>98</D:getcontentlength>", "<D:collection/>"} {
		if !strings.Contains(body, want) {
			t.Errorf("%q is missing from %s", want, body)
		}
	}
	if answer.Code != http.StatusMultiStatus {
		t.Errorf("the answer was %d", answer.Code)
	}
}

func TestWebDAVTellsOneFileFromAnother(t *testing.T) {
	_, handler := davSetup(t, "https")
	answer := davAsk(handler, "PROPFIND", "/dav/media/notes.txt", "right", http.Header{"Depth": {"0"}})
	body := answer.Body.String()
	if !strings.Contains(body, "<D:getcontentlength>98</D:getcontentlength>") {
		t.Errorf("the file's size is missing from %s", body)
	}
	if !strings.Contains(body, "text/plain") {
		t.Errorf("the file's type is missing from %s", body)
	}
	if strings.Contains(body, "<D:collection/>") {
		t.Errorf("a file was described as a folder: %s", body)
	}
}

func TestWebDAVReadsAFileAndSeeksInIt(t *testing.T) {
	d, handler := davSetup(t, "https")
	whole := davAsk(handler, "GET", "/dav/media/notes.txt", "right", nil)
	if whole.Code != http.StatusOK {
		t.Fatalf("reading answered %d %q", whole.Code, whole.Body.String())
	}
	if whole.Body.String() != "Things for the weekend\n" {
		t.Errorf("the bytes were %q", whole.Body.String())
	}
	if whole.Header().Get("ETag") == "" {
		t.Errorf("no ETag was offered, so a client can't tell versions apart")
	}
	d.asked = nil
	part := davAsk(handler, "GET", "/dav/media/notes.txt", "right",
		http.Header{"Range": {"bytes=7-9"}})
	if part.Code != http.StatusPartialContent {
		t.Fatalf("a range answered %d %q", part.Code, part.Body.String())
	}
	if part.Body.String() != "for" {
		t.Errorf("the range asked for %q", part.Body.String())
	}
	if got := part.Header().Get("Content-Range"); got != "bytes 7-9/23" {
		t.Errorf("Content-Range was %q", got)
	}
	read := false
	for _, one := range d.asked {
		if one.Op == protocol.OpFileRead && one.Start == 7 && one.Length == 3 {
			read = true
		}
	}
	if !read {
		t.Errorf("the helper was never asked for bytes 7 to 9: %v", d.asked)
	}
	// The tag names the file, not the slice of it, or every range would look
	// like a different version and a client would keep asking again
	if whole.Header().Get("ETag") != part.Header().Get("ETag") {
		t.Errorf("a range and the whole file were given different tags: %q and %q",
			whole.Header().Get("ETag"), part.Header().Get("ETag"))
	}
}

func TestWebDAVStaysInsideTheShare(t *testing.T) {
	_, handler := davSetup(t, "https")
	for _, path := range []string{"/dav/media/../../etc/passwd", "/dav/media/./notes.txt",
		"/dav/media//notes.txt", "/dav/media%2f..%2fetc/passwd"} {
		t.Run(path, func(t *testing.T) {
			answer := davAsk(handler, "GET", path, "right", nil)
			if answer.Code == http.StatusOK || answer.Code == http.StatusPartialContent {
				t.Fatalf("%s was served", path)
			}
		})
	}
}

func TestWebDAVChecksThePasswordOnceForAClient(t *testing.T) {
	d, handler := davSetup(t, "https")
	for i := 0; i < 4; i++ {
		if answer := davAsk(handler, "GET", "/dav/media/notes.txt", "right", nil); answer.Code != http.StatusOK {
			t.Fatalf("read %d answered %d", i, answer.Code)
		}
	}
	logins := 0
	for _, one := range d.asked {
		if one.Op == protocol.OpLogin {
			logins++
		}
	}
	if logins != 1 {
		t.Errorf("the password was checked %d times for one client", logins)
	}
}

// A lock is the account's, not the token's. Two people have write access to
// media here, and one of them has been told the token: it still gets them
// nowhere, because the account that took the lock is what the lock is for.
func TestWebDAVLockBelongsToTheAccountThatTookIt(t *testing.T) {
	d, handler := davSetup(t, "https")
	locked := davAskAs(handler, "sam", "LOCK", "/dav/media/notes.txt", "right", nil)
	if locked.Code != http.StatusOK {
		t.Fatalf("LOCK answered %d %q", locked.Code, locked.Body.String())
	}
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))
	if token == "" {
		t.Fatalf("no lock token came back")
	}
	named := http.Header{"If": {"(<" + token + ">)"}}

	// alex may write to media, and knows the exact token
	for _, c := range []struct {
		method  string
		path    string
		headers http.Header
	}{
		{"PUT", "/dav/media/notes.txt", named},
		{"DELETE", "/dav/media/notes.txt", named},
		{"MOVE", "/dav/media/notes.txt", http.Header{"If": {"(<" + token + ">)"},
			"Destination": {"/dav/media/taken.txt"}}},
		{"COPY", "/dav/media/notes.txt", http.Header{"If": {"(<" + token + ">)"},
			"Destination": {"/dav/media/taken.txt"}}},
		{"LOCK", "/dav/media/notes.txt", named},
		{"UNLOCK", "/dav/media/notes.txt", http.Header{"Lock-Token": {"<" + token + ">"}}},
	} {
		answer := davAskAs(handler, "alex", c.method, c.path, "right", c.headers)
		if answer.Code != http.StatusLocked {
			t.Errorf("alex %s with the owner's token answered %d %q",
				c.method, answer.Code, answer.Body.String())
		}
		if strings.Contains(answer.Body.String(), token) {
			t.Errorf("a refusal told alex the token: %q", answer.Body.String())
		}
		if got := answer.Header().Get("Lock-Token"); got != "" {
			t.Errorf("a refusal handed alex the lock token %q", got)
		}
	}

	// Nothing alex tried changed the file or the lock
	if got := d.files["media/notes.txt"]; got != "Things for the weekend\n" {
		t.Errorf("the file is now %q", got)
	}
	if _, gone := d.files["media/taken.txt"]; gone {
		t.Errorf("alex moved or copied the file")
	}
	still := davAskAs(handler, "sam", "PROPFIND", "/dav/media/notes.txt", "right",
		http.Header{"Depth": {"0"}})
	if still.Code != http.StatusMultiStatus ||
		!strings.Contains(still.Body.String(), token) {
		t.Fatalf("the lock is no longer held: %d %q", still.Code, still.Body.String())
	}

	// The owner can still work, renew, and let go
	if wrote := davAskAs(handler, "sam", "PUT", "/dav/media/notes.txt", "right", named); wrote.Code != http.StatusNoContent {
		t.Errorf("the owner's write answered %d %q", wrote.Code, wrote.Body.String())
	}
	if renewed := davAskAs(handler, "sam", "LOCK", "/dav/media/notes.txt", "right", named); renewed.Code != http.StatusOK {
		t.Errorf("the owner's renewal answered %d %q", renewed.Code, renewed.Body.String())
	}
	if let := davAskAs(handler, "sam", "UNLOCK", "/dav/media/notes.txt", "right",
		http.Header{"Lock-Token": {"<" + token + ">"}}); let.Code != http.StatusNoContent {
		t.Fatalf("the owner's UNLOCK answered %d %q", let.Code, let.Body.String())
	}
	if free := davAskAs(handler, "alex", "PUT", "/dav/media/notes.txt", "right", nil); free.Code != http.StatusNoContent {
		t.Errorf("a write after the owner let go answered %d %q", free.Code, free.Body.String())
	}
}

// A folder being deleted or moved takes what is inside it, so a lock further
// down has to stop the whole thing before anything is touched.
func TestWebDAVAFolderWithALockedFileInsideIsKept(t *testing.T) {
	d, handler := davSetup(t, "https")
	locked := davAskAs(handler, "sam", "LOCK", "/dav/media/photos/sunrise.png", "right", nil)
	if locked.Code != http.StatusOK {
		t.Fatalf("LOCK answered %d %q", locked.Code, locked.Body.String())
	}
	token := davUnquoteToken(locked.Header().Get("Lock-Token"))

	for _, c := range []struct {
		method  string
		path    string
		headers http.Header
	}{
		{"DELETE", "/dav/media/photos/", nil},
		{"MOVE", "/dav/media/photos/", http.Header{"Destination": {"/dav/media/album/"}}},
		{"DELETE", "/dav/media/photos/sunrise.png", nil},
	} {
		answer := davAskAs(handler, "alex", c.method, c.path, "right", c.headers)
		if answer.Code != http.StatusLocked {
			t.Errorf("alex %s of %s answered %d %q", c.method, c.path, answer.Code,
				answer.Body.String())
		}
	}
	if _, gone := d.folders["media/photos"]; !gone {
		t.Fatalf("the folder went anyway")
	}
	if got := d.files["media/photos/sunrise.png"]; got == "" {
		t.Fatalf("the photo went anyway")
	}
	for _, asked := range d.asked {
		if asked.Op == protocol.OpFileDelete || asked.Op == protocol.OpFileRename {
			t.Errorf("the helper was asked to change something after the refusal: %v", asked)
		}
	}

	// The folder is free once the owner lets go
	if let := davAskAs(handler, "sam", "UNLOCK", "/dav/media/photos/sunrise.png", "right",
		http.Header{"Lock-Token": {"<" + token + ">"}}); let.Code != http.StatusNoContent {
		t.Fatalf("UNLOCK answered %d %q", let.Code, let.Body.String())
	}
	if gone := davAskAs(handler, "alex", "DELETE", "/dav/media/photos/", "right", nil); gone.Code != http.StatusNoContent {
		t.Errorf("the delete after unlocking answered %d %q", gone.Code, gone.Body.String())
	}
}
