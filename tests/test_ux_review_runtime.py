"""Tests for UX review runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.models import JobRecord, JobStatus, utc_now_iso
from app.ux_review_runtime import UxReviewRuntime


def _make_job(job_id: str = "job-ux-review-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=89,
        issue_title="ux review runtime",
        issue_url="https://github.com/owner/repo/issues/89",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-89-ux-review",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="food",
    )


def _build_runtime(*, tests_passed: bool = True, preview_info: dict[str, str] | None = None) -> UxReviewRuntime:
    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return UxReviewRuntime(
        stage_run_tests=lambda **kwargs: tests_passed,
        deploy_preview_and_smoke_test=lambda job, repository_path, log_path: preview_info or {},
        run_shell=lambda **kwargs: None,
        append_actor_log=lambda log_path, actor, message: None,
        docs_file=docs_file,
    )


def test_ux_review_runtime_extracts_spec_checklist(tmp_path: Path) -> None:
    spec_path = tmp_path / "SPEC.md"
    spec_path.write_text(
        "# SPEC\n\n- 로그인 화면\n- 에러 상태\n1. 오프라인 처리\n",
        encoding="utf-8",
    )

    checklist = UxReviewRuntime.extract_spec_checklist(spec_path)

    assert checklist == ["로그인 화면", "에러 상태", "1. 오프라인 처리"]


def test_ux_review_runtime_skips_screenshots_when_preview_missing(tmp_path: Path) -> None:
    runtime = _build_runtime()
    repository_path = tmp_path / "repo"
    repository_path.mkdir()

    results = runtime.capture_ux_screenshots(
        repository_path=repository_path,
        preview_info={},
        log_path=tmp_path / "job.log",
    )

    assert results["pc"]["status"] == "skipped"
    assert results["mobile"]["status"] == "skipped"


def test_ux_review_runtime_writes_markdown_summary(tmp_path: Path) -> None:
    runtime = _build_runtime()
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    spec_path = repository_path / "_docs" / "SPEC.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# SPEC\n\n- 홈 화면\n- 빈 상태 안내\n", encoding="utf-8")

    runtime.write_ux_review_markdown(
        repository_path=repository_path,
        spec_path=spec_path,
        preview_info={
            "external_url": "http://localhost:7001",
            "health_url": "http://127.0.0.1:7001/",
        },
        screenshot_info={
            "pc": {"status": "captured", "path": "artifacts/ux/pc.png", "note": "Desktop Chrome capture completed"},
            "mobile": {"status": "captured", "path": "artifacts/ux/mobile.png", "note": "iPhone 13 capture completed"},
        },
        tests_passed=True,
    )

    content = (repository_path / "_docs" / "UX_REVIEW.md").read_text(encoding="utf-8")
    assert "Verdict: `PASS`" in content
    assert "artifacts/ux/pc.png" in content
    assert "홈 화면" in content
    assert "빈 상태 안내" in content
