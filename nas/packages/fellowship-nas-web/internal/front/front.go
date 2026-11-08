// Package front is the part of the web server that faces the network. It
// runs without special rights: it serves the desktop's files and passes each
// API request to the helper, which decides whether the session may make it.
package front

import (
	"bufio"
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"path"
	"strconv"
	"strings"
	"time"

	"fellowship-nas-web/internal/helper"
	"fellowship-nas-web/internal/protocol"
)

const (
	CookieName = "__Host-fellowship-session"
	// Sent with every changing request; the page reads it from the login answer
	TokenHeader = "X-Fellowship-Token"
	// Marks polling, which shouldn't keep an idle session alive
	BackgroundHeader = "X-Fellowship-Background"
)

const (
	signedOut   = "You have been signed out. Log in again."
	otherSite   = "This request didn't come from the NAS desktop, so it was refused."
	noHelper    = "The NAS's web helper isn't running. Restart it with: sudo systemctl restart fellowship-nas-helper"
	adminsOnly  = "Only members of nas-admins can do this."
	contentType = "Content-Type"
)

type Caller func(protocol.Request) (protocol.Response, error)

type Server struct {
	Call   Caller
	Static fs.FS
	// Read for the list of people's accounts
	Passwd string
	// "https" except when trying the desktop out over plain HTTP on this machine
	Scheme string
	// How often the jobs stream asks fnas for progress; a second when unset
	JobPoll time.Duration
}

type handler func(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string)

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/login", s.login)
	mux.HandleFunc("GET /api/session", s.withSession(false, s.session))
	mux.HandleFunc("POST /api/logout", s.withSession(true, s.logout))
	mux.HandleFunc("POST /api/fnas", s.withSession(true, s.fnas))
	mux.HandleFunc("GET /api/users", s.withSession(false, s.users))
	mux.HandleFunc("GET /api/sessions", s.withSession(false, s.sessions))
	mux.HandleFunc("POST /api/sessions/end", s.withSession(true, s.endSession))
	mux.HandleFunc("GET /api/jobs/events", s.withSession(false, s.jobEvents))
	mux.HandleFunc("/", s.static)
	return headers(mux)
}

func headers(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; object-src 'none'; "+
			"base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Cross-Origin-Opener-Policy", "same-origin")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		if strings.HasPrefix(r.URL.Path, "/api/") {
			h.Set("Cache-Control", "no-store")
		} else {
			h.Set("Cache-Control", "no-cache")
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set(contentType, "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, text string) {
	writeJSON(w, status, map[string]string{"error": text})
}

func writeFailure(w http.ResponseWriter, response protocol.Response) {
	status := http.StatusInternalServerError
	switch response.Code {
	case protocol.CodeUnauthenticated:
		status = http.StatusUnauthorized
	case protocol.CodeForbidden:
		status = http.StatusForbidden
	case protocol.CodeThrottled:
		status = http.StatusTooManyRequests
		w.Header().Set("Retry-After", strconv.Itoa(response.RetryAfter))
	case protocol.CodeBadRequest:
		status = http.StatusBadRequest
	case protocol.CodeFailed:
		status = http.StatusUnprocessableEntity
	}
	writeError(w, status, response.Error)
}

// sameOrigin is the defence against other sites: browsers always send
// Origin on POST, and it must name this server.
func (s *Server) sameOrigin(r *http.Request) bool {
	scheme := s.Scheme
	if scheme == "" {
		scheme = "https"
	}
	if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" {
		return false
	}
	origin := r.Header.Get("Origin")
	return origin != "" && origin == scheme+"://"+r.Host
}

func readJSON(w http.ResponseWriter, r *http.Request, limit int64, into any) bool {
	if !strings.HasPrefix(r.Header.Get(contentType), "application/json") {
		writeError(w, http.StatusUnsupportedMediaType, "send JSON")
		return false
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, limit)).Decode(into); err != nil {
		writeError(w, http.StatusBadRequest, "unreadable request")
		return false
	}
	return true
}

func (s *Server) call(w http.ResponseWriter, request protocol.Request) (protocol.Response, bool) {
	response, err := s.Call(request)
	if err != nil {
		log.Printf("helper: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return response, false
	}
	if response.Code != "" {
		writeFailure(w, response)
		return response, false
	}
	return response, true
}

// withSession runs next for a live session. Changing requests also need a
// matching Origin and the session's token header.
func (s *Server) withSession(changing bool, next handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if changing && !s.sameOrigin(r) {
			writeError(w, http.StatusForbidden, otherSite)
			return
		}
		cookie, err := r.Cookie(CookieName)
		if err != nil || cookie.Value == "" {
			writeError(w, http.StatusUnauthorized, signedOut)
			return
		}
		response, ok := s.call(w, protocol.Request{Op: protocol.OpCheck, Session: cookie.Value,
			Touch: r.Header.Get(BackgroundHeader) == ""})
		if !ok {
			return
		}
		if changing && subtle.ConstantTimeCompare([]byte(r.Header.Get(TokenHeader)), []byte(response.Session.CSRF)) != 1 {
			writeError(w, http.StatusForbidden, otherSite)
			return
		}
		next(w, r, response.Session, cookie.Value)
	}
}

func remoteAddress(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if !s.sameOrigin(r) {
		writeError(w, http.StatusForbidden, otherSite)
		return
	}
	var body struct {
		User     string `json:"user"`
		Password string `json:"password"`
	}
	if !readJSON(w, r, 8<<10, &body) {
		return
	}
	response, ok := s.call(w, protocol.Request{Op: protocol.OpLogin, User: strings.TrimSpace(body.User),
		Password: body.Password, Remote: remoteAddress(r)})
	if !ok {
		return
	}
	http.SetCookie(w, &http.Cookie{Name: CookieName, Value: response.Token, Path: "/", Secure: true,
		HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: int(helper.MaxAge.Seconds())})
	writeJSON(w, http.StatusOK, response.Session)
}

func (s *Server) session(w http.ResponseWriter, r *http.Request, session *protocol.Session, _ string) {
	writeJSON(w, http.StatusOK, session)
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	if _, ok := s.call(w, protocol.Request{Op: protocol.OpLogout, Session: token}); !ok {
		return
	}
	http.SetCookie(w, &http.Cookie{Name: CookieName, Value: "", Path: "/", Secure: true, HttpOnly: true,
		SameSite: http.SameSiteStrictMode, MaxAge: -1})
	writeJSON(w, http.StatusOK, map[string]string{})
}

func (s *Server) fnas(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	var body struct {
		Args  []string `json:"args"`
		Input string   `json:"input"`
	}
	if !readJSON(w, r, 2<<20, &body) {
		return
	}
	response, ok := s.call(w, protocol.Request{Op: protocol.OpFnas, Session: token, Args: body.Args, Input: body.Input,
		Touch: r.Header.Get(BackgroundHeader) == ""})
	if ok {
		writeJSON(w, http.StatusOK, map[string]string{"output": response.Output})
	}
}

// People reports the accounts people log in with, leaving out system and
// service accounts such as nas-share
func People(passwd io.Reader) []string {
	people := []string{}
	scanner := bufio.NewScanner(passwd)
	for scanner.Scan() {
		fields := strings.Split(scanner.Text(), ":")
		if len(fields) < 7 {
			continue
		}
		uid, err := strconv.Atoi(fields[2])
		if err != nil || uid < 1000 || uid >= 60000 {
			continue
		}
		if strings.HasSuffix(fields[6], "nologin") || strings.HasSuffix(fields[6], "false") {
			continue
		}
		people = append(people, fields[0])
	}
	return people
}

func (s *Server) users(w http.ResponseWriter, r *http.Request, session *protocol.Session, _ string) {
	if !session.Admin {
		writeError(w, http.StatusForbidden, adminsOnly)
		return
	}
	file, err := os.Open(s.Passwd)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer file.Close()
	writeJSON(w, http.StatusOK, People(file))
}

func (s *Server) sessions(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	response, ok := s.call(w, protocol.Request{Op: protocol.OpSessions, Session: token})
	if ok {
		if response.Sessions == nil {
			response.Sessions = []protocol.Session{}
		}
		writeJSON(w, http.StatusOK, response.Sessions)
	}
}

func (s *Server) endSession(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	var body struct {
		ID string `json:"id"`
	}
	if !readJSON(w, r, 1<<10, &body) {
		return
	}
	if _, ok := s.call(w, protocol.Request{Op: protocol.OpEndSession, Session: token, Target: body.ID}); ok {
		writeJSON(w, http.StatusOK, map[string]string{})
	}
}

// static serves the desktop's files, and nothing else: no directory listings
func (s *Server) static(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		writeError(w, http.StatusNotFound, "no such request")
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
	if name == "" {
		name = "index.html"
	}
	info, err := fs.Stat(s.Static, name)
	if errors.Is(err, fs.ErrNotExist) || (err == nil && info.IsDir()) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		http.Error(w, "unreadable", http.StatusInternalServerError)
		return
	}
	file, err := s.Static.Open(name)
	if err != nil {
		http.Error(w, "unreadable", http.StatusInternalServerError)
		return
	}
	defer file.Close()
	http.ServeContent(w, r, name, info.ModTime(), file.(io.ReadSeeker))
}

// jobEvents streams the list of long jobs as server-sent events, sending it
// again whenever a job moves on. Asking for progress doesn't count as using
// the desktop, so an idle session still ends, and the stream with it.
func (s *Server) jobEvents(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming isn't supported")
		return
	}
	poll := s.JobPoll
	if poll == 0 {
		poll = time.Second
	}
	w.Header().Set(contentType, "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	last, quiet := "", 0
	for {
		response, err := s.Call(protocol.Request{Op: protocol.OpFnas, Session: token,
			Args: []string{"--json", "job", "list"}})
		switch {
		case err != nil || response.Code == protocol.CodeUnauthenticated:
			// The page logs in again, or waits for the helper, and reconnects
			io.WriteString(w, "event: ended\ndata: {}\n\n")
			flusher.Flush()
			return
		case response.Code == "" && response.Output != last:
			// Each event is one line of data, so the JSON is sent compacted
			var compact bytes.Buffer
			if json.Compact(&compact, []byte(response.Output)) != nil {
				break
			}
			last, quiet = response.Output, 0
			io.WriteString(w, "event: jobs\ndata: "+compact.String()+"\n\n")
		case quiet*int(poll/time.Millisecond) >= 15000:
			// A comment now and then keeps proxies from closing a quiet stream
			quiet = 0
			io.WriteString(w, ": still here\n\n")
		default:
			quiet++
		}
		flusher.Flush()
		select {
		case <-r.Context().Done():
			return
		case <-time.After(poll):
		}
	}
}
