// Package front is the part of the web server that faces the network. It
// runs without special rights: it serves the desktop's files and passes each
// API request to the helper, which decides whether the session may make it.
package front

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime"
	"net"
	"net/http"
	"os"
	"path"
	"path/filepath"
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
	Call Caller
	// Stream is Call for an answer that carries a file after it
	Stream func(protocol.Request) (protocol.Response, io.ReadCloser, error)
	// Send is Call for a request that carries a file after it, such as an upload
	Send   func(protocol.Request, io.Reader) (protocol.Response, error)
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
	mux.HandleFunc("GET /api/files", s.withSession(false, s.files))
	mux.HandleFunc("GET /api/files/download", s.withSession(false, s.download))
	mux.HandleFunc("POST /api/files/upload", s.withSession(true, s.upload))
	mux.HandleFunc("POST /api/files/mkdir", s.withSession(true, s.mkdir))
	mux.HandleFunc("POST /api/files/rename", s.withSession(true, s.rename))
	mux.HandleFunc("POST /api/files/delete", s.withSession(true, s.remove))
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

// files lists what the person can browse, or what one folder holds. The helper
// decides both, from the session's own user.
func (s *Server) files(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	query := r.URL.Query()
	response, ok := s.call(w, protocol.Request{Op: protocol.OpFileList, Session: token,
		Share: query.Get("share"), Path: query.Get("path")})
	if !ok {
		return
	}
	if query.Get("share") == "" {
		writeJSON(w, http.StatusOK, json.RawMessage(`{"shares":`+response.Output+`}`))
		return
	}
	entries := response.Output
	if entries == "" {
		entries = "{}"
	}
	writeJSON(w, http.StatusOK, json.RawMessage(`{"share":`+quote(query.Get("share"))+
		`,"path":`+quote(query.Get("path"))+`,"folder":`+entries+`}`))
}

func quote(text string) string {
	out, _ := json.Marshal(text)
	return string(out)
}

// download sends a file's bytes, or the part a video player asked for. A big
// file doesn't fit the server's 60 second cover, so this answer owns its own
// deadline the way the jobs stream does.
func (s *Server) download(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	query := r.URL.Query()
	share, inside := query.Get("share"), query.Get("path")
	if share == "" || inside == "" {
		writeError(w, http.StatusBadRequest, "say which file: share and path")
		return
	}
	controller := http.NewResponseController(w)
	controller.SetReadDeadline(time.Time{})
	controller.SetWriteDeadline(time.Time{})
	request := protocol.Request{Op: protocol.OpFileRead, Session: token, Share: share, Path: inside}
	if start, length, ok := byteRange(r.Header.Get("Range")); ok {
		request.Start, request.Length = start, length
	}
	stream := s.Stream
	if stream == nil {
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	response, body, err := stream(request)
	if err != nil {
		log.Printf("helper: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	if body != nil {
		defer body.Close()
	}
	if response.Code != "" {
		writeFailure(w, response)
		return
	}
	header := w.Header()
	header.Set(contentType, contentTypeOf(inside))
	header.Set("Accept-Ranges", "bytes")
	disposition := "inline"
	if query.Get("save") != "" {
		disposition = "attachment"
	}
	header.Set("Content-Disposition", disposition+`; filename="`+safeName(inside)+`"`)
	status := http.StatusOK
	if request.Start > 0 || request.Length > 0 {
		status = http.StatusPartialContent
		header.Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", request.Start,
			request.Start+response.Length-1, response.Size))
	}
	header.Set("Content-Length", strconv.FormatInt(response.Length, 10))
	w.WriteHeader(status)
	if r.Method == http.MethodHead || body == nil {
		return
	}
	if _, err := io.Copy(w, body); err != nil {
		log.Printf("sending %s: %v", inside, err)
	}
}

// MaxUpload is the most one file may be, matching the helper's own cap.
const MaxUpload = 10 << 30

// upload receives a file and hands it to the helper, which writes it as the
// person. The body is the file itself, so nothing is held in memory here.
func (s *Server) upload(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	query := r.URL.Query()
	share, folder, name := query.Get("share"), query.Get("path"), query.Get("name")
	if share == "" || name == "" {
		writeError(w, http.StatusBadRequest, "say which file: share and name")
		return
	}
	path := name
	if folder != "" {
		path = folder + "/" + name
	}
	controller := http.NewResponseController(w)
	controller.SetReadDeadline(time.Time{})
	controller.SetWriteDeadline(time.Time{})
	body := http.MaxBytesReader(w, r.Body, MaxUpload)
	if s.Send == nil {
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	response, err := s.Send(protocol.Request{Op: protocol.OpFileWrite, Session: token,
		Share: share, Path: path, Replace: query.Get("replace") != ""}, body)
	if err != nil {
		log.Printf("helper: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	if response.Code != "" {
		writeFailure(w, response)
		return
	}
	writeJSON(w, http.StatusOK, map[string]int64{"size": response.Size})
}

// filesChange carries out the small changes: a new folder, a rename, a removal.
func (s *Server) filesChange(w http.ResponseWriter, r *http.Request, token string, op string,
	read func(body changeRequest) protocol.Request, missed func(body changeRequest) string) {
	var body changeRequest
	if !readJSON(w, r, 1<<16, &body) {
		return
	}
	// A new folder names the folder it goes in, which is empty at the top of a
	// share, so each change says for itself what it needs
	if body.Share == "" {
		writeError(w, http.StatusBadRequest, "say which share")
		return
	}
	if said := missed(body); said != "" {
		writeError(w, http.StatusBadRequest, said)
		return
	}
	request := read(body)
	request.Op = op
	request.Session = token
	request.Touch = r.Header.Get(BackgroundHeader) == ""
	if _, ok := s.call(w, request); ok {
		writeJSON(w, http.StatusOK, map[string]bool{"done": true})
	}
}

type changeRequest struct {
	Share     string `json:"share"`
	Path      string `json:"path"`
	Name      string `json:"name"`
	To        string `json:"to"`
	Recursive bool   `json:"recursive"`
}

func (s *Server) mkdir(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	s.filesChange(w, r, token, protocol.OpFileMkdir, func(body changeRequest) protocol.Request {
		path := body.Name
		if body.Path != "" {
			path = body.Path + "/" + body.Name
		}
		return protocol.Request{Share: body.Share, Path: path}
	}, func(body changeRequest) string {
		if body.Name == "" {
			return "say what to call the folder"
		}
		return ""
	})
}

func (s *Server) rename(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	s.filesChange(w, r, token, protocol.OpFileRename, func(body changeRequest) protocol.Request {
		return protocol.Request{Share: body.Share, Path: body.Path, To: body.To}
	}, func(body changeRequest) string {
		if body.Path == "" || body.To == "" {
			return "say what to rename and what to call it"
		}
		return ""
	})
}

func (s *Server) remove(w http.ResponseWriter, r *http.Request, session *protocol.Session, token string) {
	s.filesChange(w, r, token, protocol.OpFileDelete, func(body changeRequest) protocol.Request {
		return protocol.Request{Share: body.Share, Path: body.Path, Recursive: body.Recursive}
	}, func(body changeRequest) string {
		if body.Path == "" {
			return "say what to remove"
		}
		return ""
	})
}

// byteRange reads the one range a media player asks for. Anything else asks for
// the whole file, which is what a browser does when it has no opinion.
func byteRange(header string) (start, length int64, ok bool) {
	rest, found := strings.CutPrefix(header, "bytes=")
	if !found || strings.ContainsRune(rest, ',') {
		return 0, 0, false
	}
	first, last, found := strings.Cut(strings.TrimSpace(rest), "-")
	if !found {
		return 0, 0, false
	}
	start, err := strconv.ParseInt(first, 10, 64)
	if err != nil || start < 0 {
		return 0, 0, false
	}
	if last == "" {
		return start, 0, true
	}
	end, err := strconv.ParseInt(last, 10, 64)
	if err != nil || end < start {
		return start, 0, true
	}
	return start, end - start + 1, true
}

// contentTypeOf names a file for the browser. Only what the desktop previews is
// named; everything else is an opaque download.
func contentTypeOf(path string) string {
	shown := mime.TypeByExtension(strings.ToLower(filepath.Ext(path)))
	switch {
	case shown == "":
		return "application/octet-stream"
	case strings.HasPrefix(shown, "image/"), strings.HasPrefix(shown, "video/"),
		strings.HasPrefix(shown, "audio/"), strings.HasPrefix(shown, "text/"):
		return shown
	case shown == "application/pdf":
		return shown
	default:
		return "application/octet-stream"
	}
}

// safeName keeps a file name from breaking the header it goes in.
func safeName(path string) string {
	name := filepath.Base(path)
	return strings.Map(func(r rune) rune {
		if r == '"' || r == '\\' || r < 0x20 || r == 0x7f {
			return '_'
		}
		return r
	}, name)
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
	content, err := fs.ReadFile(s.Static, name)
	if err != nil {
		http.Error(w, "unreadable", http.StatusInternalServerError)
		return
	}
	// The package pins every file's date to its changelog's, so the date can't
	// tell browsers a file changed in an update. A tag from the contents can.
	sum := sha256.Sum256(content)
	w.Header().Set("ETag", `"`+hex.EncodeToString(sum[:16])+`"`)
	http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(content))
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
