// Locks: what a word processor insists on before it will save to a network
// drive. A client takes an exclusive lock on a file, writes it, and lets go.
//
// They live in this process only. Every lock carries a time, so a client that
// disappears without unlocking holds nothing for longer than that, and a restart
// of the desktop loses them all, which is what clients expect of a server that
// has restarted.
package front

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// How long a lock lasts when the client doesn't say, and the longest any
	// client may ask for
	davLockFor = 30 * time.Minute
	davLockMax = 12 * time.Hour
	// Every lock token starts with this, as RFC 4918 asks
	davLockPrefix = "opaquelocktoken:"
)

// davLock is one client's hold on one address.
type davLock struct {
	token string
	// Whose lock it is. The token alone is not enough: a token seen over someone
	// else's shoulder would otherwise let them write, or turn the lock off.
	user    string
	share   string
	inside  string
	depth   string // "0", or "infinity" for a folder and everything in it
	owner   string // the owner XML the client sent, kept as it came
	expires time.Time
}

// davLocks is every lock held, and the clock they run on.
type davLocks struct {
	mu   sync.Mutex
	held []davLock
	now  func() time.Time
}

func (l *davLocks) clock() time.Time {
	if l.now != nil {
		return l.now()
	}
	return time.Now()
}

// live drops the locks whose time is up. Callers hold l.mu.
func (l *davLocks) live() []davLock {
	now := l.clock()
	kept := make([]davLock, 0, len(l.held))
	for _, one := range l.held {
		if now.Before(one.expires) {
			kept = append(kept, one)
		}
	}
	l.held = kept
	return kept
}

// drop forgets one lock. Callers hold l.mu.
func (l *davLocks) drop(token string) {
	kept := make([]davLock, 0, len(l.held))
	for _, one := range l.held {
		if one.token != token {
			kept = append(kept, one)
		}
	}
	l.held = kept
}

// covers answers whether a lock is over an address. A lock on a folder taken with
// Depth: infinity is over everything beneath it.
func (one davLock) covers(share, inside string) bool {
	if one.share != share {
		return false
	}
	if one.inside == inside {
		return true
	}
	if one.depth != "infinity" {
		return false
	}
	if one.inside == "" {
		// A lock on the share itself is over everything in it. The prefix test
		// below can't say so, since an address inside a share has no leading slash.
		return true
	}
	return strings.HasPrefix(inside, strings.TrimSuffix(one.inside, "/")+"/")
}

// davLockOn is the lock over an address, if one is held.
func (s *Server) davLockOn(share, inside string) (davLock, bool) {
	s.davLocks.mu.Lock()
	defer s.davLocks.mu.Unlock()
	return s.davLockOnLocked(share, inside)
}

// davLockOnLocked is davLockOn for a caller that holds the mutex.
func (s *Server) davLockOnLocked(share, inside string) (davLock, bool) {
	for _, one := range s.davLocks.live() {
		if one.covers(share, inside) {
			return one, true
		}
	}
	return davLock{}, false
}

// davBlockedBy is the check every change makes: is this address locked, and does
// this request satisfy the lock? Satisfying it means being the account that took
// it *and* naming its token.
func (s *Server) davBlockedBy(r *http.Request, who davPerson, share, inside string) (davLock, bool) {
	one, held := s.davLockOn(share, inside)
	if !held {
		return davLock{}, false
	}
	return one, !one.settled(r, who)
}

// settled answers whether this request may work through a lock.
func (one davLock) settled(r *http.Request, who davPerson) bool {
	return one.user == who.name && davTokensIn(r.Header.Get("If"))[one.token]
}

// davLocksUnder is every lock at or beneath a folder, which a delete or a move
// of that folder would take with it.
func (s *Server) davLocksUnder(share, inside string) []davLock {
	prefix := strings.TrimSuffix(inside, "/") + "/"
	if inside == "" {
		// Everything in the share is under its root, and no address inside a
		// share starts with a slash for the prefix test to find
		prefix = ""
	}
	s.davLocks.mu.Lock()
	defer s.davLocks.mu.Unlock()
	var found []davLock
	for _, one := range s.davLocks.live() {
		if one.share != share {
			continue
		}
		if one.inside == inside || strings.HasPrefix(one.inside, prefix) {
			found = append(found, one)
		}
	}
	return found
}

// davFolderBlocked answers whether something inside a folder is locked in a way
// this request doesn't satisfy, which is what stops a folder being deleted or
// moved out from under someone's open file.
func (s *Server) davFolderBlocked(r *http.Request, who davPerson, share, inside string) (bool, bool) {
	held := s.davLocksUnder(share, inside)
	if len(held) == 0 {
		return false, false
	}
	for _, one := range held {
		if !one.settled(r, who) {
			return true, true
		}
	}
	return false, true
}

// davTokensIn reads every lock token in an If header, which can be a plain list
// or a list of resources each with their own tokens.
func davTokensIn(header string) map[string]bool {
	found := map[string]bool{}
	for len(header) > 0 {
		open := strings.IndexByte(header, '<')
		if open < 0 {
			break
		}
		shut := strings.IndexByte(header[open:], '>')
		if shut < 0 {
			break
		}
		found[header[open+1:open+shut]] = true
		header = header[open+shut+1:]
	}
	return found
}

// davExpiry reads the Timeout header, which is "Second-600", "Infinite", or a
// list of preferences.
func (l *davLocks) expiry(header string) time.Duration {
	for _, part := range strings.Split(header, ",") {
		part = strings.TrimSpace(part)
		if part == "Infinite" {
			return davLockMax
		}
		rest, found := strings.CutPrefix(part, "Second-")
		if !found {
			continue
		}
		seconds, err := strconv.Atoi(rest)
		if err != nil || seconds <= 0 {
			continue
		}
		want := time.Duration(seconds) * time.Second
		if want > davLockMax {
			want = davLockMax
		}
		return want
	}
	return davLockFor
}

// davLock answers LOCK, which either takes a lock or renews the one the client
// already holds.
func (s *Server) davLock(w http.ResponseWriter, r *http.Request, who davPerson, where davWhere) {
	token := who.token
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
	if err != nil {
		writeError(w, http.StatusBadRequest, "the lock request can't be read")
		return
	}
	text := string(body)
	if strings.Contains(strings.ToLower(text), "shared") {
		writeError(w, http.StatusForbidden,
			"only an exclusive lock is offered here, not a shared one")
		return
	}
	// Renewing: the client sends the token it holds and no lockinfo with it
	if len(davTokensIn(r.Header.Get("If"))) > 0 &&
		!strings.Contains(strings.ToLower(text), "lockinfo") {
		s.davRenew(w, r, who, where)
		return
	}
	entry, exists, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if !exists && where.Inside == "" {
		writeError(w, http.StatusConflict, davNoTarget)
		return
	}
	if exists && entry.Dir && r.Header.Get("Depth") == "0" {
		writeError(w, http.StatusBadRequest,
			"a folder is locked with Depth: infinity, not Depth: 0")
		return
	}
	depth := "0"
	if exists && entry.Dir {
		depth = "infinity"
	}
	s.davLocks.mu.Lock()
	defer s.davLocks.mu.Unlock()
	if held, taken := s.davLockOnLocked(where.Share, where.Inside); taken {
		// Only the account holding it may take it again or renew it: a token seen
		// over someone's shoulder is no use to anyone else
		if !held.settled(r, who) {
			writeError(w, http.StatusLocked,
				"someone else is working on that; try again when they have finished")
			return
		}
		s.davLocks.drop(held.token)
	}
	// A lock on a name that isn't there yet is held all the same: the client is
	// about to write it. Making the empty file now would break the
	// If-None-Match: * that goes with that write, which is how a mounted drive
	// saves a new file.
	one := davLock{token: davLockPrefix + newLockToken(), user: who.name,
		share: where.Share, inside: where.Inside, depth: depth, owner: davOwnerOf(text),
		expires: s.davLocks.clock().Add(s.davLocks.expiry(r.Header.Get("Timeout")))}
	s.davLocks.held = append(s.davLocks.held, one)
	s.davSendLock(w, one, http.StatusOK)
}

// davRenew pushes a lock's time out, for a client in the middle of working on the
// file. A token it doesn't hold is not a renewal.
func (s *Server) davRenew(w http.ResponseWriter, r *http.Request, who davPerson, where davWhere) {
	s.davLocks.mu.Lock()
	defer s.davLocks.mu.Unlock()
	mine := davTokensIn(r.Header.Get("If"))
	for _, one := range s.davLocks.live() {
		// Somebody else's lock, whatever token is offered with it
		if one.share == where.Share && one.inside == where.Inside && one.user != who.name {
			writeError(w, http.StatusLocked, davLockedOthers)
			return
		}
	}
	for i, one := range s.davLocks.live() {
		if one.share != where.Share || one.inside != where.Inside || one.user != who.name ||
			!mine[one.token] {
			continue
		}
		s.davLocks.held[i].expires = s.davLocks.clock().Add(s.davLocks.expiry(r.Header.Get("Timeout")))
		s.davSendLock(w, s.davLocks.held[i], http.StatusOK)
		return
	}
	writeError(w, http.StatusPreconditionFailed, "that lock isn't held here")
}

// davSendLock answers with the token and what the client now holds.
func (s *Server) davSendLock(w http.ResponseWriter, one davLock, status int) {
	w.Header().Set("Lock-Token", "<"+one.token+">")
	w.Header().Set(contentType, "application/xml; charset=utf-8")
	w.WriteHeader(status)
	fmt.Fprint(w, davLockBody(one, s.davLocks.clock()))
}

// davUnlock lets go of a lock, which only the client holding it can do.
func (s *Server) davUnlock(w http.ResponseWriter, r *http.Request, who davPerson, where davWhere) {
	token := who.token
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	want := davUnquoteToken(r.Header.Get("Lock-Token"))
	if want == "" {
		writeError(w, http.StatusBadRequest, "say which lock to let go of in Lock-Token")
		return
	}
	s.davLocks.mu.Lock()
	held, found := davLock{}, false
	for _, one := range s.davLocks.live() {
		if one.token == want && one.share == where.Share && one.inside == where.Inside {
			held, found = one, true
		}
	}
	if !found {
		s.davLocks.mu.Unlock()
		writeError(w, http.StatusConflict, "that lock isn't held here")
		return
	}
	if held.user != who.name {
		s.davLocks.mu.Unlock()
		writeError(w, http.StatusLocked, "that lock belongs to another account")
		return
	}
	s.davLocks.drop(want)
	s.davLocks.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

// davUnquoteToken takes the angle brackets off a Lock-Token header.
func davUnquoteToken(header string) string {
	return strings.Trim(strings.TrimSpace(header), "<>")
}

// davLockBody is the lockdiscovery a client expects back: what it holds, what
// kind of lock it is, and how long it has left.
func davLockBody(one davLock, now time.Time) string {
	left := one.expires.Sub(now)
	if left < time.Second {
		left = time.Second
	}
	return `<?xml version="1.0" encoding="utf-8"?>` + "\n" +
		`<D:prop xmlns:D="DAV:"><D:lockdiscovery>` +
		davActiveLock(one, int64(left/time.Second)) +
		`</D:lockdiscovery></D:prop>`
}

// davActiveLock is one activelock element.
func davActiveLock(one davLock, seconds int64) string {
	owner := ""
	if one.owner != "" {
		owner = "<D:owner>" + one.owner + "</D:owner>"
	}
	if seconds < 1 {
		seconds = 1
	}
	return `<D:activelock><D:locktype><D:write/></D:locktype>` +
		`<D:lockscope><D:exclusive/></D:lockscope><D:depth>` + one.depth + `</D:depth>` +
		owner + `<D:timeout>Second-` + strconv.FormatInt(seconds, 10) + `</D:timeout>` +
		`<D:locktoken><D:href>` + xmlEscape(one.token) + `</D:href></D:locktoken>` +
		`<D:lockroot><D:href>` +
		xmlEscape(davHref(davWhere{Share: one.share, Inside: one.inside}, false)) +
		`</D:href></D:lockroot></D:activelock>`
}

// davLockProps is what PROPFIND says about locking on one address: that an
// exclusive write lock is possible, and whether one is held.
func (s *Server) davLockProps(share, inside string) string {
	props := `<D:supportedlock><D:lockentry><D:lockscope><D:exclusive/></D:lockscope>` +
		`<D:locktype><D:write/></D:locktype></D:lockentry></D:supportedlock>`
	one, held := s.davLockOn(share, inside)
	if !held {
		return props
	}
	left := one.expires.Sub(s.davLocks.clock())
	seconds := int64(left / time.Second)
	if seconds < 1 {
		seconds = 1
	}
	return props + `<D:lockdiscovery>` + davActiveLock(one, seconds) + `</D:lockdiscovery>`
}

// davOwnerOf keeps the owner element the client sent, as it came.
func davOwnerOf(body string) string {
	lowered := strings.ToLower(body)
	start := strings.Index(lowered, "<d:owner>")
	if start < 0 {
		return ""
	}
	end := strings.Index(lowered, "</d:owner>")
	if end < start {
		return ""
	}
	return body[start+len("<d:owner>") : end]
}

// newLockToken is a token no other client will guess.
func newLockToken() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "FellowshipNAS-" + strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return "FellowshipNAS-" + hex.EncodeToString(buffer)
}
