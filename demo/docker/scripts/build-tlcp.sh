#!/bin/bash
# Build the Docker-internal TLCP relay used for ATP server-to-server traffic.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build \
  --build-arg "GOLANG_IMAGE=${GOLANG_IMAGE:-golang:1.24-bookworm}" \
  --build-arg "GOPROXY=${GOPROXY:-https://proxy.golang.org,direct}" \
  -t atp-tlcp-gateway:latest tlcp-gateway
echo "Image atp-tlcp-gateway:latest built."
