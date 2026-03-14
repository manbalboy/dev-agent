from __future__ import annotations

import json

from app.models import JobRecord, JobStage, JobStatus, PatchRunRecord
from app.patch_service_runtime import PatchServiceRuntime
from app.patch_updater_runtime import PatchUpdaterRuntime
from app.store import SQLiteJobStore


class _FakeServiceManager:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []
        self.active_services: dict[str, bool] = {}

    def stop(self, service_name: str) -> None:
        self.operations.append(("stop", service_name))

    def start(self, service_name: str) -> None:
        self.operations.append(("start", service_name))

    def restart(self, service_name: str) -> None:
        self.operations.append(("restart", service_name))
        self.active_services[service_name] = True

    def is_active(self, service_name: str) -> bool:
        return bool(self.active_services.get(service_name))


class _FakePatchHealthRuntime:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def build_post_update_health_payload(self) -> dict:
        self.calls += 1
        return dict(self.payload)


class _FakePatchRollbackRuntime:
    def __init__(self, payload: dict | None = None, *, error: str = "") -> None:
        self.payload = payload or {}
        self.error = error
        self.calls: list[dict[str, str]] = []

    def rollback_to_commit(self, *, repo_root: str, branch: str, target_commit: str) -> dict:
        self.calls.append(
            {
                "repo_root": repo_root,
                "branch": branch,
                "target_commit": target_commit,
            }
        )
        if self.error:
            raise RuntimeError(self.error)
        return dict(self.payload)


class _FakePatchBackupRuntime:
    def __init__(self, payload: dict | None = None, *, error: str = "") -> None:
        self.payload = payload or {}
        self.error = error
        self.calls: list[dict[str, str]] = []
        self.verify_calls: list[str] = []
        self.restore_calls: list[str] = []

    def create_backup(
        self,
        *,
        patch_run_id: str,
        repo_root: str,
        branch: str,
        source_commit: str,
        target_commit: str,
        reason: str,
    ) -> dict:
        self.calls.append(
            {
                "patch_run_id": patch_run_id,
                "repo_root": repo_root,
                "branch": branch,
                "source_commit": source_commit,
                "target_commit": target_commit,
                "reason": reason,
            }
        )
        if self.error:
            raise RuntimeError(self.error)
        return {
            "backup_id": "backup-1",
            "backup_dir": "/tmp/backup-1",
            "manifest_path": "/tmp/backup-1/manifest.json",
            "created_at": "2026-03-13T10:10:00+09:00",
            "file_count": 3,
            "files": [
                {
                    "relative_path": "jobs.json",
                    "source_path": "/tmp/runtime/jobs.json",
                    "destination_path": "/tmp/backup-1/jobs.json",
                }
            ],
            **self.payload,
        }

    def verify_backup_manifest(self, *, manifest: dict) -> dict:
        self.verify_calls.append(str(manifest.get("backup_id") or ""))
        if self.error:
            return {
                "ok": False,
                "backup_id": str(manifest.get("backup_id") or ""),
                "summary": self.error,
                "files": [],
            }
        return {
            "ok": True,
            "backup_id": str(manifest.get("backup_id") or "backup-1"),
            "summary": "백업 검증 완료",
            "files": [
                {
                    "relative_path": "jobs.json",
                    "runtime_destination_path": "/tmp/runtime/jobs.json",
                    "backup_source_path": "/tmp/backup-1/jobs.json",
                    "size_bytes": 12,
                }
            ],
            **({"summary": self.payload["verify_summary"]} if "verify_summary" in self.payload else {}),
        }

    def restore_backup(self, *, manifest: dict) -> dict:
        self.restore_calls.append(str(manifest.get("backup_id") or ""))
        if self.error:
            raise RuntimeError(self.error)
        return {
            "backup_id": str(manifest.get("backup_id") or "backup-1"),
            "restored_at": "2026-03-13T10:12:30+09:00",
            "restored_file_count": 1,
            "files": [
                {
                    "relative_path": "jobs.json",
                    "destination_path": "/tmp/runtime/jobs.json",
                    "size_bytes": 12,
                }
            ],
        }


def _make_job(job_id: str, *, status: str) -> JobRecord:
    now = "2026-03-13T10:00:00+09:00"
    stage = JobStage.IMPLEMENT_WITH_CODEX.value if status == JobStatus.RUNNING.value else JobStage.QUEUED.value
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=17,
        issue_title="Patch drain target",
        issue_url="https://github.com/owner/repo/issues/17",
        status=status,
        stage=stage,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/default/issue-17",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _make_patch_run(status: str, *, details: dict | None = None) -> PatchRunRecord:
    now = "2026-03-13T10:10:00+09:00"
    return PatchRunRecord(
        patch_run_id="patch-1",
        status=status,
        repo_root="/tmp/repo",
        branch="master",
        upstream_ref="origin/master",
        source_commit="11112222",
        target_commit="aaaabbbb",
        current_step_key="waiting_updater" if status == "waiting_updater" else "drain_services",
        current_step_label="업데이트 대기" if status == "waiting_updater" else "서비스 정리",
        current_step_index=2 if status == "waiting_updater" else 3,
        total_steps=6,
        progress_percent=20 if status == "waiting_updater" else 40,
        message="대기 중",
        requested_by="operator",
        requested_at=now,
        updated_at=now,
        details={
            "steps": [
                {"key": "approval_recorded", "label": "승인 기록"},
                {"key": "waiting_updater", "label": "업데이트 대기"},
                {"key": "drain_services", "label": "서비스 정리"},
                {"key": "update_code", "label": "코드 업데이트"},
                {"key": "restart_services", "label": "서비스 재기동"},
                {"key": "verify_health", "label": "상태 확인"},
            ],
            **(details or {}),
        },
    )


def _build_patch_service_runtime(store: SQLiteJobStore, tmp_path, *, manager: _FakeServiceManager | None = None):
    return PatchServiceRuntime(
        store=store,
        patch_lock_file=tmp_path / "patch_operation_lock.json",
        api_service_name="agenthub-api",
        worker_service_name="agenthub-worker",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        service_manager=manager,
    )


def test_patch_updater_runtime_reads_offline_payload_when_status_missing(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:00:00+09:00",
        patch_service_runtime=_build_patch_service_runtime(store, tmp_path),
        pid_provider=lambda: 4242,
    )

    payload = runtime.read_status_payload()

    assert payload["status"] == "offline"
    assert payload["service_name"] == "agenthub-updater"
    assert payload["active_patch_run_id"] == ""


def test_patch_updater_runtime_claims_waiting_patch_run_and_writes_lock_when_jobs_active(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    store.create_job(_make_job("job-queued", status=JobStatus.QUEUED.value))
    store.upsert_patch_run(
        _make_patch_run(
            "waiting_updater",
            details={
                "patch_status": {
                    "working_tree_dirty": False,
                    "ahead_count": 0,
                }
            },
        )
    )
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path)
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        patch_service_runtime=patch_service_runtime,
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    assert result["status"]["status"] == "tracking"
    assert result["status"]["active_patch_run_id"] == "patch-1"
    assert result["status"]["details"]["next_action"] == "wait_for_job_drain"
    saved_status = json.loads((tmp_path / "patch_updater_status.json").read_text(encoding="utf-8"))
    assert saved_status["pid"] == 4242
    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "draining"
    assert patch_run.details["next_action"] == "wait_for_job_drain"
    assert patch_run.details["active_job_count"] == 1
    lock_payload = patch_service_runtime.read_patch_lock_payload()
    assert lock_payload["active"] is True
    assert lock_payload["status"] == "draining"
    assert lock_payload["patch_run_id"] == "patch-1"


def test_patch_updater_runtime_transitions_draining_run_to_restart_when_jobs_drained(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    manager = _FakeServiceManager()
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=manager)
    patch_service_runtime.activate_patch_lock(
        patch_run=_make_patch_run("draining"),
        active_jobs=[],
    )
    store.upsert_patch_run(
        _make_patch_run(
            "draining",
            details={
                "patch_status": {
                    "working_tree_dirty": False,
                    "ahead_count": 0,
                }
            },
        )
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_backup_runtime=_FakePatchBackupRuntime(),
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "verifying"
    assert patch_run.details["next_action"] == "run_post_update_health_check"
    assert patch_run.details["update_step_skipped"] is True
    assert patch_run.details["backup_manifest"]["backup_id"] == "backup-1"
    assert manager.operations == [
        ("stop", "agenthub-worker"),
        ("restart", "agenthub-api"),
        ("restart", "agenthub-worker"),
    ]
    lock_payload = patch_service_runtime.read_patch_lock_payload()
    assert lock_payload["active"] is False
    assert lock_payload["status"] == "restart_completed"
    assert result["status"]["details"]["next_action"] == "run_post_update_health_check"


def test_patch_updater_runtime_fails_when_backup_creation_fails(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    manager = _FakeServiceManager()
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=manager)
    patch_service_runtime.activate_patch_lock(
        patch_run=_make_patch_run("draining"),
        active_jobs=[],
    )
    store.upsert_patch_run(
        _make_patch_run(
            "draining",
            details={
                "patch_status": {
                    "working_tree_dirty": False,
                    "ahead_count": 0,
                }
            },
        )
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_backup_runtime=_FakePatchBackupRuntime(error="disk full"),
        pid_provider=lambda: 4242,
    )

    runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "failed"
    assert "백업 생성에 실패" in patch_run.message
    lock_payload = patch_service_runtime.read_patch_lock_payload()
    assert lock_payload["active"] is False
    assert lock_payload["status"] == "failed"


def test_patch_updater_runtime_marks_patch_done_when_health_check_passes(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=_FakeServiceManager())
    store.upsert_patch_run(_make_patch_run("verifying", details={"next_action": "run_post_update_health_check"}))
    health_runtime = _FakePatchHealthRuntime(
        {
            "ok": True,
            "checked_at": "2026-03-13T10:11:00+09:00",
            "summary": "패치 후 API/worker/queue 상태가 모두 정상입니다.",
            "checks": {},
            "failed_checks": [],
        }
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:11:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_health_runtime=health_runtime,
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "done"
    assert patch_run.progress_percent == 100
    assert patch_run.details["health_check"]["ok"] is True
    assert result["status"]["details"]["next_action"] == ""
    assert health_runtime.calls == 1


def test_patch_updater_runtime_marks_patch_failed_when_health_check_fails(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=_FakeServiceManager())
    store.upsert_patch_run(_make_patch_run("verifying", details={"next_action": "run_post_update_health_check"}))
    health_runtime = _FakePatchHealthRuntime(
        {
            "ok": False,
            "checked_at": "2026-03-13T10:11:00+09:00",
            "summary": "패치 후 상태 확인 실패: api, worker",
            "checks": {},
            "failed_checks": ["api", "worker"],
        }
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:11:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_health_runtime=health_runtime,
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "failed"
    assert patch_run.details["next_action"] == "manual_post_update_check_required"
    assert patch_run.details["health_check"]["failed_checks"] == ["api", "worker"]
    assert "패치 후 상태 확인 실패" in result["status"]["message"]
    assert health_runtime.calls == 1


def test_patch_updater_runtime_fails_dirty_patch_before_drain(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    store.upsert_patch_run(
        _make_patch_run(
            "waiting_updater",
            details={
                "patch_status": {
                    "working_tree_dirty": True,
                    "ahead_count": 0,
                }
            },
        )
    )
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path)
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:10:00+09:00",
        patch_service_runtime=patch_service_runtime,
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "failed"
    assert patch_run.details["failure_boundary"] == "dirty_working_tree"
    assert result["status"]["active_patch_run_id"] == "patch-1"
    lock_payload = patch_service_runtime.read_patch_lock_payload()
    assert lock_payload["active"] is False
    assert lock_payload["status"] == "failed"


def test_patch_updater_runtime_executes_requested_rollback_and_marks_rolled_back(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    manager = _FakeServiceManager()
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=manager)
    patch_service_runtime.clear_patch_lock(status="idle", message="ok")
    store.upsert_patch_run(
        _make_patch_run(
            "rollback_requested",
            details={
                "steps": [
                    {"key": "rollback_requested", "label": "롤백 승인"},
                    {"key": "rollback_code", "label": "코드 롤백"},
                    {"key": "verify_rollback", "label": "롤백 상태 확인"},
                ],
                "next_action": "rollback_requested",
            },
        )
    )
    rollback_runtime = _FakePatchRollbackRuntime(
        {
            "target_commit": "11112222",
            "resulting_commit": "11112222",
            "completed_at": "2026-03-13T10:12:00+09:00",
            "operations": [{"action": "checkout_branch", "value": "master"}],
        }
    )
    health_runtime = _FakePatchHealthRuntime(
        {
            "ok": True,
            "checked_at": "2026-03-13T10:13:00+09:00",
            "summary": "롤백 후 정상",
            "checks": {},
            "failed_checks": [],
        }
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:13:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_health_runtime=health_runtime,
        patch_rollback_runtime=rollback_runtime,
        patch_backup_runtime=_FakePatchBackupRuntime(),
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "rolled_back"
    assert patch_run.details["rollback_payload"]["target_commit"] == "11112222"
    assert rollback_runtime.calls == [
        {"repo_root": "/tmp/repo", "branch": "master", "target_commit": "11112222"}
    ]
    assert manager.operations == [
        ("stop", "agenthub-worker"),
        ("restart", "agenthub-api"),
        ("restart", "agenthub-worker"),
    ]
    assert result["status"]["details"]["next_action"] == ""


def test_patch_updater_runtime_marks_rollback_failed_when_rollback_step_raises(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=_FakeServiceManager())
    store.upsert_patch_run(
        _make_patch_run(
            "rollback_requested",
            details={
                "steps": [
                    {"key": "rollback_requested", "label": "롤백 승인"},
                    {"key": "rollback_code", "label": "코드 롤백"},
                    {"key": "verify_rollback", "label": "롤백 상태 확인"},
                ],
                "next_action": "rollback_requested",
            },
        )
    )
    rollback_runtime = _FakePatchRollbackRuntime(error="checkout failed")
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:13:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_rollback_runtime=rollback_runtime,
        patch_backup_runtime=_FakePatchBackupRuntime(),
        pid_provider=lambda: 4242,
    )

    runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "rollback_failed"
    assert patch_run.details["next_action"] == "manual_rollback_required"


def test_patch_updater_runtime_executes_requested_restore_and_marks_restored(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    manager = _FakeServiceManager()
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=manager)
    patch_service_runtime.clear_patch_lock(status="idle", message="ok")
    store.upsert_patch_run(
        _make_patch_run(
            "restore_requested",
            details={
                "steps": [
                    {"key": "restore_requested", "label": "복원 승인"},
                    {"key": "restore_state", "label": "백업 복원"},
                    {"key": "verify_restore", "label": "복원 상태 확인"},
                ],
                "backup_manifest": {
                    "backup_id": "backup-1",
                    "manifest_path": "/tmp/backup-1/manifest.json",
                    "files": [
                        {
                            "relative_path": "jobs.json",
                            "source_path": "/tmp/runtime/jobs.json",
                            "destination_path": "/tmp/backup-1/jobs.json",
                        }
                    ],
                },
                "next_action": "restore_requested",
            },
        )
    )
    backup_runtime = _FakePatchBackupRuntime()
    health_runtime = _FakePatchHealthRuntime(
        {
            "ok": True,
            "checked_at": "2026-03-13T10:13:00+09:00",
            "summary": "복원 후 정상",
            "checks": {},
            "failed_checks": [],
        }
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:13:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_health_runtime=health_runtime,
        patch_backup_runtime=backup_runtime,
        pid_provider=lambda: 4242,
    )

    result = runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "restored"
    assert patch_run.details["restore_payload"]["restored_file_count"] == 1
    assert backup_runtime.verify_calls == ["backup-1"]
    assert backup_runtime.restore_calls == ["backup-1"]
    assert manager.operations == [
        ("stop", "agenthub-worker"),
        ("restart", "agenthub-api"),
        ("restart", "agenthub-worker"),
    ]
    assert result["status"]["details"]["next_action"] == ""


def test_patch_updater_runtime_marks_restore_failed_when_backup_verification_fails(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    patch_service_runtime = _build_patch_service_runtime(store, tmp_path, manager=_FakeServiceManager())
    store.upsert_patch_run(
        _make_patch_run(
            "restore_requested",
            details={
                "steps": [
                    {"key": "restore_requested", "label": "복원 승인"},
                    {"key": "restore_state", "label": "백업 복원"},
                    {"key": "verify_restore", "label": "복원 상태 확인"},
                ],
                "backup_manifest": {
                    "backup_id": "backup-1",
                    "manifest_path": "/tmp/backup-1/manifest.json",
                },
                "next_action": "restore_requested",
            },
        )
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=tmp_path / "patch_updater_status.json",
        service_name="agenthub-updater",
        utc_now_iso=lambda: "2026-03-13T10:13:00+09:00",
        patch_service_runtime=patch_service_runtime,
        patch_backup_runtime=_FakePatchBackupRuntime(error="manifest missing"),
        pid_provider=lambda: 4242,
    )

    runtime.run_once()

    patch_run = store.get_patch_run("patch-1")
    assert patch_run is not None
    assert patch_run.status == "restore_failed"
    assert patch_run.details["next_action"] == "manual_restore_required"
