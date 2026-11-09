package front

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"fellowship-nas-web/internal/protocol"
)

type fakeHelper struct {
	requests []protocol.Request
	admin    bool
}

func (f *fakeHelper) call(request protocol.Request) (protocol.Response, error) {
	f.requests = append(f.requests, request)
	session := &protocol.Session{ID: "abc", User: "sam", Admin: f.admin, CSRF: "csrf-token"}
	switch request.Op {
	case protocol.OpLogin:
		if request.Password != "right" {
			return protocol.Response{Code: protocol.CodeUnauthenticated, Error: "wrong"}, nil
		}
		return protocol.Response{Token: "secret", Session: session}, nil
	case protocol.OpCheck:
		if request.Session != "secret" {
			return protocol.Response{Code: protocol.CodeUnauthenticated, Error: "signed out"}, nil
		}
		return protocol.Response{Session: session}, nil
	case protocol.OpFnas:
		return protocol.Response{Output: strings.Join(request.Args, " ")}, nil
	}
	return protocol.Response{}, nil
}

func setup(t *testing.T, admin bool) (*fakeHelper, http.Handler) {
	f := &fakeHelper{admin: admin}
	passwd := filepath.Join(t.TempDir(), "passwd")
	os.WriteFile(passwd, []byte("root:x:0:0::/root:/bin/bash\nsam:x:1000:1000::/home/sam:/bin/bash\n"+
		"nas-share:x:3000:3000::/nonexistent:/usr/sbin/nologin\nkim:x:1001:1001::/home/kim:/bin/zsh\n"), 0o644)
	server := &Server{Call: f.call, Passwd: passwd, Static: fstest.MapFS{
		"index.html":    {Data: []byte("<!doctype html>desktop")},
		"app.js":        {Data: []byte("console.log(1)")},
		"icons/app.svg": {Data: []byte("<svg/>")},
	}}
	return f, server.Handler()
}

type request struct {
	method, path, body string
	origin, token      string
	cookie             bool
}

func send(handler http.Handler, r request) *httptest.ResponseRecorder {
	req := httptest.NewRequest(r.method, "https://nas.local"+r.path, strings.NewReader(r.body))
	if r.body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if r.origin != "" {
		req.Header.Set("Origin", r.origin)
	}
	if r.token != "" {
		req.Header.Set(TokenHeader, r.token)
	}
	if r.cookie {
		req.AddCookie(&http.Cookie{Name: CookieName, Value: "secret"})
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	return recorder
}

const origin = "https://nas.local"

func TestLoginSetsAStrictCookie(t *testing.T) {
	_, handler := setup(t, true)
	response := send(handler, request{method: "POST", path: "/api/login", origin: origin,
		body: `{"user":"sam","password":"right"}`})
	if response.Code != 200 {
		t.Fatalf("status %d: %s", response.Code, response.Body)
	}
	cookie := response.Result().Cookies()[0]
	if cookie.Name != CookieName || cookie.Value != "secret" || !cookie.Secure || !cookie.HttpOnly ||
		cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" {
		t.Fatalf("cookie %+v", cookie)
	}
	var session protocol.Session
	json.Unmarshal(response.Body.Bytes(), &session)
	if session.CSRF != "csrf-token" {
		t.Fatalf("session %+v", session)
	}
	if wrong := send(handler, request{method: "POST", path: "/api/login", origin: origin,
		body: `{"user":"sam","password":"nope"}`}); wrong.Code != 401 || len(wrong.Result().Cookies()) != 0 {
		t.Fatalf("wrong password: %d", wrong.Code)
	}
}

func TestOtherSitesAreRefused(t *testing.T) {
	f, handler := setup(t, true)
	for _, r := range []request{
		{method: "POST", path: "/api/login", body: `{"user":"sam","password":"right"}`},
		{method: "POST", path: "/api/login", origin: "https://evil.example", body: `{"user":"sam","password":"right"}`},
		{method: "POST", path: "/api/fnas", origin: "https://evil.example", token: "csrf-token", cookie: true, body: `{"args":["pool","list"]}`},
		{method: "POST", path: "/api/fnas", origin: "http://nas.local", token: "csrf-token", cookie: true, body: `{"args":["pool","list"]}`},
		{method: "POST", path: "/api/fnas", origin: origin, cookie: true, body: `{"args":["pool","list"]}`},
		{method: "POST", path: "/api/fnas", origin: origin, token: "wrong", cookie: true, body: `{"args":["pool","list"]}`},
		{method: "POST", path: "/api/logout", origin: origin, cookie: true},
	} {
		if response := send(handler, r); response.Code != 403 {
			t.Errorf("%s %s origin=%q token=%q: %d", r.method, r.path, r.origin, r.token, response.Code)
		}
	}
	for _, request := range f.requests {
		if request.Op == protocol.OpFnas || request.Op == protocol.OpLogin || request.Op == protocol.OpLogout {
			t.Errorf("reached the helper: %+v", request)
		}
	}
}

func TestFnasNeedsASession(t *testing.T) {
	_, handler := setup(t, true)
	if response := send(handler, request{method: "POST", path: "/api/fnas", origin: origin, token: "csrf-token",
		body: `{"args":["pool","list"]}`}); response.Code != 401 {
		t.Fatalf("no cookie: %d", response.Code)
	}
	response := send(handler, request{method: "POST", path: "/api/fnas", origin: origin, token: "csrf-token",
		cookie: true, body: `{"args":["--json","pool","list"]}`})
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"output":"--json pool list"`) {
		t.Fatalf("%d %s", response.Code, response.Body)
	}
	if get := send(handler, request{method: "GET", path: "/api/fnas", cookie: true}); get.Code != 404 {
		t.Fatalf("GET fnas: %d", get.Code)
	}
}

func TestBackgroundPollsDontTouch(t *testing.T) {
	f, handler := setup(t, true)
	req := httptest.NewRequest("GET", "https://nas.local/api/session", nil)
	req.AddCookie(&http.Cookie{Name: CookieName, Value: "secret"})
	req.Header.Set(BackgroundHeader, "1")
	handler.ServeHTTP(httptest.NewRecorder(), req)
	send(handler, request{method: "GET", path: "/api/session", cookie: true})
	if f.requests[0].Touch || !f.requests[1].Touch {
		t.Fatalf("touch flags: %+v", f.requests)
	}
}

func TestUsersListsPeopleForAdmins(t *testing.T) {
	_, handler := setup(t, true)
	response := send(handler, request{method: "GET", path: "/api/users", cookie: true})
	if strings.TrimSpace(response.Body.String()) != `["sam","kim"]` {
		t.Fatalf("users: %s", response.Body)
	}
	_, handler = setup(t, false)
	if response := send(handler, request{method: "GET", path: "/api/users", cookie: true}); response.Code != 403 {
		t.Fatalf("non-admin users: %d", response.Code)
	}
}

func TestStatic(t *testing.T) {
	_, handler := setup(t, false)
	index := send(handler, request{method: "GET", path: "/"})
	if index.Code != 200 || !strings.Contains(index.Body.String(), "desktop") {
		t.Fatalf("index: %d", index.Code)
	}
	for _, header := range []string{"Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"} {
		if index.Header().Get(header) == "" {
			t.Errorf("missing %s", header)
		}
	}
	for _, path := range []string{"/icons", "/icons/", "/missing.js", "/../etc/passwd"} {
		// The mux redirects /../x to /x rather than serving it
		if response := send(handler, request{method: "GET", path: path}); response.Code != 404 && response.Code != 301 {
			t.Errorf("%s: %d", path, response.Code)
		}
	}
	if response := send(handler, request{method: "GET", path: "/api/nothing"}); response.Code != 404 {
		t.Errorf("unknown API: %d", response.Code)
	}
}

func TestUpdatedFilesReachTheBrowser(t *testing.T) {
	// Every packaged file has the same date, so only the contents can say a file changed
	files := fstest.MapFS{"app.js": {Data: []byte("old"), ModTime: time.Unix(1, 0)}}
	handler := (&Server{Static: files}).Handler()
	first := send(handler, request{method: "GET", path: "/app.js"})
	tag := first.Header().Get("ETag")
	if tag == "" || first.Header().Get("Last-Modified") != "" {
		t.Fatalf("want an ETag and no Last-Modified, got %v", first.Header())
	}
	again := httptest.NewRequest("GET", "https://nas.local/app.js", nil)
	again.Header.Set("If-None-Match", tag)
	unchanged := httptest.NewRecorder()
	handler.ServeHTTP(unchanged, again)
	if unchanged.Code != http.StatusNotModified {
		t.Fatalf("an unchanged file should be 304, got %d", unchanged.Code)
	}
	files["app.js"] = &fstest.MapFile{Data: []byte("new"), ModTime: time.Unix(1, 0)}
	updated := httptest.NewRecorder()
	handler.ServeHTTP(updated, again)
	if updated.Code != http.StatusOK || updated.Body.String() != "new" {
		t.Fatalf("an updated file should be sent again, got %d %q", updated.Code, updated.Body.String())
	}
}

func TestJobEventsStreamChangesUntilSignedOut(t *testing.T) {
	outputs := []string{`[{"id": "a", "progress": 0.5}]`, `[{"id": "a", "progress": 0.5}]`,
		`[{"id": "a", "progress": 1}]`}
	var asked []protocol.Request
	call := func(request protocol.Request) (protocol.Response, error) {
		switch request.Op {
		case protocol.OpCheck:
			return protocol.Response{Session: &protocol.Session{User: "sam", Admin: true}}, nil
		case protocol.OpFnas:
			asked = append(asked, request)
			if len(asked) > len(outputs) {
				return protocol.Response{Code: protocol.CodeUnauthenticated}, nil
			}
			return protocol.Response{Output: outputs[len(asked)-1]}, nil
		}
		return protocol.Response{}, nil
	}
	server := &Server{Call: call, JobPoll: time.Millisecond}
	response := send(server.Handler(), request{method: "GET", path: "/api/jobs/events", cookie: true})
	body := response.Body.String()
	if response.Header().Get("Content-Type") != "text/event-stream" {
		t.Fatalf("content type %q", response.Header().Get("Content-Type"))
	}
	if strings.Count(body, "event: jobs\n") != 2 || !strings.Contains(body, `data: [{"id":"a","progress":1}]`) ||
		!strings.HasSuffix(body, "event: ended\ndata: {}\n\n") {
		t.Fatalf("stream:\n%s", body)
	}
	for _, r := range asked {
		if r.Touch || strings.Join(r.Args, " ") != "--json job list" {
			t.Fatalf("asked %+v", r)
		}
	}
}
