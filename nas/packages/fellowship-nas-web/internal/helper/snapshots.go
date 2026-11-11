// Snapshots: what a file looked like before, and putting one of those back.
//
// A snapshot's files are read by the helper as root, because ZFS keeps .zfs
// reachable only to root on many installs, so asking the person's own child to
// open them would fail on a working NAS. The boundary is kept elsewhere: the
// snapshot's name comes from fnas, never from the request; the path is resolved
// and confined against that snapshot's own folder; and the person must be able
// to list the folder as it is now, which is the same permission that would let
// them see the file today. Everything written back is written by the child, as
// the person, through the same path an upload takes.
package helper

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"fellowship-nas-web/internal/filechild"
	"fellowship-nas-web/internal/files"
	"fellowship-nas-web/internal/protocol"
)

// MaxSnapshots caps how many earlier versions one file may offer.
const MaxSnapshots = 200

type snapshot struct {
	Name    string `json:"name"`
	Created int64  `json:"created"`
	Size    int64  `json:"size"`
	Changed int64  `json:"changed"`
}

// snapshotsOf asks fnas which snapshots this share has, newest first.
func (h *Helper) snapshotsOf(share string) ([]snapshot, error) {
	stdout, stderr, err := h.config.Fnas([]string{"--json", "snapshot", "list", share}, "")
	if err != nil {
		text := stderr
		if len(text) == 0 {
			text = err.Error()
		}
		return nil, errors.New(strings.TrimSpace(text))
	}
	var found []snapshot
	if err := json.Unmarshal([]byte(stdout), &found); err != nil {
		return nil, fmt.Errorf("fnas gave an answer the helper couldn't read: %w", err)
	}
	return found, nil
}

// snapshotRoot is where one snapshot keeps this share's files.
func snapshotRoot(root files.Root, name string) files.Root {
	return files.Root{Share: root.Share, Path: filepath.Join(root.Path, ".zfs", "snapshot", name)}
}

// reachable is the permission check that stands in for reading as the person:
// they must be able to list the folder the file is in, as it is now.
func (h *Helper) reachable(account Account, root files.Root, path string) string {
	relative := strings.TrimPrefix(filepath.ToSlash(path), "/")
	parent := ""
	if cut := strings.LastIndex(relative, "/"); cut >= 0 {
		parent = relative[:cut]
	}
	// The folder has to exist, so versions of a file that has since gone can
	// still be looked at and brought back
	folder, err := files.Resolve([]files.Root{root}, root.Share, parent)
	if err != nil {
		return err.Error()
	}
	out, err := h.child(account, nil, "list", root, folder, 0, 0)
	if err != nil {
		return "that folder can't be opened right now"
	}
	defer out.Close()
	var listed struct {
		Error string `json:"error"`
		filechild.Folder
	}
	if err := json.NewDecoder(io.LimitReader(out, 8<<20)).Decode(&listed); err != nil {
		return "that folder can't be opened right now"
	}
	return listed.Error
}

// namedSnapshot checks a snapshot name against fnas's list for this share.
func (h *Helper) namedSnapshot(share, name string) (snapshot, error) {
	found, err := h.snapshotsOf(share)
	if err != nil {
		return snapshot{}, err
	}
	for _, one := range found {
		if one.Name == name {
			return one, nil
		}
	}
	return snapshot{}, fmt.Errorf("there is no snapshot called %s", name)
}

// fileSnapshots answers which snapshots hold this file, newest first.
func (h *Helper) fileSnapshots(user string, request protocol.Request) protocol.Response {
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	if said := h.reachable(account, root, request.Path); said != "" {
		return failure(protocol.CodeForbidden, said)
	}
	found, err := h.snapshotsOf(request.Share)
	if err != nil {
		return failure(protocol.CodeFailed, err.Error())
	}
	held := make([]snapshot, 0, len(found))
	for _, one := range found {
		if len(held) == MaxSnapshots {
			break
		}
		where := snapshotRoot(root, one.Name)
		kept, err := files.Resolve([]files.Root{where}, root.Share, request.Path)
		if err != nil {
			continue
		}
		info, err := os.Stat(kept)
		if err != nil || info.IsDir() {
			continue
		}
		held = append(held, snapshot{Name: one.Name, Created: one.Created,
			Size: info.Size(), Changed: info.ModTime().Unix()})
	}
	answer, err := json.Marshal(held)
	if err != nil {
		return failure(protocol.CodeFailed, "the earlier versions can't be listed right now")
	}
	return protocol.Response{Output: string(answer)}
}

// openSnapshot opens one earlier version of a file, as root, having checked the
// person may see the folder it lives in now.
func (h *Helper) openSnapshot(user string, request protocol.Request) (*os.File, snapshot, error) {
	root, err := h.fileRoots(user, request.Share)
	if err != nil {
		return nil, snapshot{}, err
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return nil, snapshot{}, fmt.Errorf("your account can't be read right now")
	}
	if request.Snapshot == "" {
		return nil, snapshot{}, fmt.Errorf("say which earlier version")
	}
	one, err := h.namedSnapshot(request.Share, request.Snapshot)
	if err != nil {
		return nil, snapshot{}, err
	}
	if said := h.reachable(account, root, request.Path); said != "" {
		return nil, snapshot{}, errors.New(said)
	}
	where := snapshotRoot(root, one.Name)
	if _, err := os.Stat(where.Path); err != nil {
		return nil, snapshot{}, fmt.Errorf("%s has no copy of that file", one.Name)
	}
	found, err := files.Resolve([]files.Root{where}, root.Share, request.Path)
	if err != nil {
		return nil, snapshot{}, err
	}
	file, err := os.OpenFile(found, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, snapshot{}, fmt.Errorf("that earlier version can't be opened")
	}
	return file, one, nil
}

// readSnapshot answers a download of an earlier version.
func (h *Helper) readSnapshot(user string, request protocol.Request) (protocol.Response, io.ReadCloser) {
	file, _, err := h.openSnapshot(user, request)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error()), nil
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return failure(protocol.CodeFailed, "that earlier version can't be opened"), nil
	}
	if info.IsDir() {
		file.Close()
		return failure(protocol.CodeBadRequest, "that is a folder, not a file"), nil
	}
	start := request.Start
	if start < 0 || start > info.Size() {
		file.Close()
		return failure(protocol.CodeBadRequest, "that part of the file isn't there"), nil
	}
	length := request.Length
	if length <= 0 || start+length > info.Size() {
		length = info.Size() - start
	}
	if _, err := file.Seek(start, io.SeekStart); err != nil {
		file.Close()
		return failure(protocol.CodeFailed, "that earlier version can't be opened"), nil
	}
	return protocol.Response{Size: info.Size(), Length: length},
		struct {
			io.Reader
			io.Closer
		}{io.LimitReader(file, length), file}
}

// fileRestore puts an earlier version back as a new file, or over the one there
// now when that was asked for and confirmed. The writing is the child's, as the
// person, exactly as an upload is.
func (h *Helper) fileRestore(user string, request protocol.Request) protocol.Response {
	file, _, err := h.openSnapshot(user, request)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	defer file.Close()
	root, err := h.writable(user, request.Share)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	target := request.To
	if target == "" {
		return failure(protocol.CodeBadRequest, "say what to call the restored copy")
	}
	path, err := files.ResolveNew([]files.Root{root}, request.Share, target)
	if err != nil {
		return failure(protocol.CodeForbidden, err.Error())
	}
	account, err := h.config.Lookup(user)
	if err != nil {
		return failure(protocol.CodeFailed, "your account can't be read right now")
	}
	out, err := h.child(account, file, "write", root, path, 0, 0)
	if err != nil {
		log.Printf("restoring %s for %s: %v", target, user, err)
		return failure(protocol.CodeFailed, "that copy can't be written right now")
	}
	defer out.Close()
	if said := childSaid(out); said != "" {
		return failure(protocol.CodeFailed, said)
	}
	audit(user, "restore", request.Share, request.Path+" -> "+target)
	return protocol.Response{}
}
