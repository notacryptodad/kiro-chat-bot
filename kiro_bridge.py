"""kiro_bridge.py — Production bridge between Telegram bot and Kiro CLI via ACP."""

import atexit
import json
import logging
import os
import threading

from acp_client import ACPClient, KiroAuthError, PromptResult

KIRO_CLI_PATH = os.environ.get("KIRO_CLI_PATH", "kiro-cli")
WORKING_DIR = os.environ.get("KIRO_WORKING_DIR", os.getcwd())
KIRO_DEFAULT_MODEL = os.environ.get("KIRO_DEFAULT_MODEL", "")
os.makedirs(WORKING_DIR, exist_ok=True)
CONTEXT_THRESHOLD = 80  # start new session when context usage exceeds this %
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(BOT_DIR, "SOUL.md")
SESSIONS_FILE = os.path.join(BOT_DIR, "sessions.json")

log = logging.getLogger(__name__)


def _load_soul() -> str:
    """Load SOUL.md, substituting {{BOT_NAME}} from bot_name.txt if present."""
    try:
        with open(SOUL_PATH, "r") as f:
            soul = f.read().strip()
    except FileNotFoundError:
        return ""
    name_file = os.path.join(WORKING_DIR, "bot_name.txt")
    try:
        with open(name_file, "r") as f:
            name = f.read().strip()
        soul = soul.replace("{{BOT_NAME}}", name)
        # Remove the unnamed instruction block once named
        import re
        soul = re.sub(r"\{\{#if_unnamed\}\}.*?\{\{/if_unnamed\}\}", "", soul, flags=re.DOTALL).strip()
    except FileNotFoundError:
        soul = soul.replace("{{BOT_NAME}}", "[unnamed]")
        soul = soul.replace("{{#if_unnamed}}", "").replace("{{/if_unnamed}}", "")
    return soul


class KiroBridge:
    """
    Lazy-start bridge with:
    - Session reuse across tasks
    - Auto context management (new session at 80% usage)
    - Per-user session isolation
    """

    def __init__(self):
        self._acp: ACPClient | None = None
        self._acp_lock = threading.Lock()
        self._sessions: dict[str, str] = {}  # key -> active session_id
        self._session_history: dict[str, list[str]] = {}  # key -> all session_ids
        self._session_timestamps: dict[str, float] = {}  # session_id -> creation timestamp
        self._sessions_lock = threading.Lock()
        self._soul = _load_soul()
        if self._soul:
            log.info("🧠 SOUL.md loaded:\n%s", self._soul)
        self._load_sessions()
        atexit.register(self.stop)

    def _load_sessions(self):
        """Restore session IDs from disk."""
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
            self._sessions = data.get("active", {})
            self._session_history = data.get("history", {})
            self._session_timestamps = data.get("timestamps", {})
            log.info("📂 Restored %d session(s) from disk", len(self._sessions))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_sessions(self):
        """Persist session IDs to disk."""
        with open(SESSIONS_FILE, "w") as f:
            json.dump({
                "active": self._sessions,
                "history": self._session_history,
                "timestamps": self._session_timestamps
            }, f)

    def _start_acp(self):
        with self._acp_lock:
            if self._acp is not None and self._acp.is_running():
                return
            if self._acp is not None:
                log.warning("🔄 ACP process died, restarting...")
                self._acp = None
            self._acp = ACPClient(cli_path=KIRO_CLI_PATH)
            self._acp.start(cwd=WORKING_DIR)
            self._acp.on_permission_request(lambda req: "allow_once")
            log.info("✅ Kiro ACP started (PID: %s)", self._acp._proc.pid)

    def _ensure_acp(self) -> ACPClient:
        self._start_acp()
        return self._acp

    def _get_session(self, key: str = "default") -> str:
        """Get or create a session. Resumes from disk, rotates when context is high."""
        with self._sessions_lock:
            sid = self._sessions.get(key)

        if sid:
            acp = self._ensure_acp()
            # Try to resume persisted session
            if sid not in acp._session_metadata:
                try:
                    acp.session_load(sid, WORKING_DIR)
                    log.info("▶️ Resumed session %s for %s", sid[:12], key)
                    return sid
                except Exception:
                    log.warning("Could not resume session %s, creating new", sid[:12])
            else:
                meta = acp._session_metadata.get(sid, {})
                if meta.get("contextUsagePercentage", 0) <= CONTEXT_THRESHOLD:
                    return sid
                log.warning("Context at %.1f%% for session %s, rotating",
                            meta["contextUsagePercentage"], key)

        acp = self._ensure_acp()
        session_id, _ = acp.session_new(WORKING_DIR)
        # Track creation time
        import time
        self._session_timestamps[session_id] = time.time()
        # Apply default model if configured
        if KIRO_DEFAULT_MODEL:
            try:
                acp.session_set_model(session_id, KIRO_DEFAULT_MODEL)
                if acp._models:
                    acp._models["currentModelId"] = KIRO_DEFAULT_MODEL
                log.info("🤖 Set default model: %s", KIRO_DEFAULT_MODEL)
            except Exception as e:
                log.warning("Failed to set default model: %s", e)
        with self._sessions_lock:
            self._sessions[key] = session_id
            self._session_history.setdefault(key, []).append(session_id)
            self._save_sessions()
        return session_id

    def prompt(self, text: str, user_key: str = "default",
               timeout: float = 300) -> dict:
        """Send a coding task to Kiro. Prepends SOUL.md as system context."""
        acp = self._ensure_acp()
        sid = self._get_session(user_key)

        soul = _load_soul()
        if soul != self._soul:
            log.info("🧠 SOUL updated:\n%s", soul)
            self._soul = soul
        full_prompt = f"{soul}\n\n---\n\n{text}" if soul else text
        result: PromptResult = acp.session_prompt(sid, full_prompt, timeout=timeout)

        return {
            "success": True,
            "text": result.text,
            "tool_calls": [
                {"kind": tc.kind, "title": tc.title, "status": tc.status}
                for tc in result.tool_calls
            ],
            "usage": {
                "kiro_credits": result.kiro_credits,
                "kiro_context_pct": result.kiro_context_pct,
            },
        }

    def list_models(self) -> dict:
        """Return available models and current model from ACP."""
        acp = self._ensure_acp()
        return acp._models

    def set_model(self, model_id: str, user_key: str = "default") -> bool:
        """Switch model for the user's active session."""
        sid = self._sessions.get(user_key)
        if not sid:
            log.warning("set_model: no active session for %s", user_key)
            return False
        acp = self._ensure_acp()
        result = acp.session_set_model(sid, model_id)
        log.info("🤖 Model switched to %s (result: %s)", model_id, result)
        # Update local models cache so footer reflects the change
        if acp._models:
            acp._models["currentModelId"] = model_id
        return True

    def list_sessions(self, user_key: str) -> list[dict]:
        """Return all sessions for a user with metadata."""
        import time
        with self._sessions_lock:
            history = self._session_history.get(user_key, [])
            active = self._sessions.get(user_key)
        acp = self._ensure_acp()
        result = []
        now = time.time()
        for sid in history:
            meta = acp._session_metadata.get(sid, {})
            created = self._session_timestamps.get(sid, 0)
            age_hours = (now - created) / 3600 if created else 0
            result.append({
                "session_id": sid,
                "active": sid == active,
                "context_pct": meta.get("contextUsagePercentage", 0.0),
                "credits": meta.get("credits", 0.0),
                "age_hours": age_hours,
            })
        return result

    def resume_session(self, user_key: str, session_id: str) -> bool:
        """Resume a previous session by loading it via ACP."""
        with self._sessions_lock:
            history = self._session_history.get(user_key, [])
            if session_id not in history:
                return False
        acp = self._ensure_acp()
        acp.session_load(session_id, WORKING_DIR)
        with self._sessions_lock:
            self._sessions[user_key] = session_id
            self._save_sessions()
        return True

    def stop(self):
        if self._acp:
            self._acp.stop()
            self._acp = None
            with self._sessions_lock:
                self._sessions.clear()
                self._session_history.clear()
            log.info("🛑 Kiro ACP stopped")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stop()
