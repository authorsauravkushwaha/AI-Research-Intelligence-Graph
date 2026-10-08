#!/usr/bin/env bash
# NEXUS end-to-end smoke sweep against a running server.
#
#   python run.py --port 8000 &        # in one shell
#   ./scripts/smoke.sh                 # in another
#
# Exits non-zero if any endpoint answers with a non-2xx status, so it can gate CI
# or a release. Every check is a real request — no mocks.
set -uo pipefail

BASE="${NEXUS_BASE:-http://127.0.0.1:8000}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

hit() { # method path [json-body]
  local method="$1" path="$2" body="${3:-}" code
  if [ -n "$body" ]; then
    code=$(curl -sS -o "$TMP/last" -w '%{http_code}' -X "$method" "$BASE$path" \
      -H 'content-type: application/json' -d "$body" --max-time 60)
  else
    code=$(curl -sS -o "$TMP/last" -w '%{http_code}' -X "$method" "$BASE$path" --max-time 60)
  fi
  local size; size=$(wc -c <"$TMP/last" | tr -d ' ')
  if [ "${code:0:1}" = "2" ]; then
    PASS=$((PASS + 1)); mark="ok  "
  else
    FAIL=$((FAIL + 1)); mark="FAIL"
  fi
  printf '%s %-6s %-56s %s %8sB  %s\n' "$mark" "$method" "$path" "$code" "$size" \
    "$(head -c 80 "$TMP/last" | tr -d '\n')"
}

expect404() { # an unknown id must fail closed with 404, never a 500 or an empty 200
  local method="$1" path="$2" code
  code=$(curl -sS -o "$TMP/last" -w '%{http_code}' -X "$method" "$BASE$path" --max-time 60)
  if [ "$code" = "404" ]; then
    PASS=$((PASS + 1)); printf 'ok   %-6s %-56s %s  %s\n' "$method" "$path" "$code" "$(head -c 60 "$TMP/last" | tr -d '\n')"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL %-6s %-56s %s  (expected 404)\n' "$method" "$path" "$code"
  fi
}

echo "== NEXUS smoke sweep against $BASE =="

hit GET  /api/health
hit GET  /api/tools
hit GET  /api/algorithms
hit GET  /api/opportunity-score
hit GET  /api/safety
hit GET  /api/mcp
hit GET  /api/dashboard
hit GET  /api/timeline
hit GET  /api/explorer
hit GET  '/api/search?q=memory'
hit GET  '/api/papers?limit=3'
hit GET  '/api/papers/paper:2210.03629'
expect404 GET '/api/papers/nope:1'
hit GET  '/api/nodes?label=Method&limit=3'
hit GET  '/api/nodes/topic:agent-memory'
hit GET  '/api/communities'
hit GET  '/api/communities/0'
hit GET  '/api/conflicts?limit=2'
hit GET  '/api/predictions?limit=2'
hit GET  '/api/centrality?metric=betweenness&label=Paper&limit=3'
hit GET  '/api/graph/path?source=paper:2210.03629&target=topic:agent-memory'
hit GET  '/api/graph'
hit POST /api/graph '{"query":"agent memory","depth":1,"max_nodes":60}'
hit POST /api/graph/expand '{"node_ids":["paper:2210.03629"],"limit":10}'
hit GET  '/api/graph/neighbours/paper:2210.03629'
hit POST /api/gaps '{"topic":"AI Agents","top_k":2}'
hit POST /api/gaps/0/evidence '{"topic":"AI Agents","top_k":2}'
hit POST /api/report '{"topic":"AI Agents","top_k":2}'
hit POST /api/report/markdown '{"topic":"AI Agents","top_k":2}'
hit POST /api/export '{"format":"graphml","limit":20}'
hit POST /api/export '{"format":"csv","limit":20}'
hit POST /api/export '{"format":"cypher","limit":20}'
hit POST /api/export '{"format":"json","limit":20}'
hit GET  '/api/export/cypher?limit=20'
hit POST /api/agent '{"question":"What are the most important papers?","top_k":8}'
hit POST /api/agent '{"question":"Which claims contradict each other?","top_k":8}'

# The frontend bundle must be served without any external CDN reference.
hit GET  /
hit GET  /assets/app.js
hit GET  /assets/style.css
hit GET  /vendor/three.module.js

# SSE agent stream: first event must arrive.
code=$(curl -sS -N -o "$TMP/stream" -w '%{http_code}' -X POST "$BASE/api/agent/stream" \
  -H 'content-type: application/json' \
  -d '{"question":"Where are the research gaps in agent memory?","top_k":8}' --max-time 90)
if [ "${code:0:1}" = "2" ] && grep -q '^event:' "$TMP/stream"; then
  PASS=$((PASS + 1)); printf 'ok   %-6s %-56s %s  %s\n' POST /api/agent/stream "$code" "$(head -c 60 "$TMP/stream")"
else
  FAIL=$((FAIL + 1)); printf 'FAIL %-6s %-56s %s  %s\n' POST /api/agent/stream "$code" "$(head -c 80 "$TMP/stream")"
fi

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
