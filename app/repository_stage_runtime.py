from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any, Callable, Optional

from app.models import JobStage
from app.store import JobStore


class RepositoryStageRuntime:
    """Handle small repository/stage helpers outside the orchestrator."""

    def __init__(
        self,
        *,
        store: JobStore,
        utc_now_iso: Callable[[], str],
        execute_shell_command: Callable[..., Any],
        actor_log_writer: Callable[[Path, str], Any],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.store = store
        self._utc_now_iso = utc_now_iso
        self._execute_shell_command = execute_shell_command
        self._actor_log_writer = actor_log_writer
        self._append_actor_log = append_actor_log

    @staticmethod
    def sha256_file(path: Optional[Path]) -> str:
        """Return SHA256 of one file, empty string when unavailable."""

        if path is None or not path.exists() or not path.is_file():
            return ""
        try:
            blob = path.read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def docs_file(repository_path: Path, name: str) -> Path:
        """Return a generated-document path under repository '_docs' directory."""

        docs_dir = repository_path / "_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / name

    def ref_exists(self, repository_path: Path, ref_name: str, log_path: Path) -> bool:
        """Return True when a git ref exists locally."""

        check_command = (
            f"git -C {shlex.quote(str(repository_path))} rev-parse --verify "
            f"{shlex.quote(ref_name)}"
        )
        result = self._execute_shell_command(
            command=check_command,
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "GIT"),
            check=False,
            command_purpose=f"check ref {ref_name}",
        )
        return getattr(result, "exit_code", 1) == 0

    def set_stage(self, job_id: str, stage: JobStage, log_path: Path) -> None:
        """Persist stage transition and emit one readable orchestrator log line."""

        self.store.update_job(job_id, stage=stage.value, heartbeat_at=self._utc_now_iso())
        self._append_actor_log(log_path, "ORCHESTRATOR", f"[STAGE] {stage.value}")
