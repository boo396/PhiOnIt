#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo '{"ok":false,"error":"Missing .env"}'
  exit 1
fi

source "${ENV_FILE}"

TAIL_LINES="${TAIL_LINES:-40}"
MODE="${1:-full}"

if [[ "${MODE}" == "?compact" ]]; then
  MODE="compact"
fi

if [[ "${MODE}" != "full" && "${MODE}" != "compact" ]]; then
  echo '{"ok":false,"error":"Usage: health_report_json.sh [full|compact|?compact]"}'
  exit 1
fi

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

run_docker() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    local escaped=()
    local arg
    for arg in "$@"; do
      escaped+=("$(printf '%q' "$arg")")
    done
    sg docker -c "docker ${escaped[*]}"
  fi
}

http_check() {
  local name="$1"
  local url="$2"
  local tmp_file
  tmp_file=$(mktemp)
  local code
  code=$(curl -sS -m 12 -o "$tmp_file" -w "%{http_code}" "$url" || echo "000")
  local ok="false"
  if [[ "$code" == "200" ]]; then
    ok="true"
  fi
  local body_snippet=""
  if [[ "${MODE}" == "full" ]]; then
    body_snippet=$(head -c 800 "$tmp_file" 2>/dev/null || true)
  fi
  rm -f "$tmp_file"

  printf '{"name":"%s","url":"%s","code":%s,"ok":%s,"body_snippet":%s}' \
    "$name" "$url" "$code" "$ok" "$(printf '%s' "$body_snippet" | json_escape)"
}

smoke_reasoning() {
  local tmp_file
  tmp_file=$(mktemp)
  local code
  code=$(curl -sS -m 30 -o "$tmp_file" -w "%{http_code}" \
    "http://127.0.0.1:${PUBLIC_PORT}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL_REASONING_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with READY_REASONING\"}],\"max_tokens\":16}" || echo "000")
  local body
  body=$(cat "$tmp_file" 2>/dev/null || true)
  rm -f "$tmp_file"
  local ok="false"
  if [[ "$code" == "200" && "$body" == *"choices"* ]]; then
    ok="true"
  fi
  local snippet=""
  if [[ "${MODE}" == "full" ]]; then
    snippet=$(printf '%s' "$body" | head -c 1200)
  fi
  printf '{"name":"reasoning_smoke","code":%s,"ok":%s,"body_snippet":%s}' \
    "$code" "$ok" "$(printf '%s' "$snippet" | json_escape)"
}

smoke_multimodal() {
  local tmp_file
  tmp_file=$(mktemp)
  local code
  code=$(curl -sS -m 30 -o "$tmp_file" -w "%{http_code}" \
    "http://127.0.0.1:${PUBLIC_PORT}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL_MULTIMODAL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Reply with READY_MM\"}]}],\"max_tokens\":16}" || echo "000")
  local body
  body=$(cat "$tmp_file" 2>/dev/null || true)
  rm -f "$tmp_file"
  local ok="false"
  if [[ "$code" == "200" && "$body" == *"choices"* ]]; then
    ok="true"
  fi
  local snippet=""
  if [[ "${MODE}" == "full" ]]; then
    snippet=$(printf '%s' "$body" | head -c 1200)
  fi
  printf '{"name":"multimodal_smoke","code":%s,"ok":%s,"body_snippet":%s}' \
    "$code" "$ok" "$(printf '%s' "$snippet" | json_escape)"
}

container_row_json() {
  local name="$1"
  local inspect
  inspect=$(run_docker inspect "$name" 2>/dev/null || true)
  if [[ -z "$inspect" ]]; then
    printf '{"name":"%s","present":false,"running":false}' "$name"
    return
  fi

  local running status restart_count started_at exit_code oom
  running=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(str(x["State"].get("Running", False)).lower())')
  status=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(x["State"].get("Status", ""))')
  restart_count=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(x.get("RestartCount", 0))')
  started_at=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(x["State"].get("StartedAt", ""))')
  exit_code=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(x["State"].get("ExitCode", 0))')
  oom=$(printf '%s' "$inspect" | python3 -c 'import json,sys; x=json.load(sys.stdin)[0]; print(str(x["State"].get("OOMKilled", False)).lower())')

  printf '{"name":"%s","present":true,"running":%s,"status":"%s","restart_count":%s,"started_at":"%s","exit_code":%s,"oom_killed":%s}' \
    "$name" "$running" "$status" "$restart_count" "$started_at" "$exit_code" "$oom"
}

tail_log_json() {
  local name="$1"
  local log_text
  log_text=$(run_docker logs --tail "$TAIL_LINES" "$name" 2>&1 || true)
  printf '{"container":"%s","tail_lines":%s,"log":%s}' \
    "$name" "$TAIL_LINES" "$(printf '%s' "$log_text" | json_escape)"
}

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

reasoning_container_json=$(container_row_json "$REASONING_CONTAINER")
multimodal_container_json=$(container_row_json "$MULTIMODAL_CONTAINER")
gateway_container_json=$(container_row_json "$GATEWAY_CONTAINER")

reasoning_endpoint_json=$(http_check "reasoning_models" "http://127.0.0.1:${REASONING_PORT}/v1/models")
multimodal_endpoint_json=$(http_check "multimodal_models" "http://127.0.0.1:${MULTIMODAL_PORT}/v1/models")
gateway_endpoint_json=$(http_check "gateway_models" "http://127.0.0.1:${PUBLIC_PORT}/v1/models")

reasoning_smoke_json=$(smoke_reasoning)
multimodal_smoke_json=$(smoke_multimodal)

log_tails_json="[]"
if [[ "${MODE}" == "full" ]]; then
  reasoning_log_json=$(tail_log_json "$REASONING_CONTAINER")
  multimodal_log_json=$(tail_log_json "$MULTIMODAL_CONTAINER")
  gateway_log_json=$(tail_log_json "$GATEWAY_CONTAINER")
  log_tails_json="[
    ${reasoning_log_json},
    ${multimodal_log_json},
    ${gateway_log_json}
  ]"
fi

all_ok=true
for marker in \
  "$(printf '%s' "$reasoning_container_json" | grep -o '"running":true' || true)" \
  "$(printf '%s' "$multimodal_container_json" | grep -o '"running":true' || true)" \
  "$(printf '%s' "$gateway_container_json" | grep -o '"running":true' || true)" \
  "$(printf '%s' "$reasoning_endpoint_json" | grep -o '"ok":true' || true)" \
  "$(printf '%s' "$multimodal_endpoint_json" | grep -o '"ok":true' || true)" \
  "$(printf '%s' "$gateway_endpoint_json" | grep -o '"ok":true' || true)" \
  "$(printf '%s' "$reasoning_smoke_json" | grep -o '"ok":true' || true)" \
  "$(printf '%s' "$multimodal_smoke_json" | grep -o '"ok":true' || true)"; do
  if [[ -z "$marker" ]]; then
    all_ok=false
    break
  fi
done

cat <<EOF
{
  "timestamp_utc": "${ts}",
  "mode": "${MODE}",
  "ok": ${all_ok},
  "stack": {
    "public_port": ${PUBLIC_PORT},
    "reasoning_port": ${REASONING_PORT},
    "multimodal_port": ${MULTIMODAL_PORT},
    "reasoning_model": "${MODEL_REASONING_ID}",
    "multimodal_model": "${MODEL_MULTIMODAL_ID}"
  },
  "containers": [
    ${reasoning_container_json},
    ${multimodal_container_json},
    ${gateway_container_json}
  ],
  "endpoint_checks": [
    ${reasoning_endpoint_json},
    ${multimodal_endpoint_json},
    ${gateway_endpoint_json}
  ],
  "smoke_checks": [
    ${reasoning_smoke_json},
    ${multimodal_smoke_json}
  ],
  "log_tails": ${log_tails_json}
}
EOF
