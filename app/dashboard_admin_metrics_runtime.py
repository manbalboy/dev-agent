"""Admin metrics aggregation runtime for dashboard APIs."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.config import AppSettings
from app.memory import MemoryRuntimeStore
from app.models import JobRecord
from app.provider_failure_counter_runtime import read_provider_failure_counters
from app.store import JobStore
from app.worker_startup_sweep_runtime import read_worker_startup_sweep_trace
from app.workflow_design import load_workflows, schema_payload
from app.feature_flags import read_feature_flags
from app.workflow_resume import build_workflow_artifact_paths


class DashboardAdminMetricsRuntime:
    """Encapsulate admin metrics aggregation while preserving dashboard contracts."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        feature_flags_config_path: Path,
        apps_config_path: Path,
        workflows_config_path: Path,
        roles_config_path: Path,
        list_dashboard_jobs: Callable[[JobStore, AppSettings], List[Dict[str, Any]]],
        build_job_summary: Callable[[List[Dict[str, Any]]], Dict[str, int]],
        read_default_workflow_id: Callable[[Path], str],
        read_registered_apps: Callable[[Path, str], List[Dict[str, Any]]],
        read_roles_payload: Callable[[Path], Dict[str, Any]],
        get_memory_runtime_store: Callable[[AppSettings], MemoryRuntimeStore],
        read_dashboard_json: Callable[[Path], Dict[str, Any]],
        read_dashboard_jsonl: Callable[[Path], List[Dict[str, Any]]],
        job_workspace_path: Callable[[JobRecord, AppSettings], Path],
        read_job_assistant_diagnosis_trace: Callable[[JobRecord, AppSettings], Dict[str, Any]],
        top_counter_items: Callable[[Counter[str]], List[Dict[str, Any]]],
        safe_average: Callable[[List[float]], Optional[float]],
        latest_non_empty: Callable[[List[str]], str],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.store = store
        self.settings = settings
        self.feature_flags_config_path = feature_flags_config_path
        self.apps_config_path = apps_config_path
        self.workflows_config_path = workflows_config_path
        self.roles_config_path = roles_config_path
        self.list_dashboard_jobs = list_dashboard_jobs
        self.build_job_summary = build_job_summary
        self.read_default_workflow_id = read_default_workflow_id
        self.read_registered_apps = read_registered_apps
        self.read_roles_payload = read_roles_payload
        self.get_memory_runtime_store = get_memory_runtime_store
        self.read_dashboard_json = read_dashboard_json
        self.read_dashboard_jsonl = read_dashboard_jsonl
        self.job_workspace_path = job_workspace_path
        self.read_job_assistant_diagnosis_trace = read_job_assistant_diagnosis_trace
        self.top_counter_items = top_counter_items
        self.safe_average = safe_average
        self.latest_non_empty = latest_non_empty
        self.utc_now_iso = utc_now_iso

    @staticmethod
    def is_pid_alive(raw_pid: Any) -> bool:
        """Return whether one recorded app runner PID still exists."""

        try:
            pid = int(str(raw_pid or "").strip())
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def build_admin_app_runner_status(self) -> Dict[str, Any]:
        """Return recent app runner metadata written by workspace_app.sh."""

        meta_dir = self.settings.data_dir / "pids"
        mode_counter: Counter[str] = Counter()
        state_counter: Counter[str] = Counter()
        recent_runs: List[Dict[str, Any]] = []
        if not meta_dir.exists():
            return {
                "active_count": 0,
                "mobile_count": 0,
                "web_count": 0,
                "mode_counts": [],
                "state_counts": [],
                "recent_runs": [],
            }

        def _sort_key(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        for meta_path in sorted(meta_dir.glob("app_*.json"), key=_sort_key, reverse=True):
            payload = self.read_dashboard_json(meta_path)
            if not isinstance(payload, dict) or not payload:
                continue
            app_code = str(payload.get("app_code", "")).strip() or meta_path.stem.removeprefix("app_")
            repository = str(payload.get("repository", "")).strip()
            mode = str(payload.get("mode", "")).strip() or "web"
            command = str(payload.get("command", "")).strip()
            pid = str(payload.get("pid", "")).strip()
            port = str(payload.get("port", "")).strip()
            updated_at = str(payload.get("updated_at", "")).strip()
            log_file = str(payload.get("log_file", "")).strip()
            is_mobile = mode != "web"
            state = "running" if self.is_pid_alive(pid) else "stopped"
            mode_counter[mode] += 1
            state_counter[state] += 1
            recent_runs.append(
                {
                    "app_code": app_code,
                    "repository": repository,
                    "mode": mode,
                    "state": state,
                    "command": command,
                    "pid": pid,
                    "port": port,
                    "updated_at": updated_at,
                    "log_file": log_file,
                    "is_mobile": is_mobile,
                }
            )

        mobile_count = sum(1 for item in recent_runs if bool(item.get("is_mobile")))
        return {
            "active_count": len(recent_runs),
            "mobile_count": mobile_count,
            "web_count": max(0, len(recent_runs) - mobile_count),
            "mode_counts": self.top_counter_items(mode_counter, limit=8),
            "state_counts": self.top_counter_items(state_counter, limit=8),
            "recent_runs": recent_runs[:8],
        }

    def build_admin_assistant_diagnosis_metrics(self) -> Dict[str, Any]:
        """Aggregate recent assistant diagnosis traces for operator comparison."""

        scope_counter: Counter[str] = Counter()
        tool_counter: Counter[str] = Counter()
        failed_tool_counter: Counter[str] = Counter()
        generated_ats: List[str] = []
        recent_traces: List[Dict[str, Any]] = []

        jobs = sorted(
            self.store.list_jobs(),
            key=lambda item: item.updated_at or item.created_at or "",
            reverse=True,
        )
        for job in jobs:
            trace_payload = self.read_job_assistant_diagnosis_trace(job, self.settings)
            tool_runs = trace_payload.get("tool_runs", [])
            if not trace_payload or (not trace_payload.get("enabled") and not tool_runs):
                continue

            assistant_scope = str(trace_payload.get("assistant_scope", "")).strip() or "unknown"
            generated_at = str(trace_payload.get("generated_at", "")).strip()
            generated_ats.append(generated_at)
            scope_counter[assistant_scope] += 1

            ordered_tools: List[str] = []
            failed_tools: List[str] = []
            if isinstance(tool_runs, list):
                for item in tool_runs:
                    if not isinstance(item, dict):
                        continue
                    tool_name = str(item.get("tool", "")).strip() or "unknown"
                    ordered_tools.append(tool_name)
                    tool_counter[tool_name] += 1
                    if not bool(item.get("ok")):
                        failed_tool_counter[tool_name] += 1
                        failed_tools.append(tool_name)

            recent_traces.append(
                {
                    "job_id": job.job_id,
                    "detail_url": f"/jobs/{job.job_id}",
                    "status": job.status,
                    "stage": job.stage,
                    "app_code": job.app_code,
                    "assistant_scope": assistant_scope,
                    "question": str(trace_payload.get("question", "")).strip(),
                    "generated_at": generated_at,
                    "trace_path": str(trace_payload.get("trace_path", "")).strip(),
                    "combined_context_length": int(trace_payload.get("combined_context_length", 0) or 0),
                    "tool_run_count": len(tool_runs) if isinstance(tool_runs, list) else 0,
                    "failed_tool_count": len(failed_tools),
                    "tools": ordered_tools,
                    "failed_tools": failed_tools,
                    "tool_runs": [
                        {
                            "tool": str(item.get("tool", "")).strip() or "unknown",
                            "query": str(item.get("query", "")).strip(),
                            "ok": bool(item.get("ok")),
                            "mode": str(item.get("mode", "")).strip(),
                            "context_path": str(item.get("context_path", "")).strip(),
                            "result_path": str(item.get("result_path", "")).strip(),
                            "error": str(item.get("error", "")).strip(),
                        }
                        for item in tool_runs
                        if isinstance(item, dict)
                    ],
                }
            )

        return {
            "active": bool(recent_traces),
            "trace_count": len(recent_traces),
            "latest_generated_at": self.latest_non_empty(generated_ats),
            "scope_counts": self.top_counter_items(scope_counter, limit=8),
            "tool_counts": self.top_counter_items(tool_counter, limit=8),
            "failed_tool_counts": self.top_counter_items(failed_tool_counter, limit=8),
            "recent_traces": recent_traces[:8],
        }

    def build_admin_dead_letter_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return recent dead-letter jobs for operator triage."""

        items: List[Dict[str, Any]] = []
        ordered_jobs = sorted(
            jobs,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        for job in ordered_jobs:
            if str(job.get("recovery_status", "")).strip() != "dead_letter":
                continue
            items.append(
                {
                    "job_id": str(job.get("job_id", "")).strip(),
                    "detail_url": f"/jobs/{str(job.get('job_id', '')).strip()}",
                    "issue_title": str(job.get("issue_title", "")).strip(),
                    "app_code": str(job.get("app_code", "")).strip(),
                    "stage": str(job.get("stage", "")).strip(),
                    "status": str(job.get("status", "")).strip(),
                    "recovery_status": str(job.get("recovery_status", "")).strip(),
                    "failure_class": str(job.get("failure_class", "")).strip(),
                    "failure_provider_hint": str(job.get("failure_provider_hint", "")).strip(),
                    "updated_at": str(job.get("updated_at", "")).strip(),
                    "recovery_reason": str(job.get("recovery_reason", "")).strip(),
                }
            )
        return items[:8]

    def build_admin_dead_letter_summary(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return facet counts for dead-letter drilldown filters."""

        app_counter: Counter[str] = Counter()
        failure_class_counter: Counter[str] = Counter()
        provider_counter: Counter[str] = Counter()
        for item in items:
            if not isinstance(item, dict):
                continue
            app_code = str(item.get("app_code", "")).strip()
            if app_code:
                app_counter[app_code] += 1
            failure_class = str(item.get("failure_class", "")).strip()
            if failure_class:
                failure_class_counter[failure_class] += 1
            provider_hint = str(item.get("failure_provider_hint", "")).strip()
            if provider_hint:
                provider_counter[provider_hint] += 1
        return {
            "app_counts": self.top_counter_items(app_counter, limit=8),
            "failure_class_counts": self.top_counter_items(failure_class_counter, limit=8),
            "provider_counts": self.top_counter_items(provider_counter, limit=8),
        }

    def build_admin_recovery_history(self, workspace_paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return recent recovery trail items aggregated from runtime trace artifacts."""

        decision_counter: Counter[str] = Counter()
        provider_counter: Counter[str] = Counter()
        stage_family_counter: Counter[str] = Counter()
        recent_events: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()

        for workspace in workspace_paths.values():
            trace_payload = self.read_dashboard_json(
                build_workflow_artifact_paths(workspace)["runtime_recovery_trace"]
            )
            events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                generated_at = str(event.get("generated_at", "")).strip()
                job_id = str(event.get("job_id", "")).strip()
                source = str(event.get("source", "")).strip()
                decision = str(event.get("decision", "")).strip()
                recovery_status = str(event.get("recovery_status", "")).strip()
                reason = str(event.get("reason", "")).strip()
                if not decision and not recovery_status:
                    continue
                decision_label = decision or recovery_status or "recorded"
                decision_counter[decision_label] += 1
                provider_hint = str(event.get("provider_hint", "")).strip()
                if provider_hint:
                    provider_counter[provider_hint] += 1
                stage_family = str(event.get("stage_family", "")).strip()
                if stage_family:
                    stage_family_counter[stage_family] += 1
                dedupe_key = (generated_at, job_id, source, decision_label, reason)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                recent_events.append(
                    {
                        "generated_at": generated_at,
                        "job_id": job_id,
                        "detail_url": f"/jobs/{job_id}" if job_id else "",
                        "source": source,
                        "decision": decision,
                        "recovery_status": recovery_status,
                        "failure_class": str(event.get("failure_class", "")).strip(),
                        "provider_hint": provider_hint,
                        "stage_family": stage_family,
                        "reason_code": str(event.get("reason_code", "")).strip(),
                        "reason": reason,
                    }
                )

        recent_events.sort(key=lambda item: str(item.get("generated_at", "")), reverse=True)
        return {
            "event_counts": self.top_counter_items(decision_counter, limit=8),
            "provider_counts": self.top_counter_items(provider_counter, limit=8),
            "stage_family_counts": self.top_counter_items(stage_family_counter, limit=8),
            "recent_events": recent_events[:10],
        }

    def build_admin_provider_outage_history(self, workspace_paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return recent provider outage events aggregated from recovery traces."""

        decision_counter: Counter[str] = Counter()
        provider_counter: Counter[str] = Counter()
        recent_events: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        tracked_failure_classes = {
            "provider_timeout",
            "provider_quota",
            "provider_auth",
            "tool_failure",
        }
        tracked_decisions = {
            "cooldown_wait",
            "provider_quarantined",
            "provider_circuit_open",
            "needs_human",
        }

        for workspace in workspace_paths.values():
            trace_payload = self.read_dashboard_json(
                build_workflow_artifact_paths(workspace)["runtime_recovery_trace"]
            )
            events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                failure_class = str(event.get("failure_class", "")).strip()
                provider_hint = str(event.get("provider_hint", "")).strip()
                decision = str(event.get("decision", "")).strip()
                recovery_status = str(event.get("recovery_status", "")).strip()
                normalized_decision = decision or recovery_status or "recorded"
                if failure_class not in tracked_failure_classes and normalized_decision not in tracked_decisions:
                    continue
                generated_at = str(event.get("generated_at", "")).strip()
                job_id = str(event.get("job_id", "")).strip()
                source = str(event.get("source", "")).strip()
                reason = str(event.get("reason", "")).strip()
                dedupe_key = (generated_at, job_id, source, normalized_decision, reason)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                decision_counter[normalized_decision] += 1
                if provider_hint:
                    provider_counter[provider_hint] += 1
                recent_events.append(
                    {
                        "generated_at": generated_at,
                        "job_id": job_id,
                        "detail_url": f"/jobs/{job_id}" if job_id else "",
                        "source": source,
                        "decision": decision,
                        "recovery_status": recovery_status,
                        "failure_class": failure_class,
                        "provider_hint": provider_hint,
                        "stage_family": str(event.get("stage_family", "")).strip(),
                        "reason_code": str(event.get("reason_code", "")).strip(),
                        "reason": reason,
                    }
                )

        recent_events.sort(key=lambda item: str(item.get("generated_at", "")), reverse=True)
        return {
            "event_counts": self.top_counter_items(decision_counter, limit=8),
            "provider_counts": self.top_counter_items(provider_counter, limit=8),
            "recent_events": recent_events[:10],
        }

    def build_admin_startup_sweep_history(self, startup_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return recent worker startup sweep events with compact mismatch summaries."""

        history: List[Dict[str, Any]] = []
        for event in reversed(startup_events[-10:]):
            if not isinstance(event, dict):
                continue
            details = event.get("details", {}) if isinstance(event.get("details"), dict) else {}
            mismatch_before = (
                details.get("mismatch_audit_before", {})
                if isinstance(details.get("mismatch_audit_before"), dict)
                else {}
            )
            mismatch_after = (
                details.get("mismatch_audit_after", {})
                if isinstance(details.get("mismatch_audit_after"), dict)
                else {}
            )
            before_counter: Counter[str] = Counter()
            after_counter: Counter[str] = Counter()
            for name, count in (
                mismatch_before.get("counts", {})
                if isinstance(mismatch_before.get("counts"), dict)
                else {}
            ).items():
                before_counter[str(name).strip()] += int(count or 0)
            for name, count in (
                mismatch_after.get("counts", {})
                if isinstance(mismatch_after.get("counts"), dict)
                else {}
            ).items():
                after_counter[str(name).strip()] += int(count or 0)
            history.append(
                {
                    "generated_at": str(event.get("generated_at", "")).strip(),
                    "orphan_running_node_runs_interrupted": int(
                        event.get("orphan_running_node_runs_interrupted", 0) or 0
                    ),
                    "stale_running_jobs_recovered": int(event.get("stale_running_jobs_recovered", 0) or 0),
                    "orphan_queued_jobs_recovered": int(event.get("orphan_queued_jobs_recovered", 0) or 0),
                    "running_node_job_mismatches_detected": int(
                        event.get("running_node_job_mismatches_detected", 0) or 0
                    ),
                    "running_node_job_mismatches_remaining": int(
                        event.get("running_node_job_mismatches_remaining", 0) or 0
                    ),
                    "queue_size_before": int(event.get("queue_size_before", 0) or 0),
                    "queue_size_after": int(event.get("queue_size_after", 0) or 0),
                    "mismatch_counts_before": self.top_counter_items(before_counter, limit=4),
                    "mismatch_counts_after": self.top_counter_items(after_counter, limit=4),
                }
            )
        return history

    @staticmethod
    def classify_recovery_action_group(event: Dict[str, Any]) -> str:
        """Collapse detailed recovery decisions into operator-facing action groups."""

        decision = str(event.get("decision", "")).strip()
        recovery_status = str(event.get("recovery_status", "")).strip()
        normalized = decision or recovery_status or "recorded"
        if normalized in {"dead_letter"}:
            return "dead_letter"
        if normalized in {"retry_from_dead_letter", "requeue"} or recovery_status in {
            "dead_letter_requeued",
            "manual_rerun_queued",
            "manual_resume_queued",
            "auto_recovered",
        }:
            return "requeue"
        if normalized in {"provider_quarantined", "provider_circuit_open"}:
            return "provider_outage"
        if normalized in {"cooldown_wait"}:
            return "cooldown"
        if normalized in {"needs_human"} or recovery_status == "needs_human":
            return "human_handoff"
        return "other"

    def build_admin_recovery_action_groups(self, workspace_paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return grouped recovery action counts for operator overview."""

        action_counter: Counter[str] = Counter()
        source_counter: Counter[str] = Counter()
        recent_actions: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()

        for workspace in workspace_paths.values():
            trace_payload = self.read_dashboard_json(
                build_workflow_artifact_paths(workspace)["runtime_recovery_trace"]
            )
            events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                generated_at = str(event.get("generated_at", "")).strip()
                job_id = str(event.get("job_id", "")).strip()
                source = str(event.get("source", "")).strip()
                reason = str(event.get("reason", "")).strip()
                action_group = self.classify_recovery_action_group(event)
                action_counter[action_group] += 1
                if source:
                    source_counter[source] += 1
                dedupe_key = (generated_at, job_id, source, action_group, reason)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                recent_actions.append(
                    {
                        "generated_at": generated_at,
                        "job_id": job_id,
                        "detail_url": f"/jobs/{job_id}" if job_id else "",
                        "source": source,
                        "action_group": action_group,
                        "decision": str(event.get("decision", "")).strip(),
                        "recovery_status": str(event.get("recovery_status", "")).strip(),
                        "provider_hint": str(event.get("provider_hint", "")).strip(),
                        "stage_family": str(event.get("stage_family", "")).strip(),
                        "reason": reason,
                    }
                )

        recent_actions.sort(key=lambda item: str(item.get("generated_at", "")), reverse=True)
        return {
            "action_counts": self.top_counter_items(action_counter, limit=8),
            "source_counts": self.top_counter_items(source_counter, limit=8),
            "recent_actions": recent_actions[:10],
        }

    def build_admin_operator_action_trail(self, workspace_paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return recent operator-triggered recovery actions and notes."""

        source_counter: Counter[str] = Counter()
        decision_counter: Counter[str] = Counter()
        recent_events: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()

        for workspace in workspace_paths.values():
            trace_payload = self.read_dashboard_json(
                build_workflow_artifact_paths(workspace)["runtime_recovery_trace"]
            )
            events = trace_payload.get("events", []) if isinstance(trace_payload.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                details = event.get("details", {}) if isinstance(event.get("details"), dict) else {}
                operator_note = str(details.get("operator_note", "")).strip()
                source = str(event.get("source", "")).strip()
                if not operator_note and not source.startswith("dashboard_"):
                    continue
                generated_at = str(event.get("generated_at", "")).strip()
                job_id = str(event.get("job_id", "")).strip()
                decision = str(event.get("decision", "")).strip() or str(event.get("recovery_status", "")).strip() or "recorded"
                source_counter[source or "runtime"] += 1
                decision_counter[decision] += 1
                dedupe_key = (generated_at, job_id, source, decision)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                recent_events.append(
                    {
                        "generated_at": generated_at,
                        "job_id": job_id,
                        "detail_url": f"/jobs/{job_id}" if job_id else "",
                        "source": source,
                        "decision": str(event.get("decision", "")).strip(),
                        "recovery_status": str(event.get("recovery_status", "")).strip(),
                        "provider_hint": str(event.get("provider_hint", "")).strip(),
                        "stage_family": str(event.get("stage_family", "")).strip(),
                        "operator_note": operator_note,
                        "previous_recovery_status": str(details.get("previous_recovery_status", "")).strip(),
                        "reason": str(event.get("reason", "")).strip(),
                    }
                )

        recent_events.sort(key=lambda item: str(item.get("generated_at", "")), reverse=True)
        return {
            "source_counts": self.top_counter_items(source_counter, limit=8),
            "decision_counts": self.top_counter_items(decision_counter, limit=8),
            "recent_events": recent_events[:10],
        }

    def build_admin_metrics(self) -> Dict[str, Any]:
        """Aggregate read-only admin metrics from jobs and workspace artifacts."""

        feature_flags = read_feature_flags(self.feature_flags_config_path)
        jobs = self.list_dashboard_jobs(self.store, self.settings)
        summary = self.build_job_summary(jobs)

        default_workflow_id = self.read_default_workflow_id(self.workflows_config_path)
        apps = self.read_registered_apps(
            self.apps_config_path,
            self.settings.allowed_repository,
            default_workflow_id=default_workflow_id,
        )
        workflows_payload = load_workflows(self.workflows_config_path)
        workflows = workflows_payload.get("workflows", []) if isinstance(workflows_payload, dict) else []
        roles_payload = self.read_roles_payload(self.roles_config_path)
        roles = roles_payload.get("roles", []) if isinstance(roles_payload, dict) else []
        presets = roles_payload.get("presets", []) if isinstance(roles_payload, dict) else []

        review_overalls: List[float] = []
        maturity_scores: List[float] = []
        trend_counter: Counter[str] = Counter()
        maturity_counter: Counter[str] = Counter()
        strategy_counter: Counter[str] = Counter()
        recovery_counter: Counter[str] = Counter()
        resume_counter: Counter[str] = Counter()
        shadow_strategy_counter: Counter[str] = Counter()
        shadow_decision_counter: Counter[str] = Counter()
        stage_counter: Counter[str] = Counter()
        app_counter: Counter[str] = Counter()
        track_counter: Counter[str] = Counter()
        workflow_counter: Counter[str] = Counter()
        low_category_counter: Counter[str] = Counter()
        gate_pass_count = 0
        reviewed_job_count = 0
        shadow_divergence_count = 0
        adaptive_workflow_id = "adaptive_quality_loop_v1"
        workflow_daily_counter: Dict[str, Counter[str]] = {}
        timeline_anchor: Optional[date] = None

        for job in jobs:
            runtime = job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}
            review_overall = runtime.get("review_overall")
            if isinstance(review_overall, (int, float)):
                review_overalls.append(float(review_overall))
                reviewed_job_count += 1
            maturity_score = runtime.get("maturity_score")
            if isinstance(maturity_score, (int, float)):
                maturity_scores.append(float(maturity_score))
            if runtime.get("quality_gate_passed") is True:
                gate_pass_count += 1
            trend = str(runtime.get("quality_trend_direction", "")).strip()
            if trend:
                trend_counter[trend] += 1
            maturity = str(runtime.get("maturity_level", "")).strip()
            if maturity:
                maturity_counter[maturity] += 1
            strategy = str(runtime.get("strategy", "")).strip()
            if strategy:
                strategy_counter[strategy] += 1
            stage = str(job.get("stage", "")).strip()
            if stage:
                stage_counter[stage] += 1
            app_code = str(job.get("app_code", "")).strip()
            if app_code:
                app_counter[app_code] += 1
            track = str(job.get("track", "")).strip()
            if track:
                track_counter[track] += 1
            workflow_id = str(job.get("workflow_id", "")).strip()
            if workflow_id:
                workflow_counter[workflow_id] += 1
            created_at_raw = str(job.get("created_at", "")).strip()
            if created_at_raw:
                try:
                    created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                    created_day = created_at.date()
                    timeline_anchor = created_day if timeline_anchor is None or created_day > timeline_anchor else timeline_anchor
                    day_counter = workflow_daily_counter.setdefault(created_day.isoformat(), Counter())
                    day_counter[workflow_id or "unspecified"] += 1
                except ValueError:
                    pass
            recovery = str(job.get("recovery_status", "")).strip()
            if recovery:
                recovery_counter[recovery] += 1
            resume_mode = str(runtime.get("resume_mode", "")).strip()
            if resume_mode and resume_mode != "none":
                resume_counter[resume_mode] += 1
            shadow_strategy = str(runtime.get("shadow_strategy", "")).strip()
            if shadow_strategy:
                shadow_strategy_counter[shadow_strategy] += 1
            shadow_decision_mode = str(runtime.get("shadow_decision_mode", "")).strip()
            if shadow_decision_mode:
                shadow_decision_counter[shadow_decision_mode] += 1
            if bool(runtime.get("shadow_diverged")):
                shadow_divergence_count += 1
            for category in runtime.get("quality_gate_categories", []) or []:
                normalized = str(category).strip()
                if normalized:
                    low_category_counter[normalized] += 1
            for category in runtime.get("persistent_low_categories", []) or []:
                normalized = str(category).strip()
                if normalized:
                    low_category_counter[normalized] += 1

        workspace_paths: Dict[str, Path] = {}
        for job in self.store.list_jobs():
            workspace = self.job_workspace_path(job, self.settings)
            workspace_paths[str(workspace)] = workspace

        memory_totals = {
            "workspace_count": 0,
            "workspaces_with_memory": 0,
            "workspaces_with_retrieval": 0,
            "workspaces_with_scoring": 0,
            "episodic_entries": 0,
            "decision_entries": 0,
            "failure_patterns": 0,
            "conventions": 0,
            "feedback_entries": 0,
            "workspaces_with_strategy_shadow": 0,
        }
        ranking_state_counter: Counter[str] = Counter()
        retrieval_generated_ats: List[str] = []
        scoring_generated_ats: List[str] = []
        shadow_generated_ats: List[str] = []
        provider_failure_counter: Counter[str] = Counter()
        provider_failure_workspaces = 0
        provider_failure_generated_ats: List[str] = []
        runtime_store = self.get_memory_runtime_store(self.settings)
        backlog_candidates = runtime_store.list_backlog_candidates(limit=200)
        backlog_state_counter: Counter[str] = Counter()
        for item in backlog_candidates:
            backlog_state = str(item.get("state", "")).strip() or "candidate"
            backlog_state_counter[backlog_state] += 1

        for workspace in workspace_paths.values():
            docs_dir = workspace / "_docs"
            memory_totals["workspace_count"] += 1
            memory_log_entries = self.read_dashboard_jsonl(docs_dir / "MEMORY_LOG.jsonl")
            decision_entries = self.read_dashboard_json(docs_dir / "DECISION_HISTORY.json").get("entries", [])
            failure_items = self.read_dashboard_json(docs_dir / "FAILURE_PATTERNS.json").get("items", [])
            convention_items = self.read_dashboard_json(docs_dir / "CONVENTIONS.json").get("rules", [])
            feedback_entries = self.read_dashboard_json(docs_dir / "MEMORY_FEEDBACK.json").get("entries", [])
            ranking_items = self.read_dashboard_json(docs_dir / "MEMORY_RANKINGS.json").get("items", [])
            memory_selection_payload = self.read_dashboard_json(docs_dir / "MEMORY_SELECTION.json")
            memory_context_payload = self.read_dashboard_json(docs_dir / "MEMORY_CONTEXT.json")
            memory_feedback_payload = self.read_dashboard_json(docs_dir / "MEMORY_FEEDBACK.json")
            memory_rankings_payload = self.read_dashboard_json(docs_dir / "MEMORY_RANKINGS.json")
            strategy_shadow_payload = self.read_dashboard_json(docs_dir / "STRATEGY_SHADOW_REPORT.json")
            provider_failure_payload = read_provider_failure_counters(workspace)

            if any(
                [
                    memory_log_entries,
                    isinstance(decision_entries, list) and len(decision_entries) > 0,
                    isinstance(failure_items, list) and len(failure_items) > 0,
                    isinstance(convention_items, list) and len(convention_items) > 0,
                    isinstance(feedback_entries, list) and len(feedback_entries) > 0,
                    isinstance(ranking_items, list) and len(ranking_items) > 0,
                ]
            ):
                memory_totals["workspaces_with_memory"] += 1
            if memory_selection_payload or memory_context_payload:
                memory_totals["workspaces_with_retrieval"] += 1
                retrieval_generated_ats.extend(
                    [
                        str(memory_selection_payload.get("generated_at", "")).strip(),
                        str(memory_context_payload.get("generated_at", "")).strip(),
                    ]
                )
            if memory_feedback_payload or memory_rankings_payload:
                memory_totals["workspaces_with_scoring"] += 1
                scoring_generated_ats.extend(
                    [
                        str(memory_feedback_payload.get("generated_at", "")).strip(),
                        str(memory_rankings_payload.get("generated_at", "")).strip(),
                    ]
                )
            if strategy_shadow_payload:
                memory_totals["workspaces_with_strategy_shadow"] += 1
                shadow_generated_ats.append(str(strategy_shadow_payload.get("generated_at", "")).strip())
            provider_entries = (
                provider_failure_payload.get("providers", {})
                if isinstance(provider_failure_payload.get("providers"), dict)
                else {}
            )
            if provider_entries:
                provider_failure_workspaces += 1
                provider_failure_generated_ats.append(str(provider_failure_payload.get("latest_updated_at", "")).strip())
                for provider_name, item in provider_entries.items():
                    if not isinstance(item, dict):
                        continue
                    provider_failure_counter[str(provider_name).strip()] += int(item.get("recent_failure_count", 0) or 0)

            memory_totals["episodic_entries"] += len(memory_log_entries)
            memory_totals["decision_entries"] += len(decision_entries) if isinstance(decision_entries, list) else 0
            memory_totals["failure_patterns"] += len(failure_items) if isinstance(failure_items, list) else 0
            memory_totals["conventions"] += len(convention_items) if isinstance(convention_items, list) else 0
            memory_totals["feedback_entries"] += len(feedback_entries) if isinstance(feedback_entries, list) else 0
            if isinstance(ranking_items, list):
                for item in ranking_items:
                    if not isinstance(item, dict):
                        continue
                    ranking_state = str(item.get("state", "")).strip() or "active"
                    ranking_state_counter[ranking_state] += 1

        startup_sweep_payload = read_worker_startup_sweep_trace(self.settings)
        startup_events = (
            startup_sweep_payload.get("events", [])
            if isinstance(startup_sweep_payload.get("events"), list)
            else []
        )
        latest_startup_sweep = startup_events[-1] if startup_events else {}
        latest_startup_details = (
            latest_startup_sweep.get("details", {})
            if isinstance(latest_startup_sweep, dict) and isinstance(latest_startup_sweep.get("details"), dict)
            else {}
        )
        startup_mismatch_before = (
            latest_startup_details.get("mismatch_audit_before", {})
            if isinstance(latest_startup_details.get("mismatch_audit_before"), dict)
            else {}
        )
        startup_mismatch_after = (
            latest_startup_details.get("mismatch_audit_after", {})
            if isinstance(latest_startup_details.get("mismatch_audit_after"), dict)
            else {}
        )
        startup_mismatch_before_counter: Counter[str] = Counter()
        startup_mismatch_after_counter: Counter[str] = Counter()
        for name, count in (
            startup_mismatch_before.get("counts", {})
            if isinstance(startup_mismatch_before.get("counts"), dict)
            else {}
        ).items():
            startup_mismatch_before_counter[str(name).strip()] += int(count or 0)
        for name, count in (
            startup_mismatch_after.get("counts", {})
            if isinstance(startup_mismatch_after.get("counts"), dict)
            else {}
        ).items():
            startup_mismatch_after_counter[str(name).strip()] += int(count or 0)

        unique_execution_repositories = sorted(
            {
                str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("execution_repository", "")).strip()
                for job in jobs
                if str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("execution_repository", "")).strip()
            }
        )
        app_workflow_counter: Counter[str] = Counter()
        apps_using_adaptive_workflow = 0
        apps_using_default_workflow = 0
        for app_entry in apps:
            if not isinstance(app_entry, dict):
                continue
            resolved_workflow_id = str(app_entry.get("workflow_id") or default_workflow_id or "").strip()
            if not resolved_workflow_id:
                continue
            app_workflow_counter[resolved_workflow_id] += 1
            if resolved_workflow_id == adaptive_workflow_id:
                apps_using_adaptive_workflow += 1
            if resolved_workflow_id == default_workflow_id:
                apps_using_default_workflow += 1
        if timeline_anchor is None:
            timeline_anchor = datetime.fromisoformat(self.utc_now_iso().replace("Z", "+00:00")).date()
        workflow_timeline: List[Dict[str, Any]] = []
        for offset in range(6, -1, -1):
            bucket_day = timeline_anchor - timedelta(days=offset)
            bucket_key = bucket_day.isoformat()
            bucket_counter = workflow_daily_counter.get(bucket_key, Counter())
            default_count = bucket_counter.get(default_workflow_id, 0) if default_workflow_id else 0
            adaptive_count = bucket_counter.get(adaptive_workflow_id, 0)
            total_count = sum(bucket_counter.values())
            workflow_timeline.append(
                {
                    "day": bucket_key,
                    "default_count": default_count,
                    "adaptive_count": adaptive_count,
                    "other_count": max(0, total_count - default_count - adaptive_count),
                    "total_count": total_count,
                }
            )
        supported_node_types = schema_payload().get("node_types", {})
        retrieval_enabled = bool(feature_flags.get("memory_retrieval"))
        scoring_enabled = bool(feature_flags.get("memory_scoring"))
        shadow_enabled = bool(feature_flags.get("strategy_shadow"))
        assistant_diagnosis = self.build_admin_assistant_diagnosis_metrics()
        dead_letter_jobs = self.build_admin_dead_letter_jobs(jobs)
        dead_letter_summary = self.build_admin_dead_letter_summary(dead_letter_jobs)
        recovery_history = self.build_admin_recovery_history(workspace_paths)
        provider_outage_history = self.build_admin_provider_outage_history(workspace_paths)
        startup_sweep_history = self.build_admin_startup_sweep_history(startup_events)
        recovery_action_groups = self.build_admin_recovery_action_groups(workspace_paths)
        operator_action_trail = self.build_admin_operator_action_trail(workspace_paths)
        app_runner_status = self.build_admin_app_runner_status()
        assistant_diagnosis_loop_enabled = bool(feature_flags.get("assistant_diagnosis_loop"))
        mcp_tools_shadow_enabled = bool(feature_flags.get("mcp_tools_shadow"))
        vector_memory_shadow_enabled = bool(feature_flags.get("vector_memory_shadow"))
        vector_memory_retrieval_enabled = bool(feature_flags.get("vector_memory_retrieval"))
        langgraph_planner_shadow_enabled = bool(feature_flags.get("langgraph_planner_shadow"))
        langgraph_recovery_shadow_enabled = bool(feature_flags.get("langgraph_recovery_shadow"))
        runtime_input_records = self.store.list_runtime_inputs()
        runtime_input_requested_count = sum(1 for item in runtime_input_records if str(item.status or "").strip() == "requested")
        runtime_input_provided_count = sum(1 for item in runtime_input_records if str(item.status or "").strip() == "provided")
        capabilities = [
            {
                "id": "workflow_control_nodes",
                "label": "Workflow Control Nodes",
                "enabled": "if_label_match" in supported_node_types and "loop_until_pass" in supported_node_types,
                "detail": "조건 분기와 루프 노드를 실행 엔진이 지원합니다.",
            },
            {
                "id": "memory_logging",
                "label": "Structured Memory Logging",
                "enabled": bool(feature_flags.get("memory_logging")),
                "detail": f"completed workspace의 memory log / decision / failure pattern을 기록합니다. active workspace {memory_totals['workspaces_with_memory']}",
            },
            {
                "id": "memory_retrieval",
                "label": "Controlled Retrieval",
                "enabled": retrieval_enabled,
                "detail": f"planner/reviewer/coder prompt에 read-only memory context를 주입합니다. active workspace {memory_totals['workspaces_with_retrieval']}",
            },
            {
                "id": "convention_extraction",
                "label": "Convention Extraction",
                "enabled": bool(feature_flags.get("convention_extraction")),
                "detail": f"manifest/dir/test pattern 기반 convention 규칙을 추출합니다. rule count {memory_totals['conventions']}",
            },
            {
                "id": "memory_scoring",
                "label": "Memory Quality Scoring",
                "enabled": scoring_enabled,
                "detail": f"memory feedback/ranking으로 promote/decay/banned 상태를 집계합니다. active workspace {memory_totals['workspaces_with_scoring']}",
            },
            {
                "id": "strategy_shadow",
                "label": "Adaptive Strategy Shadow",
                "enabled": shadow_enabled,
                "detail": f"실제 전략은 유지한 채 memory-aware shadow strategy를 비교 기록합니다. active workspace {memory_totals['workspaces_with_strategy_shadow']}",
            },
            {
                "id": "assistant_diagnosis_loop",
                "label": "Assistant Diagnosis Loop",
                "enabled": assistant_diagnosis_loop_enabled,
                "detail": "assistant log-analysis 전에 log_lookup/repo_search/memory_search를 순차 호출해 진단 trace를 기록합니다.",
            },
            {
                "id": "mcp_tools_shadow",
                "label": "MCP Tool Shadow",
                "enabled": mcp_tools_shadow_enabled,
                "detail": "기존 tool 실행 결과는 유지한 채 MCP shadow client를 병행 호출해 trace만 기록합니다.",
            },
            {
                "id": "vector_memory_shadow",
                "label": "Vector Memory Shadow",
                "enabled": vector_memory_shadow_enabled,
                "detail": "SQLite memory DB는 그대로 유지한 채 Qdrant용 vector candidate payload를 shadow artifact로만 기록합니다.",
            },
            {
                "id": "vector_memory_retrieval",
                "label": "Vector Memory Retrieval",
                "enabled": vector_memory_retrieval_enabled,
                "detail": "memory_search 한정으로 vector retrieval을 opt-in 실험하고, 실패 시 SQLite 검색으로 자동 fallback 합니다.",
            },
            {
                "id": "langgraph_planner_shadow",
                "label": "LangGraph Planner Shadow",
                "enabled": langgraph_planner_shadow_enabled,
                "detail": "planner primary loop는 유지한 채 LangGraph subgraph shadow trace를 `_docs/LANGGRAPH_PLANNER_SHADOW.json`에 기록합니다.",
            },
            {
                "id": "langgraph_recovery_shadow",
                "label": "LangGraph Recovery Shadow",
                "enabled": langgraph_recovery_shadow_enabled,
                "detail": "recovery primary policy는 유지한 채 LangGraph subgraph shadow trace를 `_docs/LANGGRAPH_RECOVERY_SHADOW.json`에 기록합니다.",
            },
            {
                "id": "operator_runtime_inputs",
                "label": "Operator Runtime Inputs",
                "enabled": True,
                "detail": f"운영자가 나중에 API key/tenant id 같은 런타임 입력을 등록하고 제공할 수 있으며, 초안 추천 뒤 승인 등록도 가능합니다. requested {runtime_input_requested_count}, provided {runtime_input_provided_count}",
            },
        ]
        phase_status = [
            {"phase": "Phase 1", "status": "closed", "detail": "제품형 workflow/runtime/review/recovery 기반 완료"},
            {"phase": "Phase 2-A", "status": "implemented", "detail": "workflow result context + interrupted cleanup + read-first ops"},
            {"phase": "Phase 2-B", "status": "implemented", "detail": "structured memory write path"},
            {"phase": "Phase 2-C", "status": "implemented", "detail": "controlled retrieval prompt injection"},
            {"phase": "Phase 2-D", "status": "implemented", "detail": "repo convention extraction v1"},
            {"phase": "Phase 2-E", "status": "implemented", "detail": "memory feedback/rankings + banned-memory avoidance"},
            {"phase": "Phase 2-F", "status": "implemented", "detail": "adaptive strategy shadow report"},
        ]

        return {
            "generated_at": self.utc_now_iso(),
            "system": {
                "apps_count": len(apps),
                "workflows_count": len(workflows) if isinstance(workflows, list) else 0,
                "roles_count": len(roles) if isinstance(roles, list) else 0,
                "role_presets_count": len(presets) if isinstance(presets, list) else 0,
                "jobs_total": summary["total"],
                "jobs_running": summary["running"],
                "jobs_failed": summary["failed"],
                "workspaces_count": memory_totals["workspace_count"],
                "execution_repositories_count": len(unique_execution_repositories),
                "execution_repositories": unique_execution_repositories[:8],
                "default_workflow_id": default_workflow_id,
                "adaptive_workflow_id": adaptive_workflow_id,
                "apps_using_default_workflow": apps_using_default_workflow,
                "apps_using_adaptive_workflow": apps_using_adaptive_workflow,
            },
            "runtime": {
                "job_summary": summary,
                "reviewed_jobs_count": reviewed_job_count,
                "quality_gate_pass_rate": round(gate_pass_count / reviewed_job_count, 3) if reviewed_job_count else None,
                "strategy_counts": self.top_counter_items(strategy_counter, limit=8),
                "stage_counts": self.top_counter_items(stage_counter, limit=8),
                "app_counts": self.top_counter_items(app_counter, limit=8),
                "track_counts": self.top_counter_items(track_counter, limit=8),
                "workflow_counts": self.top_counter_items(workflow_counter, limit=8),
                "recovery_counts": self.top_counter_items(recovery_counter, limit=8),
                "resume_mode_counts": self.top_counter_items(resume_counter, limit=8),
                "shadow_strategy_counts": self.top_counter_items(shadow_strategy_counter, limit=8),
                "shadow_decision_counts": self.top_counter_items(shadow_decision_counter, limit=8),
                "shadow_divergence_count": shadow_divergence_count,
                "adaptive_job_count": workflow_counter.get(adaptive_workflow_id, 0),
                "default_job_count": workflow_counter.get(default_workflow_id, 0) if default_workflow_id else 0,
                "provider_failure_counts": self.top_counter_items(provider_failure_counter, limit=8),
                "provider_failure_workspaces": provider_failure_workspaces,
                "provider_failure_latest_at": self.latest_non_empty(provider_failure_generated_ats),
                "dead_letter_jobs": dead_letter_jobs,
                "dead_letter_summary": dead_letter_summary,
                "recovery_history": recovery_history,
                "provider_outage_history": provider_outage_history,
                "recovery_action_groups": recovery_action_groups,
                "operator_action_trail": operator_action_trail,
                "app_runner_status": app_runner_status,
                "startup_sweep": {
                    "latest_generated_at": str(latest_startup_sweep.get("generated_at", "")).strip(),
                    "orphan_running_node_runs_interrupted": int(
                        latest_startup_sweep.get("orphan_running_node_runs_interrupted", 0) or 0
                    ),
                    "stale_running_jobs_recovered": int(
                        latest_startup_sweep.get("stale_running_jobs_recovered", 0) or 0
                    ),
                    "orphan_queued_jobs_recovered": int(
                        latest_startup_sweep.get("orphan_queued_jobs_recovered", 0) or 0
                    ),
                    "running_node_job_mismatches_detected": int(
                        latest_startup_sweep.get("running_node_job_mismatches_detected", 0) or 0
                    ),
                    "running_node_job_mismatches_remaining": int(
                        latest_startup_sweep.get("running_node_job_mismatches_remaining", 0) or 0
                    ),
                    "mismatch_counts_before": self.top_counter_items(startup_mismatch_before_counter, limit=8),
                    "mismatch_counts_after": self.top_counter_items(startup_mismatch_after_counter, limit=8),
                },
                "startup_sweep_history": startup_sweep_history,
            },
            "quality": {
                "average_review_overall": self.safe_average(review_overalls),
                "average_maturity_score": self.safe_average(maturity_scores),
                "trend_direction_counts": self.top_counter_items(trend_counter, limit=8),
                "maturity_level_counts": self.top_counter_items(maturity_counter, limit=8),
                "low_category_counts": self.top_counter_items(low_category_counter, limit=8),
            },
            "workflow_adoption": {
                "default_workflow_id": default_workflow_id,
                "adaptive_workflow_id": adaptive_workflow_id,
                "app_workflow_counts": self.top_counter_items(app_workflow_counter, limit=8),
                "apps_using_default_workflow": apps_using_default_workflow,
                "apps_using_adaptive_workflow": apps_using_adaptive_workflow,
                "adaptive_app_rate": round(apps_using_adaptive_workflow / len(apps), 3) if apps else None,
                "timeline": workflow_timeline,
            },
            "memory": {
                **memory_totals,
                "backlog_candidates": len(backlog_candidates),
                "ranking_state_counts": self.top_counter_items(ranking_state_counter, limit=8),
                "backlog_state_counts": self.top_counter_items(backlog_state_counter, limit=8),
            },
            "runtime_inputs": {
                "total": len(runtime_input_records),
                "requested": runtime_input_requested_count,
                "provided": runtime_input_provided_count,
                "latest_updated_at": self.latest_non_empty(
                    [item.updated_at or item.provided_at or item.requested_at for item in runtime_input_records]
                ),
            },
            "feature_flags": feature_flags,
            "capabilities": capabilities,
            "phase_status": phase_status,
            "retrieval": {
                "enabled": retrieval_enabled,
                "latest_generated_at": self.latest_non_empty(retrieval_generated_ats),
                "workspaces_with_retrieval": memory_totals["workspaces_with_retrieval"],
                "active": memory_totals["workspaces_with_retrieval"] > 0,
            },
            "scoring": {
                "enabled": scoring_enabled,
                "latest_generated_at": self.latest_non_empty(scoring_generated_ats),
                "workspaces_with_scoring": memory_totals["workspaces_with_scoring"],
                "active": memory_totals["workspaces_with_scoring"] > 0,
            },
            "shadow": {
                "enabled": shadow_enabled,
                "latest_generated_at": self.latest_non_empty(shadow_generated_ats),
                "workspaces_with_strategy_shadow": memory_totals["workspaces_with_strategy_shadow"],
                "divergence_count": shadow_divergence_count,
                "active": memory_totals["workspaces_with_strategy_shadow"] > 0,
            },
            "assistant_diagnosis": assistant_diagnosis,
        }
