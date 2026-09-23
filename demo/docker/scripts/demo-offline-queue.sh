#!/bin/bash
# Demo 3 of 3 — Async Message + Offline Queue semantic.
#
# Demonstrates the cross-domain retry queue: when the recipient's server
# is unreachable, the sender's server holds the message in QUEUED state
# and retries with exponential backoff (60s, 300s, ...).
#
# Flow:
#   1. Stop server-payment (recipient's server is offline).
#   2. travel@family.test sends a message to bill@payment.test.
#      family's delivery manager tries to transfer → fails → schedules retry.
#   3. Show family's queue: 1 message QUEUED, retry_count=1, next_retry_at set.
#   4. Start server-payment + agent-bill.
#   5. Restart server-family — forces delivery loop to retry immediately
#      (otherwise we'd wait up to 60s for the next scheduled retry).
#   6. Wait for delivery + bill's ack reply.
#   7. Show family's queue: message DELIVERED. Confirm bill acked.
#
# Usage: bash scripts/demo-offline-queue.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo '=== Demo 3/3: Async + Offline Queue ==='
echo '  Sender:    travel@family.test'
echo '  Recipient: bill@payment.test (long-running)'
echo '  Flow:      payment offline → queue+retry → payment up → deliver'
echo

DB_PATH='/root/.atp/data/messages.db'

show_family_queue() {
  echo '--- family server queue state ---'
  docker compose exec -T server-family python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
rows = list(conn.execute('SELECT nonce, status, retry_count, next_retry_at, error FROM messages ORDER BY id'))
if not rows:
    print('  (queue empty)')
for r in rows:
    print(f'  nonce={r[0]} status={r[1]} retry={r[2]} next_retry_at={r[3]} error={r[4] or \"\"}')
" 2>&1 || echo '  (db unavailable)'
}

echo
echo '=== Step 1: Stop payment server (simulate outage) ==='
docker compose stop server-payment agent-bill 2>&1 | tail -4 || true
sleep 2

echo
echo '=== Step 2: Send message from travel@family to bill@payment ==='
docker compose run --rm offline-sender

echo
echo '=== Step 3: Wait 8s for first transfer attempt + retry scheduling ==='
sleep 8
show_family_queue

echo
echo '=== Step 4: Restart payment server + bill agent ==='
docker compose start server-payment 2>&1 | tail -2
# Give payment a moment to come up before we kick family's delivery loop.
sleep 3
docker compose start agent-bill 2>&1 | tail -2 || true
sleep 2

echo
echo '=== Step 5: Force immediate retry (simulate retry-timer expiry) ==='
# The delivery manager's next scheduled retry is 60s out (exponential backoff:
# 60s, 300s, 1800s, ...). For a hackathon demo we don't want to wait 60s, so
# we reset next_retry_at to 0 on any failed message — the delivery loop will
# pick it up on its next 5s tick and re-attempt transfer now that payment is up.
docker compose exec -T server-family python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
cur = conn.execute(\"UPDATE messages SET next_retry_at=0 WHERE status='failed' AND next_retry_at IS NOT NULL\")
conn.commit()
print(f'  reset next_retry_at on {cur.rowcount} failed message(s)')
" 2>&1 || echo '  (DB update failed)'

echo
echo '=== Step 6: Wait 15s for delivery + bill ack ==='
sleep 15
show_family_queue
echo
echo '--- family server recent delivery log ---'
docker compose logs --tail 20 server-family 2>&1 | grep -E 'Delivered|Retry|Transfer' || true
echo
echo '--- bill agent recent log ---'
docker compose logs --tail 20 agent-bill 2>&1 | grep -E 'recv|send' || true

echo
echo '=== Step 7: Verify travel got the ack reply ==='
ack_count=$(docker compose exec -T server-family python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
n = conn.execute(\"SELECT COUNT(*) FROM messages WHERE status='delivered' AND from_id LIKE '%bill%' AND message_json LIKE '%bill ack%'\").fetchone()[0]
print(n)
" 2>/dev/null || echo 0)

if [ "$ack_count" -ge 1 ]; then
  echo "  travel received $ack_count ack reply/replies from bill."
  echo
  echo '=== Demo 3/3: PASS ==='
  exit 0
else
  echo "  travel received $ack_count ack replies (expected ≥1)."
  echo
  echo '=== Demo 3/3: FAIL (no ack received) ==='
  exit 1
fi
