from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.models import JobStage
from app.repository_stage_runtime import RepositoryStageRuntime


class _Store:
    def __init__(self) -> None:
        self.updated: list[tuple[str, dict[str, str]]] = []

    def update_job(self, job_id: str, **kwargs: str) -> None:
        self.updated.append((job_id, kwargs))


def test_repository_stage_runtime_sha256_file_handles_missing_and_existing(tmp_path: Path) -> None:
    assert RepositoryStageRuntime.sha256_file(tmp_path / "missing.txt") == ""

    target = tmp_path / "sample.txt"
    target.write_text("hello\n", encoding="utf-8")

    assert RepositoryStageRuntime.sha256_file(target) == hashlib.sha256(b"hello\n").hexdigest()


def test_repository_stage_runtime_docs_file_creates_docs_directory(tmp_path: Path) -> None:
    result = RepositoryStageRuntime.docs_file(tmp_path, "SPEC.md")

    assert result == tmp_path / "_docs" / "SPEC.md"
    assert result.parent.is_dir()


def test_repository_stage_runtime_ref_exists_uses_shell_executor(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def _execute_shell_command(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(exit_code=0)

    runtime = RepositoryStageRuntime(
        store=_Store(),
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
        execute_shell_command=_execute_shell_command,
        actor_log_writer=lambda log_path, actor: f"{actor}:{log_path.name}",
        append_actor_log=lambda log_path, actor, message: None,
    )

    log_path = tmp_path / "job.log"
    assert runtime.ref_exists(tmp_path, "origin/main", log_path) is True
    assert calls[0]["check"] is False
    assert calls[0]["cwd"] == tmp_path
    assert "rev-parse --verify" in str(calls[0]["command"])
    assert calls[0]["command_purpose"] == "check ref origin/main"


def test_repository_stage_runtime_set_stage_updates_store_and_logs(tmp_path: Path) -> None:
    store = _Store()
    logs: list[tuple[str, str, str]] = []
    runtime = RepositoryStageRuntime(
        store=store,
        utc_now_iso=lambda: "2026-03-13T00:00:00Z",
        execute_shell_command=lambda **kwargs: SimpleNamespace(exit_code=0),
        actor_log_writer=lambda log_path, actor: None,
        append_actor_log=lambda log_path, actor, message: logs.append((str(log_path), actor, message)),
    )

    log_path = tmp_path / "job.log"
    runtime.set_stage("job-1", JobStage.PLAN_WITH_GEMINI, log_path)

    assert store.updated == [
        (
            "job-1",
            {
                "stage": JobStage.PLAN_WITH_GEMINI.value,
                "heartbeat_at": "2026-03-13T00:00:00Z",
            },
        )
    ]
    assert logs == [(str(log_path), "ORCHESTRATOR", "[STAGE] plan_with_gemini")]
