// A file answer is a JSON head followed by the bytes, so the test that matters
// is that every byte arrives: the JSON decoder reads ahead, and what it read
// ahead is the start of the file.
package protocol

import (
	"bytes"
	"encoding/json"
	"io"
	"net"
	"path/filepath"
	"testing"
)

func TestStreamKeepsEveryByte(t *testing.T) {
	socket := filepath.Join(t.TempDir(), "sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

	// Larger than the decoder's read-ahead, which is what the first version lost
	body := bytes.Repeat([]byte("0123456789"), 500)
	go func() {
		conn, err := listener.AcceptUnix()
		if err != nil {
			return
		}
		defer conn.Close()
		var request Request
		json.NewDecoder(conn).Decode(&request)
		json.NewEncoder(conn).Encode(Response{Size: int64(len(body)), Length: int64(len(body))})
		conn.Write(body)
	}()

	response, reader, err := Stream(socket, Request{Op: OpFileRead, Share: "media", Path: "file"})
	if err != nil {
		t.Fatal(err)
	}
	if response.Length != int64(len(body)) {
		t.Fatalf("the answer said %d bytes", response.Length)
	}
	if reader == nil {
		t.Fatal("no bytes followed the answer")
	}
	defer reader.Close()
	got, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, body) {
		t.Fatalf("got %d of %d bytes", len(got), len(body))
	}
}

func TestStreamHandsBackNoBodyForARefusal(t *testing.T) {
	socket := filepath.Join(t.TempDir(), "sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, err := listener.AcceptUnix()
		if err != nil {
			return
		}
		defer conn.Close()
		var request Request
		json.NewDecoder(conn).Decode(&request)
		json.NewEncoder(conn).Encode(Response{Code: CodeForbidden, Error: "that isn't yours"})
	}()

	response, reader, err := Stream(socket, Request{Op: OpFileRead, Share: "private", Path: "x"})
	if err != nil {
		t.Fatal(err)
	}
	if reader != nil {
		reader.Close()
		t.Error("a refusal came with a body")
	}
	if response.Code != CodeForbidden {
		t.Errorf("the refusal answered %q", response.Code)
	}
}

// A zip is made as it is sent, so its answer has no length and its bytes run
// until the helper closes the connection.
func TestAStreamOfUnknownLengthRunsToTheEnd(t *testing.T) {
	socket := filepath.Join(t.TempDir(), "sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	body := bytes.Repeat([]byte("PK zip "), 20000)
	go func() {
		conn, err := listener.AcceptUnix()
		if err != nil {
			return
		}
		defer conn.Close()
		var request Request
		json.NewDecoder(conn).Decode(&request)
		json.NewEncoder(conn).Encode(Response{Size: 5, Length: ToTheEnd})
		conn.Write(body)
	}()
	_, reader, err := Stream(socket, Request{Op: OpFileZip, Share: "media", Names: []string{"a", "b"}})
	if err != nil || reader == nil {
		t.Fatalf("no bytes followed the answer: %v", err)
	}
	defer reader.Close()
	got, _ := io.ReadAll(reader)
	if !bytes.Equal(got, body) {
		t.Fatalf("got %d of %d bytes", len(got), len(body))
	}
}
