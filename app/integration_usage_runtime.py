"""Append-only integration usage audit trail runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.models import IntegrationRegistryRecord, JobRecord, utc_now_iso
from app.runtime_inputs import resolve_runtime_inputs
from app.store import JobStore


_MAX_USAGE_EVENTS = 20


class IntegrationUsageRuntime:
    """Build append-only usage events for approved integration prompt injection."""

    def __init__(self, *, store: JobStore, docs_file) -> None:
        self.store = store
        self.docs_file = docs_file

    def append_usage_trail_event(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        stage: str,
        route: str,
        prompt_path: Path,
    ) -> Dict[str, Any]:
        """Append one stage-level integration usage event for the current job."""

        event = self.build_usage_event(
            job=job,
            repository_path=repository_path,
            paths=paths,
            stage=stage,
            route=route,
            prompt_path=prompt_path,
        )
        artifact_path = paths.get(
            "integration_usage_trail",
            self.docs_file(repository_path, "INTEGRATION_USAGE_TRAIL.json"),
        )
        if not event.get("active"):
            return {"active": False, "artifact_path": str(artifact_path), "event_count": 0, "latest_event": {}}

        payload = self._read_usage_payload(artifact_path)
        events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
        events.append(event)
        payload = {
            "generated_at": utc_now_iso(),
            "repository": str(job.repository or "").strip(),
            "job_id": str(job.job_id or "").strip(),
            "event_count": len(events[-_MAX_USAGE_EVENTS:]),
            "events": events[-_MAX_USAGE_EVENTS:],
        }
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "active": True,
            "artifact_path": str(artifact_path),
            "event_count": int(payload.get("event_count", 0) or 0),
            "latest_event": payload["events"][-1] if payload["events"] else {},
        }

    def build_usage_event(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        stage: str,
        route: str,
        prompt_path: Path,
    ) -> Dict[str, Any]:
        """Return one usage event payload without writing it."""

        integration_entries = list(self.store.list_integration_registry_entries())
        runtime_input_records = self.store.list_runtime_inputs()
        resolved_inputs = resolve_runtime_inputs(
            runtime_input_records,
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            integration_registry_entries=integration_entries,
        )
        blocked_inputs = resolved_inputs.get("blocked", []) if isinstance(resolved_inputs, dict) else []
        pending_inputs = resolved_inputs.get("pending", []) if isinstance(resolved_inputs, dict) else []
        recommendation_payload = self._read_json(paths.get("integration_recommendations"))
        recommendation_items = (
            recommendation_payload.get("items", [])
            if isinstance(recommendation_payload, dict) and isinstance(recommendation_payload.get("items"), list)
            else []
        )
        recommendation_map = {
            str(item.get("integration_id", "")).strip(): item
            for item in recommendation_items
            if isinstance(item, dict) and str(item.get("integration_id", "")).strip()
        }

        blocked_by_integration = self._build_blocked_map(list(blocked_inputs) + list(pending_inputs))
        approved_items = self._build_approved_items(
            integration_entries=integration_entries,
            runtime_input_records=runtime_input_records,
            recommendation_map=recommendation_map,
            blocked_by_integration=blocked_by_integration,
        )
        if not approved_items and not recommendation_items and not blocked_by_integration:
            return {"active": False}

        return {
            "generated_at": utc_now_iso(),
            "stage": str(stage or "").strip(),
            "route": str(route or "").strip(),
            "prompt_path": str(prompt_path),
            "artifact_paths": {
                "integration_recommendations": str(paths.get("integration_recommendations", "")),
                "integration_guide_summary": str(paths.get("integration_guide_summary", "")),
                "integration_code_patterns": str(paths.get("integration_code_patterns", "")),
                "integration_verification_checklist": str(paths.get("integration_verification_checklist", "")),
            },
            "integration_count": len(approved_items),
            "recommended_candidate_count": len(recommendation_items),
            "blocked_integration_count": len(blocked_by_integration),
            "blocked_env_vars": sorted(
                {
                    str(item.get("env_var_name", "")).strip()
                    for item in list(blocked_inputs) + list(pending_inputs)
                    if isinstance(item, dict) and str(item.get("env_var_name", "")).strip()
                }
            ),
            "items": approved_items,
            "active": True,
        }

    def _build_approved_items(
        self,
        *,
        integration_entries: Iterable[IntegrationRegistryRecord],
        runtime_input_records,
        recommendation_map: Dict[str, Dict[str, Any]],
        blocked_by_integration: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for record in integration_entries:
            serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                record,
                runtime_input_records=runtime_input_records,
            )
            approval_status = str(serialized.get("approval_status", "")).strip()
            if approval_status not in {"approved", "not_required"}:
                continue
            integration_id = str(serialized.get("integration_id", "")).strip()
            if not integration_id:
                continue
            linked_inputs = blocked_by_integration.get(integration_id, [])
            recommendation = recommendation_map.get(integration_id, {})
            items.append(
                {
                    "integration_id": integration_id,
                    "display_name": str(serialized.get("display_name", "")).strip() or integration_id,
                    "category": str(serialized.get("category", "")).strip(),
                    "supported_app_types": list(serialized.get("supported_app_types", []) or []),
                    "required_env_keys": list(serialized.get("required_env_keys", []) or []),
                    "required_input_summary": dict(serialized.get("required_input_summary", {}) or {}),
                    "approval_status": approval_status,
                    "approval_required": bool(serialized.get("approval_required")),
                    "approval_note": str(serialized.get("approval_note", "")).strip(),
                    "input_readiness_status": str(serialized.get("input_readiness_status", "")).strip(),
                    "input_readiness_reason": str(serialized.get("input_readiness_reason", "")).strip(),
                    "latest_approval_action": (serialized.get("approval_trail") or [None])[0],
                    "recommendation_status": str(recommendation.get("recommendation_status", "")).strip(),
                    "matched_keywords": list(recommendation.get("matched_keywords", []) or []),
                    "usage_status": "prompt_injected",
                    "blocked_inputs": [
                        {
                            "env_var_name": str(item.get("env_var_name", "")).strip(),
                            "bridge_reason": str(item.get("bridge_reason", "")).strip(),
                            "status": str(item.get("status", "")).strip(),
                        }
                        for item in linked_inputs
                        if isinstance(item, dict)
                    ],
                }
            )
        items.sort(key=lambda item: (item["category"], item["display_name"]))
        return items

    @staticmethod
    def _build_blocked_map(input_items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        blocked_by_integration: Dict[str, List[Dict[str, Any]]] = {}
        for item in input_items:
            if not isinstance(item, dict):
                continue
            for integration_id in [str(entry).strip() for entry in item.get("linked_integrations", []) if str(entry).strip()]:
                blocked_by_integration.setdefault(integration_id, []).append(item)
        return blocked_by_integration

    @staticmethod
    def _read_json(path: Path | None) -> Dict[str, Any]:
        if not isinstance(path, Path) or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_usage_payload(self, artifact_path: Path) -> Dict[str, Any]:
        return self._read_json(artifact_path)
