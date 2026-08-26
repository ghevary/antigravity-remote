#!/usr/bin/env bash
# Start Antigravity Telegram Bridge in Background or Foreground

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$DIR/bot.pid"
LOG_FILE="$DIR/bot.log"

if [ "$1" == "--bg" ] || [ "$1" == "-d" ]; then
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "⚠️ Bot sudah berjalan (PID: $(cat "$PID_FILE"))."
        exit 0
    fi
    echo "🚀 Menjalankan bot di background..."
    PYTHONUNBUFFERED=1 nohup python3 -u "$DIR/bot.py" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "✅ Bot berjalan di background (PID: $(cat "$PID_FILE"))."
    echo "📄 Log tersimpan di: $LOG_FILE"
    echo "Gunakan ./stop.sh untuk menghentikan."
else
    echo "🚀 Menjalankan bot di foreground..."
    python3 "$DIR/bot.py"
fi
