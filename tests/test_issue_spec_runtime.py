from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.command_runner import CommandExecutionError
from app.issue_spec_runtime import IssueSpecRuntime
from app.models import JobRecord, JobStage, JobStatus
from app.orchestrator import IssueDetails
from app.prompt_builder import build_spec_json, build_spec_markdown
from app.spec_tools import (
    issue_reader,
    repo_context_reader,
    risk_policy_checker,
    spec_rewriter,
    spec_schema_validator,
)


def _make_job() -> JobRecord:
    return JobRecord(
        job_id="job-issue-spec",
        repository="owner/repo",
        issue_number=12,
        issue_title="기본 제목",
        issue_url="https://example.com/issues/12",
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        branch_name="agenthub/test/issue-12",
        created_at="2026-03-13T00:00:00+00:00",
        updated_at="2026-03-13T00:00:00+00:00",
        attempt=0,
        max_attempts=3,
        pr_url="",
        error_message="",
        log_file="job-issue-spec.log",
        started_at="",
        finished_at="",
        app_code="web",
    )


def _build_runtime(*, update_job=None, run_shell=None):
    if update_job is None:
        update_job = lambda *args, **kwargs: None
    if run_shell is None:
        run_shell = lambda **kwargs: SimpleNamespace(stdout="{}", stderr="", exit_code=0)

    settings = SimpleNamespace(
        docker_preview_host="preview.local",
        docker_preview_port_start=3100,
        docker_preview_port_end=3199,
        docker_preview_cors_origins="http://localhost:3000",
    )
    return IssueSpecRuntime(
        settings=settings,
        set_stage=lambda *_args, **_kwargs: None,
        run_shell=run_shell,
        append_actor_log=lambda *_args, **_kwargs: None,
        issue_details_factory=IssueDetails,
        build_spec_markdown=build_spec_markdown,
        build_spec_json=build_spec_json,
        issue_reader=issue_reader,
        repo_context_reader=repo_context_reader,
        risk_policy_checker=risk_policy_checker,
        spec_schema_validator=spec_schema_validator,
        spec_rewriter=spec_rewriter,
        write_stage_contracts_doc=lambda path, json_path: path.write_text(
            f"contracts -> {json_path.name}\n", encoding="utf-8"
        ),
        write_pipeline_analysis_doc=lambda path, json_path: path.write_text(
            f"pipeline -> {json_path.name}\n", encoding="utf-8"
        ),
        update_job=update_job,
    )


def test_stage_read_issue_parses_github_payload(tmp_path: Path):
    job = _make_job()
    runtime = _build_runtime(
        run_shell=lambda **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {
                    "title": "실제 제목",
                    "body": "본문",
                    "url": "https://github.com/owner/repo/issues/12",
                    "labels": [{"name": "agent:run"}, "bug", {"name": ""}],
                }
            ),
            stderr="",
            exit_code=0,
        )
    )

    issue = runtime.stage_read_issue(job, tmp_path, tmp_path / "job.log")

    assert issue.title == "실제 제목"
    assert issue.body == "본문"
    assert issue.url.endswith("/issues/12")
    assert issue.labels == ("agent:run", "bug")


def test_stage_read_issue_raises_on_invalid_json(tmp_path: Path):
    job = _make_job()
    runtime = _build_runtime(
        run_shell=lambda **_kwargs: SimpleNamespace(stdout="not-json", stderr="", exit_code=0)
    )

    with pytest.raises(CommandExecutionError):
        runtime.stage_read_issue(job, tmp_path, tmp_path / "job.log")


def test_stage_write_spec_writes_artifacts_and_updates_job(tmp_path: Path):
    job = _make_job()
    updates: list[tuple[tuple, dict]] = []
    runtime = _build_runtime(
        update_job=lambda *args, **kwargs: updates.append((args, kwargs))
    )
    issue = IssueDetails(
        title="지도 기능 추가",
        body="사용자는 지도를 보고 위치를 확인할 수 있어야 합니다.",
        url="https://github.com/owner/repo/issues/12",
        labels=("feature",),
    )

    paths = runtime.stage_write_spec(job, tmp_path, issue, tmp_path / "job.log")

    assert paths["spec"].exists()
    assert paths["spec_json"].exists()
    assert paths["spec_quality"].exists()
    assert paths["stage_contracts"].exists()
    assert paths["pipeline_analysis"].exists()
    spec_json = json.loads(paths["spec_json"].read_text(encoding="utf-8"))
    assert spec_json["issue"]["title"] == "지도 기능 추가"
    assert "_quality" in spec_json
    assert updates == [((job.job_id,), {"issue_title": issue.title, "issue_url": issue.url})]
