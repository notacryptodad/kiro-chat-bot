"""Tests for kiro_bridge.py"""
import os
import threading
from unittest.mock import MagicMock, patch

import pytest


def _make_bridge(default_model=""):
    """Create a KiroBridge with mocked ACPClient."""
    with patch.dict(os.environ, {
        "KIRO_CLI_PATH": "kiro-cli",
        "KIRO_WORKING_DIR": "/tmp",
        "KIRO_DEFAULT_MODEL": default_model,
    }):
        with patch("kiro_bridge.ACPClient") as MockACP:
            mock_acp = MagicMock()
            mock_acp.is_running.return_value = True
            mock_acp._models = {
                "currentModelId": "auto",
                "availableModels": [
                    {"modelId": "claude-sonnet-4-20250514", "name": "Claude Sonnet"},
                    {"modelId": "claude-opus-4.6", "name": "Claude Opus"},
                ]
            }
            mock_acp.session_new.return_value = ("new-session-id", {})
            mock_acp.session_load.return_value = {}
            MockACP.return_value = mock_acp

            import importlib
            import kiro_bridge
            importlib.reload(kiro_bridge)

            bridge = kiro_bridge.KiroBridge.__new__(kiro_bridge.KiroBridge)
            bridge._acp = mock_acp
            bridge._acp_lock = threading.Lock()
            bridge._sessions = {}
            bridge._session_history = {}
            bridge._sessions_lock = threading.Lock()
            bridge._soul = ""
            return bridge, mock_acp


class TestSetModel:
    def test_returns_false_when_no_session(self):
        bridge, _ = _make_bridge()
        result = bridge.set_model("claude-opus-4.6", "user1")
        assert result is False

    def test_calls_session_set_model(self):
        bridge, mock_acp = _make_bridge()
        bridge._sessions["user1"] = "sess-abc"
        bridge.set_model("claude-opus-4.6", "user1")
        mock_acp.session_set_model.assert_called_once_with("sess-abc", "claude-opus-4.6")

    def test_updates_models_cache(self):
        bridge, mock_acp = _make_bridge()
        bridge._sessions["user1"] = "sess-abc"
        mock_acp._models = {"currentModelId": "auto", "availableModels": []}
        bridge.set_model("claude-opus-4.6", "user1")
        assert mock_acp._models["currentModelId"] == "claude-opus-4.6"


class TestListModels:
    def test_returns_acp_models(self):
        bridge, mock_acp = _make_bridge()
        models = bridge.list_models()
        assert models == mock_acp._models
        assert "currentModelId" in models
