package main

import (
	"crypto/x509"
	"testing"
)

func TestTheRemoteCertificateNamesThisMachine(t *testing.T) {
	certificate, fingerprint, err := selfSigned([]string{"192.168.1.20"})
	if err != nil {
		t.Fatalf("selfSigned: %v", err)
	}
	if len(certificate.Certificate) == 0 {
		t.Fatal("no certificate was made")
	}
	parsed, err := x509.ParseCertificate(certificate.Certificate[0])
	if err != nil {
		t.Fatalf("parsing the certificate: %v", err)
	}
	// The browser is told which certificate to expect, so it has to name the
	// address the console printed
	if len(parsed.IPAddresses) != 1 || parsed.IPAddresses[0].String() != "192.168.1.20" {
		t.Errorf("the certificate names %v, not the machine's address", parsed.IPAddresses)
	}
	if parsed.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth {
		t.Errorf("the certificate is for %v, not serving", parsed.ExtKeyUsage)
	}
	// What the console prints is what the browser shows: the certificate's own
	// SHA-256, in the same colon-separated form
	if got := fingerprintOf(certificate.Certificate[0]); got != fingerprint {
		t.Errorf("the console shows %q and the certificate is %q", fingerprint, got)
	}
	if len(fingerprint) != 32*3-1 {
		t.Errorf("%q is not a colon-separated SHA-256", fingerprint)
	}
}

func TestRemoteURLsSayWhereToOpen(t *testing.T) {
	if got := remoteURLs("192.168.1.20:8443"); len(got) != 1 || got[0] != "https://192.168.1.20:8443" {
		t.Errorf("an address with a host gave %v", got)
	}
	// A bare port means every wired address, so -remote :8443 needs no knowledge
	// of the network, and there is always something to print
	if got := remoteURLs(":8443"); len(got) == 0 {
		t.Error("an address with only a port gave nothing")
	}
}
