"""Dashboard routes for job visibility."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.agent_cli_runtime import ASSISTANT_PROVIDER_ALIASES, canonical_cli_name
from app.agent_config_runtime import (
    collect_agent_cli_status,
    collect_agent_model_status,
    load_agent_template_config,
    read_command_templates as _read_command_templates,
    update_agent_template_config,
)
import app.assistant_runtime as assistant_runtime
from app.assistant_runtime import (
    build_assistant_chat_prompt as _build_assistant_chat_prompt,
    build_log_analysis_prompt as _build_log_analysis_prompt,
)
from app.config import AppSettings
from app.dashboard_admin_metrics_runtime import DashboardAdminMetricsRuntime
from app.dashboard_job_runtime import DashboardJobRuntime
from app.dashboard_roles_runtime import DashboardRolesRuntime, normalize_role_code, read_roles_payload
from app.dashboard_runtime_input_runtime import DashboardRuntimeInputRuntime
from app.dependencies import get_settings, get_store
from app.failure_classification import (
    build_failure_classification_summary,
    build_failure_evidence_summary,
    classify_runtime_recovery_event,
)
from app.feature_flags import feature_flags_payload, read_feature_flags, write_feature_flags
from app.memory import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.runtime_recovery_trace import append_runtime_recovery_trace_for_job
from app.store import JobStore
from app.workflow_design import (
    default_workflow_template,
    load_workflows,
    save_workflows,
    schema_payload,
    validate_workflow,
)
from app.workflow_resume import (
    build_workflow_artifact_paths,
    compute_workflow_resume_state,
    linearize_workflow_nodes,
    list_manual_resume_candidates,
    read_improvement_runtime_context,
    validate_manual_resume_target,
)
from app.workflow_resolution import (
    list_known_workflow_ids,
    read_default_workflow_id as _shared_read_default_workflow_id,
    read_registered_apps as _shared_read_registered_apps,
    resolve_workflow_selection,
    write_registered_apps as _shared_write_registered_apps,
)
from app.tool_runtime import ToolRequest, ToolRuntime, ToolResult


router = APIRouter(tags=["dashboard"])
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_LOG_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMESTAMPED_LINE_PATTERN = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<msg>.*)$")
_ISSUE_URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/issues/\d+")
_ISSUE_NUMBER_PATTERN = re.compile(r"/issues/(?P<number>\d+)")
_APP_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_TRACK_CHOICES = {"new", "enhance", "bug", "long", "ultra", "ultra10"}
_APPS_CONFIG_PATH = Path.cwd() / "config" / "apps.json"
_WORKFLOWS_CONFIG_PATH = Path.cwd() / "config" / "workflows.json"
_ROLES_CONFIG_PATH = Path.cwd() / "config" / "roles.json"
_FEATURE_FLAGS_CONFIG_PATH = Path.cwd() / "config" / "feature_flags.json"
_DEFAULT_DASHBOARD_PAGE_SIZE = 20
_MAX_DASHBOARD_PAGE_SIZE = 100
_PRIMARY_ASSISTANT_PROVIDERS = {"codex", "gemini"}


class IssueRegistrationRequest(BaseModel):
    """Payload for manual issue creation from dashboard."""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    app_code: str = Field(default="default", min_length=1, max_length=32)
    track: str = Field(default="enhance", min_length=1, max_length=32)
    keep_branch: bool = Field(default=True)
    branch_name: str = Field(default="", max_length=200)
    role_preset_id: str = Field(default="", max_length=64)
    workflow_id: str = Field(default="", max_length=120)


class AppConfigRequest(BaseModel):
    """Payload for app registration/editing."""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    source_repository: str = Field(default="", max_length=200)
    workflow_id: str = Field(default="", max_length=120)


class AppWorkflowMappingRequest(BaseModel):
    """Payload for binding one app to one saved workflow."""

    workflow_id: str = Field(min_length=1, max_length=120)


class RoleConfigRequest(BaseModel):
    """Payload for one role definition."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    objective: str = Field(default="", max_length=400)
    cli: str = Field(default="", max_length=40)
    template_key: str = Field(default="", max_length=80)
    inputs: str = Field(default="", max_length=400)
    outputs: str = Field(default="", max_length=400)
    checklist: str = Field(default="", max_length=1000)
    skills: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    enabled: bool = Field(default=True)


class RolePresetRequest(BaseModel):
    """Payload for one role-combination preset."""

    preset_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    role_codes: List[str] = Field(default_factory=list)


class AgentTemplateConfigRequest(BaseModel):
    """Editable command templates for planner/coder/reviewer."""

    planner: str = Field(min_length=1, max_length=4000)
    coder: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=4000)
    copilot: str = Field(default="", max_length=4000)
    escalation: str = Field(default="", max_length=4000)
    enable_escalation: bool = Field(default=False)


class WorkflowValidateRequest(BaseModel):
    """Payload wrapper for workflow validation."""

    workflow: Dict[str, Any]


class WorkflowSaveRequest(BaseModel):
    """Payload for workflow save/update in phase-1."""

    workflow: Dict[str, Any]
    set_default: bool = Field(default=False)


class WorkflowDefaultRequest(BaseModel):
    """Payload for setting default workflow id."""

    workflow_id: str = Field(min_length=1, max_length=120)


class FeatureFlagsRequest(BaseModel):
    """Payload for adaptive feature flag updates."""

    flags: Dict[str, bool] = Field(default_factory=dict)


class AssistantChatRequest(BaseModel):
    """Payload for dashboard assistant chat."""

    assistant: str = Field(default="codex", min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=8000)
    history: List[Dict[str, str]] = Field(default_factory=list)
    job_id: str = Field(default="", max_length=128)


class AssistantLogAnalysisRequest(BaseModel):
    """Payload for one-shot log analysis by selected assistant."""

    assistant: str = Field(default="codex", min_length=1, max_length=20)
    question: str = Field(default="최근 로그의 핵심 문제점을 분석해줘", min_length=1, max_length=8000)
    job_id: str = Field(default="", max_length=128)


class WorkflowManualRetryRequest(BaseModel):
    """Payload for manual workflow rerun/resume from dashboard."""

    mode: str = Field(min_length=1, max_length=40)
    node_id: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)


class DeadLetterRetryRequest(BaseModel):
    """Payload for requeueing one dead-lettered job from dashboard."""

    note: str = Field(default="", max_length=300)


class MemoryOverrideRequest(BaseModel):
    """Payload for manual memory state override."""

    state: str = Field(default="", max_length=20)
    note: str = Field(default="", max_length=300)


class BacklogCandidateActionRequest(BaseModel):
    """Payload for one backlog candidate state transition."""

    action: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=300)


class RuntimeInputRequestPayload(BaseModel):
    """Payload for one operator-managed runtime input request."""

    repository: str = Field(default="", max_length=200)
    app_code: str = Field(default="", max_length=32)
    job_id: str = Field(default="", max_length=128)
    scope: str = Field(min_length=1, max_length=20)
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    value_type: str = Field(default="text", max_length=20)
    env_var_name: str = Field(default="", max_length=80)
    sensitive: bool = Field(default=False)
    placeholder: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=300)
    requested_by: str = Field(default="operator", max_length=40)


class RuntimeInputDraftRequestPayload(BaseModel):
    """Payload for suggesting one runtime input request draft."""

    repository: str = Field(default="", max_length=200)
    app_code: str = Field(default="", max_length=32)
    job_id: str = Field(default="", max_length=128)
    context_text: str = Field(default="", max_length=2000)


class RuntimeInputProvidePayload(BaseModel):
    """Payload for providing one runtime input value later."""

    value: str = Field(default="", max_length=8000)
    note: str = Field(default="", max_length=300)


def _read_dashboard_json(path: Path) -> Dict[str, Any]:
    """Read one dashboard-side JSON artifact safely."""

    return DashboardJobRuntime.read_dashboard_json(path)


def _build_dashboard_job_runtime(store: JobStore | None, settings: AppSettings) -> DashboardJobRuntime:
    """Build one job-detail helper runtime while preserving dashboard wrappers."""

    return DashboardJobRuntime(
        store=store,
        settings=settings,
        get_memory_runtime_store=lambda: _get_memory_runtime_store(settings),
        compute_job_resume_state=_compute_job_resume_state,
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": _resolve_channel_log_path(
            runtime_settings, file_name, channel
        ),
    )


def _build_dashboard_runtime_input_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardRuntimeInputRuntime:
    """Build one runtime-input helper runtime while preserving dashboard routes."""

    return DashboardRuntimeInputRuntime(store=store, settings=settings)


def _build_dashboard_admin_metrics_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardAdminMetricsRuntime:
    """Build one admin-metrics helper runtime while preserving dashboard wrappers."""

    return DashboardAdminMetricsRuntime(
        store=store,
        settings=settings,
        feature_flags_config_path=_FEATURE_FLAGS_CONFIG_PATH,
        apps_config_path=_APPS_CONFIG_PATH,
        workflows_config_path=_WORKFLOWS_CONFIG_PATH,
        roles_config_path=_ROLES_CONFIG_PATH,
        list_dashboard_jobs=_list_dashboard_jobs,
        build_job_summary=_build_job_summary,
        read_default_workflow_id=_read_default_workflow_id,
        read_registered_apps=_read_registered_apps,
        read_roles_payload=read_roles_payload,
        get_memory_runtime_store=_get_memory_runtime_store,
        read_dashboard_json=_read_dashboard_json,
        read_dashboard_jsonl=_read_dashboard_jsonl,
        job_workspace_path=_job_workspace_path,
        read_job_assistant_diagnosis_trace=_read_job_assistant_diagnosis_trace,
        top_counter_items=_top_counter_items,
        safe_average=_safe_average,
        latest_non_empty=_latest_non_empty,
        utc_now_iso=utc_now_iso,
    )


def _build_job_runtime_signals(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
) -> Dict[str, Any]:
    """Collect runtime review/resume/recovery signals for dashboard rendering."""

    return _build_dashboard_job_runtime(store, settings).build_job_runtime_signals(job)


def _build_dashboard_roles_runtime() -> DashboardRolesRuntime:
    """Build one shared roles/presets runtime while preserving dashboard routes."""

    return DashboardRolesRuntime()


def _read_job_memory_trace(job: JobRecord, settings: AppSettings) -> Dict[str, Any]:
    """Read one job's structured memory retrieval trace."""

    workspace_path = settings.repository_workspace_path(DashboardJobRuntime.job_execution_repository(job), job.app_code)
    return DashboardJobRuntime.read_dashboard_json(workspace_path / "_docs" / "MEMORY_TRACE.json")


def _read_job_assistant_diagnosis_trace(job: JobRecord, settings: AppSettings) -> Dict[str, Any]:
    """Read one job's latest assistant diagnosis trace artifact."""

    workspace_path = settings.repository_workspace_path(DashboardJobRuntime.job_execution_repository(job), job.app_code)
    paths = build_workflow_artifact_paths(workspace_path)
    trace_payload = DashboardJobRuntime.read_dashboard_json(paths["assistant_diagnosis_trace"])
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


def _read_job_runtime_recovery_trace(job: JobRecord, settings: AppSettings) -> Dict[str, Any]:
    """Read one job's structured runtime recovery trace artifact."""

    workspace_path = settings.repository_workspace_path(DashboardJobRuntime.job_execution_repository(job), job.app_code)
    paths = build_workflow_artifact_paths(workspace_path)
    trace_payload = DashboardJobRuntime.read_dashboard_json(paths["runtime_recovery_trace"])
    if not isinstance(trace_payload, dict):
        return {}
    raw_events = trace_payload.get("events", [])
    events: List[Dict[str, Any]] = []
    if isinstance(raw_events, list):
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            evidence = build_failure_evidence_summary(
                reason_code=str(enriched.get("reason_code", "")),
                reason=str(enriched.get("reason", "")),
                stage=str(enriched.get("stage", "")),
                source=str(enriched.get("source", "")),
                generated_at=str(enriched.get("generated_at", "")),
                details=enriched.get("details") if isinstance(enriched.get("details"), dict) else None,
                failure_class=str(enriched.get("failure_class", "")) or classify_runtime_recovery_event(enriched),
            )
            enriched["failure_class"] = evidence["failure_class"]
            enriched["provider_hint"] = evidence["provider_hint"]
            enriched["stage_family"] = evidence["stage_family"]
            events.append(enriched)
    latest_failure_class = ""
    latest_provider_hint = ""
    latest_stage_family = ""
    latest_needs_human_summary: Dict[str, Any] = {}
    if events:
        latest_failure_class = str(events[-1].get("failure_class", "")).strip()
        latest_provider_hint = str(events[-1].get("provider_hint", "")).strip()
        latest_stage_family = str(events[-1].get("stage_family", "")).strip()
        for event in reversed(events):
            summary = event.get("needs_human_summary")
            if isinstance(summary, dict) and summary.get("active"):
                latest_needs_human_summary = dict(summary)
                break
    return {
        "trace_path": str(paths["runtime_recovery_trace"]),
        "generated_at": str(trace_payload.get("generated_at", "")).strip(),
        "latest_event_at": str(trace_payload.get("latest_event_at", "")).strip(),
        "event_count": int(trace_payload.get("event_count", 0) or 0),
        "latest_failure_class": latest_failure_class,
        "latest_provider_hint": latest_provider_hint,
        "latest_stage_family": latest_stage_family,
        "latest_needs_human_summary": latest_needs_human_summary,
        "events": events,
    }


def _list_dashboard_jobs(store: JobStore, settings: AppSettings) -> List[Dict[str, Any]]:
    """Return dashboard jobs sorted by latest activity first."""

    jobs: List[Dict[str, Any]] = []
    for job in store.list_jobs():
        payload = job.to_dict()
        runtime_signals = _build_job_runtime_signals(job, store=store, settings=settings)
        payload["runtime_signals"] = runtime_signals
        payload["strategy"] = runtime_signals.get("strategy", "")
        payload["resume_mode"] = runtime_signals.get("resume_mode", "none")
        payload["review_overall"] = runtime_signals.get("review_overall")
        failure_classification = build_failure_classification_summary(job=job, runtime_recovery_trace=None)
        payload["failure_classification"] = failure_classification
        payload["failure_class"] = str(failure_classification.get("failure_class", "")).strip()
        payload["failure_provider_hint"] = str(failure_classification.get("provider_hint", "")).strip()
        payload["failure_stage_family"] = str(failure_classification.get("stage_family", "")).strip()
        jobs.append(payload)
    jobs.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return jobs


def _job_execution_repository(job: JobRecord) -> str:
    """Return the repo used for clone/build/push for one job."""

    return DashboardJobRuntime.job_execution_repository(job)


def _job_workspace_path(job: JobRecord, settings: AppSettings) -> Path:
    """Resolve workspace path using execution repository, not issue hub repository."""

    return settings.repository_workspace_path(_job_execution_repository(job), job.app_code)


def _build_job_summary(jobs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Compute status counters for one job collection."""

    return {
        "total": len(jobs),
        "queued": sum(1 for item in jobs if item.get("status") == JobStatus.QUEUED.value),
        "running": sum(1 for item in jobs if item.get("status") == JobStatus.RUNNING.value),
        "done": sum(1 for item in jobs if item.get("status") == JobStatus.DONE.value),
        "failed": sum(1 for item in jobs if item.get("status") == JobStatus.FAILED.value),
    }


def _filter_dashboard_jobs(
    jobs: List[Dict[str, Any]],
    *,
    status: str,
    track: str,
    app_code: str,
    stage: str,
    recovery_status: str,
    strategy: str,
    query: str,
) -> List[Dict[str, Any]]:
    """Filter jobs for dashboard search/paging."""

    normalized_status = status.strip().lower()
    normalized_track = track.strip().lower()
    normalized_app_code = app_code.strip().lower()
    normalized_stage = stage.strip().lower()
    normalized_recovery_status = recovery_status.strip().lower()
    normalized_strategy = strategy.strip().lower()
    normalized_query = query.strip().lower()

    filtered: List[Dict[str, Any]] = []
    for job in jobs:
        job_status = str(job.get("status", "")).strip().lower()
        job_track = str(job.get("track", "")).strip().lower()
        job_app_code = str(job.get("app_code", "")).strip().lower()
        job_stage = str(job.get("stage", "")).strip().lower()
        job_recovery_status = str(job.get("recovery_status", "")).strip().lower()
        job_strategy = str(job.get("strategy", "")).strip().lower()

        if normalized_status and job_status != normalized_status:
            continue
        if normalized_track and job_track != normalized_track:
            continue
        if normalized_app_code and job_app_code != normalized_app_code:
            continue
        if normalized_stage and job_stage != normalized_stage:
            continue
        if normalized_recovery_status and job_recovery_status != normalized_recovery_status:
            continue
        if normalized_strategy and job_strategy != normalized_strategy:
            continue
        if normalized_query:
            haystack = " ".join(
                [
                    str(job.get("job_id", "")),
                    str(job.get("issue_title", "")),
                    str(job.get("issue_number", "")),
                    str(job.get("issue_url", "")),
                    str(job.get("app_code", "")),
                    str(job.get("track", "")),
                    str(job.get("status", "")),
                    str(job.get("stage", "")),
                    str(job.get("branch_name", "")),
                    str(job.get("pr_url", "")),
                    str(job.get("workflow_id", "")),
                    str(job.get("error_message", "")),
                    str(job.get("failure_class", "")),
                    str(job.get("failure_provider_hint", "")),
                    str(job.get("failure_stage_family", "")),
                    str(job.get("recovery_status", "")),
                    str(job.get("strategy", "")),
                    str(job.get("resume_mode", "")),
                    str(job.get("review_overall", "")),
                    str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("maturity_level", "")),
                    str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("quality_trend_direction", "")),
                    str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("shadow_strategy", "")),
                    str((job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("shadow_decision_mode", "")),
                    " ".join(
                        str(item)
                        for item in (
                            (job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("persistent_low_categories", [])
                            or []
                        )
                    ),
                    " ".join(
                        str(item)
                        for item in (
                            (job.get("runtime_signals", {}) if isinstance(job.get("runtime_signals"), dict) else {}).get("quality_gate_categories", [])
                            or []
                        )
                    ),
                ]
            ).lower()
            if normalized_query not in haystack:
                continue
        filtered.append(job)
    return filtered


def _paginate_dashboard_jobs(
    jobs: List[Dict[str, Any]],
    *,
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    """Slice job list for current page and return pagination metadata."""

    total_items = len(jobs)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    safe_page = min(max(page, 1), total_pages)
    start_index = (safe_page - 1) * page_size
    end_index = start_index + page_size
    page_items = jobs[start_index:end_index]
    visible_start = start_index + 1 if total_items else 0
    visible_end = min(end_index, total_items)
    return {
        "items": page_items,
        "pagination": {
            "page": safe_page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_prev": safe_page > 1,
            "has_next": safe_page < total_pages,
            "start_index": visible_start,
            "end_index": visible_end,
        },
    }


def _dashboard_filter_options(jobs: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Return available filter values derived from current jobs."""

    stages = sorted(
        {
            str(item.get("stage", "")).strip()
            for item in jobs
            if str(item.get("stage", "")).strip()
        }
    )
    recovery_statuses = sorted(
        {
            str(item.get("recovery_status", "")).strip()
            for item in jobs
            if str(item.get("recovery_status", "")).strip()
        }
    )
    strategies = sorted(
        {
            str(item.get("strategy", "")).strip()
            for item in jobs
            if str(item.get("strategy", "")).strip()
        }
    )
    return {
        "statuses": [status.value for status in JobStatus],
        "tracks": sorted(_TRACK_CHOICES),
        "stages": stages,
        "recovery_statuses": recovery_statuses,
        "strategies": strategies,
    }


def _read_dashboard_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read one JSONL file into a list of dict entries."""

    if not path.exists():
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


def _top_counter_items(counter: Counter[str], *, limit: int = 5) -> List[Dict[str, Any]]:
    """Convert counter into stable top-N payload for dashboard rendering."""

    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
        if str(name).strip()
    ]


def _safe_average(values: List[float]) -> Optional[float]:
    """Return rounded average or None when list is empty."""

    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 2)


def _latest_non_empty(values: List[str]) -> str:
    """Return latest non-empty ISO-like string using lexical max."""

    normalized = [str(value).strip() for value in values if str(value).strip()]
    if not normalized:
        return ""
    return max(normalized)


def _memory_runtime_db_path(settings: AppSettings) -> Path:
    """Return canonical SQLite DB path for memory runtime."""

    return settings.resolved_memory_dir / "memory_runtime.db"


def _get_memory_runtime_store(settings: AppSettings) -> MemoryRuntimeStore:
    """Return canonical memory runtime store for admin/search APIs."""

    return MemoryRuntimeStore(_memory_runtime_db_path(settings))


def _normalized_job_kind(job: JobRecord | Dict[str, Any]) -> str:
    """Return one normalized job kind for UI/operator displays."""

    return DashboardJobRuntime.normalized_job_kind(job)


def _job_kind_label(job_kind: str) -> str:
    """Return one short localized label for a job kind."""

    return DashboardJobRuntime.job_kind_label(job_kind)


def _job_link_summary(job: JobRecord) -> Dict[str, Any]:
    """Return one small job summary payload for lineage/operator views."""

    return DashboardJobRuntime.job_link_summary(job) or {}


def _build_job_lineage(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
) -> Dict[str, Any]:
    """Collect parent/child/backlog lineage data for one job detail page."""

    return _build_dashboard_job_runtime(store, settings).build_job_lineage(job)


def _build_job_log_summary(
    job: JobRecord,
    *,
    settings: AppSettings,
    events: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Return operator-friendly summary for debug/user log channels."""

    return _build_dashboard_job_runtime(store=None, settings=settings).build_job_log_summary(
        job,
        events=events,
    )


def _build_job_operator_inputs(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
) -> Dict[str, Any]:
    """Return read-only operator runtime input state for one job detail page."""

    return _build_dashboard_job_runtime(store, settings).build_job_operator_inputs(job)


def _build_job_needs_human_summary(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
    runtime_recovery_trace: Dict[str, Any],
    failure_classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Return structured operator handoff for jobs waiting on humans."""

    return _build_dashboard_job_runtime(store, settings).build_job_needs_human_summary(
        job,
        runtime_recovery_trace=runtime_recovery_trace,
        failure_classification=failure_classification,
    )


def _build_job_dead_letter_summary(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
    runtime_recovery_trace: Dict[str, Any],
    failure_classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Return structured dead-letter summary for quarantined jobs."""

    return _build_dashboard_job_runtime(store, settings).build_job_dead_letter_summary(
        job,
        runtime_recovery_trace=runtime_recovery_trace,
        failure_classification=failure_classification,
    )


def _build_job_dead_letter_action_trail(
    *,
    store: JobStore,
    settings: AppSettings,
    runtime_recovery_trace: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return recent dead-letter related operator action trail for one job."""

    return _build_dashboard_job_runtime(store, settings).build_job_dead_letter_action_trail(
        runtime_recovery_trace=runtime_recovery_trace
    )


def _build_job_requeue_reason_summary(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
    runtime_recovery_trace: Dict[str, Any],
) -> Dict[str, Any]:
    """Return structured requeue reason summary for restart-safe reruns."""

    return _build_dashboard_job_runtime(store, settings).build_job_requeue_reason_summary(
        job,
        runtime_recovery_trace=runtime_recovery_trace,
    )


def _normalize_memory_state(value: str) -> str:
    """Normalize one optional memory state filter/override."""

    normalized = str(value or "").strip().lower()
    if normalized in {"", "active", "candidate", "promoted", "decayed", "banned", "archived"}:
        return normalized
    return ""


def _normalize_backlog_priority(value: str) -> str:
    """Normalize one optional backlog priority filter."""

    normalized = str(value or "").strip().upper()
    if normalized in {"", "P0", "P1", "P2", "P3"}:
        return normalized
    return ""


def _normalize_backlog_action(value: str) -> str:
    """Normalize one operator action for backlog candidates."""

    normalized = str(value or "").strip().lower()
    if normalized in {"approve", "queue", "dismiss"}:
        return normalized
    return ""


def _build_memory_detail_payload(
    runtime_store: MemoryRuntimeStore,
    *,
    memory_id: str,
) -> Dict[str, Any] | None:
    """Return one detailed memory payload for operator inspection."""

    entry = runtime_store.get_entry(memory_id)
    if entry is None:
        return None
    feedback_rows = runtime_store.list_feedback(memory_id=memory_id)
    evidence_rows = runtime_store.list_evidence(memory_id)
    return {
        "entry": entry,
        "evidence": evidence_rows[:20],
        "feedback": list(reversed(feedback_rows[-20:])),
    }


def _build_admin_assistant_diagnosis_metrics(
    store: JobStore,
    settings: AppSettings,
) -> Dict[str, Any]:
    """Aggregate recent assistant diagnosis traces for operator comparison."""

    return _build_dashboard_admin_metrics_runtime(store, settings).build_admin_assistant_diagnosis_metrics()


def _build_admin_metrics(store: JobStore, settings: AppSettings) -> Dict[str, Any]:
    """Aggregate read-only admin metrics from jobs and workspace artifacts."""

    return _build_dashboard_admin_metrics_runtime(store, settings).build_admin_metrics()


@router.get("/", response_class=HTMLResponse)
def job_list_page(
    request: Request,
) -> HTMLResponse:
    """Render dashboard shell.

    The first page load must stay fast even when job/runtime signal collection gets
    expensive. Jobs, apps, workflows, and other dashboard data are loaded
    asynchronously by the existing client-side bootstrap calls.
    """

    return _templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "AgentHub Jobs",
        },
    )


@router.get("/api/jobs", response_class=JSONResponse)
def jobs_api(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_DEFAULT_DASHBOARD_PAGE_SIZE, ge=1, le=_MAX_DASHBOARD_PAGE_SIZE),
    status: str = Query(default="", max_length=32),
    track: str = Query(default="", max_length=32),
    app_code: str = Query(default="", max_length=32),
    stage: str = Query(default="", max_length=64),
    recovery_status: str = Query(default="", max_length=64),
    strategy: str = Query(default="", max_length=64),
    q: str = Query(default="", max_length=200),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return filtered/paginated jobs as JSON for dashboard polling."""

    jobs = _list_dashboard_jobs(store, settings)
    filtered_jobs = _filter_dashboard_jobs(
        jobs,
        status=status,
        track=track,
        app_code=app_code,
        stage=stage,
        recovery_status=recovery_status,
        strategy=strategy,
        query=q,
    )
    paged = _paginate_dashboard_jobs(filtered_jobs, page=page, page_size=page_size)
    return JSONResponse(
        {
            "jobs": paged["items"],
            "summary": _build_job_summary(jobs),
            "filtered_summary": _build_job_summary(filtered_jobs),
            "pagination": paged["pagination"],
            "filters": {
                "status": status.strip().lower(),
                "track": track.strip().lower(),
                "app_code": app_code.strip().lower(),
                "stage": stage.strip().lower(),
                "recovery_status": recovery_status.strip().lower(),
                "strategy": strategy.strip().lower(),
                "q": q.strip(),
                "applied": any(
                    [
                        status.strip(),
                        track.strip(),
                        app_code.strip(),
                        stage.strip(),
                        recovery_status.strip(),
                        strategy.strip(),
                        q.strip(),
                    ]
                ),
            },
            "filter_options": _dashboard_filter_options(jobs),
        }
    )


@router.get("/api/admin/metrics", response_class=JSONResponse)
def admin_metrics_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return read-only admin metrics for dashboard management view."""

    return JSONResponse(_build_admin_metrics(store, settings))


@router.get("/api/admin/memory/search", response_class=JSONResponse)
def admin_memory_search_api(
    q: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=20),
    memory_type: str = Query(default="", max_length=40),
    repository: str = Query(default="", max_length=200),
    execution_repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    workflow_id: str = Query(default="", max_length=120),
    limit: int = Query(default=12, ge=1, le=100),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Search memory runtime entries with lightweight filters for admin UI."""

    normalized_state = _normalize_memory_state(state)
    if state.strip() and not normalized_state:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 memory state 입니다: {state}")
    runtime_store = _get_memory_runtime_store(settings)
    runtime_store.refresh_rankings(as_of=utc_now_iso())
    items = runtime_store.search_entries(
        query=q,
        state=normalized_state,
        memory_type=memory_type,
        repository=repository,
        execution_repository=execution_repository,
        app_code=app_code,
        workflow_id=workflow_id,
        limit=limit,
    )
    return JSONResponse(
        {
            "items": items,
            "count": len(items),
            "filters": {
                "q": q.strip(),
                "state": normalized_state,
                "memory_type": memory_type.strip().lower(),
                "repository": repository.strip(),
                "execution_repository": execution_repository.strip(),
                "app_code": app_code.strip(),
                "workflow_id": workflow_id.strip(),
                "limit": limit,
            },
        }
    )


@router.get("/api/admin/memory/backlog", response_class=JSONResponse)
def admin_memory_backlog_api(
    q: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=40),
    priority: str = Query(default="", max_length=4),
    repository: str = Query(default="", max_length=200),
    execution_repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    workflow_id: str = Query(default="", max_length=120),
    limit: int = Query(default=12, ge=1, le=100),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """List memory-backed autonomous backlog candidates for admin review."""

    normalized_priority = _normalize_backlog_priority(priority)
    if priority.strip() and not normalized_priority:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 backlog priority 입니다: {priority}")
    runtime_store = _get_memory_runtime_store(settings)
    items = runtime_store.list_backlog_candidates(
        query=q,
        state=state,
        priority=normalized_priority,
        repository=repository,
        execution_repository=execution_repository,
        app_code=app_code,
        workflow_id=workflow_id,
        limit=limit,
    )
    return JSONResponse(
        {
            "items": items,
            "count": len(items),
            "filters": {
                "q": q.strip(),
                "state": state.strip().lower(),
                "priority": normalized_priority,
                "repository": repository.strip(),
                "execution_repository": execution_repository.strip(),
                "app_code": app_code.strip(),
                "workflow_id": workflow_id.strip(),
                "limit": limit,
            },
        }
    )


@router.post("/api/admin/memory/backlog/{candidate_id:path}/action", response_class=JSONResponse)
def admin_memory_backlog_action_api(
    candidate_id: str,
    payload: BacklogCandidateActionRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Apply one small operator action to a backlog candidate."""

    action = _normalize_backlog_action(payload.action)
    if not action:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 backlog action 입니다: {payload.action}")

    runtime_store = _get_memory_runtime_store(settings)
    candidate = runtime_store.get_backlog_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"candidate_id를 찾을 수 없습니다: {candidate_id}")

    note = str(payload.note or "").strip()
    if action == "approve":
        updated = runtime_store.set_backlog_candidate_state(
            candidate_id,
            state="approved",
            payload_updates={
                "approved_at": utc_now_iso(),
                "operator_note": note,
                "last_action": "approve",
            },
        )
        assert updated is not None
        return JSONResponse({"ok": True, "action": action, "candidate": updated})

    if action == "dismiss":
        updated = runtime_store.set_backlog_candidate_state(
            candidate_id,
            state="dismissed",
            payload_updates={
                "dismissed_at": utc_now_iso(),
                "operator_note": note,
                "last_action": "dismiss",
            },
        )
        assert updated is not None
        return JSONResponse({"ok": True, "action": action, "candidate": updated})

    if str(candidate.get("state", "")).strip().lower() == "queued":
        queued_job_id = str((candidate.get("payload", {}) or {}).get("queued_job_id", "")).strip()
        return JSONResponse(
            {
                "ok": True,
                "action": action,
                "already_queued": True,
                "candidate": candidate,
                "queued_job_id": queued_job_id,
            }
        )

    queued_job, artifact_path = _queue_followup_job_from_backlog_candidate(
        candidate=candidate,
        runtime_store=runtime_store,
        store=store,
        settings=settings,
        note=note,
    )
    updated = runtime_store.get_backlog_candidate(candidate_id)
    assert updated is not None
    return JSONResponse(
        {
            "ok": True,
            "action": action,
            "candidate": updated,
            "queued_job_id": queued_job.job_id,
            "artifact_path": str(artifact_path),
        }
    )


@router.get("/api/admin/memory/{memory_id:path}", response_class=JSONResponse)
def admin_memory_detail_api(
    memory_id: str,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one detailed memory payload including evidence and feedback."""

    runtime_store = _get_memory_runtime_store(settings)
    runtime_store.refresh_rankings(as_of=utc_now_iso())
    payload = _build_memory_detail_payload(runtime_store, memory_id=memory_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"memory_id를 찾을 수 없습니다: {memory_id}")
    return JSONResponse(payload)


@router.post("/api/admin/memory/{memory_id:path}/override", response_class=JSONResponse)
def admin_memory_override_api(
    memory_id: str,
    payload: MemoryOverrideRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Apply or clear one manual memory state override."""

    normalized_state = _normalize_memory_state(payload.state)
    if payload.state.strip() and not normalized_state:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 memory override state 입니다: {payload.state}")
    runtime_store = _get_memory_runtime_store(settings)
    updated = runtime_store.set_manual_override(memory_id, state=normalized_state, note=payload.note)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"memory_id를 찾을 수 없습니다: {memory_id}")
    detail = _build_memory_detail_payload(runtime_store, memory_id=memory_id)
    return JSONResponse(
        {
            "saved": True,
            "memory_id": memory_id,
            "manual_state_override": normalized_state,
            "entry": updated,
            "detail": detail,
        }
    )


@router.get("/api/admin/runtime-inputs", response_class=JSONResponse)
def admin_runtime_inputs_api(
    q: str = Query(default="", max_length=200),
    status: str = Query(default="", max_length=20),
    scope: str = Query(default="", max_length=20),
    repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    job_id: str = Query(default="", max_length=128),
    limit: int = Query(default=20, ge=1, le=100),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """List operator-managed runtime input requests/values for dashboard admin."""

    runtime = _build_dashboard_runtime_input_runtime(store, settings)
    return JSONResponse(
        runtime.list_runtime_inputs(
            q=q,
            status=status,
            scope=scope,
            repository=repository,
            app_code=app_code,
            job_id=job_id,
            limit=limit,
        )
    )


@router.post("/api/admin/runtime-inputs/draft", response_class=JSONResponse)
def admin_runtime_inputs_draft_api(
    payload: RuntimeInputDraftRequestPayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Suggest operator-approval runtime input drafts from job/context text."""

    runtime = _build_dashboard_runtime_input_runtime(store, settings)
    return JSONResponse(
        runtime.suggest_runtime_input_drafts(
            repository=payload.repository,
            app_code=payload.app_code,
            job_id=payload.job_id,
            context_text=payload.context_text,
        )
    )


@router.post("/api/admin/runtime-inputs/request", response_class=JSONResponse)
def admin_runtime_inputs_request_api(
    payload: RuntimeInputRequestPayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create one small operator runtime input request."""

    runtime = _build_dashboard_runtime_input_runtime(store, settings)
    return JSONResponse(
        runtime.create_runtime_input_request(
            repository=payload.repository,
            app_code=payload.app_code,
            job_id=payload.job_id,
            scope=payload.scope,
            key=payload.key,
            label=payload.label,
            description=payload.description,
            value_type=payload.value_type,
            env_var_name=payload.env_var_name,
            sensitive=payload.sensitive,
            placeholder=payload.placeholder,
            note=payload.note,
            requested_by=payload.requested_by,
        )
    )


@router.post("/api/admin/runtime-inputs/{request_id:path}/provide", response_class=JSONResponse)
def admin_runtime_inputs_provide_api(
    request_id: str,
    payload: RuntimeInputProvidePayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Provide or clear one runtime input value."""

    runtime = _build_dashboard_runtime_input_runtime(store, settings)
    return JSONResponse(
        runtime.provide_runtime_input(
            request_id=request_id,
            value=payload.value,
            note=payload.note,
        )
    )


@router.get("/api/jobs/options", response_class=JSONResponse)
def job_options_api(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return compact job options for combobox-style selectors."""

    jobs = _list_dashboard_jobs(store, settings)
    filtered_jobs = _filter_dashboard_jobs(
        jobs,
        status="",
        track="",
        app_code="",
        stage="",
        recovery_status="",
        strategy="",
        query=q,
    )
    items: List[Dict[str, str]] = []
    for job in filtered_jobs[:limit]:
        issue_title = str(job.get("issue_title", "")).strip()
        truncated_title = issue_title[:72] + ("..." if len(issue_title) > 72 else "")
        items.append(
            {
                "job_id": str(job.get("job_id", "")),
                "label": (
                    f"{str(job.get('job_id', ''))[:8]} | "
                    f"{str(job.get('status', '-'))} | "
                    f"#{str(job.get('issue_number', '-'))} {truncated_title}"
                ),
                "status": str(job.get("status", "")),
                "stage": str(job.get("stage", "")),
                "app_code": str(job.get("app_code", "")),
                "track": str(job.get("track", "")),
                "issue_title": issue_title,
            }
        )
    return JSONResponse({"items": items, "query": q.strip(), "limit": limit})


@router.get("/api/apps", response_class=JSONResponse)
def list_apps(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return registered app list for dashboard dropdowns."""

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    return JSONResponse(
        {
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
            "tracks": sorted(_TRACK_CHOICES),
            "default_workflow_id": default_workflow_id,
        }
    )


@router.post("/api/apps", response_class=JSONResponse)
def upsert_app(
    payload: AppConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create or update one app registration entry."""

    code = _normalize_app_code(payload.code)
    if not code:
        raise HTTPException(status_code=400, detail="앱 코드는 영문/숫자/-/_ 형식이어야 합니다.")
    if code == "default":
        raise HTTPException(status_code=400, detail="default 코드는 예약되어 있습니다.")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="앱 표시명을 입력해주세요.")
    source_repository = _normalize_repository_ref(payload.source_repository)
    if payload.source_repository.strip() and not source_repository:
        raise HTTPException(
            status_code=400,
            detail="source_repository는 GitHub owner/repo 또는 https://github.com/owner/repo(.git) 형식이어야 합니다.",
        )

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    workflows_payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    workflows = workflows_payload.get("workflows", [])
    known_workflow_ids = {
        str(item.get("workflow_id", "")).strip()
        for item in workflows
        if isinstance(item, dict)
    }
    requested_workflow_id = str(payload.workflow_id or "").strip()
    workflow_id = requested_workflow_id or default_workflow_id
    if workflow_id and workflow_id not in known_workflow_ids:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 workflow_id 입니다: {workflow_id}")
    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    replaced = False
    updated: List[Dict[str, str]] = []
    for app in apps:
        if app["code"] == "default":
            updated.append(app)
            continue
        if app["code"] == code:
            updated.append(
                {
                    "code": code,
                    "name": name,
                    "repository": settings.allowed_repository,
                    "workflow_id": workflow_id,
                    "source_repository": source_repository,
                }
            )
            replaced = True
            continue
        updated.append(app)
    if not replaced:
        updated.append(
            {
                "code": code,
                "name": name,
                "repository": settings.allowed_repository,
                "workflow_id": workflow_id,
                "source_repository": source_repository,
            }
        )

    _write_registered_apps(_APPS_CONFIG_PATH, updated)
    _ensure_label(settings.allowed_repository, f"app:{code}", "0052CC", f"AgentHub app namespace ({code})")
    for track in sorted(_TRACK_CHOICES):
        _ensure_label(settings.allowed_repository, f"track:{track}", "5319E7", f"AgentHub work type ({track})")
    return JSONResponse(
        {
            "saved": True,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.delete("/api/apps/{app_code}", response_class=JSONResponse)
def delete_app(
    app_code: str,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Delete one app registration entry."""

    code = _normalize_app_code(app_code)
    if not code or code == "default":
        raise HTTPException(status_code=400, detail="삭제할 수 없는 앱 코드입니다.")

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    filtered = [app for app in apps if app["code"] != code]
    _write_registered_apps(_APPS_CONFIG_PATH, filtered)
    return JSONResponse(
        {
            "deleted": True,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.post("/api/apps/{app_code}/workflow", response_class=JSONResponse)
def map_app_workflow(
    app_code: str,
    payload: AppWorkflowMappingRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Bind one app code to one registered workflow id."""

    code = _normalize_app_code(app_code)
    if not code:
        raise HTTPException(status_code=400, detail="유효하지 않은 앱 코드입니다.")

    workflows_payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    default_workflow_id = str(workflows_payload.get("default_workflow_id", "")).strip() or default_workflow_template()["workflow_id"]
    workflows = workflows_payload.get("workflows", [])
    known_workflow_ids = {
        str(item.get("workflow_id", "")).strip()
        for item in workflows
        if isinstance(item, dict)
    }

    workflow_id = payload.workflow_id.strip()
    if workflow_id not in known_workflow_ids:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 workflow_id 입니다: {workflow_id}")

    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    found = False
    updated: List[Dict[str, str]] = []
    for app in apps:
        if app["code"] == code:
            copied = dict(app)
            copied["workflow_id"] = workflow_id
            updated.append(copied)
            found = True
            continue
        updated.append(app)

    if not found:
        raise HTTPException(status_code=404, detail=f"앱을 찾을 수 없습니다: {code}")

    _write_registered_apps(_APPS_CONFIG_PATH, updated)
    return JSONResponse(
        {
            "saved": True,
            "app_code": code,
            "workflow_id": workflow_id,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.get("/api/roles", response_class=JSONResponse)
def roles_api() -> JSONResponse:
    """Return role definitions and role-combination presets."""

    payload = _build_dashboard_roles_runtime().list_roles(roles_config_path=_ROLES_CONFIG_PATH)
    return JSONResponse(payload)


@router.post("/api/roles", response_class=JSONResponse)
def upsert_role(payload: RoleConfigRequest) -> JSONResponse:
    """Create or update one role definition."""

    try:
        response_payload = _build_dashboard_roles_runtime().upsert_role(
            roles_config_path=_ROLES_CONFIG_PATH,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.delete("/api/roles/{role_code}", response_class=JSONResponse)
def delete_role(role_code: str) -> JSONResponse:
    """Delete one role and unlink it from presets."""

    try:
        response_payload = _build_dashboard_roles_runtime().delete_role(
            roles_config_path=_ROLES_CONFIG_PATH,
            role_code=role_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.post("/api/role-presets", response_class=JSONResponse)
def upsert_role_preset(payload: RolePresetRequest) -> JSONResponse:
    """Create or update one role-combination preset."""

    try:
        response_payload = _build_dashboard_roles_runtime().upsert_role_preset(
            roles_config_path=_ROLES_CONFIG_PATH,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.delete("/api/role-presets/{preset_id}", response_class=JSONResponse)
def delete_role_preset(preset_id: str) -> JSONResponse:
    """Delete one role-combination preset."""

    try:
        response_payload = _build_dashboard_roles_runtime().delete_role_preset(
            roles_config_path=_ROLES_CONFIG_PATH,
            preset_id=preset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.get("/api/workflows/schema", response_class=JSONResponse)
def workflow_schema_api() -> JSONResponse:
    """Return workflow node/edge schema metadata for editor UI."""

    return JSONResponse(schema_payload())


@router.get("/api/workflows", response_class=JSONResponse)
def workflows_api() -> JSONResponse:
    """Return saved workflows and current default workflow id."""

    payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    return JSONResponse(payload)


@router.post("/api/workflows/validate", response_class=JSONResponse)
def validate_workflow_api(
    payload: WorkflowValidateRequest,
) -> JSONResponse:
    """Validate one workflow definition without saving."""

    ok, errors = validate_workflow(payload.workflow)
    return JSONResponse({"ok": ok, "errors": errors})


@router.post("/api/workflows", response_class=JSONResponse)
def save_workflow_api(
    payload: WorkflowSaveRequest,
) -> JSONResponse:
    """Save one workflow definition in phase-1 workflow config."""

    workflow = payload.workflow
    ok, errors = validate_workflow(workflow)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": "workflow validation failed", "errors": errors})

    saved = load_workflows(_WORKFLOWS_CONFIG_PATH)
    workflows = saved.get("workflows", [])
    if not isinstance(workflows, list):
        workflows = []

    workflow_id = str(workflow.get("workflow_id", "")).strip()
    replaced = False
    next_workflows: List[Dict[str, Any]] = []
    for item in workflows:
        if isinstance(item, dict) and str(item.get("workflow_id", "")) == workflow_id:
            next_workflows.append(workflow)
            replaced = True
            continue
        if isinstance(item, dict):
            next_workflows.append(item)
    if not replaced:
        next_workflows.append(workflow)

    saved["workflows"] = next_workflows
    if payload.set_default or not str(saved.get("default_workflow_id", "")).strip():
        saved["default_workflow_id"] = workflow_id
    if saved.get("default_workflow_id") == "":
        saved["default_workflow_id"] = default_workflow_template()["workflow_id"]

    save_workflows(_WORKFLOWS_CONFIG_PATH, saved)
    return JSONResponse({"saved": True, "workflow_id": workflow_id, "default_workflow_id": saved.get("default_workflow_id")})


@router.post("/api/workflows/default", response_class=JSONResponse)
def set_default_workflow_api(
    payload: WorkflowDefaultRequest,
) -> JSONResponse:
    """Set one registered workflow as default."""

    saved = load_workflows(_WORKFLOWS_CONFIG_PATH)
    workflows = saved.get("workflows", [])
    workflow_id = payload.workflow_id.strip()
    known_workflow_ids = {
        str(item.get("workflow_id", "")).strip()
        for item in workflows
        if isinstance(item, dict)
    }
    if workflow_id not in known_workflow_ids:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 workflow_id 입니다: {workflow_id}")

    saved["default_workflow_id"] = workflow_id
    save_workflows(_WORKFLOWS_CONFIG_PATH, saved)
    return JSONResponse({"saved": True, "default_workflow_id": workflow_id})


@router.get("/api/feature-flags", response_class=JSONResponse)
def get_feature_flags_api() -> JSONResponse:
    """Return adaptive feature flags for settings/admin UI."""

    return JSONResponse(feature_flags_payload(_FEATURE_FLAGS_CONFIG_PATH))


@router.post("/api/feature-flags", response_class=JSONResponse)
def save_feature_flags_api(
    payload: FeatureFlagsRequest,
) -> JSONResponse:
    """Persist adaptive feature flags."""

    flags = write_feature_flags(_FEATURE_FLAGS_CONFIG_PATH, payload.flags)
    return JSONResponse({"saved": True, **feature_flags_payload(_FEATURE_FLAGS_CONFIG_PATH), "flags": flags})


@router.get("/api/agents/config", response_class=JSONResponse)
def get_agents_config(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return editable command templates for dashboard form."""

    return JSONResponse(
        load_agent_template_config(
            settings.command_config,
            Path.cwd() / ".env",
            enable_escalation_fallback=settings.enable_escalation,
        )
    )


@router.post("/api/agents/config", response_class=JSONResponse)
def update_agents_config(
    payload: AgentTemplateConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Update planner/coder/reviewer templates in command config file."""

    return JSONResponse(
        update_agent_template_config(
            settings.command_config,
            Path.cwd() / ".env",
            planner=payload.planner,
            coder=payload.coder,
            reviewer=payload.reviewer,
            copilot=payload.copilot,
            escalation=payload.escalation,
            enable_escalation=payload.enable_escalation,
        )
    )


@router.get("/api/agents/check", response_class=JSONResponse)
def check_agent_clis(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Check whether Gemini/Codex CLIs are executable."""

    return JSONResponse(collect_agent_cli_status(settings.command_config))


@router.get("/api/agents/models", response_class=JSONResponse)
def check_agent_models(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return inferred model settings for Gemini/Codex."""

    return JSONResponse(collect_agent_model_status(settings.command_config))


@router.post("/api/assistant/codex-chat", response_class=JSONResponse)
def codex_assistant_chat(
    payload: AssistantChatRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Legacy Codex-only chat route kept for compatibility."""

    payload.assistant = "codex"
    return assistant_chat(payload=payload, settings=settings, store=store)


@router.post("/api/assistant/chat", response_class=JSONResponse)
def assistant_chat(
    payload: AssistantChatRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Run one conversational assistant turn with selected provider."""

    requested_assistant = str(payload.assistant or "").strip().lower()
    allowed = _PRIMARY_ASSISTANT_PROVIDERS | set(ASSISTANT_PROVIDER_ALIASES)
    if requested_assistant not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"지원하지 않는 assistant 입니다: {requested_assistant}. "
                f"공식 지원: {', '.join(sorted(_PRIMARY_ASSISTANT_PROVIDERS))}. "
                f"호환 별칭: {', '.join(sorted(ASSISTANT_PROVIDER_ALIASES))}"
            ),
        )
    assistant = canonical_cli_name(requested_assistant)

    raw_message = payload.message.strip()
    if not raw_message:
        raise HTTPException(status_code=400, detail="메시지를 입력해주세요.")

    focus_job_id = payload.job_id.strip()
    focus_context = ""
    diagnosis_trace: Dict[str, Any] = {"enabled": False, "tool_runs": []}
    if focus_job_id:
        job = store.get_job(focus_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {focus_job_id}")
        focus_context = _build_focus_job_log_context(job, settings)
        diagnosis_trace = _run_assistant_diagnosis_loop(
            job=job,
            question=raw_message,
            settings=settings,
            assistant_scope="chat",
        )

    runtime_context = _build_agent_observability_context(store, settings)
    prompt = _build_assistant_chat_prompt(
        assistant=assistant,
        message=raw_message,
        history=payload.history,
        runtime_context=runtime_context,
        focus_context=focus_context,
        diagnosis_context=str(diagnosis_trace.get("context_text", "")).strip(),
    )
    try:
        templates = _read_command_templates(settings.command_config)
    except HTTPException:
        templates = {}

    output_text = _run_assistant_chat_provider(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
    )
    return JSONResponse(
        {
            "ok": True,
            "assistant": output_text,
            "provider": assistant,
            "requested_provider": requested_assistant,
            "focus_job_id": focus_job_id,
            "diagnosis_trace": {
                "enabled": bool(diagnosis_trace.get("enabled")),
                "trace_path": str(diagnosis_trace.get("trace_path", "")).strip(),
                "tool_runs": diagnosis_trace.get("tool_runs", []),
            },
        }
    )


@router.post("/api/assistant/log-analysis", response_class=JSONResponse)
def assistant_log_analysis(
    payload: AssistantLogAnalysisRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Analyze AgentHub logs with one selected assistant CLI."""

    requested_assistant = str(payload.assistant or "").strip().lower()
    allowed = _PRIMARY_ASSISTANT_PROVIDERS | set(ASSISTANT_PROVIDER_ALIASES)
    if requested_assistant not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"지원하지 않는 assistant 입니다: {requested_assistant}. "
                f"공식 지원: {', '.join(sorted(_PRIMARY_ASSISTANT_PROVIDERS))}. "
                f"호환 별칭: {', '.join(sorted(ASSISTANT_PROVIDER_ALIASES))}"
            ),
        )
    assistant = canonical_cli_name(requested_assistant)

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question은 비어 있을 수 없습니다.")

    focus_job_id = payload.job_id.strip()
    focus_context = ""
    diagnosis_trace: Dict[str, Any] = {"enabled": False, "tool_runs": []}
    if focus_job_id:
        job = store.get_job(focus_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {focus_job_id}")
        focus_context = _build_focus_job_log_context(job, settings)
        diagnosis_trace = _run_assistant_diagnosis_loop(
            job=job,
            question=question,
            settings=settings,
        )

    runtime_context = _build_agent_observability_context(store, settings)
    prompt = _build_log_analysis_prompt(
        assistant=assistant,
        question=question,
        runtime_context=runtime_context,
        focus_context=focus_context,
        diagnosis_context=str(diagnosis_trace.get("context_text", "")).strip(),
    )

    try:
        templates = _read_command_templates(settings.command_config)
    except HTTPException:
        templates = {}

    analysis = _run_log_analyzer(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
    )
    return JSONResponse(
        {
            "ok": True,
            "assistant": analysis,
            "provider": assistant,
            "requested_provider": requested_assistant,
            "focus_job_id": focus_job_id,
            "diagnosis_trace": {
                "enabled": bool(diagnosis_trace.get("enabled")),
                "trace_path": str(diagnosis_trace.get("trace_path", "")).strip(),
                "tool_runs": diagnosis_trace.get("tool_runs", []),
            },
        }
    )


@router.post("/api/issues/register", response_class=JSONResponse)
def register_issue_and_trigger(
    payload: IssueRegistrationRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create a GitHub issue, label it, and trigger a local job immediately."""

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="이슈 제목은 필수입니다.")

    body = payload.body.strip() or "AgentHub 대시보드에서 등록된 작업 이슈입니다."
    app_code = _normalize_app_code(payload.app_code) or "default"
    track = _normalize_track(payload.track)
    keep_branch = bool(payload.keep_branch)
    requested_branch_name = (payload.branch_name or "").strip()
    role_preset_id = normalize_role_code(payload.role_preset_id)
    requested_workflow_id = (payload.workflow_id or "").strip()
    title_track = _detect_title_track(title)
    if title_track:
        track = title_track
    repository = settings.allowed_repository
    registered_apps = _read_registered_apps(_APPS_CONFIG_PATH, repository)
    app_entry = next((item for item in registered_apps if item.get("code") == app_code), None)
    if app_entry is None:
        raise HTTPException(
            status_code=400,
            detail=f"등록되지 않은 앱 코드입니다: {app_code}. 설정 메뉴에서 먼저 등록해주세요.",
        )
    source_repository = str(app_entry.get("source_repository", "")).strip()
    if requested_workflow_id:
        known_workflow_ids = list_known_workflow_ids(_WORKFLOWS_CONFIG_PATH)
        if requested_workflow_id not in known_workflow_ids:
            raise HTTPException(
                status_code=400,
                detail=f"등록되지 않은 workflow_id 입니다: {requested_workflow_id}",
            )

    if role_preset_id:
        roles_payload = read_roles_payload(_ROLES_CONFIG_PATH)
        presets = roles_payload.get("presets", [])
        matched = next(
            (
                item
                for item in presets
                if _normalize_role_code(str(item.get("preset_id", ""))) == role_preset_id
            ),
            None,
        )
        if matched is None:
            raise HTTPException(status_code=400, detail=f"등록되지 않은 역할 프리셋입니다: {role_preset_id}")
        role_codes = matched.get("role_codes", [])
        body = (
            f"{body}\n\n"
            "## ROLE PRESET\n"
            f"- preset_id: `{role_preset_id}`\n"
            f"- roles: {', '.join(f'`{code}`' for code in role_codes) if role_codes else '(none)'}\n"
        )

    create_stdout = _run_gh_command(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repository,
            "--title",
            title,
            "--body",
            body,
        ],
        error_context="GitHub 이슈 생성",
    )
    issue_url = _extract_issue_url(create_stdout)

    issue_number = _extract_issue_number(issue_url)

    _ensure_agent_run_label(repository)
    _ensure_label(repository, f"app:{app_code}", "0052CC", f"AgentHub app namespace ({app_code})")
    _ensure_label(repository, f"track:{track}", "5319E7", f"AgentHub work type ({track})")

    _run_gh_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repository,
            "--add-label",
            f"agent:run,app:{app_code},track:{track}",
        ],
        error_context="작업 라벨 추가",
    )

    existing = _find_active_job(store, repository, issue_number)
    if existing is not None:
        return JSONResponse(
            {
                "accepted": True,
                "created_issue": True,
                "triggered": False,
                "reason": "already_active_job",
                "job_id": existing.job_id,
                "issue_number": issue_number,
                "issue_url": issue_url,
            }
        )

    now = utc_now_iso()
    job_id = str(uuid.uuid4())
    workflow_selection = resolve_workflow_selection(
        requested_workflow_id=requested_workflow_id,
        app_code=app_code,
        repository=repository,
        apps_path=_APPS_CONFIG_PATH,
        workflows_path=_WORKFLOWS_CONFIG_PATH,
    )
    job = JobRecord(
        job_id=job_id,
        repository=repository,
        issue_number=issue_number,
        issue_title=title,
        issue_url=issue_url,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=settings.max_retries,
        branch_name=_build_branch_name(
            app_code,
            issue_number,
            track,
            job_id,
            keep_branch=keep_branch,
            requested_branch_name=requested_branch_name,
        ),
        pr_url=None,
        error_message=None,
        log_file=_build_log_file_name(app_code, job_id),
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
        workflow_id=workflow_selection.workflow_id,
        source_repository=source_repository,
    )

    store.create_job(job)
    store.enqueue_job(job_id)

    return JSONResponse(
        {
            "accepted": True,
            "created_issue": True,
            "triggered": True,
            "job_id": job_id,
            "issue_number": issue_number,
            "issue_url": issue_url,
            "app_code": app_code,
            "track": track,
            "workflow_id": workflow_selection.workflow_id,
            "workflow_source": workflow_selection.source,
            "source_repository": source_repository,
            "keep_branch": keep_branch,
            "role_preset_id": role_preset_id,
        }
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(
    job_id: str,
    request: Request,
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    """Render details and quick links for one job."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return _templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "title": f"Job {job_id}",
        },
    )


@router.get("/api/jobs/{job_id}", response_class=JSONResponse)
def job_detail_api(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one job plus parsed log conversation events and agent artifacts."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    log_path = _resolve_channel_log_path(settings, job.log_file, channel="debug")
    events = _parse_log_events(log_path) if log_path.exists() else []
    
    workspace_path = _job_workspace_path(job, settings)
    md_files = _read_agent_md_files(workspace_path)
    stage_md_snapshots = _read_stage_md_snapshots(settings.data_dir, job_id)
    node_runs = store.list_node_runs(job_id)
    workflow_runtime, _, _ = _resolve_job_workflow_runtime(job)
    workflow_runtime["fallback_events"] = _extract_workflow_fallback_events(events)
    if any(bool(item.get("uses_fixed_pipeline")) for item in workflow_runtime["fallback_events"]):
        workflow_runtime["uses_fixed_pipeline"] = True
    resume_state = _compute_job_resume_state(job, node_runs, settings)
    runtime_signals = _build_job_runtime_signals(job, store=store, settings=settings)
    memory_trace = _read_job_memory_trace(job, settings)
    assistant_diagnosis_trace = _read_job_assistant_diagnosis_trace(job, settings)
    runtime_recovery_trace = _read_job_runtime_recovery_trace(job, settings)
    failure_classification = build_failure_classification_summary(job=job, runtime_recovery_trace=runtime_recovery_trace)
    needs_human_summary = _build_job_needs_human_summary(
        job,
        store=store,
        settings=settings,
        runtime_recovery_trace=runtime_recovery_trace,
        failure_classification=failure_classification,
    )
    dead_letter_summary = _build_job_dead_letter_summary(
        job,
        store=store,
        settings=settings,
        runtime_recovery_trace=runtime_recovery_trace,
        failure_classification=failure_classification,
    )
    dead_letter_action_trail = _build_job_dead_letter_action_trail(
        store=store,
        settings=settings,
        runtime_recovery_trace=runtime_recovery_trace,
    )
    requeue_reason_summary = _build_job_requeue_reason_summary(
        job,
        store=store,
        settings=settings,
        runtime_recovery_trace=runtime_recovery_trace,
    )
    manual_retry_options = _build_manual_retry_options(job, settings=settings, node_runs=node_runs)
    job_lineage = _build_job_lineage(job, store=store, settings=settings)
    log_summary = _build_job_log_summary(job, settings=settings, events=events)
    operator_inputs = _build_job_operator_inputs(job, store=store, settings=settings)

    return JSONResponse(
        {
            "job": job.to_dict(),
            "events": events,
            "md_files": md_files,
            "stage_md_snapshots": stage_md_snapshots,
            "node_runs": [item.to_dict() for item in node_runs],
            "workflow_runtime": workflow_runtime,
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
            "job_lineage": job_lineage,
            "log_summary": log_summary,
            "operator_inputs": operator_inputs,
            "stop_requested": _stop_signal_path(settings.data_dir, job_id).exists(),
        }
    )


@router.get("/api/jobs/{job_id}/node-runs", response_class=JSONResponse)
def job_node_runs_api(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return persisted workflow node execution records for one job."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    node_runs = store.list_node_runs(job_id)
    workflow_runtime, _, _ = _resolve_job_workflow_runtime(job)
    resume_state = _compute_job_resume_state(job, node_runs, settings)
    manual_retry_options = _build_manual_retry_options(job, settings=settings, node_runs=node_runs)
    return JSONResponse(
        {
            "job_id": job_id,
            "workflow_id": job.workflow_id,
            "node_runs": [item.to_dict() for item in node_runs],
            "workflow_runtime": workflow_runtime,
            "resume_state": resume_state,
            "manual_retry_options": manual_retry_options,
        }
    )


def _resolve_job_workflow_runtime(
    job: JobRecord,
) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Resolve one job to workflow metadata plus a validated workflow definition."""

    default_id, workflows_by_id = _load_workflows_catalog()
    requested_workflow_id = str(job.workflow_id or "").strip()
    selection = resolve_workflow_selection(
        requested_workflow_id=requested_workflow_id,
        app_code=job.app_code,
        repository=job.repository,
        apps_path=_APPS_CONFIG_PATH,
        workflows_path=_WORKFLOWS_CONFIG_PATH,
    )
    selected = workflows_by_id.get(selection.workflow_id)
    if selected is None and selection.workflow_id != default_id:
        selected = workflows_by_id.get(default_id)

    raw_workflow = selected if isinstance(selected, dict) else {}
    definition_valid = False
    validation_errors: List[str] = []
    ordered_nodes: List[Dict[str, Any]] = []
    if raw_workflow:
        definition_valid, validation_errors = validate_workflow(raw_workflow)
        if definition_valid:
            ordered_nodes = linearize_workflow_nodes(raw_workflow)

    raw_nodes = raw_workflow.get("nodes", []) if isinstance(raw_workflow.get("nodes"), list) else []
    node_source = ordered_nodes if ordered_nodes else [item for item in raw_nodes if isinstance(item, dict)]
    nodes_payload = [
        {
            "id": str(item.get("id", "")).strip(),
            "type": str(item.get("type", "")).strip(),
            "title": str(item.get("title", "")).strip(),
        }
        for item in node_source
    ]

    resolved_workflow_id = str(raw_workflow.get("workflow_id", "")).strip()
    runtime = {
        "requested_workflow_id": requested_workflow_id,
        "resolved_workflow_id": resolved_workflow_id,
        "workflow_name": str(raw_workflow.get("name", "")).strip(),
        "entry_node_id": str(raw_workflow.get("entry_node_id", "")).strip(),
        "default_workflow_id": default_id,
        "resolution_source": str(selection.source or "").strip(),
        "selection_warning": str(selection.warning or "").strip(),
        "definition_available": bool(raw_workflow),
        "definition_valid": definition_valid,
        "validation_errors": validation_errors,
        "uses_fixed_pipeline": not bool(raw_workflow) or not definition_valid,
        "nodes": nodes_payload,
    }
    return runtime, raw_workflow if definition_valid else {}, ordered_nodes


def _resolve_job_workflow_definition(job: JobRecord) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    """Resolve one job to the active workflow definition and ordered nodes."""

    runtime, workflow, ordered_nodes = _resolve_job_workflow_runtime(job)
    return str(runtime.get("resolved_workflow_id", "")).strip(), workflow, ordered_nodes


def _extract_workflow_fallback_events(events: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Extract workflow resolution/fallback signals from parsed debug events."""

    fallback_events: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        message = str(event.get("message", "")).strip()
        if not message:
            continue

        payload: Dict[str, Any] | None = None
        warning_match = re.match(r"^Workflow resolution warning:\s*(.+)$", message)
        if warning_match:
            payload = {
                "kind": "resolution_warning",
                "severity": "warn",
                "title": "선택 경고",
                "message": str(warning_match.group(1)).strip(),
                "uses_fixed_pipeline": False,
            }

        default_match = re.match(
            r"^Resolved workflow '([^']+)' missing\. Falling back to default '([^']+)'\.$",
            message,
        )
        if default_match:
            payload = {
                "kind": "default_fallback",
                "severity": "warn",
                "title": "기본 workflow로 전환",
                "message": (
                    f"등록되지 않은 workflow '{default_match.group(1)}' 대신 "
                    f"'{default_match.group(2)}'를 사용했습니다."
                ),
                "uses_fixed_pipeline": False,
            }

        validation_match = re.match(
            r"^Workflow validation failed; fallback to fixed pipeline:\s*(.+)$",
            message,
        )
        if validation_match:
            payload = {
                "kind": "validation_failure",
                "severity": "error",
                "title": "Workflow validation 실패",
                "message": str(validation_match.group(1)).strip(),
                "uses_fixed_pipeline": True,
            }

        load_match = re.match(
            r"^Workflow load failed; fallback to fixed pipeline:\s*(.+)$",
            message,
        )
        if load_match:
            payload = {
                "kind": "load_failure",
                "severity": "error",
                "title": "Workflow 로드 실패",
                "message": str(load_match.group(1)).strip(),
                "uses_fixed_pipeline": True,
            }

        if payload is None:
            continue

        dedupe_key = (str(payload.get("kind", "")), str(payload.get("message", "")))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        payload["timestamp"] = str(event.get("timestamp", "")).strip()
        fallback_events.append(payload)
    return fallback_events


def _compute_job_resume_state(
    job: JobRecord,
    node_runs: List[Any],
    settings: AppSettings,
) -> Dict[str, Any]:
    """Predict resume mode for the current or next execution attempt."""

    if job.status == JobStatus.DONE.value:
        return {
            "enabled": False,
            "mode": "none",
            "reason_code": "job_completed",
            "reason": "작업이 완료되어 재개 대상이 아닙니다.",
            "current_attempt": int(job.attempt or 0),
            "source_attempt": int(job.attempt or 0),
            "failed_node_id": "",
            "failed_node_type": "",
            "failed_node_title": "",
            "resume_from_node_id": "",
            "resume_from_node_type": "",
            "resume_from_node_title": "",
            "resume_from_index": 0,
            "skipped_nodes": [],
        }

    workflow_id, workflow, ordered_nodes = _resolve_job_workflow_definition(job)
    if not workflow:
        return {
            "enabled": False,
            "mode": "none",
            "reason_code": "workflow_unavailable",
            "reason": "워크플로우를 찾지 못해 재개 전략을 계산할 수 없습니다.",
            "current_attempt": int(job.attempt or 0),
            "source_attempt": 0,
            "failed_node_id": "",
            "failed_node_type": "",
            "failed_node_title": "",
            "resume_from_node_id": "",
            "resume_from_node_type": "",
            "resume_from_node_title": "",
            "resume_from_index": 0,
            "skipped_nodes": [],
        }

    workspace_path = _job_workspace_path(job, settings)
    improvement_runtime = read_improvement_runtime_context(
        build_workflow_artifact_paths(workspace_path)
    )
    prospective_attempt = max(1, int(job.attempt or 0))
    if job.status in {JobStatus.FAILED.value, JobStatus.QUEUED.value}:
        prospective_attempt = max(1, prospective_attempt + 1)

    return compute_workflow_resume_state(
        workflow_id=workflow_id,
        ordered_nodes=ordered_nodes,
        node_runs=node_runs,
        current_attempt=prospective_attempt,
        strategy=str(improvement_runtime.get("strategy", "")).strip(),
        scope_restriction=str(improvement_runtime.get("scope_restriction", "")).strip(),
        manual_mode=str(job.manual_resume_mode or "").strip(),
        manual_node_id=str(job.manual_resume_node_id or "").strip(),
        manual_note=str(job.manual_resume_note or "").strip(),
    )


def _build_manual_retry_options(
    job: JobRecord,
    *,
    settings: AppSettings,
    node_runs: List[Any],
) -> Dict[str, Any]:
    """Return dashboard-safe manual resume/rerun options for one job."""

    workflow_id, _, ordered_nodes = _resolve_job_workflow_definition(job)
    resume_state = _compute_job_resume_state(job, node_runs, settings)
    safe_nodes = list_manual_resume_candidates(ordered_nodes)
    failed_node_id = str(resume_state.get("failed_node_id", "")).strip()
    can_resume_failed = any(str(item.get("id", "")).strip() == failed_node_id for item in safe_nodes)
    return {
        "workflow_id": workflow_id,
        "safe_nodes": safe_nodes,
        "can_manual_retry": job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value},
        "can_resume_failed_node": can_resume_failed,
        "failed_node_id": failed_node_id,
        "default_mode": "resume_failed_node" if can_resume_failed else "full_rerun",
    }


def _load_workflows_catalog() -> tuple[str, Dict[str, Dict[str, Any]]]:
    """Read workflow catalog with a dashboard-safe fallback."""

    payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    default_workflow_id = str(payload.get("default_workflow_id", "")).strip()
    if not default_workflow_id:
        default_workflow_id = default_workflow_template()["workflow_id"]

    workflows_by_id: Dict[str, Dict[str, Any]] = {}
    raw_workflows = payload.get("workflows", [])
    if isinstance(raw_workflows, list):
        for item in raw_workflows:
            if not isinstance(item, dict):
                continue
            workflow_id = str(item.get("workflow_id", "")).strip()
            if workflow_id:
                workflows_by_id[workflow_id] = item
    return default_workflow_id, workflows_by_id


def _read_agent_md_files(workspace_path: Path) -> List[Dict[str, str]]:
    """Read .md files generated by agents in the workspace."""

    if not workspace_path.exists():
        return []

    md_files = []
    md_paths: List[Path] = []
    md_paths.extend(sorted(workspace_path.glob("*.md")))
    docs_dir = workspace_path / "_docs"
    if docs_dir.exists():
        md_paths.extend(sorted(docs_dir.glob("*.md")))
    seen = set()
    for path in md_paths:
        if not path.is_file():
            continue
        rel = str(path.relative_to(workspace_path))
        if rel in seen:
            continue
        seen.add(rel)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            md_files.append({
                "name": rel,
                "content": content
            })
        except Exception:
            continue
    
    # 파일명 순으로 정렬
    md_files.sort(key=lambda x: x["name"])
    return md_files


def _read_stage_md_snapshots(data_dir: Path, job_id: str) -> List[Dict[str, Any]]:
    """Read per-stage markdown snapshots saved by orchestrator."""

    snapshot_root = (data_dir / "md_snapshots" / job_id).resolve()
    base_root = (data_dir / "md_snapshots").resolve()
    if not snapshot_root.exists():
        return []
    if base_root not in snapshot_root.parents and snapshot_root != base_root:
        return []

    snapshots: List[Dict[str, Any]] = []
    for path in sorted(snapshot_root.glob("attempt_*.json")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        snapshots.append(
            {
                "attempt": int(payload.get("attempt", 0) or 0),
                "stage": str(payload.get("stage", "")),
                "created_at": str(payload.get("created_at", "")),
                "changed_files": payload.get("changed_files", []),
                "changed_files_all": payload.get("changed_files_all", []),
                "md_files": payload.get("md_files", []),
                "file_snapshots": payload.get("file_snapshots", []),
            }
        )

    snapshots.sort(key=lambda item: (item.get("attempt", 0), item.get("stage", "")))
    return snapshots


@router.post("/api/jobs/{job_id}/stop", response_class=JSONResponse)
def request_job_stop(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Request graceful stop for one running ultra job."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        raise HTTPException(status_code=400, detail="실행 중 작업에서만 정지 요청할 수 있습니다.")

    path = _stop_signal_path(settings.data_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop\n", encoding="utf-8")
    return JSONResponse({"requested": True, "job_id": job_id, "stop_file": str(path)})


@router.post("/api/jobs/{job_id}/requeue", response_class=JSONResponse)
def requeue_job(
    job_id: str,
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Requeue one failed job from dashboard."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        return JSONResponse({"requeued": False, "reason": "already_active", "job_id": job_id})
    if job.status != JobStatus.FAILED.value:
        raise HTTPException(status_code=400, detail="실패 상태 작업만 재큐잉할 수 있습니다.")

    store.update_job(
        job_id,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        error_message=None,
        started_at=None,
        finished_at=None,
        heartbeat_at=None,
        manual_resume_mode="",
        manual_resume_node_id="",
        manual_resume_requested_at=None,
        manual_resume_note="",
    )
    store.enqueue_job(job_id)
    return JSONResponse({"requeued": True, "job_id": job_id})


@router.post("/api/jobs/{job_id}/dead-letter/retry", response_class=JSONResponse)
def retry_dead_letter_job(
    job_id: str,
    payload: DeadLetterRetryRequest | None = None,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Requeue one dead-lettered job with an explicit operator action trace."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        raise HTTPException(status_code=400, detail="대기 또는 실행 중 작업은 dead-letter 재시도를 할 수 없습니다.")
    if job.status != JobStatus.FAILED.value or str(job.recovery_status or "").strip() != "dead_letter":
        raise HTTPException(status_code=400, detail="dead-letter 상태의 실패 작업만 다시 큐에 넣을 수 있습니다.")

    note = str(payload.note or "").strip() if payload else ""
    previous_reason = str(job.recovery_reason or job.error_message or "").strip()
    retry_reason = note or (
        f"운영자가 dead-letter 작업을 다시 큐에 넣었습니다. 이전 사유: {previous_reason}"
        if previous_reason
        else "운영자가 dead-letter 작업을 다시 큐에 넣었습니다."
    )

    store.update_job(
        job_id,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        error_message=None,
        started_at=None,
        finished_at=None,
        heartbeat_at=None,
        recovery_status="dead_letter_requeued",
        recovery_reason=retry_reason,
        recovery_count=0,
        last_recovered_at=utc_now_iso(),
        manual_resume_mode="",
        manual_resume_node_id="",
        manual_resume_requested_at=None,
        manual_resume_note="",
    )
    store.enqueue_job(job_id)

    updated = store.get_job(job_id)
    assert updated is not None
    append_runtime_recovery_trace_for_job(
        settings,
        updated,
        source="dashboard_dead_letter_retry",
        reason_code="dead_letter_retry",
        reason=retry_reason,
        decision="retry_from_dead_letter",
        recovery_status="dead_letter_requeued",
        recovery_count=int(updated.recovery_count or 0),
        details={
            "previous_recovery_status": "dead_letter",
            "previous_reason": previous_reason,
            "operator_note": note,
            "retry_from_scratch": True,
        },
    )
    return JSONResponse(
        {
            "queued": True,
            "job_id": job_id,
            "recovery_status": "dead_letter_requeued",
            "reason": retry_reason,
        }
    )


@router.post("/api/jobs/{job_id}/workflow/manual-retry", response_class=JSONResponse)
def manual_retry_workflow_job(
    job_id: str,
    payload: WorkflowManualRetryRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Queue one failed/completed job with an explicit manual rerun/resume policy."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        raise HTTPException(status_code=400, detail="대기 또는 실행 중 작업에는 수동 재개를 설정할 수 없습니다.")

    requested_mode = str(payload.mode or "").strip().lower()
    if requested_mode not in {"full_rerun", "resume_failed_node", "resume_from_node"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 수동 재개 모드입니다.")

    workflow_id, _, ordered_nodes = _resolve_job_workflow_definition(job)
    if not workflow_id or not ordered_nodes:
        raise HTTPException(status_code=400, detail="워크플로우를 찾지 못해 수동 재개를 설정할 수 없습니다.")

    selected_node_id = ""
    selected_reason = ""
    if requested_mode == "resume_failed_node":
        current_resume_state = _compute_job_resume_state(job, store.list_node_runs(job_id), settings)
        selected_node_id = str(current_resume_state.get("failed_node_id", "")).strip()
        validation = validate_manual_resume_target(
            ordered_nodes=ordered_nodes,
            node_id=selected_node_id,
        )
        if not validation.get("valid"):
            raise HTTPException(
                status_code=400,
                detail=str(validation.get("reason", "실패 노드에서 수동 재개할 수 없습니다.")),
            )
        selected_reason = str(current_resume_state.get("reason", "")).strip()
    elif requested_mode == "resume_from_node":
        selected_node_id = str(payload.node_id or "").strip()
        validation = validate_manual_resume_target(
            ordered_nodes=ordered_nodes,
            node_id=selected_node_id,
        )
        if not validation.get("valid"):
            raise HTTPException(
                status_code=400,
                detail=str(validation.get("reason", "선택한 노드에서 수동 재개할 수 없습니다.")),
            )
        selected_reason = str(validation.get("reason", "")).strip()
    else:
        selected_reason = "운영자가 전체 재실행을 지정했습니다."

    next_attempt = max(1, int(job.attempt or 0) + 1)
    next_max_attempts = max(int(job.max_attempts or 1), next_attempt)
    note = str(payload.note or "").strip()
    recovery_status = "manual_rerun_queued" if requested_mode == "full_rerun" else "manual_resume_queued"
    recovery_reason = note or selected_reason or (
        "운영자가 전체 재실행을 지정했습니다."
        if requested_mode == "full_rerun"
        else "운영자가 수동 재개를 지정했습니다."
    )
    store.update_job(
        job_id,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        max_attempts=next_max_attempts,
        error_message=None,
        started_at=None,
        finished_at=None,
        heartbeat_at=None,
        recovery_status=recovery_status,
        recovery_reason=recovery_reason,
        manual_resume_mode=requested_mode,
        manual_resume_node_id=selected_node_id,
        manual_resume_requested_at=utc_now_iso(),
        manual_resume_note=note,
    )
    store.enqueue_job(job_id)

    updated = store.get_job(job_id)
    assert updated is not None
    trace_decision = "manual_rerun_requeue" if requested_mode == "full_rerun" else "manual_resume_requeue"
    trace_reason_code = "manual_rerun_requeue" if requested_mode == "full_rerun" else "manual_resume_requeue"
    append_runtime_recovery_trace_for_job(
        settings,
        updated,
        source="dashboard_manual_retry",
        reason_code=trace_reason_code,
        reason=recovery_reason,
        decision=trace_decision,
        recovery_status=recovery_status,
        recovery_count=int(updated.recovery_count or 0),
        details={
            "previous_recovery_status": str(job.recovery_status or "").strip(),
            "previous_reason": str(job.recovery_reason or job.error_message or "").strip(),
            "operator_note": note,
            "target_node_id": selected_node_id,
            "retry_from_scratch": requested_mode == "full_rerun",
        },
    )
    node_runs = store.list_node_runs(job_id)
    resume_state = _compute_job_resume_state(updated, node_runs, settings)
    return JSONResponse(
        {
            "queued": True,
            "job_id": job_id,
            "workflow_id": workflow_id,
            "mode": requested_mode,
            "target_node_id": selected_node_id,
            "next_attempt": next_attempt,
            "resume_state": resume_state,
        }
    )


@router.post("/api/jobs/requeue-failed", response_class=JSONResponse)
def requeue_failed_jobs(
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Requeue all failed jobs in one action."""

    jobs = store.list_jobs()
    failed_job_ids = [job.job_id for job in jobs if job.status == JobStatus.FAILED.value]
    for job_id in failed_job_ids:
        store.update_job(
            job_id,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            attempt=0,
            error_message=None,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            manual_resume_mode="",
            manual_resume_node_id="",
            manual_resume_requested_at=None,
            manual_resume_note="",
        )
        store.enqueue_job(job_id)
    return JSONResponse({"requeued": len(failed_job_ids)})


@router.get("/logs/{file_name}", response_class=PlainTextResponse)
def job_log_file(
    file_name: str,
    channel: str = Query(default="debug"),
    settings: AppSettings = Depends(get_settings),
) -> PlainTextResponse:
    """Serve one log file as plain text.

    The file name is strictly validated to block path traversal such as `../`.
    """

    if not _LOG_NAME_PATTERN.match(file_name):
        raise HTTPException(
            status_code=400,
            detail="Invalid log file name. Use only letters, numbers, dot, dash, underscore.",
        )

    target_path = _resolve_channel_log_path(settings, file_name, channel=channel)

    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {file_name}")

    return PlainTextResponse(target_path.read_text(encoding="utf-8"))


def _resolve_channel_log_path(settings: AppSettings, file_name: str, channel: str = "debug") -> Path:
    """Resolve one job log path by channel with legacy fallback for debug."""

    normalized_channel = (channel or "debug").strip().lower()
    if normalized_channel not in {"debug", "user"}:
        normalized_channel = "debug"
    logs_dir = settings.logs_dir.resolve()
    channel_path = (logs_dir / normalized_channel / file_name).resolve()
    legacy_path = (logs_dir / file_name).resolve()

    if logs_dir not in channel_path.parents and channel_path != logs_dir:
        raise HTTPException(status_code=400, detail="Invalid log file path.")
    if logs_dir not in legacy_path.parents and legacy_path != logs_dir:
        raise HTTPException(status_code=400, detail="Invalid legacy log file path.")

    if normalized_channel == "debug" and not channel_path.exists() and legacy_path.exists():
        return legacy_path
    return channel_path


def _parse_log_events(log_path: Path) -> List[Dict[str, str]]:
    """Parse log lines into timeline events with inferred speaker and receiver."""

    events: List[Dict[str, str]] = []
    raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    last_timestamp = ""
    active_target = "shell"

    for raw_line in raw_lines:
        match = _TIMESTAMPED_LINE_PATTERN.match(raw_line)
        if not match:
            if events:
                events[-1]["message"] += "\n" + raw_line
            continue

        timestamp = match.group("ts")
        message = match.group("msg")
        last_timestamp = timestamp

        if message.startswith("[RUN] "):
            command = message.replace("[RUN] ", "", 1)
            active_target = _classify_command_target(command)
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": "agenthub",
                    "receiver": active_target,
                    "kind": "run",
                    "message": command,
                }
            )
            continue

        if message.startswith("[STDOUT]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "stdout",
                    "message": message.replace("[STDOUT]", "", 1).strip() or "(stdout)",
                }
            )
            continue

        if message.startswith("[STDERR]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "stderr",
                    "message": message.replace("[STDERR]", "", 1).strip() or "(stderr)",
                }
            )
            continue

        if message.startswith("[STAGE] "):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": "agenthub",
                    "receiver": "dashboard",
                    "kind": "stage",
                    "message": message.replace("[STAGE] ", "", 1),
                }
            )
            continue

        if message.startswith("[DONE]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "done",
                    "message": message,
                }
            )
            continue

        events.append(
            {
                "timestamp": last_timestamp,
                "speaker": "agenthub",
                "receiver": "dashboard",
                "kind": "info",
                "message": message,
            }
        )

    return events[-300:]


def _classify_command_target(command: str) -> str:
    """Infer command target actor for conversation-style timeline."""

    lowered = command.lower()
    if "plann" in lowered and "gemini" in lowered:
        return "planner"
    if "review" in lowered and "gemini" in lowered:
        return "reviewer"
    if "codex" in lowered:
        return "coder"
    if lowered.startswith("gh "):
        return "github"
    if lowered.startswith("git ") or " git " in lowered:
        return "git"
    return "shell"


def _extract_issue_number(issue_url: str) -> int:
    """Extract issue number from GitHub issue URL."""

    match = _ISSUE_NUMBER_PATTERN.search(issue_url)
    if match is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "이슈 URL에서 번호를 읽지 못했습니다. "
                "gh CLI 출력 형식을 확인해주세요."
            ),
        )
    return int(match.group("number"))


def _extract_issue_url(stdout: str) -> str:
    """Extract issue URL from gh output text."""

    match = _ISSUE_URL_PATTERN.search(stdout)
    if match is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "이슈 생성 결과에서 URL을 읽지 못했습니다. "
                "gh CLI 출력 형식을 확인해주세요."
            ),
        )
    return match.group(0)


def _run_gh_command(args: List[str], error_context: str) -> str:
    """Run gh command with consistent error mapping."""

    process = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        stderr_preview = (process.stderr or "").strip()[:500]
        raise HTTPException(
            status_code=502,
            detail=(
                f"{error_context} 실패: gh CLI 상태를 확인해주세요. "
                f"stderr: {stderr_preview or '(no stderr)'}"
            ),
        )
    return process.stdout


def _build_agent_observability_context(store: JobStore, settings: AppSettings) -> str:
    """Build compact runtime context for diagnosis-focused assistant responses."""

    jobs = store.list_jobs()
    if not jobs:
        return "No jobs found."

    sorted_jobs = sorted(jobs, key=lambda item: item.updated_at or "", reverse=True)
    queued = sum(1 for item in jobs if item.status == JobStatus.QUEUED.value)
    running = sum(1 for item in jobs if item.status == JobStatus.RUNNING.value)
    done = sum(1 for item in jobs if item.status == JobStatus.DONE.value)
    failed = sum(1 for item in jobs if item.status == JobStatus.FAILED.value)

    lines: List[str] = []
    lines.append(f"Job summary: total={len(jobs)}, queued={queued}, running={running}, done={done}, failed={failed}")

    recent_running = [item for item in sorted_jobs if item.status == JobStatus.RUNNING.value][:3]
    if recent_running:
        lines.append("Running jobs:")
        for item in recent_running:
            lines.append(
                f"- {item.job_id} app={item.app_code} track={item.track} "
                f"stage={item.stage} attempt={item.attempt}/{item.max_attempts} updated={item.updated_at}"
            )

    recent_failed = [item for item in sorted_jobs if item.status == JobStatus.FAILED.value][:3]
    if recent_failed:
        lines.append("Recent failed jobs:")
        for item in recent_failed:
            lines.append(
                f"- {item.job_id} app={item.app_code} track={item.track} "
                f"stage={item.stage} error={item.error_message or '-'} updated={item.updated_at}"
            )
            log_path = _resolve_channel_log_path(settings, item.log_file, channel="debug")
            if log_path.exists():
                lines.append(f"  log_tail({item.log_file}):")
                lines.extend([f"    {row}" for row in _tail_text_lines(log_path, max_lines=16)])

    recent_any = sorted_jobs[:3]
    lines.append("Recent jobs:")
    for item in recent_any:
        lines.append(
            f"- {item.job_id} status={item.status} stage={item.stage} "
            f"app={item.app_code} track={item.track} updated={item.updated_at}"
        )

    text = "\n".join(lines).strip()
    if len(text) > 14000:
        return text[:14000] + "\n...(truncated)"
    return text


def _build_focus_job_log_context(job: JobRecord, settings: AppSettings) -> str:
    """Build detailed context for one specific job log analysis."""

    lines: List[str] = [
        "Focused job:",
        (
            f"- job_id={job.job_id} status={job.status} stage={job.stage} "
            f"app={job.app_code} track={job.track} attempt={job.attempt}/{job.max_attempts}"
        ),
        f"- issue=#{job.issue_number} title={job.issue_title}",
        f"- error={job.error_message or '-'}",
    ]

    for channel in ("debug", "user"):
        log_path = _resolve_channel_log_path(settings, job.log_file, channel=channel)
        if not log_path.exists():
            continue
        lines.append(f"{channel} log tail ({log_path.name}):")
        for row in _tail_text_lines(log_path, max_lines=120):
            lines.append(f"  {row}")
    text = "\n".join(lines).strip()
    if len(text) > 20000:
        return text[:20000] + "\n...(truncated)"
    return text


def _assistant_tool_docs_file(repository_path: Path, name: str) -> Path:
    """Return assistant diagnosis tool artifact path under one workspace."""

    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return docs_dir / f"ASSISTANT_{name}"


def _derive_assistant_diagnosis_queries(
    *,
    job: JobRecord,
    question: str,
    settings: AppSettings,
) -> Dict[str, str]:
    """Build small diagnosis-oriented queries for internal tool loop."""

    debug_log_path = _resolve_channel_log_path(settings, job.log_file, channel="debug")
    events = _parse_log_events(debug_log_path) if debug_log_path.exists() else []
    latest_command = ""
    latest_error = ""
    for event in reversed(events):
        kind = str(event.get("kind", "")).strip().lower()
        if not latest_command and kind == "run":
            latest_command = str(event.get("message", "")).strip()
        if not latest_error and kind in {"stderr", "done"}:
            latest_error = str(event.get("message", "")).strip()
        if latest_command and latest_error:
            break

    def _collapse(*parts: str) -> str:
        ordered: List[str] = []
        for raw in parts:
            value = str(raw or "").strip()
            if not value or value in ordered:
                continue
            ordered.append(value)
        return " ".join(ordered)[:240]

    base_error = str(job.error_message or "").strip()
    base_stage = str(job.stage or "").strip()
    issue_title = str(job.issue_title or "").strip()
    return {
        "log_lookup": _collapse(question, base_error, base_stage, latest_error, latest_command),
        "repo_search": _collapse(base_stage, base_error, latest_command, issue_title),
        "memory_search": _collapse(base_error, base_stage, issue_title, latest_error),
    }


def _build_assistant_diagnosis_runtime(settings: AppSettings) -> ToolRuntime:
    """Build one minimal internal tool runtime for assistant diagnosis loops."""

    runtime_store = _get_memory_runtime_store(settings)
    return ToolRuntime(
        command_templates=None,
        docs_file=_assistant_tool_docs_file,
        build_template_variables=lambda *_args, **_kwargs: {},
        template_for_route=lambda route_name: route_name,
        actor_log_writer=lambda *_args, **_kwargs: None,
        append_actor_log=lambda *_args, **_kwargs: None,
        build_local_evidence_fallback=lambda *_args, **_kwargs: {"context_text": ""},
        search_memory_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
        search_vector_memory_entries=lambda **_kwargs: {},
        feature_enabled=lambda _flag_name: False,
    )


def _run_assistant_diagnosis_loop(
    *,
    job: JobRecord,
    question: str,
    settings: AppSettings,
    assistant_scope: str = "log_analysis",
) -> Dict[str, Any]:
    """Run one small internal tool diagnosis loop and write a trace artifact."""

    feature_flags = read_feature_flags(_FEATURE_FLAGS_CONFIG_PATH)
    if not bool(feature_flags.get("assistant_diagnosis_loop")):
        return {"enabled": False, "tool_runs": []}

    repository_path = _job_workspace_path(job, settings)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    log_path = _resolve_channel_log_path(settings, job.log_file, channel="debug")
    runtime = _build_assistant_diagnosis_runtime(settings)
    queries = _derive_assistant_diagnosis_queries(job=job, question=question, settings=settings)
    tool_runs: List[Dict[str, Any]] = []
    context_sections: List[str] = []

    for tool_name in ("log_lookup", "repo_search", "memory_search"):
        query = str(queries.get(tool_name, "")).strip()
        if not query:
            continue
        request = ToolRequest(tool=tool_name, query=query, reason="assistant diagnosis loop")
        try:
            result = runtime.execute(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                request=request,
            )
            tool_runs.append(
                {
                    "tool": tool_name,
                    "query": query,
                    "ok": result.ok,
                    "mode": result.mode,
                    "context_path": result.context_path,
                    "result_path": result.result_path,
                    "error": result.error,
                }
            )
            if result.context_text:
                context_sections.append(f"[{tool_name}]\n{result.context_text.strip()}")
        except Exception as error:  # noqa: BLE001
            tool_runs.append(
                {
                    "tool": tool_name,
                    "query": query,
                    "ok": False,
                    "mode": "error",
                    "context_path": "",
                    "result_path": "",
                    "error": str(error),
                }
            )

    combined_context = "\n\n".join(section for section in context_sections if section).strip()
    trace_payload = {
        "generated_at": utc_now_iso(),
        "enabled": True,
        "job_id": job.job_id,
        "assistant_scope": str(assistant_scope or "log_analysis").strip() or "log_analysis",
        "question": question,
        "tool_runs": tool_runs,
        "combined_context_length": len(combined_context),
    }
    trace_path = repository_path / "_docs" / "ASSISTANT_DIAGNOSIS_TRACE.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        **trace_payload,
        "trace_path": str(trace_path),
        "context_text": combined_context[:20_000],
    }


def _run_log_analyzer(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one log-analysis request while keeping dashboard monkeypatch points stable."""

    if assistant == "codex":
        return _run_codex_log_analysis(prompt, templates)
    if assistant == "gemini":
        return _run_gemini_log_analysis(prompt, templates)
    raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")


def _run_assistant_chat_provider(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one chat request while keeping dashboard monkeypatch points stable."""

    if assistant == "codex":
        return _run_codex_chat_completion(prompt, templates)
    if assistant == "gemini":
        return _run_gemini_chat_completion(prompt, templates)
    raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")


def _run_codex_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return assistant_runtime.run_codex_chat_completion(prompt, templates)


def _run_gemini_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return assistant_runtime.run_gemini_chat_completion(prompt, templates)


def _run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return assistant_runtime.run_codex_log_analysis(prompt, templates)


def _run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return assistant_runtime.run_gemini_log_analysis(prompt, templates)


def _run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Claude alias maintained for compatibility and routed to Codex."""

    return assistant_runtime.run_claude_log_analysis(prompt, templates)


def _run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Copilot alias maintained for compatibility and routed to Codex."""

    return assistant_runtime.run_copilot_log_analysis(prompt, templates)


def _tail_text_lines(path: Path, max_lines: int = 16) -> List[str]:
    """Read the tail lines of a UTF-8 text file safely."""

    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(failed to read log file)"]
    tail = rows[-max_lines:] if len(rows) > max_lines else rows
    return [row[:300] for row in tail]

def _ensure_agent_run_label(repository: str) -> None:
    """Ensure `agent:run` label exists in the target repository."""

    _ensure_label(
        repository=repository,
        label_name="agent:run",
        color="1D76DB",
        description="Trigger AgentHub worker",
    )


def _ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
    """Ensure one GitHub label exists in the target repository."""

    process = subprocess.run(
        [
            "gh",
            "label",
            "create",
            label_name,
            "--repo",
            repository,
            "--color",
            color,
            "--description",
            description,
        ],
        capture_output=True,
        text=True,
    )

    if process.returncode == 0:
        return

    stderr_lower = (process.stderr or "").lower()
    if "already exists" in stderr_lower or "name already exists" in stderr_lower:
        return

    stderr_preview = (process.stderr or "").strip()[:500]
    raise HTTPException(
        status_code=502,
        detail=(
            f"{label_name} 라벨 자동 생성 실패: gh CLI 상태를 확인해주세요. "
            f"stderr: {stderr_preview or '(no stderr)'}"
        ),
    )


def _normalize_app_code(value: str) -> str:
    """Normalize app code for labels and branch/workspace names."""

    lowered = (value or "").strip().lower()
    if not lowered:
        return ""
    if not _APP_CODE_PATTERN.match(lowered):
        return ""
    return lowered


def _normalize_repository_ref(value: str) -> str:
    """Normalize GitHub repository input to owner/repo form."""

    raw = (value or "").strip()
    if not raw:
        return ""
    https_match = re.match(r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", raw, re.IGNORECASE)
    if https_match:
        return f"{https_match.group(1)}/{https_match.group(2)}"
    ssh_match = re.match(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$", raw, re.IGNORECASE)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"
    plain_match = re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", raw)
    if plain_match:
        return raw
    return ""


def _normalize_track(value: str) -> str:
    """Normalize track value to one of known choices."""

    lowered = (value or "").strip().lower()
    if lowered in {"ultra10", "ultra-10", "초초장기"}:
        lowered = "ultra10"
    if lowered in {"ultra", "초장기"}:
        lowered = "ultra"
    if lowered in {"longterm", "long-term", "장기"}:
        lowered = "long"
    if lowered in _TRACK_CHOICES:
        return lowered
    return "enhance"


def _detect_title_track(title: str) -> str:
    """Detect explicit title marker track override."""

    lowered = (title or "").strip().lower()
    if "[초초장기]" in lowered or "[ultra10]" in lowered:
        return "ultra10"
    if "[초장기]" in lowered or "[ultra]" in lowered:
        return "ultra"
    if "[장기]" in lowered or "[long]" in lowered:
        return "long"
    return ""


def _build_branch_name(
    app_code: str,
    issue_number: int,
    track: str,
    job_id: str,
    keep_branch: bool = True,
    requested_branch_name: str = "",
) -> str:
    """Build namespaced branch name for one job.

    `enhance` track reuses one stable issue branch so iterative jobs can build
    on previous commits.
    """

    custom = _sanitize_branch_name(requested_branch_name)
    if custom:
        return custom
    if keep_branch:
        return f"agenthub/{app_code}/issue-{issue_number}"
    if track == "enhance":
        return f"agenthub/{app_code}/issue-{issue_number}-enhance"
    return f"agenthub/{app_code}/issue-{issue_number}-{job_id[:8]}"


def _sanitize_branch_name(value: str) -> str:
    """Best-effort sanitize for user-provided branch names."""

    name = (value or "").strip()
    if not name:
        return ""
    allowed = re.sub(r"[^a-zA-Z0-9/_-]", "-", name)
    collapsed = re.sub(r"/{2,}", "/", allowed).strip("/ ")
    if not collapsed:
        return ""
    return collapsed[:120]


def _build_log_file_name(app_code: str, job_id: str) -> str:
    """Build one safe log file name."""

    return f"{app_code}--{job_id}.log"


def _stop_signal_path(data_dir: Path, job_id: str) -> Path:
    """Return stop signal file path for one job."""

    return data_dir / "control" / f"stop_{job_id}.flag"


def _read_registered_apps(
    path: Path,
    repository: str,
    default_workflow_id: str = "",
) -> List[Dict[str, str]]:
    """Read app registration list from JSON file with a default fallback."""

    return _shared_read_registered_apps(path, repository, default_workflow_id=default_workflow_id)


def _write_registered_apps(path: Path, apps: List[Dict[str, str]]) -> None:
    """Persist app list as pretty JSON."""

    _shared_write_registered_apps(path, apps)


def _read_default_workflow_id(path: Path) -> str:
    """Read default workflow id from workflow config with safe fallback."""

    return _shared_read_default_workflow_id(path)


def _find_active_job(
    store: JobStore,
    repository: str,
    issue_number: int,
) -> Optional[JobRecord]:
    """Find an already-active job for the same repository issue."""

    for item in store.list_jobs():
        if item.repository != repository:
            continue
        if item.issue_number != issue_number:
            continue
        if item.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            return item
    return None


def _queue_followup_job_from_backlog_candidate(
    *,
    candidate: Dict[str, Any],
    runtime_store: MemoryRuntimeStore,
    store: JobStore,
    settings: AppSettings,
    note: str,
) -> tuple[JobRecord, Path]:
    """Queue one follow-up job that consumes an approved backlog candidate."""

    payload = candidate.get("payload", {}) if isinstance(candidate.get("payload"), dict) else {}
    source_job_id = str(payload.get("job_id", "")).strip()
    source_job = store.get_job(source_job_id) if source_job_id else None
    candidate_id = str(candidate.get("candidate_id", "")).strip()

    repository = str(candidate.get("repository", "")).strip() or settings.allowed_repository
    execution_repository = (
        str(candidate.get("execution_repository", "")).strip()
        or (str(source_job.source_repository or "").strip() if source_job is not None else "")
        or (str(source_job.repository or "").strip() if source_job is not None else "")
        or repository
    )
    source_repository = execution_repository if execution_repository and execution_repository != repository else ""
    app_code = _normalize_app_code(
        str(candidate.get("app_code", "")).strip() or (source_job.app_code if source_job is not None else "default")
    ) or "default"

    issue_number = int(payload.get("issue_number", 0) or (source_job.issue_number if source_job is not None else 0) or 0)
    if issue_number <= 0:
        raise HTTPException(
            status_code=400,
            detail="현재 follow-up bridge는 기존 GitHub issue에 연결된 backlog 후보만 큐잉할 수 있습니다.",
        )
    issue_url = (
        str(source_job.issue_url or "").strip()
        if source_job is not None
        else f"https://github.com/{repository}/issues/{issue_number}"
    )
    if not issue_url:
        issue_url = f"https://github.com/{repository}/issues/{issue_number}"

    workflow_id = str(candidate.get("workflow_id", "")).strip() or (
        str(source_job.workflow_id or "").strip() if source_job is not None else ""
    )
    if not workflow_id:
        selection = resolve_workflow_selection(
            requested_workflow_id="",
            app_code=app_code,
            repository=repository,
            apps_path=_APPS_CONFIG_PATH,
            workflows_path=_WORKFLOWS_CONFIG_PATH,
        )
        workflow_id = selection.workflow_id

    track = "enhance"
    if source_job is not None:
        source_track = _normalize_track(source_job.track)
        if source_track in {"bug", "new", "enhance"}:
            track = source_track

    now = utc_now_iso()
    queued_job_id = str(uuid.uuid4())
    followup_title = f"[Follow-up] {str(candidate.get('title', '')).strip() or f'Issue {issue_number} improvement'}"
    queued_job = JobRecord(
        job_id=queued_job_id,
        repository=repository,
        issue_number=issue_number,
        issue_title=followup_title,
        issue_url=issue_url,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=settings.max_retries,
        branch_name=_build_branch_name(
            app_code,
            issue_number,
            track,
            queued_job_id,
            keep_branch=True,
        ),
        pr_url=None,
        error_message=None,
        log_file=_build_log_file_name(app_code, queued_job_id),
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
        workflow_id=workflow_id,
        source_repository=source_repository,
        job_kind="followup_backlog",
        parent_job_id=source_job_id,
        backlog_candidate_id=candidate_id,
    )
    store.create_job(queued_job)
    store.enqueue_job(queued_job_id)

    artifact_path = _write_followup_backlog_artifact(
        candidate=candidate,
        queued_job=queued_job,
        settings=settings,
        note=note,
        source_job=source_job,
    )
    runtime_store.set_backlog_candidate_state(
        str(candidate.get("candidate_id", "")).strip(),
        state="queued",
        payload_updates={
            "approved_at": str(payload.get("approved_at", "")).strip() or now,
            "queued_at": now,
            "queued_job_id": queued_job_id,
            "queued_job_kind": queued_job.job_kind,
            "queued_job_issue_number": issue_number,
            "queued_job_issue_url": issue_url,
            "parent_job_id": source_job_id,
            "backlog_candidate_id": candidate_id,
            "followup_artifact_path": str(artifact_path),
            "operator_note": note,
            "last_action": "queue",
        },
    )
    return queued_job, artifact_path


def _write_followup_backlog_artifact(
    *,
    candidate: Dict[str, Any],
    queued_job: JobRecord,
    settings: AppSettings,
    note: str,
    source_job: Optional[JobRecord],
) -> Path:
    """Write one explicit follow-up backlog artifact for the next planner round."""

    workspace_path = _job_workspace_path(queued_job, settings)
    paths = build_workflow_artifact_paths(workspace_path)
    artifact_path = paths["followup_backlog_task"]
    payload = candidate.get("payload", {}) if isinstance(candidate.get("payload"), dict) else {}
    artifact_payload = {
        "generated_at": utc_now_iso(),
        "source": "memory_backlog_candidate",
        "job_contract": {
            "kind": "followup_backlog",
            "version": "v1",
            "issue_backed": True,
            "dedicated_followup": True,
        },
        "candidate_id": str(candidate.get("candidate_id", "")).strip(),
        "title": str(candidate.get("title", "")).strip(),
        "summary": str(candidate.get("summary", "")).strip(),
        "priority": str(candidate.get("priority", "P2")).strip() or "P2",
        "state": "queued",
        "queued_job_id": queued_job.job_id,
        "queued_job_kind": str(queued_job.job_kind or "").strip() or "followup_backlog",
        "queued_job_issue_number": queued_job.issue_number,
        "queued_job_issue_url": queued_job.issue_url,
        "workflow_id": queued_job.workflow_id,
        "app_code": queued_job.app_code,
        "track": queued_job.track,
        "parent_job_id": str(queued_job.parent_job_id or "").strip(),
        "backlog_candidate_id": str(queued_job.backlog_candidate_id or "").strip(),
        "recommended_node_type": str(payload.get("recommended_node_type", "")).strip(),
        "recommended_action": (
            str(payload.get("action", "")).strip()
            or str(payload.get("recommended_action", "")).strip()
        ),
        "source_kind": str(payload.get("source_kind", "")).strip(),
        "source_job_id": str(payload.get("job_id", "")).strip(),
        "source_issue_number": int(payload.get("issue_number", queued_job.issue_number) or queued_job.issue_number),
        "source_issue_title": str(payload.get("issue_title", "")).strip()
        or (str(source_job.issue_title or "").strip() if source_job is not None else ""),
        "source_job_kind": str(source_job.job_kind or "").strip() if source_job is not None else "issue",
        "cluster_key": str(payload.get("cluster_key", "")).strip(),
        "operator_note": note,
        "raw_payload": payload,
    }
    artifact_path.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact_path
