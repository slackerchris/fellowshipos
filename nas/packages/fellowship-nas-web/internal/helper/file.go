// File ops: what the desktop's Files app and, later, WebDAV ask the helper for.
// The person comes from the session, never from the request, and the work is
// done by a child running as that person, so the kernel agrees with the answer.
package helper

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"path"
	"strings"
	"time"

	"fellowship-nas-web/internal/filechild"
	"fellowship-nas-web/internal/files"
	"fellowship-nas-web/internal/protocol"
)

// rootResponse is what `fnas share roots USER` prints: the shares a person can
// use and where each one is.
type rootResponse struct {
	Share     string `json:"share"`
	Path      string `json:"path"`
	Level     string `json:"level"`
	Through   string `json:"through"`
	Snapshots bool   `json:"snapshots"`
}

// RootsFor is how long a person's shares are remembered. A change to who may
// use a share reaches Files within this, and a folder of thumbnails costs one
// fnas run rather than one a picture.
const RootsFor = 5 * time.Second

type knownRoots struct {
	at    time.Time
	roots []files.Root
	found []rootResponse
}

// roots asks fnas for the shares this person can use. The helper runs fnas
// itself, so the desktop never names a path on disk.
func (h *Helper) roots(user string) ([]files.Root, []rootResponse, error) {
	now := h.config.Now()
	h.rootsMu.Lock()
	kept, ok := h.known[user]
	h.rootsMu.Unlock()
	if ok && now.Sub(kept.at) < RootsFor && now.Sub(kept.at) >= 0 {
		return kept.roots, kept.found, nil
	}
	stdout, stderr, err := h.config.Fnas([]string{"--json", "share", "roots", user}, "")
	if err != nil {
		text := stderr
		if len(text) == 0 {
			text = err.Error()
		}
		return nil, nil, errors.New(text)
	}
	var found []rootResponse
	if err := json.Unmarshal([]byte(stdout), &found); err != nil {
		return nil, nil, fmt.Errorf("fnas gave an answer the helper couldn't read: %w", err)
	}
	roots := make([]files.Root, 0, len(found))
	for _, root := range found {
		roots = append(roots, files.Root{Share: root.Share, Path: root.Path})
	}
	h.rootsMu.Lock()
	h.known[user] = knownRoots{at: now, roots: roots, found: found}
	h.rootsMu.Unlock()
	return roots, found, nil
}

// fileRoots is roots with the share a request named checked against them.
func (h *Helper) fileRoots(user, share string) (files.Root, error) {
	roots, _, err := h.roots(user)
	if err != nil {
		return files.Root{}, errors.New("the shares can't be listed right now")
	}
	return files.Find(roots, share)
}

// shareItem is what the desktop is told about a share: its name and what the
// person may do with it. Where it is on disk stays on the NAS.
type shareItem struct {
	Share     string `json:"share"`
	Level     string `json:"level"`
	Through   string `json:"through"`
	Snapshots bool   `json:"snapshots"`
}

// listShares answers what the person may browse, for the Files app's first
// screen: no paths at all, only names and how they may use them.
func (h *Helper) listShares(user string) protocol.Response {
	_, found, err := h.roots(user)
	if err != nil {
		return failure(protocol.CodeFailed, err.Error())
	}
	items := make([]shareItem, 0, len(found))
	for _, root := range found {
		items = append(items, shareItem{Share: root.Share, Level: root.Level,
			Through: root.Through, Snapshots: root.Snapshots})
	}
	out, err := json.Marshal(items)
	if err != nil {
		return failure(protocol.CodeFailed, "the shares can't be listed right now")
	}
	return protocol.Response{Output: string(out)}
}

// fileList answers with what a folder holds.
func (h *Helper) fileList(user string, request protocol.Request) protocol.Response {
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	path, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	out, err := h.child(account, nil, "list", root, path, 0, 0)
	if err != nil {
		log.Printf("listing %s for %s: %v", path, user, err)
		return failure(protocol.CodeFailed, "that folder can't be listed right now")
	}
	defer out.Close()
	// The child answers with the folder, or says why it won't
	var listed struct {
		Error string `json:"error"`
		filechild.Folder
	}
	if err := json.NewDecoder(io.LimitReader(out, 8<<20)).Decode(&listed); err != nil {
		return failure(protocol.CodeFailed, "that folder can't be listed right now")
	}
	if listed.Error != "" {
		return failure(protocol.CodeFailed, listed.Error)
	}
	folder := filechild.Folder{Entries: listed.Entries}
	answer := protocol.Response{}
	folderText, err := json.Marshal(folder)
	if err != nil {
		return failure(protocol.CodeFailed, "that folder can't be listed right now")
	}
	answer.Output = string(folderText)
	return answer
}

// fileRead answers with the head of the file, leaving the bytes to follow.
func (h *Helper) fileRead(user string, request protocol.Request) (protocol.Response, io.ReadCloser) {
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error()), nil
	}
	path, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error()), nil
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now"), nil
	}
	out, err := h.child(account, nil, "read", root, path, request.Start, request.Length)
	if err != nil {
		log.Printf("reading %s for %s: %v", path, user, err)
		return failure(protocol.CodeFailed, "that file can't be opened right now"), nil
	}
	return headed(out, "that file can't be opened right now")
}

// MaxZipNames is how many things one zip may be asked for by name; a folder among
// them brings what it holds, which the child counts against its own size cap.
const MaxZipNames = 1000

// fileZip answers with a zip of several things in one folder, made by the child
// as the person while it is sent. Every name is checked before any work starts.
func (h *Helper) fileZip(user string, request protocol.Request) (protocol.Response, io.ReadCloser) {
	if len(request.Names) == 0 {
		return failure(protocol.CodeBadRequest, "say what goes in the zip"), nil
	}
	if len(request.Names) > MaxZipNames {
		return failure(protocol.CodeBadRequest, fmt.Sprintf("a zip takes at most %d things at once", MaxZipNames)), nil
	}
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error()), nil
	}
	folder, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error()), nil
	}
	for _, name := range request.Names {
		// Names in the folder, never a path: the folder is the only path, and it is resolved above
		if name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\x00") {
			return failure(protocol.CodeForbidden, "that name can't be used"), nil
		}
		if _, err := files.Resolve([]files.Root{root}, request.Share, path.Join(request.Path, name)); err != nil {
			return failure(protocol.CodeForbidden, err.Error()), nil
		}
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now"), nil
	}
	out, err := h.child(account, nil, "zip", root, folder, 0, 0, request.Names...)
	if err != nil {
		log.Printf("zipping in %s for %s: %v", folder, user, err)
		return failure(protocol.CodeFailed, "the zip can't be made right now"), nil
	}
	response, body := headed(out, "the zip can't be made right now")
	if response.Code == "" {
		// The zip's size is only known once it is made, so it runs to the end
		response.Length = protocol.ToTheEnd
	}
	return response, body
}

// headed reads the child's one line of JSON, which says how big what follows is
// or why there is nothing, and hands back the bytes after it.
func headed(out io.ReadCloser, failed string) (protocol.Response, io.ReadCloser) {
	body := bufio.NewReaderSize(out, 64<<10)
	line, err := body.ReadBytes('\n')
	if err != nil {
		out.Close()
		return failure(protocol.CodeFailed, failed), nil
	}
	// The child answers with the size, or says why it won't
	var head struct {
		Error string `json:"error"`
		filechild.Head
	}
	if err := json.Unmarshal(line, &head); err != nil {
		out.Close()
		return failure(protocol.CodeFailed, failed), nil
	}
	if head.Error != "" {
		out.Close()
		return failure(protocol.CodeFailed, head.Error), nil
	}
	return protocol.Response{Size: head.Size, Length: head.Length},
		struct {
			io.Reader
			io.Closer
		}{body, out}
}

// Stream is Handle for the ops that carry bytes either way: a read answers with
// the file after its answer, a write arrives with the file after its request.
// The caller must close the reader.
func (h *Helper) Stream(request protocol.Request, body io.Reader) (protocol.Response, io.ReadCloser) {
	switch request.Op {
	case protocol.OpFileWrite:
		found := h.find(request.Session, request.Touch)
		if found == nil {
			return failure(protocol.CodeUnauthenticated, signedOut), nil
		}
		return h.fileWrite(found.user, request, body), nil
	case protocol.OpFileRead, protocol.OpFileZip:
	default:
		return h.Handle(request), nil
	}
	found := h.find(request.Session, request.Touch)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut), nil
	}
	if request.Op == protocol.OpFileZip {
		return h.fileZip(found.user, request)
	}
	return h.fileRead(found.user, request)
}

// MaxUpload is the most one file may be, per the plan's 10 GB transfer.
const MaxUpload = 10 << 30

// writable is the root for a share the person may also change.
func (h *Helper) writable(user, share string) (files.Root, error) {
	roots, found, err := h.roots(user)
	if err != nil {
		return files.Root{}, errors.New("the shares can't be listed right now")
	}
	root, err := files.Find(roots, share)
	if err != nil {
		return files.Root{}, err
	}
	for _, one := range found {
		if one.Share == share && one.Level != "write" {
			return files.Root{}, fmt.Errorf("%s is read only for you", share)
		}
	}
	return root, nil
}

// fileWrite receives an upload and puts it in place as the person.
func (h *Helper) fileWrite(user string, request protocol.Request, body io.Reader) protocol.Response {
	root, err := h.writable(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	var path string
	if request.Replace {
		path, err = files.Resolve([]files.Root{root}, request.Share, request.Path)
	} else {
		path, err = files.ResolveNew([]files.Root{root}, request.Share, request.Path)
	}
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	if body == nil {
		return failure(protocol.CodeBadRequest, "no file arrived")
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	// One byte over the cap is enough to know the file is too big
	counting := &counted{reader: io.LimitReader(body, MaxUpload+1)}
	var extra []string
	if request.Replace {
		extra = []string{"replace"}
	}
	out, err := h.child(account, counting, "write", root, path, 0, 0, extra...)
	if err != nil {
		log.Printf("writing %s for %s: %v", path, user, err)
		return failure(protocol.CodeFailed, "that file can't be written right now")
	}
	defer out.Close()
	if answer := childSaid(out); answer != "" {
		return failure(protocol.CodeFailed, answer)
	}
	if counting.wrote > MaxUpload {
		return failure(protocol.CodeBadRequest, "that file is larger than the 10 GB this allows")
	}
	return protocol.Response{Size: counting.wrote}
}

// counted remembers how much went through it, so a file over the cap is caught
// without being kept anywhere.
type counted struct {
	reader io.Reader
	wrote  int64
}

func (c *counted) Read(into []byte) (int, error) {
	taken, err := c.reader.Read(into)
	c.wrote += int64(taken)
	return taken, err
}

// fileMkdir makes a folder in a share the person can change.
func (h *Helper) fileMkdir(user string, request protocol.Request) protocol.Response {
	return h.change(user, request, "that folder can't be made right now", func(root files.Root) (string, error) {
		return files.ResolveNew([]files.Root{root}, request.Share, request.Path)
	}, []string{"mkdir"})
}

// fileRename moves or renames something inside one share. Moving between shares
// would be a copy and a delete, and is not offered yet.
func (h *Helper) fileRename(user string, request protocol.Request) protocol.Response {
	root, err := h.writable(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	from, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	to, err := files.ResolveNew([]files.Root{root}, request.Share, request.To)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	_, toInside, err := files.Relative(root, to)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	out, err := h.child(account, nil, "rename", root, from, 0, 0, toInside)
	if err != nil {
		return failure(protocol.CodeFailed, "that can't be renamed right now")
	}
	defer out.Close()
	if answer := childSaid(out); answer != "" {
		return failure(protocol.CodeFailed, answer)
	}
	return protocol.Response{}
}

// fileCopy copies a file, or a folder with everything in it, to a new name in the
// same share. Copying between shares would mean two roots, and is not offered.
func (h *Helper) fileCopy(user string, request protocol.Request) protocol.Response {
	root, err := h.writable(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	from, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	to, err := files.ResolveNew([]files.Root{root}, request.Share, request.To)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	_, fromInside, err := files.Relative(root, from)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	_, toInside, err := files.Relative(root, to)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	if toInside == fromInside || strings.HasPrefix(toInside, fromInside+"/") {
		return failure(protocol.CodeForbidden, "a folder can't be copied into itself")
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	out, err := h.child(account, nil, "copy", root, from, 0, 0, toInside)
	if err != nil {
		return failure(protocol.CodeFailed, "that can't be copied right now")
	}
	defer out.Close()
	if answer := childSaid(out); answer != "" {
		return failure(protocol.CodeFailed, answer)
	}
	return protocol.Response{}
}

// fileTree answers with the folders beneath one, for picking where something goes.
// Reading is enough: the move itself checks the share can be changed.
func (h *Helper) fileTree(user string, request protocol.Request) protocol.Response {
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	folder, err := files.Resolve([]files.Root{root}, request.Share, request.Path)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	out, err := h.child(account, nil, "tree", root, folder, 0, 0)
	if err != nil {
		return failure(protocol.CodeFailed, "the folders can't be listed right now")
	}
	defer out.Close()
	var answer struct {
		Error string `json:"error"`
		filechild.Tree
	}
	if err := json.NewDecoder(io.LimitReader(out, 8<<20)).Decode(&answer); err != nil {
		return failure(protocol.CodeFailed, "the folders can't be listed right now")
	}
	if answer.Error != "" {
		return failure(protocol.CodeFailed, answer.Error)
	}
	text, err := json.Marshal(answer.Tree)
	if err != nil {
		return failure(protocol.CodeFailed, "the folders can't be listed right now")
	}
	return protocol.Response{Output: string(text)}
}

// fileDelete removes something, taking a folder's contents with it only when
// that was asked for.
func (h *Helper) fileDelete(user string, request protocol.Request) protocol.Response {
	whole := "keep"
	if request.Recursive {
		whole = "tree"
	}
	return h.change(user, request, "that can't be removed right now", func(root files.Root) (string, error) {
		return files.Resolve([]files.Root{root}, request.Share, request.Path)
	}, []string{"remove", whole})
}

// change is the shape mkdir and delete share: check the share can be written,
// work out the path, then let the child do it and keep its words if it refuses.
func (h *Helper) change(user string, request protocol.Request, failed string,
	path func(root files.Root) (string, error), command []string) protocol.Response {
	root, err := h.writable(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	found, err := path(root)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	out, err := h.child(account, nil, command[0], root, found, 0, 0, command[1:]...)
	if err != nil {
		log.Printf("%s for %s: %v", command[0], user, err)
		return failure(protocol.CodeFailed, failed)
	}
	defer out.Close()
	if answer := childSaid(out); answer != "" {
		return failure(protocol.CodeFailed, answer)
	}
	return protocol.Response{}
}

// child runs a file operation as the person on a path Resolve or ResolveNew
// answered, handing over the share's real folder and the path inside it, so the
// child opens it beneath that folder rather than trusting a path made earlier.
func (h *Helper) child(account Account, body io.Reader, op string, root files.Root, path string,
	start, length int64, extra ...string) (io.ReadCloser, error) {
	real, inside, err := files.Relative(root, path)
	if err != nil {
		return nil, err
	}
	return h.config.Child(account, body, op, real, inside, start, length, extra...)
}

// childSaid reads the one line of JSON the child answers with, returning what it
// complained about, if anything.
// Every child that finishes says so, so no answer at all means it stopped part
// way, which is reported rather than taken for success.
func childSaid(out io.Reader) string {
	var answer struct {
		Error string `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(out, 64<<10)).Decode(&answer); err != nil {
		return "that stopped before it finished"
	}
	return answer.Error
}
