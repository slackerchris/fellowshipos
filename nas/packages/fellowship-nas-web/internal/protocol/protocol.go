// Package protocol is what the web front and the privileged helper say to
// each other over the helper's socket: one JSON request and one JSON
// response per connection.
package protocol

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"time"
)

const Socket = "/run/fellowship-nas-web/helper.sock"

// MaxMessage caps either side's message, so neither can make the other
// hold an unbounded amount in memory
const MaxMessage = 8 << 20

const (
	OpLogin      = "login"
	OpCheck      = "check"
	OpLogout     = "logout"
	OpFnas       = "fnas"
	OpSessions   = "sessions"
	OpEndSession = "end-session"
)

const (
	CodeUnauthenticated = "unauthenticated"
	CodeForbidden       = "forbidden"
	CodeThrottled       = "throttled"
	CodeBadRequest      = "bad-request"
	CodeFailed          = "failed"
)

type Request struct {
	Op string `json:"op"`
	// The secret from the session cookie; the helper alone decides what it is worth
	Session  string   `json:"session,omitempty"`
	Touch    bool     `json:"touch,omitempty"`
	User     string   `json:"user,omitempty"`
	Password string   `json:"password,omitempty"`
	Remote   string   `json:"remote,omitempty"`
	Args     []string `json:"args,omitempty"`
	Input    string   `json:"input,omitempty"`
	// The public ID of a session to end
	Target string `json:"target,omitempty"`
}

type Session struct {
	ID       string    `json:"id"`
	User     string    `json:"user"`
	Admin    bool      `json:"admin"`
	CSRF     string    `json:"csrf,omitempty"`
	Remote   string    `json:"remote"`
	Started  time.Time `json:"started"`
	LastSeen time.Time `json:"last_seen"`
	Current  bool      `json:"current,omitempty"`
}

type Response struct {
	Code       string    `json:"code,omitempty"`
	Error      string    `json:"error,omitempty"`
	RetryAfter int       `json:"retry_after,omitempty"`
	Token      string    `json:"token,omitempty"`
	Session    *Session  `json:"session,omitempty"`
	Sessions   []Session `json:"sessions,omitempty"`
	Output     string    `json:"output,omitempty"`
}

// Call sends one request to the helper listening on socket and waits for
// its answer. Backups and pool changes can take a long time, so the only
// limit is a generous one.
func Call(socket string, request Request) (Response, error) {
	var response Response
	conn, err := net.Dial("unix", socket)
	if err != nil {
		return response, err
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(6 * time.Hour))
	if err := json.NewEncoder(conn).Encode(request); err != nil {
		return response, err
	}
	conn.(*net.UnixConn).CloseWrite()
	if err := json.NewDecoder(io.LimitReader(conn, MaxMessage)).Decode(&response); err != nil {
		return response, fmt.Errorf("reading the helper's answer: %w", err)
	}
	return response, nil
}
