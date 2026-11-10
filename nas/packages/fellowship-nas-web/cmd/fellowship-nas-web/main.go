// fellowship-nas-web serves the FellowshipNAS desktop over HTTPS. It runs as
// the unprivileged fnas-web account and asks the helper for everything else.
package main

import (
	"crypto/tls"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"fellowship-nas-web/internal/front"
	"fellowship-nas-web/internal/protocol"
)

var (
	listen   = flag.String("listen", ":443", "the HTTPS address")
	redirect = flag.String("redirect", ":80", "an HTTP address that sends browsers to HTTPS; empty for none")
	static   = flag.String("static", "/usr/share/fellowship-nas-web", "the desktop's files")
	cert     = flag.String("cert", "/etc/fellowship-nas/web/cert.pem", "the certificate")
	key      = flag.String("key", "/etc/fellowship-nas/web/key.pem", "the certificate's private key")
	socket   = flag.String("helper", protocol.Socket, "the helper's socket")
	devHTTP  = flag.Bool("dev-http", false, "serve plain HTTP for trying the desktop on localhost; never on a real NAS")
)

func toHTTPS(w http.ResponseWriter, r *http.Request) {
	host, _, err := net.SplitHostPort(r.Host)
	if err != nil {
		host = r.Host
	}
	if _, port, err := net.SplitHostPort(*listen); err == nil && port != "443" {
		host = net.JoinHostPort(host, port)
	}
	http.Redirect(w, r, "https://"+host+r.URL.RequestURI(), http.StatusMovedPermanently)
}

func main() {
	flag.Parse()
	log.SetFlags(0)
	server := &front.Server{
		Call: func(request protocol.Request) (protocol.Response, error) {
			return protocol.Call(*socket, request)
		},
		Stream: func(request protocol.Request) (protocol.Response, io.ReadCloser, error) {
			return protocol.Stream(*socket, request)
		},
		Send: func(request protocol.Request, body io.Reader) (protocol.Response, error) {
			return protocol.Send(*socket, request, body)
		},
		Static: os.DirFS(*static),
		Passwd: "/etc/passwd",
	}
	if *devHTTP {
		log.Printf("WARNING: plain HTTP for development on %s; passwords cross the network unencrypted", *listen)
		server.Scheme = "http"
		plain := &http.Server{Addr: *listen, Handler: server.Handler(), ReadHeaderTimeout: 10 * time.Second,
			ReadTimeout: 60 * time.Second}
		log.Fatal(plain.ListenAndServe())
	}
	pair, err := tls.LoadX509KeyPair(*cert, *key)
	if err != nil {
		log.Fatalf("the certificate: %v", err)
	}
	// No WriteTimeout: the jobs stream sends events for as long as a job runs.
	// ReadTimeout covers the whole request, so a slow body can't hold a worker.
	https := &http.Server{
		Addr:              *listen,
		Handler:           server.Handler(),
		TLSConfig:         &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{pair}},
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		IdleTimeout:       2 * time.Minute,
		MaxHeaderBytes:    64 << 10,
	}
	if *redirect != "" {
		plain := &http.Server{Addr: *redirect, Handler: http.HandlerFunc(toHTTPS),
			ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second}
		go func() { log.Fatal(plain.ListenAndServe()) }()
	}
	log.Printf("serving the desktop on %s", *listen)
	log.Fatal(https.ListenAndServeTLS("", ""))
}
