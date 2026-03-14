"""Post-update health checks for patch updater runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import request as urllib_request

from app.patch_service_runtime import PatchServiceRuntime
from app.store import JobStore


class PatchHealthRuntime:
    """Validate API/worker/store state after patch restart completes."""

    def __init__(
        self,
        *,
        store: JobStore,
        patch_service_runtime: PatchServiceRuntime,
        api_health_url: str,
        updater_status_file: Path,
        updater_service_name: str,
        utc_now_iso: Callable[[], str],
        http_get: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self.store = store
        self.patch_service_runtime = patch_service_runtime
        self.api_health_url = api_health_url
        self.updater_status_file = updater_status_file
        self.updater_service_name = updater_service_name
        self.utc_now_iso = utc_now_iso
        self.http_get = http_get or self._default_http_get

    def build_post_update_health_payload(self) -> Dict[str, Any]:
        """Return one operator-safe health payload after patch restart."""

        checked_at = self.utc_now_iso()
        api_result = self._check_api_health()
        worker_result = self._check_service_active(self.patch_service_runtime.worker_service_name)
        updater_result = self._check_service_active(self.updater_service_name)
        queue_result = self._check_queue_health()
        patch_lock_result = self._check_patch_lock()
        updater_status_result = self._check_updater_status()

        checks = {
            "api": api_result,
            "worker": worker_result,
            "updater": updater_result,
            "queue": queue_result,
            "patch_lock": patch_lock_result,
            "updater_status": updater_status_result,
        }
        failed_checks = [name for name, payload in checks.items() if not bool(payload.get("ok"))]
        overall_ok = not failed_checks

        return {
            "checked_at": checked_at,
            "ok": overall_ok,
            "status": "healthy" if overall_ok else "failed",
            "failed_checks": failed_checks,
            "checks": checks,
            "summary": (
                "패치 후 API/worker/queue 상태가 모두 정상입니다."
                if overall_ok
                else f"패치 후 상태 확인 실패: {', '.join(failed_checks)}"
            ),
        }

    def _check_api_health(self) -> Dict[str, Any]:
        try:
            payload = self.http_get(self.api_health_url)
        except Exception as exc:  # pragma: no cover - defensive network boundary
            return {
                "ok": False,
                "url": self.api_health_url,
                "error": str(exc),
            }
        status_code = int(payload.get("status_code") or 0)
        body = payload.get("body")
        body_status = ""
        if isinstance(body, dict):
            body_status = str(body.get("status") or "")
        return {
            "ok": status_code == 200 and body_status == "ok",
            "url": self.api_health_url,
            "status_code": status_code,
            "body_status": body_status,
        }

    def _check_service_active(self, service_name: str) -> Dict[str, Any]:
        active = self.patch_service_runtime.service_manager.is_active(service_name)
        return {
            "ok": bool(active),
            "service_name": service_name,
            "active": bool(active),
        }

    def _check_queue_health(self) -> Dict[str, Any]:
        jobs = self.store.list_jobs()
        queued = 0
        running = 0
        for job in jobs:
            status = str(job.status or "").strip()
            if status == "queued":
                queued += 1
            elif status == "running":
                running += 1
        return {
            "ok": True,
            "queued_count": queued,
            "running_count": running,
        }

    def _check_patch_lock(self) -> Dict[str, Any]:
        payload = self.patch_service_runtime.read_patch_lock_payload()
        return {
            "ok": not bool(payload.get("active")),
            "active": bool(payload.get("active")),
            "status": str(payload.get("status") or ""),
        }

    def _check_updater_status(self) -> Dict[str, Any]:
        if not self.updater_status_file.exists():
            return {
                "ok": False,
                "status": "missing",
                "message": "updater status 파일이 없습니다.",
            }
        try:
            payload = json.loads(self.updater_status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "status": "error",
                "message": str(exc),
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "status": "invalid",
                "message": "updater status payload 형식이 올바르지 않습니다.",
            }
        return {
            "ok": True,
            "status": str(payload.get("status") or ""),
            "service_name": str(payload.get("service_name") or ""),
            "active_patch_run_id": str(payload.get("active_patch_run_id") or ""),
        }

    @staticmethod
    def _default_http_get(url: str) -> Dict[str, Any]:
        with urllib_request.urlopen(url, timeout=5) as response:
            body_text = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                body = {"raw": body_text}
            return {
                "status_code": int(getattr(response, "status", 0) or 0),
                "body": body,
            }
