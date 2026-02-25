#!/usr/bin/env python3
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8080"))
REASONING_URL = os.environ.get("REASONING_URL", "http://127.0.0.1:8355")
MULTIMODAL_URL = os.environ.get("MULTIMODAL_URL", "http://127.0.0.1:8356")

MODEL_REASONING_ID = os.environ.get("MODEL_REASONING_ID", "nvidia/Phi-4-reasoning-plus-FP8")
MODEL_MULTIMODAL_ID = os.environ.get("MODEL_MULTIMODAL_ID", "nvidia/Phi-4-multimodal-instruct-NVFP4")
MODEL_REASONING_ALIAS = os.environ.get("MODEL_REASONING_ALIAS", "phi-4-reasoning-plus")
MODEL_MULTIMODAL_ALIAS = os.environ.get("MODEL_MULTIMODAL_ALIAS", "phi-4-multimodal-instruct")

STATIC_DIR = Path(__file__).resolve().parent / "static"

_CPU_PREV_TOTAL = None
_CPU_PREV_IDLE = None


def write_json(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def resolve_backend(model_name):
    reasoning_candidates = {MODEL_REASONING_ID, MODEL_REASONING_ALIAS}
    multimodal_candidates = {MODEL_MULTIMODAL_ID, MODEL_MULTIMODAL_ALIAS}

    if model_name in reasoning_candidates:
        return REASONING_URL
    if model_name in multimodal_candidates:
        return MULTIMODAL_URL
    return None


def _normalize_percent(value):
    return max(0.0, min(100.0, float(value)))


def _collect_local_memory_stats():
    try:
        mem_total_kb = None
        mem_available_kb = None
        with open("/proc/meminfo", "r", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_available_kb = int(line.split()[1])

        if not mem_total_kb or mem_available_kb is None:
            return None, None, None

        used_ratio = (mem_total_kb - mem_available_kb) / mem_total_kb
        mem_total_gb = mem_total_kb / (1024 * 1024)
        mem_used_gb = (mem_total_kb - mem_available_kb) / (1024 * 1024)
        return _normalize_percent(used_ratio * 100.0), mem_used_gb, mem_total_gb
    except Exception:
        return None, None, None


def _collect_local_gpu_percent():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not values:
            return None
        return _normalize_percent(max(float(value) for value in values))
    except Exception:
        return None


def _collect_local_cpu_percent():
    global _CPU_PREV_TOTAL
    global _CPU_PREV_IDLE

    try:
        with open("/proc/stat", "r", encoding="utf-8") as proc_stat:
            first_line = proc_stat.readline().strip()

        parts = first_line.split()
        if len(parts) < 5 or parts[0] != "cpu":
            return None

        values = [float(value) for value in parts[1:9]]
        user, nice, system, idle, iowait, irq, softirq, steal = values

        idle_all = idle + iowait
        non_idle = user + nice + system + irq + softirq + steal
        total = idle_all + non_idle

        if _CPU_PREV_TOTAL is None or _CPU_PREV_IDLE is None:
            _CPU_PREV_TOTAL = total
            _CPU_PREV_IDLE = idle_all
            return None

        total_delta = total - _CPU_PREV_TOTAL
        idle_delta = idle_all - _CPU_PREV_IDLE

        _CPU_PREV_TOTAL = total
        _CPU_PREV_IDLE = idle_all

        if total_delta <= 0:
            return None

        cpu_ratio = (total_delta - idle_delta) / total_delta
        return _normalize_percent(cpu_ratio * 100.0)
    except Exception:
        return None


def _collect_local_cpu_clock_stats():
    try:
        current_values = []
        with open("/proc/cpuinfo", "r", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.lower().startswith("cpu mhz"):
                    current_values.append(float(line.split(":", 1)[1].strip()))

        current_mhz = sum(current_values) / len(current_values) if current_values else None

        if current_mhz is None:
            for cur_path in (
                Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
                Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq"),
            ):
                if cur_path.exists():
                    try:
                        current_khz = float(cur_path.read_text(encoding="utf-8").strip())
                        current_mhz = current_khz / 1000.0
                        break
                    except Exception:
                        continue

        max_mhz = None
        max_freq_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
        if max_freq_path.exists():
            max_khz = float(max_freq_path.read_text(encoding="utf-8").strip())
            max_mhz = max_khz / 1000.0

        if max_mhz is None:
            scaling_max_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq")
            if scaling_max_path.exists():
                try:
                    max_khz = float(scaling_max_path.read_text(encoding="utf-8").strip())
                    max_mhz = max_khz / 1000.0
                except Exception:
                    pass

        if max_mhz is None and current_mhz is not None:
            max_mhz = current_mhz

        return current_mhz, max_mhz
    except Exception:
        return None, None


def _collect_local_gpu_clock_stats():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=clocks.current.graphics,clocks.max.graphics",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None, None

        first = lines[0].split(",")
        if len(first) < 2:
            return None, None

        return float(first[0].strip()), float(first[1].strip())
    except Exception:
        return None, None


def _infer_route(text, has_image):
    text_lower = text.lower()

    if has_image:
        probabilities = {MODEL_REASONING_ID: 0.01, MODEL_MULTIMODAL_ID: 0.99}
        return MODEL_MULTIMODAL_ID, 0.99, "shortcut", probabilities, [MODEL_MULTIMODAL_ID, MODEL_REASONING_ID]

    reasoning_keywords = ["reason", "analy", "proof", "derive", "step", "logic", "math", "explain why"]
    multimodal_keywords = ["image", "photo", "picture", "vision", "audio", "video"]

    if any(keyword in text_lower for keyword in multimodal_keywords):
        probabilities = {MODEL_REASONING_ID: 0.15, MODEL_MULTIMODAL_ID: 0.85}
        return MODEL_MULTIMODAL_ID, 0.85, "shortcut", probabilities, [MODEL_MULTIMODAL_ID, MODEL_REASONING_ID]

    if any(keyword in text_lower for keyword in reasoning_keywords):
        probabilities = {MODEL_REASONING_ID: 0.88, MODEL_MULTIMODAL_ID: 0.12}
        return MODEL_REASONING_ID, 0.88, "shortcut", probabilities, [MODEL_REASONING_ID, MODEL_MULTIMODAL_ID]

    probabilities = {MODEL_REASONING_ID: 0.65, MODEL_MULTIMODAL_ID: 0.35}
    return MODEL_REASONING_ID, 0.65, "mlp_compat", probabilities, [MODEL_REASONING_ID, MODEL_MULTIMODAL_ID]


def _forward_json(method, url, payload=None, timeout=600):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        return response.status, body, response.headers.get("Content-Type", "application/json")


def _invoke_model(text, model_name, image_url=None, image_path=None):
    backend = resolve_backend(model_name)
    if backend is None:
        return {"ok": False, "error": f"unknown model: {model_name}"}

    is_multimodal = model_name in {MODEL_MULTIMODAL_ID, MODEL_MULTIMODAL_ALIAS}
    if is_multimodal and image_url:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]
    elif is_multimodal and image_path:
        messages = [
            {
                "role": "user",
                "content": f"{text}\n\nimage_path hint: {image_path}",
            }
        ]
    else:
        messages = [{"role": "user", "content": text}]

    payload = {"model": model_name, "messages": messages, "max_tokens": 256}
    try:
        status, body, _ = _forward_json("POST", f"{backend}/v1/chat/completions", payload=payload, timeout=600)
        if status != 200:
            return {"ok": False, "error": f"backend status {status}"}
        decoded = json.loads(body.decode("utf-8"))
        choice = decoded.get("choices", [{}])[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        text_out = message.get("content", "") if isinstance(message, dict) else ""
        return {
            "ok": True,
            "result": {
                "text": text_out,
                "used_precision": "runtime",
                "raw": decoded,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class RouterHandler(BaseHTTPRequestHandler):
    def _handle_route(self, payload):
        text = str(payload.get("text", "")).strip()
        if not text:
            write_json(self, 400, {"error": "text is required"})
            return

        has_image = bool(payload.get("has_image") or payload.get("image_url") or payload.get("image_path"))
        image_url = payload.get("image_url")
        image_path = payload.get("image_path")

        model, confidence, source, probs, top_k_models = _infer_route(text=text, has_image=has_image)
        model_alias = MODEL_MULTIMODAL_ALIAS if model == MODEL_MULTIMODAL_ID else MODEL_REASONING_ALIAS
        dispatch_backend = "trtllm-serve"

        worker_result = _invoke_model(text=text, model_name=model, image_url=image_url, image_path=image_path)
        if worker_result.get("ok"):
            worker_status = "ok"
            worker_response = {
                "details": {
                    "target_model": model,
                    "target_alias": model_alias,
                    "result": worker_result.get("result", {}),
                }
            }
        else:
            worker_status = f"error: {worker_result.get('error', 'unknown')}"
            worker_response = {"details": {"error": worker_result.get("error", "unknown")}}

        write_json(
            self,
            200,
            {
                "model": model,
                "confidence": confidence,
                "source": source,
                "probabilities": probs,
                "top_k_models": top_k_models,
                "dispatch_target": model_alias,
                "dispatch_backend": dispatch_backend,
                "worker_invoked": True,
                "worker_status": worker_status,
                "worker_response": worker_response,
            },
        )

    def _handle_telemetry_snapshot(self):
        local_memory_percent, local_memory_used_gb, local_memory_total_gb = _collect_local_memory_stats()
        local_gpu_percent = _collect_local_gpu_percent()
        local_cpu_percent = _collect_local_cpu_percent()
        cpu_clock_mhz, cpu_clock_max_mhz = _collect_local_cpu_clock_stats()
        gpu_clock_mhz, gpu_clock_max_mhz = _collect_local_gpu_clock_stats()

        write_json(
            self,
            200,
            {
                "ok": True,
                "source": "local_system",
                "memory_percent": local_memory_percent,
                "memory_used_gb": local_memory_used_gb,
                "memory_total_gb": local_memory_total_gb,
                "gpu_percent": local_gpu_percent,
                "cpu_percent": local_cpu_percent,
                "cpu_clock_mhz": cpu_clock_mhz,
                "cpu_clock_max_mhz": cpu_clock_max_mhz,
                "gpu_clock_mhz": gpu_clock_mhz,
                "gpu_clock_max_mhz": gpu_clock_max_mhz,
                "auth_mode": "local_only",
                "ts": int(time.time()),
            },
        )

    def _serve_static(self, requested_path):
        if requested_path == "/":
            file_path = STATIC_DIR / "index.html"
        elif requested_path.startswith("/static/"):
            relative = requested_path.removeprefix("/static/")
            file_path = (STATIC_DIR / relative).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                write_json(self, 403, {"error": "Forbidden"})
                return
        else:
            write_json(self, 404, {"error": "Not Found"})
            return

        if not file_path.exists() or not file_path.is_file():
            write_json(self, 404, {"error": "Not Found"})
            return

        content = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _handle_models(self):
        payload = {
            "object": "list",
            "data": [
                {"id": MODEL_REASONING_ID, "object": "model", "owned_by": "nvidia"},
                {"id": MODEL_REASONING_ALIAS, "object": "model", "owned_by": "local"},
                {"id": MODEL_MULTIMODAL_ID, "object": "model", "owned_by": "nvidia"},
                {"id": MODEL_MULTIMODAL_ALIAS, "object": "model", "owned_by": "local"},
            ],
        }
        write_json(self, 200, payload)

    def _handle_health(self):
        write_json(self, 200, {"status": "ok"})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/healthz", "/health"):
            self._handle_health()
            return
        if path == "/v1/models":
            self._handle_models()
            return
        if path == "/telemetry/snapshot":
            self._handle_telemetry_snapshot()
            return
        if path == "/" or path.startswith("/static/"):
            self._serve_static(path)
            return
        write_json(self, 404, {"error": "Not Found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path not in ("/v1/chat/completions", "/v1/completions", "/route"):
            write_json(self, 404, {"error": "Unsupported path"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            write_json(self, 400, {"error": "Invalid JSON payload"})
            return

        if path == "/route":
            self._handle_route(payload)
            return

        model_name = payload.get("model")
        if not model_name:
            write_json(self, 400, {"error": "Request must include model"})
            return

        backend = resolve_backend(model_name)
        if backend is None:
            write_json(
                self,
                400,
                {
                    "error": (
                        "Unknown model. Use one of: "
                        f"{MODEL_REASONING_ID}, {MODEL_REASONING_ALIAS}, "
                        f"{MODEL_MULTIMODAL_ID}, {MODEL_MULTIMODAL_ALIAS}"
                    )
                },
            )
            return

        upstream_url = f"{backend}{path}"
        headers = {"Content-Type": "application/json"}
        auth = self.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth

        req = urllib.request.Request(upstream_url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=600) as response:
                response_body = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
        except urllib.error.HTTPError as exc:
            err_body = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as exc:
            write_json(self, 502, {"error": f"Upstream failure: {exc}"})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), RouterHandler)
    print(f"Gateway listening on 0.0.0.0:{PUBLIC_PORT}")
    server.serve_forever()
