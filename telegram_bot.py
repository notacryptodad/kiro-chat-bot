"""telegram_bot.py — Telegram chatbot interface for Kiro CLI via ACP."""

import logging
import os
import socket
import subprocess
import threading

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from kiro_bridge import KiroBridge
from heartbeat import Heartbeat
from acp_client import KiroAuthError

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("/tmp/kiro-chat-bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = os.environ.get("ALLOWED_USER_IDS", "")  # comma-separated

bridge = KiroBridge()
heartbeat = Heartbeat(bridge)

STATE_FILE = os.path.join(os.path.dirname(__file__), ".bot_state")


async def _keep_typing(chat_id: int, bot):
    """Send typing indicator every 4 seconds until cancelled."""
    import asyncio
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.debug(f"Typing indicator failed: {e}")
            break


def _sd_notify(msg: str):
    """Send notification to systemd via NOTIFY_SOCKET."""
    socket_path = os.environ.get("NOTIFY_SOCKET")
    if not socket_path:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if socket_path.startswith("@"):
            socket_path = "\0" + socket_path[1:]
        sock.sendto(msg.encode(), socket_path)
        sock.close()
    except Exception as e:
        log.debug(f"sd_notify failed: {e}")


def _watchdog_thread():
    """Periodically notify systemd that we're alive."""
    interval = int(os.environ.get("WATCHDOG_USEC", 0)) / 2_000_000  # half of watchdog interval
    if interval <= 0:
        return
    log.info(f"Watchdog enabled: notifying every {interval:.1f}s")
    while True:
        threading.Event().wait(interval)
        _sd_notify("WATCHDOG=1")


def _is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return str(user_id) in {u.strip() for u in ALLOWED_USERS.split(",") if u.strip()}


def _was_offline() -> bool:
    """Check if bot was previously offline."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return f.read().strip() == "offline"
    return False


def _mark_online():
    """Mark bot as online."""
    with open(STATE_FILE, "w") as f:
        f.write("online")


def _mark_offline():
    """Mark bot as offline."""
    with open(STATE_FILE, "w") as f:
        f.write("offline")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Kiro CLI Bot v1.0.0 ready!\n"
        "Send me any coding task and I'll execute it via Kiro.\n\n"
        "Commands:\n"
        "/start — this message\n"
        "/reset — start a fresh Kiro session\n"
        "/list — show your sessions\n"
        "/resume <n> — resume session by number\n"
        "/model — show available models\n"
        "/upgrade — pull latest code and restart"
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_key = str(update.effective_user.id)
    with bridge._sessions_lock:
        bridge._sessions.pop(user_key, None)
    await update.message.reply_text("🔄 Session reset. Next message starts fresh.")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_key = str(update.effective_user.id)
    sessions = bridge.list_sessions(user_key)
    if not sessions:
        await update.message.reply_text("No sessions yet. Send a message to start one.")
        return
    lines = []
    for i, s in enumerate(sessions, 1):
        marker = " ✅" if s["active"] else ""
        lines.append(
            f"{i}. `{s['session_id'][:12]}…`{marker}\n"
            f"   Context: {s['context_pct']:.0f}% | Credits: {s['credits']:.1f}"
        )
    await update.message.reply_text(
        "📋 Your sessions:\n\n" + "\n".join(lines)
        + "\n\nUse /resume <number> to switch.",
        parse_mode="Markdown",
    )


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_key = str(update.effective_user.id)
    args = ctx.args
    sessions = bridge.list_sessions(user_key)
    if not sessions:
        await update.message.reply_text("No sessions to resume.")
        return
    if not args:
        await update.message.reply_text("Usage: /resume <number> (see /list)")
        return
    try:
        idx = int(args[0]) - 1
        session_id = sessions[idx]["session_id"]
    except (ValueError, IndexError):
        await update.message.reply_text(f"Invalid. Pick 1–{len(sessions)} from /list")
        return
    if bridge.resume_session(user_key, session_id):
        await update.message.reply_text(
            f"▶️ Resumed session `{session_id[:12]}…`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ Failed to resume session.")


async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return
    bot_dir = os.path.dirname(os.path.abspath(__file__))
    msg = await update.message.reply_text("⬆️ Pulling latest code...")
    try:
        r = subprocess.run(
            ["git", "pull"], cwd=bot_dir,
            capture_output=True, text=True, timeout=30,
        )
        output = r.stdout.strip() or r.stderr.strip()
        await msg.edit_text(f"⬆️ git pull:\n{output}\n\n🔄 Restarting...")
        subprocess.Popen(["systemctl", "--user", "restart", "kiro-chat-bot"])
    except Exception as e:
        await msg.edit_text(f"❌ Upgrade failed: {e}")


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    models = bridge.list_models()
    if not models:
        await update.message.reply_text("No model info available yet. Send a message first.")
        return
    current = models.get("currentModelId", "unknown")
    available = models.get("availableModels", [])
    lines = []
    for m in available:
        marker = " ✅" if m["modelId"] == current else ""
        lines.append(f"• `{m['modelId']}`{marker}\n  {m['description']}")
    await update.message.reply_text(
        f"🤖 Current model: `{current}`\n\n" + "\n".join(lines)
        + "\n\n⚠️ Model switching via ACP is not yet supported by Kiro CLI.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return

    user_key = str(user.id)
    text = update.message.text
    
    # Start typing indicator loop
    typing_task = ctx.application.create_task(
        _keep_typing(update.message.chat_id, ctx.application.bot)
    )

    try:
        result = bridge.prompt(text, user_key=user_key)
        typing_task.cancel()  # Stop typing indicator

        response_parts = []
        if result["text"]:
            response_parts.append(result["text"])

        if result["tool_calls"]:
            actions = "\n".join(
                f"  • [{tc['status']}] {tc['title']}" for tc in result["tool_calls"]
            )
            response_parts.append(f"\n🔧 Actions:\n{actions}")

        usage = result["usage"]
        response_parts.append(
            f"\n💳 Credits: {usage['kiro_credits']:.1f} | "
            f"Context: {usage['kiro_context_pct']:.0f}%"
        )

        response = "\n".join(response_parts) or "(empty response)"

        # Telegram message limit is 4096 chars
        for i in range(0, len(response), 4096):
            await update.message.reply_text(response[i : i + 4096])

    except KiroAuthError:
        typing_task.cancel()
        await update.message.reply_text(
            "🔐 Kiro CLI authentication required.\n\n"
            "Please run `kiro-cli login` on the host machine "
            "and restart the container to refresh credentials."
        )
    except TimeoutError:
        typing_task.cancel()
        await update.message.reply_text("⏰ Task timed out. Try a smaller request.")
    except Exception as e:
        typing_task.cancel()
        log.exception("Error processing message from %s", user.id)
        await update.message.reply_text(f"❌ Error: {e}")


async def _on_startup(app: Application):
    """Send notification if bot was offline and set bot commands."""
    # Set bot command menu
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Show welcome message"),
        BotCommand("reset", "Start a fresh Kiro session"),
        BotCommand("list", "Show all your sessions"),
        BotCommand("resume", "Resume session by number"),
        BotCommand("model", "Show available models"),
        BotCommand("upgrade", "Pull latest code and restart"),
    ]
    await app.bot.set_my_commands(commands)
    
    if _was_offline() and ALLOWED_USERS:
        for user_id in ALLOWED_USERS.split(","):
            user_id = user_id.strip()
            if user_id:
                try:
                    await app.bot.send_message(
                        chat_id=int(user_id),
                        text="✅ Bot is back online and ready to work!"
                    )
                except Exception as e:
                    log.warning(f"Failed to notify user {user_id}: {e}")
    _mark_online()


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("update", cmd_upgrade))  # alias
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.post_init = _on_startup

    log.info("🚀 Telegram bot starting...")
    
    # Start watchdog thread
    watchdog = threading.Thread(target=_watchdog_thread, daemon=True)
    watchdog.start()
    
    # Notify systemd we're ready
    _sd_notify("READY=1")
    
    heartbeat.start()
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        _sd_notify("STOPPING=1")
        _mark_offline()


if __name__ == "__main__":
    main()
