// fellowship-nas-helper is the only part of the web server that runs as
// root. It listens on a socket that only the web front's account can reach.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"log"
	"net"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"fellowship-nas-web/internal/filechild"
	"fellowship-nas-web/internal/helper"
	"fellowship-nas-web/internal/pam"
	"fellowship-nas-web/internal/protocol"
)

const outputLimit = 4 << 20

// maxRunning caps how many fnas processes run at once. Each one is a root
// Python process, so a flood of requests mustn't be able to fill the machine.
const maxRunning = 8

var slots = make(chan struct{}, maxRunning)

var (
	socket     = flag.String("socket", protocol.Socket, "where to listen")
	fnasPath   = flag.String("fnas", "/usr/sbin/fnas", "the fnas command")
	service    = flag.String("pam-service", "fellowship-nas-web", "the PAM service that checks passwords")
	frontUser  = flag.String("front-user", "fnas-web", "the account the web front runs as")
	adminGroup = flag.String("admin-group", "nas-admins", "the group that may manage the NAS")
)

// limited keeps the first outputLimit bytes, so a runaway command can't fill memory
type limited struct{ bytes.Buffer }

func (l *limited) Write(data []byte) (int, error) {
	if room := outputLimit - l.Len(); room > 0 {
		l.Buffer.Write(data[:min(len(data), room)])
	}
	return len(data), nil
}

func runFnas(args []string, input string) (string, string, error) {
	// Wait for a free slot rather than refusing: the work still has to happen,
	// and a queue of a few hundred goroutines costs nothing
	slots <- struct{}{}
	defer func() { <-slots }()
	command := exec.Command(*fnasPath, args...)
	command.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8"}
	command.Stdin = strings.NewReader(input)
	var stdout, stderr limited
	command.Stdout, command.Stderr = &stdout, &stderr
	err := command.Run()
	return stdout.String(), stderr.String(), err
}

func lookup(name string) (helper.Account, error) {
	account, err := user.Lookup(name)
	if err != nil {
		return helper.Account{}, err
	}
	uid, _ := strconv.Atoi(account.Uid)
	gid, _ := strconv.Atoi(account.Gid)
	ids, err := account.GroupIds()
	if err != nil {
		return helper.Account{}, err
	}
	groups := make([]uint32, 0, len(ids))
	for _, id := range ids {
		number, err := strconv.Atoi(id)
		if err != nil {
			continue
		}
		groups = append(groups, uint32(number))
	}
	admins, err := user.LookupGroup(*adminGroup)
	return helper.Account{UID: uid, GID: gid, Groups: groups,
		Admin: err == nil && slices.Contains(ids, admins.Gid)}, nil
}

// runChild runs one file operation in a child of this binary, already set to the
// person's uid, gid and groups. The kernel does the permission checking, so a
// mistake in the path check can't reach a file the person couldn't open
// themselves.
func runChild(account helper.Account, op, path string, start, length int64) (io.ReadCloser, error) {
	self, err := os.Executable()
	if err != nil {
		return nil, err
	}
	args := []string{"file-child", op, path}
	if op == "read" {
		args = append(args, strconv.FormatInt(start, 10), strconv.FormatInt(length, 10))
	}
	command := exec.Command(self, args...)
	command.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8"}
	command.SysProcAttr = &syscall.SysProcAttr{
		Credential: &syscall.Credential{Uid: uint32(account.UID), Gid: uint32(account.GID),
			Groups: account.Groups, NoSetGroups: len(account.Groups) == 0},
	}
	out, err := command.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := command.Start(); err != nil {
		return nil, err
	}
	// The child is reaped by the same reader finishing, so a half-read file
	// still ends the process rather than leaving it behind
	return &childOut{ReadCloser: out, command: command}, nil
}

type childOut struct {
	io.ReadCloser
	command *exec.Cmd
}

func (c *childOut) Close() error {
	c.command.Process.Kill()
	c.command.Wait()
	return c.ReadCloser.Close()
}

func authenticate(name, password string) error {
	err := pam.Authenticate(*service, name, password)
	if errors.Is(err, pam.ErrExpired) {
		return helper.ErrExpired
	}
	return err
}

func alert(title, message string) {
	if _, stderr, err := runFnas([]string{"alert", "send", "--severity", "high", title, message}, ""); err != nil {
		log.Printf("couldn't send an alert: %v %s", err, stderr)
	}
}

func peerUID(conn *net.UnixConn) (int, error) {
	raw, err := conn.SyscallConn()
	if err != nil {
		return -1, err
	}
	var credentials *syscall.Ucred
	var credentialsErr error
	if err := raw.Control(func(fd uintptr) {
		credentials, credentialsErr = syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	}); err != nil {
		return -1, err
	}
	if credentialsErr != nil {
		return -1, credentialsErr
	}
	return int(credentials.Uid), nil
}

func serve(h *helper.Helper, conn *net.UnixConn, allowed int) {
	defer conn.Close()
	if uid, err := peerUID(conn); err != nil || (uid != allowed && uid != 0) {
		log.Printf("refused a connection from uid %d", uid)
		return
	}
	var request protocol.Request
	if err := json.NewDecoder(io.LimitReader(conn, protocol.MaxMessage)).Decode(&request); err != nil {
		return
	}
	response, body := h.Stream(request)
	if err := json.NewEncoder(conn).Encode(response); err != nil {
		return
	}
	if body != nil {
		defer body.Close()
		io.Copy(conn, body)
	}
}

func main() {
	// The helper re-executes itself, already running as the person, for file
	// work. This is that second life: no flags, no socket, just the one job.
	if len(os.Args) > 1 && os.Args[1] == "file-child" {
		os.Exit(filechild.Run(os.Args[2:]))
	}
	flag.Parse()
	log.SetFlags(0)
	front, err := user.Lookup(*frontUser)
	if err != nil {
		log.Fatalf("the web front's account: %v", err)
	}
	frontUID, _ := strconv.Atoi(front.Uid)
	frontGID, _ := strconv.Atoi(front.Gid)

	directory := filepath.Dir(*socket)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		log.Fatal(err)
	}
	os.Chown(directory, 0, frontGID)
	os.Remove(*socket)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: *socket, Net: "unix"})
	if err != nil {
		log.Fatal(err)
	}
	if err := os.Chown(*socket, 0, frontGID); err != nil {
		log.Fatal(err)
	}
	if err := os.Chmod(*socket, 0o660); err != nil {
		log.Fatal(err)
	}

	h := helper.New(helper.Config{Authenticate: authenticate, Lookup: lookup, Fnas: runFnas,
		Child: runChild, Alert: alert})
	log.Printf("listening on %s", *socket)
	for {
		conn, err := listener.AcceptUnix()
		if err != nil {
			log.Fatal(err)
		}
		go serve(h, conn, frontUID)
	}
}
