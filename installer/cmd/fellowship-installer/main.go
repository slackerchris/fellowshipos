// fellowship-installer runs in the live system when the boot menu's NAS
// entry is chosen. It serves the install page to the machine's own screen on
// loopback, and, only when asked for with -remote, to another computer over
// HTTPS once the pairing code shown on that screen has been typed.
package main

import (
	"bufio"
	"bytes"
	"crypto/tls"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"fellowship-installer/internal/disks"
	"fellowship-installer/internal/edition"
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
	"fellowship-installer/internal/netif"
	"fellowship-installer/internal/server"
)

var (
	listen = flag.String("listen", "127.0.0.1:80", "where the machine's own screen reaches the install page")
	remote = flag.String("remote", "",
		"also serve another computer over HTTPS at this address, such as 192.168.1.20:8443 or :8443")
	static   = flag.String("static", "/usr/share/fellowship-installer", "the install page's files")
	squashfs = flag.String("squashfs", "/run/live/medium/live/filesystem.squashfs", "the live system's image")
	rootfs   = flag.String("rootfs", "/run/live/rootfs/filesystem.squashfs", "where the live image is mounted")
	target   = flag.String("target", "/target", "where the new system is assembled")
	which    = flag.String("edition", "", "which image this is: nas or multi (default: from the boot menu)")
	demo     = flag.Bool("demo", false, "show sample disks and pretend to install, touching nothing")
)

// chosenEdition is the image this installer is running from: the flag when it
// was given, otherwise what the boot menu put on the kernel command line.
func chosenEdition() (edition.Edition, error) {
	if *which != "" {
		return edition.Parse(*which)
	}
	return edition.FromCmdline(kernelCmdline()), nil
}

// kernelCmdline is how the boot menu says which image was started, and whether
// another computer should be served.
func kernelCmdline() string {
	content, err := os.ReadFile("/proc/cmdline")
	if err != nil {
		return ""
	}
	return string(content)
}

// The NAS role's packages and ZFS's build, added after the install
const roleAllowance = 2 * layout.GiB

type runner struct{ logf func(string) }

func (r runner) Run(input string, name string, args ...string) (string, error) {
	command := exec.Command(name, args...)
	command.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8", "HOME=/root"}
	command.Stdin = strings.NewReader(input)
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	err := command.Run()
	if err != nil {
		// What the command said is the useful part of the error
		if text := strings.TrimSpace(stderr.String()); text != "" {
			lines := strings.Split(text, "\n")
			r.logf(lines[len(lines)-1])
			return stdout.String(), &commandError{err, lines[len(lines)-1]}
		}
	}
	return stdout.String(), err
}

type commandError struct {
	err  error
	text string
}

func (e *commandError) Error() string { return e.text }
func (e *commandError) Unwrap() error { return e.err }

// installedSize measures the live system, which is what gets copied
func installedSize() int64 {
	output, err := exec.Command("du", "-sb", "-x", *rootfs).Output()
	if err != nil {
		return 4 * layout.GiB
	}
	size, _ := strconv.ParseInt(strings.Fields(string(output))[0], 10, 64)
	return size + roleAllowance
}

func memory() int64 {
	file, err := os.Open("/proc/meminfo")
	if err != nil {
		return 4 * layout.GiB
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) >= 2 && fields[0] == "MemTotal:" {
			kb, _ := strconv.ParseInt(fields[1], 10, 64)
			return kb * 1024
		}
	}
	return 4 * layout.GiB
}

func timezones() ([]string, error) {
	output, err := exec.Command("timedatectl", "list-timezones").Output()
	if err != nil {
		return nil, err
	}
	return strings.Fields(string(output)), nil
}

// addresses are where other computers can reach the install page: the
// wired ports' addresses, not Docker's bridge
func addresses() []string {
	var found []string
	for _, name := range netif.Wired("/sys/class/net") {
		iface, err := net.InterfaceByName(name)
		if err != nil || iface.Flags&net.FlagUp == 0 {
			continue
		}
		addrs, _ := iface.Addrs()
		for _, addr := range addrs {
			if ip, _, err := net.ParseCIDR(addr.String()); err == nil && ip.To4() != nil {
				found = append(found, ip.String())
			}
		}
	}
	return found
}

func main() {
	flag.Parse()
	log.SetFlags(0)
	chosen, err := chosenEdition()
	if err != nil {
		log.Fatal(err)
	}
	if *demo {
		log.Printf("demo: sample disks, nothing is written")
		serve(server.New(demoMachine(), os.DirFS(*static), chosen), chosen)
		return
	}
	installed, mem := installedSize(), memory()
	_, efiErr := os.Stat("/sys/firmware/efi")
	liveDisk := disks.LiveDisk()
	log.Printf("system needs about %s; memory %s; booted from %s", layout.Size(installed), layout.Size(mem), liveDisk)

	machine := server.Machine{
		Installed: installed, Memory: mem,
		Disks: func() ([]disks.Disk, error) {
			return disks.List(liveDisk, layout.MinSystem(installed)+layout.EFI+4*layout.GiB)
		},
		Timezones: timezones,
		Addresses: addresses,
		Install: func(settings engine.Settings, step, logf func(string)) error {
			e := &engine.Engine{Run: runner{logf}, Target: *target, Squashfs: *squashfs, Installed: installed,
				Memory: mem, EFI: efiErr == nil, WholeDisk: chosen.WholeDisk(),
				Network: netif.Wired("/sys/class/net"), Step: step, Log: logf}
			return e.Install(settings)
		},
		Reboot: func() error {
			time.Sleep(time.Second)
			syscall.Sync()
			return exec.Command("systemctl", "reboot").Run()
		},
	}
	installer := server.New(machine, os.DirFS(*static), chosen)
	serve(installer, chosen)
}

// serve runs the page the machine's own screen opens, on loopback, and, when it
// was asked for by name, a second listener for another computer over HTTPS.
// Nothing installs from the network unless that was asked for, and the remote
// page's certificate is printed here for the person at the machine to compare
// with the one their browser shows.
func serve(installer *server.Server, which edition.Edition) {
	local := &http.Server{Addr: *listen, Handler: installer.Handler(), ReadHeaderTimeout: 10 * time.Second}
	address := *remote
	if address == "" {
		address = edition.RemoteFromCmdline(kernelCmdline())
	}
	if address == "" {
		log.Printf("%s page on http://%s, this machine only; add fellowship.installer.remote=ADDRESS to the boot menu, or pass -remote, to install from another computer",
			which.Title(), *listen)
		log.Fatal(local.ListenAndServe())
	}
	certificate, fingerprint, err := selfSigned(addresses())
	if err != nil {
		log.Fatalf("the certificate for another computer: %v", err)
	}
	away := &http.Server{Addr: address, Handler: installer.Handler(),
		TLSConfig:         &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{certificate}},
		ReadHeaderTimeout: 10 * time.Second}
	// So the page can tell the person at the screen where to send the other one
	installer.RemoteURLs = remoteURLs(address)
	go func() { log.Fatal(away.ListenAndServeTLS("", "")) }()
	for _, url := range installer.RemoteURLs {
		// An address with no host part, before the network has given this
		// machine one, would print as https://:443, which nobody can open
		if host, port, err := net.SplitHostPort(strings.TrimPrefix(url, "https://")); err == nil && host == "" {
			log.Printf("%s page for another computer on port %s of this machine's address, once the network gives it one (ip -brief address shows it)",
				which.Title(), port)
			continue
		}
		log.Printf("%s page for another computer on %s", which.Title(), url)
	}
	log.Printf("  the certificate's SHA-256 is %s", fingerprint)
	log.Printf("  compare that with the certificate the browser shows, then type the pairing code %s",
		installer.Code())
	log.Fatal(local.ListenAndServe())
}

// remoteURLs is where the other computer should open: for an address with no
// host part, every wired address this machine has.
func remoteURLs(address string) []string {
	host, port, err := net.SplitHostPort(address)
	if err != nil || host != "" {
		return []string{"https://" + address}
	}
	found := make([]string, 0, len(addresses()))
	for _, one := range addresses() {
		found = append(found, "https://"+net.JoinHostPort(one, port))
	}
	if len(found) == 0 {
		return []string{"https://" + address}
	}
	return found
}
