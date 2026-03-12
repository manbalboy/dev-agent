"""Structured recovery trace artifact helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.config import AppSettings
from app.dead_letter_policy import build_dead_letter_summary
from app.failure_classification import build_failure_evidence_summary
from app.models import JobRecord, utc_now_iso
from app.needs_human_policy import build_needs_human_summary
from app.requeue_reason_runtime import build_requeue_reason_summary, is_requeue_event
from app.workflow_resume import build_workflow_artifact_paths


def job_execution_repository(job: JobRecord) -> str:
    """Return the execution repository for one job."""

    return str(job.source_repository or job.repository or "").strip()


def append_runtime_recovery_trace(
    repository_path: Path,
    *,
    source: str,
    reason_code: str,
    reason: str,
    decision: str,
    stage: str = "",
    gate_label: str = "",
    job_id: str = "",
    attempt: int = 0,
    recovery_status: str = "",
    recovery_count: int = 0,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Append one recovery event to the canonical runtime trace artifact."""

    now = utc_now_iso()
    trace_path = build_workflow_artifact_paths(repository_path)["runtime_recovery_trace"]
    payload: Dict[str, Any] = {}
    if trace_path.exists():
        try:
            payload = json.loads(trace_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {}
    events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
    previous_event_count = int(payload.get("event_count", len(events)) or 0)
    event = {
        "generated_at": now,
        "source": str(source or "").strip() or "runtime",
        "job_id": str(job_id or "").strip(),
        "attempt": int(attempt or 0),
        "stage": str(stage or "").strip(),
        "gate_label": str(gate_label or "").strip(),
        "reason_code": str(reason_code or "").strip() or "unknown_runtime",
        "reason": str(reason or "").strip(),
        "decision": str(decision or "").strip() or "recorded",
        "recovery_status": str(recovery_status or "").strip(),
        "recovery_count": int(recovery_count or 0),
        "details": details or {},
    }
    evidence = build_failure_evidence_summary(
        reason_code=event["reason_code"],
        reason=event["reason"],
        stage=event["stage"],
        source=event["source"],
        generated_at=event["generated_at"],
        details=event["details"],
    )
    event["failure_class"] = evidence["failure_class"]
    event["provider_hint"] = evidence["provider_hint"]
    event["stage_family"] = evidence["stage_family"]
    if event["decision"] in {"needs_human", "provider_quarantined", "provider_circuit_open"} or event["recovery_status"] in {
        "needs_human",
        "provider_quarantined",
        "provider_circuit_open",
    }:
        retry_policy = (
            event["details"].get("retry_policy")
            if isinstance(event["details"], dict) and isinstance(event["details"].get("retry_policy"), dict)
            else {}
        )
        summary_recovery_path = str(retry_policy.get("recovery_path", "")).strip()
        if event["decision"] == "provider_quarantined" or event["recovery_status"] == "provider_quarantined":
            summary_recovery_path = "provider_quarantine"
        if event["decision"] == "provider_circuit_open" or event["recovery_status"] == "provider_circuit_open":
            summary_recovery_path = "provider_circuit_breaker"
        event["needs_human_summary"] = build_needs_human_summary(
            failure_class=event["failure_class"],
            provider_hint=event["provider_hint"],
            stage_family=event["stage_family"],
            reason_code=event["reason_code"],
            reason=event["reason"],
            recovery_path=summary_recovery_path,
            source=event["source"],
            generated_at=event["generated_at"],
            details=event["details"],
        )
    if event["decision"] == "dead_letter" or event["recovery_status"] == "dead_letter":
        event["dead_letter_summary"] = build_dead_letter_summary(
            failure_class=event["failure_class"],
            provider_hint=event["provider_hint"],
            stage_family=event["stage_family"],
            reason_code=event["reason_code"],
            reason=event["reason"],
            source=event["source"],
            generated_at=event["generated_at"],
            details=event["details"],
        )
    if is_requeue_event(decision=event["decision"], recovery_status=event["recovery_status"]):
        event["requeue_reason_summary"] = build_requeue_reason_summary(
            source=event["source"],
            reason_code=event["reason_code"],
            reason=event["reason"],
            decision=event["decision"],
            recovery_status=event["recovery_status"],
            generated_at=event["generated_at"],
            details=event["details"],
        )
    events.append(event)
    result = {
        "generated_at": payload.get("generated_at") or now,
        "latest_event_at": now,
        "event_count": max(previous_event_count, len(events) - 1) + 1,
        "events": events[-20:],
    }
    trace_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def append_runtime_recovery_trace_for_job(
    settings: AppSettings,
    job: JobRecord,
    *,
    source: str,
    reason_code: str,
    reason: str,
    decision: str,
    recovery_status: str = "",
    recovery_count: int = 0,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve repository path from job/settings and append one recovery trace event."""

    repository_path = settings.repository_workspace_path(job_execution_repository(job), job.app_code)
    return append_runtime_recovery_trace(
        repository_path,
        source=source,
        reason_code=reason_code,
        reason=reason,
        decision=decision,
        stage=str(job.stage or "").strip(),
        job_id=job.job_id,
        attempt=int(job.attempt or 0),
        recovery_status=recovery_status,
        recovery_count=recovery_count,
        details=details,
    )
