# Kiro CLI Telegram Bot

A Telegram chatbot that routes coding tasks to [Kiro CLI](https://kiro.dev) via the ACP (Agent Communication Protocol). Your Telegram messages become coding tasks that Kiro executes — writing files, running tests, installing packages — all billed as Kiro Credits instead of expensive LLM API tokens.

## Architecture

```
Telegram User
    │ message
    ▼
telegram_bot.py      ← Telegram interface, user auth, message chunking
    │
    ▼
kiro_bridge.py       ← Session management, auto context rotation at 80%
    │
    ▼
acp_client.py        ← Pure stdlib JSON-RPC 2.0 over stdio (zero pip deps)
    │ subprocess
    ▼
kiro-cli acp         ← Code generation, file I/O, terminal execution
```

## Prerequisites

1. **Python 3.11+**
2. **Kiro CLI** installed and authenticated:
   ```bash
   curl -fsSL https://kiro.dev/install.sh | sh
   kiro-cli auth login
   ```
3. **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)

## Setup

```bash
curl -fsSL https://raw.githubusercontent.com/user/kiro-chat-bot/main/install.sh | bash
```

The installer will:
1. Install `uv` and `kiro-cli` if missing
2. Clone the repo to `~/.kiro-chat-bot`
3. Prompt for your Telegram bot token and config
4. Test Telegram API connectivity
5. Install as a **systemd user service** (auto-start on boot, auto-restart on failure)

### Manual setup

```bash
git clone <this-repo> && cd kiro-chat-bot
cp .env.example .env        # edit with your TELEGRAM_BOT_TOKEN
./launch.sh                  # preflight checks + optional systemd install
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `ALLOWED_USER_IDS` | — | (all) | Comma-separated Telegram user IDs |
| `KIRO_CLI_PATH` | — | `kiro-cli` | Path to kiro-cli binary |
| `KIRO_WORKING_DIR` | — | current dir | Working directory for Kiro sessions |
| `HEARTBEAT_INTERVAL` | — | `900` | Seconds between heartbeat checks |

## Run

```bash
./launch.sh
```

First launch will:
1. Check `uv`, `.env`, `TELEGRAM_BOT_TOKEN`, Telegram API connectivity, `kiro-cli`
2. Sync dependencies via `uv sync`
3. Offer to install as a **systemd user service** (auto-start on boot, auto-restart on failure)

To run manually without systemd:
```bash
export $(grep -v '^#' .env | xargs)
uv run python telegram_bot.py
```

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message |
| `/reset` | Clear your Kiro session (fresh context) |
| `/list` | Show all your sessions with context % and credits |
| `/resume <n>` | Resume session by number from `/list` |
| *(any text)* | Send as coding task to Kiro |

## How It Works

- Each Telegram user gets an isolated Kiro session
- Sessions auto-rotate when context usage exceeds 80%
- Kiro handles code generation, file writes, and terminal execution
- Responses include tool call summaries and credit usage
- Messages over 4096 chars are automatically chunked

## SOUL.md — System Identity

Edit `SOUL.md` to define the bot's personality and instructions. This file is loaded at startup and prepended to every prompt sent to Kiro. Use it to set coding style, response tone, or project-specific rules.

## Heartbeat — Autonomous Task Execution

The heartbeat loop reads `heartbeat.md` every `HEARTBEAT_INTERVAL` seconds:

- **`sleep`** (or empty) → do nothing
- **Any other content** → execute it as a Kiro task, log the result to `heartbeat_log.md`, reset file to `sleep`

To trigger a task, just write to `heartbeat.md`:
```bash
echo "Run pytest and fix any failures" > heartbeat.md
```

Results are appended to `heartbeat_log.md` with timestamps.

## Files

| File | Deps | Purpose |
|---|---|---|
| `acp_client.py` | stdlib only | JSON-RPC 2.0 client for `kiro-cli acp` |
| `kiro_bridge.py` | acp_client | Session management, lazy start, context rotation |
| `telegram_bot.py` | python-telegram-bot | Telegram interface |
| `heartbeat.py` | kiro_bridge | Periodic task execution from `heartbeat.md` |
| `SOUL.md` | — | System identity prepended to every prompt |
| `heartbeat.md` | — | Task file for heartbeat (write tasks here) |

## Reference

- [Kiro CLI Docs](https://kiro.dev/docs)
- [ACP Protocol Reference](https://kiro.dev/docs/acp)
- [Original article](https://dev.to/aws-builders/integrate-kiro-cli-into-your-ai-agent-via-acp-10jn) by 李小飛
