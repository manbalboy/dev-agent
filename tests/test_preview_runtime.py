"""Tests for preview runtime extraction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models import JobRecord, JobStatus, utc_now_iso
from app.preview_runtime import PreviewRuntime


def _make_job(job_id: str = "job-preview-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="preview runtime",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-88-preview",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="food",
    )


def _build_runtime(*, settings) -> PreviewRuntime:
    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return PreviewRuntime(
        settings=settings,
        run_shell=lambda **kwargs: None,
        execute_shell_command=lambda **kwargs: None,
        actor_log_writer=lambda log_path, actor: lambda message: None,
        append_actor_log=lambda log_path, actor, message: None,
        docs_file=docs_file,
    )


def test_preview_runtime_detects_exposed_container_port(tmp_path: Path) -> None:
    runtime = _build_runtime(
        settings=SimpleNamespace(
            docker_preview_enabled=False,
            docker_preview_cors_origins="http://localhost",
            docker_preview_port_start=7000,
            docker_preview_port_end=7099,
            docker_preview_container_port=3000,
            docker_preview_host="localhost",
            docker_preview_health_path="/",
        )
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    (repository_path / "Dockerfile").write_text("FROM node:20\nEXPOSE 4321\n", encoding="utf-8")

    assert runtime.detect_container_port(repository_path) == 4321


def test_preview_runtime_writes_skipped_markdown_when_disabled(tmp_path: Path) -> None:
    runtime = _build_runtime(
        settings=SimpleNamespace(
            docker_preview_enabled=False,
            docker_preview_cors_origins="http://localhost",
            docker_preview_port_start=7000,
            docker_preview_port_end=7099,
            docker_preview_container_port=3000,
            docker_preview_host="localhost",
            docker_preview_health_path="/",
        )
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    info = runtime.deploy_preview_and_smoke_test(
        _make_job(),
        repository_path,
        tmp_path / "job.log",
    )

    preview_path = repository_path / "_docs" / "PREVIEW.md"
    assert info["status"] == "skipped"
    assert "disabled" in info["reason"].lower()
    assert preview_path.exists()
    assert "Docker preview is disabled" in preview_path.read_text(encoding="utf-8")


def test_preview_runtime_appends_preview_section_to_existing_body(tmp_path: Path) -> None:
    runtime = _build_runtime(
        settings=SimpleNamespace(
            docker_preview_enabled=False,
            docker_preview_cors_origins="http://localhost",
            docker_preview_port_start=7000,
            docker_preview_port_end=7099,
            docker_preview_container_port=3000,
            docker_preview_host="localhost",
            docker_preview_health_path="/",
        )
    )
    pr_body_path = tmp_path / "PR_BODY.md"
    pr_body_path.write_text("# Summary\n", encoding="utf-8")

    runtime.append_preview_section_to_pr_body(
        pr_body_path,
        {
            "status": "running",
            "container_name": "agenthub-preview-1234",
            "port": "7001",
            "container_port": "3000",
            "external_url": "http://localhost:7001",
            "health_url": "http://127.0.0.1:7001/",
            "cors_origins": "http://localhost",
            "reason": "Preview container is reachable.",
        },
    )

    content = pr_body_path.read_text(encoding="utf-8")
    assert "# Summary" in content
    assert "## Deployment Preview" in content
    assert "agenthub-preview-1234" in content
