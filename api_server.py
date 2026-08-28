#!/usr/bin/env python3
"""
Antigravity Universal REST & OpenAI-Compatible API Server
Includes Full Server-Sent Events (SSE) Streaming Support for Hermes Agent.
"""

import os
import sys
import json
import time
import signal
import shutil
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional, List, Tuple

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

# ==========================================
# Configuration
# ==========================================
PORT = int(os.getenv("PORT", "8765"))
HOST = os.getenv("HOST", "0.0.0.0")
AGY_BIN = os.getenv("AGY_BIN_PATH", "/usr/local/bin/agy" if os.path.exists("/usr/local/bin/agy") else "/home/ghenom/.local/bin/agy")
DEFAULT_WORKDIR = os.getenv("DEFAULT_WORKING_DIR", os.path.expanduser("~"))
API_KEY = os.getenv("API_KEY", "")

# ==========================================
# Complete Model Catalog
# ==========================================
ALL_MODELS = [
    {
        "id": "gemini-3.7-flash-high",
        "name": "Gemini 3.7 Flash (High Reasoning)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "high",
        "description": "Latest Gemini 3.7 Flash with maximum reasoning effort for complex engineering."
    },
    {
        "id": "gemini-3.7-flash-medium",
        "name": "Gemini 3.7 Flash (Medium Reasoning)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "medium",
        "description": "Fast and balanced Gemini 3.7 Flash model."
    },
    {
        "id": "gemini-3.7-flash-low",
        "name": "Gemini 3.7 Flash (Low Reasoning)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "low",
        "description": "Ultra-fast response for lightweight coding queries."
    },
    {
        "id": "gemini-3.6-flash-high",
        "name": "Gemini 3.6 Flash (High)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "high",
        "description": "Stable Gemini 3.6 Flash with high reasoning."
    },
    {
        "id": "gemini-3.6-flash-medium",
        "name": "Gemini 3.6 Flash (Medium)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "medium",
        "description": "Standard Gemini 3.6 Flash model."
    },
    {
        "id": "gemini-3.6-flash-low",
        "name": "Gemini 3.6 Flash (Low)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "low",
        "description": "Low-latency Gemini 3.6 Flash."
    },
    {
        "id": "gemini-3.5-flash-high",
        "name": "Gemini 3.5 Flash (High)",
        "provider": "Google DeepMind",
        "tier": "Flash",
        "default_effort": "high",
        "description": "Gemini 3.5 Flash high capability model."
    },
    {
        "id": "gemini-3.1-pro-high",
        "name": "Gemini 3.1 Pro (High)",
        "provider": "Google DeepMind",
        "tier": "Pro",
        "default_effort": "high",
        "description": "Deep reasoning flagship Gemini Pro model."
    },
    {
        "id": "gemini-3.1-pro-low",
        "name": "Gemini 3.1 Pro (Low)",
        "provider": "Google DeepMind",
        "tier": "Pro",
        "default_effort": "low",
        "description": "Standard latency Gemini 3.1 Pro model."
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 (Thinking)",
        "provider": "Anthropic",
        "tier": "Sonnet",
        "default_effort": "high",
        "description": "Anthropic Claude Sonnet 4.6 with extended reasoning and thinking process."
    },
    {
        "id": "claude-opus-4-6-thinking",
        "name": "Claude Opus 4.6 (Thinking)",
        "provider": "Anthropic",
        "tier": "Opus",
        "default_effort": "high",
        "description": "Anthropic Claude Opus 4.6 maximum capability engineering model."
    },
    {
        "id": "gpt-oss-120b-medium",
        "name": "GPT-OSS 120B (Medium)",
        "provider": "OpenAI / OSS",
        "tier": "OpenWeight",
        "default_effort": "medium",
        "description": "Open-weight 120B parameter model with medium reasoning effort."
    }
]

# ==========================================
# Task State Manager
# ==========================================
class TaskManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_process: Optional[subprocess.Popen] = None
        self.active_task_info: Optional[Dict[str, Any]] = None

    def start_task(self, cmd: List[str], cwd: str, task_info: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> Tuple[bool, Optional[subprocess.Popen]]:
        with self.lock:
            if self.active_process and self.active_process.poll() is None:
                return False, None
            self.active_task_info = task_info
            sub_env = env or os.environ.copy()
            sub_env["HOME"] = os.path.expanduser("~")
            sub_env["USER"] = os.getenv("USER", "root")
            self.active_process = subprocess.Popen(
                cmd,
                cwd=os.path.expanduser(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=sub_env
            )
            return True, self.active_process

    def finish_task(self):
        with self.lock:
            self.active_process = None
            self.active_task_info = None

    def cancel_task(self) -> bool:
        with self.lock:
            if self.active_process and self.active_process.poll() is None:
                self.active_process.terminate()
                self.active_process = None
                self.active_task_info = None
                return True
            return False

    def is_running(self) -> bool:
        with self.lock:
            return self.active_process is not None and self.active_process.poll() is None

    def get_info(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.active_task_info

task_manager = TaskManager()

# ==========================================
# HTTP Request Handler
# ==========================================
class AntigravityAPIHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not API_KEY:
            return True
        auth_header = self.headers.get("Authorization", "")
        api_key_header = self.headers.get("X-API-Key", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            if token == API_KEY:
                return True
        if api_key_header == API_KEY:
            return True
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._check_auth():
            self._send_json(401, {"error": "Unauthorized. Invalid API Key."})
            return

        # 1. Health / Home
        if path in ["/", "/health"]:
            self._send_json(200, {
                "status": "healthy",
                "service": "Antigravity REST API",
                "version": "1.0.0",
                "total_models": len(ALL_MODELS),
                "endpoints": {
                    "GET /v1/models": "List all 14 available Antigravity models",
                    "POST /v1/agent/task": "Execute autonomous engineering agent task",
                    "POST /v1/chat/completions": "OpenAI-compatible Chat Completion API (supports stream: true)",
                    "GET /v1/status": "System status, CPU, RAM, Disk, active task",
                    "POST /v1/agent/cancel": "Cancel currently running task"
                }
            })

        # 2. List Models (OpenAI compatible format)
        elif path in ["/v1/models", "/models", "/api/v1/models", "/api/tags"]:
            openai_format = [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": m["provider"],
                    "permission": [],
                    "root": m["id"],
                    "parent": None,
                    "metadata": m
                }
                for m in ALL_MODELS
            ]
            self._send_json(200, {
                "object": "list",
                "data": openai_format,
                "models": openai_format,
                "total_models": len(ALL_MODELS)
            })

        # 3. Model Info query (Hermes compatibility)
        elif path.startswith("/v1/models/"):
            model_id = path.split("/v1/models/", 1)[1]
            found = next((m for m in ALL_MODELS if m["id"] == model_id), ALL_MODELS[0])
            self._send_json(200, {
                "id": found["id"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": found["provider"],
                "metadata": found
            })

        # 4. Status
        elif path in ["/v1/status", "/status", "/version"]:
            try:
                load1, load5, load15 = os.getloadavg()
                total_disk, used_disk, free_disk = shutil.disk_usage(DEFAULT_WORKDIR)
                free_gb = round(free_disk / (1024**3), 2)
                total_gb = round(total_disk / (1024**3), 2)
                
                is_running = task_manager.is_running()
                self._send_json(200, {
                    "version": "1.0.0",
                    "status": "running" if is_running else "idle",
                    "active_task": task_manager.get_info() if is_running else None,
                    "host": {
                        "cpu_load_avg": [load1, load5, load15],
                        "free_disk_gb": free_gb,
                        "total_disk_gb": total_gb,
                        "default_workdir": DEFAULT_WORKDIR,
                        "agy_binary": AGY_BIN
                    }
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        else:
            self._send_json(200, {"status": "ok", "path": path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._check_auth():
            self._send_json(401, {"error": "Unauthorized. Invalid API Key."})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            body = json.loads(post_body) if post_body.strip() else {}
        except Exception:
            self._send_json(400, {"error": "Invalid JSON in request body"})
            return

        # 1. Execute Agent Task (REST endpoint)
        if path == "/v1/agent/task":
            prompt = body.get("prompt")
            if not prompt:
                self._send_json(400, {"error": "Missing required field: 'prompt'"})
                return

            model = body.get("model", "gemini-3.7-flash-high")
            effort = body.get("effort", "high")
            cwd = body.get("working_dir", DEFAULT_WORKDIR)
            continue_session = body.get("continue_session", False)
            conversation_id = body.get("conversation_id")
            timeout_sec = int(body.get("timeout", 300))

            cmd = [
                AGY_BIN,
                "-p", prompt,
                "--dangerously-skip-permissions",
                "--effort", effort,
                "--model", model
            ]
            if conversation_id:
                cmd.extend(["--conversation", conversation_id])
            elif continue_session:
                cmd.append("-c")

            start_time = time.time()
            task_info = {
                "prompt": prompt[:80],
                "model": model,
                "effort": effort,
                "cwd": cwd,
                "started_at": int(start_time)
            }

            sub_env = os.environ.copy()
            sub_env["HOME"] = os.path.expanduser("~")
            sub_env["USER"] = os.getenv("USER", "root")

            started, proc = task_manager.start_task(cmd, cwd, task_info, env=sub_env)
            if not started:
                self._send_json(409, {
                    "error": "Another Antigravity task is currently running.",
                    "running_task": task_manager.get_info()
                })
                return

            try:
                stdout_data, _ = proc.communicate(timeout=timeout_sec)
                returncode = proc.returncode
                duration = round(time.time() - start_time, 2)
                task_manager.finish_task()

                self._send_json(200, {
                    "status": "success" if returncode == 0 else "error",
                    "returncode": returncode,
                    "duration_seconds": duration,
                    "model": model,
                    "effort": effort,
                    "working_dir": cwd,
                    "output": stdout_data.strip() if stdout_data else ""
                })

            except subprocess.TimeoutExpired:
                task_manager.cancel_task()
                self._send_json(504, {"error": f"Task timed out after {timeout_sec} seconds."})
            except Exception as e:
                task_manager.finish_task()
                self._send_json(500, {"error": str(e)})

        # 2. OpenAI-Compatible Chat Completions (Real-Time SSE Streaming)
        elif path in ["/v1/chat/completions", "/chat/completions"]:
            messages = body.get("messages", [])
            model = body.get("model", "gemini-3.7-flash-high")
            stream_mode = body.get("stream", False)
            
            if not messages:
                self._send_json(400, {"error": "Missing 'messages' array"})
                return

            # Extract user prompt (supporting both string and multimodal parts)
            last_msg = messages[-1].get("content", "")
            if isinstance(last_msg, list):
                last_msg = " ".join([p.get("text", "") for p in last_msg if isinstance(p, dict)])

            # Also check if system prompt exists
            system_prompts = [m.get("content", "") for m in messages if m.get("role") == "system"]
            combined_prompt = str(last_msg)
            if system_prompts:
                combined_prompt = f"[System Context: {' '.join(system_prompts)}]\n\n{combined_prompt}"

            if len(combined_prompt) > 80000:
                combined_prompt = combined_prompt[:20000] + "\n...[middle context truncated]...\n" + combined_prompt[-60000:]

            cmd = [
                AGY_BIN,
                "-p", combined_prompt,
                "--dangerously-skip-permissions",
                "--model", model,
                "--print-timeout", "10m"
            ]

            start_t = time.time()
            response_id = f"chatcmpl-{int(start_t)}"
            sub_env = os.environ.copy()
            sub_env["HOME"] = os.path.expanduser("~")
            sub_env["USER"] = os.getenv("USER", "root")

            if stream_mode:
                # Send headers immediately to prevent client timeout
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                try:
                    # Send initial role chunk immediately
                    initial_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": int(start_t),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": ""},
                                "finish_reason": None
                            }
                        ]
                    }
                    self.wfile.write(f"data: {json.dumps(initial_chunk)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                    proc = subprocess.Popen(
                        cmd,
                        cwd=DEFAULT_WORKDIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        env=sub_env
                    )

                    while True:
                        line = proc.stdout.readline()
                        if not line and proc.poll() is not None:
                            break
                        if line:
                            # Stream text chunk
                            chunk_data = {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": int(start_t),
                                "model": model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": line},
                                        "finish_reason": None
                                    }
                                ]
                            }
                            self.wfile.write(f"data: {json.dumps(chunk_data)}\n\n".encode("utf-8"))
                            self.wfile.flush()

                    # Final finish chunk
                    final_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": int(start_t),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop"
                            }
                        ]
                    }
                    self.wfile.write(f"data: {json.dumps(final_chunk)}\n\n".encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()

                except (BrokenPipeError, ConnectionResetError):
                    # Client disconnected or timed out
                    if 'proc' in locals() and proc.poll() is None:
                        proc.terminate()
                except Exception as e:
                    if 'proc' in locals() and proc.poll() is None:
                        proc.terminate()
                    try:
                        err_chunk = {"error": str(e)}
                        self.wfile.write(f"data: {json.dumps(err_chunk)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        pass

            else:
                # Non-streaming response
                try:
                    res = subprocess.run(
                        cmd,
                        cwd=DEFAULT_WORKDIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=600,
                        env=sub_env
                    )
                    content_out = res.stdout.strip() if res.stdout else "Antigravity process completed."

                    self._send_json(200, {
                        "id": response_id,
                        "object": "chat.completion",
                        "created": int(start_t),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": content_out
                                },
                                "finish_reason": "stop"
                            }
                        ],
                        "usage": {
                            "prompt_tokens": max(1, len(str(last_msg)) // 4),
                            "completion_tokens": max(1, len(content_out) // 4),
                            "total_tokens": max(2, (len(str(last_msg)) + len(content_out)) // 4)
                        }
                    })
                except Exception as e:
                    self._send_json(500, {"error": str(e)})

        # 3. Cancel Task
        elif path == "/v1/agent/cancel":
            cancelled = task_manager.cancel_task()
            if cancelled:
                self._send_json(200, {"status": "cancelled", "message": "Active task terminated."})
            else:
                self._send_json(200, {"status": "idle", "message": "No active task running."})

        else:
            self._send_json(200, {"status": "ok", "path": path})

def main():
    print("=" * 60)
    print(" 🚀 Antigravity Universal REST & OpenAI API Server (Streaming)")
    print("=" * 60)
    print(f"[+] Listening on: http://{HOST}:{PORT}")
    print(f"[+] Antigravity Binary: {AGY_BIN}")
    print(f"[+] Default Working Directory: {DEFAULT_WORKDIR}")
    print(f"[+] Loaded Models: {len(ALL_MODELS)} models available")
    print(f"[+] Server-Sent Events (SSE) Streaming: Enabled")
    print("=" * 60)

    server = ThreadedHTTPServer((HOST, PORT), AntigravityAPIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Shutting down REST API server.")
        server.server_close()

if __name__ == "__main__":
    main()
