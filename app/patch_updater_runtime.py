"""Standalone patch updater helpers.

This baseline keeps the updater separate from the API/worker so patch progress
can continue even when operator-facing services are being prepared for restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.models import PatchRunRecord
from app.patch_backup_runtime import PatchBackupRuntime
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_rollback_runtime import PatchRollbackRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.store import JobStore


class PatchUpdaterRuntime:
    """Track updater heartbeat and claim patch runs awaiting an updater."""

    def __init__(
        self,
        *,
        store: JobStore,
        status_file: Path,
        service_name: str,
        utc_now_iso: Callable[[], str],
        patch_service_runtime: PatchServiceRuntime,
        patch_health_runtime: Optional[PatchHealthRuntime] = None,
        patch_rollback_runtime: Optional[PatchRollbackRuntime] = None,
        patch_backup_runtime: Optional[PatchBackupRuntime] = None,
        pid_provider: Callable[[], int] = os.getpid,
    ) -> None:
        self.store = store
        self.status_file = status_file
        self.service_name = service_name
        self.utc_now_iso = utc_now_iso
        self.patch_service_runtime = patch_service_runtime
        self.patch_health_runtime = patch_health_runtime
        self.patch_rollback_runtime = patch_rollback_runtime
        self.patch_backup_runtime = patch_backup_runtime
        self.pid_provider = pid_provider

    def read_status_payload(self) -> Dict[str, Any]:
        """Return the latest updater status payload for operator visibility."""

        if not self.status_file.exists():
            return {
                "service_name": self.service_name,
                "status": "offline",
                "pid": None,
                "active_patch_run_id": "",
                "last_heartbeat_at": "",
                "updated_at": "",
                "message": "Updater service가 아직 시작되지 않았습니다.",
                "details": {},
            }
        try:
            payload = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "service_name": self.service_name,
                "status": "error",
                "pid": None,
                "active_patch_run_id": "",
                "last_heartbeat_at": "",
                "updated_at": "",
                "message": "Updater status 파일을 읽을 수 없습니다.",
                "details": {},
            }
        if not isinstance(payload, dict):
            return {
                "service_name": self.service_name,
                "status": "error",
                "pid": None,
                "active_patch_run_id": "",
                "last_heartbeat_at": "",
                "updated_at": "",
                "message": "Updater status payload 형식이 올바르지 않습니다.",
                "details": {},
            }
        return {
            "service_name": str(payload.get("service_name") or self.service_name),
            "status": str(payload.get("status") or "offline"),
            "pid": payload.get("pid"),
            "active_patch_run_id": str(payload.get("active_patch_run_id") or ""),
            "last_heartbeat_at": str(payload.get("last_heartbeat_at") or ""),
            "updated_at": str(payload.get("updated_at") or ""),
            "message": str(payload.get("message") or ""),
            "details": dict(payload.get("details") or {}),
        }

    def run_once(self) -> Dict[str, Any]:
        """Write one heartbeat and advance the latest active patch run if present."""

        patch_run = self._find_active_patch_run()
        if patch_run is None:
            payload = self._write_status(
                status="idle",
                active_patch_run_id="",
                message="Updater service가 연결되어 있으며 대기 중입니다.",
                details={"next_action": "wait_for_patch_run"},
            )
            return {"status": payload, "claimed_patch_run": None}

        updated_patch_run = self._advance_patch_run(patch_run)
        payload = self._write_status(
            status="tracking",
            active_patch_run_id=updated_patch_run.patch_run_id,
            message=str(updated_patch_run.message or ""),
            details={
                "branch": updated_patch_run.branch,
                "upstream_ref": updated_patch_run.upstream_ref,
                "current_step_key": updated_patch_run.current_step_key,
                "next_action": str((updated_patch_run.details or {}).get("next_action") or ""),
                "active_job_count": int((updated_patch_run.details or {}).get("active_job_count") or 0),
            },
        )
        return {"status": payload, "claimed_patch_run": updated_patch_run.to_dict()}

    def _find_active_patch_run(self) -> Optional[PatchRunRecord]:
        for patch_run in self.store.list_patch_runs():
            status = str(patch_run.status or "").strip()
            if status in {
                "waiting_updater",
                "draining",
                "restarting",
                "verifying",
                "rollback_requested",
                "rolling_back",
                "rollback_verifying",
                "restore_requested",
                "restoring",
                "restore_verifying",
            }:
                return patch_run
        return None

    def _advance_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        status = str(patch_run.status or "").strip()
        if status == "waiting_updater":
            return self._claim_waiting_patch_run(patch_run)
        if status == "draining":
            return self._continue_draining_patch_run(patch_run)
        if status == "restarting":
            return self._refresh_restarting_patch_run(patch_run)
        if status == "verifying":
            return self._run_verifying_patch_run(patch_run)
        if status == "rollback_requested":
            return self._claim_requested_rollback_patch_run(patch_run)
        if status == "rolling_back":
            return self._continue_rolling_back_patch_run(patch_run)
        if status == "rollback_verifying":
            return self._run_rollback_verifying_patch_run(patch_run)
        if status == "restore_requested":
            return self._claim_requested_restore_patch_run(patch_run)
        if status == "restoring":
            return self._continue_restoring_patch_run(patch_run)
        if status == "restore_verifying":
            return self._run_restore_verifying_patch_run(patch_run)
        return patch_run

    def _claim_waiting_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        patch_status = dict(details.get("patch_status") or {})
        if bool(patch_status.get("working_tree_dirty")):
            return self._fail_patch_run(
                patch_run,
                reason="로컬 변경 사항이 있어 자동 패치를 시작할 수 없습니다.",
                details={"next_action": "manual_patch_required", "failure_boundary": "dirty_working_tree"},
            )
        if int(patch_status.get("ahead_count") or 0) > 0:
            return self._fail_patch_run(
                patch_run,
                reason="원격보다 앞선 로컬 커밋이 있어 자동 패치를 시작할 수 없습니다.",
                details={"next_action": "manual_patch_required", "failure_boundary": "ahead_of_upstream"},
            )

        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        details.update(
            {
                "updater_service_name": self.service_name,
                "updater_service_pid": self.pid_provider(),
                "updater_claimed_at": details.get("updater_claimed_at") or now,
                "updater_last_heartbeat_at": now,
                "next_action": "wait_for_job_drain" if active_jobs else "restart_services_ready",
                "drain_started_at": details.get("drain_started_at") or now,
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="draining",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="drain_services",
            current_step_label=self._step_label(patch_run, "drain_services", "서비스 정리"),
            current_step_index=self._step_index(patch_run, "drain_services", 3),
            total_steps=patch_run.total_steps,
            progress_percent=40,
            message=(
                "Updater service가 patch run을 확인했습니다. 기존 작업을 정리하는 중입니다."
                if active_jobs
                else "Updater service가 patch run을 확인했습니다. 서비스 재기동 준비가 완료되었습니다."
            ),
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        return updated

    def _claim_requested_rollback_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        details.update(
            {
                "updater_service_name": self.service_name,
                "updater_service_pid": self.pid_provider(),
                "updater_claimed_at": details.get("updater_claimed_at") or now,
                "updater_last_heartbeat_at": now,
                "rollback_started_at": details.get("rollback_started_at") or now,
                "next_action": "wait_for_job_drain" if active_jobs else "run_rollback_code",
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="rolling_back",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="rollback_code",
            current_step_label=self._step_label(patch_run, "rollback_code", "코드 롤백"),
            current_step_index=self._step_index(patch_run, "rollback_code", 2),
            total_steps=patch_run.total_steps,
            progress_percent=40,
            message=(
                "Updater service가 롤백을 확인했습니다. 기존 작업을 정리하는 중입니다."
                if active_jobs
                else "Updater service가 롤백을 확인했습니다. 롤백을 시작합니다."
            ),
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        if active_jobs:
            return updated
        return self._continue_rolling_back_patch_run(updated)

    def _claim_requested_restore_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        details.update(
            {
                "updater_service_name": self.service_name,
                "updater_service_pid": self.pid_provider(),
                "updater_claimed_at": details.get("updater_claimed_at") or now,
                "updater_last_heartbeat_at": now,
                "restore_started_at": details.get("restore_started_at") or now,
                "next_action": "wait_for_job_drain" if active_jobs else "run_restore_state",
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="restoring",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="restore_state",
            current_step_label=self._step_label(patch_run, "restore_state", "백업 복원"),
            current_step_index=self._step_index(patch_run, "restore_state", 2),
            total_steps=patch_run.total_steps,
            progress_percent=40,
            message=(
                "Updater service가 복원을 확인했습니다. 기존 작업을 정리하는 중입니다."
                if active_jobs
                else "Updater service가 복원을 확인했습니다. 백업 복원을 시작합니다."
            ),
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        if active_jobs:
            return updated
        return self._continue_restoring_patch_run(updated)

    def _continue_draining_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        details = dict(patch_run.details or {})
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        if active_jobs:
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="draining",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="drain_services",
                current_step_label=self._step_label(patch_run, "drain_services", "서비스 정리"),
                current_step_index=self._step_index(patch_run, "drain_services", 3),
                total_steps=patch_run.total_steps,
                progress_percent=45,
                message=f"기존 작업 {len(active_jobs)}건이 남아 있어 drain을 계속 대기합니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details={**details, "next_action": "wait_for_job_drain"},
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        backup_manifest = self._ensure_backup_manifest(
            patch_run=patch_run,
            details=details,
            reason="before_patch_restart",
            failure_status="failed",
        )
        if isinstance(backup_manifest, PatchRunRecord):
            return backup_manifest
        details["backup_manifest"] = backup_manifest
        details["backup_created_at"] = str(backup_manifest.get("created_at") or now)

        restart_details = self.patch_service_runtime.restart_services_for_patch()
        self.patch_service_runtime.clear_patch_lock(
            status="restart_completed",
            message="서비스 재기동이 완료되어 새 작업 수락을 다시 허용합니다.",
            details={"patch_run_id": patch_run.patch_run_id},
        )
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "restart_completed_at": restart_details.get("completed_at"),
                "restart_operations": restart_details.get("operations", []),
                "next_action": "run_post_update_health_check",
                "update_step_skipped": True,
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="verifying",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="verify_health",
            current_step_label=self._step_label(patch_run, "verify_health", "상태 확인"),
            current_step_index=self._step_index(patch_run, "verify_health", 6),
            total_steps=patch_run.total_steps,
            progress_percent=85,
            message="서비스 재기동이 완료되었습니다. 패치 후 상태를 확인하는 중입니다.",
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        return updated

    def _continue_rolling_back_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        details = dict(patch_run.details or {})
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        if active_jobs:
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="rolling_back",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="rollback_code",
                current_step_label=self._step_label(patch_run, "rollback_code", "코드 롤백"),
                current_step_index=self._step_index(patch_run, "rollback_code", 2),
                total_steps=patch_run.total_steps,
                progress_percent=45,
                message=f"기존 작업 {len(active_jobs)}건이 남아 있어 롤백을 계속 대기합니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details={**details, "next_action": "wait_for_job_drain"},
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        backup_manifest = self._ensure_backup_manifest(
            patch_run=patch_run,
            details=details,
            reason="before_rollback",
            failure_status="rollback_failed",
        )
        if isinstance(backup_manifest, PatchRunRecord):
            return backup_manifest
        details["backup_manifest"] = backup_manifest
        details["backup_created_at"] = str(backup_manifest.get("created_at") or now)

        if self.patch_rollback_runtime is None:
            return self._fail_patch_run_with_status(
                patch_run,
                status="rollback_failed",
                reason="롤백 런타임이 아직 연결되지 않았습니다.",
                details={**details, "next_action": "manual_rollback_required"},
            )

        try:
            rollback_payload = self.patch_rollback_runtime.rollback_to_commit(
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                target_commit=patch_run.source_commit,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime boundary
            return self._fail_patch_run_with_status(
                patch_run,
                status="rollback_failed",
                reason=f"롤백 실행에 실패했습니다: {exc}",
                details={**details, "next_action": "manual_rollback_required"},
            )

        restart_details = self.patch_service_runtime.restart_services_for_patch()
        self.patch_service_runtime.clear_patch_lock(
            status="rollback_restart_completed",
            message="롤백 후 서비스 재기동이 완료되어 새 작업 수락을 다시 허용합니다.",
            details={"patch_run_id": patch_run.patch_run_id},
        )
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "rollback_payload": rollback_payload,
                "rollback_completed_at": rollback_payload.get("completed_at"),
                "restart_completed_at": restart_details.get("completed_at"),
                "restart_operations": restart_details.get("operations", []),
                "next_action": "run_post_rollback_health_check",
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="rollback_verifying",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="verify_rollback",
            current_step_label=self._step_label(patch_run, "verify_rollback", "롤백 상태 확인"),
            current_step_index=self._step_index(patch_run, "verify_rollback", 3),
            total_steps=patch_run.total_steps,
            progress_percent=85,
            message="롤백이 완료되었습니다. 롤백 후 상태를 확인하는 중입니다.",
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        return self._run_rollback_verifying_patch_run(updated)

    def _continue_restoring_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        active_jobs = self.patch_service_runtime.list_active_jobs_for_drain()
        details = dict(patch_run.details or {})
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
            }
        )
        self.patch_service_runtime.activate_patch_lock(patch_run=patch_run, active_jobs=active_jobs)
        if active_jobs:
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="restoring",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="restore_state",
                current_step_label=self._step_label(patch_run, "restore_state", "백업 복원"),
                current_step_index=self._step_index(patch_run, "restore_state", 2),
                total_steps=patch_run.total_steps,
                progress_percent=45,
                message=f"기존 작업 {len(active_jobs)}건이 남아 있어 복원을 계속 대기합니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details={**details, "next_action": "wait_for_job_drain"},
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        if self.patch_backup_runtime is None:
            return self._fail_patch_run_with_status(
                patch_run,
                status="restore_failed",
                reason="백업 복원 런타임이 아직 연결되지 않았습니다.",
                details={**details, "next_action": "manual_restore_required"},
            )

        restore_manifest = self._resolve_restore_manifest(patch_run=patch_run, details=details)
        if not restore_manifest:
            return self._fail_patch_run_with_status(
                patch_run,
                status="restore_failed",
                reason="복원할 백업 정보가 없어 restore를 계속할 수 없습니다.",
                details={**details, "next_action": "manual_restore_required"},
            )

        verification = self.patch_backup_runtime.verify_backup_manifest(manifest=restore_manifest)
        details["restore_verification"] = verification
        if not bool(verification.get("ok")):
            return self._fail_patch_run_with_status(
                patch_run,
                status="restore_failed",
                reason=str(verification.get("summary") or "백업 검증에 실패했습니다."),
                details={**details, "next_action": "manual_restore_required"},
            )

        try:
            restore_payload = self.patch_backup_runtime.restore_backup(manifest=restore_manifest)
        except Exception as exc:  # pragma: no cover - defensive runtime boundary
            return self._fail_patch_run_with_status(
                patch_run,
                status="restore_failed",
                reason=f"백업 복원에 실패했습니다: {exc}",
                details={**details, "next_action": "manual_restore_required"},
            )

        restart_details = self.patch_service_runtime.restart_services_for_patch()
        self.patch_service_runtime.clear_patch_lock(
            status="restore_restart_completed",
            message="백업 복원 후 서비스 재기동이 완료되어 새 작업 수락을 다시 허용합니다.",
            details={"patch_run_id": patch_run.patch_run_id},
        )
        details.update(
            {
                "updater_last_heartbeat_at": now,
                "restore_manifest": restore_manifest,
                "restore_payload": restore_payload,
                "restore_completed_at": restore_payload.get("restored_at"),
                "restart_completed_at": restart_details.get("completed_at"),
                "restart_operations": restart_details.get("operations", []),
                "next_action": "run_post_restore_health_check",
            }
        )
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="restore_verifying",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="verify_restore",
            current_step_label=self._step_label(patch_run, "verify_restore", "복원 상태 확인"),
            current_step_index=self._step_index(patch_run, "verify_restore", 3),
            total_steps=patch_run.total_steps,
            progress_percent=85,
            message="백업 복원이 완료되었습니다. 복원 후 상태를 확인하는 중입니다.",
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        return self._run_restore_verifying_patch_run(updated)

    def _refresh_restarting_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        details["updater_last_heartbeat_at"] = now
        details.setdefault("next_action", "run_post_update_health_check")
        updated = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="verifying",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="verify_health",
            current_step_label=self._step_label(patch_run, "verify_health", "상태 확인"),
            current_step_index=self._step_index(patch_run, "verify_health", 6),
            total_steps=patch_run.total_steps,
            progress_percent=max(int(patch_run.progress_percent or 0), 85),
            message=str(patch_run.message or "서비스 재기동 후 상태 확인을 기다리는 중입니다."),
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(updated)
        return self._run_verifying_patch_run(updated)

    def _run_verifying_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        details["updater_last_heartbeat_at"] = now
        if self.patch_health_runtime is None:
            details["next_action"] = "patch_health_runtime_missing"
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="verifying",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_health",
                current_step_label=self._step_label(patch_run, "verify_health", "상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_health", 6),
                total_steps=patch_run.total_steps,
                progress_percent=max(int(patch_run.progress_percent or 0), 90),
                message="패치 후 상태 확인 런타임이 아직 연결되지 않았습니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        health_payload = self.patch_health_runtime.build_post_update_health_payload()
        details["health_check"] = health_payload
        details["health_checked_at"] = str(health_payload.get("checked_at") or now)
        if bool(health_payload.get("ok")):
            details["next_action"] = ""
            done = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="done",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_health",
                current_step_label=self._step_label(patch_run, "verify_health", "상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_health", 6),
                total_steps=patch_run.total_steps,
                progress_percent=100,
                message="패치 후 상태 확인이 완료됐습니다. 서비스가 정상입니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(done)
            return done

        details["next_action"] = "manual_post_update_check_required"
        failed = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status="failed",
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key="verify_health",
            current_step_label=self._step_label(patch_run, "verify_health", "상태 확인"),
            current_step_index=self._step_index(patch_run, "verify_health", 6),
            total_steps=patch_run.total_steps,
            progress_percent=max(int(patch_run.progress_percent or 0), 90),
            message=str(health_payload.get("summary") or "패치 후 상태 확인에 실패했습니다."),
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=details,
        )
        self.store.upsert_patch_run(failed)
        return failed

    def _run_rollback_verifying_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        details["updater_last_heartbeat_at"] = now
        if self.patch_health_runtime is None:
            details["next_action"] = "patch_health_runtime_missing"
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="rollback_verifying",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_rollback",
                current_step_label=self._step_label(patch_run, "verify_rollback", "롤백 상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_rollback", 3),
                total_steps=patch_run.total_steps,
                progress_percent=max(int(patch_run.progress_percent or 0), 90),
                message="롤백 후 상태 확인 런타임이 아직 연결되지 않았습니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        health_payload = self.patch_health_runtime.build_post_update_health_payload()
        details["health_check"] = health_payload
        details["health_checked_at"] = str(health_payload.get("checked_at") or now)
        if bool(health_payload.get("ok")):
            details["next_action"] = ""
            rolled_back = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="rolled_back",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_rollback",
                current_step_label=self._step_label(patch_run, "verify_rollback", "롤백 상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_rollback", 3),
                total_steps=patch_run.total_steps,
                progress_percent=100,
                message="롤백 후 상태 확인이 완료됐습니다. 서비스가 정상입니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(rolled_back)
            return rolled_back

        return self._fail_patch_run_with_status(
            patch_run,
            status="rollback_failed",
            reason=str(health_payload.get("summary") or "롤백 후 상태 확인에 실패했습니다."),
            details={
                **details,
                "health_check": health_payload,
                "health_checked_at": str(health_payload.get("checked_at") or now),
                "next_action": "manual_rollback_check_required",
            },
        )

    def _run_restore_verifying_patch_run(self, patch_run: PatchRunRecord) -> PatchRunRecord:
        now = self.utc_now_iso()
        details = dict(patch_run.details or {})
        details["updater_last_heartbeat_at"] = now
        if self.patch_health_runtime is None:
            details["next_action"] = "patch_health_runtime_missing"
            waiting = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="restore_verifying",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_restore",
                current_step_label=self._step_label(patch_run, "verify_restore", "복원 상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_restore", 3),
                total_steps=patch_run.total_steps,
                progress_percent=max(int(patch_run.progress_percent or 0), 90),
                message="복원 후 상태 확인 런타임이 아직 연결되지 않았습니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(waiting)
            return waiting

        health_payload = self.patch_health_runtime.build_post_update_health_payload()
        details["health_check"] = health_payload
        details["health_checked_at"] = str(health_payload.get("checked_at") or now)
        if bool(health_payload.get("ok")):
            details["next_action"] = ""
            restored = PatchRunRecord(
                patch_run_id=patch_run.patch_run_id,
                status="restored",
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                upstream_ref=patch_run.upstream_ref,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                current_step_key="verify_restore",
                current_step_label=self._step_label(patch_run, "verify_restore", "복원 상태 확인"),
                current_step_index=self._step_index(patch_run, "verify_restore", 3),
                total_steps=patch_run.total_steps,
                progress_percent=100,
                message="백업 복원 후 상태 확인이 완료됐습니다. 서비스가 정상입니다.",
                requested_by=patch_run.requested_by,
                requested_at=patch_run.requested_at,
                updated_at=now,
                refresh_used=patch_run.refresh_used,
                note=patch_run.note,
                details=details,
            )
            self.store.upsert_patch_run(restored)
            return restored

        return self._fail_patch_run_with_status(
            patch_run,
            status="restore_failed",
            reason=str(health_payload.get("summary") or "복원 후 상태 확인에 실패했습니다."),
            details={
                **details,
                "health_check": health_payload,
                "health_checked_at": str(health_payload.get("checked_at") or now),
                "next_action": "manual_restore_check_required",
            },
        )

    def _fail_patch_run(self, patch_run: PatchRunRecord, *, reason: str, details: Dict[str, Any]) -> PatchRunRecord:
        return self._fail_patch_run_with_status(
            patch_run,
            status="failed",
            reason=reason,
            details=details,
        )

    def _fail_patch_run_with_status(
        self,
        patch_run: PatchRunRecord,
        *,
        status: str,
        reason: str,
        details: Dict[str, Any],
    ) -> PatchRunRecord:
        now = self.utc_now_iso()
        merged_details = dict(patch_run.details or {})
        merged_details.update(details)
        self.patch_service_runtime.clear_patch_lock(
            status=status,
            message=reason,
            details={"patch_run_id": patch_run.patch_run_id, **details},
        )
        failed = PatchRunRecord(
            patch_run_id=patch_run.patch_run_id,
            status=status,
            repo_root=patch_run.repo_root,
            branch=patch_run.branch,
            upstream_ref=patch_run.upstream_ref,
            source_commit=patch_run.source_commit,
            target_commit=patch_run.target_commit,
            current_step_key=patch_run.current_step_key,
            current_step_label=patch_run.current_step_label,
            current_step_index=patch_run.current_step_index,
            total_steps=patch_run.total_steps,
            progress_percent=patch_run.progress_percent,
            message=reason,
            requested_by=patch_run.requested_by,
            requested_at=patch_run.requested_at,
            updated_at=now,
            refresh_used=patch_run.refresh_used,
            note=patch_run.note,
            details=merged_details,
        )
        self.store.upsert_patch_run(failed)
        return failed

    def _ensure_backup_manifest(
        self,
        *,
        patch_run: PatchRunRecord,
        details: Dict[str, Any],
        reason: str,
        failure_status: str,
    ) -> Dict[str, Any] | PatchRunRecord:
        existing = details.get("backup_manifest")
        if isinstance(existing, dict) and existing.get("backup_id"):
            return existing
        if self.patch_backup_runtime is None:
            return self._fail_patch_run_with_status(
                patch_run,
                status=failure_status,
                reason="패치 전 백업 런타임이 연결되지 않아 작업을 계속할 수 없습니다.",
                details={"next_action": "manual_backup_required", "failure_boundary": "backup_runtime_missing"},
            )
        try:
            return self.patch_backup_runtime.create_backup(
                patch_run_id=patch_run.patch_run_id,
                repo_root=patch_run.repo_root,
                branch=patch_run.branch,
                source_commit=patch_run.source_commit,
                target_commit=patch_run.target_commit,
                reason=reason,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime boundary
            return self._fail_patch_run_with_status(
                patch_run,
                status=failure_status,
                reason=f"패치 전 백업 생성에 실패했습니다: {exc}",
                details={"next_action": "manual_backup_check_required", "failure_boundary": "backup_creation_failed"},
            )

    @staticmethod
    def _resolve_restore_manifest(*, patch_run: PatchRunRecord, details: Dict[str, Any]) -> Dict[str, Any]:
        restore_manifest = details.get("restore_manifest")
        if isinstance(restore_manifest, dict) and restore_manifest.get("backup_id"):
            return restore_manifest
        backup_manifest = details.get("backup_manifest")
        if isinstance(backup_manifest, dict) and backup_manifest.get("backup_id"):
            return backup_manifest
        patch_details = dict(patch_run.details or {})
        restore_manifest = patch_details.get("restore_manifest")
        if isinstance(restore_manifest, dict) and restore_manifest.get("backup_id"):
            return restore_manifest
        backup_manifest = patch_details.get("backup_manifest")
        if isinstance(backup_manifest, dict) and backup_manifest.get("backup_id"):
            return backup_manifest
        return {}

    @staticmethod
    def _step_label(patch_run: PatchRunRecord, key: str, fallback: str) -> str:
        steps = list((patch_run.details or {}).get("steps") or [])
        for item in steps:
            if str(item.get("key") or "") == key:
                return str(item.get("label") or fallback)
        return fallback

    @staticmethod
    def _step_index(patch_run: PatchRunRecord, key: str, fallback: int) -> int:
        steps = list((patch_run.details or {}).get("steps") or [])
        for index, item in enumerate(steps, start=1):
            if str(item.get("key") or "") == key:
                return index
        return fallback

    def _write_status(
        self,
        *,
        status: str,
        active_patch_run_id: str,
        message: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = self.utc_now_iso()
        payload = {
            "service_name": self.service_name,
            "status": status,
            "pid": self.pid_provider(),
            "active_patch_run_id": active_patch_run_id,
            "last_heartbeat_at": now,
            "updated_at": now,
            "message": message,
            "details": details,
        }
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.status_file.with_suffix(f"{self.status_file.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.status_file)
        return payload
