"""Tests for acp_client.py"""
import json
import threading
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from acp_client import ACPClient, KiroAuthError


def _make_client_with_mock_proc(responses: list[dict]) -> ACPClient:
    """Create an ACPClient with a mocked subprocess that returns given JSON responses."""
    client = ACPClient.__new__(ACPClient)
    client._cli_path = "kiro-cli"
    client._req_id = 0
    client._lock = threading.Lock()
    client._pending = {}
    client._session_updates = {}
    client._permission_handler = None
    client._session_metadata = {}
    client._models = {}
    client._running = True

    # Build stdout that returns responses in order
    lines = "\n".join(json.dumps(r) for r in responses) + "\n"
    client._proc = MagicMock()
    client._proc.poll.return_value = None
    client._proc.stdout = BytesIO(lines.encode())
    client._proc.stdin = MagicMock()
    client._proc.stderr = BytesIO(b"")
    return client


class TestSessionNew:
    def test_populates_models(self):
        client = ACPClient.__new__(ACPClient)
        client._req_id = 0
        client._lock = threading.Lock()
        client._pending = {}
        client._session_updates = {}
        client._permission_handler = None
        client._session_metadata = {}
        client._models = {}
        client._running = True
        client._proc = MagicMock()
        client._proc.poll.return_value = None
        client._proc.stdin = MagicMock()

        models_data = {
            "currentModelId": "auto",
            "availableModels": [
                {"modelId": "claude-sonnet-4-20250514", "name": "Claude Sonnet"},
                {"modelId": "claude-opus-4.6", "name": "Claude Opus"},
            ]
        }

        def fake_send(method, params, timeout=300):
            return {"sessionId": "test-session-123", "models": models_data}

        client._send_request = fake_send
        session_id, _ = client.session_new("/tmp")

        assert session_id == "test-session-123"
        assert client._models == models_data
        assert client._models["currentModelId"] == "auto"
        assert len(client._models["availableModels"]) == 2

    def test_raises_on_missing_session_id(self):
        client = ACPClient.__new__(ACPClient)
        client._req_id = 0
        client._lock = threading.Lock()
        client._pending = {}
        client._session_updates = {}
        client._permission_handler = None
        client._session_metadata = {}
        client._models = {}
        client._running = True
        client._proc = MagicMock()
        client._proc.stdin = MagicMock()
        client._send_request = lambda *a, **kw: {}

        with pytest.raises(RuntimeError, match="no sessionId"):
            client.session_new("/tmp")


class TestSessionLoad:
    def test_populates_models_from_load(self):
        client = ACPClient.__new__(ACPClient)
        client._req_id = 0
        client._lock = threading.Lock()
        client._pending = {}
        client._session_updates = {}
        client._permission_handler = None
        client._session_metadata = {}
        client._models = {}
        client._running = True
        client._proc = MagicMock()
        client._proc.stdin = MagicMock()

        models_data = {"currentModelId": "claude-opus-4.6", "availableModels": []}
        client._send_request = lambda *a, **kw: {"sessionId": "abc", "models": models_data}

        client.session_load("abc", "/tmp")
        assert client._models["currentModelId"] == "claude-opus-4.6"

    def test_does_not_overwrite_models_if_empty(self):
        client = ACPClient.__new__(ACPClient)
        client._req_id = 0
        client._lock = threading.Lock()
        client._pending = {}
        client._session_updates = {}
        client._permission_handler = None
        client._session_metadata = {}
        client._models = {"currentModelId": "existing-model", "availableModels": []}
        client._running = True
        client._proc = MagicMock()
        client._proc.stdin = MagicMock()

        client._send_request = lambda *a, **kw: {"sessionId": "abc"}  # no models key

        client.session_load("abc", "/tmp")
        assert client._models["currentModelId"] == "existing-model"  # unchanged


class TestSessionSetModel:
    def test_sends_correct_rpc(self):
        client = ACPClient.__new__(ACPClient)
        client._req_id = 0
        client._lock = threading.Lock()
        client._pending = {}
        client._session_updates = {}
        client._permission_handler = None
        client._session_metadata = {}
        client._models = {}
        client._running = True
        client._proc = MagicMock()
        client._proc.stdin = MagicMock()

        calls = []
        def fake_send(method, params, timeout=300):
            calls.append((method, params))
            return {"modelId": params["modelId"]}

        client._send_request = fake_send
        client.session_set_model("sess-123", "claude-opus-4.6")

        assert calls[0] == ("session/set_model", {"sessionId": "sess-123", "modelId": "claude-opus-4.6"})


class TestAuthError:
    def test_detects_auth_keywords(self):
        assert ACPClient._is_auth_error(0, "not logged in") is True
        assert ACPClient._is_auth_error(401, "") is True
        assert ACPClient._is_auth_error(0, "some random error") is False
