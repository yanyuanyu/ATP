#!/bin/bash
# Generate CA + 3 domain certs (family.test, hotel.test, payment.test).
set -euo pipefail
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$DOCKER_DIR/certs"
mkdir -p "$CERT_DIR"

# 1. Hackathon CA
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -days 365 -nodes \
  -subj "/CN=ATP Hackathon CA" \
  -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt"

# family.test
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -subj "/CN=family.test" \
  -addext "subjectAltName=DNS:family.test,DNS:server-family.family.test,DNS:*.family.test" \
  -keyout "$CERT_DIR/family.test.key" -out "$CERT_DIR/family.test.csr"

openssl x509 -req -in "$CERT_DIR/family.test.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -days 365 -copy_extensions copy \
  -out "$CERT_DIR/family.test.crt"

# hotel.test
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -subj "/CN=hotel.test" \
  -addext "subjectAltName=DNS:hotel.test,DNS:server-hotel.hotel.test,DNS:*.hotel.test" \
  -keyout "$CERT_DIR/hotel.test.key" -out "$CERT_DIR/hotel.test.csr"

openssl x509 -req -in "$CERT_DIR/hotel.test.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -days 365 -copy_extensions copy \
  -out "$CERT_DIR/hotel.test.crt"

# payment.test
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -subj "/CN=payment.test" \
  -addext "subjectAltName=DNS:payment.test,DNS:server-payment.payment.test,DNS:*.payment.test" \
  -keyout "$CERT_DIR/payment.test.key" -out "$CERT_DIR/payment.test.csr"

openssl x509 -req -in "$CERT_DIR/payment.test.csr" \
  -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
  -days 365 -copy_extensions copy \
  -out "$CERT_DIR/payment.test.crt"

rm -f "$CERT_DIR"/*.csr "$CERT_DIR"/*.srl
echo "Certificates generated in $CERT_DIR"
