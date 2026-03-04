"""Tests for JSON store and queue behavior."""

from __future__ import annotations

from app.models import JobRecord, JobStage, JobStatus, utc_now_iso



def _make_job(job_id: str = "job-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_title="Sample issue",
        issue_url="https://github.com/owner/repo/issues/1",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-1-job1",
        pr_url=None,
        error_message=None,
        log_file="job-1.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_store_create_update_list_and_queue(app_components):
    _, store, _ = app_components

    first = _make_job("job-1")
    second = _make_job("job-2")

    store.create_job(first)
    store.create_job(second)

    store.enqueue_job("job-1")
    store.enqueue_job("job-2")

    assert store.queue_size() == 2
    assert store.dequeue_job() == "job-1"
    assert store.dequeue_job() == "job-2"
    assert store.dequeue_job() is None

    updated = store.update_job("job-1", status=JobStatus.RUNNING.value)
    assert updated.status == JobStatus.RUNNING.value

    jobs = store.list_jobs()
    assert len(jobs) == 2
    assert {item.job_id for item in jobs} == {"job-1", "job-2"}
