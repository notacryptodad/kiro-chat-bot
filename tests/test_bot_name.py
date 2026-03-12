"""Tests for bot name substitution in _load_soul()."""
import os
import pytest

SOUL_TEMPLATE = """\
Your name is **{{BOT_NAME}}**.

{{#if_unnamed}}
You don't have a name. Ask the user what to call you, then write it to bot_name.txt.
{{/if_unnamed}}

## Principles
- Be concise
"""


@pytest.fixture(autouse=True)
def patch_soul(tmp_path, monkeypatch):
    import kiro_bridge

    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text(SOUL_TEMPLATE)
    monkeypatch.setattr(kiro_bridge, "SOUL_PATH", str(soul_file))
    monkeypatch.setattr(kiro_bridge, "WORKING_DIR", str(tmp_path))
    return tmp_path


def test_unnamed_substitutes_placeholder(patch_soul):
    import kiro_bridge
    soul = kiro_bridge._load_soul()
    assert "[unnamed]" in soul
    assert "{{BOT_NAME}}" not in soul


def test_unnamed_includes_ask_instruction(patch_soul):
    import kiro_bridge
    soul = kiro_bridge._load_soul()
    assert "bot_name.txt" in soul


def test_named_substitutes_name(patch_soul):
    import kiro_bridge
    (patch_soul / "bot_name.txt").write_text("Aria")
    soul = kiro_bridge._load_soul()
    assert "**Aria**" in soul
    assert "{{BOT_NAME}}" not in soul


def test_named_removes_ask_instruction(patch_soul):
    import kiro_bridge
    (patch_soul / "bot_name.txt").write_text("Aria")
    soul = kiro_bridge._load_soul()
    assert "{{#if_unnamed}}" not in soul
    assert "Ask the user" not in soul


def test_missing_soul_returns_empty(patch_soul, monkeypatch):
    import kiro_bridge
    monkeypatch.setattr(kiro_bridge, "SOUL_PATH", "/nonexistent/SOUL.md")
    assert kiro_bridge._load_soul() == ""
