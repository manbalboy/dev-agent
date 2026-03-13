"""Integration-registry helper/runtime for dashboard admin APIs."""

from __future__ import annotations

from typing import Any, Dict, List
import re

from fastapi import HTTPException

from app.models import IntegrationRegistryRecord, RuntimeInputRecord, utc_now_iso
from app.runtime_inputs import normalize_env_var_name, normalize_runtime_input_scope, normalize_runtime_input_status
from app.store import JobStore


_INTEGRATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SUPPORTED_APP_TYPES = {"web", "app", "api"}
_APPROVAL_ACTIONS = {"approve", "reject", "reset"}


def normalize_integration_id(value: str) -> str:
    """Return one registry-safe integration id."""

    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")
    if normalized and _INTEGRATION_ID_PATTERN.match(normalized):
        return normalized
    return ""


def normalize_app_types(values: List[str]) -> List[str]:
    """Return supported app types preserving first occurrence order."""

    seen: set[str] = set()
    normalized_items: List[str] = []
    for value in values:
        item = str(value or "").strip().lower()
        if item not in _SUPPORTED_APP_TYPES or item in seen:
            continue
        seen.add(item)
        normalized_items.append(item)
    return normalized_items


def normalize_string_list(values: List[str]) -> List[str]:
    """Return non-empty unique string items preserving order."""

    seen: set[str] = set()
    normalized_items: List[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized_items.append(item)
    return normalized_items


def normalize_integration_approval_status(value: str, *, approval_required: bool) -> str:
    """Return one normalized operator approval state."""

    normalized = str(value or "").strip().lower()
    if normalized in {"approved", "rejected", "pending"}:
        return normalized
    return "pending" if approval_required else "not_required"


def _normalize_approval_trail(trail: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    """Return one normalized approval trail preserving order."""

    normalized_items: List[Dict[str, Any]] = []
    for item in list(trail or []):
        if not isinstance(item, dict):
            continue
        normalized_items.append(
            {
                "action": str(item.get("action", "")).strip().lower(),
                "source": str(item.get("source", "")).strip() or "operator",
                "previous_status": str(item.get("previous_status", "")).strip(),
                "current_status": str(item.get("current_status", "")).strip(),
                "note": str(item.get("note", "")).strip(),
                "acted_by": str(item.get("acted_by", "")).strip() or "operator",
                "acted_at": str(item.get("acted_at", "")).strip(),
            }
        )
    return normalized_items


def _build_approval_trail_entry(
    *,
    action: str,
    source: str,
    previous_status: str,
    current_status: str,
    note: str,
    acted_by: str,
    acted_at: str,
) -> Dict[str, Any]:
    """Return one normalized approval trail event."""

    return {
        "action": str(action or "").strip().lower(),
        "source": str(source or "").strip() or "operator",
        "previous_status": str(previous_status or "").strip(),
        "current_status": str(current_status or "").strip(),
        "note": str(note or "").strip(),
        "acted_by": str(acted_by or "").strip() or "operator",
        "acted_at": str(acted_at or "").strip(),
    }


class DashboardIntegrationRegistryRuntime:
    """Encapsulate integration-registry serialization and CRUD helpers."""

    def __init__(self, *, store: JobStore) -> None:
        self.store = store

    @staticmethod
    def _serialize_runtime_input_link(record: RuntimeInputRecord) -> Dict[str, Any]:
        """Return one safe runtime-input payload for integration linkage."""

        return {
            "request_id": str(record.request_id or "").strip(),
            "label": str(record.label or record.key or "").strip(),
            "key": str(record.key or "").strip(),
            "env_var_name": normalize_env_var_name(record.env_var_name, fallback_key=record.key),
            "scope": normalize_runtime_input_scope(record.scope) or "repository",
            "repository": str(record.repository or "").strip(),
            "app_code": str(record.app_code or "").strip(),
            "job_id": str(record.job_id or "").strip(),
            "status": normalize_runtime_input_status(record.status) or "requested",
            "requested_by": str(record.requested_by or "operator").strip(),
            "requested_at": str(record.requested_at or "").strip(),
            "provided_at": str(record.provided_at or "").strip(),
            "updated_at": str(record.updated_at or "").strip(),
        }

    @classmethod
    def _build_required_input_links(
        cls,
        *,
        required_env_keys: List[str],
        runtime_input_records: List[RuntimeInputRecord],
    ) -> Dict[str, Any]:
        """Return one linkage summary between integration envs and runtime inputs."""

        links: List[Dict[str, Any]] = []
        provided_count = 0
        requested_count = 0
        missing_count = 0
        for env_var_name in required_env_keys:
            normalized_env = normalize_env_var_name(env_var_name, fallback_key="INTEGRATION_KEY")
            matched_records = [
                record
                for record in runtime_input_records
                if normalize_env_var_name(record.env_var_name, fallback_key=record.key) == normalized_env
            ]
            matched_records.sort(
                key=lambda item: (
                    str(item.updated_at or item.requested_at or ""),
                    str(item.request_id or ""),
                ),
                reverse=True,
            )
            latest_record = matched_records[0] if matched_records else None
            linked_requests = [cls._serialize_runtime_input_link(record) for record in matched_records[:3]]
            if any(normalize_runtime_input_status(record.status) == "provided" for record in matched_records):
                status = "provided"
                provided_count += 1
            elif matched_records:
                status = "requested"
                requested_count += 1
            else:
                status = "missing"
                missing_count += 1
            links.append(
                {
                    "env_var_name": normalized_env,
                    "status": status,
                    "linked_request_count": len(matched_records),
                    "latest_request": cls._serialize_runtime_input_link(latest_record) if latest_record is not None else None,
                    "linked_requests": linked_requests,
                }
            )
        return {
            "links": links,
            "summary": {
                "total": len(required_env_keys),
                "provided": provided_count,
                "requested": requested_count,
                "missing": missing_count,
            },
        }

    @staticmethod
    def _build_input_readiness(
        *,
        required_input_summary: Dict[str, Any],
        approval_required: bool,
        approval_status: str,
        approval_note: str,
    ) -> Dict[str, str]:
        """Return one operator-facing readiness state for integration inputs."""

        total = int(required_input_summary.get("total", 0) or 0)
        provided = int(required_input_summary.get("provided", 0) or 0)
        requested = int(required_input_summary.get("requested", 0) or 0)
        missing = int(required_input_summary.get("missing", 0) or 0)
        normalized_approval_status = normalize_integration_approval_status(
            approval_status,
            approval_required=approval_required,
        )
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
                "reason": "필수 env가 없어 바로 검토할 수 있습니다.",
            }
        if missing > 0:
            return {
                "status": "input_required",
                "reason": f"필수 env {missing}건이 아직 요청/제공되지 않아 운영자 입력이 필요합니다.",
            }
        if requested > 0 and provided < total:
            return {
                "status": "input_requested",
                "reason": f"필수 env {requested}건이 요청됨 상태라 값이 제공될 때까지 준비 대기입니다.",
            }
        if approval_required and normalized_approval_status != "approved":
            return {
                "status": "approval_required",
                "reason": "필수 env는 준비됐지만 운영자 승인 후에만 실제 구현/연동을 진행할 수 있습니다.",
            }
        return {
            "status": "ready",
            "reason": "필수 env가 준비돼 있어 바로 구현 검토를 진행할 수 있습니다.",
        }

    @classmethod
    def serialize_entry(
        cls,
        record: IntegrationRegistryRecord,
        *,
        runtime_input_records: List[RuntimeInputRecord] | None = None,
    ) -> Dict[str, Any]:
        """Return one operator-safe integration registry payload."""

        normalized_required_env_keys = [
            normalize_env_var_name(item) for item in record.required_env_keys if str(item).strip()
        ]
        required_input_linkage = cls._build_required_input_links(
            required_env_keys=normalized_required_env_keys,
            runtime_input_records=list(runtime_input_records or []),
        )
        normalized_approval_status = normalize_integration_approval_status(
            record.approval_status,
            approval_required=bool(record.approval_required),
        )
        input_readiness = cls._build_input_readiness(
            required_input_summary=required_input_linkage["summary"],
            approval_required=bool(record.approval_required),
            approval_status=normalized_approval_status,
            approval_note=str(record.approval_note or ""),
        )
        return {
            "integration_id": str(record.integration_id or "").strip(),
            "display_name": str(record.display_name or "").strip(),
            "category": str(record.category or "").strip(),
            "supported_app_types": [str(item).strip() for item in record.supported_app_types if str(item).strip()],
            "tags": [str(item).strip() for item in record.tags if str(item).strip()],
            "required_env_keys": normalized_required_env_keys,
            "optional_env_keys": [normalize_env_var_name(item) for item in record.optional_env_keys if str(item).strip()],
            "operator_guide_markdown": str(record.operator_guide_markdown or ""),
            "implementation_guide_markdown": str(record.implementation_guide_markdown or ""),
            "verification_notes": str(record.verification_notes or ""),
            "approval_required": bool(record.approval_required),
            "approval_status": normalized_approval_status,
            "approval_note": str(record.approval_note or ""),
            "approval_updated_at": str(record.approval_updated_at or ""),
            "approval_updated_by": str(record.approval_updated_by or "operator"),
            "approval_trail_count": len(record.approval_trail or []),
            "approval_trail": list(reversed(_normalize_approval_trail(record.approval_trail)[-5:])),
            "enabled": bool(record.enabled),
            "has_operator_guide": bool(str(record.operator_guide_markdown or "").strip()),
            "has_implementation_guide": bool(str(record.implementation_guide_markdown or "").strip()),
            "required_input_summary": required_input_linkage["summary"],
            "required_input_links": required_input_linkage["links"],
            "input_readiness_status": input_readiness["status"],
            "input_readiness_reason": input_readiness["reason"],
            "created_at": str(record.created_at or ""),
            "updated_at": str(record.updated_at or ""),
        }

    def list_entries(
        self,
        *,
        q: str,
        category: str,
        app_type: str,
        enabled: str,
        limit: int,
    ) -> Dict[str, Any]:
        """List integration registry entries for dashboard admin APIs."""

        normalized_query = str(q or "").strip().lower()
        normalized_category = str(category or "").strip().lower()
        normalized_app_type = str(app_type or "").strip().lower()
        if normalized_app_type and normalized_app_type not in _SUPPORTED_APP_TYPES:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 app_type 입니다: {app_type}")
        normalized_enabled = str(enabled or "").strip().lower()
        if normalized_enabled not in {"", "true", "false"}:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 enabled 필터입니다: {enabled}")

        runtime_input_records = self.store.list_runtime_inputs()
        items: List[Dict[str, Any]] = []
        for record in self.store.list_integration_registry_entries():
            payload = self.serialize_entry(record, runtime_input_records=runtime_input_records)
            if normalized_category and payload["category"].lower() != normalized_category:
                continue
            if normalized_app_type and normalized_app_type not in [item.lower() for item in payload["supported_app_types"]]:
                continue
            if normalized_enabled:
                enabled_flag = normalized_enabled == "true"
                if bool(payload["enabled"]) != enabled_flag:
                    continue
            if normalized_query:
                haystack = " ".join(
                    [
                        payload["integration_id"],
                        payload["display_name"],
                        payload["category"],
                        " ".join(payload["tags"]),
                        " ".join(payload["supported_app_types"]),
                        " ".join(payload["required_env_keys"]),
                        " ".join(payload["optional_env_keys"]),
                        payload["operator_guide_markdown"],
                        payload["implementation_guide_markdown"],
                        payload["verification_notes"],
                    ]
                ).lower()
                if normalized_query not in haystack:
                    continue
            items.append(payload)

        items.sort(
            key=lambda item: (
                str(item.get("updated_at", "")),
                str(item.get("integration_id", "")),
            ),
            reverse=True,
        )
        return {
            "items": items[:limit],
            "count": len(items),
            "filters": {
                "q": str(q or "").strip(),
                "category": normalized_category,
                "app_type": normalized_app_type,
                "enabled": normalized_enabled,
                "limit": limit,
            },
        }

    def save_entry(
        self,
        *,
        integration_id: str,
        display_name: str,
        category: str,
        supported_app_types: List[str],
        tags: List[str],
        required_env_keys: List[str],
        optional_env_keys: List[str],
        operator_guide_markdown: str,
        implementation_guide_markdown: str,
        verification_notes: str,
        approval_required: bool,
        enabled: bool,
    ) -> Dict[str, Any]:
        """Create or update one integration registry entry."""

        normalized_integration_id = normalize_integration_id(integration_id)
        if not normalized_integration_id:
            raise HTTPException(status_code=400, detail="integration_id 형식이 올바르지 않습니다.")
        normalized_display_name = str(display_name or "").strip()
        if not normalized_display_name:
            raise HTTPException(status_code=400, detail="display_name은 비어 있을 수 없습니다.")
        normalized_category = str(category or "").strip().lower()
        normalized_supported_app_types = normalize_app_types(supported_app_types)
        normalized_tags = normalize_string_list(tags)
        normalized_required_env_keys = [
            normalize_env_var_name(item, fallback_key="INTEGRATION_KEY")
            for item in normalize_string_list(required_env_keys)
        ]
        normalized_optional_env_keys = [
            normalize_env_var_name(item, fallback_key="INTEGRATION_KEY")
            for item in normalize_string_list(optional_env_keys)
        ]
        existing = self.store.get_integration_registry_entry(normalized_integration_id)
        now = utc_now_iso()
        record = IntegrationRegistryRecord(
            integration_id=normalized_integration_id,
            display_name=normalized_display_name,
            category=normalized_category,
            supported_app_types=normalized_supported_app_types,
            tags=normalized_tags,
            required_env_keys=normalized_required_env_keys,
            optional_env_keys=normalized_optional_env_keys,
            operator_guide_markdown=str(operator_guide_markdown or ""),
            implementation_guide_markdown=str(implementation_guide_markdown or ""),
            verification_notes=str(verification_notes or ""),
            approval_required=bool(approval_required),
            enabled=bool(enabled),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            approval_status=normalize_integration_approval_status(
                existing.approval_status if existing is not None else "",
                approval_required=bool(approval_required),
            ),
            approval_note=str(existing.approval_note if existing is not None else ""),
            approval_updated_at=str(existing.approval_updated_at if existing is not None else ""),
            approval_updated_by=str(existing.approval_updated_by if existing is not None else "operator") or "operator",
            approval_trail=_normalize_approval_trail(existing.approval_trail if existing is not None else []),
        )
        self.store.upsert_integration_registry_entry(record)
        return {
            "saved": True,
            "item": self.serialize_entry(record),
        }

    def set_approval_action(
        self,
        *,
        integration_id: str,
        action: str,
        note: str,
        acted_by: str,
    ) -> Dict[str, Any]:
        """Apply one operator approval decision to an integration entry."""

        normalized_integration_id = normalize_integration_id(integration_id)
        if not normalized_integration_id:
            raise HTTPException(status_code=400, detail="integration_id 형식이 올바르지 않습니다.")
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in _APPROVAL_ACTIONS:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 approval action 입니다: {action}")

        existing = self.store.get_integration_registry_entry(normalized_integration_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"통합 항목을 찾을 수 없습니다: {integration_id}")

        now = utc_now_iso()
        previous_status = normalize_integration_approval_status(
            existing.approval_status,
            approval_required=bool(existing.approval_required),
        )
        if normalized_action == "approve":
            approval_status = "approved"
        elif normalized_action == "reject":
            approval_status = "rejected"
        else:
            approval_status = "pending" if bool(existing.approval_required) else "not_required"
        approval_trail = _normalize_approval_trail(existing.approval_trail)
        approval_trail.append(
            _build_approval_trail_entry(
                action=normalized_action,
                source="dashboard",
                previous_status=previous_status,
                current_status=approval_status,
                note=str(note or "").strip(),
                acted_by=str(acted_by or "operator").strip() or "operator",
                acted_at=now,
            )
        )

        record = IntegrationRegistryRecord(
            integration_id=existing.integration_id,
            display_name=existing.display_name,
            category=existing.category,
            supported_app_types=list(existing.supported_app_types),
            tags=list(existing.tags),
            required_env_keys=list(existing.required_env_keys),
            optional_env_keys=list(existing.optional_env_keys),
            operator_guide_markdown=existing.operator_guide_markdown,
            implementation_guide_markdown=existing.implementation_guide_markdown,
            verification_notes=existing.verification_notes,
            approval_required=bool(existing.approval_required),
            enabled=bool(existing.enabled),
            created_at=existing.created_at,
            updated_at=now,
            approval_status=approval_status,
            approval_note=str(note or "").strip(),
            approval_updated_at=now,
            approval_updated_by=str(acted_by or "operator").strip() or "operator",
            approval_trail=approval_trail,
        )
        self.store.upsert_integration_registry_entry(record)
        return {
            "saved": True,
            "action": normalized_action,
            "item": self.serialize_entry(record, runtime_input_records=self.store.list_runtime_inputs()),
        }
