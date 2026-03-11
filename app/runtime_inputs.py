"""Helpers for operator-provided runtime input requests and resolution."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from app.models import RuntimeInputRecord


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
    environment: Dict[str, str] = {}

    for key in sorted(resolved_by_key.keys()):
        record = resolved_by_key[key]
        is_sensitive = bool(record.sensitive or normalize_runtime_input_value_type(record.value_type) == "secret")
        normalized_status = normalize_runtime_input_status(record.status) or "requested"
        env_var_name = normalize_env_var_name(record.env_var_name, fallback_key=record.key)
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
        }
        if item["available"]:
            resolved_inputs.append(item)
            if str(record.value or "").strip():
                environment[env_var_name] = str(record.value)
        else:
            pending_inputs.append(item)

    return {
        "resolved": resolved_inputs,
        "pending": pending_inputs,
        "environment": environment,
    }
