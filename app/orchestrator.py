"""Rule-based orchestration engine for AgentHub jobs.

Important design principle:
- This module is the conductor.
- AI CLIs are workers called at fixed points.
- The order, retries, and termination conditions are code-driven.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import shlex
from typing import Any, Callable, Dict, List, Optional, Set

from app.command_runner import (
    CommandExecutionError,
    CommandTemplateRunner,
    run_shell_command,
)
from app.ai_route_runtime import AIRouteRuntime
from app.ai_role_routing import AIRoleRouter
from app.app_type_runtime import AppTypeRuntime
from app.artifact_io_runtime import ArtifactIoRuntime
from app.config import AppSettings
from app.fixed_pipeline_runtime import FixedPipelineRuntime
from app.implement_runtime import ImplementRuntime
from app.improvement_runtime import ImprovementRuntime
from app.integration_guide_runtime import IntegrationGuideRuntime
from app.integration_recommendation_runtime import IntegrationRecommendationRuntime
from app.integration_usage_runtime import IntegrationUsageRuntime
from app.issue_spec_runtime import IssueSpecRuntime
from app.job_control_runtime import JobControlRuntime
from app.job_execution_runtime import JobExecutionRuntime
from app.job_failure_runtime import JobFailureRuntime
from app.job_mode_runtime import JobModeRuntime
from app.memory_retrieval_runtime import MemoryRetrievalRuntime
from app.memory_quality_runtime import MemoryQualityRuntime
from app.orchestrator_runtime_input_runtime import OrchestratorRuntimeInputRuntime
from app.orchestrator_context_runtime import OrchestratorContextRuntime
from app.structured_memory_runtime import StructuredMemoryRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.prompt_builder import build_spec_json, build_spec_markdown
from app.content_stage_runtime import ContentStageRuntime
from app.docs_snapshot_runtime import DocsSnapshotRuntime
from app.design_governance_runtime import DesignGovernanceRuntime
from app.planner_runtime import PlannerRuntime
from app.preview_runtime import PreviewRuntime
from app.product_definition_runtime import ProductDefinitionRuntime
from app.product_review_runtime import ProductReviewRuntime
from app.provider_runtime import ProviderRuntime
from app.repository_stage_runtime import RepositoryStageRuntime
from app.spec_tools import (
    repo_context_reader,
    risk_policy_checker,
    spec_rewriter,
    spec_schema_validator,
    issue_reader,
)
from app.store import JobStore
from app.workflow_registry import (
    WORKFLOW_NODE_HANDLER_NAMES,
)
from app.workflow_resume import (
    build_workflow_artifact_paths,
    read_improvement_runtime_context,
)
from app.workflow_resolution_runtime import WorkflowResolutionRuntime
from app.memory.fix_store import FixStore, NoOpFixStore
from app.job_log_runtime import JobLogRuntime
from app.mobile_quality_runtime import MobileQualityRuntime
from app.memory.qdrant_shadow import QdrantShadowTransport
from app.memory.runtime_store import MemoryRuntimeStore
from app.langgraph_planner_shadow import LangGraphPlannerShadowRunner
from app.langgraph_recovery_shadow import LangGraphRecoveryShadowRunner
from app.recovery_runtime import RecoveryRuntime
from app.review_fix_runtime import ReviewFixRuntime
from app.runtime_inputs import normalize_env_var_name, resolve_runtime_inputs
from app.shell_test_runtime import ShellTestRuntime
from app.summary_runtime import SummaryRuntime
from app.self_growing_effectiveness_runtime import SelfGrowingEffectivenessRuntime
from app.tool_runtime import ToolRequest, ToolRuntime
from app.tool_support_runtime import ToolSupportRuntime
from app.template_artifact_runtime import TemplateArtifactRuntime
from app.ux_review_runtime import UxReviewRuntime
from app.workflow_node_runtime import WorkflowNodeRuntime
from app.workflow_pipeline_runtime import WorkflowPipelineRuntime
from app.workspace_repository_runtime import WorkspaceRepositoryRuntime
from app.workflow_binding_runtime import WorkflowBindingRuntime


ShellExecutor = Callable[..., object]
WORKFLOW_NODE_ROUTE_NAMES: Dict[str, tuple[str, ...]] = {
    "gemini_plan": ("planner",),
    "idea_to_product_brief": ("planner",),
    "generate_user_flows": ("planner",),
    "define_mvp_scope": ("planner",),
    "architecture_planning": ("planner",),
    "project_scaffolding": ("planner",),
    "designer_task": ("designer",),
    "publisher_task": ("publisher",),
    "copywriter_task": ("copywriter",),
    "documentation_task": ("documentation",),
    "codex_implement": ("coder",),
    "code_change_summary": ("codex_helper",),
    "commit_implement": ("commit_summary",),
    "gemini_review": ("reviewer",),
    "codex_fix": ("coder",),
    "coder_fix_from_test_report": ("coder",),
    "commit_fix": ("commit_summary",),
    "create_pr": ("pr_summary",),
}


@dataclass
class IssueDetails:
    """Issue data loaded from GitHub CLI."""

    title: str
    body: str
    url: str
    labels: tuple[str, ...] = ()


class Orchestrator:
    """Consume queued jobs and execute the fixed orchestration pipeline."""

    def __init__(
        self,
        settings: AppSettings,
        store: JobStore,
        command_templates: CommandTemplateRunner,
        shell_executor: ShellExecutor = run_shell_command,
        ai_role_router: AIRoleRouter | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.command_templates = command_templates
        self.shell_executor = shell_executor
        self.ai_role_router = ai_role_router or AIRoleRouter(
            roles_path=Path.cwd() / "config" / "roles.json",
            routing_path=Path.cwd() / "config" / "ai_role_routing.json",
        )
        self.feature_flags_path = Path.cwd() / "config" / "feature_flags.json"
        self._agent_profile = "primary"
        self._workflow_route_role_overrides: Dict[str, str] = {}
        self._active_job_id: str | None = None
        self._last_heartbeat_monotonic: float = 0.0
        self._shell_executor_accepts_heartbeat = self._callable_accepts_kwargs(
            self.shell_executor,
            {"heartbeat_callback", "heartbeat_interval_seconds"},
        )
        self._shell_executor_accepts_env = self._callable_accepts_kwargs(
            self.shell_executor,
            {"extra_env"},
        )
        self._active_runtime_input_env: Dict[str, str] = {}
        self._memory_runtime_db_path = settings.resolved_memory_dir / "memory_runtime.db"
        self._qdrant_shadow_transport = QdrantShadowTransport.from_env()
        self._langgraph_planner_shadow = LangGraphPlannerShadowRunner()
        self._langgraph_recovery_shadow = LangGraphRecoveryShadowRunner()
        self._fix_store: FixStore | NoOpFixStore = (
            FixStore(settings.resolved_memory_dir) if settings.memory_enabled else NoOpFixStore()
        )
        self._runtime_input_runtime = OrchestratorRuntimeInputRuntime(
            store=self.store,
            resolve_runtime_inputs=resolve_runtime_inputs,
            normalize_env_var_name=normalize_env_var_name,
            utc_now_iso=utc_now_iso,
        )
        self._mobile_quality_runtime = MobileQualityRuntime(settings=self.settings)
        self._job_control_runtime = JobControlRuntime(
            store=self.store,
            data_dir=self.settings.data_dir,
        )
        self._job_mode_runtime = JobModeRuntime(
            default_enable_escalation=self.settings.enable_escalation,
        )
        self._job_log_runtime = JobLogRuntime(
            store=self.store,
            utc_now_iso=utc_now_iso,
        )
        self._repository_stage_runtime = RepositoryStageRuntime(
            store=self.store,
            utc_now_iso=utc_now_iso,
            execute_shell_command=self._execute_shell_command,
            actor_log_writer=self._actor_log_writer,
            append_actor_log=self._append_actor_log,
        )
        self._self_growing_effectiveness_runtime = SelfGrowingEffectivenessRuntime(
            store=self.store,
        )
        self._template_artifact_runtime = TemplateArtifactRuntime(
            docs_file=self._docs_file,
            job_workspace_path=self._job_workspace_path,
            job_execution_repository=self._job_execution_repository,
            write_operator_inputs_artifact=self._write_operator_inputs_artifact,
            append_actor_log=self._append_actor_log,
        )
        self._issue_spec_runtime = IssueSpecRuntime(
            settings=self.settings,
            set_stage=self._set_stage,
            run_shell=self._run_shell,
            append_actor_log=self._append_actor_log,
            issue_details_factory=IssueDetails,
            build_spec_markdown=build_spec_markdown,
            build_spec_json=build_spec_json,
            issue_reader=issue_reader,
            repo_context_reader=repo_context_reader,
            risk_policy_checker=risk_policy_checker,
            spec_schema_validator=spec_schema_validator,
            spec_rewriter=spec_rewriter,
            write_stage_contracts_doc=self._write_stage_contracts_doc,
            write_pipeline_analysis_doc=self._write_pipeline_analysis_doc,
            update_job=self.store.update_job,
        )
        self._product_definition_runtime = ProductDefinitionRuntime(
            command_templates=self.command_templates,
            set_stage=self._set_stage,
            docs_file=self._docs_file,
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
            append_actor_log=self._append_actor_log,
        )
        self._product_review_runtime = ProductReviewRuntime(
            set_stage=self._set_stage,
            docs_file=self._docs_file,
            read_text_file=self._read_text_file,
            read_json_file=self._read_json_file,
            extract_review_todo_items=self._extract_review_todo_items,
            collect_product_review_evidence=lambda **kwargs: self._collect_product_review_evidence(**kwargs),
            stable_issue_id=self._stable_issue_id,
            build_operating_principle_alignment=lambda **kwargs: self._build_operating_principle_alignment(**kwargs),
            summarize_operating_policy=lambda principle_alignment: self._summarize_operating_policy(principle_alignment),
            build_repo_maturity_snapshot=lambda **kwargs: self._build_repo_maturity_snapshot(**kwargs),
            build_quality_trend_snapshot=lambda **kwargs: self._build_quality_trend_snapshot(**kwargs),
            validate_product_review_payload=lambda payload: self._validate_product_review_payload(payload),
            write_self_growing_effectiveness_artifact=(
                lambda **kwargs: self._write_self_growing_effectiveness_artifact(**kwargs)
            ),
            fix_store=self._fix_store,
        )
        self._improvement_runtime = ImprovementRuntime(
            set_stage=self._set_stage,
            docs_file=self._docs_file,
            read_json_file=self._read_json_file,
            execute_shell_command=self._execute_shell_command,
            actor_log_writer=self._actor_log_writer,
            append_actor_log=self._append_actor_log,
            write_structured_memory_artifacts=lambda **kwargs: self._write_structured_memory_artifacts(**kwargs),
            write_memory_retrieval_artifacts=lambda **kwargs: self._write_memory_retrieval_artifacts(**kwargs),
            write_strategy_shadow_report=lambda **kwargs: self._write_strategy_shadow_report(**kwargs),
            ingest_memory_runtime_artifacts=lambda **kwargs: self._ingest_memory_runtime_artifacts(**kwargs),
            build_improvement_strategy_inputs=lambda **kwargs: self._build_improvement_strategy_inputs(**kwargs),
            select_improvement_strategy=lambda **kwargs: self._select_improvement_strategy(**kwargs),
            select_next_improvement_items=lambda **kwargs: self._select_next_improvement_items(**kwargs),
        )
        self._memory_retrieval_runtime = MemoryRetrievalRuntime(
            feature_enabled=self._feature_enabled,
            docs_file=self._docs_file,
            write_json_artifact=self._write_json_artifact,
            job_execution_repository=self._job_execution_repository,
            get_memory_runtime_store=lambda: self._get_memory_runtime_store(),
            read_json_file=self._read_json_file,
            append_actor_log=self._append_actor_log,
            get_qdrant_shadow_transport=lambda: self._qdrant_shadow_transport,
        )
        self._tool_support_runtime = ToolSupportRuntime(
            get_memory_runtime_store=lambda: self._get_memory_runtime_store(),
            utc_now_iso=utc_now_iso,
            get_qdrant_shadow_transport=lambda: self._qdrant_shadow_transport,
            repo_context_reader=repo_context_reader,
        )
        self._memory_quality_runtime = MemoryQualityRuntime(
            read_json_file=self._read_json_file,
            upsert_json_history_entries=self._upsert_json_history_entries,
            job_execution_repository=self._job_execution_repository,
        )
        self._structured_memory_runtime = StructuredMemoryRuntime(
            feature_enabled=self._feature_enabled,
            docs_file=self._docs_file,
            job_execution_repository=self._job_execution_repository,
            upsert_jsonl_entries=self._upsert_jsonl_entries,
            upsert_json_history_entries=self._upsert_json_history_entries,
            write_json_artifact=self._write_json_artifact,
            write_memory_quality_artifacts=lambda **kwargs: self._write_memory_quality_artifacts(**kwargs),
            read_json_file=self._read_json_file,
            read_text_file=self._read_text_file,
        )
        self._integration_recommendation_runtime = IntegrationRecommendationRuntime(
            store=self.store,
            append_actor_log=self._append_actor_log,
            docs_file=self._docs_file,
        )
        self._integration_guide_runtime = IntegrationGuideRuntime(
            store=self.store,
            docs_file=self._docs_file,
        )
        self._integration_usage_runtime = IntegrationUsageRuntime(
            store=self.store,
            docs_file=self._docs_file,
        )
        self._app_type_runtime = AppTypeRuntime(
            docs_file=self._docs_file,
            set_stage=self._set_stage,
            append_actor_log=self._append_actor_log,
        )
        self._shell_test_runtime = ShellTestRuntime(
            settings=self.settings,
            shell_executor=self.shell_executor,
            shell_executor_accepts_heartbeat=self._shell_executor_accepts_heartbeat,
            shell_executor_accepts_env=self._shell_executor_accepts_env,
            touch_job_heartbeat=self._touch_job_heartbeat,
            actor_log_writer=self._actor_log_writer,
            infer_actor_from_command=self._infer_actor_from_command,
            set_stage=self._set_stage,
            append_actor_log=self._append_actor_log,
            is_long_track=self._is_long_track,
            write_mobile_quality_artifact=self._mobile_quality_runtime.write_mobile_app_checklist,
        )
        self._orchestrator_context_runtime = OrchestratorContextRuntime(
            feature_flags_path=lambda: self.feature_flags_path,
            memory_runtime_db_path=self._memory_runtime_db_path,
            runtime_input_runtime=self._runtime_input_runtime,
            command_templates=self.command_templates,
            shell_test_runtime=self._shell_test_runtime,
        )
        self._recovery_runtime = RecoveryRuntime(
            command_templates=self.command_templates,
            stage_run_tests=self._stage_run_tests,
            append_actor_log=self._append_actor_log,
            stage_fix_with_codex=self._stage_fix_with_codex,
            commit_markdown_changes_after_stage=self._commit_markdown_changes_after_stage,
            is_recovery_mode_enabled=self._is_recovery_mode_enabled,
            find_configured_template_for_route=self._find_configured_template_for_route,
            template_for_route=self._template_for_route,
            build_template_variables=self._build_template_variables,
            docs_file=self._docs_file,
            actor_log_writer=self._actor_log_writer,
            is_escalation_enabled=self._is_escalation_enabled,
            run_optional_escalation=self._run_optional_escalation,
            feature_enabled=self._feature_enabled,
            recovery_shadow_runner=self._langgraph_recovery_shadow,
        )
        self._summary_runtime = SummaryRuntime(
            command_templates=self.command_templates,
            run_shell=self._run_shell,
            append_log=self._append_log,
            append_actor_log=self._append_actor_log,
            docs_file=self._docs_file,
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
            find_configured_template_for_route=self._find_configured_template_for_route,
            set_stage=self._set_stage,
            parse_porcelain_path=self._parse_porcelain_path,
            is_long_track=self._is_long_track,
        )
        self._docs_snapshot_runtime = DocsSnapshotRuntime(
            settings=self.settings,
            run_shell=self._run_shell,
            docs_file=self._docs_file,
            append_actor_log=self._append_actor_log,
            prepare_commit_summary_with_ai=self._prepare_commit_summary_with_ai,
        )
        self._design_governance_runtime = DesignGovernanceRuntime(
            docs_file=self._docs_file,
            sha256_file=self._sha256_file,
            append_actor_log=self._append_actor_log,
        )
        self._content_stage_runtime = ContentStageRuntime(
            command_templates=self.command_templates,
            set_stage=self._set_stage,
            ensure_product_definition_ready=self._ensure_product_definition_ready,
            docs_file=self._docs_file,
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
            template_candidates_for_route=self._template_candidates_for_route,
            append_actor_log=self._append_actor_log,
            ensure_design_artifacts=self._ensure_design_artifacts,
            ensure_publisher_artifacts=self._ensure_publisher_artifacts,
            ensure_copywriter_artifacts=self._ensure_copywriter_artifacts,
            ensure_documentation_artifacts=self._ensure_documentation_artifacts,
        )
        self._review_fix_runtime = ReviewFixRuntime(
            command_templates=self.command_templates,
            set_stage=self._set_stage,
            write_memory_retrieval_artifacts=self._write_memory_retrieval_artifacts,
            write_integration_guide_summary_artifact=self._write_integration_guide_summary_artifact,
            write_integration_code_patterns_artifact=self._write_integration_code_patterns_artifact,
            write_integration_verification_checklist_artifact=self._write_integration_verification_checklist_artifact,
            append_integration_usage_trail_event=self._append_integration_usage_trail_event,
            docs_file=self._docs_file,
            build_route_runtime_context=lambda route_name: self._build_route_runtime_context(route_name),
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
            template_for_route_in_repository=self._template_for_route_in_repository,
            read_improvement_runtime_context=lambda paths: self._read_improvement_runtime_context(paths),
            stage_plan_with_gemini=lambda *args, **kwargs: self._stage_plan_with_gemini(*args, **kwargs),
            append_actor_log=self._append_actor_log,
        )
        self._planner_runtime = PlannerRuntime(
            command_templates=self.command_templates,
            set_stage=self._set_stage,
            append_actor_log=self._append_actor_log,
            docs_file=self._docs_file,
            write_memory_retrieval_artifacts=self._write_memory_retrieval_artifacts,
            build_route_runtime_context=lambda route_name: self._build_route_runtime_context(route_name),
            is_long_track_job=lambda job: self._is_long_track(self._require_job(job.job_id)),
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
            template_for_route_in_repository=self._template_for_route_in_repository,
            route_allows_tool=self._route_allows_tool,
            execute_planner_tool_request=self._execute_planner_tool_request,
            feature_enabled=self._feature_enabled,
            planner_shadow_runner=self._langgraph_planner_shadow,
            write_integration_recommendation_artifact=self._write_integration_recommendation_artifact,
            write_integration_guide_summary_artifact=self._write_integration_guide_summary_artifact,
            write_integration_code_patterns_artifact=self._write_integration_code_patterns_artifact,
            write_integration_verification_checklist_artifact=self._write_integration_verification_checklist_artifact,
            append_integration_usage_trail_event=self._append_integration_usage_trail_event,
        )
        self._implement_runtime = ImplementRuntime(
            command_templates=self.command_templates,
            set_stage=self._set_stage,
            ensure_product_definition_ready=self._ensure_product_definition_ready,
            write_memory_retrieval_artifacts=self._write_memory_retrieval_artifacts,
            write_integration_guide_summary_artifact=self._write_integration_guide_summary_artifact,
            write_integration_code_patterns_artifact=self._write_integration_code_patterns_artifact,
            write_integration_verification_checklist_artifact=self._write_integration_verification_checklist_artifact,
            append_integration_usage_trail_event=self._append_integration_usage_trail_event,
            docs_file=self._docs_file,
            build_route_runtime_context=lambda route_name: self._build_route_runtime_context(route_name),
            build_template_variables=self._build_template_variables,
            actor_log_writer=self._actor_log_writer,
            template_for_route=self._template_for_route,
        )
        self._ux_review_runtime = UxReviewRuntime(
            stage_run_tests=self._stage_run_tests,
            deploy_preview_and_smoke_test=self._deploy_preview_and_smoke_test,
            run_shell=self._run_shell,
            append_actor_log=self._append_actor_log,
            docs_file=self._docs_file,
        )
        self._workflow_node_runtime = WorkflowNodeRuntime(owner=self)
        self._workflow_pipeline_runtime = WorkflowPipelineRuntime(owner=self)
        self._workflow_resolution_runtime = WorkflowResolutionRuntime(
            store=self.store,
            append_actor_log=self._append_actor_log,
            read_improvement_runtime_context=self._read_improvement_runtime_context,
        )
        self._workflow_binding_runtime = WorkflowBindingRuntime(
            ai_role_router=self.ai_role_router,
            issue_type=IssueDetails,
            route_names_map=WORKFLOW_NODE_ROUTE_NAMES,
        )
        self._fixed_pipeline_runtime = FixedPipelineRuntime(
            stage_read_issue=self._stage_read_issue,
            commit_markdown_changes_after_stage=self._commit_markdown_changes_after_stage,
            stage_write_spec=self._stage_write_spec,
            stage_idea_to_product_brief=self._stage_idea_to_product_brief,
            stage_generate_user_flows=self._stage_generate_user_flows,
            stage_define_mvp_scope=self._stage_define_mvp_scope,
            stage_architecture_planning=self._stage_architecture_planning,
            stage_project_scaffolding=self._stage_project_scaffolding,
            stage_plan_with_gemini=self._stage_plan_with_gemini,
            snapshot_plan_variant=self._snapshot_plan_variant,
            stage_design_with_codex=self._stage_design_with_codex,
            stage_publish_with_codex=self._stage_publish_with_codex,
            stage_implement_with_codex=self._stage_implement_with_codex,
            stage_summarize_code_changes=self._stage_summarize_code_changes,
            run_test_hard_gate=self._run_test_hard_gate,
            stage_commit=self._stage_commit,
            stage_review_with_gemini=self._stage_review_with_gemini,
            stage_product_review=self._stage_product_review,
            stage_improvement_stage=self._stage_improvement_stage,
            stage_fix_with_codex=self._stage_fix_with_codex,
            stage_documentation_with_claude=self._stage_documentation_with_claude,
            stage_push_branch=self._stage_push_branch,
            stage_create_pr=self._stage_create_pr,
            set_stage=self._set_stage,
        )
        self._workspace_repository_runtime = WorkspaceRepositoryRuntime(
            settings=self.settings,
            set_stage=self._set_stage,
            append_log=self._append_log,
            run_shell=self._run_shell,
            ref_exists=self._ref_exists,
        )
        self._preview_runtime = PreviewRuntime(
            settings=self.settings,
            run_shell=self._run_shell,
            execute_shell_command=self._execute_shell_command,
            actor_log_writer=self._actor_log_writer,
            append_actor_log=self._append_actor_log,
            docs_file=self._docs_file,
        )
        self._provider_runtime = ProviderRuntime(
            settings=self.settings,
            store=self.store,
            run_shell=self._run_shell,
            set_stage=self._set_stage,
            require_job=self._require_job,
            job_execution_repository=self._job_execution_repository,
            deploy_preview_and_smoke_test=self._deploy_preview_and_smoke_test,
            docs_file=self._docs_file,
            stage_prepare_pr_summary=self._stage_prepare_pr_summary_with_claude,
            issue_reference_line=self._issue_reference_line,
            append_preview_section_to_pr_body=self._append_preview_section_to_pr_body,
            append_actor_log=self._append_actor_log,
        )
        self._job_failure_runtime = JobFailureRuntime(
            settings=self.settings,
            store=self.store,
            command_templates=self.command_templates,
            require_job=self._require_job,
            run_single_attempt=lambda job_id, log_path: self._run_single_attempt(job_id, log_path),
            touch_job_heartbeat=self._touch_job_heartbeat,
            append_actor_log=self._append_actor_log,
            is_escalation_enabled=self._is_escalation_enabled,
            find_configured_template_for_route=self._find_configured_template_for_route,
            docs_file=self._docs_file,
            job_workspace_path=self._job_workspace_path,
            build_template_variables=self._build_template_variables,
            template_for_route=self._template_for_route,
            actor_log_writer=self._actor_log_writer,
            set_stage=self._set_stage,
            issue_reference_line=self._issue_reference_line,
            run_shell=self._run_shell,
            push_branch_with_recovery=self._push_branch_with_recovery,
            job_execution_repository=self._job_execution_repository,
            get_pr_url=self._get_pr_url,
            is_stop_requested=self._is_stop_requested,
            clear_stop_requested=self._clear_stop_requested,
            set_agent_profile=self._set_agent_profile,
        )
        self._job_execution_runtime = JobExecutionRuntime(owner=self)
        self._tool_runtime = ToolRuntime(
            command_templates=self.command_templates,
            docs_file=self._docs_file,
            build_template_variables=self._build_template_variables,
            template_for_route=self._template_for_route,
            actor_log_writer=self._actor_log_writer,
            append_actor_log=self._append_actor_log,
            build_local_evidence_fallback=self._build_local_evidence_fallback,
            search_memory_entries=self._search_memory_entries_for_tool,
            search_vector_memory_entries=self._search_vector_memory_entries_for_tool,
            feature_enabled=self._feature_enabled,
        )
        self._ai_route_runtime = AIRouteRuntime(
            ai_role_router=self.ai_role_router,
            command_templates=self.command_templates,
            get_agent_profile=lambda: self._agent_profile,
            get_workflow_route_role_overrides=lambda: self._workflow_route_role_overrides,
            append_actor_log=self._append_actor_log,
        )
        self._install_command_template_heartbeat()

    def _install_command_template_heartbeat(self) -> None:
        self._orchestrator_context_runtime.install_command_template_heartbeat(
            active_runtime_input_env=self._active_runtime_input_env,
            touch_job_heartbeat=self._touch_job_heartbeat,
        )

    @staticmethod
    def _callable_accepts_kwargs(target: Callable[..., object], names: Set[str]) -> bool:
        """Return True when one callable exposes every requested keyword parameter."""

        try:
            parameters = inspect.signature(target).parameters
        except (TypeError, ValueError):
            return False
        return names.issubset(parameters.keys())

    def _feature_enabled(self, flag_name: str) -> bool:
        return self._orchestrator_context_runtime.feature_enabled(flag_name)

    def _get_memory_runtime_store(self) -> MemoryRuntimeStore:
        return self._orchestrator_context_runtime.get_memory_runtime_store()

    def _resolve_runtime_inputs_for_job(self, job: JobRecord) -> Dict[str, object]:
        return self._orchestrator_context_runtime.resolve_runtime_inputs_for_job(job)

    def _set_active_runtime_input_environment(self, job: JobRecord) -> None:
        self._active_runtime_input_env = self._orchestrator_context_runtime.set_active_runtime_input_environment(job)
        self._install_command_template_heartbeat()

    def _write_operator_inputs_artifact(
        self,
        job: JobRecord,
        artifact_path: Path,
    ) -> Dict[str, object]:
        return self._orchestrator_context_runtime.write_operator_inputs_artifact(job, artifact_path)

    def _search_memory_entries_for_tool(
        self,
        *,
        query: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self._tool_support_runtime.search_memory_entries_for_tool(
            query=query,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )

    def _search_vector_memory_entries_for_tool(
        self,
        *,
        query: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        return self._tool_support_runtime.search_vector_memory_entries_for_tool(
            query=query,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )

    def process_next_job(self) -> bool:
        return self._job_execution_runtime.process_next_job()

    def process_job(self, job_id: str) -> None:
        self._job_execution_runtime.process_job(job_id)

    def _process_long_job(self, job_id: str, log_path: Path) -> None:
        self._job_execution_runtime.process_long_job(job_id, log_path)

    def _process_ultra_job(
        self,
        job_id: str,
        log_path: Path,
        max_runtime_hours: int = 5,
        mode_tag: str = "ULTRA",
    ) -> None:
        self._job_execution_runtime.process_ultra_job(
            job_id,
            log_path,
            max_runtime_hours=max_runtime_hours,
            mode_tag=mode_tag,
        )

    def _run_single_attempt(self, job_id: str, log_path: Path) -> None:
        self._job_execution_runtime.run_single_attempt(job_id, log_path)

    def _run_fixed_pipeline(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Run legacy hard-coded pipeline (fallback path)."""

        self._fixed_pipeline_runtime.run_fixed_pipeline(job, repository_path, log_path)

    def _run_workflow_pipeline(
        self,
        job: JobRecord,
        repository_path: Path,
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
        log_path: Path,
        *,
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run workflow via edge-driven execution with success/failure/always transitions."""
        self._workflow_pipeline_runtime.run_workflow_pipeline(
            job=job,
            repository_path=repository_path,
            workflow=workflow,
            ordered_nodes=ordered_nodes,
            log_path=log_path,
            resume_state=resume_state,
        )

    def _resolve_workflow_resume_state(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Resolve whether the current attempt should resume from a failed node."""
        return self._workflow_resolution_runtime.resolve_workflow_resume_state(
            job=job,
            repository_path=repository_path,
            workflow=workflow,
            ordered_nodes=ordered_nodes,
        )

    def _resolve_workflow_node_executor(
        self,
        node_type: str,
    ) -> Optional[Callable[..., None]]:
        handler_name = WORKFLOW_NODE_HANDLER_NAMES.get(node_type)
        if not handler_name:
            return None
        handler = getattr(self, handler_name, None)
        if callable(handler):
            return handler
        return self._workflow_node_runtime.resolve(node_type)

    def _workflow_node_agent_profile(self, node: Dict[str, Any]) -> str:
        return self._workflow_binding_runtime.workflow_node_agent_profile(
            node,
            self._agent_profile,
        )

    @staticmethod
    def _normalize_workflow_binding_id(value: str, *, max_length: int = 64) -> str:
        return WorkflowBindingRuntime.normalize_workflow_binding_id(
            value,
            max_length=max_length,
        )

    def _workflow_node_route_names(self, node: Dict[str, Any]) -> tuple[str, ...]:
        return self._workflow_binding_runtime.workflow_node_route_names(node)

    def _workflow_node_route_role_overrides(self, node: Dict[str, Any]) -> Dict[str, str]:
        return self._workflow_binding_runtime.workflow_node_route_role_overrides(node)

    def _workflow_context_issue(self, context: Dict[str, Any]) -> IssueDetails:
        return self._workflow_binding_runtime.workflow_context_issue(context)

    def _workflow_context_paths(self, context: Dict[str, Any]) -> Dict[str, Path]:
        return self._workflow_binding_runtime.workflow_context_paths(context)

    def _load_active_workflow(self, job: JobRecord, log_path: Path) -> Optional[Dict[str, Any]]:
        """Resolve and load one workflow config; fallback to fixed pipeline on any error."""
        return self._workflow_resolution_runtime.load_active_workflow(
            job=job,
            log_path=log_path,
        )

    @staticmethod
    def _linearize_workflow_nodes(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return linear execution order from entry node over success/always edges."""
        return WorkflowResolutionRuntime.linearize_workflow_nodes(workflow)

    @staticmethod
    def _job_execution_repository(job: JobRecord) -> str:
        """Return repository used for clone/build/push for one job."""
        return WorkspaceRepositoryRuntime.job_execution_repository(job)

    def _job_workspace_path(self, job: JobRecord) -> Path:
        """Resolve workspace path using execution repository."""
        return self._workspace_repository_runtime.job_workspace_path(job)

    def _issue_reference_line(self, job: JobRecord) -> str:
        """Return PR-safe issue reference text.

        If execution repository differs from the issue hub repository, using
        `Closes #<n>` would target the wrong repository issue. In that case we
        keep a full tracking URL instead.
        """
        return self._workspace_repository_runtime.issue_reference_line(job)

    def _stage_prepare_repo(self, job: JobRecord, log_path: Path) -> Path:
        return self._workspace_repository_runtime.stage_prepare_repo(job, log_path)

    def _stage_read_issue(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> IssueDetails:
        return self._issue_spec_runtime.stage_read_issue(job, repository_path, log_path)

    def _stage_write_spec(
        self,
        job: JobRecord,
        repository_path: Path,
        issue: IssueDetails,
        log_path: Path,
    ) -> Dict[str, Path]:
        return self._issue_spec_runtime.stage_write_spec(
            job,
            repository_path,
            issue,
            log_path,
        )

    def _run_markdown_generation_with_refinement(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage_name: str,
        actor: str,
        output_path: Path,
        prompt_path: Path,
        prompt_builder: Callable[[str], str],
        required_sections: Dict[str, List[str]],
        required_evidence: List[str],
        fallback_writer: Callable[[], None],
    ) -> None:
        self._product_definition_runtime.run_markdown_generation_with_refinement(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage_name=stage_name,
            actor=actor,
            output_path=output_path,
            prompt_path=prompt_path,
            prompt_builder=prompt_builder,
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=fallback_writer,
        )

    def _stage_idea_to_product_brief(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.stage_idea_to_product_brief(job, repository_path, paths, log_path)

    def _stage_generate_user_flows(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.stage_generate_user_flows(job, repository_path, paths, log_path)

    def _stage_define_mvp_scope(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.stage_define_mvp_scope(job, repository_path, paths, log_path)

    def _stage_architecture_planning(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.stage_architecture_planning(job, repository_path, paths, log_path)

    def _stage_project_scaffolding(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.stage_project_scaffolding(job, repository_path, paths, log_path)

    @staticmethod
    def _build_bootstrap_report(
        *,
        repository_path: Path,
        spec_json_path: Optional[Path],
        repo_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return ProductDefinitionRuntime.build_bootstrap_report(
            repository_path=repository_path,
            spec_json_path=spec_json_path,
            repo_context=repo_context,
        )

    def _ensure_markdown_stage_contract(
        self,
        *,
        stage_name: str,
        path: Path,
        required_sections: Dict[str, List[str]],
        required_evidence: Optional[List[str]],
        fallback_writer: Optional[Callable[[], None]],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.ensure_markdown_stage_contract(
            stage_name=stage_name,
            path=path,
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=fallback_writer,
            log_path=log_path,
        )

    @staticmethod
    def _missing_markdown_sections(
        path: Path,
        required_sections: Dict[str, List[str]],
        *,
        required_evidence: Optional[List[str]] = None,
    ) -> List[str]:
        return ProductDefinitionRuntime.missing_markdown_sections(
            path,
            required_sections,
            required_evidence=required_evidence,
        )

    def _ensure_product_definition_ready(
        self,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_definition_runtime.ensure_product_definition_ready(paths, log_path)

    # ------------------------------------------------------------------
    # Fallback writers — used when AI call fails for product-def stages
    # ------------------------------------------------------------------

    def _write_product_brief_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        product_brief_path: Path,
    ) -> None:
        self._product_definition_runtime.write_product_brief_fallback(job, paths, product_brief_path)

    def _write_user_flows_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        user_flows_path: Path,
    ) -> None:
        self._product_definition_runtime.write_user_flows_fallback(job, paths, user_flows_path)

    def _write_mvp_scope_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        mvp_scope_path: Path,
    ) -> None:
        self._product_definition_runtime.write_mvp_scope_fallback(job, paths, mvp_scope_path)

    @staticmethod
    def _write_architecture_plan_fallback(
        job: JobRecord,
        paths: Dict[str, Path],
        architecture_plan_path: Path,
    ) -> None:
        ProductDefinitionRuntime.write_architecture_plan_fallback(job, paths, architecture_plan_path)

    @staticmethod
    def _write_project_scaffolding_fallback(
        bootstrap_report: Dict[str, Any],
        scaffold_plan_path: Path,
    ) -> None:
        ProductDefinitionRuntime.write_project_scaffolding_fallback(bootstrap_report, scaffold_plan_path)

    def _stage_product_review(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._product_review_runtime.stage_product_review(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_improvement_stage(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._improvement_runtime.stage_improvement_stage(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _write_structured_memory_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        maturity_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        next_tasks_payload: Dict[str, Any],
    ) -> None:
        self._structured_memory_runtime.write_structured_memory_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
            next_tasks_payload=next_tasks_payload,
        )

    @staticmethod
    def _upsert_jsonl_entries(path: Path, entries: List[Dict[str, Any]], *, key_field: str) -> None:
        ArtifactIoRuntime.upsert_jsonl_entries(path, entries, key_field=key_field)

    @staticmethod
    def _upsert_json_history_entries(
        path: Path,
        entries: List[Dict[str, Any]],
        *,
        key_field: str,
        root_key: str,
        max_entries: int,
    ) -> None:
        ArtifactIoRuntime.upsert_json_history_entries(
            path,
            entries,
            key_field=key_field,
            root_key=root_key,
            max_entries=max_entries,
        )

    @staticmethod
    def _write_json_artifact(path: Optional[Path], payload: Dict[str, Any]) -> None:
        ArtifactIoRuntime.write_json_artifact(path, payload)

    def _update_failure_patterns_artifact(
        self,
        *,
        failure_patterns_path: Path,
        review_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        trend_payload: Dict[str, Any],
        next_tasks_payload: Dict[str, Any],
        generated_at: str,
    ) -> None:
        self._structured_memory_runtime.update_failure_patterns_artifact(
            failure_patterns_path=failure_patterns_path,
            review_payload=review_payload,
            loop_state=loop_state,
            trend_payload=trend_payload,
            next_tasks_payload=next_tasks_payload,
            generated_at=generated_at,
        )

    def _write_conventions_artifact(
        self,
        *,
        repository_path: Path,
        conventions_path: Path,
        job: JobRecord,
        generated_at: str,
    ) -> None:
        self._structured_memory_runtime.write_conventions_artifact(
            repository_path=repository_path,
            conventions_path=conventions_path,
            job=job,
            generated_at=generated_at,
        )

    def _write_memory_quality_artifacts(
        self,
        *,
        job: JobRecord,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
        generated_at: str,
        current_memory_ids: List[str],
        memory_feedback_path: Path,
        memory_rankings_path: Path,
    ) -> None:
        self._memory_quality_runtime.write_memory_quality_artifacts(
            job=job,
            paths=paths,
            review_payload=review_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
            generated_at=generated_at,
            current_memory_ids=current_memory_ids,
            memory_rankings_path=memory_rankings_path,
            memory_feedback_path=memory_feedback_path,
        )

    @staticmethod
    def _build_memory_feedback_outcome(
        *,
        review_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        return MemoryQualityRuntime.build_memory_feedback_outcome(
            review_payload=review_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
        )

    def _update_memory_rankings_artifact(
        self,
        *,
        memory_rankings_path: Path,
        feedback_entries: List[Dict[str, Any]],
        generated_at: str,
    ) -> None:
        self._memory_quality_runtime.update_memory_rankings_artifact(
            memory_rankings_path=memory_rankings_path,
            feedback_entries=feedback_entries,
            generated_at=generated_at,
        )

    @staticmethod
    def _memory_ranking_state(*, score: float, positive_count: int, negative_count: int) -> str:
        return MemoryQualityRuntime.memory_ranking_state(
            score=score,
            positive_count=positive_count,
            negative_count=negative_count,
        )

    @staticmethod
    def _memory_kind_from_id(memory_id: str) -> str:
        return MemoryQualityRuntime.memory_kind_from_id(memory_id)

    @staticmethod
    def _package_dependency_map(package_json: Dict[str, Any]) -> Dict[str, str]:
        return StructuredMemoryRuntime.package_dependency_map(package_json)

    @staticmethod
    def _detect_component_extension_preference(repository_path: Path) -> Dict[str, Any]:
        return StructuredMemoryRuntime.detect_component_extension_preference(repository_path)

    @staticmethod
    def _detect_test_file_conventions(repository_path: Path) -> Dict[str, Any]:
        return StructuredMemoryRuntime.detect_test_file_conventions(repository_path)

    def _write_integration_recommendation_artifact(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> Dict[str, Any]:
        """Write planner-safe integration recommendation artifact."""

        return self._integration_recommendation_runtime.write_integration_recommendation_artifact(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _write_integration_guide_summary_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Write prompt-safe approved integration guide summary artifact."""

        return self._integration_guide_runtime.write_prompt_safe_guide_summary_artifact(
            repository_path=repository_path,
            paths=paths,
        )

    def _write_integration_code_patterns_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Write prompt-safe approved integration code pattern hints."""

        return self._integration_guide_runtime.write_code_pattern_hint_artifact(
            repository_path=repository_path,
            paths=paths,
        )

    def _write_integration_verification_checklist_artifact(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> Dict[str, Any]:
        """Write prompt-safe approved integration verification checklist."""

        return self._integration_guide_runtime.write_verification_checklist_artifact(
            repository_path=repository_path,
            paths=paths,
        )

    def _append_integration_usage_trail_event(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        stage: str,
        route: str,
        prompt_path: Path,
    ) -> Dict[str, Any]:
        """Append one stage-level integration usage trail event."""

        return self._integration_usage_runtime.append_usage_trail_event(
            job=job,
            repository_path=repository_path,
            paths=paths,
            stage=stage,
            route=route,
            prompt_path=prompt_path,
        )

    def _write_memory_retrieval_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> None:
        self._memory_retrieval_runtime.write_memory_retrieval_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
        )

    def _load_vector_shadow_runtime_entries(self, *, job: JobRecord) -> List[Dict[str, Any]]:
        return self._memory_retrieval_runtime.load_vector_shadow_runtime_entries(job=job)

    def _write_vector_shadow_index_artifact(
        self,
        *,
        job: JobRecord,
        output_path: Path,
        runtime_entries: List[Dict[str, Any]],
        enabled: bool,
        status: str,
    ) -> None:
        self._memory_retrieval_runtime.write_vector_shadow_index_artifact(
            job=job,
            output_path=output_path,
            runtime_entries=runtime_entries,
            enabled=enabled,
            status=status,
        )

    def _load_memory_retrieval_corpus_from_db(self, *, job: JobRecord) -> Optional[Dict[str, Any]]:
        return self._memory_retrieval_runtime.load_memory_retrieval_corpus_from_db(job=job)

    def _load_memory_retrieval_corpus_from_files(self, *, paths: Dict[str, Path]) -> Dict[str, Any]:
        return self._memory_retrieval_runtime.load_memory_retrieval_corpus_from_files(paths=paths)

    @staticmethod
    def _memory_runtime_entry_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.memory_runtime_entry_payload(entry)

    @staticmethod
    def _memory_route_trace_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.memory_route_trace_payload(items)

    def _write_strategy_shadow_report(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        strategy_inputs: Dict[str, Any],
        selected_strategy: str,
        selected_focus: str,
    ) -> None:
        self._memory_retrieval_runtime.write_strategy_shadow_report(
            job=job,
            repository_path=repository_path,
            paths=paths,
            strategy_inputs=strategy_inputs,
            selected_strategy=selected_strategy,
            selected_focus=selected_focus,
        )

    def _ingest_memory_runtime_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._memory_retrieval_runtime.ingest_memory_runtime_artifacts(
            job=job,
            paths=paths,
            log_path=log_path,
        )

    def _build_strategy_shadow_report_payload(
        self,
        *,
        job: JobRecord,
        context_payload: Dict[str, Any],
        rankings_map: Dict[str, Dict[str, Any]],
        strategy_inputs: Dict[str, Any],
        selected_strategy: str,
        selected_focus: str,
    ) -> Dict[str, Any]:
        return self._memory_retrieval_runtime.build_strategy_shadow_report_payload(
            job=job,
            context_payload=context_payload,
            rankings_map=rankings_map,
            strategy_inputs=strategy_inputs,
            selected_strategy=selected_strategy,
            selected_focus=selected_focus,
        )

    @staticmethod
    def _strategy_shadow_route_weight(route_name: str) -> float:
        return MemoryRetrievalRuntime.strategy_shadow_route_weight(route_name)

    @staticmethod
    def _strategy_shadow_ranking_weight(ranking: Dict[str, Any]) -> float:
        return MemoryRetrievalRuntime.strategy_shadow_ranking_weight(ranking)

    @staticmethod
    def _strategy_shadow_recommendations(item: Dict[str, Any]) -> List[Dict[str, str]]:
        return MemoryRetrievalRuntime.strategy_shadow_recommendations(item)

    @staticmethod
    def _strategy_focus_for_name(strategy: str) -> str:
        return MemoryRetrievalRuntime.strategy_focus_for_name(strategy)

    def _build_route_memory_context(
        self,
        *,
        route: str,
        memory_log_entries: List[Dict[str, Any]],
        decision_entries: List[Dict[str, Any]],
        failure_pattern_entries: List[Dict[str, Any]],
        convention_entries: List[Dict[str, Any]],
        rankings_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        return self._memory_retrieval_runtime.build_route_memory_context(
            route=route,
            memory_log_entries=memory_log_entries,
            decision_entries=decision_entries,
            failure_pattern_entries=failure_pattern_entries,
            convention_entries=convention_entries,
            rankings_map=rankings_map,
        )

    @staticmethod
    def _memory_log_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.memory_log_context_entry(entry)

    @staticmethod
    def _decision_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.decision_context_entry(entry)

    @staticmethod
    def _failure_pattern_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.failure_pattern_context_entry(entry)

    @staticmethod
    def _convention_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return MemoryRetrievalRuntime.convention_context_entry(entry)

    @staticmethod
    def _read_jsonl_entries(path: Optional[Path]) -> List[Dict[str, Any]]:
        return MemoryRetrievalRuntime.read_jsonl_entries(path)

    def _read_json_history_entries(self, path: Optional[Path], *, root_key: str = "entries") -> List[Dict[str, Any]]:
        return self._memory_retrieval_runtime.read_json_history_entries(path, root_key=root_key)

    @staticmethod
    def _build_improvement_strategy_inputs(
        *,
        review_payload: Dict[str, Any],
        maturity_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        categories_below: List[str],
    ) -> Dict[str, Any]:
        return ImprovementRuntime.build_improvement_strategy_inputs(
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            categories_below=categories_below,
        )

    @staticmethod
    def _select_improvement_strategy(
        *,
        overall_score: float,
        strategy_inputs: Dict[str, Any],
        repeated_issue_limit_hit: bool,
        score_stagnation_detected: bool,
        quality_regression_detected: bool,
        design_reset_required: bool,
        scope_reset_required: bool,
        quality_focus_required: bool,
    ) -> Dict[str, Any]:
        return ImprovementRuntime.select_improvement_strategy(
            overall_score=overall_score,
            strategy_inputs=strategy_inputs,
            repeated_issue_limit_hit=repeated_issue_limit_hit,
            score_stagnation_detected=score_stagnation_detected,
            quality_regression_detected=quality_regression_detected,
            design_reset_required=design_reset_required,
            scope_reset_required=scope_reset_required,
            quality_focus_required=quality_focus_required,
        )

    @staticmethod
    def _select_next_improvement_items(
        *,
        strategy: str,
        backlog_items: List[Dict[str, Any]],
        categories_below: List[str],
        scores: Dict[str, Any],
        artifact_health: Dict[str, Any],
        quality_gate: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        return ImprovementRuntime.select_next_improvement_items(
            strategy=strategy,
            backlog_items=backlog_items,
            categories_below=categories_below,
            scores=scores,
            artifact_health=artifact_health,
            quality_gate=quality_gate,
        )

    def _stage_plan_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self._planner_runtime.stage_plan_with_gemini(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            planning_mode=planning_mode,
        )

    def _run_planner_legacy_one_shot(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self._planner_runtime.run_planner_legacy_one_shot(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            planning_mode=planning_mode,
        )

    def _snapshot_plan_variant(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        planning_mode: str,
        log_path: Path,
    ) -> None:
        self._docs_snapshot_runtime.snapshot_plan_variant(
            repository_path=repository_path,
            paths=paths,
            planning_mode=planning_mode,
            log_path=log_path,
        )

    def _run_planner_graph_mvp(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self._planner_runtime.run_planner_graph_mvp(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            planning_mode=planning_mode,
        )

    def _write_langgraph_planner_shadow_trace(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
        rounds: List[Dict[str, Any]],
        max_rounds: int,
        planning_mode: str,
    ) -> None:
        self._planner_runtime.write_langgraph_planner_shadow_trace(
            repository_path=repository_path,
            paths=paths,
            rounds=rounds,
            max_rounds=max_rounds,
            planning_mode=planning_mode,
        )

    @staticmethod
    def _planner_graph_max_rounds() -> int:
        return PlannerRuntime.planner_graph_max_rounds()

    @staticmethod
    def _planner_graph_enabled() -> bool:
        return PlannerRuntime.planner_graph_enabled()

    @staticmethod
    def _parse_planner_tool_request(plan_text: str) -> Optional[ToolRequest]:
        return PlannerRuntime.parse_planner_tool_request(plan_text)

    def _execute_planner_tool_request(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        tool_request: ToolRequest,
    ) -> Dict[str, Any]:
        """Execute planner-requested tool via the shared runtime."""

        return self._tool_runtime.execute(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            request=tool_request,
        ).to_dict()

    def _build_local_evidence_fallback(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        query: str,
        error_text: str,
    ) -> Dict[str, str]:
        return self._tool_support_runtime.build_local_evidence_fallback(
            repository_path,
            paths,
            query,
            error_text,
        )

    @staticmethod
    def _build_planner_tool_context_addendum(
        *,
        tool_request: ToolRequest,
        outcome: Dict[str, Any],
    ) -> str:
        return PlannerRuntime.build_planner_tool_context_addendum(
            tool_request=tool_request,
            outcome=outcome,
        )

    def _stage_implement_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._implement_runtime.stage_implement_with_codex(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_design_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._content_stage_runtime.stage_design_with_codex(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_publish_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._content_stage_runtime.stage_publish_with_codex(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_copywriter_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._content_stage_runtime.stage_copywriter_with_codex(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_documentation_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._content_stage_runtime.stage_documentation_with_claude(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _apply_documentation_bundle(
        self,
        repository_path: Path,
        bundle_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> bool:
        return self._content_stage_runtime.apply_documentation_bundle(
            repository_path=repository_path,
            bundle_path=bundle_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_run_tests(
        self,
        job: JobRecord,
        repository_path: Path,
        stage: JobStage,
        log_path: Path,
    ) -> bool:
        return self._shell_test_runtime.stage_run_tests(
            job=job,
            repository_path=repository_path,
            stage=stage,
            log_path=log_path,
        )

    def _run_test_hard_gate(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
    ) -> None:
        self._recovery_runtime.run_test_hard_gate(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=stage,
            gate_label=gate_label,
        )

    def _run_test_gate_by_policy(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
        app_type: str,
    ) -> None:
        self._recovery_runtime.run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=stage,
            gate_label=gate_label,
            app_type=app_type,
        )

    def _try_recovery_flow(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        stage: JobStage,
        gate_label: str,
        reason: str,
    ) -> bool:
        return self._recovery_runtime.try_recovery_flow(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=stage,
            gate_label=gate_label,
            reason=reason,
        )

    @staticmethod
    def _is_recoverable_failure(repository_path: Path, stage: JobStage) -> bool:
        return RecoveryRuntime.is_recoverable_failure(repository_path, stage)

    def _run_failure_assistant(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        reason: str,
    ) -> None:
        self._recovery_runtime.run_failure_assistant(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
            reason=reason,
        )

    def _resolve_app_type(self, repository_path: Path, paths: Dict[str, Path]) -> str:
        return self._app_type_runtime.resolve_app_type(repository_path, paths)

    def _stage_skip_ux_review_for_non_web(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        *,
        app_type: str,
    ) -> None:
        self._app_type_runtime.stage_skip_ux_review_for_non_web(
            job,
            repository_path,
            paths,
            log_path,
            app_type=app_type,
        )

    @staticmethod
    def _hard_gate_max_attempts() -> int:
        return RecoveryRuntime.hard_gate_max_attempts()

    @staticmethod
    def _hard_gate_timebox_seconds() -> int:
        return RecoveryRuntime.hard_gate_timebox_seconds()

    def _latest_test_failure_signature(self, repository_path: Path, stage: JobStage) -> str:
        return self._recovery_runtime.latest_test_failure_signature(repository_path, stage)

    def _stage_ux_e2e_review(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._ux_review_runtime.stage_ux_e2e_review(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _capture_ux_screenshots(
        self,
        repository_path: Path,
        preview_info: Dict[str, str],
        log_path: Path,
    ) -> Dict[str, Dict[str, str]]:
        return self._ux_review_runtime.capture_ux_screenshots(
            repository_path=repository_path,
            preview_info=preview_info,
            log_path=log_path,
        )

    def _write_ux_review_markdown(
        self,
        repository_path: Path,
        spec_path: Optional[Path],
        preview_info: Dict[str, str],
        screenshot_info: Dict[str, Dict[str, str]],
        tests_passed: bool,
    ) -> None:
        self._ux_review_runtime.write_ux_review_markdown(
            repository_path=repository_path,
            spec_path=spec_path,
            preview_info=preview_info,
            screenshot_info=screenshot_info,
            tests_passed=tests_passed,
        )

    @staticmethod
    def _extract_spec_checklist(spec_path: Optional[Path]) -> List[str]:
        return UxReviewRuntime.extract_spec_checklist(spec_path)

    def _run_fix_retry_loop_after_test_failure(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._recovery_runtime.run_fix_retry_loop_after_test_failure(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_summarize_code_changes(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> None:
        self._summary_runtime.stage_summarize_code_changes(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
        )

    def _build_code_change_summary_prompt(
        self,
        job: JobRecord,
        changed_files: List[Dict[str, str]],
        numstats: Dict[str, Dict[str, str]],
    ) -> str:
        return self._summary_runtime._build_code_change_summary_prompt(
            job=job,
            changed_files=changed_files,
            numstats=numstats,
        )

    def _summarize_changes_with_copilot(
        self,
        job: JobRecord,
        prompt: str,
        repository_path: Path,
        log_path: Path,
    ) -> Optional[str]:
        return self._summary_runtime._summarize_changes_with_helper(
            job=job,
            prompt=prompt,
            repository_path=repository_path,
            log_path=log_path,
        )

    def _write_test_failure_reason(
        self,
        repository_path: Path,
        stage: JobStage,
        reason: str,
    ) -> None:
        """Persist test failure reason without aborting the workflow."""

        self._shell_test_runtime.write_test_failure_reason(
            repository_path=repository_path,
            stage=stage,
            reason=reason,
        )

    def _resolve_test_command(self, stage: JobStage, secondary: bool) -> str:
        """Pick stage-aware tester command with conservative fallbacks."""

        return self._shell_test_runtime.resolve_test_command(stage, secondary)

    def _wrap_test_command_with_timeout(self, command: str, log_path: Path) -> str:
        """Wrap test command with shell timeout when available."""

        return self._shell_test_runtime.wrap_test_command_with_timeout(command, log_path)

    @staticmethod
    def _has_timeout_utility() -> bool:
        """Return True when GNU/BSD timeout utility is available."""

        return ShellTestRuntime.has_timeout_utility()

    @staticmethod
    def _test_command_timeout_seconds() -> int:
        """Read per-test-command timeout in seconds (0 disables wrapping)."""

        return ShellTestRuntime.test_command_timeout_seconds()

    def _write_test_report(
        self,
        repository_path: Path,
        stage: JobStage,
        command_result: object,
        tester_name: str,
        report_suffix: str,
    ) -> Path:
        """Persist stage-level test summary in markdown for dashboard visibility."""

        return self._shell_test_runtime.write_test_report(
            repository_path=repository_path,
            stage=stage,
            command_result=command_result,
            tester_name=tester_name,
            report_suffix=report_suffix,
        )

    @staticmethod
    def _extract_test_counters(text: str) -> Dict[str, int]:
        """Extract common test counters from pytest/jest/vitest-like outputs."""

        return ShellTestRuntime.extract_test_counters(text)

    @staticmethod
    def _tail_text(text: str, max_lines: int) -> str:
        """Return only tail lines so report size stays readable."""

        return ShellTestRuntime.tail_text(text, max_lines)

    @staticmethod
    def _safe_slug(value: str) -> str:
        """Convert label text to safe uppercase slug."""

        return ShellTestRuntime.safe_slug(value)

    def _stage_commit(
        self,
        job: JobRecord,
        repository_path: Path,
        stage: JobStage,
        log_path: Path,
        commit_type: str,
    ) -> None:
        self._summary_runtime.stage_commit(
            job=job,
            repository_path=repository_path,
            stage=stage,
            log_path=log_path,
            commit_type=commit_type,
        )

    def _prepare_commit_summary_with_ai(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        changed_paths: List[str],
        log_path: Path,
    ) -> str:
        return self._summary_runtime.prepare_commit_summary_with_ai(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            changed_paths=changed_paths,
            log_path=log_path,
        )

    def _prepare_commit_summary_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        log_path: Path,
    ) -> str:
        return self._summary_runtime._prepare_commit_summary_with_template(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            log_path=log_path,
        )

    def _prepare_commit_summary_with_copilot(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        changed_paths: List[str],
        log_path: Path,
    ) -> str:
        return self._summary_runtime._prepare_commit_summary_with_helper(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            changed_paths=changed_paths,
            log_path=log_path,
        )

    @staticmethod
    def _sanitize_commit_summary(raw: str) -> str:
        return SummaryRuntime.sanitize_commit_summary(raw)

    @staticmethod
    def _is_usable_commit_summary(summary: str) -> bool:
        return SummaryRuntime.is_usable_commit_summary(summary)

    def _stage_review_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._review_fix_runtime.stage_review_with_gemini(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _stage_fix_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._review_fix_runtime.stage_fix_with_codex(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    @staticmethod
    def _read_improvement_runtime_context(paths: Dict[str, Path]) -> Dict[str, Any]:
        """Read current improvement strategy and next-task summary."""
        return read_improvement_runtime_context(paths)

    def _stage_push_branch(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        self._provider_runtime.stage_push_branch(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
        )

    def _stage_create_pr(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._provider_runtime.stage_create_pr(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _deploy_preview_and_smoke_test(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> Dict[str, str]:
        return self._preview_runtime.deploy_preview_and_smoke_test(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
        )

    def _detect_container_port(self, repository_path: Path) -> int:
        return self._preview_runtime.detect_container_port(repository_path)

    def _append_preview_section_to_pr_body(self, pr_body_path: Path, preview_info: Dict[str, str]) -> None:
        self._preview_runtime.append_preview_section_to_pr_body(pr_body_path, preview_info)

    def _build_preview_pr_section(self, preview_info: Dict[str, str]) -> str:
        return self._preview_runtime.build_preview_pr_section(preview_info)

    def _write_preview_markdown(self, repository_path: Path, preview_info: Dict[str, str]) -> None:
        self._preview_runtime.write_preview_markdown(repository_path, preview_info)

    def _allocate_preview_port(self) -> Optional[int]:
        return self._preview_runtime.allocate_preview_port()

    @staticmethod
    def _is_local_port_in_use(port: int) -> bool:
        return PreviewRuntime.is_local_port_in_use(port)

    @staticmethod
    def _probe_http(url: str) -> bool:
        return PreviewRuntime.probe_http(url)

    def _get_pr_url(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        create_result: Optional[object],
    ) -> Optional[str]:
        return self._provider_runtime.get_pr_url(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
            create_result=create_result,
        )

    def _stage_prepare_pr_summary_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> Optional[Path]:
        return self._summary_runtime.stage_prepare_pr_summary(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )

    def _commit_markdown_changes_after_stage(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        log_path: Path,
    ) -> None:
        self._docs_snapshot_runtime.commit_markdown_changes_after_stage(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            log_path=log_path,
        )

    def _write_stage_md_snapshot(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        changed_lines: List[str],
        changed_lines_all: List[str],
        log_path: Path,
    ) -> None:
        self._docs_snapshot_runtime.write_stage_md_snapshot(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            changed_lines=changed_lines,
            changed_lines_all=changed_lines_all,
            log_path=log_path,
        )

    def _collect_stage_file_snapshots(
        self,
        repository_path: Path,
        changed_lines_all: List[str],
    ) -> List[Dict[str, Any]]:
        return self._docs_snapshot_runtime.collect_stage_file_snapshots(
            repository_path,
            changed_lines_all,
        )

    @staticmethod
    def _parse_porcelain_path(raw_line: str) -> str:
        return DocsSnapshotRuntime.parse_porcelain_path(raw_line)

    @staticmethod
    def _should_skip_md_commit(changed_md_paths: List[str]) -> bool:
        return DocsSnapshotRuntime.should_skip_md_commit(changed_md_paths)

    @staticmethod
    def _canonical_stage_name(stage_name: str) -> str:
        return DocsSnapshotRuntime.canonical_stage_name(stage_name)

    @staticmethod
    def _format_stage_display_name(stage_name: str) -> str:
        return DocsSnapshotRuntime.format_stage_display_name(stage_name)

    def _run_optional_escalation(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Run optional escalation template after a failure."""
        self._job_failure_runtime.run_optional_escalation(job_id, log_path, last_error)

    def _finalize_failed_job(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Best-effort cleanup when all retries are exhausted."""
        self._job_failure_runtime.finalize_failed_job(job_id, log_path, last_error)

    def _try_create_wip_pr(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Try to commit STATUS.md and open a draft PR after fatal failure."""
        self._job_failure_runtime.try_create_wip_pr(job, repository_path, log_path)

    def _build_template_variables(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        prompt_file_path: Path,
    ) -> Dict[str, str]:
        return self._template_artifact_runtime.build_template_variables(
            job,
            paths,
            prompt_file_path,
        )

    def _ensure_design_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._template_artifact_runtime.ensure_design_artifacts(repository_path, paths, log_path)

    def _ensure_publisher_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._template_artifact_runtime.ensure_publisher_artifacts(repository_path, paths, log_path)

    def _ensure_copywriter_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._template_artifact_runtime.ensure_copywriter_artifacts(repository_path, paths, log_path)

    def _ensure_documentation_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._template_artifact_runtime.ensure_documentation_artifacts(repository_path, paths, log_path)

    def _is_design_system_locked(self, repository_path: Path, paths: Dict[str, Path]) -> bool:
        return self._design_governance_runtime.is_design_system_locked(repository_path, paths)

    def _lock_design_system_decision(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._design_governance_runtime.lock_design_system_decision(repository_path, paths, log_path)

    def _read_decisions_payload(self, repository_path: Path) -> Dict[str, Any]:
        return self._design_governance_runtime.read_decisions_payload(repository_path)

    def _write_decisions_payload(self, repository_path: Path, payload: Dict[str, Any]) -> None:
        self._design_governance_runtime.write_decisions_payload(repository_path, payload)

    @staticmethod
    def _read_json_file(path: Optional[Path]) -> Dict[str, Any]:
        return ArtifactIoRuntime.read_json_file(path)

    @staticmethod
    def _read_text_file(path: Optional[Path]) -> str:
        return ArtifactIoRuntime.read_text_file(path)

    @staticmethod
    def _extract_review_todo_items(review_text: str) -> List[str]:
        return ArtifactIoRuntime.extract_review_todo_items(review_text)

    @staticmethod
    def _stable_issue_id(raw_text: str) -> str:
        return ArtifactIoRuntime.stable_issue_id(raw_text)

    @staticmethod
    def _build_operating_principle_alignment(
        *,
        product_brief_exists: bool,
        user_flows_exists: bool,
        mvp_scope_exists: bool,
        architecture_exists: bool,
        mvp_has_out_of_scope: bool,
        mvp_has_gates: bool,
        flows_has_primary: bool,
        flows_has_entry_exit: bool,
        review_exists: bool,
        ux_review_exists: bool,
        test_report_count: int,
        todo_items_count: int,
        priority_summary: Dict[str, int],
        candidate_count: int,
        scores: Dict[str, int],
        overall: float,
    ) -> Dict[str, Dict[str, Any]]:
        return ProductReviewRuntime.build_operating_principle_alignment(
            product_brief_exists=product_brief_exists,
            user_flows_exists=user_flows_exists,
            mvp_scope_exists=mvp_scope_exists,
            architecture_exists=architecture_exists,
            mvp_has_out_of_scope=mvp_has_out_of_scope,
            mvp_has_gates=mvp_has_gates,
            flows_has_primary=flows_has_primary,
            flows_has_entry_exit=flows_has_entry_exit,
            review_exists=review_exists,
            ux_review_exists=ux_review_exists,
            test_report_count=test_report_count,
            todo_items_count=todo_items_count,
            priority_summary=priority_summary,
            candidate_count=candidate_count,
            scores=scores,
            overall=overall,
        )

    def _collect_product_review_evidence(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
        spec_text: str,
        plan_text: str,
        review_text: str,
        ux_review_text: str,
        test_report_paths: List[Path],
        todo_items: List[str],
    ) -> Dict[str, Any]:
        return self._product_review_runtime.collect_product_review_evidence(
            repository_path=repository_path,
            paths=paths,
            spec_text=spec_text,
            plan_text=plan_text,
            review_text=review_text,
            ux_review_text=ux_review_text,
            test_report_paths=test_report_paths,
            todo_items=todo_items,
        )

    @staticmethod
    def _summarize_operating_policy(
        principle_alignment: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        return ProductReviewRuntime.summarize_operating_policy(principle_alignment)

    @staticmethod
    def _build_repo_maturity_snapshot(
        *,
        job_id: str,
        scores: Dict[str, int],
        overall: float,
        artifact_health: Dict[str, Any],
        quality_gate: Dict[str, Any],
        principle_alignment: Dict[str, Dict[str, Any]],
        previous_level: str,
    ) -> Dict[str, Any]:
        return ProductReviewRuntime.build_repo_maturity_snapshot(
            job_id=job_id,
            scores=scores,
            overall=overall,
            artifact_health=artifact_health,
            quality_gate=quality_gate,
            principle_alignment=principle_alignment,
            previous_level=previous_level,
        )

    @staticmethod
    def _build_quality_trend_snapshot(
        *,
        job_id: str,
        history_entries: List[Dict[str, Any]],
        maturity_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        return ProductReviewRuntime.build_quality_trend_snapshot(
            job_id=job_id,
            history_entries=history_entries,
            maturity_snapshot=maturity_snapshot,
        )

    @staticmethod
    def _validate_product_review_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        return ProductReviewRuntime.validate_product_review_payload(payload)

    def _write_self_growing_effectiveness_artifact(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        review_payload: Dict[str, Any],
        maturity_snapshot: Dict[str, Any],
        trend_snapshot: Dict[str, Any],
        review_history_entries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self._self_growing_effectiveness_runtime.write_self_growing_effectiveness_artifact(
            job=job,
            repository_path=repository_path,
            paths=paths,
            review_payload=review_payload,
            maturity_snapshot=maturity_snapshot,
            trend_snapshot=trend_snapshot,
            review_history_entries=review_history_entries,
        )

    @staticmethod
    def _write_stage_contracts_doc(path: Path, json_path: Path) -> None:
        DesignGovernanceRuntime.write_stage_contracts_doc(path, json_path)

    @staticmethod
    def _write_pipeline_analysis_doc(path: Path, json_path: Path) -> None:
        DesignGovernanceRuntime.write_pipeline_analysis_doc(path, json_path)

    @staticmethod
    def _sha256_file(path: Optional[Path]) -> str:
        return RepositoryStageRuntime.sha256_file(path)

    @staticmethod
    def _docs_file(repository_path: Path, name: str) -> Path:
        return RepositoryStageRuntime.docs_file(repository_path, name)

    def _ref_exists(self, repository_path: Path, ref_name: str, log_path: Path) -> bool:
        return self._repository_stage_runtime.ref_exists(repository_path, ref_name, log_path)

    def _push_branch_with_recovery(
        self,
        repository_path: Path,
        branch_name: str,
        log_path: Path,
        purpose: str,
    ) -> None:
        self._provider_runtime.push_branch_with_recovery(
            repository_path=repository_path,
            branch_name=branch_name,
            log_path=log_path,
            purpose=purpose,
        )

    def _set_stage(self, job_id: str, stage: JobStage, log_path: Path) -> None:
        self._repository_stage_runtime.set_stage(job_id, stage, log_path)

    def _run_shell(
        self,
        command: str,
        cwd: Path,
        log_path: Path,
        purpose: str,
    ):
        """Run shell command with shared logging and strict error handling."""

        return self._shell_test_runtime.run_shell(
            command=command,
            cwd=cwd,
            log_path=log_path,
            purpose=purpose,
        )

    def _execute_shell_command(
        self,
        *,
        command: str,
        cwd: Path,
        log_writer,
        check: bool,
        command_purpose: str,
    ):
        """Run one shell command and attach heartbeat hooks when supported."""

        return self._shell_test_runtime.execute_shell_command(
            command=command,
            cwd=cwd,
            log_writer=log_writer,
            check=check,
            command_purpose=command_purpose,
        )

    def _actor_log_writer(self, log_path: Path, actor: str):
        """Return a log writer that annotates each line with actor information."""

        return self._job_log_runtime.actor_log_writer(
            log_path,
            actor,
            append_actor_log=self._append_actor_log,
        )

    @staticmethod
    def _infer_actor_from_command(command: str, purpose: str) -> str:
        return JobLogRuntime.infer_actor_from_command(command, purpose)

    def _append_actor_log(self, log_path: Path, actor: str, message: str) -> None:
        """Append one timestamped actor-tagged line to job log file."""

        self._job_log_runtime.append_actor_log(
            log_path,
            actor,
            message,
            touch_job_heartbeat=self._touch_job_heartbeat,
        )

    def _touch_job_heartbeat(self, *, force: bool = False) -> None:
        """Persist one lightweight heartbeat for the active job."""

        self._last_heartbeat_monotonic = self._job_log_runtime.touch_job_heartbeat(
            active_job_id=self._active_job_id,
            last_heartbeat_monotonic=self._last_heartbeat_monotonic,
            force=force,
        )

    @staticmethod
    def _channel_log_path(log_path: Path, channel: str) -> Path:
        return JobLogRuntime.channel_log_path(log_path, channel)

    @staticmethod
    def _should_emit_user_log(message: str) -> bool:
        return JobLogRuntime.should_emit_user_log(message)

    def _is_escalation_enabled(self) -> bool:
        return self._job_mode_runtime.is_escalation_enabled()

    def _is_recovery_mode_enabled(self) -> bool:
        return self._job_mode_runtime.is_recovery_mode_enabled()

    @staticmethod
    def _is_long_track(job: JobRecord) -> bool:
        return JobModeRuntime.is_long_track(job)

    @staticmethod
    def _is_ultra_track(job: JobRecord) -> bool:
        return JobModeRuntime.is_ultra_track(job)

    @staticmethod
    def _is_ultra10_track(job: JobRecord) -> bool:
        return JobModeRuntime.is_ultra10_track(job)

    def _resolve_ai_route(self, route_name: str):
        return self._ai_route_runtime.resolve_ai_route(route_name)

    def _template_candidates_for_route(self, route_name: str) -> List[str]:
        return self._ai_route_runtime.template_candidates_for_route(route_name)

    def _build_route_runtime_context(self, route_name: str) -> str:
        return self._ai_route_runtime.build_route_runtime_context(route_name)

    def _route_allows_tool(self, route_name: str, tool_name: str) -> bool:
        return self._ai_route_runtime.route_allows_tool(route_name, tool_name)

    def _template_for_route(self, route_name: str) -> str:
        return self._ai_route_runtime.template_for_route(route_name)

    def _template_for_route_in_repository(
        self,
        route_name: str,
        repository_path: Path,
        log_path: Path | None = None,
    ) -> str:
        return self._ai_route_runtime.template_for_route_in_repository(
            route_name,
            repository_path,
            log_path,
        )

    def _find_configured_template_for_route(self, route_name: str) -> Optional[str]:
        return self._ai_route_runtime.find_configured_template_for_route(route_name)

    def _stop_signal_path(self, job_id: str) -> Path:
        return self._job_control_runtime.stop_signal_path(job_id)

    def _is_stop_requested(self, job_id: str) -> bool:
        return self._job_control_runtime.is_stop_requested(job_id)

    def _clear_stop_requested(self, job_id: str) -> None:
        self._job_control_runtime.clear_stop_requested(job_id)

    def _set_agent_profile(self, profile: str) -> None:
        self._agent_profile = self._job_control_runtime.normalize_agent_profile(profile)

    @staticmethod
    def _append_log(log_path: Path, message: str) -> None:
        JobLogRuntime.append_log(log_path, message, utc_now_iso=utc_now_iso)

    def _require_job(self, job_id: str) -> JobRecord:
        return self._job_control_runtime.require_job(job_id)
