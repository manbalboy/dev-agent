"""Patch run state/progress helpers for dashboard operator controls."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException

from app.models import PatchRunRecord, utc_now_iso
from app.patch_backup_runtime import PatchBackupRuntime
from app.patch_control_runtime import PatchControlRuntime
from app.store import JobStore


class DashboardPatchRuntime:
    """Encapsulate operator-facing patch run creation and latest-status reads."""

    _ACTIVE_STATUSES = {
        "queued",
        "preparing",
        "waiting_updater",
        "draining",
        "updating",
        "restarting",
        "verifying",
        "rollback_requested",
        "rolling_back",
        "rollback_verifying",
        "restore_requested",
        "restoring",
        "restore_verifying",
    }

    _PATCH_STEPS: List[Dict[str, Any]] = [
        {"key": "approval_recorded", "label": "승인 기록"},
        {"key": "waiting_updater", "label": "업데이트 대기"},
        {"key": "drain_services", "label": "서비스 정리"},
        {"key": "update_code", "label": "코드 업데이트"},
        {"key": "restart_services", "label": "서비스 재기동"},
        {"key": "verify_health", "label": "상태 확인"},
    ]
    _ROLLBACK_STEPS: List[Dict[str, Any]] = [
        {"key": "rollback_requested", "label": "롤백 승인"},
        {"key": "rollback_code", "label": "코드 롤백"},
        {"key": "verify_rollback", "label": "롤백 상태 확인"},
    ]
    _RESTORE_STEPS: List[Dict[str, Any]] = [
        {"key": "restore_requested", "label": "복원 승인"},
        {"key": "restore_state", "label": "백업 복원"},
        {"key": "verify_restore", "label": "복원 상태 확인"},
    ]

    def __init__(
        self,
        *,
        store: JobStore,
        build_patch_control_runtime: Callable[[], PatchControlRuntime],
        build_patch_backup_runtime: Optional[Callable[[], PatchBackupRuntime]] = None,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.store = store
        self.build_patch_control_runtime = build_patch_control_runtime
        self.build_patch_backup_runtime = build_patch_backup_runtime
        self.utc_now_iso = utc_now_iso

    def get_latest_patch_run_payload(self) -> Dict[str, Any]:
        """Return one operator-safe payload for the newest patch run."""

        runs = self.store.list_patch_runs()
        if not runs:
            return {
                "active": False,
                "message": "등록된 패치 실행 기록이 없습니다.",
                "status": "idle",
            }
        return self._serialize_patch_run(runs[0])

    def create_patch_run(self, *, refresh: bool = False, note: str = "") -> Dict[str, Any]:
        """Create one baseline patch run waiting for an updater service."""

        active = self._latest_active_patch_run()
        if active is not None:
            raise HTTPException(
                status_code=409,
                detail="이미 진행 중인 패치 실행 기록이 있습니다. 현재 실행을 먼저 확인하세요.",
            )

        patch_status = self.build_patch_control_runtime().build_patch_status(refresh=bool(refresh))
        if not bool(patch_status.get("update_available")):
            raise HTTPException(
                status_code=400,
                detail=str(patch_status.get("message") or "현재 적용할 패치가 없습니다."),
            )
        if bool(patch_status.get("working_tree_dirty")):
            raise HTTPException(
                status_code=409,
                detail="로컬 변경 사항이 있어 자동 패치를 시작할 수 없습니다. 먼저 작업 트리를 정리하세요.",
            )
        if int(patch_status.get("ahead_count") or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail="원격보다 앞선 로컬 커밋이 있어 자동 패치를 시작할 수 없습니다. 먼저 수동 동기화하세요.",
            )

        now = self.utc_now_iso()
        steps = [dict(item) for item in self._PATCH_STEPS]
        note = str(note or "").strip()
        current_step = steps[1]
        record = PatchRunRecord(
            patch_run_id=f"patch-{uuid4()}",
            status="waiting_updater",
            repo_root=str(patch_status.get("repo_root") or ""),
            branch=str(patch_status.get("current_branch") or ""),
            upstream_ref=str(patch_status.get("upstream_ref") or ""),
            source_commit=str(patch_status.get("current_commit") or ""),
            target_commit=str(patch_status.get("upstream_commit") or ""),
            current_step_key=str(current_step["key"]),
            current_step_label=str(current_step["label"]),
            current_step_index=2,
            total_steps=len(steps),
            progress_percent=20,
            message="패치 실행이 등록됐습니다. updater service를 기다리는 중입니다.",
            requested_by="operator",
            requested_at=now,
            updated_at=now,
            refresh_used=bool(refresh),
            note=note,
            details={
                "steps": steps,
                "patch_status": patch_status,
                "operator_note": note,
                "next_action": "separate_updater_service_required",
            },
        )
        self.store.upsert_patch_run(record)
        return {
            "created": True,
            "patch_run": self._serialize_patch_run(record),
        }

    def request_rollback(self, *, patch_run_id: str, note: str = "") -> Dict[str, Any]:
        """Request one operator-approved rollback for a failed patch run."""

        patch_run = self.store.get_patch_run(str(patch_run_id or "").strip())
        if patch_run is None:
            raise HTTPException(status_code=404, detail="패치 실행 기록을 찾을 수 없습니다.")
        current_status = str(patch_run.status or "").strip()
        if current_status not in {"failed", "rollback_failed"}:
            raise HTTPException(
                status_code=409,
                detail="실패한 패치 실행 기록만 롤백할 수 있습니다.",
            )
        if not str(patch_run.source_commit or "").strip():
            raise HTTPException(
                status_code=409,
                detail="원본 커밋 정보가 없어 롤백을 시작할 수 없습니다.",
            )

        now = self.utc_now_iso()
        rollback_steps = [dict(item) for item in self._ROLLBACK_STEPS]
        rollback_note = str(note or "").strip()
        details = dict(patch_run.details or {})
        details.update(
            {
                "steps": rollback_steps,
                "rollback_requested_at": now,
                "rollback_requested_by": "operator",
                "rollback_note": rollback_note,
                "rollback_target_commit": str(patch_run.source_commit or ""),
                "rollback_from_status": current_status,
                "next_action": "rollback_requested",
            }
        )
        requested = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="rollback_requested",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="rollback_requested",
            current_step_label="롤백 승인",
            current_step_index=1,
            total_steps=len(rollback_steps),
            progress_percent=15,
            message="롤백이 승인되었습니다. updater service가 롤백을 수행합니다.",
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(requested)
        return {
            "created": False,
            "rollback_requested": True,
            "patch_run": self._serialize_patch_run(requested),
        }

    def request_restore(self, *, patch_run_id: str, note: str = "") -> Dict[str, Any]:
        """Request one operator-approved backup restore for a terminal patch run."""

        patch_run = self.store.get_patch_run(str(patch_run_id or "").strip())
        if patch_run is None:
            raise HTTPException(status_code=404, detail="패치 실행 기록을 찾을 수 없습니다.")
        current_status = str(patch_run.status or "").strip()
        if current_status not in {"failed", "rollback_failed", "rolled_back", "restore_failed"}:
            raise HTTPException(
                status_code=409,
                detail="복원은 실패/롤백 완료된 패치 실행 기록에서만 시작할 수 있습니다.",
            )

        now = self.utc_now_iso()
        restore_steps = [dict(item) for item in self._RESTORE_STEPS]
        restore_note = str(note or "").strip()
        details = dict(patch_run.details or {})
        backup_manifest = details.get("backup_manifest")
        if not isinstance(backup_manifest, dict) or not str(backup_manifest.get("backup_id") or "").strip():
            raise HTTPException(
                status_code=409,
                detail="복원할 백업 정보가 없어 restore를 시작할 수 없습니다.",
            )

        verification: Dict[str, Any] = {}
        if self.build_patch_backup_runtime is not None:
            backup_runtime = self.build_patch_backup_runtime()
            verification = backup_runtime.verify_backup_manifest(manifest=backup_manifest)
            if not bool(verification.get("ok")):
                raise HTTPException(
                    status_code=409,
                    detail=str(verification.get("summary") or "백업 검증에 실패해 restore를 시작할 수 없습니다."),
                )

        details.update(
            {
                "steps": restore_steps,
                "restore_requested_at": now,
                "restore_requested_by": "operator",
                "restore_note": restore_note,
                "restore_from_status": current_status,
                "restore_manifest": backup_manifest,
                "restore_verification": verification,
                "next_action": "restore_requested",
            }
        )
        requested = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="restore_requested",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="restore_requested",
            current_step_label="복원 승인",
            current_step_index=1,
            total_steps=len(restore_steps),
            progress_percent=15,
            message="백업 복원이 승인되었습니다. updater service가 복원을 수행합니다.",
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(requested)
        return {
            "created": False,
            "restore_requested": True,
            "patch_run": self._serialize_patch_run(requested),
        }

    def _latest_active_patch_run(self) -> Optional[PatchRunRecord]:
        for patch_run in self.store.list_patch_runs():
            if str(patch_run.status or "") in self._ACTIVE_STATUSES:
                return patch_run
        return None

    @staticmethod
    def _serialize_patch_run(record: PatchRunRecord) -> Dict[str, Any]:
        status = str(record.status or "").strip()
        return {
            "active": status in DashboardPatchRuntime._ACTIVE_STATUSES,
            "patch_run_id": record.patch_run_id,
            "status": status or "idle",
            "repo_root": record.repo_root,
            "branch": record.branch,
            "upstream_ref": record.upstream_ref,
            "source_commit": record.source_commit,
            "target_commit": record.target_commit,
            "current_step_key": record.current_step_key,
            "current_step_label": record.current_step_label,
            "current_step_index": int(record.current_step_index or 0),
            "total_steps": int(record.total_steps or 0),
            "progress_percent": int(record.progress_percent or 0),
            "message": str(record.message or ""),
            "requested_by": str(record.requested_by or "operator"),
            "requested_at": str(record.requested_at or ""),
            "updated_at": str(record.updated_at or ""),
            "refresh_used": bool(record.refresh_used),
            "note": str(record.note or ""),
            "details": dict(record.details or {}),
        }
