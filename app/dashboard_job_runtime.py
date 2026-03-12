"""Job-detail/dashboard helper runtime for dashboard routes."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List

from app.config import AppSettings
from app.dead_letter_policy import build_dead_letter_summary
from app.log_signal_utils import classify_cli_health_hint, is_optional_helper_actor
from app.models import JobRecord
from app.needs_human_policy import build_needs_human_summary
from app.requeue_reason_runtime import REQUEUE_RECOVERY_STATUSES, build_requeue_reason_summary
from app.runtime_inputs import resolve_runtime_inputs
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
        )
        resolved_inputs = resolved.get("resolved", []) if isinstance(resolved, dict) else []
        pending_inputs = resolved.get("pending", []) if isinstance(resolved, dict) else []
        environment = dict(resolved.get("environment", {}) or {}) if isinstance(resolved, dict) else {}
        return {
            "artifact_path": str(paths["operator_inputs"]),
            "available_count": len(resolved_inputs),
            "pending_count": len(pending_inputs),
            "available_env_vars": sorted(environment.keys()),
            "resolved_inputs": resolved_inputs,
            "pending_inputs": pending_inputs,
        }
