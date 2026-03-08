"""Tests for stale running auto-recovery in worker loop."""

from __future__ import annotations

from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.worker_main import _recover_stale_running_jobs


def _make_running_job(job_id: str, *, heartbeat_at: str, recovery_count: int = 0) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="stale recovery",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.RUNNING.value,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/issue-77-stale",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=heartbeat_at,
        started_at=now,
        finished_at=None,
        heartbeat_at=heartbeat_at,
        recovery_count=recovery_count,
    )


def test_stale_running_job_is_auto_requeued(app_components):
    settings, store, _ = app_components
    job = _make_running_job("job-stale-recover", heartbeat_at="2026-03-08T00:00:00+00:00")
    store.create_job(job)

    recovered = _recover_stale_running_jobs(store, settings)

    assert recovered == 1
    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.QUEUED.value
    assert stored.stage == JobStage.QUEUED.value
    assert stored.recovery_status == "auto_recovered"
    assert stored.recovery_count == 1
    assert "running heartbeat stale detected" in (stored.recovery_reason or "")
    assert store.queue_size() == 1
    assert store.dequeue_job() == job.job_id


def test_stale_running_job_stops_after_recovery_limit(app_components):
    settings, store, _ = app_components
    job = _make_running_job(
        "job-stale-needs-human",
        heartbeat_at="2026-03-08T00:00:00+00:00",
        recovery_count=settings.worker_max_auto_recoveries,
    )
    store.create_job(job)

    recovered = _recover_stale_running_jobs(store, settings)

    assert recovered == 0
    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED.value
    assert stored.stage == JobStage.FAILED.value
    assert stored.recovery_status == "needs_human"
    assert stored.recovery_count == settings.worker_max_auto_recoveries + 1
    assert store.queue_size() == 0
