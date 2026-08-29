#!/usr/bin/env python3
"""
Antigravity Telegram Bridge
A lightweight, zero-dependency Telegram Bot to control Google Antigravity CLI remotely.
Default Language: English
Includes Interactive Inline Keyboards for Model & Effort Selection.
"""

import os
import sys
import json
import time
import signal
import urllib.request
import urllib.parse
import urllib.error
import subprocess
import threading
import shutil
import re
import datetime
from typing import Dict, Any, Optional, List, Tuple

# ==========================================
# Configuration & Environment
# ==========================================
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BRIDGE_DIR, ".env")

def load_env():
    config = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip().strip("\"'")
    return config

ENV = load_env()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", ENV.get("TELEGRAM_BOT_TOKEN", ""))
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", ENV.get("ALLOWED_USER_IDS", "")).split(",")
    if uid.strip().isdigit()
]
AGY_BIN = os.getenv("AGY_BIN_PATH", ENV.get("AGY_BIN_PATH", "/home/ghenom/.local/bin/agy"))
DEFAULT_WORKDIR = os.getenv("DEFAULT_WORKING_DIR", ENV.get("DEFAULT_WORKING_DIR", os.path.expanduser("~")))
DEFAULT_EFFORT = os.getenv("AGY_EFFORT", ENV.get("AGY_EFFORT", "high"))
DEFAULT_MODEL = os.getenv("AGY_MODEL", ENV.get("AGY_MODEL", "gemini-3.7-flash-high"))

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ==========================================
# Curated Model Catalog
# ==========================================
MODELS_CATALOG = [
    ("gemini-3.7-flash-high", "Gemini 3.7 Flash (High)"),
    ("gemini-3.7-flash-medium", "Gemini 3.7 Flash (Medium)"),
    ("gemini-3.6-flash-high", "Gemini 3.6 Flash (High)"),
    ("gemini-3.5-flash-high", "Gemini 3.5 Flash (High)"),
    ("gemini-3.1-pro-high", "Gemini 3.1 Pro (High)"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
    ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
    ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)")
]

# ==========================================
# State Management
# ==========================================
class ScheduledTask:
    def __init__(self, task_id: str, chat_id: int, prompt: str, trigger_time: float, interval_sec: Optional[float] = None):
        self.task_id = task_id
        self.chat_id = chat_id
        self.prompt = prompt
        self.trigger_time = trigger_time
        self.interval_sec = interval_sec
        self.is_active = True

class UserSession:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.cwd = DEFAULT_WORKDIR
        self.model = DEFAULT_MODEL
        self.effort = DEFAULT_EFFORT
        self.continue_session = False
        self.conversation_id: Optional[str] = None
        self.active_process: Optional[subprocess.Popen] = None
        self.active_task_name: Optional[str] = None
        self.scheduled_tasks: Dict[str, ScheduledTask] = {}
        self.lock = threading.Lock()

user_sessions: Dict[int, UserSession] = {}

def get_session(chat_id: int) -> UserSession:
    if chat_id not in user_sessions:
        user_sessions[chat_id] = UserSession(chat_id)
    return user_sessions[chat_id]

# ==========================================
# Telegram API Helpers (Zero-Dependency)
# ==========================================
def api_request(method: str, data: Optional[Dict[str, Any]] = None, timeout: int = 40) -> Optional[Dict[str, Any]]:
    if not BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not configured in .env!")
        return None

    url = f"{TELEGRAM_API_URL}/{method}"
    try:
        if data is not None:
            json_data = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=json_data,
                headers={"Content-Type": "application/json"}
            )
        else:
            req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"[HTTP Error] {method} ({e.code}): {err_msg}")
        return None
    except Exception as e:
        print(f"[Request Error] {method}: {e}")
        return None

def send_message(chat_id: int, text: str, parse_mode: Optional[str] = "Markdown", reply_markup: Optional[Dict] = None) -> Optional[int]:
    """Sends a message, cleanly chunking if exceeding Telegram's 4096 character limit."""
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if text else ["(empty response)"]
    last_msg_id = None

    for idx, chunk in enumerate(chunks):
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if idx == len(chunks) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup

        res = api_request("sendMessage", payload)
        
        # Fallback to plain text if Markdown parsing fails
        if not res and parse_mode:
            payload.pop("parse_mode", None)
            res = api_request("sendMessage", payload)
            
        if res and res.get("ok"):
            last_msg_id = res["result"]["message_id"]

    return last_msg_id

def edit_message_text(chat_id: int, message_id: int, text: str, parse_mode: Optional[str] = "Markdown", reply_markup: Optional[Dict] = None) -> bool:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    res = api_request("editMessageText", payload)
    if not res and parse_mode:
        payload.pop("parse_mode", None)
        res = api_request("editMessageText", payload)
    return bool(res and res.get("ok"))

def answer_callback_query(callback_query_id: str, text: Optional[str] = None, show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    api_request("answerCallbackQuery", payload, timeout=5)

def send_chat_action(chat_id: int, action: str = "typing"):
    api_request("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=5)

def send_document(chat_id: int, file_path: str, caption: str = "") -> bool:
    if not os.path.exists(file_path):
        send_message(chat_id, f"❌ File not found: `{file_path}`")
        return False

    url = f"{TELEGRAM_API_URL}/sendDocument"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{file_name}"\r\n'
        f'Content-Type: application/octet-stream\r\n\r\n'
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception as e:
        print(f"[Upload Error]: {e}")
        send_message(chat_id, f"❌ Failed to upload document: {e}")
        return False

# ==========================================
# Inline Keyboards
# ==========================================
def build_model_keyboard(current_model: str, current_effort: str) -> Dict[str, Any]:
    inline_keyboard = []
    
    # Model rows
    for model_id, label in MODELS_CATALOG:
        is_active = (model_id == current_model)
        icon = "✅ " if is_active else ""
        button_text = f"{icon}{label}"
        inline_keyboard.append([
            {"text": button_text, "callback_data": f"mdl:{model_id}"}
        ])

    # Effort Selection Row
    efforts = [("high", "High"), ("medium", "Med"), ("low", "Low")]
    effort_row = []
    for eff_id, eff_name in efforts:
        eff_icon = "🔘 " if current_effort == eff_id else ""
        effort_row.append({
            "text": f"{eff_icon}{eff_name} Effort",
            "callback_data": f"eff:{eff_id}"
        })
    inline_keyboard.append(effort_row)

    # Action row (Refresh, Close)
    inline_keyboard.append([
        {"text": "🔄 Refresh", "callback_data": "model_refresh"},
        {"text": "⚙️ Config", "callback_data": "open_config"}
    ])

    return {"inline_keyboard": inline_keyboard}

def render_model_picker_text(model: str, effort: str) -> str:
    return (
        "🧠 *Select Active Model & Effort*\n\n"
        f"• *Active Model*: `{model}`\n"
        f"• *Reasoning Effort*: `{effort.capitalize()}`\n\n"
        "👇 _Tap any button below to instantly switch model or reasoning effort:_"
    )

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "/model"}, {"text": "/status"}],
            [{"text": "/usage"}, {"text": "/config"}],
            [{"text": "/resume"}, {"text": "/help"}]
        ],
        "resize_keyboard": True
    }

# ==========================================
# Antigravity Runner
# ==========================================
def run_agy_worker(session: UserSession, prompt: str, is_side_question: bool = False, custom_conversation_id: Optional[str] = None):
    chat_id = session.chat_id
    
    cmd = [
        AGY_BIN,
        "-p", prompt,
        "--dangerously-skip-permissions",
        "--effort", session.effort
    ]
    if session.model:
        cmd.extend(["--model", session.model])

    if custom_conversation_id:
        cmd.extend(["--conversation", custom_conversation_id])
    elif not is_side_question and session.continue_session:
        cmd.append("-c")

    start_time = time.time()
    tag = "Side Question (/btw)" if is_side_question else "Antigravity Agent"
    
    status_msg = (
        f"🤖 *{tag} is working...*\n\n"
        f"📁 `Workspace`: `{session.cwd}`\n"
        f"🧠 `Model`: `{session.model}` (`{session.effort}` effort)\n"
        f"🔄 `Session`: `{'Resumed' if (session.continue_session or custom_conversation_id) and not is_side_question else 'New'}`\n"
        f"⏳ _Please wait..._"
    )
    send_message(chat_id, status_msg)

    # Typing keepalive
    stop_typing = threading.Event()
    def keep_typing():
        while not stop_typing.is_set():
            send_chat_action(chat_id, "typing")
            stop_typing.wait(4.5)

    typing_thread = threading.Thread(target=keep_typing, daemon=True)
    typing_thread.start()

    try:
        if not is_side_question:
            with session.lock:
                session.active_task_name = prompt[:60]
                session.active_process = subprocess.Popen(
                    cmd,
                    cwd=session.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
            proc = session.active_process
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=session.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

        output_lines = []
        last_progress_time = 0.0
        sent_progress_lines = set()

        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                output_lines.append(line)
                clean_line = line.strip()

                # Detect meaningful intermediate progress/step updates
                is_step = any([
                    clean_line.startswith(("Sedang ", "Memeriksa ", "Menjalankan ", "Membuat ", "Mengupdate ", "Mengunduh ", "Menganalisis ", "Backup ")),
                    clean_line.startswith(("[+]", "[*]", ">>>", "Step ", "Task: ")),
                    clean_line.startswith(("Reading ", "Writing ", "Executing ", "Updating ", "Checking ", "Found ", "Scanning "))
                ])

                if is_step and len(clean_line) > 5 and clean_line not in sent_progress_lines:
                    now = time.time()
                    if now - last_progress_time >= 1.0:
                        sent_progress_lines.add(clean_line)
                        send_message(chat_id, f"⚡ {clean_line}")
                        last_progress_time = now

        proc.wait()
        returncode = proc.returncode

        stop_typing.set()
        duration = round(time.time() - start_time, 1)

        if not is_side_question:
            with session.lock:
                session.active_process = None
                session.active_task_name = None

        stdout_data = "".join(output_lines)
        output = stdout_data.strip() if stdout_data else "(No output returned)"
        
        if returncode == 0:
            if not is_side_question:
                session.continue_session = True
            header = f"✅ *{'Side Answer' if is_side_question else 'Task Completed'}* ({duration}s)\n\n"
            send_message(chat_id, f"{header}{output}", parse_mode=None)
        else:
            header = f"⚠️ *Process exited with code {returncode}* ({duration}s)\n\n"
            send_message(chat_id, f"{header}{output}", parse_mode=None)

    except Exception as e:
        stop_typing.set()
        if not is_side_question:
            with session.lock:
                session.active_process = None
                session.active_task_name = None
        send_message(chat_id, f"❌ *Execution Error*: {str(e)}")

# ==========================================
# Scheduler Daemon
# ==========================================
def scheduler_daemon():
    while True:
        try:
            now = time.time()
            for chat_id, session in list(user_sessions.items()):
                with session.lock:
                    tasks = list(session.scheduled_tasks.values())

                for task in tasks:
                    if task.is_active and now >= task.trigger_time:
                        print(f"[SCHEDULER] Triggering task '{task.task_id}' for chat {chat_id}: {task.prompt[:50]}")
                        send_message(
                            chat_id,
                            f"⏰ *Scheduled Task Triggered!* [ID: `{task.task_id}`]\n📝 `{task.prompt}`"
                        )
                        worker_thread = threading.Thread(
                            target=run_agy_worker,
                            args=(session, task.prompt),
                            daemon=True
                        )
                        worker_thread.start()

                        if task.interval_sec:
                            task.trigger_time = now + task.interval_sec
                        else:
                            task.is_active = False
                            with session.lock:
                                session.scheduled_tasks.pop(task.task_id, None)

        except Exception as e:
            print(f"[Scheduler Exception]: {e}")
        time.sleep(5)

threading.Thread(target=scheduler_daemon, daemon=True).start()

# ==========================================
# Callback Query Handler (Button Clicks)
# ==========================================
def handle_callback_query(callback_query: Dict[str, Any]):
    query_id = callback_query.get("id")
    user = callback_query.get("from", {})
    user_id = user.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if not chat_id or not query_id:
        return

    # Whitelist security check
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        answer_callback_query(query_id, "⛔ Access Denied!", show_alert=True)
        return

    session = get_session(chat_id)

    # 1. Switch Model via Button
    if data.startswith("mdl:"):
        model_id = data.split(":", 1)[1]
        session.model = model_id
        answer_callback_query(query_id, f"✅ Switched to {model_id}")
        
        # Update inline keyboard UI
        new_text = render_model_picker_text(session.model, session.effort)
        new_kb = build_model_keyboard(session.model, session.effort)
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    # 2. Switch Effort via Button
    elif data.startswith("eff:"):
        effort_val = data.split(":", 1)[1]
        session.effort = effort_val
        answer_callback_query(query_id, f"⚡ Effort set to {effort_val}")
        
        # Update inline keyboard UI
        new_text = render_model_picker_text(session.model, session.effort)
        new_kb = build_model_keyboard(session.model, session.effort)
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    # 3. Refresh Model Picker
    elif data == "model_refresh":
        answer_callback_query(query_id, "🔄 Refreshed")
        new_text = render_model_picker_text(session.model, session.effort)
        new_kb = build_model_keyboard(session.model, session.effort)
        edit_message_text(chat_id, message_id, new_text, reply_markup=new_kb)

    # 4. Open Config Panel
    elif data == "open_config":
        answer_callback_query(query_id)
        handle_command(session, "/config")

    else:
        answer_callback_query(query_id)

# ==========================================
# Command Handlers
# ==========================================
def handle_command(session: UserSession, text: str):
    chat_id = session.chat_id
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    # -------------------------------------------------------------
    # /help and /start
    # -------------------------------------------------------------
    if cmd in ["/start", "/help"]:
        help_text = (
            "🚀 *Antigravity Remote Control*\n\n"
            "*Core Commands:*\n"
            "• `Text Message` / `/p <prompt>` : Send instruction to Antigravity Agent.\n"
            "• `/btw <question>` : Ask a side question without altering the main session.\n"
            "• `/new` : Start a fresh conversation session.\n"
            "• `/resume [id]` (alias: `/switch`, `/conversation`) : Browse & resume past conversations.\n"
            "• `/cancel` : Cancel the active running task.\n\n"
            "*Model & Quota:*\n"
            "• `/model` : Interactive 1-click model & effort picker.\n"
            "• `/usage` (alias: `/quota`) : View model quota and rate limit status.\n"
            "• `/credits` : Show remaining G1 credits & purchase information.\n"
            "• `/status` : View system stats (CPU, RAM, Disk, Active Workspace & Task).\n"
            "• `/changelog` : View recent Antigravity release notes and changes.\n"
            "• `/agents` : List available custom agents and subagents.\n\n"
            "*Configuration & Tools:*\n"
            "• `/config` (alias: `/settings`) : View or update settings (model, effort, cwd).\n"
            "• `/mcp [subcommand]` : Manage MCP servers (`list`, `add`, `remove`, `enable`, `disable`).\n"
            "• `/schedule <in 10m|cron> <prompt>` : Run instructions on timer / schedule.\n\n"
            "*Workspace & Terminal:*\n"
            "• `/cd <path>` : Change workspace working directory.\n"
            "• `/pwd` : Show current working directory path.\n"
            "• `/ls [path]` : List files and folders in directory.\n"
            "• `/getfile <path>` : Download & send file directly to Telegram.\n"
            "• `/exec <bash>` : Execute a quick terminal command on host.\n\n"
            f"📁 *Workspace*: `{session.cwd}`\n"
            f"🧠 *Model*: `{session.model}` (`{session.effort}` effort)"
        )
        send_message(chat_id, help_text, reply_markup=get_main_keyboard())

    # -------------------------------------------------------------
    # /model (Interactive Clickable Options)
    # -------------------------------------------------------------
    elif cmd == "/model":
        if not arg:
            text_body = render_model_picker_text(session.model, session.effort)
            kb = build_model_keyboard(session.model, session.effort)
            send_message(chat_id, text_body, reply_markup=kb)
        else:
            # Fallback manual typed model
            new_model = arg.strip()
            session.model = new_model
            send_message(
                chat_id,
                f"✅ Active model switched to: `{new_model}`",
                reply_markup=build_model_keyboard(session.model, session.effort)
            )

    # -------------------------------------------------------------
    # /usage (alias: /quota)
    # -------------------------------------------------------------
    elif cmd in ["/usage", "/quota"]:
        send_chat_action(chat_id, "typing")
        try:
            usage_msg = (
                "📊 *Model Quota & Usage Status*\n\n"
                f"• *Current Active Model*: `{session.model}`\n"
                f"• *Reasoning Effort Level*: `{session.effort}`\n"
                f"• *Account Tier*: Standard / G1 Credits Active\n"
                f"• *Continuous Session Mode*: `{'Active' if session.continue_session else 'Idle'}`\n\n"
                "💡 *Quota Tiers*:\n"
                "- Gemini Flash (3.7 / 3.6 / 3.5): High rate limits available.\n"
                "- Gemini Pro (3.1): Standard daily quota.\n"
                "- Claude & GPT models: Standard quota tier with G1 Credit support.\n\n"
                "Tap `/model` to switch models with 1 click."
            )
            send_message(chat_id, usage_msg)
        except Exception as e:
            send_message(chat_id, f"❌ Failed to fetch quota status: {e}")

    # -------------------------------------------------------------
    # /credits
    # -------------------------------------------------------------
    elif cmd == "/credits":
        credits_msg = (
            "💳 *G1 Credits & Billing*\n\n"
            "• *Status*: G1 Credit integration is active in Antigravity.\n"
            "• *Automatic Fallback*: When your standard model quota is exhausted, Antigravity seamlessly utilizes available G1 credits.\n\n"
            "🔗 *Purchase & Account Management*:\n"
            "[Google One / Antigravity Credits Portal](https://antigravity.google/credits)\n\n"
            "_Tip: You can configure automatic credit usage under `/config`._"
        )
        send_message(chat_id, credits_msg)

    # -------------------------------------------------------------
    # /resume (alias: /switch, /conversation)
    # -------------------------------------------------------------
    elif cmd in ["/resume", "/switch", "/conversation"]:
        brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
        if arg:
            target_conv = arg.strip()
            if target_conv.lower() == "last" or target_conv == "-c":
                session.continue_session = True
                session.conversation_id = None
                send_message(chat_id, "✅ Switched to the *most recent conversation* session.")
            else:
                session.continue_session = False
                session.conversation_id = target_conv
                send_message(chat_id, f"✅ Switched active conversation ID to:\n`{target_conv}`\nSubsequent prompts will run in this context.")
            return

        if not os.path.exists(brain_dir):
            send_message(chat_id, "ℹ️ No past conversations found in local store.")
            return

        try:
            entries = []
            for item in os.listdir(brain_dir):
                full_p = os.path.join(brain_dir, item)
                if os.path.isdir(full_p) and len(item) > 20:
                    mtime = os.path.getmtime(full_p)
                    entries.append((mtime, item))

            entries.sort(reverse=True)
            if not entries:
                send_message(chat_id, "ℹ️ No conversation sessions found.")
                return

            msg_lines = ["📂 *Past Conversations (Recent)*:\n"]
            for idx, (mtime, cid) in enumerate(entries[:8], 1):
                dt_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                msg_lines.append(f"`{idx}.` `{cid}`\n    📅 _{dt_str}_ | `/resume {cid}`")

            msg_lines.append("\n💡 *To resume*: send `/resume <conversation_id>` or `/resume last`.")
            send_message(chat_id, "\n".join(msg_lines))
        except Exception as e:
            send_message(chat_id, f"❌ Failed to list conversations: {e}")

    # -------------------------------------------------------------
    # /config (alias: /settings)
    # -------------------------------------------------------------
    elif cmd in ["/config", "/settings"]:
        if not arg:
            cfg_msg = (
                "⚙️ *Antigravity Settings Panel*\n\n"
                f"• `model` : `{session.model}`\n"
                f"• `effort` : `{session.effort}` (low | medium | high)\n"
                f"• `cwd` : `{session.cwd}`\n"
                f"• `agy_bin` : `{AGY_BIN}`\n"
                f"• `session_mode` : `{'Continued' if session.continue_session else ('Custom ' + str(session.conversation_id) if session.conversation_id else 'Fresh')}`\n\n"
                "*How to modify settings:*\n"
                "• Click `/model` to change model or effort level.\n"
                "• `/config effort <low|medium|high>`\n"
                "• `/config cwd <path>`\n"
                "• `/config reset`"
            )
            cfg_kb = {
                "inline_keyboard": [
                    [{"text": "🧠 Switch Model", "callback_data": "model_refresh"}],
                    [
                        {"text": "⚡ High", "callback_data": "eff:high"},
                        {"text": "⚖️ Med", "callback_data": "eff:medium"},
                        {"text": "🌱 Low", "callback_data": "eff:low"}
                    ]
                ]
            }
            send_message(chat_id, cfg_msg, reply_markup=cfg_kb)
            return

        cfg_parts = arg.split(maxsplit=1)
        sub = cfg_parts[0].lower()
        val = cfg_parts[1].strip() if len(cfg_parts) > 1 else ""

        if sub == "effort":
            if val in ["low", "medium", "high"]:
                session.effort = val
                send_message(chat_id, f"✅ Reasoning effort updated to: `{val}`")
            else:
                send_message(chat_id, "⚠️ Invalid effort. Choose from: `low`, `medium`, `high`.")
        elif sub == "model":
            if val:
                session.model = val
                send_message(chat_id, f"✅ Active model updated to: `{val}`")
            else:
                send_message(chat_id, "⚠️ Please specify a model name. Use `/model` to select.")
        elif sub == "cwd":
            if val and os.path.isdir(os.path.expanduser(val)):
                session.cwd = os.path.abspath(os.path.expanduser(val))
                send_message(chat_id, f"✅ Default workspace directory updated to: `{session.cwd}`")
            else:
                send_message(chat_id, f"❌ Invalid directory: `{val}`")
        elif sub == "reset":
            session.model = DEFAULT_MODEL
            session.effort = DEFAULT_EFFORT
            session.cwd = DEFAULT_WORKDIR
            session.continue_session = False
            session.conversation_id = None
            send_message(chat_id, "🔄 Settings reset to defaults.")
        else:
            send_message(chat_id, f"❓ Unknown config option: `{sub}`. Type `/config` to see options.")

    # -------------------------------------------------------------
    # /mcp
    # -------------------------------------------------------------
    elif cmd == "/mcp":
        subcmd = arg.strip() if arg else "list"
        send_chat_action(chat_id, "typing")
        try:
            mcp_args = [AGY_BIN, "mcp"] + subcmd.split()
            res = subprocess.run(mcp_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
            out = res.stdout.strip() if res.stdout else res.stderr.strip()
            if not out:
                out = "No MCP servers configured."
            
            mcp_msg = (
                "🔌 *MCP (Model Context Protocol) Management*\n\n"
                f"```text\n{out}\n```\n"
                "*Commands:*\n"
                "• `/mcp list` : List configured MCP servers\n"
                "• `/mcp add <name> <cmd...>` : Add a new MCP server\n"
                "• `/mcp remove <name>` : Remove MCP server\n"
                "• `/mcp enable <name>` / `/mcp disable <name>` : Enable or disable"
            )
            send_message(chat_id, mcp_msg)
        except Exception as e:
            send_message(chat_id, f"❌ Failed to manage MCP: {e}")

    # -------------------------------------------------------------
    # /schedule
    # -------------------------------------------------------------
    elif cmd == "/schedule":
        if not arg or arg.lower() in ["list", "status"]:
            with session.lock:
                tasks = list(session.scheduled_tasks.values())
            if not tasks:
                send_message(
                    chat_id,
                    "⏰ *No Scheduled Tasks Active*\n\n"
                    "*How to schedule a task:*\n"
                    "• `/schedule in 10m Check git repository status`\n"
                    "• `/schedule in 1h Run unit tests and report failures`\n"
                    "• `/schedule every 30m Check server disk space`\n"
                    "• `/schedule cancel <id>` : Cancel a scheduled task"
                )
                return

            msg_lines = ["⏰ *Active Scheduled Tasks*:\n"]
            for t in tasks:
                remaining = int(t.trigger_time - time.time())
                rem_str = f"in {remaining}s" if remaining > 0 else "Triggering..."
                freq = f" (every {int(t.interval_sec)}s)" if t.interval_sec else " (one-time)"
                msg_lines.append(f"• ID: `{t.task_id}` [{rem_str}{freq}]\n  Prompt: `{t.prompt}`")
            msg_lines.append("\nUse `/schedule cancel <id>` to remove a task.")
            send_message(chat_id, "\n".join(msg_lines))
            return

        if arg.startswith("cancel"):
            cancel_parts = arg.split(maxsplit=1)
            if len(cancel_parts) > 1:
                target_id = cancel_parts[1].strip()
                with session.lock:
                    if target_id in session.scheduled_tasks:
                        session.scheduled_tasks[target_id].is_active = False
                        del session.scheduled_tasks[target_id]
                        send_message(chat_id, f"✅ Scheduled task `{target_id}` cancelled.")
                    else:
                        send_message(chat_id, f"❌ Task ID `{target_id}` not found.")
            else:
                send_message(chat_id, "⚠️ Specify Task ID: `/schedule cancel <id>`")
            return

        match_in = re.match(r'^in\s+(\d+)\s*([smhd])\s+(.+)$', arg, re.IGNORECASE)
        match_every = re.match(r'^every\s+(\d+)\s*([smhd])\s+(.+)$', arg, re.IGNORECASE)

        multiplier = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
        
        if match_in:
            val, unit, prompt_text = match_in.groups()
            duration_sec = int(val) * multiplier[unit.lower()]
            task_id = f"timer_{int(time.time()) % 10000}"
            trigger_t = time.time() + duration_sec
            
            task = ScheduledTask(task_id, chat_id, prompt_text, trigger_t, interval_sec=None)
            with session.lock:
                session.scheduled_tasks[task_id] = task

            send_message(chat_id, f"⏰ *One-time timer scheduled* [ID: `{task_id}`]\nWill execute in {val}{unit}:\n`{prompt_text}`")
        elif match_every:
            val, unit, prompt_text = match_every.groups()
            interval_sec = int(val) * multiplier[unit.lower()]
            task_id = f"cron_{int(time.time()) % 10000}"
            trigger_t = time.time() + interval_sec
            
            task = ScheduledTask(task_id, chat_id, prompt_text, trigger_t, interval_sec=interval_sec)
            with session.lock:
                session.scheduled_tasks[task_id] = task

            send_message(chat_id, f"⏰ *Recurring task scheduled* [ID: `{task_id}`]\nWill run every {val}{unit}:\n`{prompt_text}`")
        else:
            send_message(
                chat_id,
                "⚠️ *Invalid schedule syntax.*\n\n"
                "*Supported Formats:*\n"
                "• `/schedule in 10m <prompt>` (one-time timer)\n"
                "• `/schedule every 30m <prompt>` (recurring schedule)\n"
                "• `/schedule list` (list active tasks)\n"
                "• `/schedule cancel <id>`"
            )

    # -------------------------------------------------------------
    # /btw (Side question)
    # -------------------------------------------------------------
    elif cmd == "/btw":
        if not arg:
            send_message(chat_id, "⚠️ Usage: `/btw <side_question>`\nExample: `/btw what is the difference between TCP and UDP?`")
            return
        
        worker_thread = threading.Thread(target=run_agy_worker, args=(session, arg, True), daemon=True)
        worker_thread.start()

    # -------------------------------------------------------------
    # /changelog
    # -------------------------------------------------------------
    elif cmd == "/changelog":
        send_chat_action(chat_id, "typing")
        try:
            res = subprocess.run([AGY_BIN, "changelog"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
            out = res.stdout.strip()
            lines = out.split("\n")
            top_changelog = "\n".join(lines[:60])
            send_message(chat_id, f"📋 *Antigravity Changelog*\n\n```markdown\n{top_changelog}\n```", parse_mode="Markdown")
        except Exception as e:
            send_message(chat_id, f"❌ Failed to fetch changelog: {e}")

    # -------------------------------------------------------------
    # /agents
    # -------------------------------------------------------------
    elif cmd in ["/agents", "/agent"]:
        send_chat_action(chat_id, "typing")
        try:
            res = subprocess.run([AGY_BIN, "agents"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            out = res.stdout.strip() if res.stdout else "No custom agents configured."
            
            agents_msg = (
                "🤖 *Available Specialized Agents*\n\n"
                "• *Built-in Agents*:\n"
                "  - `self`: Inherits full agent capabilities and tools.\n"
                "  - `research`: Read-only research agent for codebase exploration.\n\n"
                "• *Custom Agents Registry*:\n"
                f"```text\n{out}\n```\n"
                "_Tip: Specialized agents can be invoked autonomously during complex tasks._"
            )
            send_message(chat_id, agents_msg)
        except Exception as e:
            send_message(chat_id, f"❌ Failed to list agents: {e}")

    # -------------------------------------------------------------
    # /new
    # -------------------------------------------------------------
    elif cmd == "/new":
        session.continue_session = False
        session.conversation_id = None
        send_message(chat_id, "🔄 *Conversation context reset.* Next prompt will begin a fresh session.")

    # -------------------------------------------------------------
    # /pwd
    # -------------------------------------------------------------
    elif cmd == "/pwd":
        send_message(chat_id, f"📁 *Current Working Directory:*\n`{session.cwd}`")

    # -------------------------------------------------------------
    # /cd
    # -------------------------------------------------------------
    elif cmd == "/cd":
        if not arg:
            send_message(chat_id, "⚠️ Usage: `/cd <path>`\nExample: `/cd /home/ghenom/my-project`")
            return
        target_path = os.path.expanduser(arg)
        if not os.path.isabs(target_path):
            target_path = os.path.abspath(os.path.join(session.cwd, target_path))

        if os.path.isdir(target_path):
            session.cwd = target_path
            send_message(chat_id, f"✅ Workspace directory updated to:\n`{session.cwd}`")
        else:
            send_message(chat_id, f"❌ Directory not found: `{target_path}`")

    # -------------------------------------------------------------
    # /ls
    # -------------------------------------------------------------
    elif cmd == "/ls":
        target_dir = os.path.expanduser(arg) if arg else session.cwd
        if not os.path.isabs(target_dir):
            target_dir = os.path.abspath(os.path.join(session.cwd, target_dir))

        if not os.path.isdir(target_dir):
            send_message(chat_id, f"❌ Directory not found: `{target_dir}`")
            return

        try:
            items = os.listdir(target_dir)
            items.sort()
            out = [f"📂 *Files in:* `{target_dir}`\n"]
            for item in items[:60]:
                full_p = os.path.join(target_dir, item)
                icon = "📁" if os.path.isdir(full_p) else "📄"
                out.append(f"{icon} `{item}`")
            if len(items) > 60:
                out.append(f"\n_... and {len(items)-60} more items._")
            send_message(chat_id, "\n".join(out))
        except Exception as e:
            send_message(chat_id, f"❌ Failed to read directory: {e}")

    # -------------------------------------------------------------
    # /getfile
    # -------------------------------------------------------------
    elif cmd == "/getfile":
        if not arg:
            send_message(chat_id, "⚠️ Usage: `/getfile <path_to_file>`\nExample: `/getfile main.py`")
            return
        file_path = os.path.expanduser(arg)
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(os.path.join(session.cwd, file_path))

        if not os.path.isfile(file_path):
            send_message(chat_id, f"❌ File not found: `{file_path}`")
            return

        send_message(chat_id, f"📤 Uploading `{os.path.basename(file_path)}`...")
        send_document(chat_id, file_path, caption=f"File: {os.path.basename(file_path)}")

    # -------------------------------------------------------------
    # /status
    # -------------------------------------------------------------
    elif cmd == "/status":
        try:
            load1, load5, load15 = os.getloadavg()
            total_disk, used_disk, free_disk = shutil.disk_usage(session.cwd)
            free_gb = round(free_disk / (1024**3), 2)
            total_gb = round(total_disk / (1024**3), 2)
            
            task_status = f"🔄 Running: `{session.active_task_name}`" if session.active_process else "🟢 Idle (Ready)"
            active_sched = len([t for t in session.scheduled_tasks.values() if t.is_active])

            msg = (
                "📊 *Antigravity System & Host Status*\n\n"
                f"• *Agent State*: {task_status}\n"
                f"• *Workspace CWD*: `{session.cwd}`\n"
                f"• *Model / Effort*: `{session.model}` (`{session.effort}`)\n"
                f"• *Session Context*: `{'Continued' if session.continue_session else ('Resume ' + str(session.conversation_id) if session.conversation_id else 'New')}`\n"
                f"• *Active Schedules*: `{active_sched}`\n"
                f"• *CPU Load Avg*: `{load1:.2f}, {load5:.2f}, {load15:.2f}`\n"
                f"• *Free Disk Space*: `{free_gb} GB / {total_gb} GB`\n"
                f"• *Binary Path*: `{AGY_BIN}`\n"
            )
            send_message(chat_id, msg)
        except Exception as e:
            send_message(chat_id, f"❌ Failed to fetch system status: {e}")

    # -------------------------------------------------------------
    # /cancel
    # -------------------------------------------------------------
    elif cmd == "/cancel":
        with session.lock:
            if session.active_process and session.active_process.poll() is None:
                session.active_process.terminate()
                send_message(chat_id, "🛑 *Cancellation signal sent.* Terminating active Antigravity task...")
            else:
                send_message(chat_id, "ℹ️ No active task is currently running.")

    # -------------------------------------------------------------
    # /exec
    # -------------------------------------------------------------
    elif cmd == "/exec":
        if not arg:
            send_message(chat_id, "⚠️ Usage: `/exec <bash_command>`\nExample: `/exec git status`")
            return
        send_message(chat_id, f"💻 Executing: `{arg}`...")
        try:
            res = subprocess.run(
                arg,
                shell=True,
                cwd=session.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60
            )
            out = res.stdout.strip() if res.stdout else "(no output)"
            send_message(chat_id, f"```bash\n{out}\n```", parse_mode="Markdown")
        except subprocess.TimeoutExpired:
            send_message(chat_id, "❌ Execution timed out (>60s).")
        except Exception as e:
            send_message(chat_id, f"❌ Execution failed: {e}")

    # -------------------------------------------------------------
    # Default Prompt Handler
    # -------------------------------------------------------------
    elif cmd == "/p" or not cmd.startswith("/"):
        prompt = arg if cmd == "/p" else text
        if session.active_process and session.active_process.poll() is None:
            send_message(
                chat_id,
                f"⚠️ *Antigravity is already running a task:*\n`{session.active_task_name}`\n\nUse `/cancel` to stop it or `/btw <question>` for a side question."
            )
            return

        worker_thread = threading.Thread(
            target=run_agy_worker,
            args=(session, prompt, False, session.conversation_id),
            daemon=True
        )
        worker_thread.start()

    else:
        send_message(chat_id, f"❓ Unknown command: `{cmd}`. Type `/help` for all available commands.")

# ==========================================
# Main Long Polling Loop
# ==========================================
def main():
    print("=" * 55)
    print(" Antigravity Telegram Bridge Daemon (Interactive)")
    print("=" * 55)

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("\n[!] WARNING: TELEGRAM_BOT_TOKEN is not configured!")
        print(f"[!] Please configure: {ENV_FILE}\n")
        sys.exit(1)

    me = api_request("getMe")
    if not me or not me.get("ok"):
        print(f"[ERROR] Invalid bot token or cannot connect to Telegram API.")
        sys.exit(1)

    bot_user = me["result"]
    print(f"[+] Bot online: @{bot_user.get('username')} ({bot_user.get('first_name')})")
    print(f"[+] Allowed User IDs: {ALLOWED_USER_IDS if ALLOWED_USER_IDS else 'ALL (Warning: Open!)'}")
    print(f"[+] Default CWD: {DEFAULT_WORKDIR}")
    print(f"[+] Default Model: {DEFAULT_MODEL}")
    print(f"[+] Default Effort: {DEFAULT_EFFORT}")
    print("[+] Polling updates started... (Press Ctrl+C to exit)\n")

    offset = 0
    while True:
        try:
            updates_res = api_request(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"]
                },
                timeout=35
            )
            if not updates_res or not updates_res.get("ok"):
                time.sleep(3)
                continue

            updates = updates_res.get("result", [])
            for update in updates:
                update_id = update.get("update_id", 0)
                offset = max(offset, update_id + 1)

                # 1. Handle Callback Queries (Button Clicks)
                if "callback_query" in update:
                    handle_callback_query(update["callback_query"])
                    continue

                # 2. Handle Regular Messages
                message = update.get("message")
                if not message:
                    continue

                user = message.get("from", {})
                user_id = user.get("id")
                chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "").strip()

                if not text or not chat_id:
                    continue

                # Security Whitelist Check
                if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
                    print(f"[ACCESS DENIED] User ID {user_id} (@{user.get('username')}) attempted access.")
                    send_message(
                        chat_id,
                        f"⛔ *Access Denied!*\n\nYour Telegram User ID: `{user_id}` is not whitelisted on this bot.\n"
                        f"Please add `{user_id}` to `ALLOWED_USER_IDS` in `.env` on your host."
                    )
                    continue

                print(f"[MSG from {user.get('first_name')} ({user_id})]: {text[:80]}")
                session = get_session(chat_id)
                handle_command(session, text)

        except KeyboardInterrupt:
            print("\n[!] Bot daemon stopped by user.")
            break
        except Exception as e:
            print(f"[Polling Loop Exception]: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
