// Package helper is the root half of the web server. It checks passwords,
// keeps the sessions, and runs fnas for the people allowed to. The web front
// only ever holds a session's secret, so taking over the front gives no more
// than the sessions of people who log in while it is taken over.
package helper

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"regexp"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"fellowship-nas-web/internal/protocol"
)

const (
	IdleTimeout = 30 * time.Minute
	MaxAge      = 12 * time.Hour
	maxSessions = 256
	// Failed logins on one account before an alert, then again every this many
	alertAfter = 10
)

const (
	wrongPassword = "The user name or password is wrong."
	signedOut     = "You have been signed out. Log in again."
	notAdmin      = "Your account can't change the NAS. Ask an administrator to add it to the nas-admins group, then log in again."
)

// ErrExpired means the password was right but has to be changed first
var ErrExpired = errors.New("password expired")

var userName = regexp.MustCompile(`^[a-z_][a-z0-9_-]{0,31}$`)

// Commands nas-admins may run, by area and action. An area mapped to nil
// takes no action.
var commands = map[string][]string{
	"disk":     {"list"},
	"pool":     {"list", "scan", "status", "create", "import", "export", "scrub"},
	"share":    {"list", "create", "set", "delete"},
	"user":     {"smb-password"},
	"lan":      {"show", "set"},
	"snapshot": {"list", "policy"},
	"backup":   {"show", "set", "off", "run", "list", "restore"},
	"alert":    {"list", "show", "test", "email-set", "email-off", "ntfy-set", "ntfy-off"},
	"config":   {"show", "restore", "adopt"},
	"system":   {"show", "timezones", "hostname", "timezone", "certificate", "restart", "shutdown"},
	"health":   nil,
	"check":    nil,
}

type Account struct {
	UID   int
	Admin bool
}

type Config struct {
	// Checks a password with PAM, returning ErrExpired for an expired one
	Authenticate func(user, password string) error
	Lookup       func(user string) (Account, error)
	// Runs fnas, returning what it printed and whether it succeeded
	Fnas  func(args []string, input string) (stdout, stderr string, err error)
	Alert func(title, message string)
	Now   func() time.Time
}

type session struct {
	id, user, csrf, remote string
	started, lastSeen      time.Time
}

type Helper struct {
	config   Config
	mu       sync.Mutex
	sessions map[[32]byte]*session
	limiter  *limiter
}

func New(config Config) *Helper {
	if config.Now == nil {
		config.Now = time.Now
	}
	return &Helper{config: config, sessions: map[[32]byte]*session{}, limiter: newLimiter()}
}

func randomText(bytes int) string {
	buffer := make([]byte, bytes)
	if _, err := rand.Read(buffer); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(buffer)
}

func failure(code, text string) protocol.Response {
	return protocol.Response{Code: code, Error: text}
}

func (h *Helper) Handle(request protocol.Request) protocol.Response {
	switch request.Op {
	case protocol.OpLogin:
		return h.login(request)
	case protocol.OpCheck:
		return h.check(request)
	case protocol.OpLogout:
		h.mu.Lock()
		delete(h.sessions, sha256.Sum256([]byte(request.Session)))
		h.mu.Unlock()
		return protocol.Response{}
	case protocol.OpFnas:
		return h.fnas(request)
	case protocol.OpSessions:
		return h.list(request)
	case protocol.OpEndSession:
		return h.end(request)
	}
	return failure(protocol.CodeBadRequest, fmt.Sprintf("unknown request %q", request.Op))
}

func (h *Helper) login(request protocol.Request) protocol.Response {
	now := h.config.Now()
	keys := []string{"user:" + request.User, "addr:" + request.Remote}
	if wait := h.limiter.wait(keys, now); wait > 0 {
		seconds := int(wait.Round(time.Second) / time.Second)
		if seconds < 1 {
			seconds = 1
		}
		return protocol.Response{Code: protocol.CodeThrottled, RetryAfter: seconds,
			Error: fmt.Sprintf("Too many wrong passwords. Try again in %d seconds.", seconds)}
	}

	err := errors.New("not a person's account")
	if userName.MatchString(request.User) && request.Password != "" && len(request.Password) <= 1024 {
		var account Account
		if account, err = h.config.Lookup(request.User); err == nil {
			if account.UID < 1000 || account.UID >= 60000 {
				err = errors.New("not a person's account")
			} else {
				err = h.config.Authenticate(request.User, request.Password)
			}
		}
	}
	if errors.Is(err, ErrExpired) {
		return failure(protocol.CodeUnauthenticated, "Your password has expired. Change it over SSH, then log in here.")
	}
	if err != nil {
		log.Printf("failed login for %q from %s: %v", request.User, request.Remote, err)
		if count := h.limiter.fail(keys, now); count%alertAfter == 0 && h.config.Alert != nil {
			go h.config.Alert("Repeated failed logins",
				fmt.Sprintf("%d wrong passwords for %q on the NAS desktop, most recently from %s.",
					count, request.User, request.Remote))
		}
		return failure(protocol.CodeUnauthenticated, wrongPassword)
	}
	// The address keeps its count, so one good account can't reset guessing at others
	h.limiter.succeed(keys[:1])

	token := randomText(32)
	digest := sha256.Sum256([]byte(token))
	created := &session{id: hex.EncodeToString(digest[:6]), user: request.User, csrf: randomText(24),
		remote: request.Remote, started: now, lastSeen: now}
	h.mu.Lock()
	h.sessions[digest] = created
	h.prune(now)
	h.mu.Unlock()
	log.Printf("%s logged in from %s", request.User, request.Remote)

	response := h.describe(created, true)
	return protocol.Response{Token: token, Session: response}
}

// prune drops ended sessions, then the oldest ones over the limit. Callers hold h.mu.
func (h *Helper) prune(now time.Time) {
	for key, existing := range h.sessions {
		if h.ended(existing, now) {
			delete(h.sessions, key)
		}
	}
	for len(h.sessions) > maxSessions {
		var oldest [32]byte
		var when time.Time
		for key, existing := range h.sessions {
			if when.IsZero() || existing.lastSeen.Before(when) {
				oldest, when = key, existing.lastSeen
			}
		}
		delete(h.sessions, oldest)
	}
}

func (h *Helper) ended(s *session, now time.Time) bool {
	return now.Sub(s.lastSeen) > IdleTimeout || now.Sub(s.started) > MaxAge
}

// find returns the live session for token, counting this as activity when touch is set
func (h *Helper) find(token string, touch bool) *session {
	now := h.config.Now()
	key := sha256.Sum256([]byte(token))
	h.mu.Lock()
	defer h.mu.Unlock()
	found := h.sessions[key]
	if found == nil {
		return nil
	}
	if h.ended(found, now) {
		delete(h.sessions, key)
		return nil
	}
	if touch {
		found.lastSeen = now
	}
	copied := *found
	return &copied
}

func (h *Helper) admin(user string) bool {
	account, err := h.config.Lookup(user)
	return err == nil && account.Admin
}

func (h *Helper) describe(s *session, withSecret bool) *protocol.Session {
	described := &protocol.Session{ID: s.id, User: s.user, Admin: h.admin(s.user), Remote: s.remote,
		Started: s.started, LastSeen: s.lastSeen}
	if withSecret {
		described.CSRF = s.csrf
	}
	return described
}

func (h *Helper) check(request protocol.Request) protocol.Response {
	found := h.find(request.Session, request.Touch)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut)
	}
	return protocol.Response{Session: h.describe(found, true)}
}

// Allowed reports whether user may run fnas with args. Group membership is
// looked up on every call, so removing someone from nas-admins takes effect at once.
func Allowed(args []string, user string, admin bool) bool {
	if len(args) > 64 {
		return false
	}
	for _, arg := range args {
		if len(arg) > 4096 || strings.ContainsRune(arg, 0) {
			return false
		}
	}
	if !admin {
		// Everyone may set their own SMB password, and nothing else
		return slices.Equal(args, []string{"user", "smb-password", user, "--password-stdin"})
	}
	rest := args
	if len(rest) > 0 && rest[0] == "--json" {
		rest = rest[1:]
	}
	if len(rest) == 0 {
		return false
	}
	actions, known := commands[rest[0]]
	if !known {
		return false
	}
	if actions == nil {
		return len(rest) == 1
	}
	return len(rest) >= 2 && slices.Contains(actions, rest[1])
}

func (h *Helper) fnas(request protocol.Request) protocol.Response {
	// The desktop's polling runs fnas too, and mustn't keep an idle session alive
	found := h.find(request.Session, request.Touch)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut)
	}
	if len(request.Input) > 1<<20 {
		return failure(protocol.CodeBadRequest, "too much input")
	}
	if !Allowed(request.Args, found.user, h.admin(found.user)) {
		log.Printf("refused fnas %q for %s", request.Args, found.user)
		return failure(protocol.CodeForbidden, notAdmin)
	}
	log.Printf("%s ran fnas %s", found.user, strings.Join(request.Args, " "))
	stdout, stderr, err := h.config.Fnas(request.Args, request.Input)
	if err != nil {
		text := strings.TrimSpace(stderr)
		if text == "" {
			text = err.Error()
		}
		lines := strings.Split(text, "\n")
		for i, line := range lines {
			lines[i] = strings.TrimPrefix(line, "fnas: ")
		}
		return protocol.Response{Code: protocol.CodeFailed, Error: strings.Join(lines, "\n"), Output: stdout}
	}
	return protocol.Response{Output: stdout}
}

func (h *Helper) list(request protocol.Request) protocol.Response {
	found := h.find(request.Session, request.Touch)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut)
	}
	admin := h.admin(found.user)
	now := h.config.Now()
	h.mu.Lock()
	h.prune(now)
	var sessions []protocol.Session
	for _, existing := range h.sessions {
		// People see their own sessions; admins see everyone's
		if admin || existing.user == found.user {
			described := protocol.Session{ID: existing.id, User: existing.user, Remote: existing.remote,
				Started: existing.started, LastSeen: existing.lastSeen, Current: existing.id == found.id}
			sessions = append(sessions, described)
		}
	}
	h.mu.Unlock()
	for i := range sessions {
		sessions[i].Admin = h.admin(sessions[i].User)
	}
	sort.Slice(sessions, func(a, b int) bool {
		if !sessions[a].Started.Equal(sessions[b].Started) {
			return sessions[a].Started.Before(sessions[b].Started)
		}
		return sessions[a].ID < sessions[b].ID
	})
	return protocol.Response{Sessions: sessions}
}

func (h *Helper) end(request protocol.Request) protocol.Response {
	found := h.find(request.Session, true)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut)
	}
	admin := h.admin(found.user)
	h.mu.Lock()
	defer h.mu.Unlock()
	for key, existing := range h.sessions {
		if existing.id == request.Target {
			if !admin && existing.user != found.user {
				return failure(protocol.CodeForbidden, "Only an administrator can end someone else's session.")
			}
			delete(h.sessions, key)
			log.Printf("%s ended a session of %s", found.user, existing.user)
			return protocol.Response{}
		}
	}
	return failure(protocol.CodeFailed, "That session has already ended.")
}
