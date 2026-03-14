"""Shared dashboard builder implementations behind compatibility wrappers."""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Dict, List

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
from app.dashboard_compat_runtime import DashboardCompatRuntime
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
from app.durable_runtime_hygiene import DurableRuntimeHygieneRuntime
from app.durable_runtime_self_check import DurableRuntimeSelfCheckRuntime
from app.memory import MemoryRuntimeStore
from app.models import JobRecord
from app.patch_backup_runtime import PatchBackupRuntime
from app.patch_control_runtime import PatchControlRuntime
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.patch_updater_runtime import PatchUpdaterRuntime
from app.security_governance_runtime import SecurityGovernanceRuntime
from app.self_check_alert_delivery_runtime import SelfCheckAlertDeliveryRuntime
from app.store import JobStore


def _dashboard_module():
    import app.dashboard as dashboard

    return dashboard


def build_dashboard_job_runtime(store: JobStore | None, settings: AppSettings) -> DashboardJobRuntime:
    dashboard = _dashboard_module()
    artifact_runtime = dashboard._build_dashboard_job_artifact_runtime(settings)
    workflow_runtime = dashboard._build_dashboard_job_workflow_runtime(settings)
    return DashboardJobRuntime(
        store=store,
        settings=settings,
        get_memory_runtime_store=lambda: dashboard._get_memory_runtime_store(settings),
        compute_job_resume_state=lambda job, node_runs, _settings: workflow_runtime.compute_job_resume_payload(
            job,
            node_runs,
        ),
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": artifact_runtime.resolve_channel_log_path(
            file_name,
            channel,
        ),
    )


def build_dashboard_job_artifact_runtime(
    settings: AppSettings,
) -> DashboardJobArtifactRuntime:
    dashboard = _dashboard_module()
    return DashboardJobArtifactRuntime(
        settings=settings,
        timestamped_line_pattern=dashboard._TIMESTAMPED_LINE_PATTERN,
        classify_command_target=dashboard._classify_command_target,
    )


def build_dashboard_job_list_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobListRuntime:
    dashboard = _dashboard_module()
    job_runtime = dashboard._build_dashboard_job_runtime(store, settings)
    return DashboardJobListRuntime(
        store=store,
        track_choices=sorted(dashboard._TRACK_CHOICES),
        build_job_runtime_signals=job_runtime.build_job_runtime_signals,
        build_failure_classification_summary=lambda job: dashboard.build_failure_classification_summary(
            job=job,
            runtime_recovery_trace=None,
        ),
    )


def build_dashboard_job_workflow_runtime(
    settings: AppSettings,
) -> DashboardJobWorkflowRuntime:
    dashboard = _dashboard_module()
    return DashboardJobWorkflowRuntime(
        apps_config_path=dashboard._APPS_CONFIG_PATH,
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
        load_workflows=dashboard.load_workflows,
        default_workflow_template=dashboard.default_workflow_template,
        resolve_workflow_selection=dashboard.resolve_workflow_selection,
        validate_workflow=dashboard.validate_workflow,
        linearize_workflow_nodes=dashboard.linearize_workflow_nodes,
        job_workspace_path=lambda job: dashboard._job_workspace_path(job, settings),
        build_workflow_artifact_paths=dashboard.build_workflow_artifact_paths,
        read_improvement_runtime_context=dashboard.read_improvement_runtime_context,
        compute_workflow_resume_state=dashboard.compute_workflow_resume_state,
        list_manual_resume_candidates=dashboard.list_manual_resume_candidates,
    )


def build_dashboard_job_detail_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobDetailRuntime:
    dashboard = _dashboard_module()
    artifact_runtime = dashboard._build_dashboard_job_artifact_runtime(settings)
    job_runtime = dashboard._build_dashboard_job_runtime(store, settings)
    workflow_runtime = dashboard._build_dashboard_job_workflow_runtime(settings)
    return DashboardJobDetailRuntime(
        store=store,
        resolve_debug_log_path=lambda job: artifact_runtime.resolve_channel_log_path(job.log_file, channel="debug"),
        parse_log_events=artifact_runtime.parse_log_events,
        job_workspace_path=job_runtime.job_workspace_path,
        read_agent_md_files=artifact_runtime.read_agent_md_files,
        read_stage_md_snapshots=artifact_runtime.read_stage_md_snapshots,
        resolve_job_workflow_runtime=workflow_runtime.resolve_job_workflow_runtime,
        extract_workflow_fallback_events=workflow_runtime.extract_workflow_fallback_events,
        compute_job_resume_state=workflow_runtime.compute_job_resume_payload,
        build_job_runtime_signals=job_runtime.build_job_runtime_signals,
        read_job_memory_trace=job_runtime.read_job_memory_trace,
        read_job_assistant_diagnosis_trace=job_runtime.read_job_assistant_diagnosis_trace,
        read_job_runtime_recovery_trace=job_runtime.read_job_runtime_recovery_trace,
        build_failure_classification_summary=lambda job, runtime_recovery_trace: dashboard.build_failure_classification_summary(
            job=job,
            runtime_recovery_trace=runtime_recovery_trace,
        ),
        build_job_needs_human_summary=lambda job, runtime_recovery_trace, failure_classification: job_runtime.build_job_needs_human_summary(
            job,
            runtime_recovery_trace=runtime_recovery_trace,
            failure_classification=failure_classification,
        ),
        build_job_dead_letter_summary=lambda job, runtime_recovery_trace, failure_classification: job_runtime.build_job_dead_letter_summary(
            job,
            runtime_recovery_trace=runtime_recovery_trace,
            failure_classification=failure_classification,
        ),
        build_job_dead_letter_action_trail=lambda runtime_recovery_trace: job_runtime.build_job_dead_letter_action_trail(
            runtime_recovery_trace=runtime_recovery_trace,
        ),
        build_job_requeue_reason_summary=lambda job, runtime_recovery_trace: job_runtime.build_job_requeue_reason_summary(
            job,
            runtime_recovery_trace=runtime_recovery_trace,
        ),
        build_job_self_growing_effectiveness=job_runtime.build_job_self_growing_effectiveness,
        build_job_mobile_e2e_result=job_runtime.build_job_mobile_e2e_result,
        build_manual_retry_options=lambda job, node_runs: workflow_runtime.build_manual_retry_options(
            job,
            node_runs=node_runs,
        ),
        build_job_lineage=job_runtime.build_job_lineage,
        build_job_log_summary=lambda job, events: job_runtime.build_job_log_summary(
            job,
            events=events,
        ),
        build_job_operator_inputs=job_runtime.build_job_operator_inputs,
        build_job_integration_operator_boundary=job_runtime.build_job_integration_operator_boundary,
        build_job_integration_usage_trail=job_runtime.build_job_integration_usage_trail,
        build_job_integration_health_facets=lambda job, integration_operator_boundary, integration_usage_trail, log_summary, failure_classification: job_runtime.build_job_integration_health_facets(
            job=job,
            integration_operator_boundary=integration_operator_boundary,
            integration_usage_trail=integration_usage_trail,
            log_summary=log_summary,
            failure_classification=failure_classification,
        ),
        stop_signal_exists=lambda job_id: dashboard._stop_signal_path(settings.data_dir, job_id).exists(),
    )


def build_dashboard_view_runtime(
    store: JobStore | None,
    settings: AppSettings,
) -> DashboardViewRuntime:
    dashboard = _dashboard_module()
    return DashboardViewRuntime(
        store=store,
        templates=dashboard._templates,
        artifact_runtime=dashboard._build_dashboard_job_artifact_runtime(settings),
    )


def build_dashboard_app_registry_runtime(
    settings: AppSettings,
) -> DashboardAppRegistryRuntime:
    dashboard = _dashboard_module()
    return DashboardAppRegistryRuntime(
        allowed_repository=settings.allowed_repository,
        track_choices=sorted(dashboard._TRACK_CHOICES),
        read_registered_apps=dashboard._read_registered_apps,
        write_registered_apps=dashboard._write_registered_apps,
        read_default_workflow_id=dashboard._read_default_workflow_id,
        load_workflows=dashboard.load_workflows,
        default_workflow_template=dashboard.default_workflow_template,
        normalize_app_code=DashboardJobEnqueueRuntime.normalize_app_code,
        normalize_repository_ref=dashboard._normalize_repository_ref,
        ensure_label=dashboard._ensure_label,
        apps_config_path=dashboard._APPS_CONFIG_PATH,
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
    )


def build_dashboard_settings_runtime(
    settings: AppSettings,
) -> DashboardSettingsRuntime:
    dashboard = _dashboard_module()
    return DashboardSettingsRuntime(
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
        feature_flags_config_path=dashboard._FEATURE_FLAGS_CONFIG_PATH,
        command_config_path=settings.command_config,
        env_path=Path.cwd() / ".env",
        enable_escalation_fallback=settings.enable_escalation,
        schema_payload=dashboard.schema_payload,
        load_workflows=dashboard.load_workflows,
        save_workflows=dashboard.save_workflows,
        validate_workflow=dashboard.validate_workflow,
        default_workflow_template=dashboard.default_workflow_template,
        feature_flags_payload=dashboard.feature_flags_payload,
        write_feature_flags=dashboard.write_feature_flags,
        load_agent_template_config=dashboard.load_agent_template_config,
        update_agent_template_config=dashboard.update_agent_template_config,
        collect_agent_cli_status=dashboard.collect_agent_cli_status,
        collect_agent_model_status=dashboard.collect_agent_model_status,
    )


def build_dashboard_assistant_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardAssistantRuntime:
    dashboard = _dashboard_module()
    artifact_runtime = dashboard._build_dashboard_job_artifact_runtime(settings)
    diagnosis_runtime = dashboard._build_dashboard_assistant_diagnosis_runtime(settings)
    return DashboardAssistantRuntime(
        store=store,
        settings=settings,
        primary_assistant_providers=sorted(dashboard._PRIMARY_ASSISTANT_PROVIDERS),
        assistant_provider_aliases=dashboard.ASSISTANT_PROVIDER_ALIASES,
        canonical_cli_name=dashboard.canonical_cli_name,
        build_focus_job_log_context=lambda job, runtime_settings: artifact_runtime.build_focus_job_log_context(job),
        build_agent_observability_context=lambda runtime_store, runtime_settings: diagnosis_runtime.build_agent_observability_context(
            runtime_store
        ),
        run_assistant_diagnosis_loop=lambda job, question, settings=None, assistant_scope="log_analysis": diagnosis_runtime.run_assistant_diagnosis_loop(
            job=job,
            question=question,
            assistant_scope=assistant_scope,
        ),
        build_assistant_chat_prompt=dashboard._build_assistant_chat_prompt,
        build_log_analysis_prompt=dashboard._build_log_analysis_prompt,
        read_command_templates=dashboard._read_command_templates,
        run_assistant_chat_provider=dashboard._run_assistant_chat_provider,
        run_log_analyzer=dashboard._run_log_analyzer,
    )


def build_dashboard_assistant_diagnosis_runtime(
    settings: AppSettings,
) -> DashboardAssistantDiagnosisRuntime:
    dashboard = _dashboard_module()
    return DashboardAssistantDiagnosisRuntime(
        settings=settings,
        feature_flags_config_path=dashboard._FEATURE_FLAGS_CONFIG_PATH,
        artifact_runtime=dashboard._build_dashboard_job_artifact_runtime(settings),
        get_memory_runtime_store=dashboard._get_memory_runtime_store,
        read_feature_flags=dashboard.read_feature_flags,
        build_workflow_artifact_paths=dashboard.build_workflow_artifact_paths,
        utc_now_iso=dashboard.utc_now_iso,
    )


def build_dashboard_memory_admin_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardMemoryAdminRuntime:
    dashboard = _dashboard_module()
    enqueue_runtime = dashboard._build_dashboard_job_enqueue_runtime(store, settings)
    return DashboardMemoryAdminRuntime(
        store=store,
        settings=settings,
        get_memory_runtime_store=dashboard._get_memory_runtime_store,
        utc_now_iso=dashboard.utc_now_iso,
        queue_followup_job_from_backlog_candidate=enqueue_runtime.queue_followup_job_from_backlog_candidate,
    )


def build_dashboard_issue_registration_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardIssueRegistrationRuntime:
    dashboard = _dashboard_module()
    return DashboardIssueRegistrationRuntime(
        store=store,
        settings=settings,
        apps_config_path=dashboard._APPS_CONFIG_PATH,
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
        roles_config_path=dashboard._ROLES_CONFIG_PATH,
        ensure_patch_accepting_new_jobs=lambda: dashboard._build_patch_service_runtime(
            store,
            settings,
        ).ensure_patch_accepting_new_jobs(),
        normalize_app_code=DashboardJobEnqueueRuntime.normalize_app_code,
        normalize_track=DashboardJobEnqueueRuntime.normalize_track,
        detect_title_track=DashboardJobEnqueueRuntime.detect_title_track,
        normalize_role_code=dashboard.normalize_role_code,
        read_registered_apps=dashboard._read_registered_apps,
        list_known_workflow_ids=dashboard.list_known_workflow_ids,
        read_roles_payload=dashboard.read_roles_payload,
        run_gh_command=dashboard._run_gh_command,
        extract_issue_url=dashboard._extract_issue_url,
        extract_issue_number=dashboard._extract_issue_number,
        ensure_agent_run_label=dashboard._ensure_agent_run_label,
        ensure_label=dashboard._ensure_label,
        find_active_job=DashboardJobEnqueueRuntime.find_active_job,
        resolve_workflow_selection=dashboard.resolve_workflow_selection,
        build_branch_name=DashboardJobEnqueueRuntime.build_branch_name,
        build_log_file_name=DashboardJobEnqueueRuntime.build_log_file_name,
        utc_now_iso=dashboard.utc_now_iso,
        uuid_factory=lambda: str(uuid.uuid4()),
    )


def build_dashboard_job_enqueue_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobEnqueueRuntime:
    dashboard = _dashboard_module()
    return DashboardJobEnqueueRuntime(
        store=store,
        settings=settings,
        apps_config_path=dashboard._APPS_CONFIG_PATH,
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
        resolve_workflow_selection=dashboard.resolve_workflow_selection,
        build_workflow_artifact_paths=dashboard.build_workflow_artifact_paths,
        utc_now_iso=dashboard.utc_now_iso,
        uuid_factory=lambda: str(uuid.uuid4()),
    )


def build_dashboard_admin_metrics_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardAdminMetricsRuntime:
    dashboard = _dashboard_module()
    job_runtime = dashboard._build_dashboard_job_runtime(store, settings)
    return DashboardAdminMetricsRuntime(
        store=store,
        settings=settings,
        feature_flags_config_path=dashboard._FEATURE_FLAGS_CONFIG_PATH,
        apps_config_path=dashboard._APPS_CONFIG_PATH,
        workflows_config_path=dashboard._WORKFLOWS_CONFIG_PATH,
        roles_config_path=dashboard._ROLES_CONFIG_PATH,
        list_dashboard_jobs=lambda runtime_store, runtime_settings: dashboard._build_dashboard_job_list_runtime(
            runtime_store,
            runtime_settings,
        ).list_dashboard_jobs(),
        build_job_summary=DashboardJobListRuntime.build_job_summary,
        read_default_workflow_id=dashboard._read_default_workflow_id,
        read_registered_apps=dashboard._read_registered_apps,
        read_roles_payload=dashboard.read_roles_payload,
        get_memory_runtime_store=dashboard._get_memory_runtime_store,
        read_dashboard_json=DashboardJobRuntime.read_dashboard_json,
        read_dashboard_jsonl=DashboardJobRuntime.read_dashboard_jsonl,
        job_workspace_path=lambda job, _settings: job_runtime.job_workspace_path(job),
        read_job_assistant_diagnosis_trace=lambda job, _settings: job_runtime.read_job_assistant_diagnosis_trace(job),
        top_counter_items=DashboardJobRuntime.top_counter_items,
        safe_average=DashboardJobRuntime.safe_average,
        latest_non_empty=DashboardJobRuntime.latest_non_empty,
        utc_now_iso=dashboard.utc_now_iso,
    )


def build_dashboard_job_action_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DashboardJobActionRuntime:
    dashboard = _dashboard_module()
    workflow_runtime = dashboard._build_dashboard_job_workflow_runtime(settings)
    return DashboardJobActionRuntime(
        store=store,
        settings=settings,
        stop_signal_path=dashboard._stop_signal_path,
        resolve_job_workflow_definition=workflow_runtime.resolve_job_workflow_definition,
        compute_job_resume_state=lambda job, node_runs, _settings: workflow_runtime.compute_job_resume_payload(
            job,
            node_runs,
        ),
        validate_manual_resume_target=dashboard.validate_manual_resume_target,
        append_runtime_recovery_trace_for_job=dashboard.append_runtime_recovery_trace_for_job,
        ensure_patch_accepting_new_jobs=lambda: dashboard._build_patch_service_runtime(
            store,
            settings,
        ).ensure_patch_accepting_new_jobs(),
    )


def build_patch_control_runtime() -> PatchControlRuntime:
    dashboard = _dashboard_module()
    return PatchControlRuntime(
        repo_root=dashboard._PATCH_REPO_ROOT,
        utc_now_iso=dashboard.utc_now_iso,
    )


def build_patch_service_runtime(store: JobStore, settings: AppSettings) -> PatchServiceRuntime:
    return PatchServiceRuntime(
        store=store,
        patch_lock_file=settings.patch_lock_file,
        api_service_name=settings.patch_api_service_name,
        worker_service_name=settings.patch_worker_service_name,
        utc_now_iso=_dashboard_module().utc_now_iso,
    )


def build_patch_backup_runtime(settings: AppSettings) -> PatchBackupRuntime:
    return PatchBackupRuntime(
        backups_dir=settings.patch_backups_dir,
        data_root=settings.data_dir,
        state_files={
            "jobs": settings.jobs_file,
            "queue": settings.queue_file,
            "node_runs": settings.data_dir / "node_runs.json",
            "runtime_inputs": settings.data_dir / "runtime_inputs.json",
            "integrations": settings.data_dir / "integrations.json",
            "patch_runs": settings.data_dir / "patch_runs.json",
            "sqlite": settings.sqlite_file,
        },
        utc_now_iso=_dashboard_module().utc_now_iso,
    )


def build_dashboard_patch_runtime(store: JobStore, settings: AppSettings) -> DashboardPatchRuntime:
    dashboard = _dashboard_module()
    return DashboardPatchRuntime(
        store=store,
        build_patch_control_runtime=dashboard._build_patch_control_runtime,
        build_patch_backup_runtime=lambda: dashboard._build_patch_backup_runtime(settings),
        utc_now_iso=dashboard.utc_now_iso,
    )


def build_durable_runtime_hygiene_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DurableRuntimeHygieneRuntime:
    return DurableRuntimeHygieneRuntime(
        store=store,
        settings=settings,
        utc_now_iso=_dashboard_module().utc_now_iso,
        report_file=settings.durable_runtime_hygiene_report_file,
    )


def build_patch_updater_runtime(store: JobStore, settings: AppSettings) -> PatchUpdaterRuntime:
    dashboard = _dashboard_module()
    return PatchUpdaterRuntime(
        store=store,
        status_file=settings.patch_updater_status_file,
        service_name=settings.patch_updater_service_name,
        utc_now_iso=dashboard.utc_now_iso,
        patch_service_runtime=dashboard._build_patch_service_runtime(store, settings),
    )


def build_patch_health_runtime(store: JobStore, settings: AppSettings) -> PatchHealthRuntime:
    dashboard = _dashboard_module()
    return PatchHealthRuntime(
        store=store,
        patch_service_runtime=dashboard._build_patch_service_runtime(store, settings),
        api_health_url=f"http://127.0.0.1:{settings.api_port}/healthz",
        updater_status_file=settings.patch_updater_status_file,
        updater_service_name=settings.patch_updater_service_name,
        utc_now_iso=dashboard.utc_now_iso,
    )


def build_security_governance_runtime(settings: AppSettings) -> SecurityGovernanceRuntime:
    return SecurityGovernanceRuntime(
        settings=settings,
        utc_now_iso=_dashboard_module().utc_now_iso,
    )


def build_self_check_alert_delivery_runtime(
    settings: AppSettings,
) -> SelfCheckAlertDeliveryRuntime:
    return SelfCheckAlertDeliveryRuntime(
        webhook_url=settings.self_check_alert_webhook_url,
        critical_webhook_url=settings.self_check_alert_critical_webhook_url,
        delivery_file=settings.durable_runtime_self_check_alert_delivery_file,
        utc_now_iso=_dashboard_module().utc_now_iso,
        repeat_minutes=settings.self_check_alert_repeat_minutes,
        failure_backoff_max_minutes=settings.self_check_alert_failure_backoff_max_minutes,
        timeout_seconds=settings.self_check_alert_webhook_timeout_seconds,
    )


def build_durable_runtime_self_check_runtime(
    store: JobStore,
    settings: AppSettings,
) -> DurableRuntimeSelfCheckRuntime:
    dashboard = _dashboard_module()
    delivery_runtime = dashboard._build_self_check_alert_delivery_runtime(settings)
    return DurableRuntimeSelfCheckRuntime(
        build_patch_status=lambda: dashboard._build_patch_control_runtime().build_patch_status(refresh=False),
        build_patch_run_payload=lambda: dashboard._build_dashboard_patch_runtime(
            store,
            settings,
        ).get_latest_patch_run_payload(),
        build_patch_updater_status=lambda: dashboard._build_patch_updater_runtime(
            store,
            settings,
        ).read_status_payload(),
        build_patch_health_payload=lambda: dashboard._build_patch_health_runtime(
            store,
            settings,
        ).build_post_update_health_payload(),
        build_hygiene_status=lambda: dashboard._build_durable_runtime_hygiene_runtime(
            store,
            settings,
        ).build_hygiene_status(),
        build_security_status=lambda: dashboard._build_security_governance_runtime(settings).build_status(),
        utc_now_iso=dashboard.utc_now_iso,
        report_file=settings.durable_runtime_self_check_report_file,
        alert_file=settings.durable_runtime_self_check_alert_file,
        delivery_file=settings.durable_runtime_self_check_alert_delivery_file,
        read_alert_delivery=lambda alert, report: delivery_runtime.read_status(
            alert=alert,
            report=report,
        ),
        deliver_alert=lambda alert, report: delivery_runtime.process_alert(
            alert=alert,
            report=report,
        ),
        stale_after_minutes=settings.self_check_stale_minutes,
    )


def build_dashboard_roles_runtime() -> DashboardRolesRuntime:
    return DashboardRolesRuntime()


def job_workspace_path(job: JobRecord, settings: AppSettings) -> Path:
    return settings.repository_workspace_path(DashboardJobRuntime.job_execution_repository(job), job.app_code)


def memory_runtime_db_path(settings: AppSettings) -> Path:
    return settings.resolved_memory_dir / "memory_runtime.db"


def get_memory_runtime_store(settings: AppSettings) -> MemoryRuntimeStore:
    return MemoryRuntimeStore(memory_runtime_db_path(settings))


def classify_command_target(command: str) -> str:
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


def extract_issue_number(issue_url: str) -> int:
    return DashboardCompatRuntime.extract_issue_number(issue_url)


def extract_issue_url(stdout: str) -> str:
    return DashboardCompatRuntime.extract_issue_url(stdout)


def run_gh_command(args: List[str], error_context: str) -> str:
    return DashboardCompatRuntime.run_gh_command(args, error_context)


def run_log_analyzer(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    dashboard = _dashboard_module()
    return DashboardCompatRuntime.run_log_analyzer(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
        run_codex_log_analysis=dashboard._run_codex_log_analysis,
        run_gemini_log_analysis=dashboard._run_gemini_log_analysis,
    )


def run_assistant_chat_provider(
    *,
    assistant: str,
    prompt: str,
    templates: Dict[str, str],
) -> str:
    dashboard = _dashboard_module()
    return DashboardCompatRuntime.run_assistant_chat_provider(
        assistant=assistant,
        prompt=prompt,
        templates=templates,
        run_codex_chat_completion=dashboard._run_codex_chat_completion,
        run_gemini_chat_completion=dashboard._run_gemini_chat_completion,
    )


def run_codex_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_codex_chat_completion(prompt, templates)


def run_gemini_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_gemini_chat_completion(prompt, templates)


def run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_codex_log_analysis(prompt, templates)


def run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_gemini_log_analysis(prompt, templates)


def run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_claude_log_analysis(prompt, templates)


def run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
    return DashboardCompatRuntime.run_copilot_log_analysis(prompt, templates)


def ensure_agent_run_label(repository: str) -> None:
    DashboardCompatRuntime.ensure_agent_run_label(repository)


def ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
    DashboardCompatRuntime.ensure_label(repository, label_name, color, description)


def normalize_repository_ref(value: str) -> str:
    return DashboardCompatRuntime.normalize_repository_ref(value)


def stop_signal_path(data_dir: Path, job_id: str) -> Path:
    return DashboardViewRuntime.stop_signal_path(data_dir, job_id)


def read_registered_apps(
    path: Path,
    repository: str,
    default_workflow_id: str = "",
) -> List[Dict[str, str]]:
    return DashboardCompatRuntime.read_registered_apps(path, repository, default_workflow_id=default_workflow_id)


def write_registered_apps(path: Path, apps: List[Dict[str, str]]) -> None:
    DashboardCompatRuntime.write_registered_apps(path, apps)


def read_default_workflow_id(path: Path) -> str:
    return DashboardCompatRuntime.read_default_workflow_id(path)
