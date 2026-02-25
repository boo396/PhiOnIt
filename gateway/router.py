#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "8080"))
REASONING_URL = os.environ.get("REASONING_URL", "http://127.0.0.1:8355")
MULTIMODAL_URL = os.environ.get("MULTIMODAL_URL", "http://127.0.0.1:8356")

MODEL_REASONING_ID = os.environ.get("MODEL_REASONING_ID", "nvidia/Phi-4-reasoning-plus-FP8")
MODEL_MULTIMODAL_ID = os.environ.get("MODEL_MULTIMODAL_ID", "nvidia/Phi-4-multimodal-instruct-NVFP4")
MODEL_REASONING_ALIAS = os.environ.get("MODEL_REASONING_ALIAS", "phi-4-reasoning-plus")
MODEL_MULTIMODAL_ALIAS = os.environ.get("MODEL_MULTIMODAL_ALIAS", "phi-4-multimodal-instruct")


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


class RouterHandler(BaseHTTPRequestHandler):
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
        if self.path in ("/healthz", "/health"):
            self._handle_health()
            return
        if self.path == "/v1/models":
            self._handle_models()
            return
        write_json(self, 404, {"error": "Not Found"})

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/v1/completions"):
            write_json(self, 404, {"error": "Unsupported path"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            write_json(self, 400, {"error": "Invalid JSON payload"})
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

        upstream_url = f"{backend}{self.path}"
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
