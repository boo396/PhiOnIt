#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE="${ROOT_DIR}/env/.env.example"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} from template. Populate HF_TOKEN before deploy."
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

check_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    return 1
  fi
}

echo "[1/6] Checking required host commands..."
check_cmd nvidia-smi
check_cmd docker
check_cmd curl
check_cmd python3

echo "[2/6] Validating GPU visibility..."
nvidia-smi >/dev/null

echo "[3/6] Validating Docker daemon access..."
run_docker info >/dev/null

echo "[4/6] Validating GPU container support in Docker..."
if ! run_docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
  echo "Docker GPU container test failed."
  echo "Configure nvidia-container-toolkit and Docker GPU support, then retry."
  exit 1
fi

echo "[5/6] Pulling pinned TensorRT-LLM image: ${TRTLLM_IMAGE}"
run_docker pull "${TRTLLM_IMAGE}"

echo "[6/6] Checking HF token presence..."
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is empty in ${ENV_FILE}."
  echo "Set HF_TOKEN before deployment to download gated/public model artifacts."
  exit 1
fi

mkdir -p "${ROOT_DIR}/data/hf-cache" "${ROOT_DIR}/logs"

echo "Fresh-image setup checks complete."
echo "Next: run scripts/deploy_stack.sh start"
