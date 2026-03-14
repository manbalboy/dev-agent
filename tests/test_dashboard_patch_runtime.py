from __future__ import annotations

from app.dashboard_patch_runtime import DashboardPatchRuntime
from app.models import PatchRunRecord, utc_now_iso
from app.store import SQLiteJobStore


class _DummyPatchControlRuntime:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def build_patch_status(self, *, refresh: bool = False):
        self.calls.append(refresh)
        return dict(self.payload)


class _DummyPatchBackupRuntime:
    def __init__(self, *, ok: bool = True, summary: str = "백업 검증 완료") -> None:
        self.ok = ok
        self.summary = summary
        self.calls = []

    def verify_backup_manifest(self, *, manifest):
        self.calls.append(str(manifest.get("backup_id") or ""))
        return {
            "ok": self.ok,
            "backup_id": str(manifest.get("backup_id") or ""),
            "summary": self.summary,
            "files": [],
        }


def test_dashboard_patch_runtime_creates_waiting_updater_run(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    dummy = _DummyPatchControlRuntime(
        {
            "status": "update_available",
            "repo_root": str(tmp_path),
            "current_branch": "master",
            "upstream_ref": "origin/master",
            "current_commit": "1111222233334444",
            "upstream_commit": "aaaabbbbccccdddd",
            "message": "패치가 있습니다. 진행하시겠습니까?",
            "update_available": True,
        }
    )
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: dummy,
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    payload = runtime.create_patch_run(refresh=True, note="운영자 승인")

    assert payload["created"] is True
    patch_run = payload["patch_run"]
    assert patch_run["status"] == "waiting_updater"
    assert patch_run["progress_percent"] == 20
    assert patch_run["current_step_key"] == "waiting_updater"
    assert patch_run["note"] == "운영자 승인"
    assert dummy.calls == [True]


def test_dashboard_patch_runtime_rejects_second_active_run(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_patch_run(
        PatchRunRecord(
            patch_run_id="patch-1",
            status="waiting_updater",
            repo_root=str(tmp_path),
            branch="master",
            upstream_ref="origin/master",
            source_commit="1111",
            target_commit="2222",
            current_step_key="waiting_updater",
            current_step_label="업데이트 대기",
            current_step_index=2,
            total_steps=6,
            progress_percent=20,
            message="대기 중",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
        )
    )
    dummy = _DummyPatchControlRuntime({"status": "update_available", "update_available": True})
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: dummy,
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    try:
        runtime.create_patch_run(refresh=False, note="")
    except Exception as exc:  # HTTPException
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("expected HTTPException")


def test_dashboard_patch_runtime_returns_latest_payload_when_missing(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: _DummyPatchControlRuntime({}),
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    payload = runtime.get_latest_patch_run_payload()

    assert payload["active"] is False
    assert payload["status"] == "idle"


def test_dashboard_patch_runtime_rejects_dirty_worktree(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    dummy = _DummyPatchControlRuntime(
        {
            "status": "update_available",
            "update_available": True,
            "working_tree_dirty": True,
            "ahead_count": 0,
        }
    )
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: dummy,
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    try:
        runtime.create_patch_run(refresh=False, note="")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "로컬 변경 사항" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected HTTPException")


def test_dashboard_patch_runtime_rejects_ahead_of_upstream(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    dummy = _DummyPatchControlRuntime(
        {
            "status": "update_available",
            "update_available": True,
            "working_tree_dirty": False,
            "ahead_count": 2,
        }
    )
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: dummy,
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    try:
        runtime.create_patch_run(refresh=False, note="")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "원격보다 앞선 로컬 커밋" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected HTTPException")


def test_dashboard_patch_runtime_requests_rollback_for_failed_run(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_patch_run(
        PatchRunRecord(
            patch_run_id="patch-1",
            status="failed",
            repo_root=str(tmp_path),
            branch="master",
            upstream_ref="origin/master",
            source_commit="1111",
            target_commit="2222",
            current_step_key="verify_health",
            current_step_label="상태 확인",
            current_step_index=6,
            total_steps=6,
            progress_percent=90,
            message="패치 후 상태 확인 실패",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
            details={
                "next_action": "manual_post_update_check_required",
                "backup_manifest": {
                    "backup_id": "backup-1",
                    "manifest_path": str(tmp_path / "patch_backups" / "backup-1" / "manifest.json"),
                },
            },
        )
    )
    backup_runtime = _DummyPatchBackupRuntime()
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: _DummyPatchControlRuntime({}),
        build_patch_backup_runtime=lambda: backup_runtime,
        utc_now_iso=utc_now_iso,
    )

    payload = runtime.request_rollback(patch_run_id="patch-1", note="직전 커밋으로 복구")

    assert payload["rollback_requested"] is True
    patch_run = payload["patch_run"]
    assert patch_run["status"] == "rollback_requested"
    assert patch_run["current_step_key"] == "rollback_requested"
    assert patch_run["details"]["rollback_target_commit"] == "1111"
    assert patch_run["details"]["rollback_note"] == "직전 커밋으로 복구"
    assert backup_runtime.calls == []


def test_dashboard_patch_runtime_rejects_rollback_for_non_failed_run(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_patch_run(
        PatchRunRecord(
            patch_run_id="patch-1",
            status="waiting_updater",
            repo_root=str(tmp_path),
            branch="master",
            upstream_ref="origin/master",
            source_commit="1111",
            target_commit="2222",
            current_step_key="waiting_updater",
            current_step_label="업데이트 대기",
            current_step_index=2,
            total_steps=6,
            progress_percent=20,
            message="대기 중",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
        )
    )
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: _DummyPatchControlRuntime({}),
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(),
        utc_now_iso=utc_now_iso,
    )

    try:
        runtime.request_rollback(patch_run_id="patch-1", note="")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("expected HTTPException")


def test_dashboard_patch_runtime_requests_restore_for_failed_run_with_verified_backup(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_patch_run(
        PatchRunRecord(
            patch_run_id="patch-1",
            status="failed",
            repo_root=str(tmp_path),
            branch="master",
            upstream_ref="origin/master",
            source_commit="1111",
            target_commit="2222",
            current_step_key="verify_health",
            current_step_label="상태 확인",
            current_step_index=6,
            total_steps=6,
            progress_percent=90,
            message="패치 후 상태 확인 실패",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
            details={
                "next_action": "manual_post_update_check_required",
                "backup_manifest": {
                    "backup_id": "backup-1",
                    "manifest_path": str(tmp_path / "patch_backups" / "backup-1" / "manifest.json"),
                },
            },
        )
    )
    backup_runtime = _DummyPatchBackupRuntime()
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: _DummyPatchControlRuntime({}),
        build_patch_backup_runtime=lambda: backup_runtime,
        utc_now_iso=utc_now_iso,
    )

    payload = runtime.request_restore(patch_run_id="patch-1", note="백업 상태로 복원")

    assert payload["restore_requested"] is True
    patch_run = payload["patch_run"]
    assert patch_run["status"] == "restore_requested"
    assert patch_run["current_step_key"] == "restore_requested"
    assert patch_run["details"]["restore_note"] == "백업 상태로 복원"
    assert patch_run["details"]["restore_manifest"]["backup_id"] == "backup-1"
    assert patch_run["details"]["restore_verification"]["ok"] is True
    assert backup_runtime.calls == ["backup-1"]


def test_dashboard_patch_runtime_rejects_restore_when_backup_verification_fails(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_patch_run(
        PatchRunRecord(
            patch_run_id="patch-1",
            status="rollback_failed",
            repo_root=str(tmp_path),
            branch="master",
            upstream_ref="origin/master",
            source_commit="1111",
            target_commit="2222",
            current_step_key="verify_rollback",
            current_step_label="롤백 상태 확인",
            current_step_index=3,
            total_steps=3,
            progress_percent=90,
            message="롤백 상태 확인 실패",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
            details={
                "backup_manifest": {
                    "backup_id": "backup-1",
                    "manifest_path": str(tmp_path / "patch_backups" / "backup-1" / "manifest.json"),
                }
            },
        )
    )
    runtime = DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=lambda: _DummyPatchControlRuntime({}),
        build_patch_backup_runtime=lambda: _DummyPatchBackupRuntime(ok=False, summary="manifest missing"),
        utc_now_iso=utc_now_iso,
    )

    try:
        runtime.request_restore(patch_run_id="patch-1", note="")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "manifest missing" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected HTTPException")
