package helper

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"fellowship-nas-web/internal/protocol"
)

type fake struct {
	mu     sync.Mutex
	now    time.Time
	ran    [][]string
	alerts []string
	admins map[string]bool
}

func newFake() (*fake, *Helper) {
	f := &fake{now: time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC), admins: map[string]bool{"sam": true}}
	h := New(Config{
		Authenticate: func(user, password string) error {
			if password == "right" {
				return nil
			}
			if password == "old" {
				return ErrExpired
			}
			return errors.New("wrong")
		},
		Lookup: func(user string) (Account, error) {
			switch user {
			case "root":
				return Account{UID: 0, Admin: true}, nil
			case "sam", "kim":
				return Account{UID: 1000, Admin: f.admins[user]}, nil
			}
			return Account{}, errors.New("no such user")
		},
		Fnas: func(args []string, input string) (string, string, error) {
			f.ran = append(f.ran, args)
			if args[0] == "share" {
				return "", "fnas: no such share\nfnas: second line", errors.New("exit status 1")
			}
			return "ok\n", "", nil
		},
		Alert: func(title, message string) {
			f.mu.Lock()
			f.alerts = append(f.alerts, message)
			f.mu.Unlock()
		},
		Now: func() time.Time { return f.now },
	})
	return f, h
}

func login(t *testing.T, h *Helper, user string) protocol.Response {
	t.Helper()
	response := h.Handle(protocol.Request{Op: protocol.OpLogin, User: user, Password: "right", Remote: "10.0.0.2"})
	if response.Code != "" {
		t.Fatalf("login for %s: %s %s", user, response.Code, response.Error)
	}
	return response
}

func TestLogin(t *testing.T) {
	_, h := newFake()
	response := login(t, h, "sam")
	if len(response.Token) < 40 || response.Session.CSRF == "" || !response.Session.Admin || response.Session.User != "sam" {
		t.Fatalf("unexpected session %+v", response)
	}
	checked := h.Handle(protocol.Request{Op: protocol.OpCheck, Session: response.Token})
	if checked.Code != "" || checked.Session.CSRF != response.Session.CSRF {
		t.Fatalf("check: %+v", checked)
	}
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: response.Token + "x"}).Code != protocol.CodeUnauthenticated {
		t.Fatal("a wrong token was accepted")
	}
}

func TestLoginRefusals(t *testing.T) {
	_, h := newFake()
	for _, attempt := range []protocol.Request{
		{User: "sam", Password: "wrong"},
		{User: "root", Password: "right"},
		{User: "nobody", Password: "right"},
		{User: "sam", Password: ""},
		{User: "Sam;rm", Password: "right"},
	} {
		attempt.Op = protocol.OpLogin
		attempt.Remote = "10.0.0.9"
		response := h.Handle(attempt)
		if response.Code != protocol.CodeUnauthenticated || response.Token != "" {
			t.Errorf("%s/%s: %+v", attempt.User, attempt.Password, response)
		}
	}
	expired := h.Handle(protocol.Request{Op: protocol.OpLogin, User: "kim", Password: "old"})
	if !strings.Contains(expired.Error, "expired") || expired.Token != "" {
		t.Errorf("expired: %+v", expired)
	}
}

func TestAChangedPasswordEndsEverySession(t *testing.T) {
	f, h := newFake()
	signedOut := filepath.Join(t.TempDir(), "signed-out.json")
	h.config.SignedOut = signedOut
	token := login(t, h, "sam").Token

	// fnas writes this when the password changes or the account is turned off
	stamp := f.now.Add(time.Second).Unix()
	if err := os.WriteFile(signedOut, []byte(fmt.Sprintf(`{"sam": %d}`, stamp)), 0o600); err != nil {
		t.Fatal(err)
	}
	f.now = f.now.Add(2 * time.Second)
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token}).Code != protocol.CodeUnauthenticated {
		t.Fatal("a session made with the old password lived on")
	}
	// Somebody else's session is nobody else's business
	other := login(t, h, "kim").Token
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: other}).Code != "" {
		t.Fatal("a different account was signed out")
	}
}

func TestSessionsEnd(t *testing.T) {
	f, h := newFake()
	token := login(t, h, "sam").Token
	f.now = f.now.Add(IdleTimeout - time.Minute)
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token, Touch: true}).Code != "" {
		t.Fatal("session ended early")
	}
	// Polling doesn't count as activity
	f.now = f.now.Add(IdleTimeout - time.Minute)
	h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token})
	f.now = f.now.Add(2 * time.Minute)
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token}).Code != protocol.CodeUnauthenticated {
		t.Fatal("an idle session lived on")
	}

	token = login(t, h, "sam").Token
	for elapsed := time.Duration(0); elapsed <= MaxAge; elapsed += 20 * time.Minute {
		f.now = f.now.Add(20 * time.Minute)
		h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token, Touch: true})
	}
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token}).Code != protocol.CodeUnauthenticated {
		t.Fatal("a session outlived its maximum age")
	}

	token = login(t, h, "sam").Token
	h.Handle(protocol.Request{Op: protocol.OpLogout, Session: token})
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: token}).Code != protocol.CodeUnauthenticated {
		t.Fatal("logout left the session alive")
	}
}

func TestThrottle(t *testing.T) {
	f, h := newFake()
	wrong := protocol.Request{Op: protocol.OpLogin, User: "sam", Password: "wrong", Remote: "10.0.0.9"}
	for i := 0; i < freeTries; i++ {
		if code := h.Handle(wrong).Code; code != protocol.CodeUnauthenticated {
			t.Fatalf("try %d: %s", i, code)
		}
	}
	throttled := h.Handle(wrong)
	if throttled.Code != protocol.CodeThrottled || throttled.RetryAfter < 1 {
		t.Fatalf("not throttled: %+v", throttled)
	}
	// The right password waits too, so throttling can't be used to test guesses
	right := wrong
	right.Password = "right"
	if h.Handle(right).Code != protocol.CodeThrottled {
		t.Fatal("throttling let a login through")
	}
	// Another account from the same address waits as well
	other := protocol.Request{Op: protocol.OpLogin, User: "kim", Password: "right", Remote: "10.0.0.9"}
	if h.Handle(other).Code != protocol.CodeThrottled {
		t.Fatal("the address wasn't throttled")
	}
	f.now = f.now.Add(forgetAfter + time.Second)
	if h.Handle(right).Code != "" {
		t.Fatal("throttling never ended")
	}
}

func TestAlertAfterRepeatedFailures(t *testing.T) {
	f, h := newFake()
	for i := 0; i < alertAfter; i++ {
		f.now = f.now.Add(longestWait + time.Second)
		h.Handle(protocol.Request{Op: protocol.OpLogin, User: "kim", Password: "wrong", Remote: "10.0.0.7"})
	}
	time.Sleep(50 * time.Millisecond)
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.alerts) != 1 || !strings.Contains(f.alerts[0], `"kim"`) || !strings.Contains(f.alerts[0], "10.0.0.7") {
		t.Fatalf("alerts: %q", f.alerts)
	}
}

func TestAllowed(t *testing.T) {
	cases := []struct {
		args  []string
		admin bool
		want  bool
	}{
		{[]string{"--json", "pool", "list"}, true, true},
		{[]string{"pool", "create", "tank", "mirror", "a", "b", "--confirm", "erase 2 disks"}, true, true},
		{[]string{"health"}, true, true},
		{[]string{"--json", "health"}, true, true},
		{[]string{"health", "extra"}, true, false},
		{[]string{"alert", "send", "x", "y"}, true, false},
		{[]string{"pool", "destroy", "tank"}, true, false},
		{[]string{"--help"}, true, false},
		{[]string{}, true, false},
		{[]string{"pool", "list\x00"}, true, false},
		{[]string{"--json", "pool", "list"}, false, false},
		{[]string{"user", "smb-password", "kim", "--password-stdin"}, false, true},
		{[]string{"user", "smb-password", "sam", "--password-stdin"}, false, false},
		{[]string{"user", "password", "kim", "--password-stdin"}, false, true},
		{[]string{"user", "password", "sam", "--password-stdin"}, false, false},
		{[]string{"user", "create", "sam", "--password-stdin"}, false, false},
		{[]string{"user", "create", "sam", "--password-stdin"}, true, true},
		// A look is each person's own, so a non-admin may only touch their own
		{[]string{"--json", "look", "show", "kim"}, false, true},
		{[]string{"look", "set", "kim", "--mode", "dark"}, false, true},
		{[]string{"look", "set", "kim", "--palette", "highwatch"}, false, true},
		{[]string{"look", "show", "sam"}, false, false},
		{[]string{"look", "set", "sam", "--mode", "dark"}, false, false},
		{[]string{"look", "show"}, false, false},
		// The shape is right, so fnas answers that there is nothing to change
		{[]string{"look", "set", "kim"}, false, true},
		{[]string{"look", "remove", "kim"}, false, false},
		{[]string{"look", "show", "kim"}, true, true},
		{[]string{"--json", "look", "set", "sam", "--mode", "dark"}, true, true},
		{[]string{"group", "create", "family"}, true, true},
		{[]string{"sharing", "nfs", "--v3", "on"}, true, true},
		{[]string{"share", "access", "docs", "--everyone"}, true, true},
		{[]string{"--json", "job", "list"}, true, true},
		{[]string{"job", "run", "0123456789ab"}, true, false},
		{[]string{"pool", "replace", "tank", "old", "new", "--confirm", "erase 1 disk"}, true, true},
		{[]string{"system", "grow", "all"}, false, false},
	}
	for _, c := range cases {
		if got := Allowed(c.args, "kim", c.admin); got != c.want {
			t.Errorf("Allowed(%q, admin=%v) = %v", c.args, c.admin, got)
		}
	}
}

func TestFnas(t *testing.T) {
	f, h := newFake()
	admin := login(t, h, "sam").Token
	person := login(t, h, "kim").Token

	response := h.Handle(protocol.Request{Op: protocol.OpFnas, Session: admin, Args: []string{"--json", "pool", "list"}})
	if response.Code != "" || response.Output != "ok\n" {
		t.Fatalf("pool list: %+v", response)
	}
	failed := h.Handle(protocol.Request{Op: protocol.OpFnas, Session: admin, Args: []string{"share", "list"}})
	if failed.Code != protocol.CodeFailed || failed.Error != "no such share\nsecond line" {
		t.Fatalf("failure: %+v", failed)
	}
	if h.Handle(protocol.Request{Op: protocol.OpFnas, Session: person, Args: []string{"pool", "list"}}).Code != protocol.CodeForbidden {
		t.Fatal("a non-admin ran an admin command")
	}
	own := h.Handle(protocol.Request{Op: protocol.OpFnas, Session: person,
		Args: []string{"user", "smb-password", "kim", "--password-stdin"}, Input: "pw"})
	if own.Code != "" {
		t.Fatalf("own SMB password: %+v", own)
	}
	// Leaving nas-admins takes effect without logging out
	f.admins["sam"] = false
	if h.Handle(protocol.Request{Op: protocol.OpFnas, Session: admin, Args: []string{"pool", "list"}}).Code != protocol.CodeForbidden {
		t.Fatal("a removed admin kept their rights")
	}
	if len(f.ran) != 3 {
		t.Fatalf("ran %q", f.ran)
	}
	if h.Handle(protocol.Request{Op: protocol.OpFnas, Session: "made-up", Args: []string{"pool", "list"}}).Code != protocol.CodeUnauthenticated {
		t.Fatal("ran fnas without a session")
	}
}

func TestSessionList(t *testing.T) {
	_, h := newFake()
	admin := login(t, h, "sam")
	person := login(t, h, "kim")

	all := h.Handle(protocol.Request{Op: protocol.OpSessions, Session: admin.Token}).Sessions
	if len(all) != 2 || all[0].CSRF != "" || all[1].CSRF != "" {
		t.Fatalf("admin's list: %+v", all)
	}
	for _, listed := range all {
		if listed.Current != (listed.User == "sam") {
			t.Fatalf("current marked wrongly: %+v", all)
		}
	}
	own := h.Handle(protocol.Request{Op: protocol.OpSessions, Session: person.Token}).Sessions
	if len(own) != 1 || own[0].User != "kim" {
		t.Fatalf("person's list: %+v", own)
	}
	if h.Handle(protocol.Request{Op: protocol.OpEndSession, Session: person.Token, Target: admin.Session.ID}).Code != protocol.CodeForbidden {
		t.Fatal("a person ended an admin's session")
	}
	if h.Handle(protocol.Request{Op: protocol.OpEndSession, Session: admin.Token, Target: person.Session.ID}).Code != "" {
		t.Fatal("an admin couldn't end a session")
	}
	if h.Handle(protocol.Request{Op: protocol.OpCheck, Session: person.Token}).Code != protocol.CodeUnauthenticated {
		t.Fatal("the ended session lived on")
	}
}
