"""Unit tests for dashboard job action runtime."""

from __future__ import annotations

import json

from app.dashboard import (
    _build_dashboard_job_action_runtime,
    _stop_signal_path,
)
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str, *, status: str = JobStatus.FAILED.value) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=17,
        issue_title="Dashboard action runtime",
        issue_url="https://github.com/owner/repo/issues/17",
        status=status,
        stage=JobStage.FAILED.value if status == JobStatus.FAILED.value else JobStage.QUEUED.value,
        attempt=2,
        max_attempts=3,
        branch_name=f"agenthub/test/{job_id}",
        pr_url=None,
        error_message="boom" if status == JobStatus.FAILED.value else None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=now if status == JobStatus.FAILED.value else None,
    )


def test_request_job_stop_writes_stop_flag(app_components):
    settings, store, _ = app_components
    job = _make_job("job-stop", status=JobStatus.RUNNING.value)
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    store.create_job(job)

    runtime = _build_dashboard_job_action_runtime(store, settings)
    payload = runtime.request_job_stop(job.job_id)

    stop_path = _stop_signal_path(settings.data_dir, job.job_id)
    assert payload["requested"] is True
    assert payload["stop_file"] == str(stop_path)
    assert stop_path.exists() is True


def test_requeue_job_returns_already_active_for_running_job(app_components):
    settings, store, _ = app_components
    job = _make_job("job-running", status=JobStatus.RUNNING.value)
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    store.create_job(job)

    runtime = _build_dashboard_job_action_runtime(store, settings)
    payload = runtime.requeue_job(job.job_id)

    assert payload == {
        "requeued": False,
        "reason": "already_active",
        "job_id": job.job_id,
    }


def test_requeue_job_is_blocked_while_patch_lock_active(app_components):
    settings, store, _ = app_components
    job = _make_job("job-patch-blocked")
    store.create_job(job)
    settings.patch_lock_file.parent.mkdir(parents=True, exist_ok=True)
    settings.patch_lock_file.write_text(
        json.dumps(
            {
                "active": True,
                "patch_run_id": "patch-1",
                "status": "draining",
                "message": "패치 진행 중이라 새 작업 수락이 일시 중지되었습니다.",
                "updated_at": "2026-03-13T10:00:00+09:00",
                "details": {},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime = _build_dashboard_job_action_runtime(store, settings)

    try:
        runtime.requeue_job(job.job_id)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "patch_run_id=patch-1" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected HTTPException")
