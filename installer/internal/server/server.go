// Package server is the installer's web API. The machine's own screen
// (the kiosk browser, on 127.0.0.1) may use it at once. Another computer must
// first type the pairing code shown on that screen, so nobody else on the
// network can erase a disk.
package server

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"math/big"
	"net"
	"net/http"
	"path"
	"strings"
	"sync"

	"fellowship-installer/internal/disks"
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
)

const (
	cookieName = "fellowship-installer"
	// Required on every POST; another site can't send it without asking first
	header      = "X-Fellowship-Installer"
	maxAttempts = 10
)

type Machine struct {
	Installed, Memory int64
	Disks             func() ([]disks.Disk, error)
	Timezones         func() ([]string, error)
	Addresses         func() []string
	// Starts an install; reports through the callbacks
	Install func(engine.Settings, func(string), func(string)) error
	Reboot  func() error
}

type progress struct {
	Phase string   `json:"phase"` // choose, installing, done, failed
	Steps []string `json:"steps"`
	Step  int      `json:"step"`
	Log   []string `json:"log"`
	Error string   `json:"error,omitempty"`
}

type Server struct {
	machine Machine
	static  fs.FS

	mu       sync.Mutex
	code     string
	attempts int
	paired   map[string]bool
	state    progress
}

func randomCode() string {
	n, err := rand.Int(rand.Reader, big.NewInt(1_000_000))
	if err != nil {
		panic(err)
	}
	return fmt.Sprintf("%06d", n.Int64())
}

func randomToken() string {
	buffer := make([]byte, 24)
	rand.Read(buffer)
	return fmt.Sprintf("%x", buffer)
}

func New(machine Machine, static fs.FS) *Server {
	return &Server{machine: machine, static: static, code: randomCode(), paired: map[string]bool{},
		state: progress{Phase: "choose", Steps: engine.Steps, Step: -1}}
}

// Code is the current pairing code, for the console
func (s *Server) Code() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.code
}

func local(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func (s *Server) allowed(r *http.Request) bool {
	if local(r) {
		return true
	}
	cookie, err := r.Cookie(cookieName)
	if err != nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.paired[cookie.Value]
}

func sameOrigin(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	return r.Header.Get(header) == "1" && (origin == "" || origin == "http://"+r.Host)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(value)
}

func fail(w http.ResponseWriter, status int, text string) {
	writeJSON(w, status, map[string]string{"error": text})
}

func readJSON(w http.ResponseWriter, r *http.Request, into any) bool {
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(into); err != nil {
		fail(w, http.StatusBadRequest, "unreadable request")
		return false
	}
	return true
}

// guard lets a handler run only for the local screen or a paired browser,
// and only for POSTs that came from the installer page
func (s *Server) guard(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && !sameOrigin(r) {
			fail(w, http.StatusForbidden, "This request didn't come from the installer page.")
			return
		}
		if !s.allowed(r) {
			fail(w, http.StatusUnauthorized, "Type the pairing code shown on the NAS's screen.")
			return
		}
		next(w, r)
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/hello", s.hello)
	mux.HandleFunc("POST /api/pair", s.pair)
	mux.HandleFunc("GET /api/machine", s.guard(s.machineInfo))
	mux.HandleFunc("POST /api/layout", s.guard(s.layout))
	mux.HandleFunc("POST /api/install", s.guard(s.install))
	mux.HandleFunc("GET /api/progress", s.guard(s.progress))
	mux.HandleFunc("POST /api/reboot", s.guard(s.reboot))
	mux.HandleFunc("/", s.files)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		mux.ServeHTTP(w, r)
	})
}

// hello tells the page whether it may go on, and shows the pairing code
// only on the machine's own screen
func (s *Server) hello(w http.ResponseWriter, r *http.Request) {
	answer := map[string]any{"allowed": s.allowed(r), "local": local(r)}
	if local(r) {
		s.mu.Lock()
		answer["code"] = s.code
		s.mu.Unlock()
		answer["addresses"] = s.machine.Addresses()
	}
	writeJSON(w, http.StatusOK, answer)
}

func (s *Server) pair(w http.ResponseWriter, r *http.Request) {
	if !sameOrigin(r) {
		fail(w, http.StatusForbidden, "This request didn't come from the installer page.")
		return
	}
	var body struct {
		Code string `json:"code"`
	}
	if !readJSON(w, r, &body) {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if subtle.ConstantTimeCompare([]byte(strings.TrimSpace(body.Code)), []byte(s.code)) != 1 {
		s.attempts++
		if s.attempts >= maxAttempts {
			// Guessing gets nowhere: the screen shows a new code
			s.code, s.attempts = randomCode(), 0
			log.Printf("too many wrong pairing codes from %s; made a new one", r.RemoteAddr)
		}
		fail(w, http.StatusUnauthorized, "That code is wrong. Check the NAS's screen.")
		return
	}
	token := randomToken()
	s.paired[token] = true
	s.attempts = 0
	http.SetCookie(w, &http.Cookie{Name: cookieName, Value: token, Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode})
	log.Printf("paired a browser at %s", r.RemoteAddr)
	writeJSON(w, http.StatusOK, map[string]bool{"allowed": true})
}

type diskChoice struct {
	disks.Disk
	Layout *layout.Layout `json:"layout,omitempty"`
}

func (s *Server) machineInfo(w http.ResponseWriter, r *http.Request) {
	found, err := s.machine.Disks()
	if err != nil {
		fail(w, http.StatusInternalServerError, err.Error())
		return
	}
	choices := make([]diskChoice, 0, len(found))
	for _, d := range found {
		choice := diskChoice{Disk: d}
		if d.Refused == "" {
			if suggested, err := layout.Suggest(d.Size, s.machine.Installed, s.machine.Memory); err == nil {
				choice.Layout = &suggested
			} else {
				choice.Refused = err.Error()
			}
		}
		choices = append(choices, choice)
	}
	zones, err := s.machine.Timezones()
	if err != nil {
		zones = []string{"UTC"}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"disks": choices, "timezones": zones, "installed": s.machine.Installed,
		"memory": s.machine.Memory, "min_system": layout.MinSystem(s.machine.Installed),
		"split_from": layout.SplitFrom,
	})
}

func (s *Server) layout(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Size   int64 `json:"size"`
		System int64 `json:"system"`
	}
	if !readJSON(w, r, &body) {
		return
	}
	chosen, err := layout.WithSystem(body.Size, s.machine.Installed, s.machine.Memory, body.System)
	if err != nil {
		fail(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, chosen)
}

// ConfirmPhrase is what the person types to erase a disk: its short name
func ConfirmPhrase(disk string) string {
	return "erase " + path.Base(disk)
}

func (s *Server) install(w http.ResponseWriter, r *http.Request) {
	var body struct {
		engine.Settings
		Confirm string `json:"confirm"`
	}
	if !readJSON(w, r, &body) {
		return
	}
	if err := body.Settings.Check(); err != nil {
		fail(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	// Only a disk that is offered, and not refused, may be erased
	found, err := s.machine.Disks()
	if err != nil {
		fail(w, http.StatusInternalServerError, err.Error())
		return
	}
	offered := false
	for _, d := range found {
		offered = offered || (d.Path == body.Disk && d.Refused == "")
	}
	if !offered {
		fail(w, http.StatusUnprocessableEntity, "That disk can't be used.")
		return
	}
	if body.Confirm != ConfirmPhrase(body.Disk) {
		fail(w, http.StatusUnprocessableEntity, fmt.Sprintf("Type %q to confirm.", ConfirmPhrase(body.Disk)))
		return
	}
	body.Role = "nas"

	s.mu.Lock()
	if s.state.Phase == "installing" || s.state.Phase == "done" {
		s.mu.Unlock()
		fail(w, http.StatusConflict, "An install has already started.")
		return
	}
	s.state = progress{Phase: "installing", Steps: engine.Steps, Step: -1}
	s.mu.Unlock()
	log.Printf("installing to %s for %s", body.Disk, body.User)

	go func() {
		err := s.machine.Install(body.Settings, s.stepped, s.logged)
		s.mu.Lock()
		defer s.mu.Unlock()
		if err != nil {
			s.state.Phase, s.state.Error = "failed", err.Error()
			log.Printf("install failed: %v", err)
			return
		}
		s.state.Phase = "done"
		s.state.Step = len(engine.Steps)
	}()
	writeJSON(w, http.StatusAccepted, map[string]string{})
}

func (s *Server) stepped(title string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i, step := range engine.Steps {
		if step == title {
			s.state.Step = i
		}
	}
	s.state.Log = append(s.state.Log, title+"…")
}

func (s *Server) logged(line string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state.Log = append(s.state.Log, line)
	if len(s.state.Log) > 200 {
		s.state.Log = s.state.Log[len(s.state.Log)-200:]
	}
}

func (s *Server) progress(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	state := s.state
	state.Log = append([]string(nil), s.state.Log...)
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, state)
}

func (s *Server) reboot(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	phase := s.state.Phase
	s.mu.Unlock()
	if phase == "installing" {
		fail(w, http.StatusConflict, "Wait for the install to finish.")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{})
	go s.machine.Reboot()
}

func (s *Server) files(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
	if name == "" || strings.HasPrefix(name, "api/") {
		name = "index.html"
	}
	if info, err := fs.Stat(s.static, name); err != nil || info.IsDir() {
		name = "index.html"
	}
	http.ServeFileFS(w, r, s.static, name)
}
