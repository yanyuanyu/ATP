package main

import (
	"context"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	stdx509 "crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/tjfoc/gmsm/gmtls"
	"github.com/tjfoc/gmsm/sm2"
	"github.com/tjfoc/gmsm/sm3"
	gmx509 "github.com/tjfoc/gmsm/x509"
)

const (
	targetHeader     = "X-ATP-TLCP-Target"
	serverNameHeader = "X-ATP-TLCP-Server-Name"
)

type gateway struct {
	peers      map[string]string
	rootCAs    *gmx509.CertPool
	backendURL *url.URL
	backendTLS *tls.Config
}

func randomSerial() (*big.Int, error) {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	return rand.Int(rand.Reader, limit)
}

func subjectKeyID(publicKey *sm2.PublicKey) []byte {
	encoded := elliptic.Marshal(publicKey.Curve, publicKey.X, publicKey.Y)
	digest := sm3.Sm3Sum(encoded)
	return digest[:20]
}

func writeCertificate(path string, certificate []byte) error {
	return os.WriteFile(path, certificate, 0o644)
}

func writePrivateKey(path string, privateKey *sm2.PrivateKey) error {
	encoded, err := gmx509.WritePrivateKeyToPem(privateKey, nil)
	if err != nil {
		return err
	}
	return os.WriteFile(path, encoded, 0o600)
}

func createLeaf(
	directory string,
	domain string,
	purpose string,
	keyUsage gmx509.KeyUsage,
	ca *gmx509.Certificate,
	caKey *sm2.PrivateKey,
) error {
	privateKey, err := sm2.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	serial, err := randomSerial()
	if err != nil {
		return err
	}
	prefix := strings.SplitN(domain, ".", 2)[0]
	serverName := "server-" + prefix + "." + domain
	template := &gmx509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: serverName, Organization: []string{"ATP Competition"}},
		NotBefore:             time.Now().Add(-5 * time.Minute),
		NotAfter:              time.Now().Add(365 * 24 * time.Hour),
		SignatureAlgorithm:    gmx509.SM2WithSM3,
		KeyUsage:              keyUsage,
		ExtKeyUsage:           []gmx509.ExtKeyUsage{gmx509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  false,
		SubjectKeyId:          subjectKeyID(&privateKey.PublicKey),
		DNSNames: []string{
			domain,
			serverName,
			"tlcp-" + prefix + "." + domain,
		},
	}
	certificate, err := gmx509.CreateCertificateToPem(
		template, ca, &privateKey.PublicKey, caKey,
	)
	if err != nil {
		return err
	}
	base := filepath.Join(directory, domain+"-tlcp-"+purpose)
	if err := writePrivateKey(base+".key", privateKey); err != nil {
		return err
	}
	return writeCertificate(base+".crt", certificate)
}

func generateCertificates(directory string) error {
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	caKey, err := sm2.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	serial, err := randomSerial()
	if err != nil {
		return err
	}
	ca := &gmx509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "ATP TLCP SM2 CA", Organization: []string{"ATP Competition"}},
		NotBefore:             time.Now().Add(-5 * time.Minute),
		NotAfter:              time.Now().Add(10 * 365 * 24 * time.Hour),
		SignatureAlgorithm:    gmx509.SM2WithSM3,
		KeyUsage:              gmx509.KeyUsageCertSign | gmx509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		SubjectKeyId:          subjectKeyID(&caKey.PublicKey),
	}
	caPEM, err := gmx509.CreateCertificateToPem(ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	if err := writePrivateKey(filepath.Join(directory, "tlcp-ca.key"), caKey); err != nil {
		return err
	}
	if err := writeCertificate(filepath.Join(directory, "tlcp-ca.crt"), caPEM); err != nil {
		return err
	}
	for _, domain := range []string{"family.test", "hotel.test", "payment.test"} {
		if err := createLeaf(
			directory, domain, "sign", gmx509.KeyUsageDigitalSignature, ca, caKey,
		); err != nil {
			return err
		}
		if err := createLeaf(
			directory,
			domain,
			"enc",
			gmx509.KeyUsageKeyEncipherment|gmx509.KeyUsageKeyAgreement,
			ca,
			caKey,
		); err != nil {
			return err
		}
	}
	return nil
}

func requiredEnv(name string) string {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		log.Fatalf("%s is required", name)
	}
	return value
}

func parsePeers(raw string) (map[string]string, error) {
	peers := make(map[string]string)
	for _, entry := range strings.Split(raw, ",") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		parts := strings.SplitN(entry, "=", 2)
		if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" || strings.TrimSpace(parts[1]) == "" {
			return nil, fmt.Errorf("invalid ATP_TLCP_PEERS entry %q", entry)
		}
		peers[strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
	}
	if len(peers) == 0 {
		return nil, errors.New("ATP_TLCP_PEERS must contain at least one peer")
	}
	return peers, nil
}

func cipherSuiteName(cipher uint16) string {
	switch cipher {
	case gmtls.GMTLS_SM2_WITH_SM4_SM3:
		return "ECC-SM2-SM4-CBC-SM3"
	case gmtls.GMTLS_ECDHE_SM2_WITH_SM4_SM3:
		return "ECDHE-SM2-SM4-CBC-SM3"
	default:
		return fmt.Sprintf("0x%04x", cipher)
	}
}

func copyResponse(w http.ResponseWriter, response *http.Response, cipher string) {
	defer response.Body.Close()
	for name, values := range response.Header {
		for _, value := range values {
			w.Header().Add(name, value)
		}
	}
	w.Header().Set("X-ATP-Transport", "TLCPv1.1")
	w.Header().Set("X-ATP-TLCP-Cipher", cipher)
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
}

func (g *gateway) relay(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/health" {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","transport":"TLCPv1.1","cipher":"ECC-SM2-SM4-CBC-SM3"}`))
		return
	}

	target := strings.TrimSpace(r.Header.Get(targetHeader))
	serverName := strings.TrimSpace(r.Header.Get(serverNameHeader))
	peerAddress, allowed := g.peers[target]
	if !allowed || serverName == "" {
		http.Error(w, "TLCP target is not allow-listed", http.StatusForbidden)
		return
	}

	request := r.Clone(r.Context())
	request.URL.Scheme = "https"
	request.URL.Host = peerAddress
	request.Host = serverName
	request.RequestURI = ""
	request.Header.Del(targetHeader)
	request.Header.Del(serverNameHeader)

	tlsConfig := &gmtls.Config{
		GMSupport:  gmtls.NewGMSupport(),
		RootCAs:    g.rootCAs,
		ServerName: serverName,
		CipherSuites: []uint16{
			gmtls.GMTLS_SM2_WITH_SM4_SM3,
			gmtls.GMTLS_ECDHE_SM2_WITH_SM4_SM3,
		},
	}
	dialer := &net.Dialer{Timeout: 15 * time.Second}
	var negotiatedCipher uint16
	transport := &http.Transport{
		DisableKeepAlives: true,
		DialTLSContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			connection, err := gmtls.DialWithDialer(dialer, network, peerAddress, tlsConfig)
			if err != nil {
				return nil, err
			}
			state := connection.ConnectionState()
			negotiatedCipher = state.CipherSuite
			log.Printf(
				"TLCP established target=%s peer=%s version=0x%04x cipher=%s",
				target,
				peerAddress,
				state.Version,
				cipherSuiteName(state.CipherSuite),
			)
			return connection, nil
		},
	}
	response, err := transport.RoundTrip(request)
	if err != nil {
		log.Printf("TLCP relay to %s (%s) failed: %v", target, peerAddress, err)
		http.Error(w, "TLCP relay failed", http.StatusBadGateway)
		return
	}
	copyResponse(w, response, cipherSuiteName(negotiatedCipher))
}

func (g *gateway) backendProxy() http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(g.backendURL)
	proxy.Transport = &http.Transport{
		TLSClientConfig: g.backendTLS.Clone(),
	}
	originalDirector := proxy.Director
	proxy.Director = func(request *http.Request) {
		originalDirector(request)
		request.Header.Del(targetHeader)
		request.Header.Del(serverNameHeader)
		request.Header.Set("X-ATP-Transport", "TLCPv1.1")
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		log.Printf("backend proxy failed: %v", err)
		http.Error(w, "ATP backend unavailable", http.StatusBadGateway)
	}
	return proxy
}

func main() {
	if len(os.Args) == 3 && os.Args[1] == "certgen" {
		if err := generateCertificates(os.Args[2]); err != nil {
			log.Fatal(err)
		}
		log.Printf("SM2 TLCP certificate set generated in %s", os.Args[2])
		return
	}

	peers, err := parsePeers(requiredEnv("ATP_TLCP_PEERS"))
	if err != nil {
		log.Fatal(err)
	}
	backendURL, err := url.Parse(requiredEnv("ATP_BACKEND_URL"))
	if err != nil {
		log.Fatalf("invalid ATP_BACKEND_URL: %v", err)
	}
	backendCAPEM, err := os.ReadFile(requiredEnv("ATP_BACKEND_CA"))
	if err != nil {
		log.Fatal(err)
	}
	backendRoots := stdx509.NewCertPool()
	if !backendRoots.AppendCertsFromPEM(backendCAPEM) {
		log.Fatal("failed to load ATP backend CA")
	}
	backendTLS := &tls.Config{
		MinVersion: tls.VersionTLS13,
		RootCAs:    backendRoots,
		ServerName: requiredEnv("ATP_BACKEND_SERVER_NAME"),
	}

	caPEM, err := os.ReadFile(requiredEnv("ATP_TLCP_CA"))
	if err != nil {
		log.Fatal(err)
	}
	rootCAs := gmx509.NewCertPool()
	if !rootCAs.AppendCertsFromPEM(caPEM) {
		log.Fatal("failed to load TLCP CA")
	}

	signCert, err := gmtls.LoadX509KeyPair(
		requiredEnv("ATP_TLCP_SIGN_CERT"), requiredEnv("ATP_TLCP_SIGN_KEY"),
	)
	if err != nil {
		log.Fatalf("load signing certificate: %v", err)
	}
	encCert, err := gmtls.LoadX509KeyPair(
		requiredEnv("ATP_TLCP_ENC_CERT"), requiredEnv("ATP_TLCP_ENC_KEY"),
	)
	if err != nil {
		log.Fatalf("load encryption certificate: %v", err)
	}

	g := &gateway{
		peers: peers, rootCAs: rootCAs, backendURL: backendURL, backendTLS: backendTLS,
	}
	internalAddress := strings.TrimSpace(os.Getenv("ATP_INTERNAL_ADDR"))
	if internalAddress == "" {
		internalAddress = ":9080"
	}
	tlcpAddress := strings.TrimSpace(os.Getenv("ATP_TLCP_ADDR"))
	if tlcpAddress == "" {
		tlcpAddress = ":8443"
	}

	go func() {
		log.Printf("internal relay listening on %s", internalAddress)
		server := &http.Server{
			Addr:              internalAddress,
			Handler:           http.HandlerFunc(g.relay),
			ReadHeaderTimeout: 10 * time.Second,
		}
		log.Fatal(server.ListenAndServe())
	}()

	tlcpConfig := &gmtls.Config{
		GMSupport:    gmtls.NewGMSupport(),
		Certificates: []gmtls.Certificate{signCert, encCert},
		CipherSuites: []uint16{
			gmtls.GMTLS_SM2_WITH_SM4_SM3,
			gmtls.GMTLS_ECDHE_SM2_WITH_SM4_SM3,
		},
	}
	listener, err := gmtls.Listen("tcp", tlcpAddress, tlcpConfig)
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("TLCP listener on %s; backend %s", tlcpAddress, backendURL)
	server := &http.Server{
		Handler:           g.backendProxy(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Fatal(server.Serve(listener))
}
