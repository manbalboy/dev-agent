"""Memory retrieval/runtime helper extraction for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.memory.runtime_ingest import ingest_memory_runtime_artifacts
from app.memory.vector_shadow import build_vector_shadow_manifest
from app.models import JobRecord, utc_now_iso


class MemoryRetrievalRuntime:
    """Encapsulate memory retrieval artifacts, shadow reports, and ingest sync."""

    def __init__(
        self,
        *,
        feature_enabled: Callable[[str], bool],
        docs_file: Callable[[Path, str], Path],
        write_json_artifact: Callable[[Optional[Path], Dict[str, Any]], None],
        job_execution_repository: Callable[[JobRecord], str],
        get_memory_runtime_store,
        read_json_file: Callable[[Optional[Path]], Dict[str, Any]],
        append_actor_log: Callable[[Path, str, str], None],
        get_qdrant_shadow_transport,
    ) -> None:
        self.feature_enabled = feature_enabled
        self.docs_file = docs_file
        self.write_json_artifact = write_json_artifact
        self.job_execution_repository = job_execution_repository
        self.get_memory_runtime_store = get_memory_runtime_store
        self.read_json_file = read_json_file
        self.append_actor_log = append_actor_log
        self.get_qdrant_shadow_transport = get_qdrant_shadow_transport

    def write_memory_retrieval_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> None:
        """Build route-specific memory selection/context files for prompt injection."""

        selection_path = paths.get("memory_selection", self.docs_file(repository_path, "MEMORY_SELECTION.json"))
        context_path = paths.get("memory_context", self.docs_file(repository_path, "MEMORY_CONTEXT.json"))
        trace_path = paths.get("memory_trace", self.docs_file(repository_path, "MEMORY_TRACE.json"))
        vector_shadow_path = paths.get("vector_shadow_index", self.docs_file(repository_path, "VECTOR_SHADOW_INDEX.json"))
        if not self.feature_enabled("memory_retrieval"):
            generated_at = utc_now_iso()
            self.write_json_artifact(
                selection_path,
                {
                    "generated_at": generated_at,
                    "job_id": job.job_id,
                    "enabled": False,
                    "planner_context": [],
                    "reviewer_context": [],
                    "coder_context": [],
                },
            )
            self.write_json_artifact(
                context_path,
                {
                    "generated_at": generated_at,
                    "job_id": job.job_id,
                    "enabled": False,
                    "repository": self.job_execution_repository(job),
                    "planner_context": [],
                    "reviewer_context": [],
                    "coder_context": [],
                },
            )
            self.write_json_artifact(
                trace_path,
                {
                    "generated_at": generated_at,
                    "job_id": job.job_id,
                    "enabled": False,
                    "source": "disabled",
                    "fallback_used": False,
                    "repository": self.job_execution_repository(job),
                    "corpus_counts": {},
                    "selected_total": 0,
                    "selected_memory_ids": [],
                    "routes": {},
                },
            )
            self.write_vector_shadow_index_artifact(
                job=job,
                output_path=vector_shadow_path,
                runtime_entries=[],
                enabled=False,
                status="memory_retrieval_disabled",
            )
            return

        retrieval_corpus = self.load_memory_retrieval_corpus_from_db(job=job)
        source = "db"
        if retrieval_corpus is None:
            source = "file"
            retrieval_corpus = self.load_memory_retrieval_corpus_from_files(paths=paths)

        planner_context = self.build_route_memory_context(
            route="planner",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
        )
        planner_context = self.annotate_route_context_items(planner_context, retrieval_source="db")
        reviewer_context = self.build_route_memory_context(
            route="reviewer",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
        )
        reviewer_context = self.annotate_route_context_items(reviewer_context, retrieval_source="db")
        coder_context = self.build_route_memory_context(
            route="coder",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
        )
        coder_context = self.annotate_route_context_items(coder_context, retrieval_source="db")

        vector_routes = {
            route_name: self.build_route_vector_retrieval_payload(
                job=job,
                route=route_name,
                entry_map=retrieval_corpus.get("entry_map", {}),
            )
            for route_name in ("planner", "reviewer", "coder")
        }
        planner_context = self.merge_route_context_items(
            primary_items=vector_routes["planner"]["items"],
            fallback_items=planner_context,
        )
        reviewer_context = self.merge_route_context_items(
            primary_items=vector_routes["reviewer"]["items"],
            fallback_items=reviewer_context,
        )
        coder_context = self.merge_route_context_items(
            primary_items=vector_routes["coder"]["items"],
            fallback_items=coder_context,
        )

        selection_payload = {
            "generated_at": utc_now_iso(),
            "job_id": job.job_id,
            "source": source,
            "corpus_counts": {
                "episodic": len(retrieval_corpus["memory_log_entries"]),
                "decisions": len(retrieval_corpus["decision_entries"]),
                "failure_patterns": len(retrieval_corpus["failure_pattern_entries"]),
                "conventions": len(retrieval_corpus["convention_entries"]),
            },
            "planner_context": [str(item.get("id", "")).strip() for item in planner_context],
            "reviewer_context": [str(item.get("id", "")).strip() for item in reviewer_context],
            "coder_context": [str(item.get("id", "")).strip() for item in coder_context],
            "vector_routes": {
                route_name: {
                    "enabled": bool(route_payload.get("enabled")),
                    "used_in_context": bool(route_payload.get("used_in_context")),
                    "selected_ids": list(route_payload.get("selected_ids", []) or []),
                }
                for route_name, route_payload in vector_routes.items()
            },
        }
        context_payload = {
            "generated_at": selection_payload["generated_at"],
            "job_id": job.job_id,
            "repository": self.job_execution_repository(job),
            "source": source,
            "planner_context": planner_context,
            "reviewer_context": reviewer_context,
            "coder_context": coder_context,
            "vector_routes": {
                route_name: {
                    key: value
                    for key, value in route_payload.items()
                    if key != "items"
                }
                for route_name, route_payload in vector_routes.items()
            },
        }
        route_traces = {
            "planner": self.memory_route_trace_payload(planner_context),
            "reviewer": self.memory_route_trace_payload(reviewer_context),
            "coder": self.memory_route_trace_payload(coder_context),
        }
        selected_memory_ids = sorted(
            {
                memory_id
                for route_payload in route_traces.values()
                for memory_id in route_payload["selected_ids"]
            }
        )
        trace_payload = {
            "generated_at": selection_payload["generated_at"],
            "job_id": job.job_id,
            "enabled": True,
            "source": source,
            "fallback_used": source != "db",
            "repository": self.job_execution_repository(job),
            "corpus_counts": dict(selection_payload["corpus_counts"]),
            "selected_total": len(selected_memory_ids),
            "selected_memory_ids": selected_memory_ids,
            "routes": route_traces,
            "vector_routes": {
                route_name: {
                    key: value
                    for key, value in route_payload.items()
                    if key != "items"
                }
                for route_name, route_payload in vector_routes.items()
            },
        }

        selection_path.write_text(json.dumps(selection_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        context_path.write_text(json.dumps(context_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        trace_path.write_text(json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        vector_shadow_enabled = self.feature_enabled("vector_memory_shadow")
        self.write_vector_shadow_index_artifact(
            job=job,
            output_path=vector_shadow_path,
            runtime_entries=self.load_vector_shadow_runtime_entries(job=job) if vector_shadow_enabled else [],
            enabled=vector_shadow_enabled,
            status="ready" if vector_shadow_enabled else "disabled",
        )

    def load_vector_shadow_runtime_entries(self, *, job: JobRecord) -> List[Dict[str, Any]]:
        """Return canonical DB entries eligible for vector shadow indexing."""

        try:
            runtime_store = self.get_memory_runtime_store()
            runtime_store.refresh_rankings(as_of=utc_now_iso())
            return runtime_store.query_entries_for_retrieval(
                repository=job.repository,
                execution_repository=self.job_execution_repository(job),
                app_code=job.app_code,
                workflow_id=str(job.workflow_id or "").strip(),
                limit=48,
            )
        except Exception:
            return []

    def write_vector_shadow_index_artifact(
        self,
        *,
        job: JobRecord,
        output_path: Path,
        runtime_entries: List[Dict[str, Any]],
        enabled: bool,
        status: str,
    ) -> None:
        """Write one Qdrant shadow manifest without affecting primary retrieval."""

        generated_at = utc_now_iso()
        execution_repository = self.job_execution_repository(job)
        if not enabled:
            transport = self.get_qdrant_shadow_transport()
            payload = {
                "generated_at": generated_at,
                "job_id": job.job_id,
                "enabled": False,
                "provider": "qdrant",
                "mode": "shadow_manifest_only",
                "status": status,
                "repository": job.repository,
                "execution_repository": execution_repository,
                "app_code": job.app_code,
                "workflow_id": str(job.workflow_id or "").strip(),
                "candidate_count": 0,
                "candidates": [],
                "transport": transport.sync_manifest({"candidates": []}).to_dict(),
            }
            self.write_json_artifact(output_path, payload)
            return

        manifest = build_vector_shadow_manifest(
            entries=runtime_entries,
            repository=job.repository,
            execution_repository=execution_repository,
            app_code=job.app_code,
            workflow_id=str(job.workflow_id or "").strip(),
        )
        transport_result = self.get_qdrant_shadow_transport().sync_manifest(manifest)
        payload = {
            "generated_at": generated_at,
            "job_id": job.job_id,
            "enabled": True,
            "provider": "qdrant",
            "mode": "shadow_manifest_only",
            "status": (
                "transported"
                if transport_result.ok and transport_result.attempted
                else "transport_not_configured"
                if not transport_result.configured
                else "embedding_not_configured"
                if str(transport_result.detail).startswith("embedding_not_configured:")
                else "embedding_failed"
                if str(transport_result.detail).startswith("embedding_failed:")
                else "transport_failed"
                if transport_result.attempted and not transport_result.ok
                else status if manifest["candidate_count"] else "no_db_candidates"
            ),
            "repository": job.repository,
            "execution_repository": execution_repository,
            "app_code": job.app_code,
            "workflow_id": str(job.workflow_id or "").strip(),
            **manifest,
            "transport": transport_result.to_dict(),
        }
        self.write_json_artifact(output_path, payload)

    def load_memory_retrieval_corpus_from_db(self, *, job: JobRecord) -> Optional[Dict[str, Any]]:
        """Return retrieval corpus from the canonical memory DB when available."""

        try:
            runtime_store = self.get_memory_runtime_store()
            runtime_store.refresh_rankings(as_of=utc_now_iso())
            runtime_entries = runtime_store.query_entries_for_retrieval(
                repository=job.repository,
                execution_repository=self.job_execution_repository(job),
                app_code=job.app_code,
                workflow_id=str(job.workflow_id or "").strip(),
            )
        except Exception:
            return None

        if not runtime_entries:
            return None

        memory_log_entries: List[Dict[str, Any]] = []
        decision_entries: List[Dict[str, Any]] = []
        failure_pattern_entries: List[Dict[str, Any]] = []
        convention_entries: List[Dict[str, Any]] = []
        rankings_map: Dict[str, Dict[str, Any]] = {}

        for entry in runtime_entries:
            memory_id = str(entry.get("memory_id", "")).strip()
            if not memory_id:
                continue
            rankings_map[memory_id] = {
                "memory_id": memory_id,
                "state": str(entry.get("state", "active")).strip() or "active",
                "score": float(entry.get("score", 0.0) or 0.0),
                "confidence": float(entry.get("confidence", 0.5) or 0.5),
                "usage_count": int(entry.get("usage_count", 0) or 0),
            }
            payload = self.memory_runtime_entry_payload(entry)
            if not payload:
                continue
            memory_type = str(entry.get("memory_type", "")).strip()
            if memory_type == "episodic":
                memory_log_entries.append(payload)
            elif memory_type == "decision":
                decision_entries.append(payload)
            elif memory_type == "failure_pattern":
                failure_pattern_entries.append(payload)
            elif memory_type == "convention":
                convention_entries.append(payload)

        if not any([memory_log_entries, decision_entries, failure_pattern_entries, convention_entries]):
            return None
        return {
            "memory_log_entries": memory_log_entries,
            "decision_entries": decision_entries,
            "failure_pattern_entries": failure_pattern_entries,
            "convention_entries": convention_entries,
            "rankings_map": rankings_map,
            "entry_map": {
                str(entry.get("memory_id", "")).strip(): entry
                for entry in runtime_entries
                if isinstance(entry, dict) and str(entry.get("memory_id", "")).strip()
            },
        }

    def load_memory_retrieval_corpus_from_files(self, *, paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return retrieval corpus from legacy file artifacts."""

        memory_log_entries = self.read_jsonl_entries(paths.get("memory_log"))
        decision_entries = self.read_json_history_entries(paths.get("decision_history"))
        failure_patterns_payload = self.read_json_file(paths.get("failure_patterns"))
        failure_pattern_entries = failure_patterns_payload.get("items", []) if isinstance(failure_patterns_payload, dict) else []
        if not isinstance(failure_pattern_entries, list):
            failure_pattern_entries = []
        conventions_payload = self.read_json_file(paths.get("conventions"))
        convention_entries = conventions_payload.get("rules", []) if isinstance(conventions_payload, dict) else []
        if not isinstance(convention_entries, list):
            convention_entries = []
        rankings_payload = self.read_json_file(paths.get("memory_rankings"))
        ranking_entries = rankings_payload.get("items", []) if isinstance(rankings_payload, dict) else []
        if not isinstance(ranking_entries, list):
            ranking_entries = []
        rankings_map = {
            str(item.get("memory_id", "")).strip(): item
            for item in ranking_entries
            if isinstance(item, dict) and str(item.get("memory_id", "")).strip()
        }
        return {
            "memory_log_entries": memory_log_entries,
            "decision_entries": decision_entries,
            "failure_pattern_entries": failure_pattern_entries,
            "convention_entries": convention_entries,
            "rankings_map": rankings_map,
            "entry_map": {},
        }

    @staticmethod
    def memory_runtime_entry_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
        """Project one canonical DB entry back to legacy retrieval payload shape."""

        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
        if isinstance(payload, dict) and payload:
            return dict(payload)

        memory_id = str(entry.get("memory_id", "")).strip()
        memory_type = str(entry.get("memory_type", "")).strip()
        if memory_type == "episodic":
            return {
                "memory_id": memory_id,
                "memory_type": "episodic",
                "generated_at": str(entry.get("updated_at", "")).strip(),
                "issue_title": str(entry.get("issue_title", "")).strip(),
                "signals": {},
            }
        if memory_type == "decision":
            return {
                "decision_id": memory_id,
                "generated_at": str(entry.get("updated_at", "")).strip(),
                "decision_type": str(entry.get("title", "")).strip(),
                "chosen_strategy": str(entry.get("summary", "")).strip(),
            }
        if memory_type == "failure_pattern":
            return {
                "pattern_id": memory_id,
                "generated_at": str(entry.get("updated_at", "")).strip(),
                "pattern_type": str(entry.get("title", "")).strip(),
                "trigger": str(entry.get("summary", "")).strip(),
            }
        if memory_type == "convention":
            return {
                "id": memory_id,
                "type": str(entry.get("title", "")).strip(),
                "rule": str(entry.get("summary", "")).strip(),
                "confidence": float(entry.get("confidence", 0.0) or 0.0),
                "evidence_paths": [],
            }
        return {}

    @staticmethod
    def memory_route_trace_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build one compact route trace payload for dashboard/operator inspection."""

        selected_items: List[Dict[str, Any]] = []
        selected_ids: List[str] = []
        kind_counts: Dict[str, int] = {}
        for item in items:
            memory_id = str(item.get("id", "")).strip()
            if not memory_id:
                continue
            kind = str(item.get("kind", "")).strip() or "unknown"
            selected_ids.append(memory_id)
            kind_counts[kind] = int(kind_counts.get(kind, 0) or 0) + 1
            selected_items.append(
                {
                    "id": memory_id,
                    "kind": kind,
                    "summary": str(item.get("summary", "")).strip(),
                    "retrieval_source": str(item.get("retrieval_source", "")).strip() or "unknown",
                    "vector_score": float(item.get("vector_score", 0.0) or 0.0),
                }
            )
        return {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
            "kind_counts": kind_counts,
            "source_counts": MemoryRetrievalRuntime.route_source_counts(selected_items),
            "vector_selected_count": sum(
                1 for item in selected_items if str(item.get("retrieval_source", "")).strip() == "vector"
            ),
            "selected_items": selected_items,
        }

    @staticmethod
    def route_source_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in items:
            source = str(item.get("retrieval_source", "")).strip() or "unknown"
            counts[source] = int(counts.get(source, 0) or 0) + 1
        return counts

    def write_strategy_shadow_report(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        strategy_inputs: Dict[str, Any],
        selected_strategy: str,
        selected_focus: str,
    ) -> None:
        """Compute one memory-aware shadow strategy without affecting runtime behavior."""

        report_path = paths.get("strategy_shadow_report", self.docs_file(repository_path, "STRATEGY_SHADOW_REPORT.json"))
        if not self.feature_enabled("strategy_shadow"):
            self.write_json_artifact(
                report_path,
                {
                    "generated_at": utc_now_iso(),
                    "job_id": job.job_id,
                    "selected_strategy": selected_strategy,
                    "selected_focus": selected_focus,
                    "enabled": False,
                    "shadow_strategy": "",
                    "diverged": False,
                    "decision_mode": "disabled",
                    "confidence": 0.0,
                    "scores_by_strategy": {},
                    "evidence": [],
                },
            )
            return

        context_payload = self.read_json_file(paths.get("memory_context"))
        rankings_payload = self.read_json_file(paths.get("memory_rankings"))
        ranking_entries = rankings_payload.get("items", []) if isinstance(rankings_payload, dict) else []
        if not isinstance(ranking_entries, list):
            ranking_entries = []
        rankings_map = {
            str(item.get("memory_id", "")).strip(): item
            for item in ranking_entries
            if isinstance(item, dict) and str(item.get("memory_id", "")).strip()
        }

        report_payload = self.build_strategy_shadow_report_payload(
            job=job,
            context_payload=context_payload if isinstance(context_payload, dict) else {},
            rankings_map=rankings_map,
            strategy_inputs=strategy_inputs,
            selected_strategy=selected_strategy,
            selected_focus=selected_focus,
        )
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def ingest_memory_runtime_artifacts(
        self,
        *,
        job: JobRecord,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Sync file-based memory artifacts into the canonical SQLite store."""

        try:
            sync_counts = ingest_memory_runtime_artifacts(
                self.get_memory_runtime_store(),
                job=job,
                execution_repository=self.job_execution_repository(job),
                paths=paths,
            )
        except Exception as exc:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Memory runtime ingest skipped: {exc}",
            )
            return

        if any(sync_counts.values()):
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Memory runtime ingest synced "
                f"(entries={sync_counts['entries']}, "
                f"feedback={sync_counts['feedback']}, "
                f"retrieval_runs={sync_counts['retrieval_runs']})",
            )

    def build_strategy_shadow_report_payload(
        self,
        *,
        job: JobRecord,
        context_payload: Dict[str, Any],
        rankings_map: Dict[str, Dict[str, Any]],
        strategy_inputs: Dict[str, Any],
        selected_strategy: str,
        selected_focus: str,
    ) -> Dict[str, Any]:
        """Build a read-only comparison between current strategy and memory-weighted shadow strategy."""

        normalized_selected = str(selected_strategy or "normal_iterative_improvement").strip() or "normal_iterative_improvement"
        normalized_focus = str(selected_focus or "balanced").strip() or "balanced"
        protected_strategies = {"design_rebaseline", "rollback_or_stabilize", "narrow_scope_stabilization"}

        score_map: Dict[str, float] = {normalized_selected: 1.0 if normalized_selected in protected_strategies else 0.35}
        evidence_rows: List[Dict[str, Any]] = []
        evidence_count = 0

        for route_name in ("planner_context", "reviewer_context", "coder_context"):
            route_items = context_payload.get(route_name, []) if isinstance(context_payload, dict) else []
            if not isinstance(route_items, list):
                continue
            route_label = route_name.replace("_context", "")
            for item in route_items:
                if not isinstance(item, dict):
                    continue
                memory_id = str(item.get("id", "")).strip()
                if not memory_id:
                    continue
                route_weight = self.strategy_shadow_route_weight(route_label)
                recommended = self.strategy_shadow_recommendations(item)
                if not recommended:
                    continue
                ranking = rankings_map.get(memory_id, {})
                weight_multiplier = self.strategy_shadow_ranking_weight(ranking)
                evidence_weight = round(route_weight * weight_multiplier, 3)
                for candidate in recommended:
                    strategy_name = str(candidate.get("strategy", "")).strip()
                    if not strategy_name:
                        continue
                    score_map[strategy_name] = round(score_map.get(strategy_name, 0.0) + evidence_weight, 3)
                    evidence_count += 1
                    if len(evidence_rows) < 12:
                        evidence_rows.append(
                            {
                                "memory_id": memory_id,
                                "route": route_label,
                                "kind": str(item.get("kind", "")).strip(),
                                "recommended_strategy": strategy_name,
                                "reason": str(candidate.get("reason", "")).strip(),
                                "summary": str(item.get("summary", "")).strip(),
                                "weight": evidence_weight,
                                "ranking_state": str(ranking.get("state", "active")).strip() or "active",
                                "ranking_score": float(ranking.get("score", 0.0) or 0.0),
                                "ranking_confidence": float(ranking.get("confidence", 0.5) or 0.5),
                            }
                        )

        if normalized_selected in protected_strategies:
            shadow_strategy = normalized_selected
            decision_mode = "locked_by_guardrail"
            decision_reason = "현재 전략은 보호 전략이므로 memory shadow가 실행 경로를 제안해도 덮지 않습니다."
        elif evidence_count < 2:
            shadow_strategy = normalized_selected
            decision_mode = "insufficient_memory_signal"
            decision_reason = "shadow 비교를 위한 memory evidence가 충분하지 않아 기존 전략을 유지합니다."
        else:
            ordered_candidates = sorted(score_map.items(), key=lambda item: (-float(item[1]), item[0]))
            top_strategy, top_score = ordered_candidates[0]
            selected_score = float(score_map.get(normalized_selected, 0.0) or 0.0)
            if top_strategy != normalized_selected and top_score >= selected_score + 0.6:
                shadow_strategy = top_strategy
                decision_mode = "memory_divergence"
                decision_reason = "memory evidence 기준으로 다른 전략이 더 높은 점수를 받았습니다."
            else:
                shadow_strategy = normalized_selected
                decision_mode = "memory_confirms_current"
                decision_reason = "memory evidence가 현재 전략을 뒤집을 정도로 강하지 않습니다."

        shadow_focus = self.strategy_focus_for_name(shadow_strategy)
        selected_score = round(float(score_map.get(normalized_selected, 0.0) or 0.0), 3)
        shadow_score = round(float(score_map.get(shadow_strategy, 0.0) or 0.0), 3)
        confidence = round(
            max(
                0.12,
                min(
                    0.96,
                    0.35
                    + evidence_count * 0.04
                    + max(0.0, shadow_score - selected_score) * 0.08
                    + (0.12 if shadow_strategy == normalized_selected else 0.18),
                ),
            ),
            3,
        )
        return {
            "generated_at": utc_now_iso(),
            "job_id": job.job_id,
            "app_code": job.app_code,
            "repository": self.job_execution_repository(job),
            "enabled": True,
            "selected_strategy": normalized_selected,
            "selected_focus": normalized_focus,
            "shadow_strategy": shadow_strategy,
            "shadow_focus": shadow_focus,
            "diverged": shadow_strategy != normalized_selected,
            "decision_mode": decision_mode,
            "decision_reason": decision_reason,
            "confidence": confidence,
            "selected_strategy_score": selected_score,
            "shadow_strategy_score": shadow_score,
            "strategy_inputs": {
                "maturity_level": str(strategy_inputs.get("maturity_level", "")).strip(),
                "maturity_progression": str(strategy_inputs.get("maturity_progression", "")).strip(),
                "quality_trend_direction": str(strategy_inputs.get("quality_trend_direction", "")).strip(),
                "quality_gate_passed": bool(strategy_inputs.get("quality_gate_passed")),
                "persistent_low_categories": list(strategy_inputs.get("persistent_low_categories", []) or []),
                "stagnant_categories": list(strategy_inputs.get("stagnant_categories", []) or []),
            },
            "scores_by_strategy": {key: round(float(value), 3) for key, value in sorted(score_map.items())},
            "evidence_count": evidence_count,
            "evidence": evidence_rows,
        }

    @staticmethod
    def strategy_shadow_route_weight(route_name: str) -> float:
        """Return a small route bias for shadow comparisons."""

        normalized = str(route_name or "").strip().lower()
        if normalized == "planner":
            return 1.0
        if normalized == "reviewer":
            return 0.95
        if normalized == "coder":
            return 0.9
        return 0.75

    @staticmethod
    def strategy_shadow_ranking_weight(ranking: Dict[str, Any]) -> float:
        """Translate memory ranking score/confidence into one bounded multiplier."""

        if not isinstance(ranking, dict):
            return 1.0
        if str(ranking.get("state", "")).strip() == "banned":
            return 0.0
        score = float(ranking.get("score", 0.0) or 0.0)
        confidence = float(ranking.get("confidence", 0.5) or 0.5)
        usage_count = int(ranking.get("usage_count", 0) or 0)
        return max(0.25, min(1.8, 0.8 + score * 0.08 + confidence * 0.4 + min(usage_count, 5) * 0.03))

    @staticmethod
    def strategy_shadow_recommendations(item: Dict[str, Any]) -> List[Dict[str, str]]:
        """Infer one or more candidate strategies from one compact memory item."""

        kind = str(item.get("kind", "")).strip()
        if kind == "decision":
            strategy = str(item.get("strategy", "")).strip()
            if strategy:
                return [{"strategy": strategy, "reason": "과거 decision memory에서 동일 전략을 선택함"}]
            return []
        if kind == "episodic":
            signals = item.get("signals", {}) if isinstance(item.get("signals"), dict) else {}
            strategy = str(signals.get("strategy", "")).strip()
            if strategy:
                return [{"strategy": strategy, "reason": "episodic memory의 당시 개선 전략"}]
            return []
        if kind != "failure_pattern":
            return []

        category = str(item.get("category", "")).strip()
        trigger = str(item.get("summary", "")).strip().lower()
        recommendations: List[Dict[str, str]] = []
        if category == "test_coverage":
            recommendations.append({"strategy": "test_hardening", "reason": "test_coverage 관련 실패 패턴"})
        if category in {"usability", "ux_clarity", "error_state_handling", "empty_state_handling", "loading_state_handling"}:
            recommendations.append({"strategy": "ux_clarity_improvement", "reason": f"{category} 관련 실패 패턴"})
        if category in {"architecture_structure", "maintainability", "code_quality"}:
            recommendations.append({"strategy": "stabilization", "reason": f"{category} 관련 엔지니어링 실패 패턴"})
        if "quality_regression" in trigger:
            recommendations.append({"strategy": "rollback_or_stabilize", "reason": "품질 하락 loop-guard 패턴"})
        if "score_stagnation" in trigger or "repeated_issue" in trigger:
            recommendations.append({"strategy": "stabilization", "reason": "반복/정체 loop-guard 패턴"})
        return recommendations

    @staticmethod
    def strategy_focus_for_name(strategy: str) -> str:
        """Map strategy name to one compact focus label."""

        normalized = str(strategy or "").strip()
        if normalized == "feature_expansion":
            return "feature"
        if normalized == "test_hardening":
            return "testing"
        if normalized == "ux_clarity_improvement":
            return "ux"
        if normalized == "design_rebaseline":
            return "design"
        if normalized in {"rollback_or_stabilize", "stabilization"}:
            return "stability"
        if normalized == "narrow_scope_stabilization":
            return "scope"
        return "balanced"

    def build_route_memory_context(
        self,
        *,
        route: str,
        memory_log_entries: List[Dict[str, Any]],
        decision_entries: List[Dict[str, Any]],
        failure_pattern_entries: List[Dict[str, Any]],
        convention_entries: List[Dict[str, Any]],
        rankings_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Select compact top-k memory items for one route."""

        route_name = str(route or "").strip().lower()

        def ranking_state(memory_id: str) -> str:
            item = rankings_map.get(str(memory_id or "").strip(), {})
            return str(item.get("state", "active")).strip() or "active"

        def ranking_tuple(memory_id: str) -> tuple[float, float, int]:
            item = rankings_map.get(str(memory_id or "").strip(), {})
            return (
                float(item.get("score", 0.0) or 0.0),
                float(item.get("confidence", 0.5) or 0.5),
                int(item.get("usage_count", 0) or 0),
            )

        episodic_sorted = sorted(
            [
                item
                for item in memory_log_entries
                if isinstance(item, dict) and ranking_state(str(item.get("memory_id", "")).strip()) != "banned"
            ],
            key=lambda item: (
                ranking_tuple(str(item.get("memory_id", "")).strip()),
                str(item.get("generated_at", "")),
            ),
            reverse=True,
        )
        decision_sorted = sorted(
            [
                item
                for item in decision_entries
                if isinstance(item, dict) and ranking_state(str(item.get("decision_id", "")).strip()) != "banned"
            ],
            key=lambda item: (
                ranking_tuple(str(item.get("decision_id", "")).strip()),
                str(item.get("generated_at", "")),
            ),
            reverse=True,
        )
        pattern_sorted = sorted(
            [
                item
                for item in failure_pattern_entries
                if isinstance(item, dict) and ranking_state(str(item.get("pattern_id", "")).strip()) != "banned"
            ],
            key=lambda item: (
                ranking_tuple(str(item.get("pattern_id", "")).strip()),
                int(item.get("count", 0) or 0),
                str(item.get("pattern_id", "")),
            ),
            reverse=True,
        )
        convention_sorted = sorted(
            [
                item
                for item in convention_entries
                if isinstance(item, dict) and ranking_state(str(item.get("id", "")).strip()) != "banned"
            ],
            key=lambda item: (
                ranking_tuple(str(item.get("id", "")).strip()),
                float(item.get("confidence", 0.0) or 0.0),
                str(item.get("id", "")),
            ),
            reverse=True,
        )

        selected: List[Dict[str, Any]] = []
        if route_name == "planner":
            if episodic_sorted:
                selected.append(self.memory_log_context_entry(episodic_sorted[0]))
            if decision_sorted:
                selected.append(self.decision_context_entry(decision_sorted[0]))
            selected.extend(self.failure_pattern_context_entry(item) for item in pattern_sorted[:2])
            selected.extend(self.convention_context_entry(item) for item in convention_sorted[:2])
        elif route_name == "reviewer":
            if episodic_sorted:
                selected.append(self.memory_log_context_entry(episodic_sorted[0]))
            selected.extend(self.failure_pattern_context_entry(item) for item in pattern_sorted[:3])
            selected.extend(self.convention_context_entry(item) for item in convention_sorted[:2])
        else:
            if decision_sorted:
                selected.append(self.decision_context_entry(decision_sorted[0]))
            if episodic_sorted:
                selected.append(self.memory_log_context_entry(episodic_sorted[0]))
            selected.extend(self.failure_pattern_context_entry(item) for item in pattern_sorted[:2])
            selected.extend(self.convention_context_entry(item) for item in convention_sorted[:3])

        dedup: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []
        for item in selected:
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                continue
            if item_id not in dedup:
                ordered_ids.append(item_id)
            dedup[item_id] = item
        return [dedup[item_id] for item_id in ordered_ids[:6]]

    @staticmethod
    def annotate_route_context_items(
        items: List[Dict[str, Any]],
        *,
        retrieval_source: str,
    ) -> List[Dict[str, Any]]:
        normalized_source = str(retrieval_source or "").strip() or "db"
        annotated: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            annotated.append({**item, "retrieval_source": normalized_source})
        return annotated

    @staticmethod
    def merge_route_context_items(
        *,
        primary_items: List[Dict[str, Any]],
        fallback_items: List[Dict[str, Any]],
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        dedup: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []
        for item in list(primary_items) + list(fallback_items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                continue
            if item_id in dedup:
                continue
            ordered_ids.append(item_id)
            dedup[item_id] = item
        return [dedup[item_id] for item_id in ordered_ids[: max(1, int(limit or 6))]]

    def build_route_vector_retrieval_payload(
        self,
        *,
        job: JobRecord,
        route: str,
        entry_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        route_name = str(route or "").strip().lower()
        payload: Dict[str, Any] = {
            "enabled": bool(self.feature_enabled("vector_memory_retrieval")),
            "route": route_name,
            "query": "",
            "configured": False,
            "attempted": False,
            "ok": False,
            "detail": "disabled",
            "item_count": 0,
            "selected_ids": [],
            "used_in_context": False,
            "items": [],
        }
        if not payload["enabled"]:
            return payload

        query = self.route_vector_query_text(job=job, route=route_name)
        payload["query"] = query
        if not query or not entry_map:
            payload["detail"] = "no_runtime_db_entries"
            return payload

        result = self.get_qdrant_shadow_transport().query_memory_entries(
            query=query,
            repository=job.repository,
            execution_repository=self.job_execution_repository(job),
            app_code=job.app_code,
            workflow_id=str(job.workflow_id or "").strip(),
            limit=3,
            score_threshold=0.15,
        )
        result_payload = result.to_dict()
        payload.update(
            {
                "configured": bool(result_payload.get("configured")),
                "attempted": bool(result_payload.get("attempted")),
                "ok": bool(result_payload.get("ok")),
                "detail": str(result_payload.get("detail", "")).strip(),
                "item_count": int(result_payload.get("item_count", 0) or 0),
            }
        )

        vector_items: List[Dict[str, Any]] = []
        for item in result_payload.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            memory_id = str(item.get("memory_id", "")).strip()
            runtime_entry = entry_map.get(memory_id)
            if not memory_id or not isinstance(runtime_entry, dict):
                continue
            context_entry = self.runtime_entry_context_entry(runtime_entry)
            if not context_entry:
                continue
            context_entry["retrieval_source"] = "vector"
            context_entry["vector_score"] = round(float(item.get("vector_score", 0.0) or 0.0), 4)
            vector_items.append(context_entry)

        payload["items"] = self.merge_route_context_items(
            primary_items=vector_items,
            fallback_items=[],
            limit=3,
        )
        payload["selected_ids"] = [
            str(item.get("id", "")).strip()
            for item in payload["items"]
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
        payload["used_in_context"] = bool(payload["items"])
        return payload

    @staticmethod
    def route_vector_query_text(*, job: JobRecord, route: str) -> str:
        issue_title = str(job.issue_title or "").strip()
        app_code = str(job.app_code or "").strip()
        workflow_id = str(job.workflow_id or "").strip()
        parts = [issue_title]
        if app_code:
            parts.append(f"app={app_code}")
        if workflow_id:
            parts.append(f"workflow={workflow_id}")
        normalized_route = str(route or "").strip().lower()
        if normalized_route == "planner":
            parts.append("planning architecture implementation strategy")
        elif normalized_route == "reviewer":
            parts.append("review quality regression test failure")
        else:
            parts.append("implementation code fix convention")
        return " ".join(part for part in parts if part)

    def runtime_entry_context_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        memory_type = str(entry.get("memory_type", "")).strip()
        payload = self.memory_runtime_entry_payload(entry)
        if not payload:
            return {}
        if memory_type == "episodic":
            return self.memory_log_context_entry(payload)
        if memory_type == "decision":
            return self.decision_context_entry(payload)
        if memory_type == "failure_pattern":
            return self.failure_pattern_context_entry(payload)
        if memory_type == "convention":
            return self.convention_context_entry(payload)
        return {}

    @staticmethod
    def memory_log_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        signals = entry.get("signals", {}) if isinstance(entry, dict) else {}
        if not isinstance(signals, dict):
            signals = {}
        return {
            "kind": "episodic",
            "id": str(entry.get("memory_id", "")).strip(),
            "summary": (
                f"strategy={signals.get('strategy', '')}, "
                f"overall={signals.get('overall', 0)}, "
                f"maturity={signals.get('maturity_level', '')}"
            ),
            "signals": {
                "strategy": str(signals.get("strategy", "")).strip(),
                "overall": float(signals.get("overall", 0.0) or 0.0),
                "maturity_level": str(signals.get("maturity_level", "")).strip(),
                "persistent_low_categories": list(signals.get("persistent_low_categories", []) or []),
            },
        }

    @staticmethod
    def decision_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "decision",
            "id": str(entry.get("decision_id", "")).strip(),
            "summary": str(entry.get("chosen_strategy", "")).strip(),
            "strategy": str(entry.get("chosen_strategy", "")).strip(),
            "strategy_focus": str(entry.get("strategy_focus", "")).strip(),
            "change_reasons": list(entry.get("change_reasons", []) or [])[:3],
            "selected_task_titles": list(entry.get("selected_task_titles", []) or [])[:3],
        }

    @staticmethod
    def failure_pattern_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "failure_pattern",
            "id": str(entry.get("pattern_id", "")).strip(),
            "summary": str(entry.get("trigger", "")).strip(),
            "pattern_type": str(entry.get("pattern_type", "")).strip(),
            "category": str(entry.get("category", "")).strip(),
            "count": int(entry.get("count", 0) or 0),
            "recommended_actions": list(entry.get("recommended_actions", []) or [])[:3],
        }

    @staticmethod
    def convention_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "convention",
            "id": str(entry.get("id", "")).strip(),
            "summary": str(entry.get("rule", "")).strip(),
            "type": str(entry.get("type", "")).strip(),
            "confidence": float(entry.get("confidence", 0.0) or 0.0),
            "evidence_paths": list(entry.get("evidence_paths", []) or [])[:3],
        }

    @staticmethod
    def read_jsonl_entries(path: Optional[Path]) -> List[Dict[str, Any]]:
        """Read JSONL entries safely."""

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

    def read_json_history_entries(self, path: Optional[Path], *, root_key: str = "entries") -> List[Dict[str, Any]]:
        """Read one JSON history file with list entries."""

        payload = self.read_json_file(path)
        entries = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return []
        return [item for item in entries if isinstance(item, dict)]
