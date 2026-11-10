// The writing half of WebDAV: PUT, MKCOL, DELETE, MOVE and COPY.
//
// Every one of them is carried out by the helper as the person, through the same
// ops the desktop's Files app uses, so the kernel decides what may be written
// exactly as it does there. What this file adds is the WebDAV manners: which
// address may be written to, what to answer when a file has changed under a
// client's feet, and the fact that nothing overwrites something already there
// without being asked twice.
package front

import (
	"errors"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"

	"fellowship-nas-web/internal/protocol"
)

// errHidden is a path that reaches ZFS's own snapshot folder. It is not part of
// what a share offers over WebDAV, and mounting it would let a client edit what
// an earlier version holds.
var errHidden = errors.New(davHidden)

// davOpen answers whether this person may reach this address, and with changing
// set whether they may change it. It writes the refusal itself and returns it,
// so a handler only carries on when said is empty.
func (s *Server) davOpen(w http.ResponseWriter, token, share, inside string, changing bool) (davShare, bool) {
	if share == "" || (changing && inside == "") {
		writeError(w, http.StatusBadRequest, davNoTarget)
		return davShare{}, false
	}
	one, found, said := s.davShareFor(token, share)
	if said != "" {
		writeError(w, http.StatusForbidden, said)
		return davShare{}, false
	}
	if !found {
		log.Printf("webdav: %s asked to change %q, which is not theirs over WebDAV", share, inside)
		writeError(w, http.StatusNotFound, davNoSuch)
		return davShare{}, false
	}
	if changing && one.Level != "write" {
		writeError(w, http.StatusForbidden, share+" is read only for you")
		return davShare{}, false
	}
	return one, true
}

// davShareFor finds one share in what this person may use over WebDAV.
func (s *Server) davShareFor(token, share string) (davShare, bool, string) {
	shared, said := s.davShares(token)
	if said != "" {
		return davShare{}, false, said
	}
	for _, one := range shared {
		if one.Share == share {
			return one, true, ""
		}
	}
	return davShare{}, false, ""
}

// davPut writes a whole file, or replaces one, and only if the client is
// replacing the version it read when it sent If-Match.
func (s *Server) davPut(w http.ResponseWriter, r *http.Request, token string, where davWhere) {
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	if r.Header.Get("Content-Range") != "" {
		// A part of a file would mean writing into the middle of what is there,
		// which the helper's write does not do: it makes the file, then renames it
		writeError(w, http.StatusBadRequest, "writing part of a file isn't supported; send the whole file")
		return
	}
	entry, exists, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if exists && entry.Dir {
		writeError(w, http.StatusConflict, "that is a folder, so it can't be written over")
		return
	}
	want, anyVersion, ok := davIfMatch(w, r)
	if !ok {
		return
	}
	switch {
	case anyVersion && !exists:
		writeError(w, http.StatusPreconditionFailed, "that file isn't here any more")
		return
	case want != "" && (!exists || want != entry.Tag):
		writeError(w, http.StatusPreconditionFailed,
			"that file has changed since you read it, so nothing was written")
		return
	}
	if r.Header.Get("If-None-Match") == "*" && exists {
		writeError(w, http.StatusPreconditionFailed, "there is already a file with that name")
		return
	}
	controller := http.NewResponseController(w)
	controller.SetReadDeadline(time.Time{})
	controller.SetWriteDeadline(time.Time{})
	request := protocol.Request{Op: protocol.OpFileWrite, Session: token,
		Share: where.Share, Path: where.Inside, Replace: exists}
	if want != "" && !anyVersion {
		// The child checks this again as it commits, under a lock on the folder,
		// so two clients who read the same version can't both replace it
		request.IfMatch = want
	}
	request.New = r.Header.Get("If-None-Match") == "*"
	send := s.Send
	if send == nil {
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	response, err := send(request, r.Body)
	if err != nil {
		log.Printf("webdav put: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	if response.Code != "" {
		davWriteFailure(w, response)
		return
	}
	if response.Tag != "" {
		w.Header().Set("ETag", davTag(response.Tag))
	}
	if exists {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

// davIfMatch reads the If-Match header, which names the version the client
// believes it is replacing. A weak tag can never match here, since a strong
// comparison is what makes "the file I read" mean anything.
func davIfMatch(w http.ResponseWriter, r *http.Request) (tag string, anyVersion bool, ok bool) {
	header := strings.TrimSpace(r.Header.Get("If-Match"))
	if header == "" {
		return "", false, true
	}
	for _, one := range strings.Split(header, ",") {
		one = strings.TrimSpace(one)
		if one == "*" {
			return "", true, true
		}
		if strings.HasPrefix(one, "W/") {
			// A weak tag says "the same" in a loose sense, and a write can't rest
			// on that
			writeError(w, http.StatusPreconditionFailed,
				"a weak ETag can't be used to replace a file; read it again")
			return "", false, false
		}
		tag = davUnquote(one)
	}
	return tag, false, true
}

// davTag wraps a version in the quotes an ETag has.
func davTag(tag string) string { return `"` + tag + `"` }

// davUnquote takes the quotes off an ETag a client sent.
func davUnquote(tag string) string {
	return strings.Trim(tag, `"`)
}

// davChange asks the helper for one change and answers its refusal in the terms a
// client expects, rather than in the desktop's.
func (s *Server) davChange(w http.ResponseWriter, request protocol.Request) (protocol.Response, bool) {
	response, err := s.Call(request)
	if err != nil {
		log.Printf("webdav change: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return response, false
	}
	if response.Code != "" {
		davWriteFailure(w, response)
		return response, false
	}
	return response, true
}

// davWriteFailure answers a refusal about a change. Something the kernel says no
// to is a 403 whatever the helper called it, since a client asks a different
// question of "you may not" than of "the server went wrong".
func davWriteFailure(w http.ResponseWriter, response protocol.Response) {
	if response.Code == protocol.CodeFailed {
		lowered := strings.ToLower(response.Error)
		for _, said := range []string{"permission", "denied", "read only", "not permitted"} {
			if strings.Contains(lowered, said) {
				writeError(w, http.StatusForbidden, response.Error)
				return
			}
		}
	}
	writeFailure(w, response)
}

// davMkcol makes one folder. WebDAV says a name that is already taken is not a
// conflict to retry but a request that can't be met.
func (s *Server) davMkcol(w http.ResponseWriter, r *http.Request, token string, where davWhere) {
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	_, exists, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if exists {
		writeError(w, http.StatusMethodNotAllowed, "there is already something with that name")
		return
	}
	response, err := s.Call(protocol.Request{Op: protocol.OpFileMkdir, Session: token,
		Share: where.Share, Path: where.Inside})
	if err != nil {
		log.Printf("webdav mkcol: %v", err)
		writeError(w, http.StatusServiceUnavailable, noHelper)
		return
	}
	if response.Code != "" {
		// Made between the look and the ask: WebDAV has one answer for a name that
		// is taken, and it is not "conflict, try again"
		if strings.Contains(strings.ToLower(response.Error), "already here") {
			writeError(w, http.StatusMethodNotAllowed, "there is already something with that name")
			return
		}
		davWriteFailure(w, response)
		return
	}
	w.WriteHeader(http.StatusCreated)
}

// davDelete removes one file, or a folder and everything in it.
func (s *Server) davDelete(w http.ResponseWriter, r *http.Request, token string, where davWhere) {
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	entry, exists, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if !exists {
		writeError(w, http.StatusNotFound, davNoSuch)
		return
	}
	if entry.Dir && r.Header.Get("Depth") == "0" {
		writeError(w, http.StatusBadRequest, "say Depth: infinity to delete a folder and what is in it")
		return
	}
	if _, ok := s.davChange(w, protocol.Request{Op: protocol.OpFileDelete, Session: token,
		Share: where.Share, Path: where.Inside, Recursive: entry.Dir}); !ok {
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// davMoveCopy renames or copies one thing to another address in the same share.
// Both ends are checked before anything is done: the source has to be theirs to
// read, the share has to be theirs to write, and the address it is going to has
// to be free.
func (s *Server) davMoveCopy(w http.ResponseWriter, r *http.Request, token string, where davWhere) {
	if _, ok := s.davOpen(w, token, where.Share, where.Inside, true); !ok {
		return
	}
	entry, exists, said := s.davEntryAt(token, where.Share, where.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if !exists {
		writeError(w, http.StatusNotFound, davNoSuch)
		return
	}
	dest, err := davDestination(r)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, errHidden) {
			status = http.StatusForbidden
		}
		writeError(w, status, err.Error())
		return
	}
	if dest.Share != where.Share {
		// The helper's move and copy work inside one share, and a share is the
		// unit permissions are given on, so this is not something to guess at
		writeError(w, http.StatusBadGateway,
			"a move between shares isn't supported over WebDAV; copy it in Files instead")
		return
	}
	if dest.Inside == "" {
		writeError(w, http.StatusBadRequest, davNoTarget)
		return
	}
	_, taken, said := s.davEntryAt(token, dest.Share, dest.Inside)
	if said != "" {
		davRefuse(w, said)
		return
	}
	if taken {
		// Overwriting is what the desktop makes people confirm by typing a name,
		// so a client has to say so by deleting first
		writeError(w, http.StatusPreconditionFailed,
			"there is already something at that address; delete it first if you mean to replace it")
		return
	}
	// A move only needs the folders, so the kernel alone would let a person shift
	// a file they cannot even open. Over WebDAV the source has to be theirs to
	// read, which is the same question as "could you download it". This comes
	// after the addresses are checked, so a move that could never work touches
	// nothing.
	if !entry.Dir {
		if said := s.davReadable(token, where); said != "" {
			davRefuse(w, said)
			return
		}
	}
	op := protocol.OpFileCopy
	if r.Method == "MOVE" {
		op = protocol.OpFileRename
	}
	if _, ok := s.davChange(w, protocol.Request{Op: op, Session: token, Share: where.Share,
		Path: where.Inside, To: dest.Inside}); !ok {
		return
	}
	w.WriteHeader(http.StatusCreated)
}

// davReadable asks the helper for the first byte of a file, which is how a
// person's own permissions answer "may they read this at all".
func (s *Server) davReadable(token string, where davWhere) string {
	stream := s.Stream
	if stream == nil {
		return noHelper
	}
	response, body, err := stream(protocol.Request{Op: protocol.OpFileRead, Session: token,
		Share: where.Share, Path: where.Inside, Length: 1})
	if body != nil {
		body.Close()
	}
	if err != nil {
		log.Printf("webdav read check: %v", err)
		return "The NAS's web helper isn't running."
	}
	return response.Error
}

// davDestination reads where a MOVE or COPY is going, in the same shape as the
// address itself, with the same refusals.
func davDestination(r *http.Request) (davWhere, error) {
	header := strings.TrimSpace(r.Header.Get("Destination"))
	if header == "" {
		return davWhere{}, errors.New("a move or copy has to say where it is going in Destination")
	}
	parsed, err := url.Parse(header)
	if err != nil {
		return davWhere{}, errors.New("the Destination address can't be read")
	}
	return davWhereOf(parsed.Path)
}
