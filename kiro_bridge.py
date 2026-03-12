"""kiro_bridge.py — Production bridge between Telegram bot and Kiro CLI via ACP."""

import atexit
import json
import logging
import os
import threading

from acp_client import ACPClient, KiroAuthError, PromptResult

KIRO_CLI_PATH = os.environ.get("KIRO_CLI_PATH", "kiro-cli")
WORKING_DIR = os.environ.get("KIRO_WORKING_DIR", os.getcwd())
os.makedirs(WORKING_DIR, exist_ok=True)
CONTEXT_THRESHOLD = 80  # start new session when context usage exceeds this %
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUL_PATH = os.path.join(BOT_DIR, "SOUL.md")
SESSIONS_FILE = os.path.join(BOT_DIR, "sessions.json")

log = logging.getLogger(__name__)


def _load_soul() -> str:
    """Load SOUL.md as system identity. Returns empty string if missing."""
    try:
        with open(SOUL_PATH, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


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
        self._sessions_lock = threading.Lock()
        self._soul = _load_soul()
        if self._soul:
            log.info("🧠 SOUL.md loaded (%d chars)", len(self._soul))
        self._load_sessions()
        atexit.register(self.stop)

    def _load_sessions(self):
        """Restore session IDs from disk."""
        try:
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
            self._sessions = data.get("active", {})
            self._session_history = data.get("history", {})
            log.info("📂 Restored %d session(s) from disk", len(self._sessions))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_sessions(self):
        """Persist session IDs to disk."""
        with open(SESSIONS_FILE, "w") as f:
            json.dump({"active": self._sessions, "history": self._session_history}, f)

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

        full_prompt = f"{self._soul}\n\n---\n\n{text}" if self._soul else text
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

    def list_sessions(self, user_key: str) -> list[dict]:
        """Return all sessions for a user with metadata."""
        with self._sessions_lock:
            history = self._session_history.get(user_key, [])
            active = self._sessions.get(user_key)
        acp = self._ensure_acp()
        result = []
        for sid in history:
            meta = acp._session_metadata.get(sid, {})
            result.append({
                "session_id": sid,
                "active": sid == active,
                "context_pct": meta.get("contextUsagePercentage", 0.0),
                "credits": meta.get("credits", 0.0),
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
