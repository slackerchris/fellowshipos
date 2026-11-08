package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"fellowship-installer/internal/disks"
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
)

type fakeMachine struct {
	installed chan engine.Settings
}

func setup() (*Server, *fakeMachine) {
	f := &fakeMachine{installed: make(chan engine.Settings, 1)}
	s := New(Machine{
		Installed: 3 * layout.GiB, Memory: 8 * layout.GiB,
		Disks: func() ([]disks.Disk, error) {
			return []disks.Disk{
				{Path: "/dev/sda", Size: 500 * layout.GiB, Model: "SSD"},
				{Path: "/dev/sdb", Size: 16 * layout.GiB, Model: "USB", Refused: "This is the USB stick"},
			}, nil
		},
		Timezones: func() ([]string, error) { return []string{"UTC"}, nil },
		Addresses: func() []string { return []string{"192.168.1.20"} },
		Install: func(s engine.Settings, step, log func(string)) error {
			step(engine.Steps[0])
			f.installed <- s
			return nil
		},
		Reboot: func() error { return nil },
	}, fstest.MapFS{"index.html": {Data: []byte("installer")}})
	return s, f
}

type req struct {
	method, path, body, remote, cookie string
	noHeader                           bool
	origin                             string
}

func send(s *Server, r req) *httptest.ResponseRecorder {
	request := httptest.NewRequest(r.method, "http://nas.local"+r.path, strings.NewReader(r.body))
	request.RemoteAddr = r.remote
	if r.method == "POST" && !r.noHeader {
		request.Header.Set(header, "1")
	}
	if r.origin != "" {
		request.Header.Set("Origin", r.origin)
	}
	if r.cookie != "" {
		request.AddCookie(&http.Cookie{Name: cookieName, Value: r.cookie})
	}
	recorder := httptest.NewRecorder()
	s.Handler().ServeHTTP(recorder, request)
	return recorder
}

const (
	screen = "127.0.0.1:5000"
	laptop = "192.168.1.30:5000"
)

func TestScreenIsAllowedAndSeesTheCode(t *testing.T) {
	s, _ := setup()
	var hello map[string]any
	json.Unmarshal(send(s, req{method: "GET", path: "/api/hello", remote: screen}).Body.Bytes(), &hello)
	if hello["allowed"] != true || hello["code"] != s.code {
		t.Fatalf("screen: %v", hello)
	}
	var remote map[string]any
	json.Unmarshal(send(s, req{method: "GET", path: "/api/hello", remote: laptop}).Body.Bytes(), &remote)
	if remote["allowed"] != false || remote["code"] != nil || remote["addresses"] != nil {
		t.Fatalf("laptop saw %v", remote)
	}
	if code := send(s, req{method: "GET", path: "/api/machine", remote: laptop}).Code; code != 401 {
		t.Fatalf("unpaired laptop: %d", code)
	}
}

func TestPairing(t *testing.T) {
	s, _ := setup()
	if code := send(s, req{method: "POST", path: "/api/pair", body: `{"code":"000000x"}`, remote: laptop}).Code; code != 401 {
		t.Fatalf("wrong code: %d", code)
	}
	response := send(s, req{method: "POST", path: "/api/pair", body: `{"code":"` + s.code + `"}`, remote: laptop})
	cookie := response.Result().Cookies()[0]
	if response.Code != 200 || !cookie.HttpOnly {
		t.Fatalf("pair: %d %+v", response.Code, cookie)
	}
	if code := send(s, req{method: "GET", path: "/api/machine", remote: laptop, cookie: cookie.Value}).Code; code != 200 {
		t.Fatalf("paired laptop: %d", code)
	}
}

func TestGuessingChangesTheCode(t *testing.T) {
	s, _ := setup()
	first := s.code
	for i := 0; i < maxAttempts; i++ {
		send(s, req{method: "POST", path: "/api/pair", body: `{"code":"nope"}`, remote: laptop})
	}
	if s.code == first {
		t.Fatal("the code survived ten wrong guesses")
	}
}

func TestOtherSitesAreRefused(t *testing.T) {
	s, _ := setup()
	for _, r := range []req{
		{method: "POST", path: "/api/install", body: `{}`, remote: screen, noHeader: true},
		{method: "POST", path: "/api/install", body: `{}`, remote: screen, origin: "http://evil.example"},
		{method: "POST", path: "/api/pair", body: `{"code":"1"}`, remote: laptop, noHeader: true},
	} {
		if code := send(s, r).Code; code != 403 {
			t.Errorf("%s %s: %d", r.method, r.path, code)
		}
	}
}

const good = `"hostname":"vault","timezone":"UTC","user":"chris","full_name":"Chris","password":"correct horse","system":34359738368`

func TestInstallNeedsAnOfferedDiskAndTheWords(t *testing.T) {
	s, f := setup()
	for body, want := range map[string]string{
		`{"disk":"/dev/sdb",` + good + `,"confirm":"erase sdb"}`:                "can't be used",
		`{"disk":"/dev/sdc",` + good + `,"confirm":"erase sdc"}`:                "can't be used",
		`{"disk":"/dev/sda",` + good + `,"confirm":"yes"}`:                      `erase sda`,
		`{"disk":"/dev/sda",` + good + `,"password":"x","confirm":"erase sda"}`: "8 characters",
	} {
		response := send(s, req{method: "POST", path: "/api/install", body: body, remote: screen})
		if response.Code != 422 || !strings.Contains(response.Body.String(), want) {
			t.Errorf("%s: %d %s", body, response.Code, response.Body)
		}
	}
	response := send(s, req{method: "POST", path: "/api/install", remote: screen,
		body: `{"disk":"/dev/sda",` + good + `,"role":"docker","confirm":"erase sda"}`})
	if response.Code != 202 {
		t.Fatalf("install: %d %s", response.Code, response.Body)
	}
	select {
	case settings := <-f.installed:
		if settings.Disk != "/dev/sda" || settings.Role != "nas" {
			t.Fatalf("installed %+v", settings)
		}
	case <-time.After(time.Second):
		t.Fatal("the install never started")
	}
	time.Sleep(20 * time.Millisecond)
	var state progress
	json.Unmarshal(send(s, req{method: "GET", path: "/api/progress", remote: screen}).Body.Bytes(), &state)
	if state.Phase != "done" {
		t.Fatalf("progress %+v", state)
	}
	if code := send(s, req{method: "POST", path: "/api/install", remote: screen,
		body: `{"disk":"/dev/sda",` + good + `,"confirm":"erase sda"}`}).Code; code != 409 {
		t.Fatalf("second install: %d", code)
	}
}

func TestLayoutFollowsTheSlider(t *testing.T) {
	s, _ := setup()
	var chosen layout.Layout
	response := send(s, req{method: "POST", path: "/api/layout", remote: screen, body: `{"size":536870912000,"system":107374182400}`})
	json.Unmarshal(response.Body.Bytes(), &chosen)
	if chosen.System != 100*layout.GiB || chosen.Data == 0 {
		t.Fatalf("%d %+v", response.Code, chosen)
	}
}
