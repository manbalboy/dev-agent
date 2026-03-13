from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from app.store import JobStore


class JobLogRuntime:
    """Own log channel routing and lightweight job heartbeat updates."""

    def __init__(
        self,
        *,
        store: JobStore,
        utc_now_iso: Callable[[], str],
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self._utc_now_iso = utc_now_iso
        self._monotonic_fn = monotonic_fn

    def actor_log_writer(
        self,
        log_path: Path,
        actor: str,
        *,
        append_actor_log: Callable[[Path, str, str], None],
    ):
        """Return one writer that preserves actor tagging for downstream runtimes."""

        return lambda message: append_actor_log(log_path, actor, message)

    @staticmethod
    def infer_actor_from_command(command: str, purpose: str) -> str:
        """Infer execution actor from command/purpose for richer log context."""

        lowered = command.lower()
        purpose_lowered = purpose.lower()
        if "codex" in lowered:
            return "CODER"
        if "planner" in purpose_lowered or "plan" in purpose_lowered and "gemini" in lowered:
            return "PLANNER"
        if "review" in purpose_lowered and "gemini" in lowered:
            return "REVIEWER"
        if lowered.startswith("gh ") or " gh " in lowered:
            return "GITHUB"
        if lowered.startswith("git ") or " git " in lowered:
            return "GIT"
        return "SYSTEM"

    def append_actor_log(
        self,
        log_path: Path,
        actor: str,
        message: str,
        *,
        touch_job_heartbeat: Callable[[], None],
    ) -> None:
        """Append one timestamped actor-tagged line to job log files."""

        normalized_actor = (actor or "ORCHESTRATOR").strip().upper()
        if message.startswith("[ACTOR:"):
            tagged = message
        else:
            tagged = f"[ACTOR:{normalized_actor}] {message}"
        debug_log_path = self.channel_log_path(log_path, "debug")
        user_log_path = self.channel_log_path(log_path, "user")
        self.append_log(debug_log_path, tagged, utc_now_iso=self._utc_now_iso)
        if self.should_emit_user_log(message):
            self.append_log(user_log_path, tagged, utc_now_iso=self._utc_now_iso)
        touch_job_heartbeat()

    def touch_job_heartbeat(
        self,
        *,
        active_job_id: str | None,
        last_heartbeat_monotonic: float,
        force: bool = False,
        min_interval_seconds: float = 15.0,
    ) -> float:
        """Persist one lightweight heartbeat for the active job."""

        if not active_job_id:
            return last_heartbeat_monotonic
        now_monotonic = self._monotonic_fn()
        if not force and (now_monotonic - last_heartbeat_monotonic) < min_interval_seconds:
            return last_heartbeat_monotonic
        try:
            self.store.update_job(active_job_id, heartbeat_at=self._utc_now_iso())
        except Exception:
            return now_monotonic
        return now_monotonic

    @staticmethod
    def channel_log_path(log_path: Path, channel: str) -> Path:
        """Return channel-specific log path from any legacy/debug/user path."""

        normalized = "user" if channel == "user" else "debug"
        parent = log_path.parent
        if parent.name == normalized:
            return log_path
        if parent.name in {"debug", "user"}:
            return parent.parent / normalized / log_path.name
        return parent / normalized / log_path.name

    @staticmethod
    def should_emit_user_log(message: str) -> bool:
        """Return True when one log message should appear in the user-friendly channel."""

        msg = (message or "").strip()
        if not msg:
            return False
        if msg.startswith("[RUN] ") or msg.startswith("[STDOUT]") or msg.startswith("[STDERR]"):
            return False
        if msg.startswith("[STAGE] "):
            return True
        if msg.startswith("Attempt "):
            return True
        if msg.startswith("Starting job ") or msg.startswith("Job finished"):
            return True
        if msg.startswith("[DONE] "):
            return True
        if msg.startswith("Wrote ") or "snapshot saved" in msg.lower():
            return True
        if "failed" in msg.lower() or "error" in msg.lower():
            return True
        if msg.startswith("Entering fix/test retry loop") or msg.startswith("[FIX_LOOP]"):
            return True
        return False

    @staticmethod
    def append_log(log_path: Path, message: str, *, utc_now_iso: Callable[[], str]) -> None:
        """Append one timestamped line to one log file."""

        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now_iso()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
