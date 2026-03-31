#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${TALARIA_BASE_URL:-http://localhost:8400}"
MAX_QUEUE="${TALARIA_MAX_QUEUE_THRESHOLD:-25}"
WATCHER_MIN="${TALARIA_WATCHER_MIN:-1}"
WATCHER_MAX="${TALARIA_WATCHER_MAX:-2}"

say() { printf '[health-gate] %s\n' "$*"; }
fail() { printf '[health-gate] ERROR: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"

say "Checking API at $BASE_URL"
code=$(curl -sS -o /tmp/talaria_board.json -w '%{http_code}' "$BASE_URL/api/board" || true)
[[ "$code" == "200" ]] || fail "/api/board returned HTTP $code"

cards=$(jq '.cards | length' /tmp/talaria_board.json)
cols=$(jq '.columns | length' /tmp/talaria_board.json)
say "Board OK: columns=$cols cards=$cards"

queue_len=$(curl -sS "$BASE_URL/api/agent_queue" | jq 'length')
if (( queue_len > MAX_QUEUE )); then
  fail "Queue length $queue_len exceeds threshold $MAX_QUEUE"
fi
say "Queue OK: len=$queue_len threshold=$MAX_QUEUE"

watchers=$(ps ax -o command= | grep -E 'agent_watcher.py' | grep -v grep | wc -l | tr -d ' ')
if (( watchers < WATCHER_MIN || watchers > WATCHER_MAX )); then
  fail "Watcher process count out of range: got=$watchers expected=${WATCHER_MIN}-${WATCHER_MAX}"
fi
say "Watcher process count OK: $watchers"

say "Running fast tests"
python3 -m pytest tests/test_api.py tests/test_watcher_api_contract.py -q

say "Promotion health gate passed"
