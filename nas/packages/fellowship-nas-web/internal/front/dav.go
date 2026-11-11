// The WebDAV face of the NAS: read-only, over HTTPS, at /dav/.
//
// A share reaches a phone's file app this way only when it has been shared over
// WebDAV, and only for the people who could already use it in Files: the helper
// is asked for the shares this person may use, and every path goes through the
// same resolver a file in the desktop does. Nothing here works out a path on
// disk, and nothing here can change a file: the only methods answered are the
// ones that look.
//
// A client signs in with the same user name and password as the desktop, on
// every request, so the password is only ever accepted over HTTPS.
package front

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"

	"fellowship-nas-web/internal/protocol"
)

const (
	davRealm = `Basic realm="FellowshipNAS", charset="UTF-8"`
	// What a client is told it may do. A write is answered with the same list,
	// so a client learns the shape of this share rather than guessing.
	davAllow = "OPTIONS, GET, HEAD, PROPFIND, PUT, MKCOL, DELETE, MOVE, COPY, LOCK, UNLOCK"
	// How long a client's session is kept once its password has been checked, so
	// a folder of fifty thumbnails costs one check rather than fifty.
	davKeep     = 30 * time.Minute
	davNoTLS    = "WebDAV sends the password with every request, so it is only served over HTTPS."
	davNoSuch   = "There is no WebDAV folder at that address."
	davReadOnly = "That isn't something this share allows over WebDAV."
	davNoShare  = "There is no WebDAV folder at that address."
	davHidden   = "That folder isn't served over WebDAV."
	davNoTarget = "A write has to name a file, not the share itself."
	// Two ways of saying no to a change: the thing itself is locked, or something
	// inside the folder is. Neither names the lock's token, which is the holder's
	// business and nobody else's.
	davLockedOthers = "someone else is working on that; try again when they have finished"
	davLockedInside = "something inside that folder is open in someone else's app, " +
		"so it can't be moved or deleted yet"
)

// davToken is one client's session, kept so its password isn't checked again.
type davToken struct {
	user  string
	token string
	at    time.Time
}

// davPerson is who a request is from: the account, and the session the helper gave
// them. A lock belongs to the account, so both travel together.
type davPerson struct {
	name  string
	token string
}

// davWhere is the address a client asked for, split the way the helper wants it.
type davWhere struct {
	Share  string // "" at the top, where the person's shares are listed
	Inside string // a path inside the share, with no leading slash
}

// davEntry is one row of a folder as the child grades it.
type davEntry struct {
	Name     string `json:"name"`
	Dir      bool   `json:"dir"`
	Size     int64  `json:"size"`
	Tag      string `json:"tag"`
	Modified string `json:"modified"`
}

type davFolder struct {
	Error   string     `json:"error"`
	Entries []davEntry `json:"entries"`
	Self    *davEntry  `json:"self"`
}

// davShare is one share the person may use, and whether it is on WebDAV.
type davShare struct {
	Share  string `json:"share"`
	Level  string `json:"level"`
	Webdav bool   `json:"webdav"`
}

// dav keeps the sessions of the clients talking to us, keyed by a digest of the
// credentials they send, so the password itself is never held.
type davSessions struct {
	mu   sync.Mutex
	kept map[[32]byte]davToken
}

// dav is the handler for everything under /dav/.
func (s *Server) dav(w http.ResponseWriter, r *http.Request) {
	// This server terminates the TLS itself, so the connection is what decides
	// whether a password may be read: a scheme passed on the command line is not
	// evidence that this request came in over HTTPS
	if r.TLS == nil {
		writeError(w, http.StatusForbidden, davNoTLS)
		return
	}
	user, password, ok := r.BasicAuth()
	if !ok || user == "" {
		w.Header().Set("WWW-Authenticate", davRealm)
		writeError(w, http.StatusUnauthorized, "Sign in with your NAS user name and password.")
		return
	}
	token, said := s.davSession(user, password, remoteAddress(r))
	if said != "" {
		w.Header().Set("WWW-Authenticate", davRealm)
		writeError(w, http.StatusUnauthorized, said)
		return
	}
	// Who this is, as well as what the helper calls them: a lock belongs to an
	// account, not to a token somebody may have seen
	who := davPerson{name: user, token: token}
	where, err := davPath(r.URL.EscapedPath())
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, errHidden) {
			status = http.StatusForbidden
		}
		writeError(w, status, err.Error())
		return
	}
	switch r.Method {
	case http.MethodOptions:
		davAllowHeaders(w)
		w.WriteHeader(http.StatusOK)
	case "PROPFIND":
		s.davPropfind(w, r, who, where)
	case http.MethodGet, http.MethodHead:
		s.davGet(w, r, who, where)
	case http.MethodPut:
		s.davPut(w, r, who, where)
	case "MKCOL":
		s.davMkcol(w, r, who, where)
	case http.MethodDelete:
		s.davDelete(w, r, who, where)
	case "MOVE", "COPY":
		s.davMoveCopy(w, r, who, where)
	case "LOCK":
		s.davLock(w, r, who, where)
	case "UNLOCK":
		s.davUnlock(w, r, who, where)
	case "PROPPATCH", "POST", "PATCH":
		davAllowHeaders(w)
		writeError(w, http.StatusMethodNotAllowed, davReadOnly)
	default:
		davAllowHeaders(w)
		writeError(w, http.StatusMethodNotAllowed, davReadOnly)
	}
}

func davAllowHeaders(w http.ResponseWriter) {
	header := w.Header()
	// Class 2 means locking is offered, which is how a client decides to take a
	// lock before saving rather than guessing
	header.Set("DAV", "1, 2")
	header.Set("MS-Author-Via", "DAV")
	header.Set("Allow", davAllow)
}

// davSession returns the session for these credentials, checking the password
// with the helper when it has not seen it lately. The address goes with it, so a
// machine guessing passwords is slowed down on its own rather than through
// everybody else's WebDAV logins.
func (s *Server) davSession(user, password, remote string) (string, string) {
	key := sha256.Sum256([]byte(user + "\x00" + password))
	now := time.Now()
	s.davKept.mu.Lock()
	kept, seen := s.davKept.kept[key]
	s.davKept.mu.Unlock()
	if seen && now.Sub(kept.at) < davKeep {
		return kept.token, ""
	}
	response, err := s.Call(protocol.Request{Op: protocol.OpLogin, User: user, Password: password,
		Remote: remote})
	if err != nil {
		log.Printf("webdav login: %v", err)
		return "", "The NAS's web helper isn't running."
	}
	if response.Code != "" {
		log.Printf("webdav login for %s refused: %s", user, response.Error)
		return "", response.Error
	}
	s.davKept.mu.Lock()
	if s.davKept.kept == nil {
		s.davKept.kept = make(map[[32]byte]davToken)
	}
	for old, one := range s.davKept.kept {
		if now.Sub(one.at) >= davKeep {
			delete(s.davKept.kept, old)
		}
	}
	s.davKept.kept[key] = davToken{user: user, token: response.Token, at: now}
	s.davKept.mu.Unlock()
	return response.Token, ""
}

// davPath turns what the client asked for into a share and a path inside it.
func davPath(escaped string) (davWhere, error) {
	return davWhereIn(escaped)
}

// davWhereIn splits an escaped address under /dav into a share and a path
// inside it, one segment at a time, so an escaped slash can't become a folder.
// A request's own address and a Destination header both come through here, so
// a name means the same thing however it arrived.
func davWhereIn(escaped string) (davWhere, error) {
	rest, found := strings.CutPrefix(escaped, "/dav")
	if !found {
		return davWhere{}, fmt.Errorf("not a WebDAV address")
	}
	rest = strings.TrimPrefix(rest, "/")
	if rest == "" {
		return davWhere{}, nil
	}
	parts := strings.Split(strings.TrimSuffix(rest, "/"), "/")
	clean := make([]string, 0, len(parts))
	for _, part := range parts {
		name, err := url.PathUnescape(part)
		if err != nil {
			return davWhere{}, fmt.Errorf("that address can't be read")
		}
		clean = append(clean, name)
	}
	return davWhereFrom(clean)
}

// davWhereFrom is where both of those end, so an address means the same thing
// however it arrived.
func davWhereFrom(parts []string) (davWhere, error) {
	for _, name := range parts {
		if name == "" || name == "." || name == ".." || strings.ContainsRune(name, '/') ||
			strings.ContainsRune(name, 0) {
			return davWhere{}, fmt.Errorf("that address can't be used")
		}
		if name == ".zfs" {
			// ZFS keeps its snapshots there, and they are read only through the
			// desktop, never written to by a client
			return davWhere{}, errHidden
		}
	}
	return davWhere{Share: parts[0], Inside: strings.Join(parts[1:], "/")}, nil
}

// davShares asks the helper which shares this person may use and which of them
// are on WebDAV, which is the whole of the permission question at the top.
func (s *Server) davShares(token string) ([]davShare, string) {
	response, err := s.Call(protocol.Request{Op: protocol.OpFileList, Session: token})
	if err != nil {
		log.Printf("webdav shares: %v", err)
		return nil, "The NAS's web helper isn't running."
	}
	if response.Code != "" {
		return nil, response.Error
	}
	var found []davShare
	if err := json.Unmarshal([]byte(response.Output), &found); err != nil {
		return nil, "The shares can't be listed right now."
	}
	shared := make([]davShare, 0, len(found))
	for _, one := range found {
		if one.Webdav {
			shared = append(shared, one)
		}
	}
	return shared, ""
}

// davFolderIn lists one folder as the person, through the same op the desktop
// uses, so a share they can't use is refused here in the same words.
func (s *Server) davFolderIn(token, share, inside string) (davFolder, string) {
	response, err := s.Call(protocol.Request{Op: protocol.OpFileList, Session: token,
		Share: share, Path: inside})
	if err != nil {
		log.Printf("webdav list: %v", err)
		return davFolder{}, "The NAS's web helper isn't running."
	}
	if response.Code != "" {
		return davFolder{}, response.Error
	}
	var found davFolder
	if err := json.Unmarshal([]byte(response.Output), &found); err != nil || found.Error != "" {
		if found.Error != "" {
			return davFolder{}, found.Error
		}
		return davFolder{}, "That folder can't be listed right now."
	}
	return found, ""
}

// davEntryAt finds one name in a folder, which is how a file's size and date are
// known without reading it.
func (s *Server) davEntryAt(token, share, inside string) (davEntry, bool, string) {
	parent := path.Dir(inside)
	if parent == "." || parent == "/" {
		parent = ""
	}
	folder, said := s.davFolderIn(token, share, parent)
	if said != "" {
		return davEntry{}, false, said
	}
	want := path.Base(inside)
	for _, entry := range folder.Entries {
		if entry.Name == want {
			return entry, true, ""
		}
	}
	return davEntry{}, false, ""
}

// davPropfind answers what is at an address, and with Depth: 1 what is in it.
func (s *Server) davPropfind(w http.ResponseWriter, r *http.Request, who davPerson, where davWhere) {
	token := who.token
	depth := r.Header.Get("Depth")
	if depth == "" {
		depth = "1"
	}
	if depth != "0" && depth != "1" {
		// Infinity would mean walking every folder in the share, which is a way
		// to tie up the disks; the answer says so rather than pretending
		w.Header().Set(contentType, "application/xml; charset=utf-8")
		w.WriteHeader(http.StatusForbidden)
		io.WriteString(w, `<?xml version="1.0" encoding="utf-8"?>
<D:error xmlns:D="DAV:"><D:propfind-finite-depth/></D:error>`)
		return
	}
	shared, said := s.davShares(token)
	if said != "" {
		davRefuse(w, said)
		return
	}
	var body strings.Builder
	body.WriteString(`<?xml version="1.0" encoding="utf-8"?>` + "\n" + `<D:multistatus xmlns:D="DAV:">` + "\n")
	if where.Share == "" {
		s.davWriteFolder(&body, "", "", "/dav/", "dav")
		if depth == "1" {
			for _, one := range shared {
				s.davWriteFolder(&body, one.Share, "", "/dav/"+davEscape(one.Share)+"/", one.Share)
			}
		}
		body.WriteString("</D:multistatus>")
		davSend(w, body.String())
		return
	}
	allowed := false
	for _, one := range shared {
		if one.Share == where.Share {
			allowed = true
		}
	}
	if !allowed {
		// Not shared over WebDAV, or not a share this person can use: the same
		// answer either way, so an address can't be used to find out which shares
		// other people have
		log.Printf("webdav: %s asked for %q, which is not theirs over WebDAV", where.Share, where.Inside)
		writeError(w, http.StatusNotFound, davNoSuch)
		return
	}
	if where.Inside == "" {
		folder, said := s.davFolderIn(token, where.Share, "")
		if said != "" {
			davRefuse(w, said)
			return
		}
		s.davWriteFolder(&body, where.Share, where.Inside, davHref(where, true), where.Share)
		if depth == "1" {
			for _, entry := range folder.Entries {
				s.davWriteEntry(&body, davChild(where, entry.Name), entry)
			}
		}
		body.WriteString("</D:multistatus>")
		davSend(w, body.String())
		return
	}
	entry, found, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if !found {
		writeError(w, http.StatusNotFound, davNoSuch)
		return
	}
	if !entry.Dir {
		s.davWriteEntry(&body, where, entry)
		body.WriteString("</D:multistatus>")
		davSend(w, body.String())
		return
	}
	folder, said := s.davFolderIn(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	s.davWriteEntry(&body, where, entry)
	if depth == "1" {
		for _, one := range folder.Entries {
			s.davWriteEntry(&body, davChild(where, one.Name), one)
		}
	}
	body.WriteString("</D:multistatus>")
	davSend(w, body.String())
}

// davGet sends a file's bytes, honouring Range, exactly as the desktop's
// download does: same op, same offsets, same refusals.
func (s *Server) davGet(w http.ResponseWriter, r *http.Request, who davPerson, where davWhere) {
	token := who.token
	if where.Share == "" || where.Inside == "" {
		w.Header().Set(contentType, "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, "<!doctype html><title>FellowshipNAS</title><p>This is the NAS's "+
			"WebDAV address. Point a WebDAV client at it, with your NAS user name and password.")
		return
	}
	shared, said := s.davShares(token)
	if said != "" {
		davRefuse(w, said)
		return
	}
	allowed := false
	for _, one := range shared {
		if one.Share == where.Share {
			allowed = true
		}
	}
	if !allowed {
		log.Printf("webdav: %s asked to read %q, which is not theirs over WebDAV", where.Share, where.Inside)
		writeError(w, http.StatusNotFound, davNoSuch)
		return
	}
	controller := http.NewResponseController(w)
	controller.SetReadDeadline(time.Time{})
	controller.SetWriteDeadline(time.Time{})
	request := protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: where.Share, Path: where.Inside}
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
		log.Printf("webdav read: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	if body != nil {
		defer body.Close()
	}
	if response.Code != "" {
		davRefuse(w, response.Error)
		return
	}
	header := w.Header()
	kind := contentTypeOf(where.Inside)
	header.Set(contentType, kind)
	downloadPolicy(header, kind)
	header.Set("Accept-Ranges", "bytes")
	if response.Tag != "" {
		header.Set("ETag", davTag(response.Tag))
	}
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
		log.Printf("webdav: sending %s: %v", where.Inside, err)
	}
}

// davRefuse turns the helper's own words into the closest WebDAV status, so a
// person's folder never reads as the server's fault.
func davRefuse(w http.ResponseWriter, said string) {
	status := http.StatusForbidden
	lowered := strings.ToLower(said)
	if strings.Contains(lowered, "isn't in ") && strings.Contains(lowered, " any more") ||
		strings.Contains(lowered, "no such file") {
		status = http.StatusNotFound
	}
	writeError(w, status, said)
}

// davSend writes a multistatus answer, which is XML whatever the client asked.
func davSend(w http.ResponseWriter, text string) {
	w.Header().Set(contentType, "application/xml; charset=utf-8")
	w.WriteHeader(http.StatusMultiStatus)
	io.WriteString(w, text)
}

// davWriteFolder writes one response for a folder.
func (s *Server) davWriteFolder(body *strings.Builder, share, inside, href, name string) {
	body.WriteString("<D:response><D:href>" + davEscape(href) + "</D:href><D:propstat><D:prop>")
	body.WriteString("<D:displayname>" + xmlEscape(name) + "</D:displayname>")
	body.WriteString("<D:resourcetype><D:collection/></D:resourcetype>")
	body.WriteString(s.davLockProps(share, inside))
	body.WriteString("</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>\n")
}

// davWriteEntry writes one response for one name, at its own address.
func (s *Server) davWriteEntry(body *strings.Builder, here davWhere, entry davEntry) {
	if entry.Dir {
		s.davWriteFolder(body, here.Share, here.Inside, davHref(here, true), entry.Name)
		return
	}
	body.WriteString("<D:response><D:href>" + davEscape(davHref(here, false)) + "</D:href><D:propstat><D:prop>")
	body.WriteString("<D:displayname>" + xmlEscape(entry.Name) + "</D:displayname>")
	body.WriteString("<D:resourcetype/>")
	body.WriteString("<D:getcontentlength>" + strconv.FormatInt(entry.Size, 10) + "</D:getcontentlength>")
	body.WriteString("<D:getcontenttype>" + xmlEscape(contentTypeOf(entry.Name)) + "</D:getcontenttype>")
	if when, err := time.Parse(time.RFC3339Nano, entry.Modified); err == nil && !when.IsZero() {
		body.WriteString("<D:getlastmodified>" + when.UTC().Format(http.TimeFormat) + "</D:getlastmodified>")
	}
	if entry.Tag != "" {
		body.WriteString("<D:getetag>" + xmlEscape(davTag(entry.Tag)) + "</D:getetag>")
	}
	body.WriteString(s.davLockProps(here.Share, here.Inside))
	body.WriteString("</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>\n")
}

// davChild is the address of a name inside a folder.
func davChild(folder davWhere, name string) davWhere {
	return davWhere{Share: folder.Share, Inside: path.Join(folder.Inside, name)}
}

// davHref is where a share or a path inside it is found, escaped a segment at a
// time, with the trailing slash a folder needs.
func davHref(where davWhere, folder bool) string {
	href := "/dav/" + davEscape(where.Share)
	if where.Inside != "" {
		for _, part := range strings.Split(where.Inside, "/") {
			href += "/" + davEscape(part)
		}
	}
	if folder {
		href += "/"
	}
	return href
}

// davEscape escapes one path segment, leaving the slashes of a whole href.
func davEscape(text string) string {
	return strings.ReplaceAll(url.PathEscape(text), "%2F", "/")
}

func xmlEscape(text string) string {
	var out bytes.Buffer
	xml.EscapeText(&out, []byte(text))
	return out.String()
}
