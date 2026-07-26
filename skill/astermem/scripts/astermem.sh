#!/usr/bin/env bash
# AsterMem CLI - AI agent gateway to a self-hosted AsterMem memory service
#
# Background: AI Agents (Cursor / Claude Code) call this script via SKILL to read/write
# the user's AsterMem memory store; all tools go through POST /api/agent/call (Bearer Token).
# Design intent: One script covers all 17 tools. JSON assembly is delegated to python3
# stdlib for proper escaping (hand-crafting JSON in bash inevitably breaks on quotes/newlines).
# Key constraints: Credentials are read only from ~/.astermem/credentials or env vars,
# never hardcoded in the script; all errors must output a readable reason and exit non-zero
# so the Agent can detect failures.
#
# Usage:
#   astermem.sh quick "<text>" [top_k]            # semantic quick match (preferred for recall)
#   astermem.sh search "<query>" [limit]          # search memories
#   astermem.sh add "<title>" "<content>" [tags,csv] [priority]
#   astermem.sh get <mem_id|trunk_id>
#   astermem.sh update <mem_id> <field> "<value>" # field: title|content|status|priority
#   astermem.sh patch <mem_id> "<old_text>" "<new_text>"
#   astermem.sh delete <mem_id>                   # archives (soft delete)
#   astermem.sh list [status] [limit]
#   astermem.sh tags "<tag1,tag2>" [limit]        # list memories by tags
#   astermem.sh stats
#   astermem.sh profile [core|standard|full]      # one-call user profile (fields + AI claims)
#   astermem.sh config
#   astermem.sh provider <id> '<json_patch>'
#   astermem.sh test-provider <id>
#   astermem.sh rebuild
#   astermem.sh rebuild-status
#   astermem.sh api <METHOD> </api/path> ['<json>'] [confirm]
#   astermem.sh call <tool> '<json_arguments>'    # raw access to any agent tool
#
# Copyright (c) 2026 Asterove. AGPL-3.0 License

set -euo pipefail

CRED_FILE="${ASTERMEM_CREDENTIALS:-$HOME/.astermem/credentials}"

# Credential parsing: file contains KEY=VALUE lines; env vars take precedence over file
if [[ -f "$CRED_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$CRED_FILE"; set +a
fi

BASE_URL="${ASTERMEM_BASE_URL:-}"
TOKEN="${ASTERMEM_TOKEN:-}"

if [[ -z "$BASE_URL" || -z "$TOKEN" ]]; then
  cat >&2 <<EOF
[astermem] Missing credentials.
Create $HOME/.astermem/credentials with:
  ASTERMEM_BASE_URL=http://localhost:<port>
  ASTERMEM_TOKEN=ast_xxxxxxxx
Get a token from the AsterMem web UI: Admin -> API Tokens.
EOF
  exit 2
fi

BASE_URL="${BASE_URL%/}"

call_agent() {
  # $1 = tool name, $2 = JSON arguments (already valid JSON object)
  local tool="$1" args="$2" http_code body
  body=$(curl -sS --max-time 60 -w '\n%{http_code}' \
    -X POST "$BASE_URL/api/agent/call" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool\":\"$tool\",\"arguments\":$args}") || {
      echo "[astermem] Network error reaching $BASE_URL (is the AsterMem server running?)" >&2
      exit 3
    }
  http_code="${body##*$'\n'}"
  body="${body%$'\n'*}"
  if [[ "$http_code" != "200" ]]; then
    echo "[astermem] HTTP $http_code: $body" >&2
    exit 4
  fi
  # Output the result field as plain text, directly readable by the Agent
  printf '%s' "$body" | python3 -c '
import json,sys
data = json.load(sys.stdin)
result = data.get("result", data)
print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
'
}

call_rest() {
  local method="$1" path="$2" args="${3:-}" confirm="${4:-}" http_code body
  local -a curl_args=(
    -sS --max-time 120 -w $'\n%{http_code}'
    -X "$method" "$BASE_URL$path"
    -H "Authorization: Bearer $TOKEN"
  )
  if [[ -n "$args" ]]; then
    python3 -c 'import json,sys; json.loads(sys.argv[1])' "$args" ||
      { echo "[astermem] invalid JSON body" >&2; exit 5; }
    curl_args+=(-H "Content-Type: application/json" -d "$args")
  fi
  if [[ "$confirm" == "confirm" ]]; then
    curl_args+=(-H "X-AsterMem-Confirm: ${method^^} ${path%%\?*}")
  fi
  body=$(curl "${curl_args[@]}") || {
    echo "[astermem] Network error reaching $BASE_URL" >&2
    exit 3
  }
  http_code="${body##*$'\n'}"
  body="${body%$'\n'*}"
  if [[ "$http_code" != "200" ]]; then
    echo "[astermem] HTTP $http_code: $body" >&2
    exit 4
  fi
  printf '%s' "$body" | python3 -c '
import json,sys
raw=sys.stdin.read()
try: print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
except json.JSONDecodeError: print(raw)
'
}

# JSON argument assembly: python3 handles all escaping
j() { python3 -c 'import json,sys; print(json.dumps(dict(arg.split("\x00",1) for arg in sys.argv[1:]), ensure_ascii=False))' "$@" ; }

cmd="${1:-help}"; shift || true

case "$cmd" in
  quick)
    text="${1:?usage: astermem.sh quick \"<text>\" [top_k]}"; top_k="${2:-6}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1], "top_k": int(sys.argv[2])}, ensure_ascii=False))' "$text" "$top_k")
    call_agent quick_match "$args" ;;
  search)
    query="${1:?usage: astermem.sh search \"<query>\" [limit]}"; limit="${2:-10}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "limit": int(sys.argv[2])}, ensure_ascii=False))' "$query" "$limit")
    call_agent search_memories "$args" ;;
  add)
    title="${1:?usage: astermem.sh add \"<title>\" \"<content>\" [tags,csv] [priority]}"
    content="${2:?content required}"; tags="${3:-}"; priority="${4:-5}"
    args=$(python3 -c '
import json,sys
tags = [t.strip() for t in sys.argv[3].split(",") if t.strip()]
print(json.dumps({"title": sys.argv[1], "content": sys.argv[2], "tags": tags, "priority": int(sys.argv[4])}, ensure_ascii=False))
' "$title" "$content" "$tags" "$priority")
    call_agent add_memory "$args" ;;
  get)
    id="${1:?usage: astermem.sh get <mem_id|trunk_id>}"
    if [[ "$id" == trunk_* ]]; then
      args=$(python3 -c 'import json,sys; print(json.dumps({"trunk_id": sys.argv[1]}))' "$id")
      call_agent get_trunk "$args"
    else
      args=$(python3 -c 'import json,sys; print(json.dumps({"memory_id": sys.argv[1]}))' "$id")
      call_agent get_memory "$args"
    fi ;;
  update)
    id="${1:?usage: astermem.sh update <mem_id> <field> \"<value>\"}"
    field="${2:?field required: title|content|status|priority}"
    value="${3:?value required}"
    args=$(python3 -c '
import json,sys
field, value = sys.argv[2], sys.argv[3]
payload = {"memory_id": sys.argv[1], field: int(value) if field == "priority" else value}
print(json.dumps(payload, ensure_ascii=False))
' "$id" "$field" "$value")
    call_agent update_memory "$args" ;;
  patch)
    id="${1:?usage: astermem.sh patch <mem_id> \"<old_text>\" \"<new_text>\"}"
    old="${2:?old_text required}"; new="${3:?new_text required}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"memory_id": sys.argv[1], "old_text": sys.argv[2], "new_text": sys.argv[3]}, ensure_ascii=False))' "$id" "$old" "$new")
    call_agent patch_memory "$args" ;;
  delete)
    id="${1:?usage: astermem.sh delete <mem_id>}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"memory_id": sys.argv[1]}))' "$id")
    call_agent delete_memory "$args" ;;
  list)
    status="${1:-active}"; limit="${2:-20}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"status": sys.argv[1], "limit": int(sys.argv[2])}))' "$status" "$limit")
    call_agent list_memories "$args" ;;
  tags)
    tags="${1:?usage: astermem.sh tags \"tag1,tag2\" [limit]}"; limit="${2:-20}"
    args=$(python3 -c '
import json,sys
print(json.dumps({"tags": [t.strip() for t in sys.argv[1].split(",") if t.strip()], "limit": int(sys.argv[2])}, ensure_ascii=False))
' "$tags" "$limit")
    call_agent list_memories_by_tag "$args" ;;
  stats)
    call_agent get_memory_stats '{}' ;;
  profile)
    level="${1:-standard}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"level": sys.argv[1]}))' "$level")
    call_agent get_profile "$args" ;;
  config)
    call_agent get_system_config '{}' ;;
  provider)
    id="${1:?usage: astermem.sh provider <id> '<json_patch>'}"
    patch="${2:-\{\}}"
    args=$(python3 -c '
import json,sys
payload = json.loads(sys.argv[2])
if not isinstance(payload, dict):
    raise SystemExit("provider patch must be a JSON object")
payload["provider_id"] = sys.argv[1]
print(json.dumps(payload, ensure_ascii=False))
' "$id" "$patch") || { echo "[astermem] invalid provider JSON patch" >&2; exit 5; }
    call_agent configure_provider "$args" ;;
  test-provider)
    id="${1:?usage: astermem.sh test-provider <id>}"
    args=$(python3 -c 'import json,sys; print(json.dumps({"provider_id": sys.argv[1]}))' "$id")
    call_agent test_provider "$args" ;;
  rebuild)
    call_agent rebuild_vector_index '{"confirm":true}' ;;
  rebuild-status)
    call_agent get_vector_rebuild_status '{}' ;;
  api)
    method="${1:?usage: astermem.sh api <METHOD> </api/path> ['<json>'] [confirm]}"
    path="${2:?API path required}"
    call_rest "${method^^}" "$path" "${3:-}" "${4:-}" ;;
  call)
    tool="${1:?usage: astermem.sh call <tool> '<json_arguments>'}"
    args="${2:-\{\}}"
    # Validate caller-provided JSON to avoid sending bad arguments to the server
    python3 -c 'import json,sys; json.loads(sys.argv[1])' "$args" || { echo "[astermem] invalid JSON arguments" >&2; exit 5; }
    call_agent "$tool" "$args" ;;
  help|*)
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
