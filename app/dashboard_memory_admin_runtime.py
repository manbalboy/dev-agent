"""Memory admin helper runtime for dashboard APIs."""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import HTTPException

from app.config import AppSettings
from app.memory.runtime_store import MemoryRuntimeStore
from app.store import JobStore


def normalize_memory_state(value: str) -> str:
    """Normalize one optional memory state filter/override."""

    normalized = str(value or "").strip().lower()
    if normalized in {"", "active", "candidate", "promoted", "decayed", "banned", "archived"}:
        return normalized
    return ""


def normalize_backlog_priority(value: str) -> str:
    """Normalize one optional backlog priority filter."""

    normalized = str(value or "").strip().upper()
    if normalized in {"", "P0", "P1", "P2", "P3"}:
        return normalized
    return ""


def normalize_backlog_action(value: str) -> str:
    """Normalize one operator action for backlog candidates."""

    normalized = str(value or "").strip().lower()
    if normalized in {"approve", "queue", "dismiss"}:
        return normalized
    return ""


def build_memory_detail_payload(
    runtime_store: MemoryRuntimeStore,
    *,
    memory_id: str,
) -> Dict[str, Any] | None:
    """Return one detailed memory payload for operator inspection."""

    entry = runtime_store.get_entry(memory_id)
    if entry is None:
        return None
    feedback_rows = runtime_store.list_feedback(memory_id=memory_id)
    evidence_rows = runtime_store.list_evidence(memory_id)
    return {
        "entry": entry,
        "evidence": evidence_rows[:20],
        "feedback": list(reversed(feedback_rows[-20:])),
    }


class DashboardMemoryAdminRuntime:
    """Encapsulate dashboard memory admin search/detail/action behavior."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        get_memory_runtime_store: Callable[[AppSettings], MemoryRuntimeStore],
        utc_now_iso: Callable[[], str],
        queue_followup_job_from_backlog_candidate: Callable[..., tuple[Any, Any]],
    ) -> None:
        self.store = store
        self.settings = settings
        self._get_memory_runtime_store = get_memory_runtime_store
        self._utc_now_iso = utc_now_iso
        self._queue_followup_job_from_backlog_candidate = queue_followup_job_from_backlog_candidate

    def search_entries(
        self,
        *,
        q: str,
        state: str,
        memory_type: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        """Search memory runtime entries with lightweight filters for admin UI."""

        normalized_state = normalize_memory_state(state)
        if state.strip() and not normalized_state:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 memory state 입니다: {state}")
        runtime_store = self._get_memory_runtime_store(self.settings)
        runtime_store.refresh_rankings(as_of=self._utc_now_iso())
        items = runtime_store.search_entries(
            query=q,
            state=normalized_state,
            memory_type=memory_type,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "filters": {
                "q": q.strip(),
                "state": normalized_state,
                "memory_type": memory_type.strip().lower(),
                "repository": repository.strip(),
                "execution_repository": execution_repository.strip(),
                "app_code": app_code.strip(),
                "workflow_id": workflow_id.strip(),
                "limit": limit,
            },
        }

    def list_backlog_candidates(
        self,
        *,
        q: str,
        state: str,
        priority: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        """List memory-backed autonomous backlog candidates for admin review."""

        normalized_priority = normalize_backlog_priority(priority)
        if priority.strip() and not normalized_priority:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 backlog priority 입니다: {priority}")
        runtime_store = self._get_memory_runtime_store(self.settings)
        items = runtime_store.list_backlog_candidates(
            query=q,
            state=state,
            priority=normalized_priority,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "filters": {
                "q": q.strip(),
                "state": state.strip().lower(),
                "priority": normalized_priority,
                "repository": repository.strip(),
                "execution_repository": execution_repository.strip(),
                "app_code": app_code.strip(),
                "workflow_id": workflow_id.strip(),
                "limit": limit,
            },
        }

    def apply_backlog_action(self, *, candidate_id: str, action: str, note: str) -> Dict[str, Any]:
        """Apply one small operator action to a backlog candidate."""

        normalized_action = normalize_backlog_action(action)
        if not normalized_action:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 backlog action 입니다: {action}")

        runtime_store = self._get_memory_runtime_store(self.settings)
        candidate = runtime_store.get_backlog_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"candidate_id를 찾을 수 없습니다: {candidate_id}")

        normalized_note = str(note or "").strip()
        if normalized_action == "approve":
            updated = runtime_store.set_backlog_candidate_state(
                candidate_id,
                state="approved",
                payload_updates={
                    "approved_at": self._utc_now_iso(),
                    "operator_note": normalized_note,
                    "last_action": "approve",
                },
            )
            assert updated is not None
            return {"ok": True, "action": normalized_action, "candidate": updated}

        if normalized_action == "dismiss":
            updated = runtime_store.set_backlog_candidate_state(
                candidate_id,
                state="dismissed",
                payload_updates={
                    "dismissed_at": self._utc_now_iso(),
                    "operator_note": normalized_note,
                    "last_action": "dismiss",
                },
            )
            assert updated is not None
            return {"ok": True, "action": normalized_action, "candidate": updated}

        if str(candidate.get("state", "")).strip().lower() == "queued":
            queued_job_id = str((candidate.get("payload", {}) or {}).get("queued_job_id", "")).strip()
            return {
                "ok": True,
                "action": normalized_action,
                "already_queued": True,
                "candidate": candidate,
                "queued_job_id": queued_job_id,
            }

        queued_job, artifact_path = self._queue_followup_job_from_backlog_candidate(
            candidate=candidate,
            runtime_store=runtime_store,
            note=normalized_note,
        )
        updated = runtime_store.get_backlog_candidate(candidate_id)
        assert updated is not None
        return {
            "ok": True,
            "action": normalized_action,
            "candidate": updated,
            "queued_job_id": queued_job.job_id,
            "artifact_path": str(artifact_path),
        }

    def get_memory_detail(self, *, memory_id: str) -> Dict[str, Any]:
        """Return one detailed memory payload including evidence and feedback."""

        runtime_store = self._get_memory_runtime_store(self.settings)
        runtime_store.refresh_rankings(as_of=self._utc_now_iso())
        payload = build_memory_detail_payload(runtime_store, memory_id=memory_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"memory_id를 찾을 수 없습니다: {memory_id}")
        return payload

    def override_memory(self, *, memory_id: str, state: str, note: str) -> Dict[str, Any]:
        """Apply or clear one manual memory state override."""

        normalized_state = normalize_memory_state(state)
        if state.strip() and not normalized_state:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 memory override state 입니다: {state}")
        runtime_store = self._get_memory_runtime_store(self.settings)
        updated = runtime_store.set_manual_override(memory_id, state=normalized_state, note=note)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"memory_id를 찾을 수 없습니다: {memory_id}")
        detail = build_memory_detail_payload(runtime_store, memory_id=memory_id)
        return {
            "saved": True,
            "memory_id": memory_id,
            "manual_state_override": normalized_state,
            "entry": updated,
            "detail": detail,
        }
