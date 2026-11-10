// Package protocol is what the web front and the privileged helper say to
// each other over the helper's socket: one JSON request and one JSON
// response per connection.
package protocol

import (
	"bufio"
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
	OpFileList   = "file-list"
	OpFileRead   = "file-read"
	OpFileWrite  = "file-write"
	OpFileMkdir  = "file-mkdir"
	OpFileRename = "file-rename"
	OpFileDelete = "file-delete"
	OpFileZip    = "file-zip"
	OpFileCopy   = "file-copy"
	OpFileTree   = "file-tree"
	OpSessions   = "sessions"
	OpEndSession = "end-session"
)

// ToTheEnd is the Length of an answer whose bytes run until the helper closes the
// connection, since nobody knows a zip's size before it is made.
const ToTheEnd int64 = -1

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
	// A file request: the share as the desktop names it, a path inside it, and
	// the part of the file wanted, for a video that is being seeked
	Share  string `json:"share,omitempty"`
	Path   string `json:"path,omitempty"`
	Start  int64  `json:"start,omitempty"`
	Length int64  `json:"length,omitempty"`
	// A rename's new name, and whether a write may replace what is there
	To      string `json:"to,omitempty"`
	Replace bool   `json:"replace,omitempty"`
	// A folder delete that takes what is inside with it
	Recursive bool `json:"recursive,omitempty"`
	// Several things in the folder Path names, by name alone, as a zip downloads them
	Names []string `json:"names,omitempty"`
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
	// A file's bytes follow this answer: Length of them, out of a file of Size, or
	// to the end when Length is ToTheEnd, for a zip that is made as it is sent
	Size   int64 `json:"size,omitempty"`
	Length int64 `json:"length,omitempty"`
}

// Send is Call for a request that carries bytes after it, such as an upload.
func Send(socket string, request Request, body io.Reader) (Response, error) {
	var response Response
	conn, err := net.Dial("unix", socket)
	if err != nil {
		return response, err
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(6 * time.Hour))
	encoded, err := json.Marshal(request)
	if err != nil {
		return response, err
	}
	if _, err := conn.Write(append(encoded, '\n')); err != nil {
		return response, err
	}
	if _, err := io.Copy(conn, body); err != nil {
		return response, fmt.Errorf("sending the file: %w", err)
	}
	conn.(*net.UnixConn).CloseWrite()
	if err := json.NewDecoder(io.LimitReader(conn, MaxMessage)).Decode(&response); err != nil {
		return response, fmt.Errorf("reading the helper's answer: %w", err)
	}
	return response, nil
}

// Stream is Call for an answer that carries a file after it. The caller must
// close the reader.
func Stream(socket string, request Request) (Response, io.ReadCloser, error) {
	var response Response
	conn, err := net.Dial("unix", socket)
	if err != nil {
		return response, nil, err
	}
	conn.SetDeadline(time.Now().Add(6 * time.Hour))
	if err := json.NewEncoder(conn).Encode(request); err != nil {
		conn.Close()
		return response, nil, err
	}
	conn.(*net.UnixConn).CloseWrite()
	// The answer is one line of JSON and the file follows it, so the head is read
	// as a line: a JSON decoder would swallow the start of the file, and leave
	// the newline that ended the head at the front of it
	reader := bufio.NewReaderSize(conn, 64<<10)
	head, err := reader.ReadBytes('\n')
	if err != nil {
		conn.Close()
		return response, nil, fmt.Errorf("reading the helper's answer: %w", err)
	}
	if err := json.Unmarshal(head, &response); err != nil {
		conn.Close()
		return response, nil, fmt.Errorf("reading the helper's answer: %w", err)
	}
	if response.Code != "" || response.Length == 0 {
		conn.Close()
		return response, nil, nil
	}
	var bytes io.Reader = reader
	if response.Length != ToTheEnd {
		bytes = io.LimitReader(reader, response.Length)
	}
	return response, struct {
		io.Reader
		io.Closer
	}{bytes, conn}, nil
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
