#!/bin/bash
# One-shot setup: build -> gen-certs -> up -> wait for health -> update-atk
set -e
cd "$(dirname "$0")/.."

echo '=== Step 1: Build Docker images ==='
bash scripts/build.sh
bash scripts/build-demo.sh

echo '=== Step 2: Generate TLS certificates ==='
bash scripts/gen-certs.sh

echo '=== Step 3: Start containers ==='
docker compose up -d

echo '=== Step 4: Wait for all servers to be healthy ==='
# Health check runs inside each server container — host cannot resolve
# docker-internal DNS names (server-family.family.test etc.) on macOS.
until docker compose exec -T server-family curl -sk https://localhost:7443/.well-known/atp/v1/health > /dev/null 2>&1; do
  echo '  waiting for server-family...'; sleep 2;
done
echo '  server-family ready.'
until docker compose exec -T server-hotel curl -sk https://localhost:7443/.well-known/atp/v1/health > /dev/null 2>&1; do
  echo '  waiting for server-hotel...'; sleep 2;
done
echo '  server-hotel ready.'
until docker compose exec -T server-payment curl -sk https://localhost:7443/.well-known/atp/v1/health > /dev/null 2>&1; do
  echo '  waiting for server-payment...'; sleep 2;
done
echo '  server-payment ready.'

echo '=== Step 5: Update ATK DNS records ==='
bash scripts/update-atk.sh

echo '=== Setup complete ==='
