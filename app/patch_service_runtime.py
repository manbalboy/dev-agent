"""Patch drain/restart helpers and job-intake boundary controls."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from app.models import JobStatus, PatchRunRecord
from app.store import JobStore


class SystemdServiceManager:
    """Minimal systemctl wrapper for updater-driven service operations."""

    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self.timeout_seconds = timeout_seconds

    def stop(self, service_name: str) -> None:
        self._run("stop", service_name)

    def start(self, service_name: str) -> None:
        self._run("start", service_name)

    def restart(self, service_name: str) -> None:
        self._run("restart", service_name)

    def is_active(self, service_name: str) -> bool:
        completed = subprocess.run(
            ["systemctl", "is-active", service_name],
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        return completed.returncode == 0 and str(completed.stdout or "").strip() == "active"

    def _run(self, action: str, service_name: str) -> None:
        subprocess.run(
            ["systemctl", action, service_name],
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=True,
        )


class PatchServiceRuntime:
    """Control patch drain boundaries and service restart steps."""

    def __init__(
        self,
        *,
        store: JobStore,
        patch_lock_file: Path,
        api_service_name: str,
        worker_service_name: str,
        utc_now_iso: Callable[[], str],
        service_manager: Optional[SystemdServiceManager] = None,
    ) -> None:
        self.store = store
        self.patch_lock_file = patch_lock_file
        self.api_service_name = api_service_name
        self.worker_service_name = worker_service_name
        self.utc_now_iso = utc_now_iso
        self.service_manager = service_manager or SystemdServiceManager()

    def read_patch_lock_payload(self) -> Dict[str, Any]:
        """Return current patch drain boundary payload if present."""

        if not self.patch_lock_file.exists():
            return {
                "active": False,
                "patch_run_id": "",
                "status": "idle",
                "message": "",
                "updated_at": "",
                "details": {},
            }
        try:
            payload = json.loads(self.patch_lock_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "active": False,
                "patch_run_id": "",
                "status": "error",
                "message": "patch lock 상태 파일을 읽을 수 없습니다.",
                "updated_at": "",
                "details": {},
            }
        if not isinstance(payload, dict):
            return {
                "active": False,
                "patch_run_id": "",
                "status": "error",
                "message": "patch lock 상태 형식이 올바르지 않습니다.",
                "updated_at": "",
                "details": {},
            }
        return {
            "active": bool(payload.get("active")),
            "patch_run_id": str(payload.get("patch_run_id") or ""),
            "status": str(payload.get("status") or "idle"),
            "message": str(payload.get("message") or ""),
            "updated_at": str(payload.get("updated_at") or ""),
            "details": dict(payload.get("details") or {}),
        }

    def ensure_patch_accepting_new_jobs(self) -> None:
        """Raise when patch drain boundary is currently blocking new jobs."""

        payload = self.read_patch_lock_payload()
        if not payload.get("active"):
            return
        patch_run_id = str(payload.get("patch_run_id") or "").strip()
        message = str(payload.get("message") or "패치 진행 중이라 새 작업을 받을 수 없습니다.").strip()
        detail = message
        if patch_run_id:
            detail = f"{message} patch_run_id={patch_run_id}"
        raise HTTPException(status_code=409, detail=detail)

    def list_active_jobs_for_drain(self) -> List[Dict[str, str]]:
        """List queued/running jobs that should drain before restart."""

        active_items: List[Dict[str, str]] = []
        for job in self.store.list_jobs():
            if str(job.status or "").strip() not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
                continue
            active_items.append(
                {
                    "job_id": job.job_id,
                    "status": str(job.status or ""),
                    "stage": str(job.stage or ""),
                    "issue_title": str(job.issue_title or ""),
                    "app_code": str(job.app_code or ""),
                }
            )
        return active_items

    def activate_patch_lock(self, *, patch_run: PatchRunRecord, active_jobs: List[Dict[str, str]]) -> Dict[str, Any]:
        """Block new job intake while a patch run is draining active work."""

        now = self.utc_now_iso()
        payload = {
            "active": True,
            "patch_run_id": patch_run.patch_run_id,
            "status": "draining",
            "message": "패치 진행 중이라 새 작업 수락이 일시 중지되었습니다.",
            "updated_at": now,
            "details": {
                "active_job_count": len(active_jobs),
                "active_job_ids": [item["job_id"] for item in active_jobs[:20]],
                "worker_service_name": self.worker_service_name,
                "api_service_name": self.api_service_name,
            },
        }
        self._write_lock_payload(payload)
        return payload

    def clear_patch_lock(self, *, status: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Clear the patch intake boundary after restart completes or aborts."""

        payload = {
            "active": False,
            "patch_run_id": "",
            "status": status,
            "message": message,
            "updated_at": self.utc_now_iso(),
            "details": dict(details or {}),
        }
        self._write_lock_payload(payload)
        return payload

    def restart_services_for_patch(self) -> Dict[str, Any]:
        """Stop the worker, restart API, then restart the worker."""

        operations: List[Dict[str, str]] = []

        self.service_manager.stop(self.worker_service_name)
        operations.append({"action": "stop", "service": self.worker_service_name})

        self.service_manager.restart(self.api_service_name)
        operations.append({"action": "restart", "service": self.api_service_name})

        self.service_manager.restart(self.worker_service_name)
        operations.append({"action": "restart", "service": self.worker_service_name})

        return {
            "operations": operations,
            "api_service_name": self.api_service_name,
            "worker_service_name": self.worker_service_name,
            "completed_at": self.utc_now_iso(),
        }

    def _write_lock_payload(self, payload: Dict[str, Any]) -> None:
        self.patch_lock_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.patch_lock_file.with_suffix(f"{self.patch_lock_file.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.patch_lock_file)
