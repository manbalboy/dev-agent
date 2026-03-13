"""Prompt-safe approved integration guide summary runtime."""

from __future__ import annotations

from pathlib import Path
import re
from textwrap import dedent
from typing import Any, Dict, List

from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.store import JobStore


_SUMMARY_CHAR_LIMIT = 800
_CODE_HINT_LIMIT = 500
_MAX_PATTERN_HINTS = 5
_MAX_SNIPPET_HINTS = 2
_MAX_VERIFICATION_HINTS = 5
_MAX_CHECKLIST_ITEMS = 8


def _compact_text(value: str, *, limit: int = _SUMMARY_CHAR_LIMIT) -> str:
    """Return one prompt-safe compact text block."""

    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_markdown_bullets(value: str, *, limit: int, max_items: int) -> List[str]:
    """Return compact bullet-like hints from markdown text."""

    items: List[str] = []
    seen: set[str] = set()
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        elif re.match(r"^\d+[.)]\s+", line):
            line = re.sub(r"^\d+[.)]\s+", "", line)
        else:
            continue
        compact = _compact_text(line, limit=limit)
        if not compact:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(compact)
        if len(items) >= max_items:
            break
    return items


def _redact_secret_assignments(value: str) -> str:
    """Return text with obvious secret assignment values redacted."""

    redacted = str(value or "")
    patterns = [
        r"((?:api[_-]?key|secret|token|password|client[_-]?secret|access[_-]?token)[A-Za-z0-9_-]*\s*[:=]\s*)([\"']?)[^\"'\s,]+(\2)",
        r"((?:GOOGLE|STRIPE|SUPABASE|FIREBASE|OPENAI|MAPS|SENTRY|AWS|AZURE|GITHUB)_[A-Z0-9_]*\s*=\s*)([\"']?)[^\"'\s]+(\2)",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1<REDACTED>", redacted, flags=re.IGNORECASE)
    return redacted


def _extract_code_blocks(value: str, *, max_items: int, limit: int) -> List[str]:
    """Return compact redacted code snippets from markdown fenced blocks."""

    matches = re.findall(r"```[^\n]*\n(.*?)```", str(value or ""), flags=re.DOTALL)
    items: List[str] = []
    seen: set[str] = set()
    for block in matches:
        compact = _redact_secret_assignments(block.strip())
        if not compact:
            continue
        if len(compact) > limit:
            compact = compact[:limit].rstrip() + "\n..."
        key = compact.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(compact)
        if len(items) >= max_items:
            break
    return items


class IntegrationGuideRuntime:
    """Build prompt-safe guide summary artifacts from approved integrations."""

    def __init__(self, *, store: JobStore, docs_file) -> None:
        self.store = store
        self.docs_file = docs_file

    def write_prompt_safe_guide_summary_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Persist one approved-integration guide summary artifact."""

        payload = self.build_prompt_safe_guide_summary_payload()
        artifact_path = paths.get(
            "integration_guide_summary",
            self.docs_file(repository_path, "INTEGRATION_GUIDE_SUMMARY.md"),
        )
        artifact_path.write_text(
            self._render_markdown(payload),
            encoding="utf-8",
        )
        return payload

    def write_code_pattern_hint_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Persist approved integration code pattern/snippet hints."""

        payload = self.build_code_pattern_hint_payload()
        artifact_path = paths.get(
            "integration_code_patterns",
            self.docs_file(repository_path, "INTEGRATION_CODE_PATTERNS.md"),
        )
        artifact_path.write_text(
            self._render_code_patterns_markdown(payload),
            encoding="utf-8",
        )
        return payload

    def write_verification_checklist_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Persist approved integration verification checklist artifact."""

        payload = self.build_verification_checklist_payload()
        artifact_path = paths.get(
            "integration_verification_checklist",
            self.docs_file(repository_path, "INTEGRATION_VERIFICATION_CHECKLIST.md"),
        )
        artifact_path.write_text(
            self._render_verification_checklist_markdown(payload),
            encoding="utf-8",
        )
        return payload

    def build_prompt_safe_guide_summary_payload(self) -> Dict[str, Any]:
        """Return one prompt-safe summary payload for approved integrations."""

        runtime_input_records = self.store.list_runtime_inputs()
        items: List[Dict[str, Any]] = []
        for record in self.store.list_integration_registry_entries():
            serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                record,
                runtime_input_records=runtime_input_records,
            )
            approval_status = str(serialized.get("approval_status", "")).strip()
            if approval_status not in {"approved", "not_required"}:
                continue
            items.append(
                {
                    "integration_id": str(serialized.get("integration_id", "")).strip(),
                    "display_name": str(serialized.get("display_name", "")).strip(),
                    "category": str(serialized.get("category", "")).strip(),
                    "supported_app_types": list(serialized.get("supported_app_types", []) or []),
                    "required_env_keys": list(serialized.get("required_env_keys", []) or []),
                    "input_readiness_status": str(serialized.get("input_readiness_status", "")).strip(),
                    "input_readiness_reason": _compact_text(str(serialized.get("input_readiness_reason", "")).strip(), limit=240),
                    "approval_status": approval_status,
                    "approval_note": _compact_text(str(serialized.get("approval_note", "")).strip(), limit=240),
                    "latest_approval_action": (serialized.get("approval_trail") or [None])[0],
                    "operator_guide_summary": _compact_text(str(serialized.get("operator_guide_markdown", "")).strip()),
                    "implementation_guide_summary": _compact_text(
                        str(serialized.get("implementation_guide_markdown", "")).strip()
                    ),
                    "verification_summary": _compact_text(str(serialized.get("verification_notes", "")).strip(), limit=320),
                }
            )
        items.sort(key=lambda item: (item["category"], item["display_name"]))
        return {
            "count": len(items),
            "items": items,
        }

    def build_code_pattern_hint_payload(self) -> Dict[str, Any]:
        """Return prompt-safe implementation pattern/snippet hints."""

        runtime_input_records = self.store.list_runtime_inputs()
        items: List[Dict[str, Any]] = []
        for record in self.store.list_integration_registry_entries():
            serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                record,
                runtime_input_records=runtime_input_records,
            )
            approval_status = str(serialized.get("approval_status", "")).strip()
            if approval_status not in {"approved", "not_required"}:
                continue
            implementation_markdown = str(serialized.get("implementation_guide_markdown", "")).strip()
            verification_notes = str(serialized.get("verification_notes", "")).strip()
            items.append(
                {
                    "integration_id": str(serialized.get("integration_id", "")).strip(),
                    "display_name": str(serialized.get("display_name", "")).strip(),
                    "category": str(serialized.get("category", "")).strip(),
                    "required_env_keys": list(serialized.get("required_env_keys", []) or []),
                    "input_readiness_status": str(serialized.get("input_readiness_status", "")).strip(),
                    "approval_status": approval_status,
                    "pattern_hints": _extract_markdown_bullets(
                        implementation_markdown,
                        limit=220,
                        max_items=_MAX_PATTERN_HINTS,
                    ),
                    "snippet_hints": _extract_code_blocks(
                        implementation_markdown,
                        max_items=_MAX_SNIPPET_HINTS,
                        limit=_CODE_HINT_LIMIT,
                    ),
                    "verification_hints": _extract_markdown_bullets(
                        verification_notes,
                        limit=220,
                        max_items=_MAX_VERIFICATION_HINTS,
                    ),
                }
            )
        items.sort(key=lambda item: (item["category"], item["display_name"]))
        return {"count": len(items), "items": items}

    def build_verification_checklist_payload(self) -> Dict[str, Any]:
        """Return prompt-safe verification checklist items for approved integrations."""

        runtime_input_records = self.store.list_runtime_inputs()
        items: List[Dict[str, Any]] = []
        for record in self.store.list_integration_registry_entries():
            serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                record,
                runtime_input_records=runtime_input_records,
            )
            approval_status = str(serialized.get("approval_status", "")).strip()
            if approval_status not in {"approved", "not_required"}:
                continue
            verification_notes = str(serialized.get("verification_notes", "")).strip()
            checklist_items = _extract_markdown_bullets(
                verification_notes,
                limit=220,
                max_items=_MAX_CHECKLIST_ITEMS,
            )
            if not checklist_items and verification_notes:
                fallback = _compact_text(verification_notes, limit=220)
                if fallback:
                    checklist_items = [fallback]
            items.append(
                {
                    "integration_id": str(serialized.get("integration_id", "")).strip(),
                    "display_name": str(serialized.get("display_name", "")).strip(),
                    "category": str(serialized.get("category", "")).strip(),
                    "required_env_keys": list(serialized.get("required_env_keys", []) or []),
                    "input_readiness_status": str(serialized.get("input_readiness_status", "")).strip(),
                    "approval_status": approval_status,
                    "checklist_items": checklist_items,
                    "verification_summary": _compact_text(verification_notes, limit=320),
                }
            )
        items.sort(key=lambda item: (item["category"], item["display_name"]))
        return {"count": len(items), "items": items}

    @staticmethod
    def _render_markdown(payload: Dict[str, Any]) -> str:
        """Render one markdown artifact from the summary payload."""

        items = list(payload.get("items", []) or [])
        if not items:
            return "# INTEGRATION_GUIDE_SUMMARY\n\n- 승인된 통합 가이드가 없습니다.\n"

        lines: List[str] = [
            "# INTEGRATION_GUIDE_SUMMARY",
            "",
            "- 이 문서는 승인된 통합만 포함합니다.",
            "- secret 값은 포함하지 않고 env var 이름과 가이드 요약만 제공합니다.",
            "",
        ]
        for item in items:
            latest_action = item.get("latest_approval_action") or {}
            lines.extend(
                [
                    f"## {item.get('display_name') or item.get('integration_id')}",
                    "",
                    f"- integration_id: {item.get('integration_id') or '-'}",
                    f"- category: {item.get('category') or '-'}",
                    f"- supported_app_types: {', '.join(item.get('supported_app_types') or []) or '-'}",
                    f"- required_env_keys: {', '.join(item.get('required_env_keys') or []) or '-'}",
                    f"- input_readiness_status: {item.get('input_readiness_status') or '-'}",
                    f"- input_readiness_reason: {item.get('input_readiness_reason') or '-'}",
                    f"- approval_status: {item.get('approval_status') or '-'}",
                    f"- approval_note: {item.get('approval_note') or '-'}",
                    f"- latest_approval_action: {latest_action.get('action') or '-'} / {latest_action.get('acted_by') or '-'} / {latest_action.get('acted_at') or '-'}",
                    "",
                    "### Operator Guide Summary",
                    item.get("operator_guide_summary") or "-",
                    "",
                    "### Implementation Guide Summary",
                    item.get("implementation_guide_summary") or "-",
                    "",
                    "### Verification Summary",
                    item.get("verification_summary") or "-",
                    "",
                ]
            )
        return dedent("\n".join(lines)).strip() + "\n"

    @staticmethod
    def _render_code_patterns_markdown(payload: Dict[str, Any]) -> str:
        """Render one markdown artifact with code patterns/snippet hints."""

        items = list(payload.get("items", []) or [])
        if not items:
            return "# INTEGRATION_CODE_PATTERNS\n\n- 승인된 통합 코드 패턴 힌트가 없습니다.\n"

        lines: List[str] = [
            "# INTEGRATION_CODE_PATTERNS",
            "",
            "- 이 문서는 승인된 통합만 포함합니다.",
            "- secret 값은 포함하지 않고 패턴/스니펫 힌트만 제공합니다.",
            "",
        ]
        for item in items:
            lines.extend(
                [
                    f"## {item.get('display_name') or item.get('integration_id')}",
                    "",
                    f"- integration_id: {item.get('integration_id') or '-'}",
                    f"- category: {item.get('category') or '-'}",
                    f"- required_env_keys: {', '.join(item.get('required_env_keys') or []) or '-'}",
                    f"- input_readiness_status: {item.get('input_readiness_status') or '-'}",
                    "",
                    "### Code Pattern Hints",
                ]
            )
            pattern_hints = list(item.get("pattern_hints") or [])
            if pattern_hints:
                lines.extend(f"- {hint}" for hint in pattern_hints)
            else:
                lines.append("- 구현 패턴 힌트가 없습니다.")
            lines.extend(["", "### Snippet Hints"])
            snippet_hints = list(item.get("snippet_hints") or [])
            if snippet_hints:
                for snippet in snippet_hints:
                    lines.extend(["```text", snippet, "```", ""])
            else:
                lines.extend(["- 스니펫 힌트가 없습니다.", ""])
            lines.append("### Verification Checklist Hints")
            verification_hints = list(item.get("verification_hints") or [])
            if verification_hints:
                lines.extend(f"- {hint}" for hint in verification_hints)
            else:
                lines.append("- 검증 힌트가 없습니다.")
            lines.append("")
        return dedent("\n".join(lines)).strip() + "\n"

    @staticmethod
    def _render_verification_checklist_markdown(payload: Dict[str, Any]) -> str:
        """Render one markdown artifact with verification checklist items."""

        items = list(payload.get("items", []) or [])
        if not items:
            return "# INTEGRATION_VERIFICATION_CHECKLIST\n\n- 승인된 통합 검증 체크리스트가 없습니다.\n"

        lines: List[str] = [
            "# INTEGRATION_VERIFICATION_CHECKLIST",
            "",
            "- 이 문서는 승인된 통합만 포함합니다.",
            "- 구현/리뷰 전에 검증 체크리스트 기준으로 self-check 하세요.",
            "",
        ]
        for item in items:
            lines.extend(
                [
                    f"## {item.get('display_name') or item.get('integration_id')}",
                    "",
                    f"- integration_id: {item.get('integration_id') or '-'}",
                    f"- category: {item.get('category') or '-'}",
                    f"- required_env_keys: {', '.join(item.get('required_env_keys') or []) or '-'}",
                    f"- input_readiness_status: {item.get('input_readiness_status') or '-'}",
                    "",
                    "### Verification Checklist",
                ]
            )
            checklist_items = list(item.get("checklist_items") or [])
            if checklist_items:
                lines.extend(f"- [ ] {entry}" for entry in checklist_items)
            else:
                lines.append("- [ ] 검증 체크리스트 항목이 없습니다.")
            lines.extend(
                [
                    "",
                    "### Verification Summary",
                    item.get("verification_summary") or "-",
                    "",
                ]
            )
        return dedent("\n".join(lines)).strip() + "\n"
