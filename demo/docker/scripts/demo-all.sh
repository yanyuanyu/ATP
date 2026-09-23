#!/bin/bash
# Run all three ATP semantic demos in sequence.
#
# Each demo exercises one of the three core ATP semantics:
#   1. Request/Response      — ping → echo, cross-domain round trip
#   2. Event/Subscription     — publisher emits 5 events, subscriber consumes
#   3. Async + Offline Queue  — recipient server offline → queue + retry → deliver
#
# Prerequisite: containers must be up. Run `bash scripts/setup.sh` first if
# not. This script does NOT reset state between demos; each demo is
# independent (different agent IDs, different nonces) and can run on top
# of any prior demo's residue.
#
# Usage: bash scripts/demo-all.sh
set -euo pipefail
cd "$(dirname "$0")/.."

passed=0
failed=0

for demo in demo-request-response demo-event-subscription demo-offline-queue; do
  echo
  echo "################################################################"
  echo "# Running ${demo}"
  echo "################################################################"
  echo
  if bash "scripts/${demo}.sh"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    echo "[demo-all] ${demo} FAILED — continuing to next demo"
  fi
done

echo
echo "################################################################"
echo "# Summary: ${passed} passed, ${failed} failed"
echo "################################################################"
exit $failed
