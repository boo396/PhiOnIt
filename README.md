# DGX Spark TensorRT-LLM Phi-4 Hosting Stack

## Re-Image / Re-Deploy (Run First)

Manage with https://build.nvidia.com/spark/connect-to-your-spark/sync Nvidia sync
Use this exact sequence on a fresh image, remember to clear SSH keys if using Nvidia sync on a rebuild with same host info
on windows at C:\Users\$user\AppData\Local\NVIDIA Corporation\Sync\config 
Delete nvsync.key

```bash
cp env/.env.example .env
nano .env                    # set HF_TOKEN, keep digest pin, set STRICT_DOCKER_DIRECT=1 for fail-fast
grep -E '^(TRTLLM_IMAGE|STRICT_DOCKER_DIRECT|MODEL_REASONING_ID|MODEL_MULTIMODAL_ID)=' .env
./scripts/setup_fresh_image.sh
./scripts/deploy_stack.sh start
./scripts/healthcheck.sh
./scripts/health_report_json.sh compact | python3 -m json.tool
```

Open UI after health passes:

```bash
xdg-open http://127.0.0.1:8080/
```

This repository deploys a **single unified OpenAI-compatible endpoint** on a DGX Spark/GB10 host while serving two TensorRT-LLM model backends:

- `nvidia/Phi-4-reasoning-plus-FP8`
- `nvidia/Phi-4-multimodal-instruct-NVFP4`

The implementation is pinned to a **container-only workflow** and aligned to the NVIDIA Spark TensorRT-LLM framework.

## Architecture

- Backend 1 (`trtllm-serve`): reasoning model on `127.0.0.1:8355`
- Backend 2 (`trtllm-serve`): multimodal model on `127.0.0.1:8356`
- Unified gateway: public endpoint on `0.0.0.0:8080` routing by `model`
- MLP-compatible router endpoint: `POST /route` (auto-selects model and invokes backend)
- Web frontend: `GET /` with static assets under `GET /static/*`

## Files

- `env/.env.example`: pinned versions and runtime environment variables
- `scripts/setup_fresh_image.sh`: host/runtime preflight and image pull
- `scripts/deploy_stack.sh`: `start|restart|stop` lifecycle for full stack
- `configs/reasoning.yaml`: reasoning runtime settings
- `configs/multimodal.yaml`: multimodal runtime settings
- `gateway/router.py`: model-aware OpenAI-style router
- `cookbook/deployment-cookbook.md`: full fresh-image operator runbook

## Quick start

1. Copy env file and set token:

   ```bash
   cp env/.env.example .env
   nano .env
   ```

  Set `HF_TOKEN=<your_huggingface_token>`.
  For strict runs, set `STRICT_DOCKER_DIRECT=1`.

  Verify effective pins:

  ```bash
  grep -E '^(TRTLLM_IMAGE|STRICT_DOCKER_DIRECT|MODEL_REASONING_ID|MODEL_MULTIMODAL_ID)=' .env
  ```

2. Run fresh-image setup:

   ```bash
   ./scripts/setup_fresh_image.sh
   ```

3. Deploy stack:

   ```bash
   ./scripts/deploy_stack.sh start
   ```

4. Check served models:

   ```bash
   curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool
   ```

  Open the web frontend:

  ```bash
  xdg-open http://127.0.0.1:8080/
  ```

5. Stop stack when needed:

   ```bash
   ./scripts/deploy_stack.sh stop
   ```

6. Run one-command health verification:

  ```bash
  ./scripts/healthcheck.sh
  ```

7. Emit dashboard-friendly JSON report (includes checks + log tails):

  ```bash
  ./scripts/health_report_json.sh | python3 -m json.tool
  ```

  Compact polling mode (no log tails, lighter payload):

  ```bash
  ./scripts/health_report_json.sh compact | python3 -m json.tool
  ```

  Optional log tail size:

  ```bash
  TAIL_LINES=100 ./scripts/health_report_json.sh
  ```

## Example requests

MLP-compatible route API (used by frontend):

```bash
curl -s http://127.0.0.1:8080/route \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Explain the difference between throughput and latency.",
    "has_image": false
  }' | python3 -m json.tool
```

Reasoning model:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Phi-4-reasoning-plus-FP8",
    "messages": [{"role": "user", "content": "Summarize tensor parallelism in 3 bullets."}],
    "max_tokens": 256
  }'
```

Multimodal model (chat API, image URL payload):

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Phi-4-multimodal-instruct-NVFP4",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Describe this image."},
          {"type": "image_url", "image_url": {"url": "https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg"}}
        ]
      }
    ],
    "max_tokens": 256
  }'
```

## Notes

- Multimodal serving is configured conservatively with `kv_cache_config.enable_block_reuse: false`.
- Use only the pinned image in `.env` to keep setup reproducible across fresh nodes.
- First startup can take longer while model artifacts are downloaded to `./data/hf-cache`.
- `STRICT_DOCKER_DIRECT=1` enforces fail-fast behavior if direct Docker socket access is unavailable.
- `STRICT_DOCKER_DIRECT=0` allows script fallback via `sg docker`.
