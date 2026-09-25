#!/usr/bin/env bash
set -euo pipefail
# Keep Docker/Compose paths and arguments intact when run from Git Bash.
export MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose exec -T server-family python /agents/check_sm2_transfer.py

declare -A before
for domain in family hotel payment; do
  before[$domain]=$(docker compose exec -T "server-$domain" atp keys show --algorithm sm2 --public)
done
docker compose restart server-family server-hotel server-payment
for domain in family hotel payment; do
  ready=false
  for attempt in {1..30}; do
    if docker compose exec -T "server-$domain" curl -fsS \
      "https://server-$domain.$domain.test:7443/.well-known/atp/v1/health" >/dev/null; then
      ready=true
      break
    fi
    sleep 1
  done
  "$ready" || { echo "$domain did not become healthy" >&2; exit 1; }
  after=$(docker compose exec -T "server-$domain" atp keys show --algorithm sm2 --public)
  test "${before[$domain]}" = "$after" || { echo "$domain key changed after restart" >&2; exit 1; }
done
docker compose exec -T server-family python /agents/check_sm2_transfer.py
echo 'PASS: all three SM2 public keys survive restart; cross-domain verification still works.'
for role in search rates bill; do
  test "$(docker inspect --format '{{.State.Running}}' "$(docker compose ps -a -q "agent-$role")")" = true
done
echo 'PASS: service Agents remain running across server restarts.'
