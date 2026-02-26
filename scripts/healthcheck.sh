#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}."
  exit 1
fi

source "${ENV_FILE}"

run_docker() {
  local strict_mode="${STRICT_DOCKER_DIRECT:-0}"
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    if [[ "${strict_mode}" == "1" ]]; then
      echo "Direct Docker access required (STRICT_DOCKER_DIRECT=1), but docker info failed."
      echo "Fix Docker socket permissions for the current user and retry."
      return 1
    fi
    local escaped=()
    local arg
    for arg in "$@"; do
      escaped+=("$(printf '%q' "$arg")")
    done
    sg docker -c "docker ${escaped[*]}"
  fi
}

check_http() {
  local url="$1"
  local name="$2"
  local code
  code=$(curl -sS -o /dev/null -m 8 -w "%{http_code}" "$url" || true)
  if [[ "$code" != "200" ]]; then
    echo "FAIL: ${name} -> HTTP ${code} (${url})"
    return 1
  fi
  echo "PASS: ${name} -> HTTP 200"
}

echo "== Container status =="
run_docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | sed -n '1,20p'

echo
echo "== Endpoint checks =="
check_http "http://127.0.0.1:${REASONING_PORT}/v1/models" "reasoning backend"
check_http "http://127.0.0.1:${MULTIMODAL_PORT}/v1/models" "multimodal backend"
check_http "http://127.0.0.1:${PUBLIC_PORT}/v1/models" "unified gateway"

echo
echo "== Smoke inference checks =="

reasoning_resp=$(curl -sS "http://127.0.0.1:${PUBLIC_PORT}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_REASONING_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with READY_REASONING\"}],\"max_tokens\":16}" \
  || true)

if [[ "$reasoning_resp" != *"choices"* ]]; then
  echo "FAIL: reasoning smoke test returned unexpected payload"
  echo "$reasoning_resp" | head -c 400
  echo
  exit 1
fi
echo "PASS: reasoning smoke test"

multimodal_resp=$(curl -sS "http://127.0.0.1:${PUBLIC_PORT}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_MULTIMODAL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Reply with READY_MM\"}]}],\"max_tokens\":16}" \
  || true)

if [[ "$multimodal_resp" != *"choices"* ]]; then
  echo "FAIL: multimodal smoke test returned unexpected payload"
  echo "$multimodal_resp" | head -c 400
  echo
  exit 1
fi
echo "PASS: multimodal smoke test"

echo
echo "All health checks passed."
