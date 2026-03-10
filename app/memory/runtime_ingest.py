"""Artifact-to-DB ingest helpers for the Phase 3 memory runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord


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

    store.refresh_rankings(as_of=_latest_generated_at(paths=paths))

    return {
        "entries": len(entry_ids),
        "evidence": len(evidence_ids),
        "feedback": len(feedback_ids),
        "retrieval_runs": len(retrieval_run_ids),
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
