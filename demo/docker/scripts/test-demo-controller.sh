#!/bin/bash
# End-to-end regression for the conversational four-Pi-Agent demo.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO_URL="${ATP_DEMO_URL:-http://localhost:8080}"
DEADLINE="${ATP_DEMO_TEST_TIMEOUT:-240}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo '=== Demo Controller smoke checks ==='
INDEX_HTML="$(curl -fsS "$DEMO_URL/")"
printf '%s' "$INDEX_HTML" | grep -q '<div id="root"></div>'
ASSET_PATH="$(printf '%s' "$INDEX_HTML" | grep -oE '/static/assets/[^\" ]+\.js' | head -1)"
test -n "$ASSET_PATH"
curl -fsS "$DEMO_URL$ASSET_PATH" > "$TMP_DIR/app.js"
grep -q 'Live topology' "$TMP_DIR/app.js"
grep -q 'Talk to Travel' "$TMP_DIR/app.js"
curl -fsS "$DEMO_URL/api/state/topology" | jq -e '
  .containers["server-family"] == "running" and
  .containers["server-hotel"] == "running" and
  .containers["server-payment"] == "running" and
  .containers["agent-search"] == "running" and
  .containers["agent-rates"] == "running" and
  .containers["agent-bill"] == "running"
' >/dev/null

echo '=== Start a clean Pi conversation ==='
curl -fsS -X POST "$DEMO_URL/api/control/soft-reset" >/dev/null
curl -fsS -X POST "$DEMO_URL/api/chat/reset" | jq -e '
  .chat.status == "idle" and
  .chat.runtime.name == "Pi" and
  .chat.runtime.status == "ready"
' >/dev/null

LOG_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
START_JSON="$(curl -fsS -X POST "$DEMO_URL/api/chat/messages" \
  -H 'Content-Type: application/json' \
  --data '{"message":"请搜索巴黎住两晚的酒店，订阅 ParisGarden 的三次价格，并按最低价完成模拟预订和付款；最后告诉我酒店、最低价和总价。"}')"
RUN_ID="$(printf '%s' "$START_JSON" | jq -r '.run_id')"
test -n "$RUN_ID"
echo "  run_id=$RUN_ID"

started_at="$(date +%s)"
while true; do
  curl -fsS "$DEMO_URL/api/state/chat" > "$TMP_DIR/chat.json"
  chat_status="$(jq -r '.chat.status' "$TMP_DIR/chat.json")"
  activity_count="$(jq -r '.chat.activities | length' "$TMP_DIR/chat.json")"
  echo "  $chat_status / $activity_count typed Travel tools"
  if [ "$chat_status" = 'idle' ]; then
    break
  fi
  if [ "$chat_status" = 'failed' ]; then
    jq . "$TMP_DIR/chat.json"
    exit 1
  fi
  if [ $(( $(date +%s) - started_at )) -ge "$DEADLINE" ]; then
    echo "Pi conversation timed out after ${DEADLINE}s" >&2
    exit 1
  fi
  sleep 2
done

echo '=== Validate conversation and typed tool activity ==='
jq -e --arg run_id "$RUN_ID" '
  . as $chat |
  .chat.run_id == $run_id and
  .chat.runtime.name == "Pi" and
  .chat.runtime.version == "0.80.6" and
  .chat.messages[-1].role == "assistant" and
  .chat.messages[-1].status == "completed" and
  (.chat.messages[-1].content | test("ParisGarden"; "i")) and
  (.chat.messages[-1].content | test("170")) and
  (.chat.messages[-1].content | test("340")) and
  ([.chat.activities[] | select(.tool == "atp_send" and .status == "completed")] | length >= 3) and
  ([.chat.activities[] | select(.tool == "atp_receive" and .status == "completed")] | length >= 3) and
  ([.chat.activities[] | select(.status != "completed")] | length == 0) and
  ([.chat.agent_audit[] | select(.run_id == $run_id and .role == "travel" and .kind == "user_input")] | length == 1) and
  ([.chat.agent_audit[] | select(.run_id == $run_id and .role == "travel" and .kind == "tool_output")] | length >= 3) and
  ([.chat.agent_audit[] | select(.run_id == $run_id and .role == "travel" and .kind == "response")] | length == 1) and
  (all(["search", "rates", "bill"][]; . as $role |
    $chat |
    ([.chat.agent_audit[] | select(.run_id == $run_id and .role == $role and .kind == "atp_input")] | length >= 1) and
    ([.chat.agent_audit[] | select(.run_id == $run_id and .role == $role and .kind == "tool_input")] | length >= 1) and
    ([.chat.agent_audit[] | select(.run_id == $run_id and .role == $role and .kind == "tool_output")] | length >= 1) and
    ([.chat.agent_audit[] | select(.run_id == $run_id and .role == $role and .kind == "response")] | length >= 1)
  ))
' "$TMP_DIR/chat.json" >/dev/null

docker compose logs --no-color --since "$LOG_SINCE" agent-search agent-rates agent-bill > "$TMP_DIR/service-agents.log"
for tool in inventory_lookup send_search_results get_rate_schedule publish_rate_events authorize_simulated_payment send_payment_result; do
  grep -q "\"tool\":\"$tool\"" "$TMP_DIR/service-agents.log"
done

echo '=== Validate support trace ==='
curl -fsS "$DEMO_URL/api/runs/$RUN_ID/trace" > "$TMP_DIR/trace.json"
jq -e '
  (.count >= 2) and
  (.events[0].event == "user_message") and
  (.events[-1].event == "assistant_message") and
  (([.events[].seq] | unique | length) == .count) and
  ([.events[].seq] == ([.events[].seq] | sort))
' "$TMP_DIR/trace.json" >/dev/null

echo '=== Validate packet-first evidence ==='
while true; do
  curl -fsS "$DEMO_URL/api/runs/$RUN_ID/packets" > "$TMP_DIR/packets.json"
  if jq -e '
    .summary.agent_edge_observed and
    .summary.svcb_observed and
    .summary.tcp_observed and
    .summary.tls_observed and
    .summary.ats_lookup_observed and
    .summary.atk_lookup_observed
  ' "$TMP_DIR/packets.json" >/dev/null; then
    break
  fi
  if [ $(( $(date +%s) - started_at )) -ge "$DEADLINE" ]; then
    echo "packet evidence timed out after ${DEADLINE}s" >&2
    jq '.summary' "$TMP_DIR/packets.json" >&2
    exit 1
  fi
  sleep 2
done

jq -e '
  (.primary_evidence == true) and
  (.source == "AF_PACKET") and
  (.count >= 30) and
  ([.signals[] | select(.evidence == "tls_handshake" and .scope == "cross_domain")] | length >= 1) and
  ([.signals[] | select(.qname == "_atp.hotel.test")] | length >= 1) and
  ([.signals[] | select(has("body") or has("payload"))] | length == 0)
' "$TMP_DIR/packets.json" >/dev/null

echo "PASS: $RUN_ID completed through four Pi sessions with $(jq -r '.chat.activities | length' "$TMP_DIR/chat.json") Travel tool calls and $(jq -r '.count' "$TMP_DIR/packets.json") packet signals"
