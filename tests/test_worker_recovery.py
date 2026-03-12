"""Tests for stale running auto-recovery in worker loop."""

from __future__ import annotations

import json

from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.worker_main import _cleanup_orphan_running_node_runs, _recover_stale_running_jobs, _run_startup_sweep


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
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-stale-1",
            job_id=job.job_id,
            workflow_id="wf-default",
            node_id="n12",
            node_type="implement_with_codex",
            node_title="구현",
            status="running",
            attempt=1,
            started_at="2026-03-08T00:00:10+00:00",
        )
    )

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
    node_runs = store.list_node_runs(job.job_id)
    assert len(node_runs) == 1
    assert node_runs[0].status == "interrupted"
    assert "running heartbeat stale detected" in (node_runs[0].error_message or "")
    assert node_runs[0].finished_at is not None
    trace_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["event_count"] == 1
    assert trace_payload["events"][0]["decision"] == "requeue"
    assert trace_payload["events"][0]["reason_code"] == "stale_heartbeat"
    assert trace_payload["events"][0]["failure_class"] == "stale_heartbeat"
    assert trace_payload["events"][0]["provider_hint"] == "runtime"
    assert trace_payload["events"][0]["stage_family"] == "runtime_recovery"
    assert trace_payload["events"][0]["requeue_reason_summary"]["active"] is True
    assert trace_payload["events"][0]["requeue_reason_summary"]["trigger"] == "worker_restart_or_stale_recovery"


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
    trace_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "needs_human"
    assert trace_payload["events"][0]["recovery_status"] == "needs_human"
    assert trace_payload["events"][0]["failure_class"] == "stale_heartbeat"
    assert trace_payload["events"][0]["provider_hint"] == "runtime"
    assert trace_payload["events"][0]["stage_family"] == "runtime_recovery"
    assert trace_payload["events"][0]["details"]["effective_retry_budget"] == 1
    assert trace_payload["events"][0]["needs_human_summary"]["failure_class"] == "stale_heartbeat"


def test_stale_running_job_uses_class_aware_budget_before_settings_limit(app_components):
    settings, store, _ = app_components
    job = _make_running_job(
        "job-stale-policy-needs-human",
        heartbeat_at="2026-03-08T00:00:00+00:00",
        recovery_count=1,
    )
    store.create_job(job)

    recovered = _recover_stale_running_jobs(store, settings)

    assert recovered == 0
    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED.value
    assert stored.recovery_status == "needs_human"
    assert stored.recovery_count == 2
    trace_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "needs_human"
    assert trace_payload["events"][0]["details"]["effective_retry_budget"] == 1
    assert trace_payload["events"][0]["details"]["retry_policy"]["failure_class"] == "stale_heartbeat"
    assert trace_payload["events"][0]["needs_human_summary"]["manual_resume_recommended"] is True


def test_cleanup_orphan_running_node_runs_interrupts_non_running_jobs(app_components):
    _, store, _ = app_components
    job = _make_running_job("job-node-orphan", heartbeat_at="2026-03-08T00:00:00+00:00")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.FAILED.value
    store.create_job(job)
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-orphan-1",
            job_id=job.job_id,
            workflow_id="wf-default",
            node_id="n9",
            node_type="tester_task",
            node_title="테스트",
            status="running",
            attempt=1,
            started_at="2026-03-08T00:00:10+00:00",
        )
    )

    cleaned = _cleanup_orphan_running_node_runs(store)

    assert cleaned == 1
    node_runs = store.list_node_runs(job.job_id)
    assert len(node_runs) == 1
    assert node_runs[0].status == "interrupted"
    assert "job status is failed" in (node_runs[0].error_message or "")
    assert node_runs[0].finished_at is not None


def test_run_startup_sweep_records_trace_summary(app_components):
    settings, store, _ = app_components
    running_job = _make_running_job("job-startup-running", heartbeat_at="2026-03-08T00:00:00+00:00")
    queued_job = _make_running_job("job-startup-queued", heartbeat_at="2026-03-08T00:00:00+00:00")
    queued_job.status = JobStatus.QUEUED.value
    queued_job.stage = JobStage.QUEUED.value
    queued_job.heartbeat_at = None
    stuck_job = _make_running_job("job-startup-stuck", heartbeat_at=utc_now_iso())
    store.create_job(running_job)
    store.create_job(queued_job)
    store.create_job(stuck_job)

    summary = _run_startup_sweep(store, settings)

    assert summary["orphan_running_node_runs_interrupted"] == 0
    assert summary["stale_running_jobs_recovered"] == 1
    assert summary["orphan_queued_jobs_recovered"] == 0
    assert summary["running_node_job_mismatches_detected"] >= 1
    assert summary["running_node_job_mismatches_remaining"] >= 1
    trace_path = settings.data_dir / "worker_startup_sweep_trace.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["event_count"] == 1
    event = payload["events"][0]
    assert event["stale_running_jobs_recovered"] == 1
    assert event["orphan_queued_jobs_recovered"] == 0
    assert event["running_node_job_mismatches_detected"] >= 1
    assert event["running_node_job_mismatches_remaining"] >= 1
    assert event["queue_size_after"] >= 1
    assert event["details"]["worker_stale_running_seconds"] == settings.worker_stale_running_seconds
    assert event["details"]["mismatch_audit_before"]["total_mismatches"] >= 1
    assert event["details"]["mismatch_audit_after"]["total_mismatches"] >= 1
