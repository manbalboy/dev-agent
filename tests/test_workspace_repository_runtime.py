"""Tests for workspace repository runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.models import JobRecord, JobStatus, utc_now_iso
from app.workspace_repository_runtime import WorkspaceRepositoryRuntime


def _make_job(job_id: str = "job-workspace-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/hub",
        issue_number=11,
        issue_title="workspace runtime",
        issue_url="https://github.com/owner/hub/issues/11",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/default/issue-11",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="food",
    )


def test_workspace_repository_runtime_prefers_source_repository_for_execution(app_components) -> None:
    settings, _, _ = app_components
    runtime = WorkspaceRepositoryRuntime(
        settings=settings,
        set_stage=lambda *args, **kwargs: None,
        append_log=lambda *args, **kwargs: None,
        run_shell=lambda *args, **kwargs: None,
        ref_exists=lambda *args, **kwargs: False,
    )
    job = _make_job()
    job.source_repository = "owner/source-repo"

    assert runtime.job_execution_repository(job) == "owner/source-repo"
    assert runtime.job_workspace_path(job) == settings.repository_workspace_path("owner/source-repo", "food")
    assert runtime.issue_reference_line(job) == "Tracking issue: https://github.com/owner/hub/issues/11"


def test_workspace_repository_runtime_normalizes_repository_ref() -> None:
    assert WorkspaceRepositoryRuntime.normalize_repository_ref("https://github.com/owner/repo.git") == "owner/repo"
    assert WorkspaceRepositoryRuntime.normalize_repository_ref("git@github.com:owner/repo.git") == "owner/repo"
    assert WorkspaceRepositoryRuntime.normalize_repository_ref("owner/repo") == "owner/repo"


def test_workspace_repository_runtime_detects_git_metadata(tmp_path: Path, app_components) -> None:
    settings, _, _ = app_components
    runtime = WorkspaceRepositoryRuntime(
        settings=settings,
        set_stage=lambda *args, **kwargs: None,
        append_log=lambda *args, **kwargs: None,
        run_shell=lambda *args, **kwargs: None,
        ref_exists=lambda *args, **kwargs: False,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runtime.workspace_has_git_metadata(repo) is False
    (repo / ".git").mkdir()
    assert runtime.workspace_has_git_metadata(repo) is True
