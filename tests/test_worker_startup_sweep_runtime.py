"""Tests for worker startup sweep trace artifact helpers."""

from __future__ import annotations

import json

from app.models import JobRecord, JobStatus, NodeRunRecord, utc_now_iso
from app.worker_startup_sweep_runtime import (
    audit_running_node_job_mismatches,
    append_worker_startup_sweep_trace,
    worker_startup_sweep_trace_path,
)


def test_append_worker_startup_sweep_trace_persists_capped_history(app_components) -> None:
    settings, _, _ = app_components

    for index in range(1, 4):
        append_worker_startup_sweep_trace(
            settings,
            orphan_running_node_runs_interrupted=index - 1,
            stale_running_jobs_recovered=index,
            orphan_queued_jobs_recovered=0,
            queue_size_before=0,
            queue_size_after=index,
            details={"sequence": index},
        )

    path = worker_startup_sweep_trace_path(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.exists()
    assert payload["event_count"] == 3
    assert len(payload["events"]) == 3
    assert payload["events"][-1]["stale_running_jobs_recovered"] == 3
    assert payload["events"][-1]["details"]["sequence"] == 3


def test_audit_running_node_job_mismatches_classifies_expected_cases(app_components) -> None:
    _, store, _ = app_components
    now = utc_now_iso()

    running_missing = JobRecord(
        job_id="job-running-missing",
        repository="owner/repo",
        issue_number=1,
        issue_title="running missing",
        issue_url="https://github.com/owner/repo/issues/1",
        status=JobStatus.RUNNING.value,
        stage="implement_with_codex",
        attempt=2,
        max_attempts=3,
        branch_name="agenthub/test/1",
        pr_url=None,
        error_message=None,
        log_file="job-running-missing.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )
    non_running_with_node = JobRecord(
        job_id="job-non-running-node",
        repository="owner/repo",
        issue_number=2,
        issue_title="non running node",
        issue_url="https://github.com/owner/repo/issues/2",
        status=JobStatus.FAILED.value,
        stage="failed",
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/test/2",
        pr_url=None,
        error_message="failed",
        log_file="job-non-running-node.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )
    running_stale = JobRecord(
        job_id="job-running-stale-node",
        repository="owner/repo",
        issue_number=3,
        issue_title="running stale node",
        issue_url="https://github.com/owner/repo/issues/3",
        status=JobStatus.RUNNING.value,
        stage="review_with_gemini",
        attempt=3,
        max_attempts=3,
        branch_name="agenthub/test/3",
        pr_url=None,
        error_message=None,
        log_file="job-running-stale-node.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )
    for job in (running_missing, non_running_with_node, running_stale):
        store.create_job(job)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-non-running",
            job_id=non_running_with_node.job_id,
            workflow_id="wf-default",
            node_id="n8",
            node_type="tester_task",
            node_title="테스트",
            status="running",
            attempt=1,
            started_at=now,
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-running-stale",
            job_id=running_stale.job_id,
            workflow_id="wf-default",
            node_id="n16",
            node_type="review_with_gemini",
            node_title="리뷰",
            status="running",
            attempt=2,
            started_at=now,
        )
    )

    payload = audit_running_node_job_mismatches(store)

    assert payload["total_mismatches"] == 4
    assert payload["counts"]["running_job_missing_current_running_node"] == 2
    assert payload["counts"]["non_running_job_has_running_node_runs"] == 1
    assert payload["counts"]["running_job_has_stale_running_node_attempt"] == 1
