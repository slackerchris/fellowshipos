// Package files decides which file a person may touch. Files and WebDAV both
// ask it the same question, so the answer is written once: a share they can
// use, and a path inside it, with symlinks followed before the answer is given
// so a link can't lead somewhere the person couldn't go.
package files

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
)

// Root is one share a person may reach, as the desktop names it and where the
// engine keeps it.
type Root struct {
	Share string // the share's name, such as "media"
	Path  string // its folder on disk, such as /mnt/tank/shares/media
}

// Find returns the root for a share name.
func Find(roots []Root, share string) (Root, error) {
	for _, root := range roots {
		if root.Share == share {
			return root, nil
		}
	}
	return Root{}, fmt.Errorf("%s isn't a share you can use", share)
}

// Resolve turns a share and a path inside it into the path on disk, or says
// why it won't. The path must already exist: a missing file is an error rather
// than something to create.
func Resolve(roots []Root, share, inside string) (string, error) {
	root, clean, err := start(roots, share, inside)
	if err != nil {
		return "", err
	}
	real, err := rooted(root)
	if err != nil {
		return "", err
	}
	found, err := filepath.EvalSymlinks(filepath.Join(root.Path, clean))
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", fmt.Errorf("%s isn't in %s any more", name(share, inside), share)
		}
		return "", fmt.Errorf("%s can't be opened", name(share, inside))
	}
	return within(real, share, found)
}

// ResolveNew is for a name that must not exist yet, such as an upload or a
// rename onto a free name. Its folder must exist and stay inside the share, and
// the last part must be a plain name. A name that is already there is refused
// rather than followed: replacing a file goes through Resolve and an
// O_NOFOLLOW open, so a symlink sitting on the name can't be written through.
func ResolveNew(roots []Root, share, inside string) (string, error) {
	root, clean, err := start(roots, share, inside)
	if err != nil {
		return "", err
	}
	if clean == "" {
		return "", errors.New("that is the share itself, not a name inside it")
	}
	base := path.Base(clean)
	if base == "." || base == ".." || strings.ContainsRune(base, filepath.Separator) {
		return "", errors.New("that name can't be used")
	}
	real, err := rooted(root)
	if err != nil {
		return "", err
	}
	folder, err := filepath.EvalSymlinks(filepath.Join(root.Path, path.Dir(clean)))
	if err != nil {
		return "", fmt.Errorf("%s can't be opened", name(share, path.Dir(clean)))
	}
	if _, err := within(real, share, folder); err != nil {
		return "", err
	}
	target := filepath.Join(folder, base)
	if _, err := os.Lstat(target); err == nil {
		return "", fmt.Errorf("%s is already here", base)
	}
	return target, nil
}

// start checks the share and the path as written, before anything is looked
// up, and returns the root and the path with any leading slash removed.
func start(roots []Root, share, inside string) (Root, string, error) {
	root, err := Find(roots, share)
	if err != nil {
		return Root{}, "", err
	}
	if strings.ContainsRune(inside, 0) {
		return Root{}, "", errors.New("that path isn't a name")
	}
	if inside == "/" {
		inside = ""
	}
	if strings.HasPrefix(inside, "/") {
		return Root{}, "", errors.New("a path inside a share doesn't start with /")
	}
	for _, part := range strings.Split(inside, "/") {
		if part == ".." {
			return Root{}, "", fmt.Errorf("that path is outside %s", share)
		}
	}
	return root, strings.TrimPrefix(path.Clean("/"+inside), "/"), nil
}

// rooted is a root's own real path, since a pool is usually reached through a
// link. Asking first keeps a share whose pool has gone from reporting every file
// in it as missing.
func rooted(root Root) (string, error) {
	real, err := filepath.EvalSymlinks(root.Path)
	if err != nil {
		return "", fmt.Errorf("%s isn't available right now", root.Share)
	}
	return real, nil
}

// within confirms a real path is inside the root's real path.
func within(realRoot, share, found string) (string, error) {
	inside, err := filepath.Rel(realRoot, found)
	if err != nil || inside == ".." || strings.HasPrefix(inside, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("that path is outside %s", share)
	}
	return found, nil
}

// name is what an error calls the thing that went missing.
func name(share, inside string) string {
	if inside == "" || inside == "/" {
		return share
	}
	return fmt.Sprintf("%s in %s", path.Base(inside), share)
}
