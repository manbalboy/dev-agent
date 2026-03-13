"""Self-growing bridge effectiveness artifact runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.models import JobRecord, utc_now_iso
from app.store import JobStore


class SelfGrowingEffectivenessRuntime:
    """Compare follow-up job outcomes against parent baseline."""

    _LEVEL_ORDER = {
        "bootstrap": 0,
        "mvp": 1,
        "usable": 2,
        "stable": 3,
        "product_grade": 4,
    }

    _STATUS_LABELS = {
        "improved": "개선됨",
        "unchanged": "변화 없음",
        "regressed": "회귀됨",
        "insufficient_baseline": "비교 기준 부족",
    }

    def __init__(self, *, store: JobStore) -> None:
        self.store = store

    def write_self_growing_effectiveness_artifact(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        maturity_snapshot: Dict[str, Any],
        trend_snapshot: Dict[str, Any],
        review_history_entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Write effectiveness artifact for follow-up jobs after product review."""

        normalized_kind = str(job.job_kind or "").strip().lower()
        parent_job_id = str(job.parent_job_id or "").strip()
        backlog_candidate_id = str(job.backlog_candidate_id or "").strip()
        if normalized_kind != "followup_backlog" and not parent_job_id and not backlog_candidate_id:
            return {}

        artifact_path = paths.get(
            "self_growing_effectiveness",
            repository_path / "_docs" / "SELF_GROWING_EFFECTIVENESS.json",
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        current_snapshot = self._build_current_snapshot(
            review_payload=review_payload,
            maturity_snapshot=maturity_snapshot,
            trend_snapshot=trend_snapshot,
        )
        parent_entry = self._find_parent_history_entry(
            review_history_entries=review_history_entries,
            parent_job_id=parent_job_id,
        )
        parent_job = self.store.get_job(parent_job_id) if parent_job_id else None

        baseline_missing: List[str] = []
        parent_snapshot: Dict[str, Any] = {}
        parent_source = ""
        if parent_entry is None:
            baseline_missing.append("parent_review_history_entry")
        else:
            parent_snapshot = self._build_parent_snapshot(parent_entry)
            parent_source = "review_history"
            if parent_snapshot.get("review_overall") is None:
                baseline_missing.append("parent_review_overall")
            if parent_snapshot.get("maturity_score") is None:
                baseline_missing.append("parent_maturity_score")

        if baseline_missing:
            status = "insufficient_baseline"
            delta_payload = {
                "review_overall": None,
                "maturity_score": None,
                "quality_gate_passed": None,
                "maturity_level_order": None,
            }
            status_reasons = ["부모 작업 기준 산출물이 부족합니다."]
            summary = "follow-up 작업이지만 부모 작업의 review history 기준이 부족해 효과를 비교할 수 없습니다."
        else:
            status, delta_payload, status_reasons = self._classify_effectiveness(
                current_snapshot=current_snapshot,
                parent_snapshot=parent_snapshot,
            )
            summary = self._build_summary(status=status, status_reasons=status_reasons)

        payload = {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "active": True,
            "job_id": job.job_id,
            "job_kind": normalized_kind or "followup_backlog",
            "parent_job_id": parent_job_id,
            "parent_job_title": str(parent_job.issue_title or "").strip() if parent_job is not None else "",
            "backlog_candidate_id": backlog_candidate_id,
            "status": status,
            "status_label": self._STATUS_LABELS.get(status, status),
            "summary": summary,
            "comparison_basis": [
                "PRODUCT_REVIEW.scores.overall",
                "PRODUCT_REVIEW.quality_gate.passed",
                "REPO_MATURITY.score",
                "REPO_MATURITY.level",
                "QUALITY_TREND.trend_direction",
                "REVIEW_HISTORY.entries",
            ],
            "baseline_missing": baseline_missing,
            "parent_source": parent_source,
            "status_reasons": status_reasons,
            "current": current_snapshot,
            "parent": parent_snapshot,
            "deltas": delta_payload,
            "history_entry_count": len(review_history_entries),
        }
        artifact_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    @classmethod
    def _build_current_snapshot(
        cls,
        *,
        review_payload: Dict[str, Any],
        maturity_snapshot: Dict[str, Any],
        trend_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        scores = review_payload.get("scores", {}) if isinstance(review_payload.get("scores"), dict) else {}
        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload.get("quality_gate"), dict) else {}
        return {
            "review_overall": cls._to_float(scores.get("overall")),
            "quality_gate_passed": bool(quality_gate.get("passed")),
            "quality_gate_categories": list(quality_gate.get("categories_below_threshold", []) or []),
            "maturity_level": str(maturity_snapshot.get("level", "")).strip(),
            "maturity_score": cls._to_int(maturity_snapshot.get("score")),
            "maturity_progression": str(maturity_snapshot.get("progression", "")).strip(),
            "quality_trend_direction": str(trend_snapshot.get("trend_direction", "")).strip(),
            "review_round_count": cls._to_int(trend_snapshot.get("review_round_count")),
            "delta_from_previous": cls._to_float(trend_snapshot.get("delta_from_previous")),
        }

    @classmethod
    def _build_parent_snapshot(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        overall = cls._to_float(entry.get("overall"))
        return {
            "job_id": str(entry.get("job_id", "")).strip(),
            "generated_at": str(entry.get("generated_at", "")).strip(),
            "review_overall": overall,
            "quality_gate_passed": bool(overall is not None and overall >= 3.0),
            "maturity_level": str(entry.get("maturity_level", "")).strip(),
            "maturity_score": cls._to_int(entry.get("maturity_score")),
            "top_issue_ids": list(entry.get("top_issue_ids", []) or []),
        }

    @staticmethod
    def _find_parent_history_entry(
        *,
        review_history_entries: List[Dict[str, Any]],
        parent_job_id: str,
    ) -> Dict[str, Any] | None:
        if not review_history_entries:
            return None
        entries = [item for item in review_history_entries if isinstance(item, dict)]
        if len(entries) < 2:
            return None
        prior_entries = entries[:-1]
        if not parent_job_id:
            return prior_entries[-1] if prior_entries else None
        for item in reversed(prior_entries):
            if str(item.get("job_id", "")).strip() == parent_job_id:
                return item
        return None

    @classmethod
    def _classify_effectiveness(
        cls,
        *,
        current_snapshot: Dict[str, Any],
        parent_snapshot: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], List[str]]:
        review_delta = cls._delta_float(
            current_snapshot.get("review_overall"),
            parent_snapshot.get("review_overall"),
        )
        maturity_delta = cls._delta_int(
            current_snapshot.get("maturity_score"),
            parent_snapshot.get("maturity_score"),
        )
        current_gate = bool(current_snapshot.get("quality_gate_passed"))
        parent_gate = bool(parent_snapshot.get("quality_gate_passed"))
        current_level = str(current_snapshot.get("maturity_level", "")).strip()
        parent_level = str(parent_snapshot.get("maturity_level", "")).strip()
        current_level_order = cls._LEVEL_ORDER.get(current_level)
        parent_level_order = cls._LEVEL_ORDER.get(parent_level)
        level_delta = None
        if current_level_order is not None and parent_level_order is not None:
            level_delta = current_level_order - parent_level_order

        positive: List[str] = []
        negative: List[str] = []
        if review_delta is not None:
            if review_delta >= 0.1:
                positive.append(f"리뷰 점수 +{review_delta:.1f}")
            elif review_delta <= -0.1:
                negative.append(f"리뷰 점수 {review_delta:.1f}")
        if maturity_delta is not None:
            if maturity_delta >= 1:
                positive.append(f"성숙도 점수 +{maturity_delta}")
            elif maturity_delta <= -1:
                negative.append(f"성숙도 점수 {maturity_delta}")
        if level_delta is not None:
            if level_delta > 0:
                positive.append("성숙도 단계 상승")
            elif level_delta < 0:
                negative.append("성숙도 단계 하락")
        if current_gate and not parent_gate:
            positive.append("품질 게이트 통과로 전환")
        elif parent_gate and not current_gate:
            negative.append("품질 게이트 통과 상태 상실")

        if negative:
            status = "regressed"
            status_reasons = negative
        elif positive:
            status = "improved"
            status_reasons = positive
        else:
            status = "unchanged"
            status_reasons = ["리뷰 점수와 성숙도 지표가 큰 폭으로 변하지 않았습니다."]

        return (
            status,
            {
                "review_overall": review_delta,
                "maturity_score": maturity_delta,
                "quality_gate_passed": (
                    1 if current_gate and not parent_gate else -1 if parent_gate and not current_gate else 0
                ),
                "maturity_level_order": level_delta,
            },
            status_reasons,
        )

    @staticmethod
    def _build_summary(*, status: str, status_reasons: List[str]) -> str:
        reason_text = ", ".join(str(item).strip() for item in status_reasons if str(item).strip())
        if status == "improved":
            return f"follow-up 작업이 부모 작업 대비 개선되었습니다. {reason_text}".strip()
        if status == "regressed":
            return f"follow-up 작업이 부모 작업 대비 회귀했습니다. {reason_text}".strip()
        if status == "unchanged":
            return f"follow-up 작업이 부모 작업 대비 큰 변화 없이 유지됐습니다. {reason_text}".strip()
        return "follow-up 작업의 효과를 비교하기 위한 기준이 부족합니다."

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool) or value in {"", None}:
            return None
        if isinstance(value, (int, float)):
            return round(float(value), 3)
        try:
            return round(float(str(value).strip()), 3)
        except (TypeError, ValueError):
            return None

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

    @classmethod
    def _delta_float(cls, current: Any, parent: Any) -> float | None:
        current_value = cls._to_float(current)
        parent_value = cls._to_float(parent)
        if current_value is None or parent_value is None:
            return None
        return round(current_value - parent_value, 3)

    @classmethod
    def _delta_int(cls, current: Any, parent: Any) -> int | None:
        current_value = cls._to_int(current)
        parent_value = cls._to_int(parent)
        if current_value is None or parent_value is None:
            return None
        return current_value - parent_value
