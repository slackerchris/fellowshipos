// Package filechild is the small program the helper re-executes to touch a
// file, already running as the person who asked. The helper resolved the path
// first, so it arrives as the share's real folder and a path inside it with no
// links in it. This opens the folder and then the path beneath it with openat2,
// which the kernel refuses if the path leaves the folder or meets a link: a
// folder swapped for a link since the helper looked can't lead anywhere else.
//
// It speaks one line of JSON to stdout, then, for a read, the bytes. Every
// operation that succeeds says so, so silence means it didn't finish.
package filechild

import (
	"archive/zip"
	"compress/flate"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// openat2 and what it is asked to refuse, from linux/openat2.h. The syscall has
// the same number on every architecture Linux added it for.
const (
	sysOpenat2          = 437
	resolveNoMagiclinks = 0x02
	resolveNoSymlinks   = 0x04
	resolveBeneath      = 0x08
	// O_PATH, which the syscall package leaves out: open a folder only to work
	// beneath it. The value is the same on every architecture Debian builds for
	oPath = 0o10000000
)

type openHow struct {
	flags, mode, resolve uint64
}

// Entry is one thing in a folder, as the desktop lists it.
type Entry struct {
	Name     string    `json:"name"`
	Dir      bool      `json:"dir"`
	Size     int64     `json:"size"`
	Modified time.Time `json:"modified"`
}

// Folder is the answer to "list": what a folder holds.
type Folder struct {
	Entries []Entry `json:"entries"`
}

// Head goes before the bytes of a read: how big the file is, and how much of it
// follows.
type Head struct {
	Size   int64 `json:"size"`
	Length int64 `json:"length"`
}

type problem struct {
	Error string `json:"error"`
}

// Run does one file operation and returns the exit status. Its arguments are
// the operation, the share's real folder, the path inside it, and what the
// operation needs besides.
func Run(args []string) int {
	out := json.NewEncoder(os.Stdout)
	if len(args) < 3 {
		return say(out, "the file child needs an operation, a share's folder and a path")
	}
	root, err := syscall.Open(args[1], oPath|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return say(out, explain(err).Error())
	}
	defer syscall.Close(root)
	op, inside, rest := args[0], args[2], args[3:]
	switch op {
	case "mkdir":
		// A share is worked in by a group, so a new folder is for the group too,
		// and the share's setgid bit carries on into it
		target, release, err := named(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		defer release()
		if err := os.Mkdir(target, 0o775); err != nil {
			return say(out, explain(err).Error())
		}
		if err := forTheGroup(target); err != nil {
			return say(out, explain(err).Error())
		}
	case "rename":
		if len(rest) < 1 {
			return say(out, "a rename needs where it is and where it goes")
		}
		from, release, err := named(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		defer release()
		to, releaseTo, err := named(root, rest[0])
		if err != nil {
			return say(out, err.Error())
		}
		defer releaseTo()
		if err := os.Rename(from, to); err != nil {
			return say(out, explain(err).Error())
		}
	case "remove":
		// The helper says whether a folder goes with everything in it
		target, release, err := named(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		defer release()
		if len(rest) > 0 && rest[0] == "tree" {
			err = os.RemoveAll(target)
		} else {
			err = os.Remove(target)
		}
		if err != nil {
			return say(out, explain(err).Error())
		}
	case "write":
		target, release, err := named(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		defer release()
		wrote, err := write(target, os.Stdin, len(rest) > 0 && rest[0] == "replace")
		if err != nil {
			return say(out, err.Error())
		}
		if err := out.Encode(Head{Size: wrote, Length: wrote}); err != nil {
			return 1
		}
		return 0
	case "list":
		entries, err := folder(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		if err := out.Encode(Folder{Entries: entries}); err != nil {
			return 1
		}
		return 0
	case "copy":
		if len(rest) < 1 {
			return say(out, "a copy needs what to copy and where it goes")
		}
		if err := copyInto(root, inside, rest[0]); err != nil {
			return say(out, err.Error())
		}
	case "tree":
		found, err := tree(root, inside)
		if err != nil {
			return say(out, err.Error())
		}
		if err := out.Encode(found); err != nil {
			return 1
		}
		return 0
	case "search":
		if len(rest) < 1 {
			return say(out, "a search needs something to look for")
		}
		if err := out.Encode(search(root, inside, rest[0], time.Now())); err != nil {
			return 1
		}
		return 0
	case "zip":
		// The rest are names inside the folder, each put in the zip with what it holds
		items, total, err := gather(root, inside, rest)
		if err != nil {
			return say(out, err.Error())
		}
		if err := out.Encode(Head{Size: total}); err != nil {
			return 1
		}
		if err := zipUp(os.Stdout, root, items); err != nil {
			return 1
		}
		return 0
	case "read":
		start, length := int64(0), int64(0)
		if len(rest) > 0 {
			start, _ = strconv.ParseInt(rest[0], 10, 64)
		}
		if len(rest) > 1 {
			length, _ = strconv.ParseInt(rest[1], 10, 64)
		}
		if err := read(out, root, inside, start, length); err != nil {
			return say(out, err.Error())
		}
		return 0
	default:
		return say(out, "the file child doesn't know "+op)
	}
	// Done, and saying so, so the helper can tell this from a child that died part way
	if err := out.Encode(struct{}{}); err != nil {
		return 1
	}
	return 0
}

// beneath opens a path inside the share's folder, refusing one that leaves it
// or passes through a link of any kind.
func beneath(root int, inside string, flags int) (int, error) {
	if inside == "" {
		inside = "."
	}
	name, err := syscall.BytePtrFromString(inside)
	if err != nil {
		return -1, err
	}
	how := openHow{flags: uint64(flags | syscall.O_CLOEXEC),
		resolve: resolveBeneath | resolveNoSymlinks | resolveNoMagiclinks}
	fd, _, errno := syscall.Syscall6(sysOpenat2, uintptr(root), uintptr(unsafe.Pointer(name)),
		uintptr(unsafe.Pointer(&how)), unsafe.Sizeof(how), 0, 0)
	if errno != 0 {
		return -1, errno
	}
	return int(fd), nil
}

// through is a path that reaches what an open descriptor holds, for the calls
// that take a path. It is the process's own descriptor, so nothing else can
// change where it leads.
func through(fd int) string {
	return fmt.Sprintf("/proc/self/fd/%d", fd)
}

// named opens the folder a name sits in, beneath the share, and returns a path
// to the name inside that open folder, with what closes the folder again.
func named(root int, inside string) (string, func(), error) {
	parent, base := filepath.Split(inside)
	if base == "" || base == "." || base == ".." {
		return "", nil, errors.New("that name can't be used")
	}
	fd, err := beneath(root, strings.TrimSuffix(parent, "/"), oPath|syscall.O_DIRECTORY)
	if err != nil {
		return "", nil, explain(err)
	}
	return filepath.Join(through(fd), base), func() { syscall.Close(fd) }, nil
}

// folder reads a folder. Folders come first and both kinds are in name order,
// which is what the desktop shows.
func folder(root int, inside string) ([]Entry, error) {
	fd, err := beneath(root, inside, syscall.O_RDONLY|syscall.O_DIRECTORY)
	if err != nil {
		return nil, explain(err)
	}
	// Named by the descriptor, so each entry's details are read inside this folder
	dir := os.NewFile(uintptr(fd), through(fd))
	defer dir.Close()
	found, err := dir.ReadDir(-1)
	if err != nil {
		return nil, explain(err)
	}
	entries := make([]Entry, 0, len(found))
	for _, item := range found {
		entry := Entry{Name: item.Name(), Dir: item.IsDir()}
		if info, err := item.Info(); err == nil {
			entry.Size = info.Size()
			entry.Modified = info.ModTime()
		}
		entries = append(entries, entry)
	}
	sort.Slice(entries, func(a, b int) bool {
		if entries[a].Dir != entries[b].Dir {
			return entries[a].Dir
		}
		return strings.ToLower(entries[a].Name) < strings.ToLower(entries[b].Name)
	})
	return entries, nil
}

// read answers with the file's size, then the wanted part of it.
func read(out *json.Encoder, root int, inside string, start, length int64) error {
	fd, err := beneath(root, inside, syscall.O_RDONLY)
	if err != nil {
		return explain(err)
	}
	file := os.NewFile(uintptr(fd), through(fd))
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return explain(err)
	}
	if info.IsDir() {
		return errors.New("that is a folder, not a file")
	}
	if start < 0 || start > info.Size() {
		return errors.New("that part of the file isn't there")
	}
	wanted := length
	if wanted <= 0 || start+wanted > info.Size() {
		wanted = info.Size() - start
	}
	if err := out.Encode(Head{Size: info.Size(), Length: wanted}); err != nil {
		return err
	}
	if _, err := file.Seek(start, io.SeekStart); err != nil {
		return explain(err)
	}
	_, err = io.CopyN(os.Stdout, file, wanted)
	return err
}

// write puts what it reads where it was told, through a temporary name in the
// same folder, so a stalled upload never leaves half a file in place. A name
// that is already there is refused unless replacing was asked for.
func write(path string, body io.Reader, replace bool) (int64, error) {
	folder := filepath.Dir(path)
	temp, err := os.CreateTemp(folder, ".fnas-upload-*")
	if err != nil {
		return 0, explain(err)
	}
	defer os.Remove(temp.Name())
	wrote, err := io.Copy(temp, body)
	if err != nil {
		temp.Close()
		return 0, errors.New("the upload stopped before it finished")
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return 0, explain(err)
	}
	if err := temp.Close(); err != nil {
		return 0, explain(err)
	}
	// Written for the share's group, as a file made on the NAS itself would be
	if err := os.Chmod(temp.Name(), 0o664); err != nil {
		return 0, explain(err)
	}
	if !replace {
		if _, err := os.Lstat(path); err == nil {
			return 0, errors.New("something with that name is already here")
		}
	}
	// A rename over the name never follows a link that was put there since
	if err := os.Rename(temp.Name(), path); err != nil {
		return 0, explain(err)
	}
	return wrote, nil
}

// Temporary names this child makes while it works, which nothing else should show
const workingPrefix = ".fnas-"

// copyInto copies a file, or a folder with everything in it, to a name that must
// not exist yet. The copy is made under a temporary name beside it and renamed
// into place, so a copy that fails part way leaves nothing half made. A link is
// never followed: one inside a copied folder is left out.
func copyInto(root int, from, to string) error {
	if to == from || strings.HasPrefix(to, from+"/") {
		return errors.New("a folder can't be copied into itself")
	}
	kind, err := kindOf(root, from)
	if err != nil {
		return err
	}
	target, release, err := named(root, to)
	if err != nil {
		return err
	}
	defer release()
	if _, err := os.Lstat(target); err == nil {
		return errors.New("something with that name is already here")
	}
	folder := filepath.Dir(target)
	switch kind {
	case syscall.S_IFREG:
		source, err := beneath(root, from, syscall.O_RDONLY)
		if err != nil {
			return explain(err)
		}
		reader := os.NewFile(uintptr(source), through(source))
		defer reader.Close()
		temp, err := os.CreateTemp(folder, workingPrefix+"copy-*")
		if err != nil {
			return explain(err)
		}
		defer os.Remove(temp.Name())
		if _, err := io.Copy(temp, reader); err != nil {
			temp.Close()
			return errors.New("the copy stopped before it finished")
		}
		if err := temp.Close(); err != nil {
			return explain(err)
		}
		if err := os.Chmod(temp.Name(), 0o664); err != nil {
			return explain(err)
		}
		return placed(temp.Name(), target)
	case syscall.S_IFDIR:
		temp, err := os.MkdirTemp(folder, workingPrefix+"copy-*")
		if err != nil {
			return explain(err)
		}
		if err := forTheGroup(temp); err != nil {
			os.RemoveAll(temp)
			return explain(err)
		}
		into, err := syscall.Open(temp, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			os.RemoveAll(temp)
			return explain(err)
		}
		err = copyTree(root, from, into)
		syscall.Close(into)
		if err != nil {
			os.RemoveAll(temp)
			return err
		}
		if err := placed(temp, target); err != nil {
			os.RemoveAll(temp)
			return err
		}
		return nil
	}
	return errors.New("only files and folders can be copied")
}

// forTheGroup opens a new folder to its group, past the umask. A share's folders
// carry the setgid bit, which the kernel hands to a folder made in one so what is
// made in it keeps the share's group; a plain chmod would clear it, leaving what
// is made inside to the person's own group, so it is kept.
func forTheGroup(folder string) error {
	info, err := os.Stat(folder)
	if err != nil {
		return err
	}
	return os.Chmod(folder, 0o775|(info.Mode()&os.ModeSetgid))
}

// placed renames a finished copy onto its name, unless something took the name meanwhile.
func placed(temp, target string) error {
	if _, err := os.Lstat(target); err == nil {
		return errors.New("something with that name is already here")
	}
	if err := os.Rename(temp, target); err != nil {
		return explain(err)
	}
	return nil
}

// kindOf is what is at a path beneath the share, without following a link.
func kindOf(root int, inside string) (uint32, error) {
	fd, err := beneath(root, inside, oPath)
	if err != nil {
		return 0, explain(err)
	}
	defer syscall.Close(fd)
	var info syscall.Stat_t
	if err := syscall.Fstat(fd, &info); err != nil {
		return 0, explain(err)
	}
	return info.Mode & syscall.S_IFMT, nil
}

// copyTree copies what a folder holds into an open folder, working through the
// folders it opened itself so nothing it writes can be sent elsewhere.
func copyTree(root int, from string, into int) error {
	names, err := listed(root, from)
	if err != nil {
		return err
	}
	for _, name := range names {
		inside := path.Join(from, name)
		kind, err := kindOf(root, inside)
		if err != nil {
			// A link, or something gone since the folder was read
			continue
		}
		made := filepath.Join(through(into), name)
		switch kind {
		case syscall.S_IFREG:
			source, err := beneath(root, inside, syscall.O_RDONLY)
			if err != nil {
				continue
			}
			reader := os.NewFile(uintptr(source), through(source))
			writer, err := os.OpenFile(made, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0o664)
			if err != nil {
				reader.Close()
				return explain(err)
			}
			_, err = io.Copy(writer, reader)
			reader.Close()
			if closed := writer.Close(); err == nil {
				err = closed
			}
			if err != nil {
				return errors.New("the copy stopped before it finished")
			}
			if err := os.Chmod(made, 0o664); err != nil {
				return explain(err)
			}
		case syscall.S_IFDIR:
			if err := os.Mkdir(made, 0o775); err != nil {
				return explain(err)
			}
			if err := forTheGroup(made); err != nil {
				return explain(err)
			}
			sub, err := syscall.Open(made, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
			if err != nil {
				return explain(err)
			}
			err = copyTree(root, inside, sub)
			syscall.Close(sub)
			if err != nil {
				return err
			}
		}
	}
	return nil
}

// Tree is the answer to "tree": the folders in a share, for a picker of where to
// move something, and whether there were more than it lists.
type Tree struct {
	Folders []string `json:"folders"`
	Cut     bool     `json:"cut"`
}

// The most a tree lists, and how deep it goes, so a huge share can't hold a
// picker, or the disks, for long
var (
	MaxTree      = 500
	MaxTreeDepth = 8
)

// tree lists the folders beneath one, by their paths in the share. Links and the
// child's own temporary folders are left out.
func tree(root int, from string) (Tree, error) {
	found := Tree{Folders: []string{}}
	var walk func(inside string, depth int) error
	walk = func(inside string, depth int) error {
		names, err := listed(root, inside)
		if err != nil {
			if inside == from {
				return err
			}
			return nil
		}
		for _, name := range names {
			if strings.HasPrefix(name, workingPrefix) {
				continue
			}
			child := path.Join(inside, name)
			if kind, err := kindOf(root, child); err != nil || kind != syscall.S_IFDIR {
				continue
			}
			if len(found.Folders) >= MaxTree {
				found.Cut = true
				return nil
			}
			found.Folders = append(found.Folders, child)
			if depth+1 < MaxTreeDepth {
				if err := walk(child, depth+1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	return found, walk(from, 0)
}

// Found is the answer to "search": what matched, by path in the share, and
// whether it stopped before looking everywhere, and why.
type Found struct {
	Hits    []Hit  `json:"hits"`
	Stopped string `json:"stopped,omitempty"`
}

// Hit is one thing whose name matched, with what the desktop lists about it.
type Hit struct {
	Path string `json:"path"`
	Entry
}

// What one search may take, so it can't hold the disks: this many matches, this
// deep, and this long
var (
	MaxHits     = 500
	MaxDepth    = 8
	SearchLimit = 2 * time.Second
)

// search walks beneath a folder looking for names that hold the query, without
// regard to case. The query is only ever compared with names, so a slash or ".."
// in it is text. Links are never followed. It answers what it found even when it
// stops early, and says why it stopped.
func search(root int, from, query string, started time.Time) Found {
	found := Found{Hits: []Hit{}}
	wanted := strings.ToLower(query)
	var walk func(inside string, depth int) bool
	walk = func(inside string, depth int) bool {
		fd, err := beneath(root, inside, syscall.O_RDONLY|syscall.O_DIRECTORY)
		if err != nil {
			return true
		}
		// Kept open while each entry's details are read, since they are read through it
		dir := os.NewFile(uintptr(fd), through(fd))
		entries, _ := dir.ReadDir(-1)
		sort.Slice(entries, func(a, b int) bool { return entries[a].Name() < entries[b].Name() })
		var folders []string
		for _, entry := range entries {
			if time.Since(started) > SearchLimit {
				dir.Close()
				found.Stopped = "time"
				return false
			}
			name := entry.Name()
			if strings.HasPrefix(name, workingPrefix) || entry.Type()&fs.ModeSymlink != 0 {
				continue
			}
			child := path.Join(inside, name)
			if strings.Contains(strings.ToLower(name), wanted) {
				hit := Hit{Path: child, Entry: Entry{Name: name, Dir: entry.IsDir()}}
				if info, err := entry.Info(); err == nil {
					hit.Size, hit.Modified = info.Size(), info.ModTime()
				}
				found.Hits = append(found.Hits, hit)
				if len(found.Hits) >= MaxHits {
					dir.Close()
					found.Stopped = "hits"
					return false
				}
			}
			if entry.IsDir() {
				folders = append(folders, child)
			}
		}
		dir.Close()
		if depth+1 >= MaxDepth {
			if len(folders) > 0 {
				found.Stopped = "depth"
			}
			return true
		}
		for _, folder := range folders {
			if !walk(folder, depth+1) {
				return false
			}
		}
		return true
	}
	walk(from, 0)
	return found
}

// MaxZip is the most a zip may hold, before compression. It is counted before a
// byte is sent, so a zip that is too big is refused rather than cut short.
var MaxZip int64 = 2 << 30

// zipItem is one thing going in a zip: where it is in the share, what it is
// called in the zip, and whether it is a folder.
type zipItem struct {
	inside, name string
	dir          bool
	// A folder's own time, since its entry carries no file to take one from
	modified time.Time
}

// gather lists what each name in a folder brings into a zip, with the folders'
// contents, and adds up their size. A link is never followed: a picked name that
// is a link is refused, and one met inside a folder is left out, as are things
// that aren't files or folders.
func gather(root int, folder string, names []string) ([]zipItem, int64, error) {
	var items []zipItem
	var total int64
	var walk func(inside, name string, top bool) error
	walk = func(inside, name string, top bool) error {
		fd, err := beneath(root, inside, oPath)
		if err != nil {
			if top {
				return fmt.Errorf("%s: %w", name, explain(err))
			}
			return nil
		}
		var info syscall.Stat_t
		err = syscall.Fstat(fd, &info)
		syscall.Close(fd)
		if err != nil {
			return fmt.Errorf("%s: %w", name, explain(err))
		}
		switch info.Mode & syscall.S_IFMT {
		case syscall.S_IFREG:
			total += info.Size
			if total > MaxZip {
				return errors.New("these come to more than the 2 GB a zip may hold: download fewer at a time")
			}
			items = append(items, zipItem{inside: inside, name: name})
		case syscall.S_IFDIR:
			items = append(items, zipItem{inside: inside, name: name, dir: true,
				modified: time.Unix(info.Mtim.Sec, info.Mtim.Nsec)})
			found, err := listed(root, inside)
			if err != nil {
				return fmt.Errorf("%s: %w", name, err)
			}
			for _, child := range found {
				if err := walk(path.Join(inside, child), path.Join(name, child), false); err != nil {
					return err
				}
			}
		}
		return nil
	}
	for _, name := range names {
		if err := walk(path.Join(folder, name), name, true); err != nil {
			return nil, 0, err
		}
	}
	return items, total, nil
}

// listed is a folder's names, read beneath the share like everything else.
func listed(root int, inside string) ([]string, error) {
	fd, err := beneath(root, inside, syscall.O_RDONLY|syscall.O_DIRECTORY)
	if err != nil {
		return nil, explain(err)
	}
	dir := os.NewFile(uintptr(fd), through(fd))
	defer dir.Close()
	names, err := dir.Readdirnames(-1)
	if err != nil {
		return nil, explain(err)
	}
	sort.Strings(names)
	return names, nil
}

// zipUp writes the zip as it reads, so nothing of it is kept anywhere. Pictures
// and films barely compress, so it compresses at its quickest. A file that went
// away since it was counted is left out, since the zip has already started.
func zipUp(to io.Writer, root int, items []zipItem) error {
	writer := zip.NewWriter(to)
	writer.RegisterCompressor(zip.Deflate, func(out io.Writer) (io.WriteCloser, error) {
		return flate.NewWriter(out, flate.BestSpeed)
	})
	for _, item := range items {
		if item.dir {
			if _, err := writer.CreateHeader(&zip.FileHeader{Name: item.name + "/", Method: zip.Store,
				Modified: item.modified}); err != nil {
				return err
			}
			continue
		}
		fd, err := beneath(root, item.inside, syscall.O_RDONLY)
		if err != nil {
			continue
		}
		file := os.NewFile(uintptr(fd), through(fd))
		info, err := file.Stat()
		if err != nil {
			file.Close()
			continue
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			file.Close()
			return err
		}
		header.Name, header.Method = item.name, zip.Deflate
		entry, err := writer.CreateHeader(header)
		if err != nil {
			file.Close()
			return err
		}
		_, err = io.Copy(entry, file)
		file.Close()
		if err != nil {
			return err
		}
	}
	return writer.Close()
}

// explain says what went wrong in words a person reading the desktop can use.
func explain(err error) error {
	switch {
	case errors.Is(err, fs.ErrPermission):
		return errors.New("you don't have permission to open that")
	case errors.Is(err, fs.ErrNotExist):
		return errors.New("that isn't there any more")
	case errors.Is(err, syscall.ELOOP), errors.Is(err, syscall.EXDEV):
		// A link, or a way out of the share, that wasn't there when the helper looked
		return errors.New("that changed while it was being opened")
	case errors.Is(err, syscall.ENOTEMPTY):
		return errors.New("that folder isn't empty")
	case errors.Is(err, syscall.EEXIST):
		return errors.New("something with that name is already here")
	default:
		return fmt.Errorf("that can't be read: %w", err)
	}
}

func say(out *json.Encoder, text string) int {
	out.Encode(problem{Error: text})
	return 1
}
