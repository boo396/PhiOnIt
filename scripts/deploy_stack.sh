#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Run scripts/setup_fresh_image.sh first."
  exit 1
fi

source "${ENV_FILE}"

ACTION="${1:-start}"

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

stop_stack() {
  run_docker rm -f "${GATEWAY_CONTAINER}" "${MULTIMODAL_CONTAINER}" "${REASONING_CONTAINER}" >/dev/null 2>&1 || true
}

wait_for_ready() {
  local name="$1"
  local port="$2"
  local retries=360
  local i=1

  until curl -fsS "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; do
    if (( i > retries )); then
      echo "Timed out waiting for ${name} on port ${port}."
      run_docker logs "${name}" | tail -n 120 || true
      return 1
    fi
    sleep 5
    ((i++))
  done
}

wait_for_gateway_ready() {
  local retries=60
  local i=1

  until curl -fsS "http://127.0.0.1:${PUBLIC_PORT}/v1/models" >/dev/null 2>&1; do
    if (( i > retries )); then
      echo "Timed out waiting for gateway on port ${PUBLIC_PORT}."
      run_docker logs "${GATEWAY_CONTAINER}" | tail -n 120 || true
      return 1
    fi
    sleep 1
    ((i++))
  done
}

if [[ "${ACTION}" == "stop" ]]; then
  stop_stack
  echo "Stack stopped."
  exit 0
fi

if [[ "${ACTION}" != "start" && "${ACTION}" != "restart" ]]; then
  echo "Usage: $0 [start|restart|stop]"
  exit 1
fi

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is empty in ${ENV_FILE}."
  exit 1
fi

mkdir -p "${ROOT_DIR}/logs" "${ROOT_DIR}/data/hf-cache"

if [[ "${ACTION}" == "restart" ]]; then
  stop_stack
fi

echo "Starting reasoning backend on ${REASONING_PORT}..."
run_docker rm -f "${REASONING_CONTAINER}" >/dev/null 2>&1 || true
run_docker run -d \
  --name "${REASONING_CONTAINER}" \
  --restart unless-stopped \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  -e HF_TOKEN="${HF_TOKEN}" \
  -e HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
  -e HF_HOME=/hf-cache \
  -v "${ROOT_DIR}/configs:/configs:ro" \
  -v "${ROOT_DIR}/data/hf-cache:/hf-cache" \
  -p "127.0.0.1:${REASONING_PORT}:${REASONING_PORT}" \
  "${TRTLLM_IMAGE}" \
  bash -lc "trtllm-serve ${MODEL_REASONING_ID} --backend pytorch --host 0.0.0.0 --port ${REASONING_PORT} --config /configs/reasoning.yaml"

echo "Starting multimodal backend on ${MULTIMODAL_PORT}..."
run_docker rm -f "${MULTIMODAL_CONTAINER}" >/dev/null 2>&1 || true
run_docker run -d \
  --name "${MULTIMODAL_CONTAINER}" \
  --restart unless-stopped \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  -e HF_TOKEN="${HF_TOKEN}" \
  -e HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
  -e HF_HOME=/hf-cache \
  -v "${ROOT_DIR}/configs:/configs:ro" \
  -v "${ROOT_DIR}/data/hf-cache:/hf-cache" \
  -p "127.0.0.1:${MULTIMODAL_PORT}:${MULTIMODAL_PORT}" \
  "${TRTLLM_IMAGE}" \
  bash -lc "trtllm-serve ${MODEL_MULTIMODAL_ID} --backend pytorch --host 0.0.0.0 --port ${MULTIMODAL_PORT} --config /configs/multimodal.yaml --trust_remote_code"

echo "Waiting for backend readiness..."
wait_for_ready "${REASONING_CONTAINER}" "${REASONING_PORT}"
wait_for_ready "${MULTIMODAL_CONTAINER}" "${MULTIMODAL_PORT}"

echo "Starting unified gateway on ${PUBLIC_PORT}..."
run_docker rm -f "${GATEWAY_CONTAINER}" >/dev/null 2>&1 || true
run_docker run -d \
  --name "${GATEWAY_CONTAINER}" \
  --restart unless-stopped \
  --network host \
  --workdir /tmp \
  -e PUBLIC_PORT="${PUBLIC_PORT}" \
  -e REASONING_URL="http://127.0.0.1:${REASONING_PORT}" \
  -e MULTIMODAL_URL="http://127.0.0.1:${MULTIMODAL_PORT}" \
  -e MODEL_REASONING_ID="${MODEL_REASONING_ID}" \
  -e MODEL_MULTIMODAL_ID="${MODEL_MULTIMODAL_ID}" \
  -e MODEL_REASONING_ALIAS="${MODEL_REASONING_ALIAS}" \
  -e MODEL_MULTIMODAL_ALIAS="${MODEL_MULTIMODAL_ALIAS}" \
  -v "${ROOT_DIR}/gateway:/router:ro" \
  "${TRTLLM_IMAGE}" \
  python3 /router/router.py

echo "Waiting for gateway readiness..."
wait_for_gateway_ready

echo "Deployment complete."
echo "Public endpoint: http://127.0.0.1:${PUBLIC_PORT}"
echo "Models endpoint:  http://127.0.0.1:${PUBLIC_PORT}/v1/models"
