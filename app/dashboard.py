"""Dashboard routes for job visibility."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dependencies import get_settings, get_store
from app.feature_flags import feature_flags_payload, read_feature_flags, write_feature_flags
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
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
_ASSISTANT_PROVIDER_ALIASES = {"claude": "codex", "copilot": "codex"}


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

    message: str = Field(min_length=1, max_length=8000)
    history: List[Dict[str, str]] = Field(default_factory=list)


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


def _read_dashboard_json(path: Path) -> Dict[str, Any]:
    """Read one dashboard-side JSON artifact safely."""

    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_job_runtime_signals(
    job: JobRecord,
    *,
    store: JobStore,
    settings: AppSettings,
) -> Dict[str, Any]:
    """Collect runtime review/resume/recovery signals for dashboard rendering."""

    workspace_path = _job_workspace_path(job, settings)
    docs_dir = workspace_path / "_docs"
    review_payload = _read_dashboard_json(docs_dir / "PRODUCT_REVIEW.json")
    maturity_payload = _read_dashboard_json(docs_dir / "REPO_MATURITY.json")
    trend_payload = _read_dashboard_json(docs_dir / "QUALITY_TREND.json")
    loop_payload = _read_dashboard_json(docs_dir / "IMPROVEMENT_LOOP_STATE.json")
    next_tasks_payload = _read_dashboard_json(docs_dir / "NEXT_IMPROVEMENT_TASKS.json")
    strategy_shadow_payload = _read_dashboard_json(docs_dir / "STRATEGY_SHADOW_REPORT.json")
    memory_trace_payload = _read_dashboard_json(docs_dir / "MEMORY_TRACE.json")
    node_runs = store.list_node_runs(job.job_id)
    resume_state = _compute_job_resume_state(job, node_runs, settings)

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
        "execution_repository": _job_execution_repository(job),
    }


def _read_job_memory_trace(job: JobRecord, settings: AppSettings) -> Dict[str, Any]:
    """Read one job's structured memory retrieval trace."""

    workspace_path = _job_workspace_path(job, settings)
    return _read_dashboard_json(workspace_path / "_docs" / "MEMORY_TRACE.json")


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
        jobs.append(payload)
    jobs.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return jobs


def _job_execution_repository(job: JobRecord) -> str:
    """Return the repo used for clone/build/push for one job."""

    source_repository = str(job.source_repository or "").strip()
    return source_repository or str(job.repository or "").strip()


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


def _build_admin_metrics(store: JobStore, settings: AppSettings) -> Dict[str, Any]:
    """Aggregate read-only admin metrics from jobs and workspace artifacts."""

    feature_flags = read_feature_flags(_FEATURE_FLAGS_CONFIG_PATH)
    jobs = _list_dashboard_jobs(store, settings)
    summary = _build_job_summary(jobs)

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    workflows_payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    workflows = workflows_payload.get("workflows", []) if isinstance(workflows_payload, dict) else []
    roles_payload = _read_roles_payload(_ROLES_CONFIG_PATH)
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
    for job in store.list_jobs():
        workspace = _job_workspace_path(job, settings)
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

    for workspace in workspace_paths.values():
        docs_dir = workspace / "_docs"
        memory_totals["workspace_count"] += 1
        memory_log_entries = _read_dashboard_jsonl(docs_dir / "MEMORY_LOG.jsonl")
        decision_entries = _read_dashboard_json((docs_dir / "DECISION_HISTORY.json")).get("entries", [])
        failure_items = _read_dashboard_json((docs_dir / "FAILURE_PATTERNS.json")).get("items", [])
        convention_items = _read_dashboard_json((docs_dir / "CONVENTIONS.json")).get("rules", [])
        feedback_entries = _read_dashboard_json((docs_dir / "MEMORY_FEEDBACK.json")).get("entries", [])
        ranking_items = _read_dashboard_json((docs_dir / "MEMORY_RANKINGS.json")).get("items", [])
        memory_selection_payload = _read_dashboard_json(docs_dir / "MEMORY_SELECTION.json")
        memory_context_payload = _read_dashboard_json(docs_dir / "MEMORY_CONTEXT.json")
        memory_feedback_payload = _read_dashboard_json(docs_dir / "MEMORY_FEEDBACK.json")
        memory_rankings_payload = _read_dashboard_json(docs_dir / "MEMORY_RANKINGS.json")
        strategy_shadow_payload = _read_dashboard_json(docs_dir / "STRATEGY_SHADOW_REPORT.json")

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
        timeline_anchor = datetime.fromisoformat(utc_now_iso().replace("Z", "+00:00")).date()
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
        "generated_at": utc_now_iso(),
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
            "strategy_counts": _top_counter_items(strategy_counter, limit=8),
            "stage_counts": _top_counter_items(stage_counter, limit=8),
            "app_counts": _top_counter_items(app_counter, limit=8),
            "track_counts": _top_counter_items(track_counter, limit=8),
            "workflow_counts": _top_counter_items(workflow_counter, limit=8),
            "recovery_counts": _top_counter_items(recovery_counter, limit=8),
            "resume_mode_counts": _top_counter_items(resume_counter, limit=8),
            "shadow_strategy_counts": _top_counter_items(shadow_strategy_counter, limit=8),
            "shadow_decision_counts": _top_counter_items(shadow_decision_counter, limit=8),
            "shadow_divergence_count": shadow_divergence_count,
            "adaptive_job_count": workflow_counter.get(adaptive_workflow_id, 0),
            "default_job_count": workflow_counter.get(default_workflow_id, 0) if default_workflow_id else 0,
        },
        "quality": {
            "average_review_overall": _safe_average(review_overalls),
            "average_maturity_score": _safe_average(maturity_scores),
            "trend_direction_counts": _top_counter_items(trend_counter, limit=8),
            "maturity_level_counts": _top_counter_items(maturity_counter, limit=8),
            "low_category_counts": _top_counter_items(low_category_counter, limit=8),
        },
        "workflow_adoption": {
            "default_workflow_id": default_workflow_id,
            "adaptive_workflow_id": adaptive_workflow_id,
            "app_workflow_counts": _top_counter_items(app_workflow_counter, limit=8),
            "apps_using_default_workflow": apps_using_default_workflow,
            "apps_using_adaptive_workflow": apps_using_adaptive_workflow,
            "adaptive_app_rate": round(apps_using_adaptive_workflow / len(apps), 3) if apps else None,
            "timeline": workflow_timeline,
        },
        "memory": {
            **memory_totals,
            "ranking_state_counts": _top_counter_items(ranking_state_counter, limit=8),
        },
        "feature_flags": feature_flags,
        "capabilities": capabilities,
        "phase_status": phase_status,
        "retrieval": {
            "enabled": retrieval_enabled,
            "latest_generated_at": _latest_non_empty(retrieval_generated_ats),
            "workspaces_with_retrieval": memory_totals["workspaces_with_retrieval"],
            "active": memory_totals["workspaces_with_retrieval"] > 0,
        },
        "scoring": {
            "enabled": scoring_enabled,
            "latest_generated_at": _latest_non_empty(scoring_generated_ats),
            "workspaces_with_scoring": memory_totals["workspaces_with_scoring"],
            "active": memory_totals["workspaces_with_scoring"] > 0,
        },
        "shadow": {
            "enabled": shadow_enabled,
            "latest_generated_at": _latest_non_empty(shadow_generated_ats),
            "workspaces_with_strategy_shadow": memory_totals["workspaces_with_strategy_shadow"],
            "divergence_count": shadow_divergence_count,
            "active": memory_totals["workspaces_with_strategy_shadow"] > 0,
        },
    }


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

    payload = _read_roles_payload(_ROLES_CONFIG_PATH)
    return JSONResponse(payload)


@router.post("/api/roles", response_class=JSONResponse)
def upsert_role(payload: RoleConfigRequest) -> JSONResponse:
    """Create or update one role definition."""

    role_code = _normalize_role_code(payload.code)
    if not role_code:
        raise HTTPException(status_code=400, detail="역할 코드는 영문/숫자/-/_ 형식이어야 합니다.")

    role = {
        "code": role_code,
        "name": payload.name.strip(),
        "objective": payload.objective.strip(),
        "cli": payload.cli.strip().lower(),
        "template_key": payload.template_key.strip(),
        "inputs": payload.inputs.strip(),
        "outputs": payload.outputs.strip(),
        "checklist": payload.checklist.strip(),
        "skills": _normalize_role_tag_list(payload.skills),
        "allowed_tools": _normalize_role_tag_list(payload.allowed_tools),
        "enabled": bool(payload.enabled),
    }
    if not role["name"]:
        raise HTTPException(status_code=400, detail="역할 이름은 필수입니다.")

    data = _read_roles_payload(_ROLES_CONFIG_PATH)
    roles = data.get("roles", [])
    replaced = False
    updated: List[Dict[str, Any]] = []
    for item in roles:
        if not isinstance(item, dict):
            continue
        code = _normalize_role_code(str(item.get("code", "")))
        if not code:
            continue
        if code == role_code:
            updated.append(role)
            replaced = True
            continue
        copied = dict(item)
        copied["code"] = code
        updated.append(copied)

    if not replaced:
        updated.append(role)

    updated.sort(key=lambda item: str(item.get("code", "")))
    data["roles"] = updated
    _write_roles_payload(_ROLES_CONFIG_PATH, data)
    return JSONResponse({"saved": True, "roles": data["roles"], "presets": data.get("presets", [])})


@router.delete("/api/roles/{role_code}", response_class=JSONResponse)
def delete_role(role_code: str) -> JSONResponse:
    """Delete one role and unlink it from presets."""

    code = _normalize_role_code(role_code)
    if not code:
        raise HTTPException(status_code=400, detail="유효하지 않은 역할 코드입니다.")

    data = _read_roles_payload(_ROLES_CONFIG_PATH)
    roles = [item for item in data.get("roles", []) if _normalize_role_code(str(item.get("code", ""))) != code]
    data["roles"] = roles

    presets: List[Dict[str, Any]] = []
    for preset in data.get("presets", []):
        if not isinstance(preset, dict):
            continue
        copied = dict(preset)
        role_codes = [rc for rc in copied.get("role_codes", []) if _normalize_role_code(str(rc)) != code]
        copied["role_codes"] = role_codes
        presets.append(copied)
    data["presets"] = presets
    _write_roles_payload(_ROLES_CONFIG_PATH, data)
    return JSONResponse({"deleted": True, "roles": roles, "presets": presets})


@router.post("/api/role-presets", response_class=JSONResponse)
def upsert_role_preset(payload: RolePresetRequest) -> JSONResponse:
    """Create or update one role-combination preset."""

    preset_id = _normalize_role_code(payload.preset_id)
    if not preset_id:
        raise HTTPException(status_code=400, detail="프리셋 ID는 영문/숫자/-/_ 형식이어야 합니다.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="프리셋 이름은 필수입니다.")

    data = _read_roles_payload(_ROLES_CONFIG_PATH)
    known_roles = {
        _normalize_role_code(str(item.get("code", "")))
        for item in data.get("roles", [])
        if isinstance(item, dict)
    }
    role_codes: List[str] = []
    for raw in payload.role_codes:
        code = _normalize_role_code(raw)
        if code and code in known_roles and code not in role_codes:
            role_codes.append(code)

    preset = {
        "preset_id": preset_id,
        "name": name,
        "description": payload.description.strip(),
        "role_codes": role_codes,
    }

    replaced = False
    updated: List[Dict[str, Any]] = []
    for item in data.get("presets", []):
        if not isinstance(item, dict):
            continue
        if _normalize_role_code(str(item.get("preset_id", ""))) == preset_id:
            updated.append(preset)
            replaced = True
            continue
        copied = dict(item)
        copied["preset_id"] = _normalize_role_code(str(copied.get("preset_id", "")))
        copied["role_codes"] = [
            rc for rc in [
                _normalize_role_code(str(value))
                for value in copied.get("role_codes", [])
            ] if rc
        ]
        updated.append(copied)
    if not replaced:
        updated.append(preset)
    updated.sort(key=lambda item: str(item.get("preset_id", "")))

    data["presets"] = updated
    _write_roles_payload(_ROLES_CONFIG_PATH, data)
    return JSONResponse({"saved": True, "roles": data.get("roles", []), "presets": updated})


@router.delete("/api/role-presets/{preset_id}", response_class=JSONResponse)
def delete_role_preset(preset_id: str) -> JSONResponse:
    """Delete one role-combination preset."""

    normalized = _normalize_role_code(preset_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="유효하지 않은 프리셋 ID입니다.")
    data = _read_roles_payload(_ROLES_CONFIG_PATH)
    presets = [
        item for item in data.get("presets", [])
        if _normalize_role_code(str(item.get("preset_id", ""))) != normalized
    ]
    data["presets"] = presets
    _write_roles_payload(_ROLES_CONFIG_PATH, data)
    return JSONResponse({"deleted": True, "roles": data.get("roles", []), "presets": presets})


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

    templates = _read_command_templates(settings.command_config)
    return JSONResponse(
        {
            "planner": templates.get("planner", ""),
            "coder": templates.get("coder", ""),
            "reviewer": templates.get("reviewer", ""),
            "copilot": templates.get("copilot", ""),
            "escalation": templates.get("escalation", ""),
            "enable_escalation": _read_env_enable_escalation(Path.cwd() / ".env", settings.enable_escalation),
        }
    )


@router.post("/api/agents/config", response_class=JSONResponse)
def update_agents_config(
    payload: AgentTemplateConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Update planner/coder/reviewer templates in command config file."""

    current = _read_command_templates(settings.command_config)
    current["planner"] = payload.planner.strip()
    current["coder"] = payload.coder.strip()
    current["reviewer"] = payload.reviewer.strip()
    current["copilot"] = payload.copilot.strip()
    current["escalation"] = payload.escalation.strip()
    _write_command_templates(settings.command_config, current)
    _set_env_value(Path.cwd() / ".env", "AGENTHUB_ENABLE_ESCALATION", "true" if payload.enable_escalation else "false")
    return JSONResponse({"saved": True, "enable_escalation": payload.enable_escalation})


@router.get("/api/agents/check", response_class=JSONResponse)
def check_agent_clis(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Check whether Gemini/Codex CLIs are executable."""

    templates = _read_command_templates(settings.command_config)
    result = {
        "gemini": _check_one_cli("gemini", templates),
        "codex": _check_one_cli("codex", templates),
    }
    return JSONResponse(result)


@router.get("/api/agents/models", response_class=JSONResponse)
def check_agent_models(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return inferred model settings for Gemini/Codex."""

    templates = _read_command_templates(settings.command_config)
    result = {
        "gemini": _infer_cli_model("gemini", templates),
        "codex": _infer_cli_model("codex", templates),
    }
    return JSONResponse(result)


@router.post("/api/assistant/codex-chat", response_class=JSONResponse)
def codex_assistant_chat(
    payload: AssistantChatRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Run one Codex CLI turn and return assistant text output."""

    raw_message = payload.message.strip()
    if not raw_message:
        raise HTTPException(status_code=400, detail="메시지를 입력해주세요.")

    history_lines: List[str] = []
    for item in payload.history[-12:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if role == "assistant":
            history_lines.append(f"assistant: {content}")
        else:
            history_lines.append(f"user: {content}")

    conversation_context = "\n".join(history_lines)
    runtime_context = _build_agent_observability_context(store, settings)
    full_prompt = (
        "You are 'AgentHub Ops Assistant', a diagnosis chatbot for AI-agent workflows.\n"
        "Primary mission: analyze what happened in agent runs, identify likely root causes, "
        "and provide practical next actions.\n"
        "Rules:\n"
        "- Reply in concise Korean unless user asks another language.\n"
        "- Use evidence from provided runtime context first.\n"
        "- Clearly separate: 사실(관측), 추정(가설), 조치(실행 단계).\n"
        "- If evidence is insufficient, say exactly what is missing.\n"
        "- Do not fabricate logs, job ids, or command outputs.\n\n"
        f"Runtime context:\n{runtime_context}\n\n"
        f"Conversation so far:\n{conversation_context or '(none)'}\n\n"
        f"Latest user message:\n{raw_message}\n"
    )
    try:
        templates = _read_command_templates(settings.command_config)
    except HTTPException:
        templates = {}
    codex_prefix = _resolve_codex_command_prefix(templates)

    output_file = tempfile.NamedTemporaryFile(
        prefix="agenthub-codex-chat-",
        suffix=".txt",
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()

    command = [
        *codex_prefix,
        "exec",
        "-C",
        str(Path.cwd()),
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
        full_prompt,
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=504,
            detail="Codex 응답이 시간 제한(180초)을 초과했습니다.",
        ) from error
    except OSError as error:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"Codex 실행 실패: {error}",
        ) from error

    output_text = ""
    if output_path.exists():
        try:
            output_text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            output_text = ""
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass

    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()
        lowered_error = raw_error.lower()
        stderr_preview = raw_error[:1000]
        if (
            "operation not permitted" in lowered_error
            or "error sending request for url" in lowered_error
            or "failed to connect to websocket" in lowered_error
            or "stream disconnected before completion" in lowered_error
            or "chatgpt.com/backend-api/codex/responses" in lowered_error
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Codex 외부 연결이 차단되어 응답을 생성하지 못했습니다. "
                    "서버에서 chatgpt.com 으로의 아웃바운드 네트워크를 허용하거나, "
                    "로컬 OSS 모델(ollama/lmstudio) 구성을 사용해주세요. "
                    f"원본 오류: {stderr_preview or '(no output)'}"
                ),
            )
        if "not logged in" in lowered_error or "login" in lowered_error:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Codex 로그인 상태가 유효하지 않습니다. "
                    "서버 계정에서 `codex login` 후 다시 시도해주세요. "
                    f"원본 오류: {stderr_preview or '(no output)'}"
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=(
                "Codex 응답 생성 실패. "
                f"exit={process.returncode}, output={stderr_preview or '(no output)'}"
            ),
        )

    if not output_text:
        output_text = (process.stdout or "").strip()
    if not output_text:
        output_text = "응답이 비어 있습니다. 다시 시도해주세요."

    return JSONResponse(
        {
            "ok": True,
            "assistant": output_text,
            "model": "codex-cli",
            "cwd": str(Path.cwd()),
            "data_dir": str(settings.data_dir),
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
    allowed = _PRIMARY_ASSISTANT_PROVIDERS | set(_ASSISTANT_PROVIDER_ALIASES)
    if requested_assistant not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"지원하지 않는 assistant 입니다: {requested_assistant}. "
                f"공식 지원: {', '.join(sorted(_PRIMARY_ASSISTANT_PROVIDERS))}. "
                f"호환 별칭: {', '.join(sorted(_ASSISTANT_PROVIDER_ALIASES))}"
            ),
        )
    assistant = _canonical_cli_name(requested_assistant)

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question은 비어 있을 수 없습니다.")

    focus_job_id = payload.job_id.strip()
    focus_context = ""
    if focus_job_id:
        job = store.get_job(focus_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {focus_job_id}")
        focus_context = _build_focus_job_log_context(job, settings)

    runtime_context = _build_agent_observability_context(store, settings)
    prompt = _build_log_analysis_prompt(
        assistant=assistant,
        question=question,
        runtime_context=runtime_context,
        focus_context=focus_context,
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
    role_preset_id = _normalize_role_code(payload.role_preset_id)
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
        roles_payload = _read_roles_payload(_ROLES_CONFIG_PATH)
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
    manual_retry_options = _build_manual_retry_options(job, settings=settings, node_runs=node_runs)

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


def _read_command_templates(path: Path) -> Dict[str, str]:
    """Read command template JSON file into string dictionary."""

    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 파일이 없습니다: {path}",
        )

    try:
        raw_payload = path.read_text(encoding="utf-8")
        loaded = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 JSON 파싱 실패: {path}",
        ) from error

    if not isinstance(loaded, dict):
        raise HTTPException(
            status_code=500,
            detail="명령 템플릿 포맷이 올바르지 않습니다. JSON object여야 합니다.",
        )

    templates: Dict[str, str] = {}
    for key, value in loaded.items():
        if isinstance(value, str):
            templates[str(key)] = value
    return templates


def _write_command_templates(path: Path, templates: Dict[str, str]) -> None:
    """Persist command templates as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(templates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_env_enable_escalation(env_path: Path, fallback: bool) -> bool:
    """Read AGENTHUB_ENABLE_ESCALATION from .env file if available."""

    if not env_path.exists():
        return fallback

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("AGENTHUB_ENABLE_ESCALATION="):
            continue
        raw_value = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
        return raw_value in {"1", "true", "yes", "on"}
    return fallback


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    """Set or append one KEY=value entry in .env while preserving other lines."""

    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()

    prefix = f"{key}="
    replaced = False
    updated: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith(f"#{prefix}"):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        updated.append(f"{key}={value}")

    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _check_one_cli(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Probe one CLI using template-derived paths then PATH fallback."""

    candidates = _build_cli_probe_candidates(cli_name, templates)
    for args in candidates:
        probe = _run_probe(args)
        if probe["ok"]:
            return {
                "ok": True,
                "command": " ".join(args),
                "output": probe["output"],
            }

    # Return the last failure detail for easier debugging.
    last = _run_probe(candidates[-1])
    return {
        "ok": False,
        "command": " ".join(candidates[-1]),
        "output": last["output"],
    }


def _build_cli_probe_candidates(cli_name: str, templates: Dict[str, str]) -> List[List[str]]:
    """Build probe command candidates from known paths and templates."""

    cli_name = _canonical_cli_name(cli_name)
    known: List[List[str]] = []

    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    cli_paths = [
        path
        for path in absolute_paths
        if path.endswith(f"/{cli_name}") or path.endswith(f"/{cli_name}.js")
    ]

    for path in cli_paths:
        if node_paths and path.startswith("/"):
            known.append([node_paths[0], path, "--version"])
        known.append([path, "--version"])

    # Fallback to PATH
    known.append([cli_name, "--version"])

    deduped: List[List[str]] = []
    seen: set[str] = set()
    for args in known:
        key = " ".join(args)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(args)
    return deduped


def _resolve_codex_command_prefix(templates: Dict[str, str]) -> List[str]:
    """Resolve executable prefix for Codex command under systemd/non-login shells."""

    candidates: List[List[str]] = []
    env_codex = os.getenv("AGENTHUB_CODEX_BIN", "").strip()
    if env_codex:
        candidates.append([env_codex])

    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    codex_paths = [
        path for path in absolute_paths if path.endswith("/codex") or path.endswith("/codex.js")
    ]
    for path in codex_paths:
        if path.endswith(".js") and node_paths:
            candidates.append([node_paths[0], path])
        candidates.append([path])

    for known in [
        "/root/.nvm/versions/node/v24.14.0/bin/codex",
        "/usr/local/bin/codex",
        "/usr/bin/codex",
    ]:
        candidates.append([known])

    which_codex = shutil.which("codex")
    if which_codex:
        candidates.append([which_codex])

    deduped: List[List[str]] = []
    seen: set[str] = set()
    for item in candidates:
        key = " ".join(item)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    for prefix in deduped:
        try:
            probe = subprocess.run(
                [*prefix, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return prefix

    tried = ", ".join(" ".join(item) for item in deduped) or "(none)"
    raise HTTPException(
        status_code=500,
        detail=(
            "Codex 실행 파일을 찾지 못했습니다. "
            "환경변수 `AGENTHUB_CODEX_BIN`에 Codex 절대경로를 설정해주세요. "
            f"탐색 경로: {tried}"
        ),
    )


def _resolve_cli_command_prefix(
    cli_name: str,
    templates: Dict[str, str],
    *,
    env_var: str = "",
) -> List[str]:
    """Resolve executable prefix for generic CLI commands."""

    cli_name = _canonical_cli_name(cli_name)
    candidates: List[List[str]] = []
    if env_var:
        env_path = os.getenv(env_var, "").strip()
        if env_path:
            candidates.append([env_path])

    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    cli_paths = [
        path for path in absolute_paths if path.endswith(f"/{cli_name}") or path.endswith(f"/{cli_name}.js")
    ]
    for path in cli_paths:
        if path.endswith(".js") and node_paths:
            candidates.append([node_paths[0], path])
        candidates.append([path])

    which_cli = shutil.which(cli_name)
    if which_cli:
        candidates.append([which_cli])
    candidates.append([cli_name])

    deduped: List[List[str]] = []
    seen: set[str] = set()
    for item in candidates:
        key = " ".join(item)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    for prefix in deduped:
        try:
            probe = subprocess.run(
                [*prefix, "--version"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return prefix

    tried = ", ".join(" ".join(item) for item in deduped) or "(none)"
    raise HTTPException(
        status_code=500,
        detail=f"{cli_name} 실행 파일을 찾지 못했습니다. 탐색 경로: {tried}",
    )


def _run_probe(args: List[str]) -> Dict[str, Any]:
    """Run one probe command and capture compact output."""

    try:
        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "timeout"}
    except OSError as error:
        return {"ok": False, "output": str(error)}

    output = (process.stdout or process.stderr or "").strip().splitlines()
    first_line = output[0] if output else ""
    return {"ok": process.returncode == 0, "output": first_line[:240]}


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


def _build_log_analysis_prompt(
    *,
    assistant: str,
    question: str,
    runtime_context: str,
    focus_context: str,
) -> str:
    """Create one-shot prompt for assistant-driven log diagnosis."""

    return (
        f"당신은 AgentHub 로그 분석 도우미({assistant})입니다.\n"
        "목표: 로그를 근거로 문제점을 식별하고, 즉시 실행 가능한 조치안을 제시하세요.\n\n"
        "출력 규칙:\n"
        "1) 핵심 문제점 (최대 5개)\n"
        "2) 근거 로그 (각 문제점별 1~2줄)\n"
        "3) 원인 가설 (확신도 high/med/low)\n"
        "4) 즉시 조치 (명령/파일 단위)\n"
        "5) 재발 방지 제안\n"
        "- 한국어로 간결하게 작성\n"
        "- 근거 없는 단정 금지\n\n"
        f"[사용자 질문]\n{question}\n\n"
        f"[런타임 컨텍스트]\n{runtime_context}\n\n"
        + (f"[집중 분석 대상]\n{focus_context}\n\n" if focus_context else "")
    )


def _run_log_analyzer(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one log-analysis request to selected provider CLI."""

    if assistant == "codex":
        return _run_codex_log_analysis(prompt, templates)
    if assistant == "gemini":
        return _run_gemini_log_analysis(prompt, templates)
    raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")


def _run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Run codex CLI for log analysis and return text output."""

    codex_prefix = _resolve_codex_command_prefix(templates)
    output_file = tempfile.NamedTemporaryFile(
        prefix="agenthub-log-analysis-codex-",
        suffix=".txt",
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()
    try:
        process = subprocess.run(
            [
                *codex_prefix,
                "exec",
                "-C",
                str(Path.cwd()),
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-last-message",
                str(output_path),
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="Codex 로그 분석이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Codex 실행 실패: {error}") from error

    output_text = ""
    if output_path.exists():
        try:
            output_text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            output_text = ""
    output_path.unlink(missing_ok=True)
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Codex 로그 분석 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    if not output_text:
        output_text = (process.stdout or "").strip()
    return output_text or "응답이 비어 있습니다."


def _run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Run gemini CLI for log analysis and return text output."""

    prefix = _resolve_cli_command_prefix("gemini", templates, env_var="AGENTHUB_GEMINI_BIN")
    try:
        process = subprocess.run(
            [
                *prefix,
                "-p",
                prompt,
                "--approval-mode",
                "yolo",
                "--model",
                "gemini-3.1-pro-preview",
                "--output-format",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="Gemini 로그 분석이 시간 제한(180초)을 초과했습니다.") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Gemini 실행 실패: {error}") from error
    if process.returncode != 0:
        raw_error = (process.stderr or process.stdout or "").strip()[:1000]
        raise HTTPException(
            status_code=502,
            detail=f"Gemini 로그 분석 실패(exit={process.returncode}): {raw_error or '(no output)'}",
        )
    return (process.stdout or "").strip() or "응답이 비어 있습니다."


def _run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Claude alias maintained for compatibility and routed to Codex."""

    return _run_codex_log_analysis(prompt, templates)


def _run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Copilot alias maintained for compatibility and routed to Codex."""

    return _run_codex_log_analysis(prompt, templates)


def _tail_text_lines(path: Path, max_lines: int = 16) -> List[str]:
    """Read the tail lines of a UTF-8 text file safely."""

    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(failed to read log file)"]
    tail = rows[-max_lines:] if len(rows) > max_lines else rows
    return [row[:300] for row in tail]


def _infer_cli_model(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Infer model name from command templates first, then environment."""

    cli_name = _canonical_cli_name(cli_name)
    from_template = _infer_model_from_templates(cli_name, templates)
    if from_template is not None:
        return {
            "model": from_template["model"],
            "source": from_template["source"],
            "template_key": from_template["template_key"],
        }

    from_env = _infer_model_from_env(cli_name)
    if from_env is not None:
        return {
            "model": from_env["model"],
            "source": from_env["source"],
            "template_key": "",
        }

    from_runtime = _infer_model_from_runtime_files(cli_name)
    if from_runtime is not None:
        return {
            "model": from_runtime["model"],
            "source": from_runtime["source"],
            "template_key": "",
        }

    return {
        "model": "",
        "source": "not_found",
        "template_key": "",
    }


def _infer_model_from_templates(cli_name: str, templates: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Find explicit --model/-m style option in matching template command."""

    cli_name = _canonical_cli_name(cli_name)
    for key, command in templates.items():
        lowered = command.lower()
        if cli_name not in lowered:
            continue

        # --model value
        match = re.search(r"(?:--model|-m)\s+([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_flag",
                "template_key": key,
            }

        # key=value styles
        match = re.search(r"(?:model|MODEL)=([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_assignment",
                "template_key": key,
            }
    return None


def _infer_model_from_env(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from common environment variable names."""

    cli_name = _canonical_cli_name(cli_name)
    candidates: Dict[str, List[str]] = {
        "gemini": ["GEMINI_MODEL", "AGENTHUB_GEMINI_MODEL"],
        "codex": ["CODEX_MODEL", "OPENAI_MODEL", "AGENTHUB_CODEX_MODEL"],
    }
    for env_name in candidates.get(cli_name, []):
        value = os.getenv(env_name, "").strip()
        if value:
            return {"model": value, "source": f"env:{env_name}"}
    return None


def _infer_model_from_runtime_files(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from the latest local runtime/session files."""

    cli_name = _canonical_cli_name(cli_name)
    if cli_name == "gemini":
        candidates = _recent_files(Path("/root/.gemini"), "tmp/**/chats/*.json")
        model = _find_model_in_recent_files(candidates, [r'"model"\s*:\s*"([^"]+)"'])
        if model:
            return {"model": model, "source": "runtime:gemini_chats"}
        return None

    if cli_name == "codex":
        files: List[Path] = []
        files.extend(_recent_files(Path("/root/.codex"), "history.jsonl", limit=1))
        files.extend(_recent_files(Path("/root/.codex"), "sessions/**/*.jsonl"))
        model = _find_model_in_recent_files(
            files,
            [
                r'"model"\s*:\s*"([^"]+)"',
                r'"model_slug"\s*:\s*"([^"]+)"',
                r'"model_name"\s*:\s*"([^"]+)"',
            ],
        )
        if model:
            return {"model": model, "source": "runtime:codex_sessions"}
        return None

    return None


def _canonical_cli_name(cli_name: str) -> str:
    """Map legacy provider aliases to the active runtime provider."""

    normalized = str(cli_name or "").strip().lower()
    return _ASSISTANT_PROVIDER_ALIASES.get(normalized, normalized)


def _recent_files(base: Path, pattern: str, limit: int = 20) -> List[Path]:
    """Return recent files matching glob pattern, newest first."""

    if not base.exists():
        return []
    matched = [path for path in base.glob(pattern) if path.is_file()]
    matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matched[:limit]


def _find_model_in_recent_files(files: List[Path], regexes: List[str]) -> Optional[str]:
    """Search recent files for model-like fields and return the first match."""

    compiled = [re.compile(pattern) for pattern in regexes]
    for file_path in files:
        text = _read_file_tail(file_path, max_bytes=250_000)
        for regex in compiled:
            matches = regex.findall(text)
            if not matches:
                continue
            # Prefer latest entry by taking the last match in the file tail.
            candidate = str(matches[-1]).strip()
            if candidate:
                return candidate
    return None


def _read_file_tail(path: Path, max_bytes: int) -> str:
    """Read at most `max_bytes` from the end of file as text."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            raw = handle.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


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


def _normalize_role_code(value: str) -> str:
    """Normalize one role/preset identifier."""

    lowered = (value or "").strip().lower()
    filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
    return filtered[:40]


def _normalize_role_tag_list(values: Any) -> List[str]:
    """Normalize role skill/tool metadata into stable identifiers."""

    items: List[str] = []
    if isinstance(values, str):
        items = [part.strip() for part in values.replace("\n", ",").split(",")]
    elif isinstance(values, list):
        items = [str(item).strip() for item in values]
    else:
        return []

    normalized: List[str] = []
    for item in items:
        token = _normalize_role_code(item)[:80]
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _default_roles_payload() -> Dict[str, Any]:
    """Default role catalog for role-management MVP."""

    role_rows = [
        ("ai-helper", "AI 도우미", "codex", "", "요청/문제 정리", "분석/조치안"),
        ("log-analyzer-codex", "로그 분석 도우미(Codex)", "codex", "coder", "워크플로우 로그", "문제점/조치안"),
        ("log-analyzer-gemini", "로그 분석 도우미(Gemini)", "gemini", "reviewer", "워크플로우 로그", "문제점/조치안"),
        ("coder", "코더", "codex", "coder", "SPEC/PLAN", "코드 변경"),
        ("designer", "디자이너", "codex", "coder", "요구사항", "UI/디자인 산출물"),
        ("tester", "테스터", "bash", "", "코드 상태", "테스트 결과"),
        ("reviewer", "리뷰어", "gemini", "reviewer", "코드 diff", "리뷰 리포트"),
        ("copywriter", "카피라이터", "codex", "coder", "기획의도/디자인/퍼블리싱 결과", "COPYWRITING_PLAN.md, COPY_DECK.md"),
        ("consultant", "컨설턴트", "gemini", "planner", "현황", "전략 제안"),
        ("qa", "QA", "bash", "", "테스트 계획", "품질 점검"),
        ("architect", "플래너", "gemini", "planner", "요구사항", "실행 계획"),
        ("devops-sre", "인프라·운영 엔지니어", "bash", "", "서비스 상태", "운영 조치"),
        ("escalation-helper", "에스컬레이션 도우미", "codex", "escalation", "실패 로그/상태", "보조 분석/다음 액션"),
        ("security", "보안 엔지니어", "bash", "", "코드/설정", "보안 점검"),
        ("db-engineer", "데이터베이스 엔지니어", "bash", "", "스키마", "DB 변경안"),
        ("performance", "성능 최적화 엔지니어", "bash", "", "프로파일링", "개선안"),
        ("accessibility", "접근성 전문가", "bash", "", "UI", "접근성 점검"),
        ("test-automation", "테스트 자동화 엔지니어", "bash", "", "테스트 전략", "자동화 코드"),
        ("release-manager", "배포 관리자", "bash", "", "릴리즈 계획", "배포 체크"),
        ("incident-analyst", "장애 원인 분석가", "codex", "", "로그/지표", "RCA"),
        ("orchestration-helper", "오케스트레이션 도우미", "codex", "copilot", "워크플로우 상태/로그", "다음 단계/재시도 전략"),
        ("system-owner", "시스템 오너", "gemini", "planner", "이슈 본문/SPEC.md", "확정 스펙/우선순위"),
        ("tech-writer", "기술 문서 작성가", "codex", "documentation_writer", "SPEC/PLAN/REVIEW", "README.md, COPYRIGHT.md, DEVELOPMENT_GUIDE.md"),
        ("product-analyst", "제품 분석가", "gemini", "planner", "지표/요구", "개선 우선순위"),
        ("publisher", "퍼블리셔", "codex", "coder", "디자인 시스템/화면 구조", "퍼블리싱 결과물"),
        ("research-agent", "정보검색 도우미", "python3", "research_search", "질문/키워드", "SEARCH_CONTEXT.md"),
        ("refactor-specialist", "리팩토링 전문가", "codex", "coder", "코드베이스", "구조 개선"),
        ("requirements-manager", "요구사항 관리자", "gemini", "planner", "이해관계자 요청", "명세"),
        ("data-ai-engineer", "데이터/AI 엔지니어", "codex", "copilot", "데이터 과제", "파이프라인/모델 개선"),
    ]
    roles = [
        {
            "code": code,
            "name": name,
            "objective": "",
            "cli": cli,
            "template_key": template_key,
            "inputs": inputs,
            "outputs": outputs,
            "checklist": "",
            "skills": [],
            "allowed_tools": [],
            "enabled": True,
        }
        for code, name, cli, template_key, inputs, outputs in role_rows
    ]
    presets = [
        {
            "preset_id": "default-dev",
            "name": "기본 개발",
            "description": "설계-구현-테스트-리뷰",
            "role_codes": ["architect", "coder", "tester", "reviewer"],
        },
        {
            "preset_id": "fast-fix",
            "name": "빠른 수정",
            "description": "원인 파악 후 신속 수정",
            "role_codes": ["incident-analyst", "coder", "tester"],
        },
        {
            "preset_id": "research-first",
            "name": "근거 우선",
            "description": "검색 근거 확보 후 설계/구현",
            "role_codes": ["research-agent", "architect", "coder", "reviewer"],
        },
    ]
    return {"roles": roles, "presets": presets}


def _read_roles_payload(path: Path) -> Dict[str, Any]:
    """Read role/preset payload with safe defaults."""

    defaults = _default_roles_payload()
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults
    if not isinstance(payload, dict):
        return defaults

    roles: List[Dict[str, Any]] = []
    for item in payload.get("roles", []):
        if not isinstance(item, dict):
            continue
        code = _normalize_role_code(str(item.get("code", "")))
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue
        roles.append(
            {
                "code": code,
                "name": name,
                "objective": str(item.get("objective", "")).strip(),
                "cli": str(item.get("cli", "")).strip().lower(),
                "template_key": str(item.get("template_key", "")).strip(),
                "inputs": str(item.get("inputs", "")).strip(),
                "outputs": str(item.get("outputs", "")).strip(),
                "checklist": str(item.get("checklist", "")).strip(),
                "skills": _normalize_role_tag_list(item.get("skills")),
                "allowed_tools": _normalize_role_tag_list(item.get("allowed_tools")),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    if not roles:
        roles = defaults["roles"]

    known_codes = {str(item.get("code", "")) for item in roles}
    presets: List[Dict[str, Any]] = []
    for item in payload.get("presets", []):
        if not isinstance(item, dict):
            continue
        preset_id = _normalize_role_code(str(item.get("preset_id", "")))
        name = str(item.get("name", "")).strip()
        if not preset_id or not name:
            continue
        role_codes = []
        for raw in item.get("role_codes", []):
            code = _normalize_role_code(str(raw))
            if code and code in known_codes and code not in role_codes:
                role_codes.append(code)
        presets.append(
            {
                "preset_id": preset_id,
                "name": name,
                "description": str(item.get("description", "")).strip(),
                "role_codes": role_codes,
            }
        )
    if not presets:
        presets = defaults["presets"]

    roles.sort(key=lambda one: str(one.get("code", "")))
    presets.sort(key=lambda one: str(one.get("preset_id", "")))
    return {"roles": roles, "presets": presets}


def _write_roles_payload(path: Path, payload: Dict[str, Any]) -> None:
    """Persist role/preset payload as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
