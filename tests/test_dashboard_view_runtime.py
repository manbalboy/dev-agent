from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.dashboard import _TIMESTAMPED_LINE_PATTERN, _classify_command_target
from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.dashboard_view_runtime import DashboardViewRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_request(path: str = "/") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=17,
        issue_title="Dashboard view runtime",
        issue_url="https://github.com/owner/repo/issues/17",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/test/{job_id}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(app_components) -> DashboardViewRuntime:
    settings, store, _ = app_components
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parents[1] / "app" / "templates")
    )
    artifact_runtime = DashboardJobArtifactRuntime(
        settings=settings,
        timestamped_line_pattern=_TIMESTAMPED_LINE_PATTERN,
        classify_command_target=_classify_command_target,
    )
    return DashboardViewRuntime(
        store=store,
        templates=templates,
        artifact_runtime=artifact_runtime,
    )


def test_dashboard_view_runtime_renders_dashboard_shell(app_components) -> None:
    runtime = _build_runtime(app_components)

    response = runtime.render_dashboard_shell(_make_request("/"))

    assert "AgentHub Jobs" in response.body.decode("utf-8")


def test_dashboard_view_runtime_renders_job_detail_page(app_components) -> None:
    _, store, _ = app_components
    job = _make_job("view-runtime-job")
    store.create_job(job)
    runtime = _build_runtime(app_components)

    response = runtime.render_job_detail_page(_make_request(f"/jobs/{job.job_id}"), job.job_id)

    text = response.body.decode("utf-8")
    assert job.job_id in text
    assert "실패 분류" in text


def test_dashboard_view_runtime_reads_validated_log_text(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(app_components)
    log_path = settings.logs_dir / "sample.log"
    log_path.write_text("hello view runtime\n", encoding="utf-8")

    response = runtime.log_file_response(file_name="sample.log")

    assert isinstance(response, PlainTextResponse)
    assert response.body.decode("utf-8") == "hello view runtime\n"


def test_dashboard_view_runtime_rejects_invalid_log_name(app_components) -> None:
    runtime = _build_runtime(app_components)

    with pytest.raises(HTTPException) as exc_info:
        runtime.read_log_file(file_name="../bad.log")

    assert exc_info.value.status_code == 400
