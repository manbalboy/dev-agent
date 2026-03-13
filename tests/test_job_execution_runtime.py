from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.command_runner import CommandExecutionError
from app.job_execution_runtime import JobExecutionRuntime


@dataclass
class _Job:
    job_id: str
    issue_number: int = 1
    log_file: str = "job.log"


class _Store:
    def __init__(self, job_ids: list[str]) -> None:
        self.job_ids = list(job_ids)

    def dequeue_job(self):
        if not self.job_ids:
            return None
        return self.job_ids.pop(0)


class _Owner:
    def __init__(self) -> None:
        self.store = _Store([])
        self.processed: list[str] = []
        self.fixed_pipeline_calls = 0
        self.workflow_pipeline_calls = 0
        self.logged: list[str] = []

    def process_job(self, job_id: str) -> None:
        self.processed.append(job_id)

    def _require_job(self, job_id: str) -> _Job:
        return _Job(job_id=job_id)

    def _stage_prepare_repo(self, job, log_path: Path) -> Path:
        return Path("/tmp/repo")

    def _load_active_workflow(self, job, log_path: Path):
        return None

    def _run_fixed_pipeline(self, job, repository_path: Path, log_path: Path) -> None:
        self.fixed_pipeline_calls += 1

    def _linearize_workflow_nodes(self, workflow):
        return []

    def _resolve_workflow_resume_state(self, **kwargs):
        return {"mode": "full_rerun", "source_attempt": 0, "skipped_nodes": []}

    def _append_actor_log(self, log_path: Path, actor: str, message: str) -> None:
        self.logged.append(f"{actor}:{message}")

    def _run_workflow_pipeline(
        self,
        job,
        repository_path: Path,
        workflow,
        ordered_nodes,
        log_path: Path,
        *,
        resume_state=None,
    ) -> None:
        self.workflow_pipeline_calls += 1


def test_process_next_job_returns_false_when_queue_empty() -> None:
    owner = _Owner()
    runtime = JobExecutionRuntime(owner=owner)

    assert runtime.process_next_job() is False


def test_process_next_job_dequeues_and_delegates_to_owner_process_job() -> None:
    owner = _Owner()
    owner.store = _Store(["job-1"])
    runtime = JobExecutionRuntime(owner=owner)

    assert runtime.process_next_job() is True
    assert owner.processed == ["job-1"]


def test_run_single_attempt_uses_fixed_pipeline_when_workflow_missing() -> None:
    owner = _Owner()
    runtime = JobExecutionRuntime(owner=owner)

    runtime.run_single_attempt("job-1", Path("/tmp/job.log"))

    assert owner.fixed_pipeline_calls == 1
    assert owner.workflow_pipeline_calls == 0


def test_run_single_attempt_raises_when_workflow_has_no_executable_nodes() -> None:
    owner = _Owner()
    owner._load_active_workflow = lambda job, log_path: {"workflow_id": "wf-1"}  # type: ignore[method-assign]
    runtime = JobExecutionRuntime(owner=owner)

    with pytest.raises(CommandExecutionError):
        runtime.run_single_attempt("job-1", Path("/tmp/job.log"))
