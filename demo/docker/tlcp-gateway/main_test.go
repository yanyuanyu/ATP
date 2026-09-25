package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/tjfoc/gmsm/gmtls"
)

func TestParsePeers(t *testing.T) {
	peers, err := parsePeers("server-a:7443=tlcp-a:8443, server-b:7443=tlcp-b:8443")
	if err != nil {
		t.Fatal(err)
	}
	if peers["server-a:7443"] != "tlcp-a:8443" {
		t.Fatalf("unexpected peer mapping: %#v", peers)
	}
}

func TestParsePeersRejectsMalformedEntry(t *testing.T) {
	if _, err := parsePeers("not-a-mapping"); err == nil {
		t.Fatal("expected malformed peer mapping to fail")
	}
}

func TestGeneratedTLCPKeyPairsLoad(t *testing.T) {
	directory := t.TempDir()
	if err := generateCertificates(directory); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"tlcp-ca.crt",
		"family.test-tlcp-sign.crt",
		"family.test-tlcp-sign.key",
		"family.test-tlcp-enc.crt",
		"family.test-tlcp-enc.key",
	} {
		if _, err := os.Stat(filepath.Join(directory, name)); err != nil {
			t.Fatalf("missing generated file %s: %v", name, err)
		}
	}
	if _, err := gmtls.LoadX509KeyPair(
		filepath.Join(directory, "family.test-tlcp-sign.crt"),
		filepath.Join(directory, "family.test-tlcp-sign.key"),
	); err != nil {
		t.Fatalf("generated signing pair does not load: %v", err)
	}
}

func TestCipherSuiteNames(t *testing.T) {
	if got := cipherSuiteName(gmtls.GMTLS_SM2_WITH_SM4_SM3); got != "ECC-SM2-SM4-CBC-SM3" {
		t.Fatalf("unexpected static cipher name: %s", got)
	}
	if got := cipherSuiteName(gmtls.GMTLS_ECDHE_SM2_WITH_SM4_SM3); got != "ECDHE-SM2-SM4-CBC-SM3" {
		t.Fatalf("unexpected ECDHE cipher name: %s", got)
	}
}
