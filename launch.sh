#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
SERVICE_NAME="kiro-chat-bot"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"

# ── Colors ────────────────────────────────────────────────
red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }

# ── Preflight checks ─────────────────────────────────────
echo "🔍 Running preflight checks..."

# 1. uv
if ! command -v uv &>/dev/null; then
    red "✗ uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
green "✓ uv $(uv --version)"

# 2. .env
if [ ! -f "$ENV_FILE" ]; then
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    red "✗ .env created from template — edit it with your TELEGRAM_BOT_TOKEN, then re-run."
    exit 1
fi
set -a; source "$ENV_FILE"; set +a
green "✓ .env loaded"

# 3. TELEGRAM_BOT_TOKEN
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    red "✗ TELEGRAM_BOT_TOKEN is empty in .env"
    exit 1
fi
green "✓ TELEGRAM_BOT_TOKEN is set"

# 4. Telegram API connectivity
echo -n "  Testing Telegram API... "
TG_RESPONSE=$(curl -sf "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" 2>&1) || {
    red "✗ Cannot reach Telegram API. Check your token and network."
    exit 1
}
if echo "$TG_RESPONSE" | grep -q '"ok":true'; then
    BOT_NAME=$(echo "$TG_RESPONSE" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)
    green "✓ Connected as @${BOT_NAME}"
else
    red "✗ Telegram API returned error: $TG_RESPONSE"
    exit 1
fi

# 5. kiro-cli
KIRO_PATH="${KIRO_CLI_PATH:-kiro-cli}"
if ! command -v "$KIRO_PATH" &>/dev/null; then
    red "✗ kiro-cli not found at '$KIRO_PATH'. Install: curl -fsSL https://kiro.dev/install.sh | sh"
    exit 1
fi
green "✓ kiro-cli found: $($KIRO_PATH --version 2>/dev/null || echo "$KIRO_PATH")"

# 6. Sync dependencies
echo -n "  Syncing dependencies... "
(cd "$SCRIPT_DIR" && uv sync --quiet)
green "✓ Dependencies ready"

echo ""
green "All checks passed! ✅"
echo ""

# ── Install systemd service ──────────────────────────────
read -rp "Install as systemd user service (auto-start + auto-restart)? [Y/n] " answer
if [[ "${answer:-Y}" =~ ^[Nn] ]]; then
    echo "Starting directly..."
    cd "$SCRIPT_DIR"
    exec uv run python telegram_bot.py
fi

mkdir -p "$(dirname "$SERVICE_FILE")"

# Build environment lines from .env
ENV_LINES=""
while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
    ENV_LINES+="Environment=\"${line}\"\n"
done < "$ENV_FILE"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Kiro CLI Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=$(command -v uv) run python telegram_bot.py
Restart=on-failure
RestartSec=10
$(echo -e "$ENV_LINES")
[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start "$SERVICE_NAME"

echo ""
green "🚀 Service installed and started!"
echo ""
echo "Useful commands:"
echo "  systemctl --user status  $SERVICE_NAME    # check status"
echo "  journalctl --user -u $SERVICE_NAME -f     # follow logs"
echo "  systemctl --user restart $SERVICE_NAME     # restart"
echo "  systemctl --user stop    $SERVICE_NAME     # stop"
echo "  systemctl --user disable $SERVICE_NAME     # remove from startup"

# Enable lingering so service runs even when not logged in
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
fi
