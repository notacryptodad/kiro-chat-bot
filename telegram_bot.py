"""telegram_bot.py — Telegram chatbot interface for Kiro CLI via ACP."""

import logging
import os

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

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = os.environ.get("ALLOWED_USER_IDS", "")  # comma-separated

bridge = KiroBridge()
heartbeat = Heartbeat(bridge)


def _is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return str(user_id) in {u.strip() for u in ALLOWED_USERS.split(",") if u.strip()}


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Kiro CLI Bot v1.0.0 ready!\n"
        "Send me any coding task and I'll execute it via Kiro.\n\n"
        "Commands:\n"
        "/start — this message\n"
        "/reset — start a fresh Kiro session\n"
        "/list — show your sessions\n"
        "/resume <n> — resume session by number"
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


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not _is_allowed(user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return

    user_key = str(user.id)
    text = update.message.text
    thinking_msg = await update.message.reply_text("⏳ Working on it...")

    try:
        result = bridge.prompt(text, user_key=user_key)

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
            if i == 0:
                await thinking_msg.edit_text(response[:4096])
            else:
                await update.message.reply_text(response[i : i + 4096])

    except TimeoutError:
        await thinking_msg.edit_text("⏰ Task timed out. Try a smaller request.")
    except Exception as e:
        log.exception("Error processing message from %s", user.id)
        await thinking_msg.edit_text(f"❌ Error: {e}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("🚀 Telegram bot starting...")
    heartbeat.start()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
