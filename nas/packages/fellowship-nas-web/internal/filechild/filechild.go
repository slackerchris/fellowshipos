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
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
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
		if err := os.Chmod(target, 0o775); err != nil {
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
