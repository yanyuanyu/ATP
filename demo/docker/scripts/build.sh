#!/bin/bash
# Build atp-hackthon:latest from this repository's Python package source.
set -euo pipefail
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ATP_DIR="$(cd "$DOCKER_DIR/../.." && pwd)"

command -v docker >/dev/null 2>&1 || { echo "Docker is required. Start Docker Desktop with Linux containers." >&2; exit 1; }
test -f "$ATP_DIR/pyproject.toml" || { echo "ATP source missing: $ATP_DIR" >&2; exit 1; }

# Build only package inputs. Never send local credentials, caches or a venv.
BUILD_CONTEXT="$(mktemp -d)"
trap 'rm -rf -- "$BUILD_CONTEXT"' EXIT
tar --exclude='__pycache__' --exclude='*.pyc' -C "$ATP_DIR" \
  -cf - src tests pyproject.toml README.md LICENSE | tar -C "$BUILD_CONTEXT" -xf -
cp "$DOCKER_DIR/Dockerfile" "$BUILD_CONTEXT/Dockerfile"
docker build --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.org/simple}" -t atp-hackthon:latest "$BUILD_CONTEXT"

echo "Image atp-hackthon:latest built."
