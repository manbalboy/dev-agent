from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from app.models import JobRecord


class JobModeRuntime:
    """Own environment toggles and job mode/track classification helpers."""

    def __init__(
        self,
        *,
        default_enable_escalation: bool,
        env_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.default_enable_escalation = default_enable_escalation
        self.env_path = env_path or (Path.cwd() / ".env")
        self.environ = environ or os.environ

    @staticmethod
    def _env_truthy(raw: str | None, *, default: bool = False) -> bool:
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def is_escalation_enabled(self) -> bool:
        """Read escalation toggle from .env at runtime, falling back to boot setting."""

        if self.env_path.exists():
            try:
                for raw_line in self.env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if not line.startswith("AGENTHUB_ENABLE_ESCALATION="):
                        continue
                    raw_value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return self._env_truthy(raw_value, default=self.default_enable_escalation)
            except OSError:
                return self.default_enable_escalation

        return self._env_truthy(
            self.environ.get("AGENTHUB_ENABLE_ESCALATION"),
            default=self.default_enable_escalation,
        )

    def is_recovery_mode_enabled(self) -> bool:
        """Read recovery mode toggle from environment with default enabled."""

        return self._env_truthy(self.environ.get("AGENTHUB_RECOVERY_MODE", "true"), default=True)

    @staticmethod
    def is_long_track(job: JobRecord) -> bool:
        """Return True when job should use long-horizon planning mode."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "long":
            return True
        return "[장기]" in title or "[long]" in title

    @staticmethod
    def is_ultra_track(job: JobRecord) -> bool:
        """Return True when ultra-long autonomous round mode is enabled."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "ultra":
            return True
        return "[초장기]" in title or "[ultra]" in title

    @staticmethod
    def is_ultra10_track(job: JobRecord) -> bool:
        """Return True when 10-hour ultra-long autonomous round mode is enabled."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "ultra10":
            return True
        return "[초초장기]" in title or "[ultra10]" in title
