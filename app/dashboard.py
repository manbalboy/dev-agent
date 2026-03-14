"""Dashboard router assembly and compatibility wrappers."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from app import dashboard_builder_runtime
from app.agent_cli_runtime import ASSISTANT_PROVIDER_ALIASES, canonical_cli_name
from app.agent_config_runtime import (
    collect_agent_cli_status,
    collect_agent_model_status,
    load_agent_template_config,
    read_command_templates as _read_command_templates,
    update_agent_template_config,
)
from app.assistant_runtime import (
    build_assistant_chat_prompt as _build_assistant_chat_prompt,
    build_log_analysis_prompt as _build_log_analysis_prompt,
)
from app.config import AppSettings
from app.dashboard_admin_metrics_runtime import DashboardAdminMetricsRuntime
from app.dashboard_app_registry_runtime import DashboardAppRegistryRuntime
from app.dashboard_assistant_diagnosis_runtime import DashboardAssistantDiagnosisRuntime
from app.dashboard_assistant_runtime import DashboardAssistantRuntime
from app.dashboard_job_action_runtime import DashboardJobActionRuntime
from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.dashboard_job_detail_runtime import DashboardJobDetailRuntime
from app.dashboard_job_enqueue_runtime import DashboardJobEnqueueRuntime
from app.dashboard_job_list_runtime import DashboardJobListRuntime
from app.dashboard_job_runtime import DashboardJobRuntime
from app.dashboard_job_workflow_runtime import DashboardJobWorkflowRuntime
from app.dashboard_issue_registration_runtime import DashboardIssueRegistrationRuntime
from app.dashboard_memory_admin_runtime import DashboardMemoryAdminRuntime
from app.dashboard_patch_runtime import DashboardPatchRuntime
from app.dashboard_roles_runtime import DashboardRolesRuntime, normalize_role_code, read_roles_payload
from app.dashboard_settings_runtime import DashboardSettingsRuntime
from app.dashboard_view_runtime import DashboardViewRuntime
from app.dashboard_config_router import router as dashboard_config_router
from app.dashboard_job_router import router as dashboard_job_router
from app.dashboard_operator_router import router as dashboard_operator_router
from app.dashboard_write_router import router as dashboard_write_router
from app.durable_runtime_hygiene import DurableRuntimeHygieneRuntime
from app.durable_runtime_self_check import DurableRuntimeSelfCheckRuntime
from app.failure_classification import build_failure_classification_summary
from app.feature_flags import feature_flags_payload, read_feature_flags, write_feature_flags
from app.memory import MemoryRuntimeStore
from app.models import JobRecord, utc_now_iso
from app.patch_backup_runtime import PatchBackupRuntime
from app.patch_control_runtime import PatchControlRuntime
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.patch_updater_runtime import PatchUpdaterRuntime
from app.runtime_recovery_trace import append_runtime_recovery_trace_for_job
from app.security_governance_runtime import SecurityGovernanceRuntime
from app.self_check_alert_delivery_runtime import SelfCheckAlertDeliveryRuntime
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
from app.workflow_resolution import list_known_workflow_ids, resolve_workflow_selection


router = APIRouter(tags=["dashboard"])
router.include_router(dashboard_config_router)
router.include_router(dashboard_job_router)
router.include_router(dashboard_operator_router)
router.include_router(dashboard_write_router)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_TIMESTAMPED_LINE_PATTERN = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<msg>.*)$")
_TRACK_CHOICES = {"new", "enhance", "bug", "long", "ultra", "ultra10"}
_APPS_CONFIG_PATH = Path.cwd() / "config" / "apps.json"
_WORKFLOWS_CONFIG_PATH = Path.cwd() / "config" / "workflows.json"
_ROLES_CONFIG_PATH = Path.cwd() / "config" / "roles.json"
_FEATURE_FLAGS_CONFIG_PATH = Path.cwd() / "config" / "feature_flags.json"
_PATCH_REPO_ROOT = Path.cwd()
_PRIMARY_ASSISTANT_PROVIDERS = {"codex", "gemini"}


def _build_dashboard_job_runtime(store: JobStore | None, settings: AppSettings) -> DashboardJobRuntime:
    """Build one job-detail helper runtime while preserving dashboard wrappers."""

    return dashboard_builder_runtime.build_dashboard_job_runtime(store, settings)


def _build_dashboard_job_artifact_runtime(
    settings: AppSettings,
) -> DashboardJobArtifactRuntime:
    """Build one job artifact/log read runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_job_artifact_runtime(settings)


def _build_dashboard_job_list_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobListRuntime:
    """Build one job-list read runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_job_list_runtime(store, settings)


def _build_dashboard_job_workflow_runtime(
    settings: AppSettings,
) -> DashboardJobWorkflowRuntime:
    """Build one job-workflow helper runtime while preserving dashboard wrappers."""

    return dashboard_builder_runtime.build_dashboard_job_workflow_runtime(settings)


def _build_dashboard_job_detail_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobDetailRuntime:
    """Build one job-detail read runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_job_detail_runtime(store, settings)


def _build_dashboard_view_runtime(
    store: JobStore | None,
    settings: AppSettings,
) -> DashboardViewRuntime:
    """Build one HTML shell / log view runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_view_runtime(store, settings)


def _build_dashboard_app_registry_runtime(
    settings: AppSettings,
) -> DashboardAppRegistryRuntime:
    """Build one app-registry runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_app_registry_runtime(settings)


def _build_dashboard_settings_runtime(
    settings: AppSettings,
) -> DashboardSettingsRuntime:
    """Build one settings helper runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_settings_runtime(settings)


def _build_dashboard_assistant_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardAssistantRuntime:
    """Build one assistant helper runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_assistant_runtime(store, settings)


def _build_dashboard_assistant_diagnosis_runtime(
    settings: AppSettings,
) -> DashboardAssistantDiagnosisRuntime:
    """Build one assistant diagnosis runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_assistant_diagnosis_runtime(settings)


def _build_dashboard_memory_admin_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardMemoryAdminRuntime:
    """Build one memory-admin helper runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_memory_admin_runtime(store, settings)


def _build_dashboard_issue_registration_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardIssueRegistrationRuntime:
    """Build one issue-registration helper runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_issue_registration_runtime(store, settings)


def _build_dashboard_job_enqueue_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobEnqueueRuntime:
    """Build one shared job-enqueue support runtime for dashboard-originated jobs."""

    return dashboard_builder_runtime.build_dashboard_job_enqueue_runtime(store, settings)


def _build_dashboard_admin_metrics_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardAdminMetricsRuntime:
    """Build one admin-metrics helper runtime while preserving dashboard wrappers."""

    return dashboard_builder_runtime.build_dashboard_admin_metrics_runtime(store, settings)


def _build_dashboard_job_action_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobActionRuntime:
    """Build one job-action runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_job_action_runtime(store, settings)


def _build_patch_control_runtime() -> PatchControlRuntime:
    """Build one patch-status runtime for dashboard operator use."""

    return dashboard_builder_runtime.build_patch_control_runtime()


def _build_patch_service_runtime(store: JobStore, settings: AppSettings) -> PatchServiceRuntime:
    """Build one patch service/drain runtime for operator controls and updater."""

    return dashboard_builder_runtime.build_patch_service_runtime(store, settings)


def _build_patch_backup_runtime(settings: AppSettings) -> PatchBackupRuntime:
    """Build one backup runtime for patch/rollback/restore operations."""

    return dashboard_builder_runtime.build_patch_backup_runtime(settings)


def _build_dashboard_patch_runtime(store: JobStore, settings: AppSettings) -> DashboardPatchRuntime:
    """Build one patch-run runtime for dashboard operator use."""

    return dashboard_builder_runtime.build_dashboard_patch_runtime(store, settings)


def _build_durable_runtime_hygiene_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DurableRuntimeHygieneRuntime:
    """Build one durable runtime hygiene helper for operator audit/cleanup."""

    return dashboard_builder_runtime.build_durable_runtime_hygiene_runtime(store, settings)


def _build_patch_updater_runtime(store: JobStore, settings: AppSettings) -> PatchUpdaterRuntime:
    """Build one updater-status runtime for dashboard operator visibility."""

    return dashboard_builder_runtime.build_patch_updater_runtime(store, settings)


def _build_patch_health_runtime(store: JobStore, settings: AppSettings) -> PatchHealthRuntime:
    """Build one post-update health helper for patch/self-check visibility."""

    return dashboard_builder_runtime.build_patch_health_runtime(store, settings)


def _build_security_governance_runtime(settings: AppSettings) -> SecurityGovernanceRuntime:
    """Build one security / TLS / governance helper for operator visibility."""

    return dashboard_builder_runtime.build_security_governance_runtime(settings)


def _build_self_check_alert_delivery_runtime(
    settings: AppSettings,
) -> SelfCheckAlertDeliveryRuntime:
    """Build one persisted webhook delivery helper for self-check alerts."""

    return dashboard_builder_runtime.build_self_check_alert_delivery_runtime(settings)


def _build_durable_runtime_self_check_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DurableRuntimeSelfCheckRuntime:
    """Build one persisted durable runtime self-check helper."""

    return dashboard_builder_runtime.build_durable_runtime_self_check_runtime(store, settings)


def _build_dashboard_roles_runtime() -> DashboardRolesRuntime:
    """Build one shared roles/presets runtime while preserving dashboard routes."""

    return dashboard_builder_runtime.build_dashboard_roles_runtime()


def _job_workspace_path(job: JobRecord, settings: AppSettings) -> Path:
    """Resolve workspace path using execution repository, not issue hub repository."""

    return dashboard_builder_runtime.job_workspace_path(job, settings)


def _memory_runtime_db_path(settings: AppSettings) -> Path:
    """Return canonical SQLite DB path for memory runtime."""

    return dashboard_builder_runtime.memory_runtime_db_path(settings)


def _get_memory_runtime_store(settings: AppSettings) -> MemoryRuntimeStore:
    """Return canonical memory runtime store for admin/search APIs."""

    return dashboard_builder_runtime.get_memory_runtime_store(settings)


def _classify_command_target(command: str) -> str:
    """Infer command target actor for conversation-style timeline."""

    return dashboard_builder_runtime.classify_command_target(command)


def _extract_issue_number(issue_url: str) -> int:
    """Extract issue number from GitHub issue URL."""

    return dashboard_builder_runtime.extract_issue_number(issue_url)


def _extract_issue_url(stdout: str) -> str:
    """Extract issue URL from gh output text."""

    return dashboard_builder_runtime.extract_issue_url(stdout)


def _run_gh_command(args: List[str], error_context: str) -> str:
    """Run gh command with consistent error mapping."""

    return dashboard_builder_runtime.run_gh_command(args, error_context)


def _run_log_analyzer(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one log-analysis request while keeping dashboard monkeypatch points stable."""

    return dashboard_builder_runtime.run_log_analyzer(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
    )


def _run_assistant_chat_provider(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    """Dispatch one chat request while keeping dashboard monkeypatch points stable."""

    return dashboard_builder_runtime.run_assistant_chat_provider(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
    )


def _run_codex_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return dashboard_builder_runtime.run_codex_chat_completion(prompt, templates)


def _run_gemini_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return dashboard_builder_runtime.run_gemini_chat_completion(prompt, templates)


def _run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return dashboard_builder_runtime.run_codex_log_analysis(prompt, templates)


def _run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Thin compatibility wrapper around extracted assistant runtime."""

    return dashboard_builder_runtime.run_gemini_log_analysis(prompt, templates)


def _run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Claude alias maintained for compatibility and routed to Codex."""

    return dashboard_builder_runtime.run_claude_log_analysis(prompt, templates)


def _run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    """Legacy Copilot alias maintained for compatibility and routed to Codex."""

    return dashboard_builder_runtime.run_copilot_log_analysis(prompt, templates)


def _ensure_agent_run_label(repository: str) -> None:
    """Ensure `agent:run` label exists in the target repository."""

    dashboard_builder_runtime.ensure_agent_run_label(repository)


def _ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
    """Ensure one GitHub label exists in the target repository."""

    dashboard_builder_runtime.ensure_label(repository, label_name, color, description)


def _normalize_repository_ref(value: str) -> str:
    """Normalize GitHub repository input to owner/repo form."""

    return dashboard_builder_runtime.normalize_repository_ref(value)


def _stop_signal_path(data_dir: Path, job_id: str) -> Path:
    """Return stop signal file path for one job."""

    return dashboard_builder_runtime.stop_signal_path(data_dir, job_id)


def _read_registered_apps(
    path: Path,
    repository: str,
    default_workflow_id: str = "",
) -> List[Dict[str, str]]:
    """Read app registration list from JSON file with a default fallback."""

    return dashboard_builder_runtime.read_registered_apps(
        path,
        repository,
        default_workflow_id=default_workflow_id,
    )


def _write_registered_apps(path: Path, apps: List[Dict[str, str]]) -> None:
    """Persist app list as pretty JSON."""

    dashboard_builder_runtime.write_registered_apps(path, apps)


def _read_default_workflow_id(path: Path) -> str:
    """Read default workflow id from workflow config with safe fallback."""

    return dashboard_builder_runtime.read_default_workflow_id(path)
