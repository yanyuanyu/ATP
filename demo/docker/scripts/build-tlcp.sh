#!/bin/bash
# Build the Docker-internal TLCP relay used for ATP server-to-server traffic.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -t atp-tlcp-gateway:latest tlcp-gateway
echo "Image atp-tlcp-gateway:latest built."
