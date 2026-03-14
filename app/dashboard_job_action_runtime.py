"""Job action helper/runtime for dashboard mutation APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from fastapi import HTTPException

from app.config import AppSettings
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.store import JobStore


class DashboardJobActionRuntime:
    """Encapsulate dashboard-side job control and requeue actions."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        stop_signal_path: Callable[[Path, str], Path],
        resolve_job_workflow_definition: Callable[[JobRecord], Tuple[str, Dict[str, Any], List[Dict[str, Any]]]],
        compute_job_resume_state: Callable[[JobRecord, List[Any], AppSettings], Dict[str, Any]],
        validate_manual_resume_target: Callable[..., Dict[str, Any]],
        append_runtime_recovery_trace_for_job: Callable[..., None],
        ensure_patch_accepting_new_jobs: Callable[[], None],
    ) -> None:
        self.store = store
        self.settings = settings
        self.stop_signal_path = stop_signal_path
        self.resolve_job_workflow_definition = resolve_job_workflow_definition
        self.compute_job_resume_state = compute_job_resume_state
        self.validate_manual_resume_target = validate_manual_resume_target
        self.append_runtime_recovery_trace_for_job = append_runtime_recovery_trace_for_job
        self.ensure_patch_accepting_new_jobs = ensure_patch_accepting_new_jobs

    def request_job_stop(self, job_id: str) -> Dict[str, Any]:
        """Request graceful stop for one queued/running job."""

        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        if job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            raise HTTPException(status_code=400, detail="실행 중 작업에서만 정지 요청할 수 있습니다.")

        path = self.stop_signal_path(self.settings.data_dir, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stop\n", encoding="utf-8")
        return {"requested": True, "job_id": job_id, "stop_file": str(path)}

    def requeue_job(self, job_id: str) -> Dict[str, Any]:
        """Requeue one failed job from dashboard."""

        self.ensure_patch_accepting_new_jobs()
        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            return {"requeued": False, "reason": "already_active", "job_id": job_id}
        if job.status != JobStatus.FAILED.value:
            raise HTTPException(status_code=400, detail="실패 상태 작업만 재큐잉할 수 있습니다.")

        self.store.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            attempt=0,
            error_message=None,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            manual_resume_mode="",
            manual_resume_node_id="",
            manual_resume_requested_at=None,
            manual_resume_note="",
        )
        self.store.enqueue_job(job_id)
        return {"requeued": True, "job_id": job_id}

    def retry_dead_letter_job(self, job_id: str, *, note: str = "") -> Dict[str, Any]:
        """Requeue one dead-lettered job with explicit operator trace."""

        self.ensure_patch_accepting_new_jobs()
        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            raise HTTPException(status_code=400, detail="대기 또는 실행 중 작업은 dead-letter 재시도를 할 수 없습니다.")
        if job.status != JobStatus.FAILED.value or str(job.recovery_status or "").strip() != "dead_letter":
            raise HTTPException(status_code=400, detail="dead-letter 상태의 실패 작업만 다시 큐에 넣을 수 있습니다.")

        note = str(note or "").strip()
        previous_reason = str(job.recovery_reason or job.error_message or "").strip()
        retry_reason = note or (
            f"운영자가 dead-letter 작업을 다시 큐에 넣었습니다. 이전 사유: {previous_reason}"
            if previous_reason
            else "운영자가 dead-letter 작업을 다시 큐에 넣었습니다."
        )

        self.store.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            attempt=0,
            error_message=None,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            recovery_status="dead_letter_requeued",
            recovery_reason=retry_reason,
            recovery_count=0,
            last_recovered_at=utc_now_iso(),
            manual_resume_mode="",
            manual_resume_node_id="",
            manual_resume_requested_at=None,
            manual_resume_note="",
        )
        self.store.enqueue_job(job_id)

        updated = self.store.get_job(job_id)
        assert updated is not None
        self.append_runtime_recovery_trace_for_job(
            self.settings,
            updated,
            source="dashboard_dead_letter_retry",
            reason_code="dead_letter_retry",
            reason=retry_reason,
            decision="retry_from_dead_letter",
            recovery_status="dead_letter_requeued",
            recovery_count=int(updated.recovery_count or 0),
            details={
                "previous_recovery_status": "dead_letter",
                "previous_reason": previous_reason,
                "operator_note": note,
                "retry_from_scratch": True,
            },
        )
        return {
            "queued": True,
            "job_id": job_id,
            "recovery_status": "dead_letter_requeued",
            "reason": retry_reason,
        }

    def manual_retry_workflow_job(
        self,
        job_id: str,
        *,
        mode: str,
        node_id: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """Queue one failed/completed job with explicit manual rerun/resume policy."""

        self.ensure_patch_accepting_new_jobs()
        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            raise HTTPException(status_code=400, detail="대기 또는 실행 중 작업에는 수동 재개를 설정할 수 없습니다.")

        requested_mode = str(mode or "").strip().lower()
        if requested_mode not in {"full_rerun", "resume_failed_node", "resume_from_node"}:
            raise HTTPException(status_code=400, detail="지원하지 않는 수동 재개 모드입니다.")

        workflow_id, _, ordered_nodes = self.resolve_job_workflow_definition(job)
        if not workflow_id or not ordered_nodes:
            raise HTTPException(status_code=400, detail="워크플로우를 찾지 못해 수동 재개를 설정할 수 없습니다.")

        selected_node_id = ""
        selected_reason = ""
        if requested_mode == "resume_failed_node":
            current_resume_state = self.compute_job_resume_state(
                job,
                self.store.list_node_runs(job_id),
                self.settings,
            )
            selected_node_id = str(current_resume_state.get("failed_node_id", "")).strip()
            validation = self.validate_manual_resume_target(
                ordered_nodes=ordered_nodes,
                node_id=selected_node_id,
            )
            if not validation.get("valid"):
                raise HTTPException(
                    status_code=400,
                    detail=str(validation.get("reason", "실패 노드에서 수동 재개할 수 없습니다.")),
                )
            selected_reason = str(current_resume_state.get("reason", "")).strip()
        elif requested_mode == "resume_from_node":
            selected_node_id = str(node_id or "").strip()
            validation = self.validate_manual_resume_target(
                ordered_nodes=ordered_nodes,
                node_id=selected_node_id,
            )
            if not validation.get("valid"):
                raise HTTPException(
                    status_code=400,
                    detail=str(validation.get("reason", "선택한 노드에서 수동 재개할 수 없습니다.")),
                )
            selected_reason = str(validation.get("reason", "")).strip()
        else:
            selected_reason = "운영자가 전체 재실행을 지정했습니다."

        next_attempt = max(1, int(job.attempt or 0) + 1)
        next_max_attempts = max(int(job.max_attempts or 1), next_attempt)
        note = str(note or "").strip()
        recovery_status = "manual_rerun_queued" if requested_mode == "full_rerun" else "manual_resume_queued"
        recovery_reason = note or selected_reason or (
            "운영자가 전체 재실행을 지정했습니다."
            if requested_mode == "full_rerun"
            else "운영자가 수동 재개를 지정했습니다."
        )
        self.store.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            max_attempts=next_max_attempts,
            error_message=None,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            recovery_status=recovery_status,
            recovery_reason=recovery_reason,
            manual_resume_mode=requested_mode,
            manual_resume_node_id=selected_node_id,
            manual_resume_requested_at=utc_now_iso(),
            manual_resume_note=note,
        )
        self.store.enqueue_job(job_id)

        updated = self.store.get_job(job_id)
        assert updated is not None
        trace_decision = "manual_rerun_requeue" if requested_mode == "full_rerun" else "manual_resume_requeue"
        trace_reason_code = "manual_rerun_requeue" if requested_mode == "full_rerun" else "manual_resume_requeue"
        self.append_runtime_recovery_trace_for_job(
            self.settings,
            updated,
            source="dashboard_manual_retry",
            reason_code=trace_reason_code,
            reason=recovery_reason,
            decision=trace_decision,
            recovery_status=recovery_status,
            recovery_count=int(updated.recovery_count or 0),
            details={
                "previous_recovery_status": str(job.recovery_status or "").strip(),
                "previous_reason": str(job.recovery_reason or job.error_message or "").strip(),
                "operator_note": note,
                "target_node_id": selected_node_id,
                "retry_from_scratch": requested_mode == "full_rerun",
            },
        )
        node_runs = self.store.list_node_runs(job_id)
        resume_state = self.compute_job_resume_state(updated, node_runs, self.settings)
        return {
            "queued": True,
            "job_id": job_id,
            "workflow_id": workflow_id,
            "mode": requested_mode,
            "target_node_id": selected_node_id,
            "next_attempt": next_attempt,
            "resume_state": resume_state,
        }

    def requeue_failed_jobs(self) -> Dict[str, Any]:
        """Requeue every failed job in one action."""

        self.ensure_patch_accepting_new_jobs()
        jobs = self.store.list_jobs()
        failed_job_ids = [job.job_id for job in jobs if job.status == JobStatus.FAILED.value]
        for job_id in failed_job_ids:
            self.store.update_job(
                job_id,
                status=JobStatus.QUEUED.value,
                stage=JobStage.QUEUED.value,
                attempt=0,
                error_message=None,
                started_at=None,
                finished_at=None,
                heartbeat_at=None,
                manual_resume_mode="",
                manual_resume_node_id="",
                manual_resume_requested_at=None,
                manual_resume_note="",
            )
            self.store.enqueue_job(job_id)
        return {"requeued": len(failed_job_ids)}
