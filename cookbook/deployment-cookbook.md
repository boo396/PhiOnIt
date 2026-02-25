# Deployment Cookbook: DGX Spark + TensorRT-LLM + Phi-4

This cookbook is for a fresh DGX Spark/GB10 image and deploys a unified endpoint for:

- `nvidia/Phi-4-reasoning-plus-FP8`
- `nvidia/Phi-4-multimodal-instruct-NVFP4`

## 1) Prerequisites

Required on host:

- NVIDIA GPU driver active (`nvidia-smi` works)
- Docker daemon running
- Docker NVIDIA runtime configured (`docker info` includes `nvidia` runtime)
- `curl` and `python3`
- Hugging Face token with model access

Recommended:

- Dedicated storage for model cache
- Stable outbound access to NGC and Hugging Face

## 2) Initialize repository

```bash
cd /path/to/dgx-trtllm-phi4-stack
cp env/.env.example .env
```

Edit `.env`:

- Set `HF_TOKEN`
- Keep image and model IDs pinned unless you are intentionally updating

## 3) Fresh-image setup

Run:

```bash
./scripts/setup_fresh_image.sh
```

What it does:

1. Creates `.env` from template if missing
2. Verifies host commands and GPU visibility
3. Verifies Docker and NVIDIA runtime
4. Pulls pinned TensorRT-LLM image
5. Verifies `HF_TOKEN` is present
6. Prepares cache/log directories

## 4) Deploy full stack

Run:

```bash
./scripts/deploy_stack.sh start
```

This launches:

- Reasoning backend at `127.0.0.1:8355`
- Multimodal backend at `127.0.0.1:8356`
- Unified gateway at `0.0.0.0:8080`

Lifecycle commands:

```bash
./scripts/deploy_stack.sh restart
./scripts/deploy_stack.sh stop
```

## 5) Validate deployment

Run full health verification:

```bash
./scripts/healthcheck.sh
```

Emit JSON report for dashboard/tail-style monitoring:

```bash
./scripts/health_report_json.sh > /tmp/trtllm-health.json
cat /tmp/trtllm-health.json | python3 -m json.tool
```

Use compact mode for frequent frontend polling:

```bash
./scripts/health_report_json.sh compact > /tmp/trtllm-health-compact.json
```

Increase log tail depth when troubleshooting:

```bash
TAIL_LINES=120 ./scripts/health_report_json.sh
```

### 5.1 Check containers

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expected names:

- `trtllm-phi4-reasoning`
- `trtllm-phi4-multimodal`
- `trtllm-phi4-gateway`

### 5.2 List available models from unified endpoint

```bash
curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool
```

### 5.3 Text inference test

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Phi-4-reasoning-plus-FP8",
    "messages": [{"role": "user", "content": "Write a 2 sentence summary of TensorRT-LLM."}],
    "max_tokens": 128
  }' | python3 -m json.tool
```

### 5.4 Multimodal inference test

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/Phi-4-multimodal-instruct-NVFP4",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Describe the scene in one paragraph."},
          {"type": "image_url", "image_url": {"url": "https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg"}}
        ]
      }
    ],
    "max_tokens": 128
  }' | python3 -m json.tool
```

## 6) Day-2 operations

Check logs:

```bash
docker logs trtllm-phi4-reasoning --tail 200
docker logs trtllm-phi4-multimodal --tail 200
docker logs trtllm-phi4-gateway --tail 200
```

Update stack image/model pin:

1. Edit `.env`
2. Pull image: `docker pull <new_image>`
3. Restart stack: `./scripts/deploy_stack.sh restart`

## 7) Troubleshooting

### Issue: Docker cannot access NVIDIA runtime

Symptoms:

- setup script fails at NVIDIA runtime check

Fix:

- Install/configure `nvidia-container-toolkit`
- Restart Docker daemon
- Re-run setup script

### Issue: Model download/auth errors

Symptoms:

- backend container logs show Hugging Face auth or 401/403 errors

Fix:

- Confirm `HF_TOKEN` is populated in `.env`
- Confirm token has access to the model repo
- Restart stack after updating token

### Issue: Multimodal request fails with format error

Symptoms:

- API returns invalid payload/template error

Fix:

- Use Chat Completions format
- Ensure `content` is array of typed blocks (text/image/audio/video)
- Keep `model` set to multimodal ID or alias

### Issue: OOM or startup instability

Fix:

- Reduce `max_batch_size` and/or `max_num_tokens`
- Lower `free_gpu_memory_fraction`
- Restart stack

## 8) Security and policy reminders

- Keep HF token out of shell history and Git.
- Review model card terms and geography restrictions before production deployment.
- Restrict public endpoint exposure with your network controls if not for local-only usage.
