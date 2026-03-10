"""heartbeat.py — Periodic loop that reads heartbeat.md for tasks."""

import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "900"))  # seconds
HEARTBEAT_FILE = "heartbeat.md"
HEARTBEAT_LOG = "heartbeat_log.md"


class Heartbeat:
    """
    Reads heartbeat.md every N seconds.
    - If content is empty or "sleep" → do nothing.
    - Otherwise → execute content as a Kiro task, log result, reset file to "sleep".
    """

    def __init__(self, bridge, working_dir: str):
        self._bridge = bridge
        self._dir = working_dir
        self._heartbeat_path = os.path.join(working_dir, HEARTBEAT_FILE)
        self._log_path = os.path.join(working_dir, HEARTBEAT_LOG)
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("💓 Heartbeat started (every %ds, reading %s)",
                 HEARTBEAT_INTERVAL, self._heartbeat_path)

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                log.exception("Heartbeat tick error: %s", e)
            self._stop_event.wait(HEARTBEAT_INTERVAL)

    def _tick(self):
        try:
            with open(self._heartbeat_path, "r") as f:
                task = f.read().strip()
        except FileNotFoundError:
            return

        if not task or task.lower() == "sleep":
            return

        log.info("💓 Heartbeat task found: %s", task[:80])

        # Reset file immediately to prevent re-execution
        with open(self._heartbeat_path, "w") as f:
            f.write("sleep\n")

        try:
            result = self._bridge.prompt(task, user_key="heartbeat")
            self._log_result(task, result)
        except Exception as e:
            self._log_result(task, {"success": False, "text": str(e), "tool_calls": []})

    def _log_result(self, task: str, result: dict):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entry = (
            f"\n## [{ts}]\n"
            f"**Task:** {task}\n\n"
            f"**Result:** {'✅' if result.get('success') else '❌'}\n\n"
            f"{result.get('text', '')}\n"
        )
        if result.get("tool_calls"):
            entry += "\n**Actions:**\n"
            for tc in result["tool_calls"]:
                entry += f"- [{tc['status']}] {tc['title']}\n"
        entry += "\n---\n"

        with open(self._log_path, "a") as f:
            f.write(entry)
        log.info("💓 Heartbeat task completed, logged to %s", self._log_path)
