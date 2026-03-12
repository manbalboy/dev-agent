"""Tests for app type runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.app_type_runtime import AppTypeRuntime
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-app-type-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=24,
        issue_title="app type runtime",
        issue_url="https://github.com/owner/repo/issues/24",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-24-app-type",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="food",
    )


def _build_runtime(logs: list[str]) -> AppTypeRuntime:
    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return AppTypeRuntime(
        docs_file=docs_file,
        set_stage=lambda *args, **kwargs: None,
        append_actor_log=lambda log_path, actor, message: logs.append(f"{actor}:{message}"),
    )


def test_app_type_runtime_resolves_supported_type_from_spec_json(tmp_path: Path) -> None:
    logs: list[str] = []
    runtime = _build_runtime(logs)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    spec_json = repository_path / "_docs" / "SPEC.json"
    spec_json.parent.mkdir(parents=True, exist_ok=True)
    spec_json.write_text('{"app_type":"app"}\n', encoding="utf-8")

    value = runtime.resolve_app_type(repository_path, {"spec_json": spec_json})

    assert value == "app"


def test_app_type_runtime_falls_back_to_web_on_invalid_spec_json(tmp_path: Path) -> None:
    logs: list[str] = []
    runtime = _build_runtime(logs)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    spec_json = repository_path / "_docs" / "SPEC.json"
    spec_json.parent.mkdir(parents=True, exist_ok=True)
    spec_json.write_text("{invalid json", encoding="utf-8")

    value = runtime.resolve_app_type(repository_path, {"spec_json": spec_json})

    assert value == "web"


def test_app_type_runtime_writes_skip_review_for_non_web(tmp_path: Path) -> None:
    logs: list[str] = []
    runtime = _build_runtime(logs)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()

    runtime.stage_skip_ux_review_for_non_web(
        _make_job(),
        repository_path,
        {},
        tmp_path / "job.log",
        app_type="app",
    )

    content = (repository_path / "_docs" / "UX_REVIEW.md").read_text(encoding="utf-8")
    assert "Verdict: `SKIPPED`" in content
    assert "non-web app_type (app)" in content
    assert any("ux_e2e_review skipped for app_type=app" in line for line in logs)
