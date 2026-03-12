"""Structured dead-letter summary helpers."""

from __future__ import annotations

from typing import Any, Dict


def build_dead_letter_summary(
    *,
    failure_class: str,
    provider_hint: str,
    stage_family: str,
    reason_code: str,
    reason: str,
    source: str,
    generated_at: str = "",
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build an operator-facing summary for one dead-lettered job."""

    payload = details if isinstance(details, dict) else {}
    normalized_class = str(failure_class or "").strip() or "unknown_runtime"
    normalized_provider = str(provider_hint or "").strip() or "unknown"
    normalized_stage_family = str(stage_family or "").strip() or "unknown"
    normalized_reason_code = str(reason_code or "").strip() or normalized_class
    normalized_source = str(source or "").strip() or "runtime"
    summary_reason = str(reason or "").strip() or "반복 실패로 작업이 격리됐습니다."
    upstream_recovery_status = str(payload.get("upstream_recovery_status", "")).strip()

    title = "반복 실패로 작업이 격리됨"
    if normalized_class == "workflow_contract":
        title = "워크플로우 계약 문제로 작업이 격리됨"
    elif normalized_class == "git_conflict":
        title = "Git 충돌로 작업이 격리됨"
    elif normalized_class in {"provider_timeout", "provider_quota", "provider_auth"}:
        title = "공급자 문제로 작업이 격리됨"
    elif normalized_class == "test_failure":
        title = "테스트 반복 실패로 작업이 격리됨"

    recommended_actions = [
        "원인 로그와 STATUS.md를 확인한 뒤 재실행 여부를 결정합니다.",
        "필요하면 수동 재개 또는 새 작업으로 다시 큐에 넣습니다.",
    ]
    if normalized_class in {"provider_timeout", "provider_quota", "provider_auth"}:
        recommended_actions.insert(0, "공급자 상태나 인증/쿼터를 먼저 확인합니다.")
    elif normalized_class == "git_conflict":
        recommended_actions.insert(0, "브랜치 상태와 원격 충돌 여부를 먼저 확인합니다.")
    elif normalized_class == "workflow_contract":
        recommended_actions.insert(0, "워크플로우 정의와 노드 계약을 먼저 검토합니다.")

    return {
        "active": True,
        "title": title,
        "summary": summary_reason,
        "failure_class": normalized_class,
        "provider_hint": normalized_provider,
        "stage_family": normalized_stage_family,
        "reason_code": normalized_reason_code,
        "source": normalized_source,
        "generated_at": str(generated_at or "").strip(),
        "upstream_recovery_status": upstream_recovery_status,
        "manual_resume_recommended": normalized_class not in {"provider_quota", "provider_auth"},
        "retry_from_scratch_recommended": True,
        "recommended_actions": recommended_actions,
    }
