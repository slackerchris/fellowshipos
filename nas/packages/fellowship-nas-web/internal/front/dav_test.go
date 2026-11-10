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
	"strings"
	"testing"
	"testing/fstest"

	"fellowship-nas-web/internal/protocol"
)

// davHelper is a helper that knows two people's shares and one folder of files.
type davHelper struct {
	asked   []protocol.Request
	shares  []davShare
	folders map[string][]davEntry // "share/inside", with "" for the share's own folder
	files   map[string]string     // "share/inside" → bytes
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
			"media": {{Name: "photos", Dir: true}, {Name: "notes.txt", Size: 98,
				Modified: "2026-11-10T11:33:39Z"}},
			"media/photos": {{Name: "sunrise.png", Size: 26000,
				Modified: "2026-11-09T08:00:00Z"}},
			"private": {{Name: "letter.txt", Size: 12, Modified: "2026-11-10T09:00:00Z"}},
		},
		files: map[string]string{"media/notes.txt": "Things for the weekend\n",
			"media/photos/sunrise.png": "not really a picture, but enough bytes",
			"private/letter.txt":       "hello there\n"},
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
		return protocol.Response{Token: "secret", Session: &protocol.Session{User: request.User,
			CSRF: "csrf-token"}}, nil
	case protocol.OpFileList:
		if request.Session != "secret" {
			return protocol.Response{Code: protocol.CodeUnauthenticated, Error: "signed out"}, nil
		}
		if request.Share == "" {
			out, _ := json.Marshal(d.shares)
			return protocol.Response{Output: string(out)}, nil
		}
		entries, ok := d.folders[request.Share+"/"+request.Path]
		if !ok && request.Path == "" {
			entries, ok = d.folders[request.Share]
		}
		if !ok {
			return protocol.Response{Code: protocol.CodeBadRequest, Error: "that folder can't be opened"}, nil
		}
		out, _ := json.Marshal(map[string]any{"entries": entries})
		return protocol.Response{Output: string(out)}, nil
	}
	return protocol.Response{}, nil
}

// stream answers a read of a file with its bytes.
func (d *davHelper) stream(request protocol.Request) (protocol.Response, io.ReadCloser, error) {
	d.asked = append(d.asked, request)
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
	return protocol.Response{Size: size, Length: length, Modified: 1762770819},
		io.NopCloser(strings.NewReader(held[start : start+length])), nil
}

func davSetup(t *testing.T, scheme string) (*davHelper, http.Handler) {
	t.Helper()
	d := newDavHelper()
	server := &Server{Call: d.call, Stream: d.stream, Scheme: scheme,
		Static: fstest.MapFS{"index.html": {Data: []byte("<!doctype html>desktop")}}}
	return d, server.Handler()
}

// davAsk sends one WebDAV request as a person, with Basic credentials.
func davAsk(handler http.Handler, method, path, password string, headers http.Header) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "https://nas.local"+path, nil)
	for name, values := range headers {
		req.Header[name] = values
	}
	if password != "" {
		req.Header.Set("Authorization", "Basic "+
			base64.StdEncoding.EncodeToString([]byte("sam:"+password)))
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	return recorder
}

func TestWebDAVNeedsAnEncryptedConnection(t *testing.T) {
	d, handler := davSetup(t, "http")
	answer := davAsk(handler, "PROPFIND", "/dav/", "right", nil)
	if answer.Code != http.StatusForbidden {
		t.Fatalf("over plain HTTP the answer was %d %q", answer.Code, answer.Body.String())
	}
	if !strings.Contains(answer.Body.String(), "only served over HTTPS") {
		t.Errorf("the reason was %q", answer.Body.String())
	}
	if len(d.asked) != 0 {
		t.Errorf("a password was checked over plain HTTP: %v", d.asked)
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

func TestWebDAVRefusesEveryWrite(t *testing.T) {
	for _, method := range []string{"PUT", "DELETE", "MKCOL", "MOVE", "COPY", "PROPPATCH", "LOCK",
		"UNLOCK", "POST"} {
		t.Run(method, func(t *testing.T) {
			d, handler := davSetup(t, "https")
			answer := davAsk(handler, method, "/dav/media/notes.txt", "right", nil)
			if answer.Code != http.StatusMethodNotAllowed {
				t.Fatalf("%s answered %d %q", method, answer.Code, answer.Body.String())
			}
			if allow := answer.Header().Get("Allow"); strings.Contains(allow, "PUT") {
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
