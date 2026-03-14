from __future__ import annotations

import json

from app.models import JobRecord, JobStage, JobStatus
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.store import SQLiteJobStore


class _FakeServiceManager:
    def __init__(self, states: dict[str, bool]) -> None:
        self.states = dict(states)

    def stop(self, service_name: str) -> None:  # pragma: no cover - not used
        self.states[service_name] = False

    def start(self, service_name: str) -> None:  # pragma: no cover - not used
        self.states[service_name] = True

    def restart(self, service_name: str) -> None:  # pragma: no cover - not used
        self.states[service_name] = True

    def is_active(self, service_name: str) -> bool:
        return bool(self.states.get(service_name))


def _make_job(job_id: str, *, status: str) -> JobRecord:
    now = "2026-03-13T10:00:00+09:00"
    stage = JobStage.IMPLEMENT_WITH_CODEX.value if status == JobStatus.RUNNING.value else JobStage.QUEUED.value
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_title="Patch health",
        issue_url="https://github.com/owner/repo/issues/1",
        status=status,
        stage=stage,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/default/issue-1",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_patch_health_runtime_reports_healthy_payload(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    store.create_job(_make_job("job-queued", status=JobStatus.QUEUED.value))
    updater_status_file = tmp_path / "patch_updater_status.json"
    updater_status_file.write_text(
        json.dumps(
            {
                "service_name": "agenthub-updater",
                "status": "tracking",
                "active_patch_run_id": "patch-1",
            }
        ),
        encoding="utf-8",
    )
    patch_service_runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=tmp_path / "patch_operation_lock.json",
        api_service_name="agenthub-api",
        worker_service_name="agenthub-worker",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        service_manager=_FakeServiceManager({"agenthub-worker": True, "agenthub-updater": True}),
    )
    patch_service_runtime.clear_patch_lock(status="restart_completed", message="ok")
    runtime = PatchHealthRuntime(
        store=store,
        patch_service_runtime=patch_service_runtime,
        api_health_url="http://127.0.0.1:8321/healthz",
        updater_status_file=updater_status_file,
        updater_service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:12:00+09:00",
        http_get=lambda _url: {"status_code": 200, "body": {"status": "ok"}},
    )

    payload = runtime.build_post_update_health_payload()

    assert payload["ok"] is True
    assert payload["failed_checks"] == []
    assert payload["checks"]["api"]["ok"] is True
    assert payload["checks"]["worker"]["active"] is True
    assert payload["checks"]["patch_lock"]["active"] is False


def test_patch_health_runtime_reports_failed_checks(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    updater_status_file = tmp_path / "patch_updater_status.json"
    patch_service_runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=tmp_path / "patch_operation_lock.json",
        api_service_name="agenthub-api",
        worker_service_name="agenthub-worker",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        service_manager=_FakeServiceManager({"agenthub-worker": False, "agenthub-updater": False}),
    )
    patch_service_runtime.activate_patch_lock(
        patch_run=type("PatchRun", (), {"patch_run_id": "patch-1"})(),  # type: ignore[arg-type]
        active_jobs=[],
    )
    runtime = PatchHealthRuntime(
        store=store,
        patch_service_runtime=patch_service_runtime,
        api_health_url="http://127.0.0.1:8321/healthz",
        updater_status_file=updater_status_file,
        updater_service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:12:00+09:00",
        http_get=lambda _url: {"status_code": 503, "body": {"status": "down"}},
    )

    payload = runtime.build_post_update_health_payload()

    assert payload["ok"] is False
    assert "api" in payload["failed_checks"]
    assert "worker" in payload["failed_checks"]
    assert "patch_lock" in payload["failed_checks"]
    assert "updater_status" in payload["failed_checks"]
