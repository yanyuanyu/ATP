#!/bin/bash
# Build the shadcn/React frontend and atp-hackthon-demo controller image.
# Prerequisite: atp-hackthon:latest must already be built (scripts/build.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Building demo frontend..."
(
  cd demo/web
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund
  else
    npm install --no-audit --no-fund
  fi
  npm run build
)

docker build --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.org/simple}" -t atp-hackthon-demo:latest -f demo/Dockerfile .

echo "Image atp-hackthon-demo:latest built."
