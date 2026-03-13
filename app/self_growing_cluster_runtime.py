"""Helpers for measuring recurring failure cluster reduction on follow-up jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class SelfGrowingClusterRuntime:
    """Compute cluster recurrence change from follow-up workspaces."""

    _STATUS_LABELS = {
        "reduced": "재발 감소",
        "unchanged": "재발 유지",
        "increased": "재발 증가",
        "insufficient_baseline": "비교 기준 부족",
    }

    def build_cluster_recurrence(
        self,
        *,
        backlog_candidate: Dict[str, Any] | None,
        failure_patterns_path: Path,
    ) -> Dict[str, Any]:
        """Return recurrence reduction payload for one cluster-linked follow-up."""

        if not isinstance(backlog_candidate, dict):
            return {}

        payload = backlog_candidate.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if str(payload.get("source_kind", "")).strip() != "failure_pattern_cluster":
            return {}

        pattern_id = str(payload.get("pattern_id", "")).strip()
        baseline_count = self._to_int(payload.get("count"))
        pattern_type = str(payload.get("pattern_type", "")).strip()
        missing: List[str] = []
        if not pattern_id:
            missing.append("pattern_id")
        if baseline_count is None or baseline_count < 1:
            missing.append("baseline_count")

        failure_payload = self._read_json_file(failure_patterns_path)
        if not failure_payload:
            missing.append("failure_patterns_artifact")
        items = failure_payload.get("items", []) if isinstance(failure_payload.get("items"), list) else []

        matched_entry = self._find_matching_pattern(items, pattern_id)
        current_count = 0
        matched_pattern_id = ""
        if matched_entry is not None:
            matched_pattern_id = str(matched_entry.get("pattern_id", "")).strip()
            current_count = int(matched_entry.get("count", 0) or 0)

        if missing:
            status = "insufficient_baseline"
            delta_count = None
            summary = "failure pattern 재발 감소를 계산하기 위한 기준 정보가 부족합니다."
        else:
            assert baseline_count is not None
            delta_count = current_count - baseline_count
            if delta_count < 0:
                status = "reduced"
                summary = f"반복 실패가 {baseline_count}회에서 {current_count}회로 줄었습니다."
            elif delta_count > 0:
                status = "increased"
                summary = f"반복 실패가 {baseline_count}회에서 {current_count}회로 늘었습니다."
            else:
                status = "unchanged"
                summary = f"반복 실패가 {baseline_count}회로 유지됐습니다."

        return {
            "active": True,
            "status": status,
            "status_label": self._STATUS_LABELS.get(status, status),
            "summary": summary,
            "pattern_id": pattern_id,
            "pattern_type": pattern_type,
            "candidate_id": str(backlog_candidate.get("candidate_id", "")).strip(),
            "candidate_title": str(backlog_candidate.get("title", "")).strip(),
            "baseline_count": baseline_count,
            "current_count": current_count if not missing else None,
            "delta_count": delta_count,
            "missing": missing,
            "matched_pattern_id": matched_pattern_id,
            "artifact_path": str(failure_patterns_path),
        }

    @staticmethod
    def _read_json_file(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _find_matching_pattern(
        cls,
        items: List[Dict[str, Any]],
        pattern_id: str,
    ) -> Dict[str, Any] | None:
        target = cls._normalize_pattern_id(pattern_id)
        if not target:
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            current_pattern = str(item.get("pattern_id", "")).strip()
            if cls._normalize_pattern_id(current_pattern) == target:
                return item
        return None

    @staticmethod
    def _normalize_pattern_id(value: str) -> str:
        return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if isinstance(value, bool) or value in {"", None}:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value))
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
