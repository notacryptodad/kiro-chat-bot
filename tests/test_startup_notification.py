"""Tests for telegram_bot startup notification."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub env vars required at import time
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("KIRO_CLI_PATH", "kiro-cli")
os.environ.setdefault("KIRO_WORKING_DIR", "/tmp")

import telegram_bot  # noqa: E402


def _make_app_mock(user_ids: str):
    app = MagicMock()
    app.bot = MagicMock()
    app.bot.set_my_commands = AsyncMock()
    app.bot.send_message = AsyncMock()
    return app


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state_file = str(tmp_path / "bot_state.txt")
    monkeypatch.setattr(telegram_bot, "STATE_FILE", state_file)
    return tmp_path


@pytest.mark.asyncio
async def test_sends_ready_msg_when_offline():
    with open(telegram_bot.STATE_FILE, "w") as f:
        f.write("offline")

    app = _make_app_mock("111,222")
    with patch.object(telegram_bot, "ALLOWED_USERS", "111,222"):
        await telegram_bot._on_startup(app)

    assert app.bot.send_message.call_count == 2
    calls = {c.kwargs["chat_id"] for c in app.bot.send_message.call_args_list}
    assert calls == {111, 222}
    text = app.bot.send_message.call_args_list[0].kwargs["text"]
    assert "ready" in text.lower() or "online" in text.lower()


@pytest.mark.asyncio
async def test_no_msg_when_already_online():
    import telegram_bot
    with open(telegram_bot.STATE_FILE, "w") as f:
        f.write("online")

    app = _make_app_mock("111")
    with patch.object(telegram_bot, "ALLOWED_USERS", "111"):
        await telegram_bot._on_startup(app)

    app.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_msg_when_no_allowed_users():
    import telegram_bot
    with open(telegram_bot.STATE_FILE, "w") as f:
        f.write("offline")

    app = _make_app_mock("")
    with patch.object(telegram_bot, "ALLOWED_USERS", ""):
        await telegram_bot._on_startup(app)

    app.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_marks_online_after_startup():
    import telegram_bot
    with open(telegram_bot.STATE_FILE, "w") as f:
        f.write("offline")

    app = _make_app_mock("111")
    with patch.object(telegram_bot, "ALLOWED_USERS", "111"):
        await telegram_bot._on_startup(app)

    with open(telegram_bot.STATE_FILE) as f:
        assert f.read().strip() == "online"


@pytest.mark.asyncio
async def test_continues_if_send_fails():
    import telegram_bot
    with open(telegram_bot.STATE_FILE, "w") as f:
        f.write("offline")

    app = _make_app_mock("111,222")
    app.bot.send_message = AsyncMock(side_effect=Exception("network error"))
    with patch.object(telegram_bot, "ALLOWED_USERS", "111,222"):
        # Should not raise
        await telegram_bot._on_startup(app)
