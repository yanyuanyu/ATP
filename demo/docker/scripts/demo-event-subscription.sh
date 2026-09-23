#!/bin/bash
# Demo 2 of 3 — Event/Subscription semantic.
#
# news@family.test publishes 5 typed events (subject="event") to
# feed@hotel.test at 2-second intervals. feed long-polls hotel's mailbox
# and prints each event as it arrives. Exits 0 when feed has consumed
# all 5 events, 1 on timeout.
#
# Usage: bash scripts/demo-event-subscription.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo '=== Demo 2/3: Event/Subscription ==='
echo '  Publisher:  news@family.test   (one-shot, 5 events @ 2s)'
echo '  Subscriber: feed@hotel.test    (long-poll, exits after 5)'
echo '  Flow:      family → hotel (cross-domain, 5 messages)'
echo

# Clean up any leftover containers from a previous run.
docker rm -f hack-event-sub hack-event-pub 2>/dev/null || true

# Start the subscriber detached. It long-polls and exits after 5 events.
# Note: no --rm — we need the container to persist after exit so we can
# collect its logs and exit code below, then remove it manually.
docker rm -f hack-event-sub 2>/dev/null || true
docker compose run -d --name hack-event-sub event-sub >/dev/null
echo '[demo] subscriber started, waiting 3s for long-poll to begin...'
sleep 3

# Run the publisher in the foreground. Sends 5 events then exits.
echo '[demo] running publisher...'
if ! docker compose run --rm event-pub; then
  echo '=== Demo 2/3: FAIL (publisher exited non-zero) ==='
  docker rm -f hack-event-sub 2>/dev/null || true
  exit 1
fi

echo
echo '[demo] publisher done; waiting for subscriber to consume all events...'
# Wait up to 60s for the subscriber container to exit on its own.
for _ in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Status}}' hack-event-sub 2>/dev/null || echo "missing")
  if [ "$status" = "exited" ] || [ "$status" = "missing" ]; then
    break
  fi
  sleep 1
done

echo
echo '[demo] subscriber log:'
docker logs hack-event-sub 2>&1 | tail -20 || true

rc=$(docker inspect -f '{{.State.ExitCode}}' hack-event-sub 2>/dev/null || echo 1)
docker rm -f hack-event-sub 2>/dev/null || true

echo
if [ "$rc" = "0" ]; then
  echo '=== Demo 2/3: PASS ==='
else
  echo "=== Demo 2/3: FAIL (subscriber exit $rc) ==="
fi
exit "$rc"
