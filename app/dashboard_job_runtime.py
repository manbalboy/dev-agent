"""Job-detail/dashboard helper runtime for dashboard routes."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List

from app.config import AppSettings
from app.dead_letter_policy import build_dead_letter_summary
from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.log_signal_utils import classify_cli_health_hint, is_optional_helper_actor
from app.models import JobRecord
from app.needs_human_policy import build_needs_human_summary
from app.requeue_reason_runtime import REQUEUE_RECOVERY_STATUSES, build_requeue_reason_summary
from app.runtime_inputs import resolve_runtime_inputs
from app.self_growing_cluster_runtime import SelfGrowingClusterRuntime
from app.store import JobStore
from app.workflow_resume import build_workflow_artifact_paths


class DashboardJobRuntime:
    """Encapsulate job-detail helper calculations used by dashboard APIs."""

    def __init__(
        self,
        *,
        store: JobStore | None,
        settings: AppSettings,
        get_memory_runtime_store: Callable[[], Any],
        compute_job_resume_state: Callable[[JobRecord, List[Any], AppSettings], Dict[str, Any]],
        resolve_channel_log_path: Callable[[AppSettings, str, str], Path],
    ) -> None:
        self.store = store
        self.settings = settings
        self.get_memory_runtime_store = get_memory_runtime_store
        self.compute_job_resume_state = compute_job_resume_state
        self.resolve_channel_log_path = resolve_channel_log_path
        self._self_growing_cluster_runtime = SelfGrowingClusterRuntime()

    @staticmethod
    def read_dashboard_json(path: Path) -> Dict[str, Any]:
        """Read one dashboard-side JSON artifact safely."""

        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def job_execution_repository(job: JobRecord) -> str:
        """Return the repo used for clone/build/push for one job."""

        source_repository = str(job.source_repository or "").strip()
        return source_repository or str(job.repository or "").strip()

    def job_workspace_path(self, job: JobRecord) -> Path:
        """Resolve workspace path using execution repository, not issue hub repository."""

        return self.settings.repository_workspace_path(self.job_execution_repository(job), job.app_code)

    def build_job_runtime_signals(self, job: JobRecord) -> Dict[str, Any]:
        """Collect runtime review/resume/recovery signals for dashboard rendering."""

        workspace_path = self.job_workspace_path(job)
        docs_dir = workspace_path / "_docs"
        review_payload = self.read_dashboard_json(docs_dir / "PRODUCT_REVIEW.json")
        maturity_payload = self.read_dashboard_json(docs_dir / "REPO_MATURITY.json")
        trend_payload = self.read_dashboard_json(docs_dir / "QUALITY_TREND.json")
        loop_payload = self.read_dashboard_json(docs_dir / "IMPROVEMENT_LOOP_STATE.json")
        next_tasks_payload = self.read_dashboard_json(docs_dir / "NEXT_IMPROVEMENT_TASKS.json")
        strategy_shadow_payload = self.read_dashboard_json(docs_dir / "STRATEGY_SHADOW_REPORT.json")
        memory_trace_payload = self.read_dashboard_json(docs_dir / "MEMORY_TRACE.json")
        if self.store is None:
            raise RuntimeError("DashboardJobRuntime.store is required for build_job_runtime_signals")
        node_runs = self.store.list_node_runs(job.job_id)
        resume_state = self.compute_job_resume_state(job, node_runs, self.settings)

        scores = review_payload.get("scores", {}) if isinstance(review_payload.get("scores"), dict) else {}
        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload.get("quality_gate"), dict) else {}
        tasks = next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload.get("tasks"), list) else []
        first_task = tasks[0] if tasks and isinstance(tasks[0], dict) else {}
        memory_routes = memory_trace_payload.get("routes", {}) if isinstance(memory_trace_payload.get("routes"), dict) else {}
        return {
            "review_overall": scores.get("overall"),
            "quality_gate_passed": quality_gate.get("passed"),
            "quality_gate_categories": quality_gate.get("categories_below_threshold", []),
            "strategy": str(loop_payload.get("strategy", "")).strip(),
            "strategy_change_required": bool(loop_payload.get("strategy_change_required")),
            "scope_restriction": str(
                loop_payload.get("next_scope_restriction") or loop_payload.get("scope_restriction") or ""
            ).strip(),
            "resume_mode": str(resume_state.get("mode", "none") or "none"),
            "resume_enabled": bool(resume_state.get("enabled")),
            "resume_reason": str(resume_state.get("reason", "")).strip(),
            "resume_from_node_type": str(resume_state.get("resume_from_node_type", "")).strip(),
            "next_task_title": str(first_task.get("title", "")).strip(),
            "recommended_node_type": str(first_task.get("recommended_node_type", "")).strip(),
            "maturity_level": str(maturity_payload.get("level", "")).strip(),
            "maturity_score": maturity_payload.get("score"),
            "maturity_progression": str(maturity_payload.get("progression", "")).strip(),
            "quality_trend_direction": str(trend_payload.get("trend_direction", "")).strip(),
            "quality_delta_from_previous": trend_payload.get("delta_from_previous"),
            "quality_review_rounds": trend_payload.get("review_round_count"),
            "persistent_low_categories": trend_payload.get("persistent_low_categories", []),
            "stagnant_categories": trend_payload.get("stagnant_categories", []),
            "category_deltas": trend_payload.get("category_deltas", {}),
            "shadow_strategy": str(strategy_shadow_payload.get("shadow_strategy", "")).strip(),
            "shadow_confidence": strategy_shadow_payload.get("confidence"),
            "shadow_diverged": bool(strategy_shadow_payload.get("diverged")),
            "shadow_decision_mode": str(strategy_shadow_payload.get("decision_mode", "")).strip(),
            "retrieval_enabled": bool(memory_trace_payload.get("enabled")),
            "retrieval_source": str(memory_trace_payload.get("source", "")).strip(),
            "retrieval_fallback_used": bool(memory_trace_payload.get("fallback_used")),
            "retrieval_selected_total": int(memory_trace_payload.get("selected_total", 0) or 0),
            "retrieval_generated_at": str(memory_trace_payload.get("generated_at", "")).strip(),
            "retrieval_route_counts": {
                route_name: int((route_payload.get("selected_count", 0) if isinstance(route_payload, dict) else 0) or 0)
                for route_name, route_payload in memory_routes.items()
            },
            "execution_repository": self.job_execution_repository(job),
        }

    def read_job_memory_trace(self, job: JobRecord) -> Dict[str, Any]:
        """Read one job's structured memory retrieval trace."""

        workspace_path = self.job_workspace_path(job)
        return self.read_dashboard_json(workspace_path / "_docs" / "MEMORY_TRACE.json")

    def read_job_assistant_diagnosis_trace(self, job: JobRecord) -> Dict[str, Any]:
        """Read one job's latest assistant diagnosis trace artifact."""

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        trace_payload = self.read_dashboard_json(paths["assistant_diagnosis_trace"])
        if not isinstance(trace_payload, dict):
            return {}

        tool_runs = trace_payload.get("tool_runs", [])
        return {
            "enabled": bool(trace_payload.get("enabled")),
            "generated_at": str(trace_payload.get("generated_at", "")).strip(),
            "assistant_scope": str(trace_payload.get("assistant_scope", "")).strip(),
            "question": str(trace_payload.get("question", "")).strip(),
            "trace_path": str(paths["assistant_diagnosis_trace"]),
            "combined_context_length": int(trace_payload.get("combined_context_length", 0) or 0),
            "tool_runs": tool_runs if isinstance(tool_runs, list) else [],
        }

    @staticmethod
    def normalized_job_kind(job: JobRecord | Dict[str, Any]) -> str:
        """Normalize issue-backed vs follow-up job kind for UI."""

        raw = job.get("job_kind") if isinstance(job, dict) else getattr(job, "job_kind", "")
        normalized = str(raw or "").strip().lower()
        return normalized or "issue"

    @classmethod
    def job_kind_label(cls, job_kind: str) -> str:
        """Return a short Korean badge label for one job kind."""

        return {
            "followup_backlog": "후속 작업",
            "issue": "이슈 작업",
        }.get(cls.normalized_job_kind({"job_kind": job_kind}), "일반 작업")

    @classmethod
    def job_link_summary(cls, job: JobRecord | None) -> Dict[str, Any] | None:
        """Return a compact payload for lineage links."""

        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status,
            "stage": job.stage,
            "issue_number": int(job.issue_number or 0),
            "branch_name": job.branch_name,
            "job_kind": cls.normalized_job_kind(job),
            "job_kind_label": cls.job_kind_label(cls.normalized_job_kind(job)),
            "created_at": str(job.created_at or ""),
            "updated_at": str(job.updated_at or ""),
            "repository": str(job.repository or ""),
            "app_code": str(job.app_code or ""),
        }

    def matching_followup_artifact(self, job: JobRecord) -> Dict[str, Any]:
        """Return follow-up artifact payload only when it belongs to the current lineage."""

        workspace_path = self.job_workspace_path(job)
        artifact_payload = self.read_dashboard_json(build_workflow_artifact_paths(workspace_path)["followup_backlog_task"])
        if not artifact_payload:
            return {}

        queued_job_id = str(artifact_payload.get("queued_job_id", "")).strip()
        candidate_id = str(artifact_payload.get("candidate_id", "")).strip()
        source_job_id = str(artifact_payload.get("source_job_id", "")).strip()
        normalized_candidate_id = str(job.backlog_candidate_id or "").strip()
        if not any(
            [
                queued_job_id and queued_job_id == job.job_id,
                normalized_candidate_id and candidate_id == normalized_candidate_id,
                source_job_id and source_job_id == job.job_id,
                str(job.parent_job_id or "").strip() and source_job_id == str(job.parent_job_id or "").strip(),
            ]
        ):
            return {}

        return {
            "candidate_id": candidate_id,
            "queued_job_id": queued_job_id,
            "source_job_id": source_job_id,
            "recommended_node_type": str(artifact_payload.get("recommended_node_type", "")).strip(),
            "action": str(artifact_payload.get("action", "")).strip(),
            "job_contract": artifact_payload.get("job_contract", {}) if isinstance(artifact_payload.get("job_contract"), dict) else {},
            "generated_at": str(artifact_payload.get("generated_at", "")).strip(),
        }

    def build_job_lineage(self, job: JobRecord) -> Dict[str, Any]:
        """Collect parent/child/backlog lineage data for one job detail page."""

        normalized_kind = self.normalized_job_kind(job)
        parent_job_id = str(job.parent_job_id or "").strip()
        backlog_candidate_id = str(job.backlog_candidate_id or "").strip()

        if self.store is None:
            raise RuntimeError("DashboardJobRuntime.store is required for build_job_lineage")
        parent_job = self.store.get_job(parent_job_id) if parent_job_id else None
        child_jobs = [
            self.job_link_summary(item)
            for item in self.store.list_jobs()
            if str(item.parent_job_id or "").strip() == job.job_id
        ]
        child_jobs = [item for item in child_jobs if item is not None]
        child_jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)

        backlog_candidate = None
        if backlog_candidate_id:
            backlog_candidate = self.get_memory_runtime_store().get_backlog_candidate(backlog_candidate_id)

        return {
            "job_kind": normalized_kind,
            "job_kind_label": self.job_kind_label(normalized_kind),
            "is_followup": normalized_kind == "followup_backlog",
            "issue_backed": bool(int(job.issue_number or 0)),
            "parent_job": self.job_link_summary(parent_job),
            "parent_job_id": parent_job_id,
            "child_jobs": child_jobs[:12],
            "child_count": len(child_jobs),
            "backlog_candidate_id": backlog_candidate_id,
            "backlog_candidate": backlog_candidate,
            "followup_artifact": self.matching_followup_artifact(job),
        }

    def build_job_self_growing_effectiveness(self, job: JobRecord) -> Dict[str, Any]:
        """Return follow-up effectiveness comparison artifact for one job detail page."""

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        artifact_path = paths["self_growing_effectiveness"]
        payload = self.read_dashboard_json(artifact_path)
        is_followup = self.normalized_job_kind(job) == "followup_backlog" or bool(
            str(job.parent_job_id or "").strip() or str(job.backlog_candidate_id or "").strip()
        )
        if not payload:
            return {"active": False, "expected": is_followup, "artifact_path": str(artifact_path)} if is_followup else {}
        artifact_job_id = str(payload.get("job_id", "")).strip()
        if is_followup and artifact_job_id and artifact_job_id != job.job_id:
            return {
                "active": False,
                "expected": True,
                "artifact_path": str(artifact_path),
                "artifact_job_id": artifact_job_id,
                "mismatched_job_artifact": True,
            }

        enriched = dict(payload)
        backlog_candidate_id = str(enriched.get("backlog_candidate_id", "")).strip()
        backlog_candidate = None
        if backlog_candidate_id:
            runtime_store = self.get_memory_runtime_store()
            if runtime_store is not None and hasattr(runtime_store, "get_backlog_candidate"):
                backlog_candidate = runtime_store.get_backlog_candidate(backlog_candidate_id)
        cluster_recurrence = self._self_growing_cluster_runtime.build_cluster_recurrence(
            backlog_candidate=backlog_candidate,
            failure_patterns_path=paths["failure_patterns"],
        )
        if cluster_recurrence:
            enriched["cluster_recurrence"] = cluster_recurrence
        enriched["artifact_path"] = str(artifact_path)
        enriched["expected"] = is_followup
        return enriched

    def build_job_mobile_e2e_result(self, job: JobRecord) -> Dict[str, Any]:
        """Return latest mobile E2E artifact for app targets."""

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        artifact_path = paths["mobile_e2e_result"]
        payload = self.read_dashboard_json(artifact_path)
        if not payload:
            return {}

        return {
            "active": True,
            "artifact_path": str(artifact_path),
            "generated_at": str(payload.get("generated_at", "")).strip(),
            "platform": str(payload.get("platform", "")).strip(),
            "target_name": str(payload.get("target_name", "")).strip(),
            "target_id": str(payload.get("target_id", "")).strip(),
            "booted": bool(payload.get("booted")),
            "command": str(payload.get("command", "")).strip(),
            "exit_code": int(payload.get("exit_code", 1) or 1),
            "status": str(payload.get("status", "")).strip(),
            "runner": str(payload.get("runner", "")).strip(),
            "notes": str(payload.get("notes", "")).strip(),
        }

    def build_job_needs_human_summary(
        self,
        job: JobRecord,
        *,
        runtime_recovery_trace: Dict[str, Any],
        failure_classification: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return operator handoff summary for jobs currently waiting on humans."""

        trace_payload = runtime_recovery_trace if isinstance(runtime_recovery_trace, dict) else {}
        events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            summary = event.get("needs_human_summary")
            if isinstance(summary, dict) and summary.get("active"):
                return dict(summary)

        if str(job.recovery_status or "").strip() not in {"needs_human", "provider_quarantined", "provider_circuit_open"}:
            return {}

        normalized_status = str(job.recovery_status or "").strip()
        recovery_path = "manual_handoff"
        if normalized_status == "provider_quarantined":
            recovery_path = "provider_quarantine"
        elif normalized_status == "provider_circuit_open":
            recovery_path = "provider_circuit_breaker"
        summary = build_needs_human_summary(
            failure_class=str(failure_classification.get("failure_class", "")).strip(),
            provider_hint=str(failure_classification.get("provider_hint", "")).strip(),
            stage_family=str(failure_classification.get("stage_family", "")).strip(),
            reason_code=str(failure_classification.get("reason_code", "")).strip(),
            reason=str(job.recovery_reason or job.error_message or "").strip(),
            source=str(failure_classification.get("source", "job_record")).strip(),
            generated_at=str(trace_payload.get("latest_event_at", "")).strip(),
            details={},
        )
        summary["recovery_path"] = recovery_path
        return summary

    def build_job_dead_letter_summary(
        self,
        job: JobRecord,
        *,
        runtime_recovery_trace: Dict[str, Any],
        failure_classification: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return structured dead-letter summary for jobs quarantined after failure."""

        if str(job.recovery_status or "").strip() != "dead_letter":
            return {}

        trace_payload = runtime_recovery_trace if isinstance(runtime_recovery_trace, dict) else {}
        events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            summary = event.get("dead_letter_summary")
            if isinstance(summary, dict) and summary.get("active"):
                return dict(summary)

        return build_dead_letter_summary(
            failure_class=str(failure_classification.get("failure_class", "")).strip(),
            provider_hint=str(failure_classification.get("provider_hint", "")).strip(),
            stage_family=str(failure_classification.get("stage_family", "")).strip(),
            reason_code=str(failure_classification.get("reason_code", "")).strip(),
            reason=str(job.recovery_reason or job.error_message or "").strip(),
            source=str(failure_classification.get("source", "job_record")).strip(),
            generated_at=str(trace_payload.get("latest_event_at", "")).strip(),
            details={
                "upstream_recovery_status": str(job.recovery_status or "").strip(),
            },
        )

    def build_job_dead_letter_action_trail(
        self,
        *,
        runtime_recovery_trace: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return recent dead-letter related operator actions from recovery trace."""

        trace_payload = runtime_recovery_trace if isinstance(runtime_recovery_trace, dict) else {}
        events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
        trail: List[Dict[str, Any]] = []
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            decision = str(event.get("decision", "")).strip()
            recovery_status = str(event.get("recovery_status", "")).strip()
            if decision not in {"dead_letter", "retry_from_dead_letter"} and recovery_status not in {
                "dead_letter",
                "dead_letter_requeued",
            }:
                continue
            details = event.get("details", {}) if isinstance(event.get("details"), dict) else {}
            trail.append(
                {
                    "generated_at": str(event.get("generated_at", "")).strip(),
                    "source": str(event.get("source", "")).strip(),
                    "decision": decision,
                    "recovery_status": recovery_status,
                    "reason_code": str(event.get("reason_code", "")).strip(),
                    "reason": str(event.get("reason", "")).strip(),
                    "failure_class": str(event.get("failure_class", "")).strip(),
                    "provider_hint": str(event.get("provider_hint", "")).strip(),
                    "stage_family": str(event.get("stage_family", "")).strip(),
                    "operator_note": str(details.get("operator_note", "")).strip(),
                    "previous_recovery_status": str(details.get("previous_recovery_status", "")).strip(),
                    "previous_reason": str(details.get("previous_reason", "")).strip(),
                    "retry_from_scratch": bool(details.get("retry_from_scratch")),
                }
            )
        return trail[:5]

    def build_job_requeue_reason_summary(
        self,
        job: JobRecord,
        *,
        runtime_recovery_trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return structured requeue summary for restart-safe rerun flows."""

        recovery_status = str(job.recovery_status or "").strip()
        if recovery_status not in REQUEUE_RECOVERY_STATUSES:
            return {}

        trace_payload = runtime_recovery_trace if isinstance(runtime_recovery_trace, dict) else {}
        events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            summary = event.get("requeue_reason_summary")
            if isinstance(summary, dict) and summary.get("active"):
                return dict(summary)

        source = "job_record"
        decision = "requeue"
        details: Dict[str, Any] = {}
        if recovery_status == "dead_letter_requeued":
            source = "dashboard_dead_letter_retry"
            decision = "retry_from_dead_letter"
            details["retry_from_scratch"] = True
        elif recovery_status == "manual_rerun_queued":
            source = "dashboard_manual_retry"
            decision = "manual_rerun_requeue"
            details["retry_from_scratch"] = True
            details["operator_note"] = str(job.manual_resume_note or "").strip()
        elif recovery_status == "manual_resume_queued":
            source = "dashboard_manual_retry"
            decision = "manual_resume_requeue"
            details["target_node_id"] = str(job.manual_resume_node_id or "").strip()
            details["operator_note"] = str(job.manual_resume_note or "").strip()
        elif recovery_status == "auto_recovered":
            source = "worker_stale_recovery"

        return build_requeue_reason_summary(
            source=source,
            reason_code="requeue_reason",
            reason=str(job.recovery_reason or job.error_message or "").strip(),
            decision=decision,
            recovery_status=recovery_status,
            generated_at=str(trace_payload.get("latest_event_at", "")).strip(),
            details=details,
        )

    def build_job_log_summary(
        self,
        job: JobRecord,
        *,
        events: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Return operator-friendly summary for debug/user log channels."""

        debug_log_path = self.resolve_channel_log_path(self.settings, job.log_file, "debug")
        user_log_path = self.resolve_channel_log_path(self.settings, job.log_file, "user")
        kind_counts: Counter[str] = Counter()
        actor_counts: Counter[str] = Counter()
        error_count = 0
        optional_error_count = 0
        warn_count = 0
        auth_hint_count = 0
        nonzero_done_count = 0
        latest_error: Dict[str, str] | None = None
        latest_optional_error: Dict[str, str] | None = None
        latest_auth_hint: Dict[str, str] | None = None
        latest_command: Dict[str, str] | None = None

        for event in events:
            kind = str(event.get("kind", "")).strip().lower() or "info"
            kind_counts[kind] += 1

            actor = ""
            if kind == "run":
                actor = str(event.get("receiver", "")).strip().lower()
                latest_command = {
                    "timestamp": str(event.get("timestamp", "")).strip(),
                    "actor": actor or "shell",
                    "message": str(event.get("message", "")).strip(),
                }
            elif kind in {"stdout", "stderr", "done"}:
                actor = str(event.get("speaker", "")).strip().lower()
            elif str(event.get("speaker", "")).strip().lower() not in {"agenthub", "dashboard"}:
                actor = str(event.get("speaker", "")).strip().lower()

            if actor:
                actor_counts[actor] += 1

            auth_hint = classify_cli_health_hint(str(event.get("message", "")).strip(), actor=actor)
            if auth_hint:
                auth_hint_count += 1
                latest_auth_hint = {
                    "timestamp": str(event.get("timestamp", "")).strip(),
                    "actor": actor or "shell",
                    "message": auth_hint,
                    "kind": kind,
                }

            if kind == "stderr":
                warn_count += 1
                target = {
                    "timestamp": str(event.get("timestamp", "")).strip(),
                    "actor": actor or "shell",
                    "message": str(event.get("message", "")).strip(),
                    "kind": kind,
                }
                if is_optional_helper_actor(actor):
                    optional_error_count += 1
                    latest_optional_error = target
                else:
                    error_count += 1
                    latest_error = target
                continue

            if kind == "done":
                matched = re.search(r"exit_code=(\d+)", str(event.get("message", "")))
                if matched and int(matched.group(1)) != 0:
                    nonzero_done_count += 1
                    target = {
                        "timestamp": str(event.get("timestamp", "")).strip(),
                        "actor": actor or "shell",
                        "message": str(event.get("message", "")).strip(),
                        "kind": kind,
                    }
                    if is_optional_helper_actor(actor):
                        optional_error_count += 1
                        latest_optional_error = target
                    else:
                        error_count += 1
                        latest_error = target

        top_actors = [{"name": actor, "count": count} for actor, count in actor_counts.most_common(6)]
        return {
            "event_count": len(events),
            "kind_counts": dict(kind_counts),
            "actor_counts": dict(actor_counts),
            "top_actors": top_actors,
            "error_count": error_count,
            "optional_error_count": optional_error_count,
            "total_error_signal_count": error_count + optional_error_count,
            "warn_count": warn_count,
            "auth_hint_count": auth_hint_count,
            "nonzero_done_count": nonzero_done_count,
            "latest_error": latest_error or latest_optional_error or {},
            "latest_optional_error": latest_optional_error or {},
            "latest_auth_hint": latest_auth_hint or {},
            "latest_command": latest_command or {},
            "channels": {
                "debug": {
                    "exists": debug_log_path.exists(),
                    "url": f"/logs/{job.log_file}?channel=debug",
                },
                "user": {
                    "exists": user_log_path.exists(),
                    "url": f"/logs/{job.log_file}?channel=user",
                },
            },
        }

    def build_job_operator_inputs(self, job: JobRecord) -> Dict[str, Any]:
        """Return read-only operator runtime input state for one job detail page."""

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        if self.store is None:
            raise RuntimeError("DashboardJobRuntime.store is required for build_job_operator_inputs")
        resolved = resolve_runtime_inputs(
            self.store.list_runtime_inputs(),
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            integration_registry_entries=self.store.list_integration_registry_entries(),
        )
        resolved_inputs = resolved.get("resolved", []) if isinstance(resolved, dict) else []
        pending_inputs = resolved.get("pending", []) if isinstance(resolved, dict) else []
        blocked_inputs = resolved.get("blocked", []) if isinstance(resolved, dict) else []
        environment = dict(resolved.get("environment", {}) or {}) if isinstance(resolved, dict) else {}
        blocked_environment = dict(resolved.get("blocked_environment", {}) or {}) if isinstance(resolved, dict) else {}
        return {
            "artifact_path": str(paths["operator_inputs"]),
            "available_count": len(resolved_inputs),
            "pending_count": len(pending_inputs),
            "blocked_count": len(blocked_inputs),
            "available_env_vars": sorted(environment.keys()),
            "blocked_env_vars": sorted(blocked_environment.keys()),
            "resolved_inputs": resolved_inputs,
            "pending_inputs": pending_inputs,
            "blocked_inputs": blocked_inputs,
        }

    def build_job_integration_operator_boundary(self, job: JobRecord) -> Dict[str, Any]:
        """Return failed-job boundary summary for integration approval/input blocking."""

        normalized_status = str(job.status or "").strip().lower()
        normalized_recovery_status = str(job.recovery_status or "").strip().lower()
        if normalized_status != "failed" and normalized_recovery_status not in {
            "needs_human",
            "dead_letter",
            "provider_quarantined",
            "provider_circuit_open",
        }:
            return {}

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        recommendation_payload = self.read_dashboard_json(paths["integration_recommendations"])
        recommendation_items = (
            recommendation_payload.get("items", [])
            if isinstance(recommendation_payload.get("items"), list)
            else []
        )
        operator_inputs = self.build_job_operator_inputs(job)
        blocked_inputs = operator_inputs.get("blocked_inputs", []) if isinstance(operator_inputs, dict) else []
        pending_inputs = operator_inputs.get("pending_inputs", []) if isinstance(operator_inputs, dict) else []

        blocked_by_integration: Dict[str, List[Dict[str, Any]]] = {}
        for item in list(blocked_inputs) + list(pending_inputs):
            if not isinstance(item, dict):
                continue
            linked = [str(entry).strip() for entry in item.get("linked_integrations", []) if str(entry).strip()]
            for integration_id in linked:
                blocked_by_integration.setdefault(integration_id, []).append(item)

        runtime_input_records = self.store.list_runtime_inputs() if self.store is not None else []
        registry_map: Dict[str, Dict[str, Any]] = {}
        if self.store is not None:
            for entry in self.store.list_integration_registry_entries():
                serialized = DashboardIntegrationRegistryRuntime.serialize_entry(
                    entry,
                    runtime_input_records=runtime_input_records,
                )
                registry_map[str(serialized.get("integration_id", "")).strip()] = serialized

        candidates: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        def _append_candidate(raw: Dict[str, Any], *, fallback_reason: str = "") -> None:
            integration_id = str(raw.get("integration_id", "")).strip()
            if not integration_id or integration_id in seen_ids:
                return
            recommendation_status = str(raw.get("recommendation_status", "")).strip()
            input_readiness_status = str(raw.get("input_readiness_status", "")).strip()
            approval_status = str(raw.get("approval_status", "")).strip()
            required_env_keys = [str(item).strip() for item in raw.get("required_env_keys", []) if str(item).strip()]
            if recommendation_status not in {
                "operator_review_required",
                "operator_review_and_input_required",
                "operator_rejected",
            } and input_readiness_status not in {
                "approval_required",
                "approval_rejected",
                "input_required",
                "input_requested",
            }:
                return
            seen_ids.add(integration_id)
            linked_inputs = list(blocked_by_integration.get(integration_id, []))
            if required_env_keys:
                for candidate_input in list(blocked_inputs) + list(pending_inputs):
                    if not isinstance(candidate_input, dict):
                        continue
                    env_var_name = str(candidate_input.get("env_var_name", "")).strip()
                    if env_var_name and env_var_name in required_env_keys and candidate_input not in linked_inputs:
                        linked_inputs.append(candidate_input)
            candidates.append(
                {
                    "integration_id": integration_id,
                    "display_name": str(raw.get("display_name", "")).strip() or integration_id,
                    "recommendation_status": recommendation_status,
                    "input_readiness_status": input_readiness_status,
                    "input_readiness_reason": str(raw.get("input_readiness_reason", "")).strip() or fallback_reason,
                    "approval_status": approval_status,
                    "approval_required": bool(raw.get("approval_required")),
                    "required_env_keys": required_env_keys,
                    "reason": str(raw.get("reason", "")).strip() or fallback_reason,
                    "blocked_inputs": [
                        {
                            "env_var_name": str(item.get("env_var_name", "")).strip(),
                            "bridge_reason": str(item.get("bridge_reason", "")).strip(),
                            "status": str(item.get("status", "")).strip(),
                        }
                        for item in linked_inputs
                    ],
                }
            )

        for item in recommendation_items:
            if not isinstance(item, dict):
                continue
            merged = dict(registry_map.get(str(item.get("integration_id", "")).strip(), {}))
            merged.update(item)
            _append_candidate(merged)

        for integration_id, linked_inputs in blocked_by_integration.items():
            if integration_id in seen_ids:
                continue
            fallback = dict(registry_map.get(integration_id, {}))
            fallback.setdefault("integration_id", integration_id)
            fallback.setdefault("display_name", integration_id)
            fallback.setdefault("recommendation_status", "")
            fallback.setdefault("reason", "")
            fallback_reason = "운영자 승인 또는 필수 입력이 준비되지 않아 runtime env bridge가 차단됐습니다."
            if linked_inputs:
                fallback_reason = str(linked_inputs[0].get("bridge_reason", "")).strip() or fallback_reason
            _append_candidate(fallback, fallback_reason=fallback_reason)

        if not candidates:
            return {}

        def _boundary_status() -> str:
            if any(item["recommendation_status"] == "operator_review_and_input_required" for item in candidates):
                return "approval_and_input_required"
            if any(item["input_readiness_status"] in {"input_required", "input_requested"} for item in candidates):
                return "operator_input_required"
            if any(item["input_readiness_status"] == "approval_rejected" or item["recommendation_status"] == "operator_rejected" for item in candidates):
                return "operator_rejected"
            return "operator_approval_required"

        boundary_status = _boundary_status()
        status_label_map = {
            "approval_and_input_required": "승인과 입력이 모두 필요",
            "operator_input_required": "운영자 입력 필요",
            "operator_rejected": "보류된 통합 있음",
            "operator_approval_required": "운영자 승인 필요",
        }
        recommended_actions = {
            "approval_and_input_required": [
                "통합 승인 여부를 먼저 결정합니다.",
                "필수 env 요청/제공 상태를 확인하고 값을 제공합니다.",
                "조치 후 failed job 재큐잉 또는 수동 재개를 진행합니다.",
            ],
            "operator_input_required": [
                "필수 env 요청 상태를 확인하고 값을 제공합니다.",
                "값 제공 후 failed job 재큐잉 또는 수동 재개를 진행합니다.",
            ],
            "operator_rejected": [
                "보류된 통합을 계속 제외할지, 다시 검토로 돌릴지 결정합니다.",
                "필요하면 대체 구현 경로나 범위 축소를 선택합니다.",
            ],
            "operator_approval_required": [
                "추천된 통합의 승인 여부를 결정합니다.",
                "승인 후 failed job 재큐잉 또는 수동 재개를 진행합니다.",
            ],
        }[boundary_status]

        return {
            "active": True,
            "boundary_status": boundary_status,
            "boundary_status_label": status_label_map.get(boundary_status, "운영자 판단 필요"),
            "summary": f"이 작업은 외부 통합의 승인 또는 운영자 입력 준비 상태 때문에 막혔을 가능성이 큽니다.",
            "artifact_path": str(paths["integration_recommendations"]),
            "candidate_count": len(candidates),
            "blocked_input_count": len(blocked_inputs),
            "pending_input_count": len(pending_inputs),
            "recommended_actions": recommended_actions,
            "candidates": candidates[:5],
        }

    def build_job_integration_usage_trail(self, job: JobRecord) -> Dict[str, Any]:
        """Return recent integration usage audit trail for one job detail page."""

        workspace_path = self.job_workspace_path(job)
        paths = build_workflow_artifact_paths(workspace_path)
        payload = self.read_dashboard_json(paths["integration_usage_trail"])
        events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
        if not events:
            return {}
        recent_events = []
        used_ids: set[str] = set()
        for event in reversed(events[-6:]):
            if not isinstance(event, dict):
                continue
            items = event.get("items", []) if isinstance(event.get("items"), list) else []
            for item in items:
                if isinstance(item, dict):
                    integration_id = str(item.get("integration_id", "")).strip()
                    if integration_id:
                        used_ids.add(integration_id)
            recent_events.append(
                {
                    "generated_at": str(event.get("generated_at", "")).strip(),
                    "stage": str(event.get("stage", "")).strip(),
                    "route": str(event.get("route", "")).strip(),
                    "prompt_path": str(event.get("prompt_path", "")).strip(),
                    "integration_count": int(event.get("integration_count", 0) or 0),
                    "blocked_integration_count": int(event.get("blocked_integration_count", 0) or 0),
                    "blocked_env_vars": list(event.get("blocked_env_vars", []) or []),
                    "items": items[:5],
                }
            )
        latest_event = recent_events[0] if recent_events else {}
        return {
            "active": True,
            "artifact_path": str(paths["integration_usage_trail"]),
            "event_count": len(events),
            "used_integration_count": len(used_ids),
            "used_integration_ids": sorted(used_ids),
            "latest_event": latest_event,
            "recent_events": recent_events,
        }

    def build_job_integration_health_facets(
        self,
        *,
        job: JobRecord,
        integration_operator_boundary: Dict[str, Any],
        integration_usage_trail: Dict[str, Any],
        log_summary: Dict[str, Any],
        failure_classification: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Return one operator-facing facet summary for integration blockers."""

        boundary = integration_operator_boundary if isinstance(integration_operator_boundary, dict) else {}
        usage = integration_usage_trail if isinstance(integration_usage_trail, dict) else {}
        logs = log_summary if isinstance(log_summary, dict) else {}
        failure = failure_classification if isinstance(failure_classification, dict) else {}

        candidates = boundary.get("candidates", []) if isinstance(boundary.get("candidates"), list) else []
        used_ids = [str(item).strip() for item in usage.get("used_integration_ids", []) if str(item).strip()]
        used_id_set = set(used_ids)
        blocked_env_vars = {
            str(item).strip()
            for item in usage.get("latest_event", {}).get("blocked_env_vars", [])
            if str(item).strip()
        }
        missing_candidates: list[Dict[str, Any]] = []
        candidate_ids: list[str] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            readiness = str(item.get("input_readiness_status", "")).strip()
            if readiness not in {"input_required", "input_requested"}:
                continue
            integration_id = str(item.get("integration_id", "")).strip()
            if integration_id:
                candidate_ids.append(integration_id)
            blocked_inputs = item.get("blocked_inputs", []) if isinstance(item.get("blocked_inputs"), list) else []
            for blocked in blocked_inputs:
                if isinstance(blocked, dict):
                    env_name = str(blocked.get("env_var_name", "")).strip()
                    if env_name:
                        blocked_env_vars.add(env_name)
            missing_candidates.append(
                {
                    "integration_id": integration_id,
                    "display_name": str(item.get("display_name", "")).strip() or integration_id,
                    "input_readiness_status": readiness,
                    "input_readiness_reason": str(item.get("input_readiness_reason", "")).strip(),
                    "blocked_inputs": blocked_inputs[:3],
                    "used_in_this_job": integration_id in used_id_set,
                }
            )

        latest_hint = logs.get("latest_auth_hint", {}) if isinstance(logs.get("latest_auth_hint"), dict) else {}
        hint_message = str(latest_hint.get("message", "")).strip()
        failure_class = str(failure.get("failure_class", "")).strip()
        provider_hint = str(failure.get("provider_hint", "")).strip()
        stage_family = str(failure.get("stage_family", "")).strip()
        failure_reason = str(failure.get("reason", "")).strip()
        failure_source = str(failure.get("source", "")).strip()

        auth_active = failure_class == "provider_auth" or ("로그인/인증 상태 확인 필요" in hint_message)
        quota_active = failure_class == "provider_quota" or ("사용량/쿼터 확인 필요" in hint_message)

        return {
            "active": bool(missing_candidates or auth_active or quota_active),
            "missing_input": {
                "active": bool(missing_candidates),
                "candidate_count": len(missing_candidates),
                "candidate_ids": candidate_ids[:5],
                "blocked_env_vars": sorted(blocked_env_vars),
                "summary": (
                    f"필수 env {len(blocked_env_vars)}건이 아직 준비되지 않았거나 요청 상태라 통합 구현이 대기 중입니다."
                    if missing_candidates
                    else ""
                ),
                "candidates": missing_candidates[:5],
            },
            "auth": {
                "active": auth_active,
                "provider_hint": provider_hint,
                "stage_family": stage_family,
                "source": failure_source or ("log_summary" if hint_message else ""),
                "summary": hint_message if "로그인/인증 상태 확인 필요" in hint_message else failure_reason,
            },
            "quota": {
                "active": quota_active,
                "provider_hint": provider_hint,
                "stage_family": stage_family,
                "source": failure_source or ("log_summary" if hint_message else ""),
                "summary": hint_message if "사용량/쿼터 확인 필요" in hint_message else failure_reason,
            },
        }
