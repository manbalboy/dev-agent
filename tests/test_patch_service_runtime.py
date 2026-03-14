from __future__ import annotations

from fastapi import HTTPException

from app.models import PatchRunRecord
from app.patch_service_runtime import PatchServiceRuntime
from app.store import SQLiteJobStore


class _FakeServiceManager:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []

    def stop(self, service_name: str) -> None:
        self.operations.append(("stop", service_name))

    def start(self, service_name: str) -> None:
        self.operations.append(("start", service_name))

    def restart(self, service_name: str) -> None:
        self.operations.append(("restart", service_name))


def _make_patch_run() -> PatchRunRecord:
    now = "2026-03-13T10:00:00+09:00"
    return PatchRunRecord(
        patch_run_id="patch-1",
        status="draining",
        repo_root="/tmp/repo",
        branch="master",
        upstream_ref="origin/master",
        source_commit="11112222",
        target_commit="aaaabbbb",
        current_step_key="drain_services",
        current_step_label="서비스 정리",
        current_step_index=3,
        total_steps=6,
        progress_percent=40,
        message="draining",
        requested_by="operator",
        requested_at=now,
        updated_at=now,
    )


def test_patch_service_runtime_blocks_new_jobs_when_lock_active(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=tmp_path / "patch_operation_lock.json",
        api_service_name="agenthub-api",
        worker_service_name="agenthub-worker",
        utc_now_iso=lambda: "2026-03-13T10:00:00+09:00",
    )
    runtime.activate_patch_lock(patch_run=_make_patch_run(), active_jobs=[])

    try:
        runtime.ensure_patch_accepting_new_jobs()
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "patch_run_id=patch-1" in str(exc.detail)
    else:
        raise AssertionError("expected HTTPException")


def test_patch_service_runtime_restart_services_records_stop_restart_order(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    manager = _FakeServiceManager()
    runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=tmp_path / "patch_operation_lock.json",
        api_service_name="agenthub-api",
        worker_service_name="agenthub-worker",
        utc_now_iso=lambda: "2026-03-13T10:00:00+09:00",
        service_manager=manager,
    )

    payload = runtime.restart_services_for_patch()

    assert manager.operations == [
        ("stop", "agenthub-worker"),
        ("restart", "agenthub-api"),
        ("restart", "agenthub-worker"),
    ]
    assert payload["api_service_name"] == "agenthub-api"
    assert payload["worker_service_name"] == "agenthub-worker"
