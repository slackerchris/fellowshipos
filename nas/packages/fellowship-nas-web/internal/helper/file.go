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

// roots asks fnas for the shares this person can use. The helper runs fnas
// itself, so the desktop never names a path on disk.
func (h *Helper) roots(user string) ([]files.Root, []rootResponse, error) {
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
	out, err := h.config.Child(account, nil, "list", path, 0, 0)
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
	out, err := h.config.Child(account, nil, "read", path, request.Start, request.Length)
	if err != nil {
		log.Printf("reading %s for %s: %v", path, user, err)
		return failure(protocol.CodeFailed, "that file can't be opened right now"), nil
	}
	body := bufio.NewReaderSize(out, 64<<10)
	line, err := body.ReadBytes('\n')
	if err != nil {
		out.Close()
		return failure(protocol.CodeFailed, "that file can't be opened right now"), nil
	}
	// The child answers with the size, or says why it won't
	var head struct {
		Error string `json:"error"`
		filechild.Head
	}
	if err := json.Unmarshal(line, &head); err != nil {
		out.Close()
		return failure(protocol.CodeFailed, "that file can't be opened right now"), nil
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
	case protocol.OpFileRead:
	default:
		return h.Handle(request), nil
	}
	found := h.find(request.Session, request.Touch)
	if found == nil {
		return failure(protocol.CodeUnauthenticated, signedOut), nil
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
	out, err := h.config.Child(account, counting, "write", path, 0, 0, extra...)
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
	}, func(path string) []string { return []string{"mkdir", path} })
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
	out, err := h.config.Child(account, nil, "rename", from, 0, 0, to)
	if err != nil {
		return failure(protocol.CodeFailed, "that can't be renamed right now")
	}
	defer out.Close()
	if answer := childSaid(out); answer != "" {
		return failure(protocol.CodeFailed, answer)
	}
	return protocol.Response{}
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
	}, func(path string) []string { return []string{"remove", path, whole} })
}

// change is the shape mkdir and delete share: check the share can be written,
// work out the path, then let the child do it and keep its words if it refuses.
func (h *Helper) change(user string, request protocol.Request, failed string,
	path func(root files.Root) (string, error), args func(path string) []string) protocol.Response {
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
	command := args(found)
	out, err := h.config.Child(account, nil, command[0], found, 0, 0, command[1:]...)
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

// childSaid reads the one line of JSON the child answers with, returning what it
// complained about, if anything.
func childSaid(out io.Reader) string {
	var answer struct {
		Error string `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(out, 64<<10)).Decode(&answer); err != nil {
		return ""
	}
	return answer.Error
}
