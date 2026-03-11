"""Artifact-to-DB ingest helpers for the Phase 3 memory runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, utc_now_iso


def ingest_memory_runtime_artifacts(
    store: MemoryRuntimeStore,
    *,
    job: JobRecord,
    execution_repository: str,
    paths: Dict[str, Path],
) -> Dict[str, int]:
    """Upsert current file-based memory artifacts into the canonical DB."""

    ranking_entries = _read_json_file(paths.get("memory_rankings")).get("items", [])
    rankings_map = {
        str(item.get("memory_id", "")).strip(): item
        for item in ranking_entries
        if isinstance(item, dict) and str(item.get("memory_id", "")).strip()
    }
    feedback_entries = _read_json_file(paths.get("memory_feedback")).get("entries", [])
    if not isinstance(feedback_entries, list):
        feedback_entries = []
    latest_feedback_by_memory = _latest_feedback_map(feedback_entries)

    entry_ids: set[str] = set()
    evidence_ids: set[str] = set()
    feedback_ids: set[str] = set()
    retrieval_run_ids: set[str] = set()
    backlog_candidate_ids: set[str] = set()

    for entry in _read_jsonl_entries(paths.get("memory_log")):
        memory_id = str(entry.get("memory_id", "")).strip()
        if not memory_id:
            continue
        store.upsert_entry(
            _build_entry_payload(
                job=job,
                execution_repository=execution_repository,
                memory_id=memory_id,
                memory_type=str(entry.get("memory_type", "")).strip() or "episodic",
                source_kind="artifact_memory_log",
                source_path=paths.get("memory_log"),
                payload=entry,
                title=f"Job {job.issue_number} episodic summary",
                summary=_episodic_summary(entry),
                rankings_map=rankings_map,
                latest_feedback_by_memory=latest_feedback_by_memory,
                created_at=str(entry.get("generated_at", "")).strip(),
                updated_at=str(entry.get("generated_at", "")).strip(),
            )
        )
        entry_ids.add(memory_id)

    for entry in _read_json_history_entries(paths.get("decision_history")):
        memory_id = str(entry.get("decision_id", "")).strip()
        if not memory_id:
            continue
        store.upsert_entry(
            _build_entry_payload(
                job=job,
                execution_repository=execution_repository,
                memory_id=memory_id,
                memory_type="decision",
                source_kind="artifact_decision_history",
                source_path=paths.get("decision_history"),
                payload=entry,
                title=str(entry.get("decision_type", "")).strip() or "decision",
                summary=str(entry.get("chosen_strategy", "")).strip() or str(entry.get("strategy_focus", "")).strip(),
                rankings_map=rankings_map,
                latest_feedback_by_memory=latest_feedback_by_memory,
                created_at=str(entry.get("generated_at", "")).strip(),
                updated_at=str(entry.get("generated_at", "")).strip(),
            )
        )
        entry_ids.add(memory_id)

    failure_patterns_payload = _read_json_file(paths.get("failure_patterns"))
    failure_entries = failure_patterns_payload.get("items", []) if isinstance(failure_patterns_payload, dict) else []
    if not isinstance(failure_entries, list):
        failure_entries = []
    for entry in failure_entries:
        if not isinstance(entry, dict):
            continue
        memory_id = str(entry.get("pattern_id", "")).strip()
        if not memory_id:
            continue
        category = str(entry.get("category", "")).strip()
        store.upsert_entry(
            _build_entry_payload(
                job=job,
                execution_repository=execution_repository,
                memory_id=memory_id,
                memory_type="failure_pattern",
                source_kind="artifact_failure_patterns",
                source_path=paths.get("failure_patterns"),
                payload=entry,
                title=str(entry.get("pattern_type", "")).strip() or "failure_pattern",
                summary=category or str(entry.get("trigger", "")).strip(),
                rankings_map=rankings_map,
                latest_feedback_by_memory=latest_feedback_by_memory,
                created_at=str(entry.get("first_seen_at", "")).strip() or str(failure_patterns_payload.get("generated_at", "")).strip(),
                updated_at=str(entry.get("last_seen_at", "")).strip() or str(failure_patterns_payload.get("generated_at", "")).strip(),
            )
        )
        entry_ids.add(memory_id)

    conventions_payload = _read_json_file(paths.get("conventions"))
    convention_entries = conventions_payload.get("rules", []) if isinstance(conventions_payload, dict) else []
    if not isinstance(convention_entries, list):
        convention_entries = []
    for entry in convention_entries:
        if not isinstance(entry, dict):
            continue
        memory_id = str(entry.get("id", "")).strip()
        if not memory_id:
            continue
        confidence = max(
            float(entry.get("confidence", 0.0) or 0.0),
            float(rankings_map.get(memory_id, {}).get("confidence", 0.0) or 0.0),
        )
        store.upsert_entry(
            _build_entry_payload(
                job=job,
                execution_repository=execution_repository,
                memory_id=memory_id,
                memory_type="convention",
                source_kind="artifact_conventions",
                source_path=paths.get("conventions"),
                payload=entry,
                title=str(entry.get("type", "")).strip() or "convention",
                summary=str(entry.get("rule", "")).strip(),
                rankings_map=rankings_map,
                latest_feedback_by_memory=latest_feedback_by_memory,
                created_at=str(conventions_payload.get("generated_at", "")).strip(),
                updated_at=str(conventions_payload.get("generated_at", "")).strip(),
                confidence_override=confidence,
            )
        )
        evidence_payloads: List[Dict[str, Any]] = []
        for index, raw_path in enumerate(entry.get("evidence_paths", []) or []):
            source_path = str(raw_path or "").strip()
            if not source_path:
                continue
            evidence_payloads.append(
                {
                    "evidence_id": f"{memory_id}:path:{index}:{source_path}",
                    "evidence_type": "path",
                    "source_path": source_path,
                    "content": "",
                    "payload": {"source": "conventions"},
                    "created_at": str(conventions_payload.get("generated_at", "")).strip(),
                }
            )
            evidence_ids.add(f"{memory_id}:path:{index}:{source_path}")
        store.replace_evidence(memory_id, evidence_payloads)
        entry_ids.add(memory_id)

    for feedback in feedback_entries:
        if not isinstance(feedback, dict):
            continue
        feedback_id = str(feedback.get("feedback_id", "")).strip()
        if not feedback_id:
            continue
        store.upsert_feedback(
            {
                "feedback_id": feedback_id,
                "memory_id": str(feedback.get("memory_id", "")).strip(),
                "job_id": str(feedback.get("job_id", "")).strip() or job.job_id,
                "repository": str(feedback.get("repository", "")).strip() or job.repository,
                "execution_repository": execution_repository,
                "app_code": str(feedback.get("app_code", "")).strip() or job.app_code,
                "workflow_id": str(job.workflow_id or "").strip(),
                "generated_at": str(feedback.get("generated_at", "")).strip(),
                "verdict": str(feedback.get("verdict", "")).strip(),
                "score_delta": float(feedback.get("score_delta", 0.0) or 0.0),
                "routes": list(feedback.get("routes", []) or []),
                "evidence": dict(feedback.get("evidence", {}) or {}),
                "payload": feedback,
            }
        )
        feedback_ids.add(feedback_id)

    selection_payload = _read_json_file(paths.get("memory_selection"))
    context_payload = _read_json_file(paths.get("memory_context"))
    if isinstance(selection_payload, dict) or isinstance(context_payload, dict):
        selection_generated_at = str(selection_payload.get("generated_at", "")).strip() if isinstance(selection_payload, dict) else ""
        context_generated_at = str(context_payload.get("generated_at", "")).strip() if isinstance(context_payload, dict) else ""
        generated_at = selection_generated_at or context_generated_at
        if isinstance(selection_payload, dict) and "enabled" in selection_payload:
            enabled = bool(selection_payload.get("enabled"))
        elif isinstance(context_payload, dict) and "enabled" in context_payload:
            enabled = bool(context_payload.get("enabled"))
        else:
            enabled = True
        corpus_counts = selection_payload.get("corpus_counts", {}) if isinstance(selection_payload, dict) else {}
        if not isinstance(corpus_counts, dict):
            corpus_counts = {}

        for route in ("planner", "reviewer", "coder"):
            route_key = f"{route}_context"
            selection_ids = selection_payload.get(route_key, []) if isinstance(selection_payload, dict) else []
            context_entries = context_payload.get(route_key, []) if isinstance(context_payload, dict) else []
            if not isinstance(selection_ids, list):
                selection_ids = []
            if not isinstance(context_entries, list):
                context_entries = []
            run_id = f"{job.job_id}:{route}"
            store.upsert_retrieval_run(
                {
                    "run_id": run_id,
                    "job_id": job.job_id,
                    "route": route,
                    "repository": job.repository,
                    "execution_repository": execution_repository,
                    "app_code": job.app_code,
                    "workflow_id": str(job.workflow_id or "").strip(),
                    "generated_at": generated_at,
                    "enabled": enabled,
                    "selection_ids": selection_ids,
                    "context": context_entries,
                    "corpus_counts": corpus_counts,
                    "payload": {
                        "selection": selection_ids,
                        "context": context_entries,
                        "selection_generated_at": selection_generated_at,
                        "context_generated_at": context_generated_at,
                    },
                }
            )
            retrieval_run_ids.add(run_id)

    for candidate in _build_backlog_candidate_payloads(
        job=job,
        execution_repository=execution_repository,
        paths=paths,
    ):
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if not candidate_id:
            continue
        store.upsert_backlog_candidate(candidate)
        backlog_candidate_ids.add(candidate_id)

    store.refresh_rankings(as_of=_latest_generated_at(paths=paths))

    return {
        "entries": len(entry_ids),
        "evidence": len(evidence_ids),
        "feedback": len(feedback_ids),
        "retrieval_runs": len(retrieval_run_ids),
        "backlog_candidates": len(backlog_candidate_ids),
    }


def _build_entry_payload(
    *,
    job: JobRecord,
    execution_repository: str,
    memory_id: str,
    memory_type: str,
    source_kind: str,
    source_path: Path | None,
    payload: Dict[str, Any],
    title: str,
    summary: str,
    rankings_map: Dict[str, Dict[str, Any]],
    latest_feedback_by_memory: Dict[str, Dict[str, Any]],
    created_at: str,
    updated_at: str,
    confidence_override: float | None = None,
) -> Dict[str, Any]:
    ranking = rankings_map.get(memory_id, {})
    latest_feedback = latest_feedback_by_memory.get(memory_id, {})
    confidence = (
        float(confidence_override)
        if confidence_override is not None
        else float(ranking.get("confidence", 0.5) or 0.5)
    )
    return {
        "memory_id": memory_id,
        "memory_type": memory_type,
        "repository": str(payload.get("repository", "")).strip() or job.repository,
        "execution_repository": str(payload.get("execution_repository", "")).strip() or execution_repository,
        "app_code": str(payload.get("app_code", "")).strip() or job.app_code,
        "workflow_id": str(payload.get("workflow_id", "")).strip() or str(job.workflow_id or "").strip(),
        "job_id": str(payload.get("job_id", "")).strip() or job.job_id,
        "issue_number": int(payload.get("issue_number", job.issue_number) or job.issue_number or 0),
        "issue_title": str(payload.get("issue_title", "")).strip() or job.issue_title,
        "source_kind": source_kind,
        "source_path": str(source_path) if source_path is not None else "",
        "title": title,
        "summary": summary,
        "state": str(ranking.get("state", "")).strip() or "active",
        "baseline_score": float(ranking.get("score", 0.0) or 0.0),
        "baseline_confidence": float(ranking.get("confidence", 0.5) or 0.5),
        "confidence": confidence,
        "score": float(ranking.get("score", 0.0) or 0.0),
        "usage_count": int(ranking.get("usage_count", 0) or 0),
        "positive_count": int(ranking.get("positive_count", 0) or 0),
        "negative_count": int(ranking.get("negative_count", 0) or 0),
        "neutral_count": int(ranking.get("neutral_count", 0) or 0),
        "last_verdict": str(latest_feedback.get("verdict", "")).strip(),
        "last_routes": list(latest_feedback.get("routes", []) or []),
        "payload": payload,
        "created_at": created_at,
        "updated_at": updated_at or created_at,
        "last_used_at": str(latest_feedback.get("generated_at", "")).strip(),
        "last_feedback_at": str(ranking.get("last_feedback_at", "")).strip() or str(latest_feedback.get("generated_at", "")).strip(),
    }


def _episodic_summary(entry: Dict[str, Any]) -> str:
    signals = entry.get("signals", {}) if isinstance(entry, dict) else {}
    if not isinstance(signals, dict):
        signals = {}
    parts = [
        str(entry.get("issue_title", "")).strip(),
        f"strategy={str(signals.get('strategy', '')).strip()}",
        f"overall={float(signals.get('overall', 0.0) or 0.0):.2f}",
    ]
    return " | ".join(part for part in parts if part and part != "strategy=")


def _latest_feedback_map(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        memory_id = str(entry.get("memory_id", "")).strip()
        if not memory_id:
            continue
        current = latest.get(memory_id)
        generated_at = str(entry.get("generated_at", "")).strip()
        if current is None or generated_at >= str(current.get("generated_at", "")).strip():
            latest[memory_id] = entry
    return latest


def _read_jsonl_entries(path: Path | None) -> List[Dict[str, Any]]:
    if path is None or not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _read_json_file(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_history_entries(path: Path | None, *, root_key: str = "entries") -> List[Dict[str, Any]]:
    payload = _read_json_file(path)
    entries = payload.get(root_key, []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _latest_generated_at(*, paths: Dict[str, Path]) -> str | None:
    candidates: List[str] = []
    for key in (
        "memory_feedback",
        "memory_selection",
        "memory_context",
        "memory_rankings",
        "conventions",
        "failure_patterns",
        "decision_history",
    ):
        payload = _read_json_file(paths.get(key))
        generated_at = str(payload.get("generated_at", "")).strip()
        if generated_at:
            candidates.append(generated_at)
    return max(candidates) if candidates else None


def _build_backlog_candidate_payloads(
    *,
    job: JobRecord,
    execution_repository: str,
    paths: Dict[str, Path],
) -> List[Dict[str, Any]]:
    workflow_id = str(job.workflow_id or "").strip()
    candidates: List[Dict[str, Any]] = []

    improvement_backlog_payload = _read_json_file(paths.get("improvement_backlog"))
    improvement_items = improvement_backlog_payload.get("items", []) if isinstance(improvement_backlog_payload, dict) else []
    if not isinstance(improvement_items, list):
        improvement_items = []
    for index, item in enumerate(improvement_items):
        if not isinstance(item, dict):
            continue
        source_issue_id = str(item.get("id", "")).strip() or f"item_{index + 1}"
        reason = str(item.get("reason", "")).strip()
        action = str(item.get("action", "")).strip()
        candidates.append(
            _make_backlog_candidate_payload(
                job=job,
                execution_repository=execution_repository,
                workflow_id=workflow_id,
                source_kind="improvement_backlog",
                source_id=source_issue_id,
                title=str(item.get("title", "")).strip() or f"Improvement backlog {index + 1}",
                summary=_join_summary_parts(reason, action),
                priority=str(item.get("priority", "")).strip() or "P2",
                created_at=str(improvement_backlog_payload.get("generated_at", "")).strip(),
                source_path=paths.get("improvement_backlog"),
                payload={
                    "source_kind": "improvement_backlog",
                    "job_id": job.job_id,
                    "source_issue_id": source_issue_id,
                    "reason": reason,
                    "action": action,
                    "cluster_key": _cluster_key(
                        repository=job.repository,
                        app_code=job.app_code,
                        workflow_id=workflow_id,
                        source_kind="improvement_backlog",
                        source_id=source_issue_id,
                    ),
                    "raw": item,
                },
            )
        )

    next_tasks_payload = _read_json_file(paths.get("next_improvement_tasks"))
    next_tasks = next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload, dict) else []
    if not isinstance(next_tasks, list):
        next_tasks = []
    for index, item in enumerate(next_tasks):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id", "")).strip() or str(item.get("source_issue_id", "")).strip() or f"task_{index + 1}"
        reason = str(item.get("reason", "")).strip()
        action = str(item.get("action", "")).strip()
        strategy = str(item.get("selected_by_strategy", "")).strip() or str(next_tasks_payload.get("strategy", "")).strip()
        recommended_node_type = str(item.get("recommended_node_type", "")).strip()
        candidates.append(
            _make_backlog_candidate_payload(
                job=job,
                execution_repository=execution_repository,
                workflow_id=workflow_id,
                source_kind="next_improvement_task",
                source_id=task_id,
                title=str(item.get("title", "")).strip() or f"Next improvement task {index + 1}",
                summary=_join_summary_parts(reason, action, recommended_node_type),
                priority=str(item.get("priority", "")).strip() or "P2",
                created_at=str(next_tasks_payload.get("generated_at", "")).strip(),
                source_path=paths.get("next_improvement_tasks"),
                payload={
                    "source_kind": "next_improvement_task",
                    "job_id": job.job_id,
                    "task_id": task_id,
                    "source_issue_id": str(item.get("source_issue_id", "")).strip(),
                    "reason": reason,
                    "action": action,
                    "selected_by_strategy": strategy,
                    "recommended_node_type": recommended_node_type,
                    "scope_restriction": str(next_tasks_payload.get("scope_restriction", "")).strip(),
                    "cluster_key": _cluster_key(
                        repository=job.repository,
                        app_code=job.app_code,
                        workflow_id=workflow_id,
                        source_kind="next_improvement_task",
                        source_id=str(item.get("source_issue_id", "")).strip() or task_id,
                    ),
                    "raw": item,
                },
            )
        )

    quality_trend_payload = _read_json_file(paths.get("quality_trend"))
    review_round_count = int(quality_trend_payload.get("review_round_count", 0) or 0) if isinstance(quality_trend_payload, dict) else 0
    trend_direction = str(quality_trend_payload.get("trend_direction", "")).strip() if isinstance(quality_trend_payload, dict) else ""
    delta_from_previous = quality_trend_payload.get("delta_from_previous") if isinstance(quality_trend_payload, dict) else None
    persistent_low_categories = quality_trend_payload.get("persistent_low_categories", []) if isinstance(quality_trend_payload, dict) else []
    stagnant_categories = quality_trend_payload.get("stagnant_categories", []) if isinstance(quality_trend_payload, dict) else []
    if not isinstance(persistent_low_categories, list):
        persistent_low_categories = []
    if not isinstance(stagnant_categories, list):
        stagnant_categories = []

    for category in persistent_low_categories:
        normalized_category = str(category or "").strip()
        if not normalized_category:
            continue
        candidates.append(
            _make_backlog_candidate_payload(
                job=job,
                execution_repository=execution_repository,
                workflow_id=workflow_id,
                source_kind="quality_trend_persistent_low",
                source_id=normalized_category,
                title=f"지속 저점 개선: {normalized_category}",
                summary=_join_summary_parts(
                    f"최근 {review_round_count}회 리뷰에서 저점이 지속됨" if review_round_count else "지속 저점 감지",
                    _recommended_action_for_category(normalized_category),
                ),
                priority="P1",
                created_at=str(quality_trend_payload.get("generated_at", "")).strip(),
                source_path=paths.get("quality_trend"),
                payload={
                    "source_kind": "quality_trend_persistent_low",
                    "job_id": job.job_id,
                    "category": normalized_category,
                    "trend_direction": trend_direction,
                    "delta_from_previous": delta_from_previous,
                    "review_round_count": review_round_count,
                    "recommended_action": _recommended_action_for_category(normalized_category),
                    "cluster_key": _cluster_key(
                        repository=job.repository,
                        app_code=job.app_code,
                        workflow_id=workflow_id,
                        source_kind="quality_trend_persistent_low",
                        source_id=normalized_category,
                    ),
                },
            )
        )

    for category in stagnant_categories:
        normalized_category = str(category or "").strip()
        if not normalized_category:
            continue
        candidates.append(
            _make_backlog_candidate_payload(
                job=job,
                execution_repository=execution_repository,
                workflow_id=workflow_id,
                source_kind="quality_trend_stagnant",
                source_id=normalized_category,
                title=f"정체 카테고리 개선: {normalized_category}",
                summary=_join_summary_parts(
                    "최근 리뷰 라운드에서 개선 폭이 정체됨",
                    _recommended_action_for_category(normalized_category),
                ),
                priority="P2",
                created_at=str(quality_trend_payload.get("generated_at", "")).strip(),
                source_path=paths.get("quality_trend"),
                payload={
                    "source_kind": "quality_trend_stagnant",
                    "job_id": job.job_id,
                    "category": normalized_category,
                    "trend_direction": trend_direction,
                    "delta_from_previous": delta_from_previous,
                    "review_round_count": review_round_count,
                    "recommended_action": _recommended_action_for_category(normalized_category),
                    "cluster_key": _cluster_key(
                        repository=job.repository,
                        app_code=job.app_code,
                        workflow_id=workflow_id,
                        source_kind="quality_trend_stagnant",
                        source_id=normalized_category,
                    ),
                },
            )
        )

    strategy_shadow_payload = _read_json_file(paths.get("strategy_shadow_report"))
    if isinstance(strategy_shadow_payload, dict) and bool(strategy_shadow_payload.get("diverged")):
        shadow_strategy = str(strategy_shadow_payload.get("shadow_strategy", "")).strip() or "shadow"
        decision_mode = str(strategy_shadow_payload.get("decision_mode", "")).strip()
        confidence = strategy_shadow_payload.get("confidence")
        candidates.append(
            _make_backlog_candidate_payload(
                job=job,
                execution_repository=execution_repository,
                workflow_id=workflow_id,
                source_kind="strategy_shadow",
                source_id=shadow_strategy,
                title=f"전략 재검토: {shadow_strategy}",
                summary=_join_summary_parts(
                    "현재 전략과 memory-aware shadow 전략이 갈라짐",
                    decision_mode,
                ),
                priority="P1",
                created_at=str(strategy_shadow_payload.get("generated_at", "")).strip(),
                source_path=paths.get("strategy_shadow_report"),
                payload={
                    "source_kind": "strategy_shadow",
                    "job_id": job.job_id,
                    "shadow_strategy": shadow_strategy,
                    "decision_mode": decision_mode,
                    "confidence": confidence,
                    "diverged": True,
                    "cluster_key": _cluster_key(
                        repository=job.repository,
                        app_code=job.app_code,
                        workflow_id=workflow_id,
                        source_kind="strategy_shadow",
                        source_id=shadow_strategy,
                    ),
                    "raw": strategy_shadow_payload,
                },
            )
        )

    return candidates


def _make_backlog_candidate_payload(
    *,
    job: JobRecord,
    execution_repository: str,
    workflow_id: str,
    source_kind: str,
    source_id: str,
    title: str,
    summary: str,
    priority: str,
    created_at: str,
    source_path: Path | None,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_source_id = _slug_part(source_id) or "candidate"
    normalized_source_kind = _slug_part(source_kind) or "backlog"
    timestamp = created_at or utc_now_iso()
    return {
        "candidate_id": f"{normalized_source_kind}:{job.job_id}:{normalized_source_id}",
        "repository": job.repository,
        "execution_repository": execution_repository,
        "app_code": job.app_code,
        "workflow_id": workflow_id,
        "title": title.strip() or normalized_source_id,
        "summary": summary.strip() or title.strip() or normalized_source_id,
        "priority": _normalize_backlog_priority(priority),
        "state": "candidate",
        "payload": {
            **payload,
            "job_id": job.job_id,
            "issue_number": job.issue_number,
            "issue_title": job.issue_title,
            "source_path": str(source_path) if source_path is not None else "",
        },
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _join_summary_parts(*parts: str) -> str:
    normalized = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not normalized:
        return ""
    return " / ".join(normalized[:3])


def _cluster_key(
    *,
    repository: str,
    app_code: str,
    workflow_id: str,
    source_kind: str,
    source_id: str,
) -> str:
    return "|".join(
        [
            str(repository or "").strip(),
            str(app_code or "").strip() or "default",
            str(workflow_id or "").strip() or "default",
            _slug_part(source_kind) or "backlog",
            _slug_part(source_id) or "candidate",
        ]
    )


def _slug_part(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    parts: List[str] = []
    for char in normalized:
        if char.isalnum():
            parts.append(char)
        else:
            parts.append("_")
    collapsed = "".join(parts).strip("_")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed[:120]


def _normalize_backlog_priority(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"P0", "P1", "P2", "P3"}:
        return normalized
    return "P2"


def _recommended_action_for_category(category: str) -> str:
    normalized = str(category or "").strip().lower()
    if "test" in normalized:
        return "회귀 테스트와 검증 시나리오를 우선 보강"
    if normalized in {"ux_clarity", "loading_state_handling", "empty_state_handling", "error_state_handling"}:
        return "문제 흐름을 재현하고 화면 상태/카피를 함께 정리"
    if normalized in {"architecture_structure", "maintainability", "code_quality"}:
        return "구조 정리와 리팩터링 범위를 작은 단위로 쪼개서 개선"
    return "해당 카테고리의 반복 실패 원인을 기준으로 다음 작업을 설계"
