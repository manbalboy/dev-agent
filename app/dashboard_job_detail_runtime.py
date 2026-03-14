"""Job-detail read helper runtime for dashboard routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from fastapi import HTTPException

from app.models import JobRecord
from app.store import JobStore


class DashboardJobDetailRuntime:
    """Encapsulate job detail and node-run read payload assembly."""

    def __init__(
        self,
        *,
        store: JobStore,
        resolve_debug_log_path: Callable[[JobRecord], Path],
        parse_log_events: Callable[[Path], List[Dict[str, str]]],
        job_workspace_path: Callable[[JobRecord], Path],
        read_agent_md_files: Callable[[Path], List[Dict[str, str]]],
        read_stage_md_snapshots: Callable[[str], List[Dict[str, Any]]],
        resolve_job_workflow_runtime: Callable[[JobRecord], Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]],
        extract_workflow_fallback_events: Callable[[List[Dict[str, str]]], List[Dict[str, Any]]],
        compute_job_resume_state: Callable[[JobRecord, List[Any]], Dict[str, Any]],
        build_job_runtime_signals: Callable[[JobRecord], Dict[str, Any]],
        read_job_memory_trace: Callable[[JobRecord], Dict[str, Any]],
        read_job_assistant_diagnosis_trace: Callable[[JobRecord], Dict[str, Any]],
        read_job_runtime_recovery_trace: Callable[[JobRecord], Dict[str, Any]],
        build_failure_classification_summary: Callable[[JobRecord, Dict[str, Any]], Dict[str, Any]],
        build_job_needs_human_summary: Callable[[JobRecord, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        build_job_dead_letter_summary: Callable[[JobRecord, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        build_job_dead_letter_action_trail: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
        build_job_requeue_reason_summary: Callable[[JobRecord, Dict[str, Any]], Dict[str, Any]],
        build_job_self_growing_effectiveness: Callable[[JobRecord], Dict[str, Any]],
        build_job_mobile_e2e_result: Callable[[JobRecord], Dict[str, Any]],
        build_manual_retry_options: Callable[[JobRecord, List[Any]], Dict[str, Any]],
        build_job_lineage: Callable[[JobRecord], Dict[str, Any]],
        build_job_log_summary: Callable[[JobRecord, List[Dict[str, str]]], Dict[str, Any]],
        build_job_operator_inputs: Callable[[JobRecord], Dict[str, Any]],
        build_job_integration_operator_boundary: Callable[[JobRecord], Dict[str, Any]],
        build_job_integration_usage_trail: Callable[[JobRecord], Dict[str, Any]],
        build_job_integration_health_facets: Callable[
            [JobRecord, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]],
            Dict[str, Any],
        ],
        stop_signal_exists: Callable[[str], bool],
    ) -> None:
        self.store = store
        self.resolve_debug_log_path = resolve_debug_log_path
        self.parse_log_events = parse_log_events
        self.job_workspace_path = job_workspace_path
        self.read_agent_md_files = read_agent_md_files
        self.read_stage_md_snapshots = read_stage_md_snapshots
        self.resolve_job_workflow_runtime = resolve_job_workflow_runtime
        self.extract_workflow_fallback_events = extract_workflow_fallback_events
        self.compute_job_resume_state = compute_job_resume_state
        self.build_job_runtime_signals = build_job_runtime_signals
        self.read_job_memory_trace = read_job_memory_trace
        self.read_job_assistant_diagnosis_trace = read_job_assistant_diagnosis_trace
        self.read_job_runtime_recovery_trace = read_job_runtime_recovery_trace
        self.build_failure_classification_summary = build_failure_classification_summary
        self.build_job_needs_human_summary = build_job_needs_human_summary
        self.build_job_dead_letter_summary = build_job_dead_letter_summary
        self.build_job_dead_letter_action_trail = build_job_dead_letter_action_trail
        self.build_job_requeue_reason_summary = build_job_requeue_reason_summary
        self.build_job_self_growing_effectiveness = build_job_self_growing_effectiveness
        self.build_job_mobile_e2e_result = build_job_mobile_e2e_result
        self.build_manual_retry_options = build_manual_retry_options
        self.build_job_lineage = build_job_lineage
        self.build_job_log_summary = build_job_log_summary
        self.build_job_operator_inputs = build_job_operator_inputs
        self.build_job_integration_operator_boundary = build_job_integration_operator_boundary
        self.build_job_integration_usage_trail = build_job_integration_usage_trail
        self.build_job_integration_health_facets = build_job_integration_health_facets
        self.stop_signal_exists = stop_signal_exists

    def get_job_detail_payload(self, job_id: str) -> Dict[str, Any]:
        """Return job detail payload with logs, artifacts, and operator facets."""

        job = self._get_job_or_raise(job_id)
        log_path = self.resolve_debug_log_path(job)
        events = self.parse_log_events(log_path) if log_path.exists() else []
        workspace_path = self.job_workspace_path(job)
        md_files = self.read_agent_md_files(workspace_path)
        stage_md_snapshots = self.read_stage_md_snapshots(job_id)
        node_runs = self.store.list_node_runs(job_id)
        workflow_runtime, _, _ = self.resolve_job_workflow_runtime(job)
        workflow_runtime_payload = dict(workflow_runtime or {})
        workflow_runtime_payload["fallback_events"] = self.extract_workflow_fallback_events(events)
        if any(bool(item.get("uses_fixed_pipeline")) for item in workflow_runtime_payload["fallback_events"]):
            workflow_runtime_payload["uses_fixed_pipeline"] = True
        resume_state = self.compute_job_resume_state(job, node_runs)
        runtime_signals = self.build_job_runtime_signals(job)
        memory_trace = self.read_job_memory_trace(job)
        assistant_diagnosis_trace = self.read_job_assistant_diagnosis_trace(job)
        runtime_recovery_trace = self.read_job_runtime_recovery_trace(job)
        failure_classification = self.build_failure_classification_summary(job, runtime_recovery_trace)
        needs_human_summary = self.build_job_needs_human_summary(
            job,
            runtime_recovery_trace,
            failure_classification,
        )
        dead_letter_summary = self.build_job_dead_letter_summary(
            job,
            runtime_recovery_trace,
            failure_classification,
        )
        dead_letter_action_trail = self.build_job_dead_letter_action_trail(runtime_recovery_trace)
        requeue_reason_summary = self.build_job_requeue_reason_summary(job, runtime_recovery_trace)
        self_growing_effectiveness = self.build_job_self_growing_effectiveness(job)
        mobile_e2e_result = self.build_job_mobile_e2e_result(job)
        manual_retry_options = self.build_manual_retry_options(job, node_runs)
        job_lineage = self.build_job_lineage(job)
        log_summary = self.build_job_log_summary(job, events)
        operator_inputs = self.build_job_operator_inputs(job)
        integration_operator_boundary = self.build_job_integration_operator_boundary(job)
        integration_usage_trail = self.build_job_integration_usage_trail(job)
        integration_health_facets = self.build_job_integration_health_facets(
            job,
            integration_operator_boundary,
            integration_usage_trail,
            log_summary,
            failure_classification,
        )

        return {
            "job": job.to_dict(),
            "events": events,
            "md_files": md_files,
            "stage_md_snapshots": stage_md_snapshots,
            "node_runs": [item.to_dict() for item in node_runs],
            "workflow_runtime": workflow_runtime_payload,
            "resume_state": resume_state,
            "manual_retry_options": manual_retry_options,
            "runtime_signals": runtime_signals,
            "memory_trace": memory_trace,
            "assistant_diagnosis_trace": assistant_diagnosis_trace,
            "runtime_recovery_trace": runtime_recovery_trace,
            "failure_classification": failure_classification,
            "needs_human_summary": needs_human_summary,
            "dead_letter_summary": dead_letter_summary,
            "dead_letter_action_trail": dead_letter_action_trail,
            "requeue_reason_summary": requeue_reason_summary,
            "self_growing_effectiveness": self_growing_effectiveness,
            "mobile_e2e_result": mobile_e2e_result,
            "job_lineage": job_lineage,
            "log_summary": log_summary,
            "operator_inputs": operator_inputs,
            "integration_operator_boundary": integration_operator_boundary,
            "integration_usage_trail": integration_usage_trail,
            "integration_health_facets": integration_health_facets,
            "stop_requested": self.stop_signal_exists(job_id),
        }

    def get_job_node_runs_payload(self, job_id: str) -> Dict[str, Any]:
        """Return node-run payload with workflow runtime and manual retry metadata."""

        job = self._get_job_or_raise(job_id)
        node_runs = self.store.list_node_runs(job_id)
        workflow_runtime, _, _ = self.resolve_job_workflow_runtime(job)
        resume_state = self.compute_job_resume_state(job, node_runs)
        manual_retry_options = self.build_manual_retry_options(job, node_runs)
        return {
            "job_id": job_id,
            "workflow_id": job.workflow_id,
            "node_runs": [item.to_dict() for item in node_runs],
            "workflow_runtime": workflow_runtime,
            "resume_state": resume_state,
            "manual_retry_options": manual_retry_options,
        }

    def _get_job_or_raise(self, job_id: str) -> JobRecord:
        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        return job
