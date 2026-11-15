package store

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func hash(t *testing.T, password, prefix string) string {
	t.Helper()
	hashed := makeHash(password, prefix)
	if hashed == "" {
		t.Fatal("libcrypt made no hash")
	}
	return hashed
}

func write(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "accounts.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestHashesFromShadowAndFnasMatch(t *testing.T) {
	for _, prefix := range []string{"$y$", "$6$"} {
		hashed := hash(t, "Correct horse 9", prefix)
		if !Matches("Correct horse 9", hashed) {
			t.Errorf("%s: right password refused", prefix)
		}
		if Matches("wrong", hashed) || Matches("Correct horse 9", "!"+hashed) || Matches("", hashed) {
			t.Errorf("%s: matched what it shouldn't", prefix)
		}
	}
}

func TestCheck(t *testing.T) {
	good := hash(t, "tester password", "$y$")
	path := write(t, `{"version": 1, "people": {
		"tester": {"uid": 1000, "admin": true, "enabled": true, "hash": "`+good+`"},
		"off":    {"uid": 1001, "enabled": false, "hash": "`+good+`"},
		"renew":  {"uid": 1002, "enabled": true, "must_change": true, "hash": "`+good+`"},
		"nohash": {"uid": 1003, "enabled": true, "hash": ""}}}`)
	for _, c := range []struct {
		name, password string
		want           error
	}{
		{"tester", "tester password", nil},
		{"tester", "nope", ErrWrong},
		{"stranger", "tester password", ErrWrong},
		{"nohash", "", ErrWrong},
		// Off and expired are only said to someone who knows the password
		{"off", "nope", ErrWrong},
		{"off", "tester password", ErrOff},
		{"renew", "tester password", ErrExpired},
	} {
		if err := Check(path, c.name, c.password); !errors.Is(err, c.want) {
			t.Errorf("%s/%s: %v, want %v", c.name, c.password, err, c.want)
		}
	}
	if !Admin(path, "tester") || Admin(path, "off") || Admin(path, "stranger") {
		t.Error("admin flags read wrong")
	}
}

func TestSeparatedAndDamaged(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "none.json")
	if Separated(missing) {
		t.Error("no store reads as separated")
	}
	if _, err := Load(write(t, `{"version": 2, "people": {}}`)); err == nil {
		t.Error("an unknown version was read")
	}
	if err := Check(write(t, `{nope`), "a", "b"); err == nil || errors.Is(err, ErrWrong) {
		t.Errorf("a damaged store gave %v", err)
	}
}
