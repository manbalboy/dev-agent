"""Helpers for operator-provided runtime input requests and resolution."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from app.models import IntegrationRegistryRecord, RuntimeInputRecord


_ENV_SEGMENT_PATTERN = re.compile(r"[^A-Z0-9_]+")
_RUNTIME_INPUT_SCOPE_ORDER = {
    "repository": 1,
    "app": 2,
    "job": 3,
}
_RUNTIME_INPUT_REQUESTED_BY_CHOICES = {
    "operator",
    "assistant_draft",
}
_RUNTIME_INPUT_TEMPLATE_LIBRARY: List[Dict[str, Any]] = [
    {
        "template_id": "google_maps_api_key",
        "service": "google_maps",
        "key": "google_maps_api_key",
        "label": "Google Maps API Key",
        "description": "Google Maps 지도, Places, Geocoding, Directions 기능 구현에 필요합니다.",
        "value_type": "secret",
        "env_var_name": "GOOGLE_MAPS_API_KEY",
        "placeholder": "Google Maps API Key를 입력하세요",
        "keywords": [
            "google maps",
            "지도",
            "geocoding",
            "directions",
            "places api",
            "map sdk",
        ],
        "featured": True,
    },
    {
        "template_id": "stripe_secret_key",
        "service": "stripe",
        "key": "stripe_secret_key",
        "label": "Stripe Secret Key",
        "description": "결제 생성, 웹훅 검증, 결제 상태 조회 같은 서버 측 Stripe 기능에 필요합니다.",
        "value_type": "secret",
        "env_var_name": "STRIPE_SECRET_KEY",
        "placeholder": "Stripe Secret Key를 입력하세요",
        "keywords": [
            "stripe",
            "결제",
            "checkout",
            "payment intent",
            "subscription",
            "웹훅 결제",
        ],
        "featured": True,
    },
    {
        "template_id": "supabase_url",
        "service": "supabase",
        "key": "supabase_url",
        "label": "Supabase URL",
        "description": "Supabase 클라이언트 초기화와 데이터 접근에 사용할 프로젝트 URL입니다.",
        "value_type": "text",
        "env_var_name": "SUPABASE_URL",
        "placeholder": "https://xxxx.supabase.co",
        "keywords": [
            "supabase",
            "auth",
            "realtime",
            "postgres",
            "storage",
        ],
        "featured": True,
    },
    {
        "template_id": "supabase_anon_key",
        "service": "supabase",
        "key": "supabase_anon_key",
        "label": "Supabase Anon Key",
        "description": "Supabase 프론트엔드 클라이언트 초기화에 사용할 publishable key입니다.",
        "value_type": "secret",
        "env_var_name": "SUPABASE_ANON_KEY",
        "placeholder": "Supabase anon key를 입력하세요",
        "keywords": [
            "supabase",
            "auth",
            "realtime",
            "storage",
        ],
        "featured": True,
    },
]


def normalize_runtime_input_scope(value: str) -> str:
    """Return one supported runtime input scope or an empty string."""

    normalized = str(value or "").strip().lower()
    if normalized in {"repository", "app", "job"}:
        return normalized
    return ""


def normalize_runtime_input_value_type(value: str) -> str:
    """Return one supported runtime input value type or an empty string."""

    normalized = str(value or "").strip().lower()
    if normalized in {"text", "secret"}:
        return normalized
    return ""


def normalize_runtime_input_status(value: str) -> str:
    """Return one supported runtime input status or an empty string."""

    normalized = str(value or "").strip().lower()
    if normalized in {"requested", "provided"}:
        return normalized
    return ""


def normalize_runtime_input_requested_by(value: str) -> str:
    """Return one supported runtime input requester label."""

    normalized = str(value or "").strip().lower()
    if normalized in _RUNTIME_INPUT_REQUESTED_BY_CHOICES:
        return normalized
    return "operator"


def normalize_env_var_name(value: str, *, fallback_key: str = "") -> str:
    """Return one filesystem/shell safe environment variable name."""

    candidate = str(value or "").strip().upper()
    if not candidate and fallback_key:
        candidate = str(fallback_key or "").strip().upper()
    candidate = candidate.replace("-", "_").replace(" ", "_")
    candidate = _ENV_SEGMENT_PATTERN.sub("_", candidate).strip("_")
    if not candidate:
        candidate = "AGENTHUB_RUNTIME_INPUT"
    if candidate[0].isdigit():
        candidate = f"AGENTHUB_{candidate}"
    return candidate


def _normalize_integration_approval_status(value: str, *, approval_required: bool) -> str:
    """Return one normalized integration approval state."""

    normalized = str(value or "").strip().lower()
    if normalized in {"approved", "rejected", "pending"}:
        return normalized
    return "pending" if approval_required else "not_required"


def _build_integration_input_readiness(
    *,
    required_env_keys: List[str],
    runtime_input_records: List[RuntimeInputRecord],
    approval_required: bool,
    approval_status: str,
    approval_note: str,
) -> Dict[str, str]:
    """Return integration-level readiness for env bridge enforcement."""

    provided = 0
    requested = 0
    missing = 0
    for env_var_name in required_env_keys:
        normalized_env = normalize_env_var_name(env_var_name, fallback_key="INTEGRATION_KEY")
        matched_records = [
            record
            for record in runtime_input_records
            if normalize_env_var_name(record.env_var_name, fallback_key=record.key) == normalized_env
        ]
        if any(normalize_runtime_input_status(record.status) == "provided" and str(record.value or "").strip() for record in matched_records):
            provided += 1
        elif matched_records:
            requested += 1
        else:
            missing += 1

    normalized_approval_status = _normalize_integration_approval_status(
        approval_status,
        approval_required=approval_required,
    )
    total = len(required_env_keys)
    if normalized_approval_status == "rejected":
        return {
            "status": "approval_rejected",
            "reason": str(approval_note or "").strip() or "운영자가 이 통합 도입을 보류했습니다.",
        }
    if total <= 0:
        if approval_required and normalized_approval_status != "approved":
            return {
                "status": "approval_required",
                "reason": "필수 env는 없지만 운영자 승인 후에만 사용할 수 있습니다.",
            }
        return {
            "status": "ready",
            "reason": "필수 env가 없어 env bridge 허용 상태입니다.",
        }
    if missing > 0:
        return {
            "status": "input_required",
            "reason": f"필수 env {missing}건이 아직 제공되지 않아 env bridge를 허용할 수 없습니다.",
        }
    if requested > 0 and provided < total:
        return {
            "status": "input_requested",
            "reason": f"필수 env {requested}건이 요청됨 상태라 값이 제공될 때까지 env bridge를 보류합니다.",
        }
    if approval_required and normalized_approval_status != "approved":
        return {
            "status": "approval_required",
            "reason": "필수 env는 준비됐지만 운영자 승인 후에만 env bridge를 허용합니다.",
        }
    return {
        "status": "ready",
        "reason": "승인과 필수 env가 모두 준비돼 env bridge 허용 상태입니다.",
    }


def build_runtime_input_env_bridge_policy(
    *,
    runtime_input_records: List[RuntimeInputRecord],
    integration_registry_entries: Iterable[IntegrationRegistryRecord],
) -> Dict[str, Dict[str, object]]:
    """Return env-bridge allow/block policy derived from integration registry."""

    policy_by_env: Dict[str, Dict[str, object]] = {}
    for record in integration_registry_entries:
        if not bool(record.enabled):
            continue
        required_env_keys = [
            normalize_env_var_name(item, fallback_key="INTEGRATION_KEY")
            for item in list(record.required_env_keys or [])
            if str(item).strip()
        ]
        if not required_env_keys:
            continue
        readiness = _build_integration_input_readiness(
            required_env_keys=required_env_keys,
            runtime_input_records=runtime_input_records,
            approval_required=bool(record.approval_required),
            approval_status=str(record.approval_status or ""),
            approval_note=str(record.approval_note or ""),
        )
        normalized_approval_status = _normalize_integration_approval_status(
            record.approval_status,
            approval_required=bool(record.approval_required),
        )
        linked_payload = {
            "integration_id": str(record.integration_id or "").strip(),
            "display_name": str(record.display_name or record.integration_id or "").strip(),
            "approval_status": normalized_approval_status,
            "input_readiness_status": str(readiness.get("status", "")).strip(),
            "input_readiness_reason": str(readiness.get("reason", "")).strip(),
        }
        for env_var_name in required_env_keys:
            bucket = policy_by_env.setdefault(
                env_var_name,
                {
                    "allowed": False,
                    "reason": "",
                    "linked_integrations": [],
                },
            )
            linked_integrations = list(bucket.get("linked_integrations", []) or [])
            linked_integrations.append(linked_payload)
            bucket["linked_integrations"] = linked_integrations

    for env_var_name, payload in policy_by_env.items():
        linked_integrations = list(payload.get("linked_integrations", []) or [])
        ready_candidates = [
            item for item in linked_integrations if str(item.get("input_readiness_status", "")).strip() == "ready"
        ]
        if ready_candidates:
            primary = ready_candidates[0]
            payload["allowed"] = True
            payload["reason"] = (
                f"{primary.get('display_name') or primary.get('integration_id')}: "
                f"{primary.get('input_readiness_reason') or 'env bridge 허용 상태입니다.'}"
            )
        elif linked_integrations:
            primary = linked_integrations[0]
            payload["allowed"] = False
            payload["reason"] = (
                f"{primary.get('display_name') or primary.get('integration_id')}: "
                f"{primary.get('input_readiness_reason') or 'env bridge 허용 조건이 충족되지 않았습니다.'}"
            )
        else:
            payload["allowed"] = True
            payload["reason"] = "통합 레지스트리에 연결된 정책이 없어 기본 env bridge를 허용합니다."
    return policy_by_env


def mask_runtime_input_value(value: str, *, sensitive: bool) -> str:
    """Return one operator-safe display value."""

    normalized = str(value or "")
    if not normalized:
        return ""
    if not sensitive:
        return normalized
    if len(normalized) <= 4:
        return "*" * len(normalized)
    return f"{normalized[:2]}{'*' * min(12, len(normalized) - 4)}{normalized[-2:]}"


def suggest_runtime_input_drafts(
    *,
    context_text: str,
    repository: str,
    app_code: str,
    job_id: str,
) -> List[Dict[str, Any]]:
    """Return small operator-approval-ready runtime input draft suggestions."""

    normalized_context = str(context_text or "").strip().lower()
    suggestion_scope = "job" if str(job_id or "").strip() else "app" if str(app_code or "").strip() else "repository"
    suggestions: List[Dict[str, Any]] = []

    for order, template in enumerate(_RUNTIME_INPUT_TEMPLATE_LIBRARY):
        keywords = [str(item).strip().lower() for item in template.get("keywords", []) if str(item).strip()]
        matched_keywords = [keyword for keyword in keywords if keyword in normalized_context] if normalized_context else []
        score = len(matched_keywords)
        featured = bool(template.get("featured"))
        if normalized_context and score <= 0:
            continue
        if not normalized_context and not featured:
            continue
        if score > 0:
            reason = f"문맥에서 {', '.join(matched_keywords[:3])} 관련 요구가 감지되었습니다."
            source = "matched"
        else:
            reason = f"{str(template.get('label', '')).strip() or str(template.get('key', '')).strip()} 빠른 템플릿입니다."
            source = "template"
        suggestions.append(
            {
                "template_id": str(template.get("template_id", "")).strip(),
                "service": str(template.get("service", "")).strip(),
                "scope": suggestion_scope,
                "repository": str(repository or "").strip(),
                "app_code": str(app_code or "").strip(),
                "job_id": str(job_id or "").strip(),
                "key": str(template.get("key", "")).strip(),
                "label": str(template.get("label", "")).strip(),
                "description": str(template.get("description", "")).strip(),
                "value_type": normalize_runtime_input_value_type(str(template.get("value_type", "")).strip()) or "text",
                "env_var_name": normalize_env_var_name(str(template.get("env_var_name", "")).strip(), fallback_key=str(template.get("key", "")).strip()),
                "sensitive": normalize_runtime_input_value_type(str(template.get("value_type", "")).strip()) == "secret",
                "placeholder": str(template.get("placeholder", "")).strip(),
                "requested_by": "assistant_draft",
                "matched_keywords": matched_keywords,
                "reason": reason,
                "source": source,
                "score": score,
                "order": order,
            }
        )

    suggestions.sort(
        key=lambda item: (
            int(item.get("score", 0)),
            1 if str(item.get("source", "")).strip() == "matched" else 0,
            -int(item.get("order", 0)),
        ),
        reverse=True,
    )
    return suggestions


def runtime_input_matches_scope(
    record: RuntimeInputRecord,
    *,
    repository: str,
    app_code: str,
    job_id: str,
) -> bool:
    """Return True when one runtime input applies to the given job scope."""

    if str(record.repository or "").strip() and str(record.repository or "").strip() != str(repository or "").strip():
        return False
    scope = normalize_runtime_input_scope(record.scope)
    if scope == "repository":
        return True
    if scope == "app":
        return str(record.app_code or "").strip() == str(app_code or "").strip()
    if scope == "job":
        return str(record.job_id or "").strip() == str(job_id or "").strip()
    return False


def resolve_runtime_inputs(
    records: Iterable[RuntimeInputRecord],
    *,
    repository: str,
    app_code: str,
    job_id: str,
    integration_registry_entries: Iterable[IntegrationRegistryRecord] | None = None,
) -> Dict[str, object]:
    """Resolve scoped runtime inputs into prompt-safe and env-ready payloads."""

    matched: List[RuntimeInputRecord] = [
        record
        for record in records
        if runtime_input_matches_scope(
            record,
            repository=repository,
            app_code=app_code,
            job_id=job_id,
        )
    ]
    matched.sort(
        key=lambda item: (
            _RUNTIME_INPUT_SCOPE_ORDER.get(normalize_runtime_input_scope(item.scope), 0),
            item.updated_at or item.provided_at or item.requested_at or "",
            item.request_id,
        ),
        reverse=True,
    )

    resolved_by_key: Dict[str, RuntimeInputRecord] = {}
    for record in matched:
        normalized_key = str(record.key or "").strip()
        if normalized_key and normalized_key not in resolved_by_key:
            resolved_by_key[normalized_key] = record

    resolved_inputs: List[Dict[str, object]] = []
    pending_inputs: List[Dict[str, object]] = []
    blocked_inputs: List[Dict[str, object]] = []
    environment: Dict[str, str] = {}
    env_bridge_policy = build_runtime_input_env_bridge_policy(
        runtime_input_records=list(resolved_by_key.values()),
        integration_registry_entries=list(integration_registry_entries or []),
    )

    for key in sorted(resolved_by_key.keys()):
        record = resolved_by_key[key]
        is_sensitive = bool(record.sensitive or normalize_runtime_input_value_type(record.value_type) == "secret")
        normalized_status = normalize_runtime_input_status(record.status) or "requested"
        env_var_name = normalize_env_var_name(record.env_var_name, fallback_key=record.key)
        bridge_policy = env_bridge_policy.get(env_var_name)
        bridge_allowed = bool(bridge_policy.get("allowed")) if bridge_policy is not None else True
        bridge_reason = (
            str(bridge_policy.get("reason", "")).strip()
            if bridge_policy is not None
            else "통합 레지스트리 제한 없이 기본 env bridge가 허용됩니다."
        )
        item = {
            "request_id": record.request_id,
            "scope": normalize_runtime_input_scope(record.scope) or "repository",
            "key": record.key,
            "label": record.label,
            "description": record.description,
            "value_type": normalize_runtime_input_value_type(record.value_type) or "text",
            "env_var_name": env_var_name,
            "sensitive": is_sensitive,
            "status": normalized_status,
            "placeholder": record.placeholder,
            "note": record.note,
            "requested_at": record.requested_at,
            "provided_at": record.provided_at,
            "updated_at": record.updated_at,
            "value": record.value if not is_sensitive else "",
            "display_value": mask_runtime_input_value(record.value, sensitive=is_sensitive),
            "available": normalized_status == "provided" and bool(str(record.value or "").strip()),
            "bridge_allowed": bridge_allowed,
            "bridge_reason": bridge_reason,
            "linked_integrations": list(bridge_policy.get("linked_integrations", []) or []) if bridge_policy is not None else [],
        }
        if item["available"] and bridge_allowed:
            resolved_inputs.append(item)
            if str(record.value or "").strip():
                environment[env_var_name] = str(record.value)
        elif item["available"] and not bridge_allowed:
            blocked_inputs.append(item)
        else:
            pending_inputs.append(item)

    return {
        "resolved": resolved_inputs,
        "pending": pending_inputs,
        "blocked": blocked_inputs,
        "environment": environment,
        "blocked_environment": {str(item.get("env_var_name", "")).strip(): str(item.get("bridge_reason", "")).strip() for item in blocked_inputs if str(item.get("env_var_name", "")).strip()},
        "env_bridge_policy": env_bridge_policy,
    }
