#!/usr/bin/env bash
# curl -fsSL https://raw.githubusercontent.com/notacryptodad/kiro-chat-bot/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/notacryptodad/kiro-chat-bot.git"
INSTALL_DIR="$HOME/.kiro-chat-bot"
SERVICE_NAME="kiro-chat-bot"
VERSION="1.0.0"

red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

header() {
    echo ""
    bold "╔══════════════════════════════════════╗"
    bold "║   Kiro CLI Telegram Bot v${VERSION}    ║"
    bold "╚══════════════════════════════════════╝"
    echo ""
}

fail() { red "✗ $*"; exit 1; }

# ── Header ────────────────────────────────────────────────
header

# ── 1. Prerequisites ─────────────────────────────────────
bold "① Checking prerequisites..."

command -v git &>/dev/null || fail "git not found. Install git first."
green "  ✓ git"

if ! command -v uv &>/dev/null; then
    yellow "  ⚠ uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv install failed"
fi
green "  ✓ uv $(uv --version)"

KIRO_PATH=""
for p in kiro-cli "$HOME/.local/bin/kiro-cli" "$HOME/.kiro/bin/kiro-cli"; do
    if command -v "$p" &>/dev/null 2>&1 || [ -x "$p" ]; then
        KIRO_PATH="$p"
        break
    fi
done
if [ -z "$KIRO_PATH" ]; then
    yellow "  ⚠ kiro-cli not found — installing..."
    curl -fsSL https://kiro.dev/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.kiro/bin:$PATH"
    for p in kiro-cli "$HOME/.local/bin/kiro-cli" "$HOME/.kiro/bin/kiro-cli"; do
        if command -v "$p" &>/dev/null 2>&1 || [ -x "$p" ]; then
            KIRO_PATH="$p"
            break
        fi
    done
    [ -n "$KIRO_PATH" ] || fail "kiro-cli install failed"
fi
green "  ✓ kiro-cli ($KIRO_PATH)"

# Check kiro-cli auth
if ! "$KIRO_PATH" whoami &>/dev/null; then
    yellow "  ⚠ kiro-cli not logged in — launching login..."
    "$KIRO_PATH" login || fail "kiro-cli login failed"
fi
green "  ✓ kiro-cli authenticated"

echo ""

# ── 2. Clone / Update ────────────────────────────────────
bold "② Installing to $INSTALL_DIR..."

if [ -d "$INSTALL_DIR/.git" ]; then
    (cd "$INSTALL_DIR" && git pull --quiet)
    green "  ✓ Updated existing install"
else
    rm -rf "$INSTALL_DIR"
    git clone --quiet "$REPO" "$INSTALL_DIR"
    green "  ✓ Cloned"
fi

echo ""

# ── 3. Configuration ─────────────────────────────────────
bold "③ Configuration"

ENV_FILE="$INSTALL_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    yellow "  .env already exists — keeping it"
    set -a; source "$ENV_FILE"; set +a
else
    echo ""
    echo "  Get a bot token from https://t.me/BotFather"
    echo ""
    read -rp "  Telegram Bot Token: " TG_TOKEN
    [ -n "$TG_TOKEN" ] || fail "Token cannot be empty"

    read -rp "  Allowed user IDs (comma-separated, blank=all): " ALLOWED_IDS
    read -rp "  Working directory for Kiro [$HOME/projects]: " WORK_DIR
    WORK_DIR="${WORK_DIR:-$HOME/projects}"
    mkdir -p "$WORK_DIR"

    read -rp "  Bot name [Kiro]: " BOT_DISPLAY_NAME
    BOT_DISPLAY_NAME="${BOT_DISPLAY_NAME:-Kiro}"
    read -rp "  Your name: " OWNER_NAME
    OWNER_NAME="${OWNER_NAME:-Boss}"

    cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
ALLOWED_USER_IDS=${ALLOWED_IDS}
KIRO_CLI_PATH=${KIRO_PATH}
KIRO_WORKING_DIR=${WORK_DIR}
HEARTBEAT_INTERVAL=900
EOF
    green "  ✓ .env written"

    # Write personalized SOUL.md
    cat > "$INSTALL_DIR/SOUL.md" <<EOF
# SOUL — System Identity

Your name is **${BOT_DISPLAY_NAME}**. You are a friendly coding assistant operated via Telegram.
Your owner is **${OWNER_NAME}**. Address them by name.

## Principles

- Be concise and direct
- Write clean, production-ready code
- Explain what you did after completing a task
- If a task is ambiguous, state your assumptions before proceeding
- Prefer minimal changes over large rewrites
EOF
    green "  ✓ SOUL.md personalized (bot: ${BOT_DISPLAY_NAME}, owner: ${OWNER_NAME})"

    TELEGRAM_BOT_TOKEN="$TG_TOKEN"
fi

echo ""

# ── 4. Dependencies ──────────────────────────────────────
bold "④ Installing dependencies..."
(cd "$INSTALL_DIR" && uv sync --quiet)
green "  ✓ Dependencies ready"

echo ""

# ── 5. Test Telegram ──────────────────────────────────────
bold "⑤ Testing Telegram connection..."

TG_RESP=$(curl -sf "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" 2>&1) || \
    fail "Cannot reach Telegram API. Check token and network."

if echo "$TG_RESP" | grep -q '"ok":true'; then
    BOT_NAME=$(echo "$TG_RESP" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)
    green "  ✓ Connected as @${BOT_NAME}"
else
    fail "Telegram API error: $TG_RESP"
fi

echo ""

# ── 6. systemd service ───────────────────────────────────
bold "⑥ Setting up systemd service..."

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/${SERVICE_NAME}.service"
mkdir -p "$SERVICE_DIR"

# Build Environment lines
ENV_LINES=""
while IFS= read -r line; do
    [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
    ENV_LINES+="Environment=\"${line}\"\n"
done < "$ENV_FILE"

UV_PATH="$(command -v uv)"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Kiro CLI Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${UV_PATH} run python telegram_bot.py
Restart=on-failure
RestartSec=10
$(echo -e "$ENV_LINES")
[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME" --quiet
systemctl --user start "$SERVICE_NAME"

# Enable lingering so it runs without login session
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
fi

green "  ✓ Service installed and started"

echo ""
bold "╔══════════════════════════════════════╗"
bold "║     Setup complete! v${VERSION} 🚀     ║"
bold "╚══════════════════════════════════════╝"
echo ""
echo "  Bot:     @${BOT_NAME}"
echo "  Install: $INSTALL_DIR"
echo "  Logs:    journalctl --user -u $SERVICE_NAME -f"
echo ""
echo "  Commands:"
echo "    systemctl --user status  $SERVICE_NAME"
echo "    systemctl --user restart $SERVICE_NAME"
echo "    systemctl --user stop    $SERVICE_NAME"
echo ""
echo "  Edit personality:  $INSTALL_DIR/SOUL.md"
echo "  Trigger heartbeat: echo 'your task' > $INSTALL_DIR/heartbeat.md"
echo ""
