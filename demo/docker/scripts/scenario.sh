#!/bin/bash
# Start the real LLM Travel Agent through the Demo Controller and wait for it.
#
# The Controller owns packet capture and the payment outage/recovery fixture.
# qwen3.7-plus owns the business flow through ATP function tools; this script
# only provides a convenient terminal entry point to the same browser path.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO_URL="${ATP_DEMO_URL:-http://localhost:8080}"
DEADLINE="${ATP_AGENT_TIMEOUT:-180}"

echo '=== Starting LLM Travel Agent ==='
START_JSON="$(curl -fsS -X POST "$DEMO_URL/api/control/scenario-start")"
RUN_ID="$(printf '%s' "$START_JSON" | jq -r '.run_id')"
MODEL="$(printf '%s' "$START_JSON" | jq -r '.scenario.agent_model')"
PROVIDER="$(printf '%s' "$START_JSON" | jq -r '.scenario.agent_provider')"
test -n "$RUN_ID"
echo "  run_id=$RUN_ID"
echo "  model=$MODEL ($PROVIDER)"

started_at="$(date +%s)"
while true; do
  STATE="$(curl -fsS "$DEMO_URL/api/state/scenario")"
  STATUS="$(printf '%s' "$STATE" | jq -r '.scenario.status')"
  STEP="$(printf '%s' "$STATE" | jq -r '.scenario.step')"
  MESSAGE="$(printf '%s' "$STATE" | jq -r '.scenario.message')"
  printf '  %-8s %-22s %s\n' "$STATUS" "$STEP" "$MESSAGE"
  case "$STATUS" in
    passed)
      break
      ;;
    failed)
      printf '%s\n' "$STATE" | jq .
      exit 1
      ;;
  esac
  if [ $(( $(date +%s) - started_at )) -ge "$DEADLINE" ]; then
    echo "Agent timed out after ${DEADLINE}s" >&2
    exit 1
  fi
  sleep 2
done

TRACE_COUNT="$(curl -fsS "$DEMO_URL/api/runs/$RUN_ID/trace" | jq -r '.count')"
PACKET_COUNT="$(curl -fsS "$DEMO_URL/api/runs/$RUN_ID/packets" | jq -r '.count')"
echo "=== Agent complete: $RUN_ID ==="
echo "  Agent diagnostics: $TRACE_COUNT events"
echo "  Network evidence:  $PACKET_COUNT packets"
