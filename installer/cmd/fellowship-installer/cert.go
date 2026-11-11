package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"strings"
	"time"
)

// selfSigned makes the certificate the remote install page is served with. It
// is made fresh on every boot, lasts a day, and names the machine's own
// addresses, so the person at the machine can be told exactly which
// certificate to expect: the fingerprint is printed on the console beside the
// address, and the browser shows the same one.
func selfSigned(addresses []string) (tls.Certificate, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, "", err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return tls.Certificate{}, "", err
	}
	template := x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "FellowshipOS installer"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              []string{"localhost"},
	}
	for _, address := range addresses {
		if ip := net.ParseIP(address); ip != nil {
			template.IPAddresses = append(template.IPAddresses, ip)
		}
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
	if err != nil {
		return tls.Certificate{}, "", err
	}
	written, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return tls.Certificate{}, "", err
	}
	certificate, err := tls.X509KeyPair(
		pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: written}))
	if err != nil {
		return tls.Certificate{}, "", err
	}
	return certificate, fingerprintOf(der), nil
}

// fingerprintOf is a certificate's SHA-256, in the colon-separated form a
// browser's certificate viewer shows.
func fingerprintOf(der []byte) string {
	sum := sha256.Sum256(der)
	parts := make([]string, 0, len(sum))
	for _, one := range sum {
		parts = append(parts, fmt.Sprintf("%02X", one))
	}
	return strings.Join(parts, ":")
}
