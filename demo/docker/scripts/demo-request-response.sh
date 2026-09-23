#!/bin/bash
# Demo 1 of 3 — Request/Response semantic.
#
# travel@family.test sends "ping from travel" to search@hotel.test.
# The search agent long-polls hotel's mailbox, receives the message,
# and replies with "echo: ping from travel". travel long-polls and
# receives the reply. Exits 0 on success, 1 on no reply within timeout.
#
# Usage: bash scripts/demo-request-response.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo '=== Demo 1/3: Request/Response ==='
echo '  Sender:    travel@family.test'
echo '  Recipient: search@hotel.test (long-running)'
echo '  Flow:      ping → echo (cross-domain, family→hotel→family)'
echo

docker compose run --rm smoke
rc=$?
echo
if [ $rc -eq 0 ]; then
  echo '=== Demo 1/3: PASS ==='
else
  echo "=== Demo 1/3: FAIL (exit $rc) ==="
fi
exit $rc
