// fellowship-installer runs in the live system when the boot menu's NAS
// entry is chosen. It serves the install page to the machine's own screen
// and, after pairing, to other computers on the network.
package main

import (
	"bufio"
	"bytes"
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
	"fellowship-installer/internal/engine"
	"fellowship-installer/internal/layout"
	"fellowship-installer/internal/netif"
	"fellowship-installer/internal/server"
)

var (
	listen   = flag.String("listen", ":80", "where to serve the install page")
	static   = flag.String("static", "/usr/share/fellowship-installer", "the install page's files")
	squashfs = flag.String("squashfs", "/run/live/medium/live/filesystem.squashfs", "the live system's image")
	rootfs   = flag.String("rootfs", "/run/live/rootfs/filesystem.squashfs", "where the live image is mounted")
	target   = flag.String("target", "/target", "where the new system is assembled")
	demo     = flag.Bool("demo", false, "show sample disks and pretend to install, touching nothing")
)

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
	if *demo {
		log.Printf("demo: sample disks, nothing is written; install page on %s", *listen)
		log.Fatal(http.ListenAndServe(*listen, server.New(demoMachine(), os.DirFS(*static)).Handler()))
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
				Memory: mem, EFI: efiErr == nil, Network: netif.Wired("/sys/class/net"), Step: step, Log: logf}
			return e.Install(settings)
		},
		Reboot: func() error {
			time.Sleep(time.Second)
			syscall.Sync()
			return exec.Command("systemctl", "reboot").Run()
		},
	}
	installer := server.New(machine, os.DirFS(*static))
	httpServer := &http.Server{Addr: *listen, Handler: installer.Handler(), ReadHeaderTimeout: 10 * time.Second}
	// Also on the console, for a machine whose screen can't show the page
	log.Printf("install page on %s; from another computer open http://%s and enter the pairing code %s",
		*listen, strings.Join(addresses(), " or http://"), installer.Code())
	log.Fatal(httpServer.ListenAndServe())
}
