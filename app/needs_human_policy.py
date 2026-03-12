"""Structured operator-handoff helpers for needs-human states."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


_TITLE_MAP = {
    "provider_quota": "모델 할당량 또는 요금제 확인 필요",
    "provider_auth": "인증 정보 확인 필요",
    "provider_timeout": "공급자 장애 또는 지연 확인 필요",
    "workflow_contract": "워크플로우 설정 확인 필요",
    "git_conflict": "Git 충돌 수동 확인 필요",
    "stale_heartbeat": "워커 상태 수동 확인 필요",
    "tool_failure": "도구 연동 상태 확인 필요",
    "test_failure": "실패 원인 수동 검토 필요",
    "unknown_runtime": "운영자 확인 필요",
}

_SUMMARY_MAP = {
    "provider_quota": "현재 실패는 공급자 사용량 또는 quota 문제로 분류되었습니다. 자동 재시도보다 quota 복구 여부 확인이 우선입니다.",
    "provider_auth": "현재 실패는 인증 또는 권한 문제로 분류되었습니다. API 키, 토큰, 권한 범위를 먼저 점검해야 합니다.",
    "provider_timeout": "현재 실패는 공급자 응답 지연 또는 장애 징후로 분류되었습니다. 같은 공급자에 계속 재시도하기보다 route 전환 또는 잠시 격리가 우선입니다.",
    "workflow_contract": "현재 실패는 워크플로우 정의 또는 수동 재개 계약 불일치로 분류되었습니다. 설정 정합성 확인 후 재개해야 합니다.",
    "git_conflict": "현재 실패는 Git/PR 단계 충돌로 분류되었습니다. 브랜치 상태를 먼저 정리한 뒤 다시 시도해야 합니다.",
    "stale_heartbeat": "현재 실패는 장시간 실행 중 heartbeat가 멈춘 상황으로 분류되었습니다. 워커와 장시간 프로세스 상태를 함께 점검해야 합니다.",
    "tool_failure": "현재 실패는 외부 도구 또는 검색 경로 문제로 분류되었습니다. 도구 상태를 확인한 뒤 다시 시도해야 합니다.",
    "test_failure": "현재 실패는 테스트 실패로 분류되었습니다. 리포트와 failure reason을 보고 수정 방향을 정한 뒤 재시도해야 합니다.",
    "unknown_runtime": "정규화된 자동 복구 경로가 없어 운영자 검토가 필요합니다.",
}

_ACTIONS_MAP = {
    "provider_quota": [
        "해당 공급자의 quota 또는 요금제 상태를 확인합니다.",
        "quota가 복구되면 수동 재개 또는 재실행을 진행합니다.",
        "같은 공급자를 쓰는 다른 작업도 함께 점검합니다.",
    ],
    "provider_auth": [
        "API 키, 토큰, 권한 범위를 확인합니다.",
        "필요한 운영자 입력이나 secret이 누락되지 않았는지 확인합니다.",
        "인증 정보 수정 후 수동 재개를 진행합니다.",
    ],
    "provider_timeout": [
        "같은 공급자 실패가 누적되는지 운영 지표와 recovery trace를 함께 확인합니다.",
        "가능하면 fallback route 또는 대체 공급자로 전환합니다.",
        "지속되면 해당 공급자를 잠시 격리한 뒤 운영자 승인 후 재개합니다.",
    ],
    "workflow_contract": [
        "workflow_id, 수동 재개 노드, route 매핑을 확인합니다.",
        "잘못된 노드 지정이나 계약 위반 로그를 먼저 수정합니다.",
        "설정 수정 후 안전한 노드부터 다시 시작합니다.",
    ],
    "git_conflict": [
        "로컬 브랜치와 원격 브랜치 상태를 비교합니다.",
        "충돌 정리 또는 PR 대상 브랜치 정합성을 먼저 맞춥니다.",
        "정리 후 push/pr 단계를 다시 시도합니다.",
    ],
    "stale_heartbeat": [
        "워커 프로세스와 장시간 실행 명령 상태를 확인합니다.",
        "최근 debug log와 runtime recovery trace를 함께 점검합니다.",
        "문제가 해소되면 수동 재개 또는 재큐잉을 진행합니다.",
    ],
    "tool_failure": [
        "실패한 tool 이름과 query, 오류 메시지를 확인합니다.",
        "외부 MCP/search/tool 서버 상태를 점검합니다.",
        "도구가 정상화되면 관련 단계부터 다시 시도합니다.",
    ],
    "test_failure": [
        "TEST_FAILURE_REASON, TEST_REPORT, REVIEW 문서를 확인합니다.",
        "실패 범위를 줄인 뒤 targeted fix를 적용합니다.",
        "수정 후 테스트 단계부터 다시 실행합니다.",
    ],
    "unknown_runtime": [
        "debug log와 runtime recovery trace를 먼저 확인합니다.",
        "최근 변경점과 실패 직전 stage를 함께 검토합니다.",
        "명확한 원인 파악 후 수동 재개 또는 재실행을 선택합니다.",
    ],
}

_MANUAL_RESUME_RECOMMENDED = {
    "provider_quota",
    "provider_auth",
    "provider_timeout",
    "workflow_contract",
    "git_conflict",
    "stale_heartbeat",
    "tool_failure",
    "test_failure",
}


def build_needs_human_summary(
    *,
    failure_class: str = "",
    provider_hint: str = "",
    stage_family: str = "",
    reason_code: str = "",
    reason: str = "",
    recovery_path: str = "",
    source: str = "",
    generated_at: str = "",
    details: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return one normalized operator-handoff summary."""

    normalized_class = str(failure_class or "").strip() or "unknown_runtime"
    normalized_provider = str(provider_hint or "").strip() or "unknown"
    normalized_stage_family = str(stage_family or "").strip() or "unknown"
    normalized_reason_code = str(reason_code or "").strip() or normalized_class
    normalized_recovery_path = str(recovery_path or "").strip()
    normalized_source = str(source or "").strip()
    normalized_reason = str(reason or "").strip()
    detail_map = details if isinstance(details, Mapping) else {}
    retry_policy = detail_map.get("retry_policy") if isinstance(detail_map.get("retry_policy"), Mapping) else {}
    cooldown_seconds = int(retry_policy.get("cooldown_seconds", 0) or 0)
    effective_retry_budget = int(detail_map.get("effective_retry_budget", retry_policy.get("retry_budget", 0)) or 0)

    title = _TITLE_MAP.get(normalized_class, _TITLE_MAP["unknown_runtime"])
    summary = _SUMMARY_MAP.get(normalized_class, _SUMMARY_MAP["unknown_runtime"])
    recommended_actions: List[str] = list(_ACTIONS_MAP.get(normalized_class, _ACTIONS_MAP["unknown_runtime"]))
    if cooldown_seconds > 0:
        recommended_actions.append(f"추가 재시도 전 최소 {cooldown_seconds}초 cooldown을 둡니다.")
    if effective_retry_budget > 0:
        recommended_actions.append(f"현재 분류 기준 자동 재시도 예산은 {effective_retry_budget}회입니다.")

    return {
        "active": True,
        "title": title,
        "summary": summary,
        "failure_class": normalized_class,
        "provider_hint": normalized_provider,
        "stage_family": normalized_stage_family,
        "reason_code": normalized_reason_code,
        "reason": normalized_reason,
        "recovery_path": normalized_recovery_path,
        "source": normalized_source,
        "generated_at": str(generated_at or "").strip(),
        "recommended_actions": recommended_actions,
        "manual_resume_recommended": normalized_class in _MANUAL_RESUME_RECOMMENDED,
        "cooldown_seconds": cooldown_seconds,
        "effective_retry_budget": effective_retry_budget,
    }
