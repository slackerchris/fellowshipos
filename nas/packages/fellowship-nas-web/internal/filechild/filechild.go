// Package filechild is the small program the helper re-executes to touch a
// file, already running as the person who asked. It does no path work of its
// own: the helper resolved the path first, so this only opens what it was
// given and says what happened.
//
// It speaks one line of JSON to stdout, then, for a read, the bytes.
package filechild

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

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

// Run does one file operation and returns the exit status.
func Run(args []string) int {
	out := json.NewEncoder(os.Stdout)
	if len(args) < 2 {
		return say(out, "the file child needs an operation and a path")
	}
	switch args[0] {
	case "list":
		entries, err := folder(args[1])
		if err != nil {
			return say(out, err.Error())
		}
		if err := out.Encode(Folder{Entries: entries}); err != nil {
			return 1
		}
	case "read":
		start, length := int64(0), int64(0)
		if len(args) > 2 {
			start, _ = strconv.ParseInt(args[2], 10, 64)
		}
		if len(args) > 3 {
			length, _ = strconv.ParseInt(args[3], 10, 64)
		}
		if err := read(out, args[1], start, length); err != nil {
			return say(out, err.Error())
		}
	default:
		return say(out, "the file child doesn't know "+args[0])
	}
	return 0
}

// folder reads a folder. Folders come first and both kinds are in name order,
// which is what the desktop shows.
func folder(path string) ([]Entry, error) {
	found, err := os.ReadDir(path)
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

// read answers with the file's size, then the wanted part of it. The path was
// resolved by the helper, so it is opened as it is, and O_NOFOLLOW means a
// symlink swapped in since then is refused rather than followed.
func read(out *json.Encoder, path string, start, length int64) error {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return explain(err)
	}
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

// explain says what went wrong in words a person reading the desktop can use.
func explain(err error) error {
	switch {
	case errors.Is(err, fs.ErrPermission):
		return errors.New("you don't have permission to open that")
	case errors.Is(err, fs.ErrNotExist):
		return errors.New("that isn't there any more")
	case errors.Is(err, syscall.ELOOP):
		return errors.New("that changed while it was being opened")
	default:
		return fmt.Errorf("that can't be read: %w", err)
	}
}

func say(out *json.Encoder, text string) int {
	out.Encode(problem{Error: text})
	return 1
}
