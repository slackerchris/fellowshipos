// The tests worth having here are the ones that try to leave a share. Every
// way out an attacker or a bug could take is a case below, and each one must
// answer with an error rather than a path outside the root.
package files

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// shares builds a small world: one share with a folder and a file, a folder
// beside it that the share must never reach, and three links to try.
func shares(t *testing.T) []Root {
	t.Helper()
	pool := t.TempDir()
	media := filepath.Join(pool, "media")
	for _, dir := range []string{filepath.Join(media, "photos"), filepath.Join(pool, "outside")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write(t, filepath.Join(media, "photos", "a.jpg"))
	write(t, filepath.Join(pool, "outside", "secret.txt"))
	link(t, filepath.Join(pool, "outside"), filepath.Join(media, "escape"))
	link(t, filepath.Join(pool, "outside", "secret.txt"), filepath.Join(media, "secret.txt"))
	link(t, filepath.Join(media, "photos"), filepath.Join(media, "look"))
	link(t, filepath.Join(media, "missing"), filepath.Join(media, "dangling"))
	// A pool is usually reached through a link, so the root is one here too
	link(t, media, filepath.Join(pool, "media-by-name"))
	return []Root{{Share: "media", Path: filepath.Join(pool, "media-by-name")},
		{Share: "gone", Path: filepath.Join(pool, "gone")}}
}

func write(t *testing.T, path string) {
	t.Helper()
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func link(t *testing.T, target, name string) {
	t.Helper()
	if err := os.Symlink(target, name); err != nil {
		t.Fatal(err)
	}
}

func TestResolveKeepsPeopleInsideAShare(t *testing.T) {
	roots := shares(t)
	cases := []struct {
		name  string
		share string
		path  string
		ok    bool
		want  string // the path it should end in, or the words of the refusal
	}{
		{name: "a file", share: "media", path: "photos/a.jpg", ok: true, want: "media/photos/a.jpg"},
		// The root is a link here, and the answer is the real folder behind it
		{name: "the share itself", share: "media", path: "", ok: true, want: "media"},
		{name: "the share itself, written as a slash", share: "media", path: "/", ok: true, want: "media"},
		{name: "a path with needless parts", share: "media", path: "photos//./a.jpg", ok: true,
			want: "media/photos/a.jpg"},
		{name: "a link that stays inside", share: "media", path: "look/a.jpg", ok: true,
			want: "media/photos/a.jpg"},

		{name: "a share they can't use", share: "docs", path: "a.jpg", want: "isn't a share you can use"},
		{name: "climbing out", share: "media", path: "../outside/secret.txt", want: "outside media"},
		{name: "climbing out from a folder", share: "media", path: "photos/../../outside", want: "outside media"},
		{name: "a link out of the share", share: "media", path: "escape/secret.txt", want: "outside media"},
		{name: "a link to a file outside", share: "media", path: "secret.txt", want: "outside media"},
		{name: "an absolute path", share: "media", path: "/etc/passwd", want: "doesn't start with /"},
		{name: "a NUL byte", share: "media", path: "a\x00b", want: "isn't a name"},
		{name: "a link that leads nowhere", share: "media", path: "dangling", want: "isn't in media any more"},
		{name: "a file that has gone", share: "media", path: "photos/b.jpg", want: "isn't in media any more"},
		{name: "a share whose pool has gone", share: "gone", path: "a.jpg", want: "isn't available right now"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			found, err := Resolve(roots, c.share, c.path)
			if c.ok {
				if err != nil {
					t.Fatalf("Resolve(%q, %q) = %v", c.share, c.path, err)
				}
				if !strings.HasSuffix(found, c.want) {
					t.Errorf("Resolve(%q, %q) = %s, want it to end in %s", c.share, c.path, found, c.want)
				}
				return
			}
			if err == nil {
				t.Fatalf("Resolve(%q, %q) = %s, want a refusal", c.share, c.path, found)
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("Resolve(%q, %q) said %q, want %q", c.share, c.path, err, c.want)
			}
		})
	}
}

func TestResolveGivesThePathOnDisk(t *testing.T) {
	roots := shares(t)
	found, err := Resolve(roots, "media", "photos/a.jpg")
	if err != nil {
		t.Fatal(err)
	}
	real, err := filepath.EvalSymlinks(filepath.Join(roots[0].Path, "photos", "a.jpg"))
	if err != nil {
		t.Fatal(err)
	}
	if found != real {
		t.Errorf("Resolve = %s, want %s", found, real)
	}
}

func TestResolveNewNamesWhatMayNotBeThereYet(t *testing.T) {
	roots := shares(t)
	cases := []struct {
		name  string
		share string
		path  string
		want  string // the last part of the path, or the refusal
	}{
		{name: "a new name in a folder", share: "media", path: "photos/new.jpg", want: "new.jpg"},
		{name: "a new folder in the share", share: "media", path: "holiday", want: "holiday"},
		{name: "through a link that stays inside", share: "media", path: "look/new.jpg", want: "new.jpg"},

		{name: "the share itself", share: "media", path: "", want: "not a name inside it"},
		{name: "a name that is already there", share: "media", path: "photos/a.jpg", want: "already here"},
		{name: "a link sitting on the name", share: "media", path: "secret.txt", want: "already here"},
		{name: "a link to nowhere sitting on the name", share: "media", path: "dangling", want: "already here"},
		{name: "climbing out", share: "media", path: "photos/../../outside/new", want: "outside media"},
		{name: "a folder outside through a link", share: "media", path: "escape/new.jpg", want: "outside media"},
		{name: "a folder that has gone", share: "media", path: "photos/gone/new.jpg", want: "can't be opened"},
		{name: "a share they can't use", share: "docs", path: "new.jpg", want: "isn't a share you can use"},
		{name: "a share whose pool has gone", share: "gone", path: "new.jpg", want: "isn't available right now"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			found, err := ResolveNew(roots, c.share, c.path)
			if err == nil {
				if filepath.Base(found) != c.want {
					t.Fatalf("ResolveNew(%q, %q) = %s, want it to end in %s", c.share, c.path, found, c.want)
				}
				return
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Errorf("ResolveNew(%q, %q) said %q, want %q", c.share, c.path, err, c.want)
			}
		})
	}
}

func TestFindSaysWhatThePersonCanUse(t *testing.T) {
	roots := []Root{{Share: "media", Path: "/mnt/tank/shares/media"}}
	if _, err := Find(roots, "media"); err != nil {
		t.Errorf("Find(media) = %v", err)
	}
	if _, err := Find(roots, "MEDIA"); err == nil {
		t.Error("Find(MEDIA) answered, and share names are exact")
	}
	if _, err := Find(nil, "media"); err == nil {
		t.Error("Find on a person with no shares answered")
	}
}
