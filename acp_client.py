"""
acp_client.py — JSON-RPC 2.0 over stdio client for kiro-cli ACP.
No external dependencies. Pure Python stdlib.
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)
_BUF_SIZE = 4 * 1024 * 1024


@dataclass
class ToolCallInfo:
    tool_call_id: str = ""
    title: str = ""
    kind: str = ""
    status: str = "pending"
    content: str = ""


@dataclass
class PromptResult:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    stop_reason: str = ""
    kiro_context_pct: float = 0.0
    kiro_credits: float = 0.0


@dataclass
class PermissionRequest:
    session_id: str
    tool_call_id: str
    title: str
    options: list

    class KiroAuthError(Exception):
        """Raised when kiro-cli requires authentication."""
        pass


class ACPClient:
    """JSON-RPC 2.0 client that communicates with kiro-cli over stdio."""

    def __init__(self, cli_path: str = "kiro-cli"):
        self._cli_path = cli_path
        self._proc = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, tuple] = {}
        self._session_updates: dict[str, list] = {}
        self._permission_handler: Callable | None = None
        self._session_metadata: dict[str, dict] = {}
        self._models: dict = {}  # from session/new: {currentModelId, availableModels}
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────

    def start(self, cwd: str | None = None):
        """Launch kiro-cli in ACP mode and complete the JSON-RPC handshake."""
        self._proc = subprocess.Popen(
            [self._cli_path, "acp"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._running = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        # Check if process died immediately (e.g. auth failure on startup)
        time.sleep(0.5)
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read().decode(errors="replace")
            if any(kw in stderr.lower() for kw in ("auth", "login", "credential",
                                                     "unauthorized", "not logged in",
                                                     "sign in")):
                raise KiroAuthError(
                    f"kiro-cli failed to start (auth required): {stderr.strip()}"
                )
            raise RuntimeError(
                f"kiro-cli exited immediately (code {self._proc.returncode}): "
                f"{stderr.strip()}"
            )

        result = self._send_request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True},
                "terminal": True,
            },
            "clientInfo": {"name": "telegram-kiro-bridge", "version": "1.0.0"},
        })
        log.info("[ACP] Handshake OK: %s", json.dumps(result)[:200])
        return result

    def stop(self):
        """Graceful shutdown: kill children first, then close the main process."""
        self._running = False
        if self._proc and self._proc.poll() is None:
            self._kill_children(self._proc.pid)
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _kill_children(self, parent_pid: int):
        try:
            r = subprocess.run(
                ["pgrep", "-P", str(parent_pid)],
                capture_output=True, text=True,
            )
            for pid_str in r.stdout.strip().split("\n"):
                if pid_str:
                    child_pid = int(pid_str)
                    self._kill_children(child_pid)
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        except Exception as e:
            log.debug("[ACP] Child cleanup error: %s", e)

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    # ── Session Management ────────────────────────────────

    def session_new(self, cwd: str) -> tuple[str, dict]:
        """Create a new Kiro session."""
        result = self._send_request("session/new", {
            "cwd": cwd,
            "mcpServers": [],
        })
        session_id = result.get("sessionId", "")
        if not session_id:
            raise RuntimeError(f"session/new returned no sessionId: {result}")
        self._models = result.get("models", {})
        return session_id, result.get("modes", {})

    def session_load(self, session_id: str, cwd: str) -> dict:
        """Resume an existing session — preserves full conversation context."""
        return self._send_request("session/load", {
            "sessionId": session_id,
            "cwd": cwd,
            "mcpServers": [],
        })

    # ── Core: Send a Prompt ───────────────────────────────

    def session_prompt(
        self,
        session_id: str,
        text: str,
        images: list[tuple[str, str]] | None = None,
        timeout: float = 300,
    ) -> PromptResult:
        """Send a prompt and block until Kiro completes the response."""
        self._session_updates[session_id] = []
        req_id = self._next_id()

        prompt_content = []
        if images:
            for b64, mime in images:
                prompt_content.append({"type": "image", "data": b64, "mimeType": mime})
        if text:
            prompt_content.append({"type": "text", "text": text})
        elif images:
            prompt_content.append({"type": "text", "text": "?"})

        result = self._send_request_with_id("session/prompt", {
            "sessionId": session_id,
            "prompt": prompt_content,
        }, req_id, timeout=timeout)

        return self._build_prompt_result(session_id, result)

    # ── Permission Control ────────────────────────────────

    def on_permission_request(self, handler):
        """Register a permission decision callback."""
        self._permission_handler = handler

    def _handle_permission_request(self, msg_id, params: dict):
        title = params.get("toolCall", {}).get("title", "Unknown")
        if self._permission_handler is None:
            self._send_permission_response(
                msg_id, params.get("sessionId", ""), "allow_once",
            )
            return
        request = PermissionRequest(
            session_id=params.get("sessionId", ""),
            tool_call_id=params.get("toolCall", {}).get("toolCallId", ""),
            title=title,
            options=params.get("options", []),
        )

        def handle_async():
            decision = self._permission_handler(request) or "deny"
            self._send_permission_response(msg_id, request.session_id, decision)

        threading.Thread(target=handle_async, daemon=True).start()

    # ── Internal: JSON-RPC Transport ──────────────────────

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send_request(self, method, params, timeout=300):
        return self._send_request_with_id(method, params, self._next_id(), timeout)

    def _send_request_with_id(self, method, params, req_id, timeout=300):
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        evt = threading.Event()
        holder: list = []
        self._pending[req_id] = (evt, holder)
        self._proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        self._proc.stdin.flush()
        if not evt.wait(timeout=timeout):
            self._pending.pop(req_id, None)
            raise TimeoutError(f"{method} (id={req_id}) timed out after {timeout}s")
        self._pending.pop(req_id, None)
        if len(holder) == 2 and holder[0] is None:
            error = holder[1]
            code = error.get("code", 0)
            message = error.get("message", "")
            if self._is_auth_error(code, message):
                raise KiroAuthError(
                    "kiro-cli authentication required. "
                    "Please run 'kiro-cli login' on the host and ensure "
                    "~/.kiro is mounted into the container."
                )
            raise RuntimeError(f"RPC error {code}: {message}")
        return holder[0] if holder else {}
    @staticmethod
    def _is_auth_error(code: int, message: str) -> bool:
        """Detect authentication-related RPC errors from kiro-cli."""
        msg_lower = message.lower()
        auth_keywords = ("auth", "login", "credential", "token expired",
                         "unauthorized", "unauthenticated", "not logged in",
                         "session expired", "sign in")
        if any(kw in msg_lower for kw in auth_keywords):
            return True
        # Common HTTP-style auth error codes
        if code in (401, 403, -32001):
            return True
        return False

    def _read_loop(self):
        while self._running:
            try:
                line = self._proc.stdout.readline(_BUF_SIZE)
                if not line:
                    break
                self._handle_line(line.decode(errors="replace").strip())
            except Exception as e:
                if self._running:
                    log.error("[ACP] Read error: %s", e)
                break

    def _read_stderr(self):
        while self._running:
            try:
                line = self._proc.stderr.readline()
                if not line:
                    break
                log.debug("[ACP stderr] %s", line.decode(errors="replace").strip())
            except Exception:
                break

    def _handle_line(self, line: str):
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return

        msg_id = msg.get("id")
        method = msg.get("method")

        # Response to our pending request
        if msg_id is not None and method is None:
            pending = self._pending.get(msg_id)
            if pending:
                evt, holder = pending
                if msg.get("error"):
                    holder.extend([None, msg["error"]])
                else:
                    holder.append(msg.get("result", {}))
                evt.set()
            return

        # Kiro asking us for permission
        if msg_id is not None and method == "session/request_permission":
            self._handle_permission_request(msg_id, msg.get("params", {}))
            return

        # Notifications (streaming)
        if method and msg_id is None:
            params = msg.get("params", {})
            session_id = params.get("sessionId", "")

            if method == "session/update" and session_id:
                updates = self._session_updates.get(session_id)
                if updates is not None:
                    updates.append(params.get("update", {}))

            elif method == "_kiro.dev/metadata" and session_id:
                meta = self._session_metadata.get(session_id, {})
                meta.update(params)
                self._session_metadata[session_id] = meta

    def _send_permission_response(self, msg_id, session_id, option_id):
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "outcome": (
                    {"outcome": "cancelled"}
                    if option_id == "deny"
                    else {"outcome": "selected", "optionId": option_id}
                )
            },
        }
        self._proc.stdin.write((json.dumps(response) + "\n").encode())
        self._proc.stdin.flush()

    def _build_prompt_result(self, session_id, rpc_result) -> PromptResult:
        updates = self._session_updates.pop(session_id, [])
        meta = self._session_metadata.get(session_id, {})
        result = PromptResult(
            stop_reason=rpc_result.get("stopReason", ""),
            kiro_context_pct=meta.get("contextUsagePercentage", 0.0),
            kiro_credits=meta.get("credits", 0.0),
        )
        text_parts = []
        tool_calls: dict[str, ToolCallInfo] = {}

        for update in updates:
            st = update.get("sessionUpdate", "")
            if st == "agent_message_chunk":
                c = update.get("content", {})
                if isinstance(c, dict) and c.get("type") == "text":
                    text_parts.append(c.get("text", ""))
            elif st == "tool_call":
                tc_id = update.get("toolCallId", "")
                tool_calls[tc_id] = ToolCallInfo(
                    tool_call_id=tc_id,
                    title=update.get("title", ""),
                    kind=update.get("kind", ""),
                    status=update.get("status", "pending"),
                )
            elif st == "tool_call_update":
                tc_id = update.get("toolCallId", "")
                if tc := tool_calls.get(tc_id):
                    tc.status = update.get("status", tc.status)
                    for c in update.get("content", []):
                        if isinstance(c, dict):
                            inner = c.get("content", {})
                            if isinstance(inner, dict) and inner.get("type") == "text":
                                tc.content = inner.get("text", "")

        result.text = "".join(text_parts)
        result.tool_calls = list(tool_calls.values())
        return result
