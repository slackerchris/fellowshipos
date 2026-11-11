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
	"fellowship-installer/internal/edition"
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
)

type fakeMachine struct {
	installed chan engine.Settings
}

func setup() (*Server, *fakeMachine) {
	return setupEdition(edition.NAS)
}

func setupEdition(which edition.Edition) (*Server, *fakeMachine) {
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
	}, fstest.MapFS{"index.html": {Data: []byte("installer")}}, which)
	return s, f
}

type req struct {
	method, path, body, remote, cookie string
	noHeader                           bool
	origin                             string
	// secure sends the request over HTTPS, which is the only way in for a
	// browser that isn't the machine's own screen
	secure bool
}

func send(s *Server, r req) *httptest.ResponseRecorder {
	target := "http://nas.local"
	if r.secure {
		target = "https://nas.local"
	}
	request := httptest.NewRequest(r.method, target+r.path, strings.NewReader(r.body))
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
	json.Unmarshal(send(s, req{method: "GET", path: "/api/hello", remote: laptop, secure: true}).Body.Bytes(), &remote)
	if remote["allowed"] != false || remote["code"] != nil || remote["addresses"] != nil {
		t.Fatalf("laptop saw %v", remote)
	}
	// The pairing page names the image, so it needs the title before pairing
	if remote["title"] != s.edition.Title() || remote["edition"] != s.edition.String() {
		t.Fatalf("laptop wasn't told which image this is: %v", remote)
	}
	if code := send(s, req{method: "GET", path: "/api/machine", remote: laptop, secure: true}).Code; code != 401 {
		t.Fatalf("unpaired laptop: %d", code)
	}
}

func TestPairing(t *testing.T) {
	s, _ := setup()
	if code := send(s, req{method: "POST", path: "/api/pair", body: `{"code":"000000x"}`, remote: laptop,
		secure: true}).Code; code != 401 {
		t.Fatalf("wrong code: %d", code)
	}
	response := send(s, req{method: "POST", path: "/api/pair", body: `{"code":"` + s.code + `"}`,
		remote: laptop, secure: true})
	cookie := response.Result().Cookies()[0]
	if response.Code != 200 || !cookie.HttpOnly || !cookie.Secure {
		t.Fatalf("pair: %d %+v", response.Code, cookie)
	}
	if code := send(s, req{method: "GET", path: "/api/machine", remote: laptop, cookie: cookie.Value,
		secure: true}).Code; code != 200 {
		t.Fatalf("paired laptop: %d", code)
	}
}

func TestARemoteBrowserOnlyGetsInOverHTTPS(t *testing.T) {
	s, _ := setup()
	// The pairing code and the cookie it buys authorise erasing a disk, so
	// neither crosses the network in the clear
	if code := send(s, req{method: "GET", path: "/api/machine", remote: laptop}).Code; code != 403 {
		t.Errorf("an unpaired remote request over plain HTTP answered %d", code)
	}
	if code := send(s, req{method: "POST", path: "/api/pair",
		body: `{"code":"` + s.code + `"}`, remote: laptop}).Code; code != 403 {
		t.Errorf("pairing over plain HTTP answered %d", code)
	}
	if len(s.paired) != 0 {
		t.Error("a plain HTTP request was paired")
	}
	// The same request over HTTPS is served, so it is the transport that was
	// refused and not installing from another computer
	response := send(s, req{method: "POST", path: "/api/pair",
		body: `{"code":"` + s.code + `"}`, remote: laptop, secure: true})
	cookies := response.Result().Cookies()
	if response.Code != 200 || len(cookies) != 1 {
		t.Fatalf("pairing over HTTPS answered %d with %d cookies", response.Code, len(cookies))
	}
	if cookie := cookies[0]; !cookie.Secure || !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode {
		t.Errorf("the cookie for another computer is %+v", cookie)
	}
}

func TestGuessingChangesTheCode(t *testing.T) {
	s, _ := setup()
	first := s.code
	for i := 0; i < maxAttempts; i++ {
		send(s, req{method: "POST", path: "/api/pair", body: `{"code":"nope"}`, remote: laptop, secure: true})
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
		{method: "POST", path: "/api/pair", body: `{"code":"1"}`, remote: laptop, noHeader: true, secure: true},
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
	// The finished page says where to find the machine, by the name chosen
	if state.Phase != "done" || state.Hostname == "" {
		t.Fatalf("progress %+v", state)
	}
	if code := send(s, req{method: "POST", path: "/api/install", remote: screen,
		body: `{"disk":"/dev/sda",` + good + `,"confirm":"erase sda"}`}).Code; code != 409 {
		t.Fatalf("second install: %d", code)
	}
}

func TestThePageKnowsWhichImageItIs(t *testing.T) {
	for _, c := range []struct {
		which edition.Edition
		title string
		whole bool
	}{
		{edition.NAS, "FellowshipOS NAS", false},
		{edition.Multi, "FellowshipOS Multi-purpose", true},
	} {
		s, _ := setupEdition(c.which)
		var info map[string]any
		json.Unmarshal(send(s, req{method: "GET", path: "/api/machine", remote: screen}).Body.Bytes(), &info)
		if info["edition"] != c.which.String() || info["title"] != c.title || info["whole_disk"] != c.whole {
			t.Errorf("%s told the page %v", c.which, info)
		}
	}
}

func TestTheMultiPurposeImageGivesTheSystemTheWholeDisk(t *testing.T) {
	s, _ := setupEdition(edition.Multi)
	response := send(s, req{method: "POST", path: "/api/layout", remote: screen,
		body: `{"size":536870912000,"system":107374182400}`})
	var chosen layout.Layout
	json.Unmarshal(response.Body.Bytes(), &chosen)
	// On a 500 GB disk the NAS image keeps about 100 GB for the system and the
	// rest for a pool; this image keeps no pool and gives the system everything
	if chosen.Data != 0 || chosen.System <= 100*layout.GiB {
		t.Fatalf("the multi-purpose layout was %+v", chosen)
	}
}

func TestTheMultiPurposeImageRecordsNoRole(t *testing.T) {
	s, f := setupEdition(edition.Multi)
	response := send(s, req{method: "POST", path: "/api/install", remote: screen,
		body: `{"disk":"/dev/sda",` + good + `,"confirm":"erase sda"}`})
	if response.Code != 202 {
		t.Fatalf("install: %d %s", response.Code, response.Body)
	}
	select {
	case settings := <-f.installed:
		// Nothing is chosen here: first boot offers the machine's choices
		if settings.Role != "" {
			t.Fatalf("the multi-purpose image recorded the role %q", settings.Role)
		}
	case <-time.After(time.Second):
		t.Fatal("the install never started")
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
