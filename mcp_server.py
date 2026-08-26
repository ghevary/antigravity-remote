#!/usr/bin/env python3
"""
Antigravity MCP (Model Context Protocol) Server
Exposes Google Antigravity CLI capabilities as an MCP Server over stdio.
Compatible with Hermes Agent, Claude Desktop, and other MCP clients.
"""

import sys
import os
import json
import subprocess
import time
from typing import Dict, Any, List

AGY_BIN = os.getenv("AGY_BIN_PATH", "/home/ghenom/.local/bin/agy")
DEFAULT_WORKDIR = os.getenv("DEFAULT_WORKING_DIR", os.path.expanduser("~"))
DEFAULT_EFFORT = os.getenv("AGY_EFFORT", "high")
DEFAULT_MODEL = os.getenv("AGY_MODEL", "gemini-3.7-flash-high")

# Tool definitions
TOOLS = [
    {
        "name": "antigravity_run_task",
        "description": "Execute a software engineering, coding, debugging, or system task using Google Antigravity AI Agent with full tool permissions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed task instructions or prompt for the Antigravity agent."
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional workspace working directory path. Defaults to home directory."
                },
                "model": {
                    "type": "string",
                    "description": "Optional model identifier (e.g. gemini-3.7-flash-high, claude-sonnet-4-6)."
                },
                "continue_session": {
                    "type": "boolean",
                    "description": "Whether to continue the previous conversation session (-c)."
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "antigravity_get_status",
        "description": "Check current Antigravity environment status, available models, and host system information.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

def send_response(response_dict: Dict[str, Any]):
    json_str = json.dumps(response_dict)
    sys.stdout.write(f"Content-Length: {len(json_str.encode('utf-8'))}\r\n\r\n{json_str}")
    sys.stdout.flush()

def handle_run_task(arguments: Dict[str, Any]) -> str:
    prompt = arguments.get("prompt", "")
    cwd = arguments.get("working_dir") or DEFAULT_WORKDIR
    model = arguments.get("model") or DEFAULT_MODEL
    continue_session = arguments.get("continue_session", False)

    cmd = [
        AGY_BIN,
        "-p", prompt,
        "--dangerously-skip-permissions",
        "--effort", DEFAULT_EFFORT,
        "--model", model
    ]
    if continue_session:
        cmd.append("-c")

    start_time = time.time()
    try:
        res = subprocess.run(
            cmd,
            cwd=os.path.expanduser(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300
        )
        duration = round(time.time() - start_time, 1)
        output = res.stdout.strip() if res.stdout else "(No output returned)"
        return f"[Antigravity Task Completed in {duration}s (Exit code: {res.returncode})]\n\n{output}"
    except subprocess.TimeoutExpired:
        return "[Error: Antigravity task timed out after 300 seconds]"
    except Exception as e:
        return f"[Error executing Antigravity task: {str(e)}]"

def handle_get_status(arguments: Dict[str, Any]) -> str:
    try:
        res = subprocess.run([AGY_BIN, "models"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        models_out = res.stdout.strip()
        return f"Antigravity CLI Binary: {AGY_BIN}\nDefault Workspace: {DEFAULT_WORKDIR}\nDefault Model: {DEFAULT_MODEL}\n\nAvailable Models:\n{models_out}"
    except Exception as e:
        return f"Error checking status: {str(e)}"

def process_message(message: Dict[str, Any]):
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})

    if method == "initialize":
        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "antigravity-mcp-server",
                    "version": "1.0.0"
                }
            }
        })
    elif method == "notifications/initialized":
        pass  # No response needed for initialized notification
    elif method == "tools/list":
        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": TOOLS
            }
        })
    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "antigravity_run_task":
            content_text = handle_run_task(args)
        elif tool_name == "antigravity_get_status":
            content_text = handle_get_status(args)
        else:
            content_text = f"Unknown tool: {tool_name}"

        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": content_text
                    }
                ]
            }
        })
    elif method == "ping":
        send_response({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        })
    else:
        if msg_id is not None:
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not found"
                }
            })

def main():
    buffer = ""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        buffer += line
        if "\r\n\r\n" in buffer or "\n\n" in buffer:
            parts = buffer.split("\r\n\r\n", 1) if "\r\n\r\n" in buffer else buffer.split("\n\n", 1)
            header, rest = parts[0], parts[1]
            content_length = 0
            for header_line in header.split("\r\n"):
                if header_line.lower().startswith("content-length:"):
                    content_length = int(header_line.split(":", 1)[1].strip())
            
            while len(rest.encode("utf-8")) < content_length:
                more = sys.stdin.readline()
                if not more:
                    break
                rest += more

            body_bytes = rest.encode("utf-8")[:content_length]
            buffer = rest.encode("utf-8")[content_length:].decode("utf-8", errors="ignore")

            try:
                msg_obj = json.loads(body_bytes.decode("utf-8"))
                process_message(msg_obj)
            except Exception as e:
                pass

if __name__ == "__main__":
    main()
