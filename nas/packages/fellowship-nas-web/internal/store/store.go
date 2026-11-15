// Package store reads the NAS's own account store, which a separated NAS keeps
// its people in instead of Linux logins (docs/fellowship-nas-account-separation.md).
// fnas writes it; the helper only reads it, to log people in and to know who
// is a NAS admin. Passwords are crypt(3) hashes, checked by libcrypt, the same
// library /etc/shadow's hashes come from.
package store

// #cgo LDFLAGS: -lcrypt
// #include <crypt.h>
// #include <stdlib.h>
// #include <string.h>
//
// static int matches(const char *password, const char *hash) {
//     struct crypt_data *data = calloc(1, sizeof *data);
//     if (data == NULL) {
//         return 0;
//     }
//     char *out = crypt_r(password, hash, data);
//     int ok = out != NULL && out[0] != '*' && strcmp(out, hash) == 0;
//     explicit_bzero(data, sizeof *data);
//     free(data);
//     return ok;
// }
//
// static char *make_hash(const char *password, const char *prefix) {
//     const char *salt = crypt_gensalt(prefix, 0, NULL, 0);
//     return salt == NULL ? NULL : crypt(password, salt);
// }
//
// static void forget(char *text) {
//     explicit_bzero(text, strlen(text));
//     free(text);
// }
import "C"

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"unsafe"
)

// Path is where fnas keeps the store
const Path = "/var/lib/fellowship-nas/accounts.json"

const version = 1

var (
	// ErrWrong is a wrong password or a name the store doesn't have: one answer
	// for both, so a guess learns nothing
	ErrWrong = errors.New("wrong name or password")
	// ErrOff is a person whose account is turned off
	ErrOff = errors.New("account turned off")
	// ErrExpired is a right password that has to be changed before it is used
	ErrExpired = errors.New("password must be changed")
)

// Person is one entry, as far as the helper needs it
type Person struct {
	UID        int    `json:"uid"`
	Admin      bool   `json:"admin"`
	Enabled    *bool  `json:"enabled"`
	Hash       string `json:"hash"`
	MustChange bool   `json:"must_change"`
}

type file struct {
	Version int               `json:"version"`
	People  map[string]Person `json:"people"`
}

// Separated is whether the NAS keeps its people in the store at path
func Separated(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// Load reads the store at path
func Load(path string) (map[string]Person, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var found file
	if err := json.Unmarshal(data, &found); err != nil {
		return nil, fmt.Errorf("%s is damaged: %w", path, err)
	}
	if found.Version != version || found.People == nil {
		return nil, fmt.Errorf("%s isn't an account store this helper can read", path)
	}
	return found.People, nil
}

// Matches is whether password is the one hash was made from. An empty hash, a
// locked one (!) and an unusable one (*) match nothing.
func Matches(password, hash string) bool {
	if password == "" || hash == "" || hash[0] == '!' || hash[0] == '*' {
		return false
	}
	cPassword, cHash := C.CString(password), C.CString(hash)
	defer C.forget(cPassword)
	defer C.free(unsafe.Pointer(cHash))
	return C.matches(cPassword, cHash) == 1
}

// Check says whether name may log in with password: nil, ErrWrong, ErrOff or
// ErrExpired. The password is checked before anything else is said about the
// account, so only someone who knows it learns that it is off or expired.
func Check(path, name, password string) error {
	people, err := Load(path)
	if err != nil {
		return err
	}
	person, known := people[name]
	if !known || !Matches(password, person.Hash) {
		return ErrWrong
	}
	if person.Enabled != nil && !*person.Enabled {
		return ErrOff
	}
	if person.MustChange {
		return ErrExpired
	}
	return nil
}

// Admin is whether name is a NAS admin in the store at path
func Admin(path, name string) bool {
	people, err := Load(path)
	if err != nil {
		return false
	}
	person, known := people[name]
	return known && person.Admin && (person.Enabled == nil || *person.Enabled)
}

// makeHash hashes password with a new salt for the method prefix names ($y$ for
// yescrypt, $6$ for SHA-512), as chpasswd and fnas do. Only the tests use it:
// the helper never writes a password.
func makeHash(password, prefix string) string {
	cPassword, cPrefix := C.CString(password), C.CString(prefix)
	defer C.forget(cPassword)
	defer C.free(unsafe.Pointer(cPrefix))
	out := C.make_hash(cPassword, cPrefix)
	if out == nil {
		return ""
	}
	return C.GoString(out)
}
