#!/bin/bash
# Reset hackathon env to a clean state.
#
#   bash scripts/reset.sh              # hard: down -v + setup (~30s)
#   bash scripts/reset.sh --soft       # soft: wipe message DBs only (~3s)
#
# Hard reset tears down containers AND volumes — keys, agent registrations,
# messages, all gone. Use when you want a true clean state (e.g., before
# running the full scenario from scratch, or when DNS ATK records may be
# stale).
#
# Soft reset keeps containers running, preserves server keys + agent
# registrations, and only wipes the messages + nonces tables. Useful for
# iterating on scenario scripts: re-running the scenario from a known
# queue-empty state without paying the ~30s setup cost each time.
#
# Soft reset assumes setup.sh has been run at least once. If keys / DNS
# ATK records are out of sync (e.g., after a server restart that
# regenerated keys), use hard reset.
set -e
cd "$(dirname "$0")/.."

MODE="${1:-hard}"

if [ "$MODE" = "--soft" ] || [ "$MODE" = "soft" ]; then
  echo '=== Soft reset: wiping message DBs (preserving keys + agent registrations) ==='
  # Note: we DELETE rows but do NOT touch sqlite_sequence — long-running
  # agents (search, rates, bill) hold an in-memory recv cursor (_last_recv_id)
  # from prior runs. If we reset the sequence, new messages start at ID 1
  # and fall below the cursor, so the agents never see them. Leaving the
  # sequence alone means new messages get IDs strictly greater than the
  # previous max, so the existing cursors stay valid.
  for srv in server-family server-hotel server-payment; do
    echo -n "  $srv: "
    docker compose exec -T "$srv" python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.atp/data/messages.db')
conn.execute('DELETE FROM messages')
conn.commit()
n = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
seq = conn.execute(\"SELECT seq FROM sqlite_sequence WHERE name='messages'\").fetchone()
print(f'messages cleared ({n} rows), last_id={seq[0] if seq else 0}')
" 2>&1 | tail -1 || echo '(db unavailable — server may be down)'
  done
  for srv in server-family server-hotel server-payment; do
    echo -n "  $srv: "
    docker compose exec -T "$srv" python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.atp/data/nonces.db')
conn.execute('DELETE FROM nonces')
conn.commit()
n = conn.execute('SELECT COUNT(*) FROM nonces').fetchone()[0]
print(f'nonces table cleared ({n} rows)')
" 2>&1 | tail -1 || echo '(nonces db unavailable)'
  done
  echo '=== Soft reset complete ==='
  exit 0
fi

echo '=== Tearing down containers and volumes ==='
docker compose down -v

bash scripts/setup.sh
