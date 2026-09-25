#!/usr/bin/env bash
set -euo pipefail
export MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo '=== SM2/SM3/SM4 + TLCP cross-domain integration ==='
docker compose exec -T server-hotel \
  atp agent register crypto-audit \
  --server server-hotel.hotel.test:7443 \
  -p auditpass --no-verify >/dev/null 2>&1 || true

LOG_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SEND_OUTPUT="$(docker compose exec -T server-family python /agents/check_sm4_tlcp.py send)"
printf '%s\n' "$SEND_OUTPUT"
NONCE="$(printf '%s\n' "$SEND_OUTPUT" | sed -n 's/^NONCE=//p')"
MARKER="$(printf '%s\n' "$SEND_OUTPUT" | sed -n 's/^MARKER=//p')"
test -n "$NONCE"
test -n "$MARKER"

docker compose exec -T server-hotel \
  python /agents/check_sm4_tlcp.py inspect "$NONCE" "$MARKER"
docker compose exec -T server-hotel \
  python /agents/check_sm4_tlcp.py receive "$NONCE" "$MARKER"

docker compose logs --no-color --since "$LOG_SINCE" tlcp-family \
  | grep -q 'TLCP established target=server-hotel.hotel.test:7443.*cipher=.*SM4.*SM3'
echo 'PASS: family→hotel negotiated TLCP with an SM2/SM4/SM3 cipher suite'

STATUS="$(docker compose exec -T server-family curl -sS -o /dev/null -w '%{http_code}' \
  -H 'X-ATP-TLCP-Target: attacker.invalid:7443' \
  -H 'X-ATP-TLCP-Server-Name: attacker.invalid' \
  http://tlcp-family:9080/.well-known/atp/v1/capabilities)"
test "$STATUS" = "403"
echo 'PASS: non-allow-listed TLCP destinations are rejected'
