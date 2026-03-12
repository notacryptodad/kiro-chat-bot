#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🧹 Killing orphaned processes..."
pkill -9 -f "python.*telegram_bot" 2>/dev/null || true
pkill -9 -f "kiro-cli acp" 2>/dev/null || true
sleep 2

echo "🚀 Starting bot..."
cd "$INSTALL_DIR"
export $(grep -v '^#' .env | xargs)
nohup uv run python telegram_bot.py >> /tmp/kiro-chat-bot.log 2>&1 &
echo "PID: $!"

echo ""
echo "📋 Tail logs with:"
echo "  tail -f /tmp/kiro-chat-bot.log"
