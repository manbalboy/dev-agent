"""Structured requeue reason helpers for restart-safe recovery flows."""

from __future__ import annotations

from typing import Any, Dict


REQUEUE_RECOVERY_STATUSES = {
    "auto_recovered",
    "dead_letter_requeued",
    "manual_rerun_queued",
    "manual_resume_queued",
}

REQUEUE_DECISIONS = {
    "requeue",
    "retry_from_dead_letter",
    "manual_rerun_requeue",
    "manual_resume_requeue",
}


def build_requeue_reason_summary(
    *,
    source: str,
    reason_code: str,
    reason: str,
    decision: str,
    recovery_status: str,
    generated_at: str,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build one structured operator-facing requeue reason summary."""

    payload = details if isinstance(details, dict) else {}
    normalized_source = str(source or "").strip()
    normalized_reason_code = str(reason_code or "").strip()
    normalized_decision = str(decision or "").strip()
    normalized_status = str(recovery_status or "").strip()
    normalized_reason = str(reason or "").strip()

    trigger = "unknown"
    title = "재큐잉 사유"
    retry_from_scratch = bool(payload.get("retry_from_scratch"))

    if normalized_source == "worker_stale_recovery":
        trigger = "worker_restart_or_stale_recovery"
        title = "Worker 자동 복구로 다시 큐에 넣음"
        retry_from_scratch = False
    elif normalized_source == "dashboard_dead_letter_retry":
        trigger = "operator_dead_letter_retry"
        title = "운영자 판단으로 dead-letter 작업을 다시 큐에 넣음"
        retry_from_scratch = True
    elif normalized_source == "dashboard_manual_retry":
        trigger = "operator_manual_retry"
        title = "운영자 수동 재개 요청으로 다시 큐에 넣음"
        retry_from_scratch = normalized_decision == "manual_rerun_requeue"
    elif normalized_source == "dashboard_requeue_failed":
        trigger = "operator_failed_requeue"
        title = "운영자 재큐잉 요청"
        retry_from_scratch = True

    summary = normalized_reason or "재큐잉 사유가 기록되지 않았습니다."
    if normalized_decision == "manual_resume_requeue":
        target_node_id = str(payload.get("target_node_id", "")).strip()
        if target_node_id:
            summary = f"{summary} (시작 노드: {target_node_id})"

    return {
        "active": normalized_status in REQUEUE_RECOVERY_STATUSES or normalized_decision in REQUEUE_DECISIONS,
        "title": title,
        "summary": summary,
        "source": normalized_source,
        "reason_code": normalized_reason_code,
        "decision": normalized_decision,
        "recovery_status": normalized_status,
        "generated_at": str(generated_at or "").strip(),
        "trigger": trigger,
        "retry_from_scratch": retry_from_scratch,
        "operator_note": str(payload.get("operator_note", "")).strip(),
        "previous_recovery_status": str(payload.get("previous_recovery_status", "")).strip(),
        "previous_reason": str(payload.get("previous_reason", "")).strip(),
        "target_node_id": str(payload.get("target_node_id", "")).strip(),
    }


def is_requeue_event(*, decision: str, recovery_status: str) -> bool:
    """Return True when one runtime event should surface as requeue reason."""

    return (
        str(decision or "").strip() in REQUEUE_DECISIONS
        or str(recovery_status or "").strip() in REQUEUE_RECOVERY_STATUSES
    )
