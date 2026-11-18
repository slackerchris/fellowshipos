// The recycle bin: where Files puts what is deleted on a share whose bin is on.
// It is the folder SMB's recycle bin already uses, .recycle/<person> at the top
// of the share, with each thing at the path it had, so a file deleted from
// Windows and one deleted in Files end up side by side, are emptied by the same
// nightly job after the share's number of days, and can be put back the same way.
package filechild

import (
	"errors"
	"fmt"
	"path"
	"strings"
	"syscall"
	"unsafe"
)

// RecycleDir is the bin's folder at the top of a share, as Samba names it.
const RecycleDir = ".recycle"

// InBin says whether a path inside a share is in its recycle bin, so deleting
// it there is for good rather than into the bin again.
func InBin(inside string) bool {
	clean := strings.TrimPrefix(path.Clean("/"+inside), "/")
	return clean == RecycleDir || strings.HasPrefix(clean, RecycleDir+"/")
}

// atFdcwd is AT_FDCWD: the paths given are whole, through /proc/self/fd. A
// variable, since a negative constant can't become a uintptr.
var atFdcwd = -100

// renameNoreplace is renameat2's RENAME_NOREPLACE: the kernel refuses rather than
// replaces something that took the name since it was looked at.
const renameNoreplace = 1

// Most a bin or a putting back tries for a free name before giving up
const maxCopies = 999

// recycle moves something into the person's bin, at the path it had, making the
// folders that path needs. A name already in the bin from an earlier delete is
// kept, and this one is called "Copy #1 of ..." as Samba calls it. Everything
// moved has its time set to now, since the nightly emptying goes by that time,
// as it does for SMB, and a folder kept for a month must not lose its oldest
// files the next night.
func recycle(root int, inside, person string) (string, error) {
	if !plainName(person) {
		return "", errors.New("the recycle bin needs a person's name")
	}
	clean := strings.TrimPrefix(path.Clean("/"+inside), "/")
	if clean == "" {
		return "", errors.New("the share itself can't go in the recycle bin")
	}
	if InBin(clean) {
		return "", errors.New("that is already in the recycle bin")
	}
	folder := path.Join(RecycleDir, person, path.Dir(clean))
	if err := folders(root, folder, 0o770); err != nil {
		return "", err
	}
	to, err := freeName(root, folder, path.Base(clean), func(name string, n int) string {
		return fmt.Sprintf("Copy #%d of %s", n, name)
	})
	if err != nil {
		return "", err
	}
	if err := move(root, clean, to); err != nil {
		if errors.Is(err, syscall.EXDEV) {
			return "", errors.New("that is on a different volume from the share's recycle bin, so it can't be moved there")
		}
		return "", err
	}
	touchAll(root, to, 0)
	return to, nil
}

// putBack moves something out of the person's bin to where it was, making any
// folder on the way that has gone since. If something has the name now, it
// comes back as "name (put back)" beside it, so nothing is replaced.
func putBack(root int, inside, person, to string) (string, error) {
	if !plainName(person) {
		return "", errors.New("the recycle bin needs a person's name")
	}
	bin := path.Join(RecycleDir, person) + "/"
	from := strings.TrimPrefix(path.Clean("/"+inside), "/")
	if !strings.HasPrefix(from, bin) {
		return "", errors.New("only what is in your own recycle bin can be put back")
	}
	clean := strings.TrimPrefix(path.Clean("/"+to), "/")
	if clean == "" || InBin(clean) {
		return "", errors.New("that can't be put back there")
	}
	for _, part := range strings.Split(to, "/") {
		if part == ".." {
			return "", errors.New("that path is outside the share")
		}
	}
	if err := folders(root, path.Dir(clean), 0o775); err != nil {
		return "", err
	}
	name := path.Base(clean)
	target, err := freeName(root, path.Dir(clean), name, func(name string, n int) string {
		stem, ext := name, ""
		if dot := strings.LastIndex(name, "."); dot > 0 {
			stem, ext = name[:dot], name[dot:]
		}
		if n == 1 {
			return fmt.Sprintf("%s (put back)%s", stem, ext)
		}
		return fmt.Sprintf("%s (put back %d)%s", stem, n, ext)
	})
	if err != nil {
		return "", err
	}
	if err := move(root, from, target); err != nil {
		if errors.Is(err, syscall.EXDEV) {
			return "", errors.New("where it was is on a different volume now, so it can't be moved back")
		}
		return "", err
	}
	return target, nil
}

// plainName is a person's name as one path part: nothing that could climb out.
func plainName(name string) bool {
	return name != "" && name != "." && name != ".." && !strings.ContainsAny(name, "/\x00")
}

// folders makes each folder on a path inside the share that isn't there yet,
// beneath the share like everything else, so a link on the way is refused
// rather than followed. A share's setgid bit carries into each one.
func folders(root int, inside string, mode uint32) error {
	clean := strings.TrimPrefix(path.Clean("/"+inside), "/")
	if clean == "" {
		return nil
	}
	parts := strings.Split(clean, "/")
	for at := range parts {
		step := strings.Join(parts[:at+1], "/")
		kind, err := kindOf(root, step)
		if err == nil {
			if kind != syscall.S_IFDIR {
				return fmt.Errorf("%s is in the way: it isn't a folder", step)
			}
			continue
		}
		target, release, err := named(root, step)
		if err != nil {
			return err
		}
		err = syscall.Mkdir(target, mode)
		if err == nil {
			// Past the umask, keeping the setgid bit the share's folder handed down
			var made syscall.Stat_t
			if syscall.Lstat(target, &made) == nil {
				syscall.Chmod(target, mode|(made.Mode&syscall.S_ISGID))
			}
		}
		release()
		if err != nil && !errors.Is(err, syscall.EEXIST) {
			return explain(err)
		}
	}
	return nil
}

// freeName is the first of name, then what call makes of it with 1, 2 and so
// on, that nothing in a folder has yet.
func freeName(root int, folder, name string, call func(string, int) string) (string, error) {
	try := name
	for n := 1; ; n++ {
		at := path.Join(folder, try)
		if _, err := kindOf(root, at); err != nil {
			return at, nil
		}
		if n > maxCopies {
			return "", errors.New("there are too many things with that name already")
		}
		try = call(name, n)
	}
}

// move renames one path in the share to another without replacing anything
// there, each opened beneath the share. Where the filesystem can't promise
// that, as some can't, it looks first.
func move(root int, from, to string) error {
	source, release, err := named(root, from)
	if err != nil {
		return err
	}
	defer release()
	target, releaseTo, err := named(root, to)
	if err != nil {
		return err
	}
	defer releaseTo()
	err = renameat2(source, target, renameNoreplace)
	if errors.Is(err, syscall.EINVAL) || errors.Is(err, syscall.ENOSYS) {
		if _, err := kindOf(root, to); err == nil {
			return errors.New("something with that name is already here")
		}
		err = syscall.Rename(source, target)
	}
	if err != nil {
		if errors.Is(err, syscall.EXDEV) {
			return err
		}
		return explain(err)
	}
	return nil
}

func renameat2(from, to string, flags int) error {
	fromPtr, err := syscall.BytePtrFromString(from)
	if err != nil {
		return err
	}
	toPtr, err := syscall.BytePtrFromString(to)
	if err != nil {
		return err
	}
	if sysRenameat2 < 0 {
		return syscall.ENOSYS
	}
	_, _, errno := syscall.Syscall6(uintptr(sysRenameat2), uintptr(atFdcwd), uintptr(unsafe.Pointer(fromPtr)),
		uintptr(atFdcwd), uintptr(unsafe.Pointer(toPtr)), uintptr(flags), 0)
	if errno != 0 {
		return errno
	}
	return nil
}

// Deepest a bin's time setting goes into a folder
const maxTouchDepth = 64

// touchAll sets the time of something in the share, and of everything in it if
// it is a folder, to now. Each is reached through its own folder opened beneath
// the share, and a link is never followed. What can't be touched is left: it
// only goes from the bin sooner.
func touchAll(root int, inside string, depth int) {
	if target, release, err := named(root, inside); err == nil {
		touchNow(target)
		release()
	}
	if depth >= maxTouchDepth {
		return
	}
	if kind, err := kindOf(root, inside); err != nil || kind != syscall.S_IFDIR {
		return
	}
	names, err := listed(root, inside)
	if err != nil {
		return
	}
	for _, name := range names {
		touchAll(root, path.Join(inside, name), depth+1)
	}
}

// touchNow is utimensat with no times, which means now, on the name itself
// rather than what a link there points to.
func touchNow(target string) {
	name, err := syscall.BytePtrFromString(target)
	if err != nil {
		return
	}
	const atSymlinkNofollow = 0x100
	syscall.Syscall6(syscall.SYS_UTIMENSAT, uintptr(atFdcwd), uintptr(unsafe.Pointer(name)), 0,
		uintptr(atSymlinkNofollow), 0, 0)
}
