from __future__ import annotations

from app.dashboard_job_list_runtime import DashboardJobListRuntime
from app.models import JobRecord, JobStage, JobStatus


class _FakeStore:
    def __init__(self, jobs: list[JobRecord]) -> None:
        self._jobs = list(jobs)

    def list_jobs(self) -> list[JobRecord]:
        return list(self._jobs)


def _make_job(
    job_id: str,
    *,
    issue_number: int,
    issue_title: str,
    status: str,
    stage: str,
    app_code: str,
    track: str,
    updated_at: str,
    error_message: str | None = None,
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=f"https://github.com/owner/repo/issues/{issue_number}",
        status=status,
        stage=stage,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/{app_code}/issue-{issue_number}",
        pr_url=None,
        error_message=error_message,
        log_file=f"{job_id}.log",
        created_at="2026-03-14T00:00:00+00:00",
        updated_at=updated_at,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
    )


def _build_runtime(jobs: list[JobRecord]) -> DashboardJobListRuntime:
    runtime_signals = {
        "job-a": {
            "strategy": "quality_hardening",
            "resume_mode": "resume_failed_node",
            "review_overall": 4.4,
            "quality_gate_categories": ["test_coverage"],
            "persistent_low_categories": ["ux"],
        },
        "job-b": {
            "strategy": "mvp",
            "resume_mode": "none",
            "review_overall": 3.8,
            "maturity_level": "mvp",
        },
        "job-c": {
            "strategy": "",
            "resume_mode": "none",
            "review_overall": None,
        },
    }
    failure_classifications = {
        "job-a": {
            "failure_class": "git_conflict",
            "provider_hint": "codex",
            "stage_family": "implementation",
        },
        "job-b": {
            "failure_class": "",
            "provider_hint": "",
            "stage_family": "",
        },
        "job-c": {
            "failure_class": "",
            "provider_hint": "",
            "stage_family": "",
        },
    }
    return DashboardJobListRuntime(
        store=_FakeStore(jobs),
        track_choices=["bug", "enhance", "enhance"],
        build_job_runtime_signals=lambda job: runtime_signals.get(
            job.job_id,
            {"strategy": "", "resume_mode": "none", "review_overall": None},
        ),
        build_failure_classification_summary=lambda job: failure_classifications.get(
            job.job_id,
            {"failure_class": "", "provider_hint": "", "stage_family": ""},
        ),
    )


def test_dashboard_job_list_runtime_builds_paginated_payload() -> None:
    runtime = _build_runtime(
        [
            _make_job(
                "job-a",
                issue_number=101,
                issue_title="Failure during review hardening",
                status=JobStatus.FAILED.value,
                stage=JobStage.PRODUCT_REVIEW.value,
                app_code="admin",
                track="bug",
                updated_at="2026-03-14T03:00:00+00:00",
                error_message="git conflict while rebasing",
            ),
            _make_job(
                "job-b",
                issue_number=102,
                issue_title="Shipping dashboard filter work",
                status=JobStatus.RUNNING.value,
                stage=JobStage.IMPLEMENT_WITH_CODEX.value,
                app_code="web",
                track="enhance",
                updated_at="2026-03-14T02:00:00+00:00",
            ),
            _make_job(
                "job-c",
                issue_number=103,
                issue_title="Queued backlog item",
                status=JobStatus.QUEUED.value,
                stage=JobStage.QUEUED.value,
                app_code="default",
                track="enhance",
                updated_at="2026-03-14T01:00:00+00:00",
            ),
        ]
    )

    payload = runtime.list_jobs_payload(
        page=1,
        page_size=2,
        status="",
        track="",
        app_code="",
        stage="",
        recovery_status="",
        strategy="",
        q="",
    )

    assert [item["job_id"] for item in payload["jobs"]] == ["job-a", "job-b"]
    assert payload["summary"] == {
        "total": 3,
        "queued": 1,
        "running": 1,
        "done": 0,
        "failed": 1,
    }
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total_items": 3,
        "total_pages": 2,
        "has_prev": False,
        "has_next": True,
        "start_index": 1,
        "end_index": 2,
    }
    assert payload["jobs"][0]["failure_class"] == "git_conflict"
    assert payload["filter_options"]["tracks"] == ["bug", "enhance"]
    assert payload["filter_options"]["strategies"] == ["mvp", "quality_hardening"]


def test_dashboard_job_list_runtime_filters_by_runtime_query_terms() -> None:
    runtime = _build_runtime(
        [
            _make_job(
                "job-a",
                issue_number=101,
                issue_title="Failure during review hardening",
                status=JobStatus.FAILED.value,
                stage=JobStage.PRODUCT_REVIEW.value,
                app_code="admin",
                track="bug",
                updated_at="2026-03-14T03:00:00+00:00",
            ),
            _make_job(
                "job-b",
                issue_number=102,
                issue_title="Shipping dashboard filter work",
                status=JobStatus.RUNNING.value,
                stage=JobStage.IMPLEMENT_WITH_CODEX.value,
                app_code="web",
                track="enhance",
                updated_at="2026-03-14T02:00:00+00:00",
            ),
        ]
    )

    payload = runtime.list_jobs_payload(
        page=1,
        page_size=10,
        status="",
        track="",
        app_code="",
        stage="",
        recovery_status="",
        strategy="",
        q="test_coverage",
    )

    assert [item["job_id"] for item in payload["jobs"]] == ["job-a"]
    assert payload["filtered_summary"]["total"] == 1
    assert payload["filters"]["applied"] is True


def test_dashboard_job_list_runtime_returns_compact_job_options() -> None:
    long_title = "Thumbnail regeneration " + ("x" * 90)
    runtime = _build_runtime(
        [
            _make_job(
                "job-thumbnail-runtime",
                issue_number=201,
                issue_title=long_title,
                status=JobStatus.RUNNING.value,
                stage=JobStage.IMPLEMENT_WITH_CODEX.value,
                app_code="web",
                track="enhance",
                updated_at="2026-03-14T04:00:00+00:00",
            )
        ]
    )

    payload = runtime.get_job_options_payload(q="thumbnail", limit=1)

    assert payload["query"] == "thumbnail"
    assert payload["limit"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["job_id"] == "job-thumbnail-runtime"
    assert payload["items"][0]["label"].startswith("job-thum | running | #201 ")
    assert "..." in payload["items"][0]["label"]
    assert payload["items"][0]["issue_title"] == long_title
