"""Integration recommendation draft runtime for planner-stage prompts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.models import JobRecord, RuntimeInputRecord, utc_now_iso
from app.store import JobStore


_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "mapping": ["지도", "위치", "맵", "map", "maps", "place", "places", "route", "directions", "marker"],
    "payments": ["결제", "정기결제", "구독", "payment", "payments", "checkout", "billing", "invoice"],
    "auth": ["로그인", "회원가입", "oauth", "auth", "authentication", "sso", "social login"],
    "analytics": ["분석", "analytics", "tracking", "event", "events", "funnel"],
    "notifications": ["알림", "notification", "push", "email", "sms", "message"],
    "monitoring": ["모니터링", "에러", "error tracking", "observability", "sentry", "logging"],
    "database": ["db", "database", "storage", "postgres", "sqlite", "supabase"],
}


def _normalize_text(value: str) -> str:
    """Return one lower-cased searchable text block."""

    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokenize_display_name(value: str) -> List[str]:
    """Extract simple display-name tokens for matching."""

    tokens = re.split(r"[^0-9a-zA-Z가-힣]+", str(value or "").strip().lower())
    return [token for token in tokens if len(token) >= 2]


class IntegrationRecommendationRuntime:
    """Create read-only planner recommendation drafts from registry + SPEC."""

    def __init__(
        self,
        *,
        store: JobStore,
        append_actor_log,
        docs_file,
    ) -> None:
        self.store = store
        self.append_actor_log = append_actor_log
        self.docs_file = docs_file

    def write_integration_recommendation_artifact(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> Dict[str, Any]:
        """Build and persist planner-stage integration recommendation draft."""

        payload = self.build_recommendation_payload(
            job=job,
            repository_path=repository_path,
            paths=paths,
        )
        artifact_path = paths.get(
            "integration_recommendations",
            self.docs_file(repository_path, "INTEGRATION_RECOMMENDATIONS.json"),
        )
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        candidate_ids = [str(item.get("integration_id", "")).strip() for item in payload.get("items", []) if str(item.get("integration_id", "")).strip()]
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            "Integration recommendation draft: "
            + (", ".join(candidate_ids[:3]) if candidate_ids else "no candidates"),
        )
        return payload

    def build_recommendation_payload(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Return planner-safe recommendation payload from integration registry."""

        spec_json_path = paths.get("spec_json", self.docs_file(repository_path, "SPEC.json"))
        spec_path = paths.get("spec", self.docs_file(repository_path, "SPEC.md"))
        spec_payload = self._load_spec_payload(spec_json_path)
        app_type = str(spec_payload.get("app_type", "")).strip().lower()
        search_corpus = self._build_search_corpus(job=job, spec_payload=spec_payload, spec_path=spec_path)
        runtime_input_records = self.store.list_runtime_inputs()

        items: List[Dict[str, Any]] = []
        for record in self.store.list_integration_registry_entries():
            serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                record,
                runtime_input_records=runtime_input_records,
            )
            if not bool(serialized.get("enabled")):
                continue
            supported_app_types = [str(item).strip().lower() for item in serialized.get("supported_app_types", []) if str(item).strip()]
            if app_type and supported_app_types and app_type not in supported_app_types:
                continue
            matched_keywords = self._match_keywords(serialized, search_corpus)
            if not matched_keywords:
                continue
            required_input_summary = serialized.get("required_input_summary", {}) or {}
            items.append(
                {
                    "integration_id": str(serialized.get("integration_id", "")).strip(),
                    "display_name": str(serialized.get("display_name", "")).strip(),
                    "category": str(serialized.get("category", "")).strip(),
                    "supported_app_types": serialized.get("supported_app_types", []),
                    "tags": serialized.get("tags", []),
                    "required_env_keys": serialized.get("required_env_keys", []),
                    "required_input_summary": required_input_summary,
                    "input_readiness_status": str(serialized.get("input_readiness_status", "")).strip(),
                    "input_readiness_reason": str(serialized.get("input_readiness_reason", "")).strip(),
                    "approval_required": bool(serialized.get("approval_required")),
                    "approval_status": str(serialized.get("approval_status", "")).strip(),
                    "approval_note": str(serialized.get("approval_note", "")).strip(),
                    "approval_trail_count": int(serialized.get("approval_trail_count", 0) or 0),
                    "latest_approval_action": (serialized.get("approval_trail") or [None])[0],
                    "recommendation_status": self._recommendation_status(
                        approval_required=bool(serialized.get("approval_required")),
                        approval_status=str(serialized.get("approval_status", "")).strip(),
                        required_input_summary=required_input_summary,
                    ),
                    "matched_keywords": matched_keywords,
                    "reason": self._build_reason(
                        display_name=str(serialized.get("display_name", "")).strip() or str(serialized.get("integration_id", "")).strip(),
                        matched_keywords=matched_keywords,
                        required_input_summary=required_input_summary,
                    ),
                }
            )

        items.sort(
            key=lambda item: (
                int(len(item.get("matched_keywords", []) or [])),
                int((item.get("required_input_summary", {}) or {}).get("provided", 0)),
                str(item.get("display_name", "")),
            ),
            reverse=True,
        )
        return {
            "generated_at": utc_now_iso(),
            "repository": str(job.repository or "").strip(),
            "job_id": str(job.job_id or "").strip(),
            "issue_title": str(job.issue_title or "").strip(),
            "app_type": app_type,
            "count": len(items),
            "source": {
                "spec_json_path": str(spec_json_path),
                "spec_path": str(spec_path),
            },
            "items": items[:5],
        }

    @staticmethod
    def _load_spec_payload(spec_json_path: Path) -> Dict[str, Any]:
        """Return parsed SPEC.json payload when available."""

        if not spec_json_path.exists():
            return {}
        try:
            payload = json.loads(spec_json_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _build_search_corpus(*, job: JobRecord, spec_payload: Dict[str, Any], spec_path: Path) -> str:
        """Return searchable corpus from job/spec artifacts."""

        text_parts: List[str] = [
            str(job.issue_title or "").strip(),
            str(spec_payload.get("goal", "")).strip(),
            str(spec_payload.get("raw_request", "")).strip(),
        ]
        for key in ("scope_in", "scope_out", "acceptance_criteria", "constraints"):
            value = spec_payload.get(key, [])
            if isinstance(value, list):
                text_parts.extend(str(item or "").strip() for item in value)
        issue_payload = spec_payload.get("issue", {})
        if isinstance(issue_payload, dict):
            text_parts.append(str(issue_payload.get("title", "")).strip())
        if spec_path.exists():
            try:
                text_parts.append(spec_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        return _normalize_text("\n".join(part for part in text_parts if part))

    @staticmethod
    def _candidate_keywords(item: Dict[str, Any]) -> List[str]:
        """Return normalized match keywords for one integration entry."""

        normalized: List[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            token = _normalize_text(value)
            if not token or token in seen or len(token) < 2:
                return
            seen.add(token)
            normalized.append(token)

        for tag in item.get("tags", []) or []:
            _add(str(tag))
        for token in _tokenize_display_name(str(item.get("display_name", ""))):
            _add(token)
        category = str(item.get("category", "")).strip().lower()
        _add(category)
        for token in _CATEGORY_KEYWORDS.get(category, []):
            _add(token)
        return normalized

    def _match_keywords(self, item: Dict[str, Any], search_corpus: str) -> List[str]:
        """Return keywords that match the current planner context."""

        if not search_corpus:
            return []
        matched = [keyword for keyword in self._candidate_keywords(item) if keyword in search_corpus]
        return matched[:6]

    @staticmethod
    def _recommendation_status(
        *,
        approval_required: bool,
        approval_status: str,
        required_input_summary: Dict[str, Any],
    ) -> str:
        """Return one human-readable review status for planner candidates."""

        total = int(required_input_summary.get("total", 0) or 0)
        provided = int(required_input_summary.get("provided", 0) or 0)
        normalized_approval_status = str(approval_status or "").strip().lower()
        if normalized_approval_status == "rejected":
            return "operator_rejected"
        if total > provided:
            return "operator_review_and_input_required"
        if normalized_approval_status == "approved":
            return "approved_candidate"
        if approval_required:
            return "operator_review_required"
        return "review_candidate"

    @staticmethod
    def _build_reason(
        *,
        display_name: str,
        matched_keywords: List[str],
        required_input_summary: Dict[str, Any],
    ) -> str:
        """Return one compact planner-safe reason line."""

        keyword_text = ", ".join(matched_keywords[:3]) if matched_keywords else "관련 요구"
        missing_count = int(required_input_summary.get("missing", 0) or 0)
        if missing_count > 0:
            return f"{display_name} 도입 검토 후보입니다. 문맥에서 {keyword_text} 관련 요구가 감지됐고, 필요한 입력 {missing_count}건이 아직 비어 있습니다."
        return f"{display_name} 도입 검토 후보입니다. 문맥에서 {keyword_text} 관련 요구가 감지됐습니다."
