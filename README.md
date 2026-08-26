<div align="center">

# 🚀 Antigravity Remote

**Control Google Antigravity AI Agent directly from Telegram on any device.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-2CA5E0.svg?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Antigravity CLI](https://img.shields.io/badge/Antigravity-CLI%20Enabled-4285F4.svg?logo=google&logoColor=white)](https://antigravity.google)
[![Zero Dependency](https://img.shields.io/badge/Dependencies-Zero%20(Pure%20Python)-brightgreen.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<br/>

> **Short Description (Tagline)**:
> *A lightweight, zero-dependency Telegram bot bridge to control Google Antigravity CLI (`agy`) remotely. Pair program, switch AI models with 1 click, execute terminal tasks, and schedule background automation directly from Telegram.*

<br/>

<p align="center">
  <img src="assets/demo-telegram.png" alt="Antigravity Telegram Conversation Demo" width="380" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="assets/command-reference.png" alt="Command Reference and Help Screen" width="520" />
</p>

</div>

---

## 🌟 Highlights & Key Features

- 📦 **Zero External Dependencies**: Built entirely with pure Python 3 standard libraries (`urllib`, `json`, `subprocess`, `asyncio`). Runs instantly without `pip install` issues.
- 🧠 **1-Click Model & Effort Picker**: Switch between **Gemini 3.7 Flash**, **Gemini 3.1 Pro**, **Claude Sonnet 4.6 (Thinking)**, **Claude Opus**, and **GPT-OSS** using interactive Telegram inline buttons.
- 🔒 **Ironclad Security Whitelist**: Restricts bot access strictly to your whitelisted Telegram User IDs. Unauthorized users are immediately blocked.
- 💡 **Side Questions (`/btw`)**: Ask parallel or quick side questions without interrupting or polluting your active task session context.
- ⏰ **Autonomous Task Scheduling (`/schedule`)**: Run one-time timers (`/schedule in 10m ...`) or recurring cron schedules (`/schedule every 30m ...`) that execute and report back on Telegram.
- 📁 **Remote Workspace Control**: Browse files (`/ls`), switch project folders (`/cd`), inspect paths (`/pwd`), and download generated artifacts directly to your chat (`/getfile`).
- 💻 **Direct Host Shell Access**: Execute quick bash commands on your host server (`/exec git status`, `/exec docker ps`).
- 🔄 **Conversation Persistence (`/resume`)**: Browse, switch, or resume past agent conversation sessions seamlessly.
- 🛡️ **24/7 Systemd Daemon Support**: Ready-to-use background service file for automated auto-start on boot.

---

## 📋 Full Command Reference

| Command | Aliases | Description |
| :--- | :--- | :--- |
| **Direct Message** | `/p <prompt>` | Send coding or development task to Antigravity Agent |
| `/btw <question>` | | Ask a quick side question without altering the main session |
| `/model [id]` | | **Interactive 1-Click model & reasoning effort picker** |
| `/usage` | `/quota` | View model quota usage and rate limit status |
| `/credits` | | Show remaining G1 credits & purchase information |
| `/resume [id]` | `/switch`, `/conversation` | Browse & resume past conversations (e.g. `/resume last`) |
| `/new` | | Reset conversation context for a fresh session |
| `/config` | `/settings` | Open/modify settings panel (effort, model, cwd) |
| `/mcp [subcmd]` | | Manage MCP servers (`list`, `add`, `remove`, `enable`, `disable`) |
| `/schedule` | | Run instructions on a timer (`in 10m`) or recurring schedule (`every 30m`) |
| `/changelog` | | View recent Antigravity release notes and changes |
| `/agents` | `/agent` | List available custom and built-in agents |
| `/status` | | View host CPU, RAM, Disk, active model, and task status |
| `/cd <path>` | | Change workspace working directory |
| `/pwd` | | Print current working directory |
| `/ls [path]` | | List folder contents |
| `/getfile <file>`| | Download and send a file from host to Telegram |
| `/exec <cmd>` | | Run a quick terminal bash command |
| `/cancel` | | Cancel active task execution |
| `/help` | `/start` | Display the interactive command menu |

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Linux / macOS with **Python 3.10+**
- Google Antigravity CLI (`agy`) installed and authenticated on the host

### 2. Clone the Repository
```bash
git clone https://github.com/ghevary/antigravity-remote.git
cd antigravity-remote
```

### 3. Create Telegram Bot & Configure
1. Open Telegram and message [@BotFather](https://t.me/BotFather) to create a new bot (`/newbot`). Copy your **HTTP API Token**.
2. Message [@userinfobot](https://t.me/userinfobot) to get your numerical **Telegram User ID**.
3. Create your `.env` configuration file:
```bash
cp .env.example .env
nano .env
```
4. Fill in your credentials:
```env
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHI..."
ALLOWED_USER_IDS="YOUR_TELEGRAM_USER_ID"
DEFAULT_WORKING_DIR="/home/yourusername"
AGY_BIN_PATH="/home/yourusername/.local/bin/agy"
AGY_EFFORT="high"
```

---

## 🏃 Running the Bot

### Option A: Foreground (Testing)
```bash
./start.sh
```

### Option B: Background Daemon
```bash
./start.sh --bg
```
To stop the background daemon:
```bash
./stop.sh
```

### Option C: 24/7 Systemd Service (Auto-start on Boot)
```bash
mkdir -p ~/.config/systemd/user
cp antigravity-telegram.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now antigravity-telegram
```

Check service status and logs:
```bash
systemctl --user status antigravity-telegram
journalctl --user -u antigravity-telegram -f
```

---

## 🔒 Security Recommendations

- ⚠️ **Never commit your `.env` file!** It contains your secret bot token.
- 🛡️ **Always whitelist your `ALLOWED_USER_IDS`**: Since Antigravity can execute terminal commands and modify files, ensure only your own Telegram account has access.
- 🔐 For multi-user setups, separate IDs by commas in `ALLOWED_USER_IDS="12345678,87654321"`.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
