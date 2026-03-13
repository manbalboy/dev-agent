"""Memory quality/feedback helper extraction for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.models import JobRecord


class MemoryQualityRuntime:
    """Encapsulate memory feedback aggregation and ranking updates."""

    def __init__(
        self,
        *,
        read_json_file: Callable[[Path | None], Dict[str, Any]],
        upsert_json_history_entries,
        job_execution_repository: Callable[[JobRecord], str],
    ) -> None:
        self.read_json_file = read_json_file
        self.upsert_json_history_entries = upsert_json_history_entries
        self.job_execution_repository = job_execution_repository

    def write_memory_quality_artifacts(
        self,
        *,
        job: JobRecord,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        generated_at: str,
        current_memory_ids: List[str],
        memory_feedback_path: Path,
        memory_rankings_path: Path,
    ) -> None:
        """Write feedback history and aggregated rankings for current memory usage."""

        outcome = self.build_memory_feedback_outcome(
            review_payload=review_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
        )
        selection_payload = self.read_json_file(paths.get("memory_selection"))

        used_by_routes: Dict[str, List[str]] = {}
        for route_key in ("planner_context", "reviewer_context", "coder_context"):
            route_name = route_key.replace("_context", "")
            route_ids = selection_payload.get(route_key, []) if isinstance(selection_payload, dict) else []
            if not isinstance(route_ids, list):
                continue
            for raw_id in route_ids:
                memory_id = str(raw_id or "").strip()
                if not memory_id:
                    continue
                used_by_routes.setdefault(memory_id, [])
                if route_name not in used_by_routes[memory_id]:
                    used_by_routes[memory_id].append(route_name)

        for memory_id in current_memory_ids:
            normalized = str(memory_id or "").strip()
            if not normalized:
                continue
            used_by_routes.setdefault(normalized, [])
            if "generated" not in used_by_routes[normalized]:
                used_by_routes[normalized].append("generated")

        feedback_entries: List[Dict[str, Any]] = []
        for memory_id, routes in sorted(used_by_routes.items()):
            feedback_entries.append(
                {
                    "feedback_id": f"{memory_id}:{job.job_id}",
                    "memory_id": memory_id,
                    "memory_kind": self.memory_kind_from_id(memory_id),
                    "job_id": job.job_id,
                    "app_code": job.app_code,
                    "repository": self.job_execution_repository(job),
                    "generated_at": generated_at,
                    "routes": sorted(routes),
                    "verdict": outcome["verdict"],
                    "score_delta": outcome["score_delta"],
                    "evidence": outcome["evidence"],
                }
            )

        self.upsert_json_history_entries(
            memory_feedback_path,
            feedback_entries,
            key_field="feedback_id",
            root_key="entries",
            max_entries=800,
        )
        self.update_memory_rankings_artifact(
            memory_rankings_path=memory_rankings_path,
            feedback_entries=feedback_entries,
            generated_at=generated_at,
        )

    @staticmethod
    def build_memory_feedback_outcome(
        *,
        review_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Translate current run quality signals into one simple memory verdict."""

        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate, dict):
            quality_gate = {}

        delta = float(trend_payload.get("delta_from_previous", 0.0) or 0.0) if isinstance(trend_payload, dict) else 0.0
        regression = bool(loop_state.get("quality_regression_detected"))
        stagnation = bool(loop_state.get("score_stagnation_detected"))
        repeated = bool(loop_state.get("repeated_issue_limit_hit"))
        gate_passed = bool(quality_gate.get("passed", False))

        if regression or delta <= -0.2:
            verdict = "decay"
            score_delta = -2
        elif repeated:
            verdict = "decay"
            score_delta = -2
        elif stagnation:
            verdict = "decay"
            score_delta = -1
        elif gate_passed and delta >= 0.3:
            verdict = "promote"
            score_delta = 2
        elif delta > 0.0:
            verdict = "promote"
            score_delta = 1
        else:
            verdict = "keep"
            score_delta = 0

        return {
            "verdict": verdict,
            "score_delta": score_delta,
            "evidence": {
                "quality_gate_passed": gate_passed,
                "trend_direction": str(trend_payload.get("trend_direction", "")).strip()
                if isinstance(trend_payload, dict)
                else "",
                "delta_from_previous": delta,
                "quality_regression_detected": regression,
                "score_stagnation_detected": stagnation,
                "repeated_issue_limit_hit": repeated,
                "persistent_low_categories": list(trend_payload.get("persistent_low_categories", []) or [])
                if isinstance(trend_payload, dict)
                else [],
            },
        }

    def update_memory_rankings_artifact(
        self,
        *,
        memory_rankings_path: Path,
        feedback_entries: List[Dict[str, Any]],
        generated_at: str,
    ) -> None:
        """Aggregate feedback history into durable memory rankings."""

        existing_payload = self.read_json_file(memory_rankings_path)
        current_items = existing_payload.get("items", []) if isinstance(existing_payload, dict) else []
        if not isinstance(current_items, list):
            current_items = []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_items:
            if not isinstance(item, dict):
                continue
            memory_id = str(item.get("memory_id", "")).strip()
            if memory_id:
                merged[memory_id] = item

        for feedback in feedback_entries:
            memory_id = str(feedback.get("memory_id", "")).strip()
            if not memory_id:
                continue
            current = merged.get(
                memory_id,
                {
                    "memory_id": memory_id,
                    "memory_kind": str(feedback.get("memory_kind", "")).strip(),
                    "score": 0.0,
                    "usage_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                    "confidence": 0.5,
                    "state": "active",
                    "last_feedback_at": generated_at,
                },
            )
            score_delta = float(feedback.get("score_delta", 0.0) or 0.0)
            current["usage_count"] = int(current.get("usage_count", 0) or 0) + 1
            current["score"] = max(-6.0, min(6.0, float(current.get("score", 0.0) or 0.0) + score_delta))
            if score_delta > 0:
                current["positive_count"] = int(current.get("positive_count", 0) or 0) + 1
            elif score_delta < 0:
                current["negative_count"] = int(current.get("negative_count", 0) or 0) + 1
            else:
                current["neutral_count"] = int(current.get("neutral_count", 0) or 0) + 1
            current["last_feedback_at"] = generated_at
            current["last_routes"] = list(feedback.get("routes", []) or [])
            current["last_verdict"] = str(feedback.get("verdict", "")).strip()
            current["confidence"] = round(
                max(
                    0.05,
                    min(
                        0.98,
                        0.5
                        + float(current.get("score", 0.0) or 0.0) * 0.05
                        + int(current.get("positive_count", 0) or 0) * 0.02
                        - int(current.get("negative_count", 0) or 0) * 0.03,
                    ),
                ),
                3,
            )
            current["state"] = self.memory_ranking_state(
                score=float(current.get("score", 0.0) or 0.0),
                positive_count=int(current.get("positive_count", 0) or 0),
                negative_count=int(current.get("negative_count", 0) or 0),
            )
            merged[memory_id] = current

        ordered_items = sorted(
            merged.values(),
            key=lambda item: (
                str(item.get("state", "")) == "banned",
                -float(item.get("score", 0.0) or 0.0),
                -float(item.get("confidence", 0.0) or 0.0),
                -int(item.get("usage_count", 0) or 0),
                str(item.get("memory_id", "")),
            ),
        )
        memory_rankings_path.write_text(
            json.dumps({"generated_at": generated_at, "items": ordered_items[:400]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def memory_ranking_state(*, score: float, positive_count: int, negative_count: int) -> str:
        """Map aggregate score history to one compact ranking state."""

        if negative_count >= 3 and score <= -3.0:
            return "banned"
        if score >= 3.0 or positive_count >= 3:
            return "promoted"
        if score < 0.0:
            return "decayed"
        return "active"

    @staticmethod
    def memory_kind_from_id(memory_id: str) -> str:
        """Infer one stable memory kind from stored identifier shape."""

        raw = str(memory_id or "").strip()
        if raw.startswith("episodic_"):
            return "episodic"
        if raw.startswith("improvement_strategy:"):
            return "decision"
        if raw.startswith("low_category:") or raw.startswith("persistent_low:") or raw.startswith("stagnant:") or raw.startswith("loop_guard:"):
            return "failure_pattern"
        if raw.startswith("conv_"):
            return "convention"
        return "unknown"
