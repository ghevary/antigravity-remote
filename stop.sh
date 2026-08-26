#!/usr/bin/env bash
# Stop Antigravity Telegram Bridge

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$DIR/bot.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "🛑 Menghentikan bot (PID: $PID)..."
        kill "$PID"
        rm -f "$PID_FILE"
        echo "✅ Bot berhasil dihentikan."
        exit 0
    fi
fi

# Fallback check
PIDS=$(pgrep -f "python3.*antigravity-telegram-bridge/bot.py")
if [ -n "$PIDS" ]; then
    echo "🛑 Menghentikan proses bot: $PIDS..."
    kill $PIDS
    echo "✅ Bot berhasil dihentikan."
else
    echo "ℹ️ Bot tidak sedang berjalan."
fi
