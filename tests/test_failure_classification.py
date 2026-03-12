"""Tests for normalized failure classification."""

from __future__ import annotations

from app.failure_classification import (
    build_failure_classification_summary,
    build_failure_evidence_summary,
    classify_failure,
)
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job() -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id="job-failure-class",
        repository="owner/repo",
        issue_number=1,
        issue_title="failure classification",
        issue_url="https://github.com/owner/repo/issues/1",
        status=JobStatus.FAILED.value,
        stage="failed",
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/issue-1-failure-classification",
        pr_url=None,
        error_message=None,
        log_file="job-failure-class.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )


def test_classify_failure_maps_provider_quota_patterns() -> None:
    failure_class = classify_failure(
        reason="PR summary failed: 402 You have no quota remaining",
        stage="create_pr",
    )

    assert failure_class == "provider_quota"


def test_build_failure_evidence_summary_infers_provider_and_stage_family() -> None:
    summary = build_failure_evidence_summary(
        stage="implement_with_codex",
        reason="request timeout while waiting for provider response",
        source="recovery_runtime",
    )

    assert summary["failure_class"] == "provider_timeout"
    assert summary["provider_hint"] == "codex"
    assert summary["stage_family"] == "implementation"


def test_classify_failure_maps_git_conflict_patterns() -> None:
    failure_class = classify_failure(
        error_message="git push rejected: non-fast-forward update failed",
        stage="push_branch",
    )

    assert failure_class == "git_conflict"


def test_build_failure_classification_summary_prefers_trace_event() -> None:
    job = _make_job()
    job.error_message = "generic failure"
    summary = build_failure_classification_summary(
        job=job,
        runtime_recovery_trace={
            "events": [
                {
                    "generated_at": "2026-03-12T10:00:00+00:00",
                    "reason_code": "stale_heartbeat",
                    "reason": "running heartbeat stale detected",
                    "stage": "implement_with_codex",
                    "source": "worker_stale_recovery",
                    "details": {"stale_seconds": 1803},
                }
            ]
        },
    )

    assert summary["failure_class"] == "stale_heartbeat"
    assert summary["source"] == "runtime_recovery_trace"


def test_build_failure_classification_summary_uses_job_record_without_trace() -> None:
    job = _make_job()
    job.stage = "test_after_fix"
    job.error_message = "playwright snapshot mismatch on mobile viewport"

    summary = build_failure_classification_summary(job=job, runtime_recovery_trace=None)

    assert summary["failure_class"] == "test_failure"
    assert summary["source"] == "job_record"
    assert summary["provider_hint"] == "test_runner"
    assert summary["stage_family"] == "test"
