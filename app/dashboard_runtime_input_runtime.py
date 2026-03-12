"""Runtime-input helper/runtime for dashboard admin APIs."""

from __future__ import annotations

from typing import Any, Dict, List
import uuid

from fastapi import HTTPException

from app.config import AppSettings
from app.models import RuntimeInputRecord, utc_now_iso
from app.runtime_inputs import (
    mask_runtime_input_value,
    normalize_env_var_name,
    normalize_runtime_input_requested_by,
    normalize_runtime_input_scope,
    normalize_runtime_input_status,
    normalize_runtime_input_value_type,
    suggest_runtime_input_drafts,
)
from app.store import JobStore


class DashboardRuntimeInputRuntime:
    """Encapsulate runtime-input serialization and CRUD helpers."""

    def __init__(self, *, store: JobStore, settings: AppSettings) -> None:
        self.store = store
        self.settings = settings

    @staticmethod
    def serialize_runtime_input(record: RuntimeInputRecord) -> Dict[str, Any]:
        """Return one operator-safe runtime input payload for dashboard APIs."""

        sensitive = bool(record.sensitive or normalize_runtime_input_value_type(record.value_type) == "secret")
        normalized_value = str(record.value or "")
        normalized_status = normalize_runtime_input_status(record.status) or "requested"
        return {
            "request_id": record.request_id,
            "repository": str(record.repository or "").strip(),
            "app_code": str(record.app_code or "").strip(),
            "job_id": str(record.job_id or "").strip(),
            "scope": normalize_runtime_input_scope(record.scope) or "repository",
            "key": str(record.key or "").strip(),
            "label": str(record.label or "").strip(),
            "description": str(record.description or "").strip(),
            "value_type": normalize_runtime_input_value_type(record.value_type) or "text",
            "env_var_name": normalize_env_var_name(record.env_var_name, fallback_key=record.key),
            "sensitive": sensitive,
            "status": normalized_status,
            "has_value": bool(normalized_value),
            "display_value": mask_runtime_input_value(normalized_value, sensitive=sensitive),
            "value": normalized_value if normalized_value and not sensitive else "",
            "placeholder": str(record.placeholder or "").strip(),
            "note": str(record.note or "").strip(),
            "requested_by": str(record.requested_by or "operator").strip(),
            "requested_at": str(record.requested_at or "").strip(),
            "provided_at": str(record.provided_at or "").strip(),
            "updated_at": str(record.updated_at or "").strip(),
        }

    def list_runtime_inputs(
        self,
        *,
        q: str,
        status: str,
        scope: str,
        repository: str,
        app_code: str,
        job_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        """List operator-managed runtime input requests/values for dashboard admin."""

        normalized_status = normalize_runtime_input_status(status)
        if status.strip() and not normalized_status:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 runtime input status 입니다: {status}")
        normalized_scope = normalize_runtime_input_scope(scope)
        if scope.strip() and not normalized_scope:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 runtime input scope 입니다: {scope}")

        normalized_query = q.strip().lower()
        items = []
        for record in self.store.list_runtime_inputs():
            payload = self.serialize_runtime_input(record)
            if normalized_status and payload["status"] != normalized_status:
                continue
            if normalized_scope and payload["scope"] != normalized_scope:
                continue
            if repository.strip() and payload["repository"] != repository.strip():
                continue
            if app_code.strip() and payload["app_code"] != app_code.strip():
                continue
            if job_id.strip() and payload["job_id"] != job_id.strip():
                continue
            if normalized_query:
                haystack = " ".join(
                    [
                        str(payload.get("key", "")),
                        str(payload.get("label", "")),
                        str(payload.get("description", "")),
                        str(payload.get("repository", "")),
                        str(payload.get("app_code", "")),
                        str(payload.get("job_id", "")),
                        str(payload.get("env_var_name", "")),
                        str(payload.get("note", "")),
                        str(payload.get("value", "")),
                    ]
                ).lower()
                if normalized_query not in haystack:
                    continue
            items.append(payload)

        items.sort(
            key=lambda item: (
                str(item.get("updated_at", "")),
                str(item.get("request_id", "")),
            ),
            reverse=True,
        )
        return {
            "items": items[:limit],
            "count": len(items),
            "filters": {
                "q": q.strip(),
                "status": normalized_status,
                "scope": normalized_scope,
                "repository": repository.strip(),
                "app_code": app_code.strip(),
                "job_id": job_id.strip(),
                "limit": limit,
            },
        }

    def suggest_runtime_input_drafts(
        self,
        *,
        repository: str,
        app_code: str,
        job_id: str,
        context_text: str,
    ) -> Dict[str, Any]:
        """Suggest operator-approval runtime input drafts from job/context text."""

        job = None
        normalized_job_id = str(job_id or "").strip()
        if normalized_job_id:
            job = self.store.get_job(normalized_job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {normalized_job_id}")

        normalized_repository = str(repository or "").strip() or (job.repository if job is not None else self.settings.allowed_repository)
        normalized_app_code = str(app_code or "").strip() or (job.app_code if job is not None else "")
        merged_context = "\n".join(
            part
            for part in [
                str(context_text or "").strip(),
                str(job.issue_title or "").strip() if job is not None else "",
                normalized_app_code,
                normalized_repository,
            ]
            if part
        )
        suggestions = suggest_runtime_input_drafts(
            context_text=merged_context,
            repository=normalized_repository,
            app_code=normalized_app_code,
            job_id=normalized_job_id,
        )
        return {
            "items": suggestions,
            "count": len(suggestions),
            "context_text": merged_context,
            "repository": normalized_repository,
            "app_code": normalized_app_code,
            "job_id": normalized_job_id,
        }

    def create_runtime_input_request(
        self,
        *,
        repository: str,
        app_code: str,
        job_id: str,
        scope: str,
        key: str,
        label: str,
        description: str,
        value_type: str,
        env_var_name: str,
        sensitive: bool,
        placeholder: str,
        note: str,
        requested_by: str,
    ) -> Dict[str, Any]:
        """Create one small operator runtime input request."""

        normalized_scope = normalize_runtime_input_scope(scope)
        if not normalized_scope:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 runtime input scope 입니다: {scope}")
        normalized_value_type = normalize_runtime_input_value_type(value_type)
        if not normalized_value_type:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 runtime input value_type 입니다: {value_type}")
        normalized_requested_by = normalize_runtime_input_requested_by(requested_by)

        job = None
        normalized_job_id = str(job_id or "").strip()
        if normalized_job_id:
            job = self.store.get_job(normalized_job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {normalized_job_id}")

        normalized_repository = str(repository or "").strip() or (job.repository if job is not None else self.settings.allowed_repository)
        normalized_app_code = str(app_code or "").strip() or (job.app_code if job is not None else "")
        if normalized_scope == "app" and not normalized_app_code:
            raise HTTPException(status_code=400, detail="app scope는 app_code가 필요합니다.")
        if normalized_scope == "job" and not normalized_job_id:
            raise HTTPException(status_code=400, detail="job scope는 job_id가 필요합니다.")

        request_id = f"runtime-input-{uuid.uuid4().hex[:10]}"
        now = utc_now_iso()
        record = RuntimeInputRecord(
            request_id=request_id,
            repository=normalized_repository,
            app_code=normalized_app_code,
            job_id=normalized_job_id,
            scope=normalized_scope,
            key=str(key or "").strip(),
            label=str(label or "").strip() or str(key or "").strip(),
            description=str(description or "").strip(),
            value_type=normalized_value_type,
            env_var_name=normalize_env_var_name(env_var_name, fallback_key=key),
            sensitive=bool(sensitive or normalized_value_type == "secret"),
            status="requested",
            value="",
            placeholder=str(placeholder or "").strip(),
            note=str(note or "").strip(),
            requested_by=normalized_requested_by,
            requested_at=now,
            provided_at=None,
            updated_at=now,
        )
        self.store.upsert_runtime_input(record)
        return {"saved": True, "item": self.serialize_runtime_input(record)}

    def provide_runtime_input(
        self,
        *,
        request_id: str,
        value: str,
        note: str,
    ) -> Dict[str, Any]:
        """Provide or clear one runtime input value."""

        current = self.store.get_runtime_input(request_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"request_id를 찾을 수 없습니다: {request_id}")
        normalized_value = str(value or "")
        now = utc_now_iso()
        updated = RuntimeInputRecord(
            request_id=current.request_id,
            repository=current.repository,
            app_code=current.app_code,
            job_id=current.job_id,
            scope=current.scope,
            key=current.key,
            label=current.label,
            description=current.description,
            value_type=current.value_type,
            env_var_name=normalize_env_var_name(current.env_var_name, fallback_key=current.key),
            sensitive=bool(current.sensitive),
            status="provided" if normalized_value.strip() else "requested",
            value=normalized_value,
            placeholder=current.placeholder,
            note=str(note or "").strip(),
            requested_by=current.requested_by,
            requested_at=current.requested_at,
            provided_at=now if normalized_value.strip() else None,
            updated_at=now,
        )
        self.store.upsert_runtime_input(updated)
        return {"saved": True, "item": self.serialize_runtime_input(updated)}
