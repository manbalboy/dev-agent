"""Rule-based orchestration engine for AgentHub jobs.

Important design principle:
- This module is the conductor.
- AI CLIs are workers called at fixed points.
- The order, retries, and termination conditions are code-driven.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import socket
import time
from typing import Any, Callable, Dict, List, Optional, Set
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4

from app.command_runner import (
    CommandExecutionError,
    CommandTemplateRunner,
    run_shell_command,
)
from app.ai_role_routing import AIRoleRouter
from app.config import AppSettings
from app.feature_flags import is_feature_enabled
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.prompt_builder import (
    build_architecture_plan_prompt,
    build_commit_message_prompt,
    build_copywriter_prompt,
    build_coder_prompt,
    build_documentation_prompt,
    build_designer_prompt,
    build_mvp_scope_prompt,
    build_planner_prompt,
    build_product_brief_prompt,
    build_project_scaffolding_prompt,
    build_publisher_prompt,
    build_pr_summary_prompt,
    build_reviewer_prompt,
    build_spec_json,
    build_spec_markdown,
    build_status_markdown,
    build_user_flows_prompt,
)
from app.planner_graph import build_refinement_instruction, evaluate_plan_markdown
from app.spec_tools import (
    issue_reader,
    repo_context_reader,
    risk_policy_checker,
    spec_rewriter,
    spec_schema_validator,
)
from app.store import JobStore
from app.workflow_design import validate_workflow
from app.workflow_registry import (
    WORKFLOW_NODE_HANDLER_NAMES,
    WORKFLOW_NODE_SKIP_AUTO_COMMIT,
)
from app.workflow_resume import (
    build_workflow_artifact_paths,
    compute_workflow_resume_state,
    linearize_workflow_nodes,
    read_improvement_runtime_context,
)
from app.workflow_resolution import load_workflow_catalog, resolve_workflow_selection
from app.memory.fix_store import FixStore, NoOpFixStore
from app.memory.runtime_ingest import ingest_memory_runtime_artifacts
from app.memory.runtime_store import MemoryRuntimeStore
from app.recovery_runtime import RecoveryRuntime
from app.shell_test_runtime import ShellTestRuntime
from app.tool_runtime import ToolRequest, ToolResult, ToolRuntime


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
        self._memory_runtime_db_path = settings.resolved_memory_dir / "memory_runtime.db"
        self._memory_runtime_store: MemoryRuntimeStore | None = None
        self._fix_store: FixStore | NoOpFixStore = (
            FixStore(settings.resolved_memory_dir) if settings.memory_enabled else NoOpFixStore()
        )
        self._shell_test_runtime = ShellTestRuntime(
            settings=self.settings,
            shell_executor=self.shell_executor,
            shell_executor_accepts_heartbeat=self._shell_executor_accepts_heartbeat,
            touch_job_heartbeat=self._touch_job_heartbeat,
            actor_log_writer=self._actor_log_writer,
            infer_actor_from_command=self._infer_actor_from_command,
            set_stage=self._set_stage,
            append_actor_log=self._append_actor_log,
            is_long_track=self._is_long_track,
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
        )
        self._tool_runtime = ToolRuntime(
            command_templates=self.command_templates,
            docs_file=self._docs_file,
            build_template_variables=self._build_template_variables,
            template_for_route=self._template_for_route,
            actor_log_writer=self._actor_log_writer,
            append_actor_log=self._append_actor_log,
            build_local_evidence_fallback=self._build_local_evidence_fallback,
        )
        self._install_command_template_heartbeat()

    def _install_command_template_heartbeat(self) -> None:
        """Attach heartbeat hooks to the template runner when it supports attributes."""

        try:
            setattr(self.command_templates, "heartbeat_callback", self._touch_job_heartbeat)
            setattr(self.command_templates, "heartbeat_interval_seconds", 10.0)
        except Exception:
            return

    @staticmethod
    def _callable_accepts_kwargs(target: Callable[..., object], names: Set[str]) -> bool:
        """Return True when one callable exposes every requested keyword parameter."""

        try:
            parameters = inspect.signature(target).parameters
        except (TypeError, ValueError):
            return False
        return names.issubset(parameters.keys())

    def _feature_enabled(self, flag_name: str) -> bool:
        """Read one adaptive feature flag without requiring process restart."""

        return is_feature_enabled(self.feature_flags_path, flag_name)

    def _get_memory_runtime_store(self) -> MemoryRuntimeStore:
        """Create the canonical memory DB lazily so normal API boot stays light."""

        if self._memory_runtime_store is None:
            self._memory_runtime_store = MemoryRuntimeStore(self._memory_runtime_db_path)
        return self._memory_runtime_store

    def process_next_job(self) -> bool:
        """Pop one job from queue and process it.

        Returns:
            True if a job was processed, False if queue was empty.
        """

        job_id = self.store.dequeue_job()
        if job_id is None:
            return False

        self.process_job(job_id)
        return True

    def process_job(self, job_id: str) -> None:
        """Run one job with retry policy and final failure handling."""

        job = self._require_job(job_id)
        log_path = self.settings.logs_debug_dir / job.log_file
        self._active_job_id = job_id
        self._last_heartbeat_monotonic = 0.0
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Starting job {job.job_id} for issue #{job.issue_number}",
        )

        self.store.update_job(
            job_id,
            status=JobStatus.RUNNING.value,
            stage=JobStage.QUEUED.value,
            started_at=job.started_at or utc_now_iso(),
            heartbeat_at=utc_now_iso(),
            error_message=None,
        )
        self._touch_job_heartbeat(force=True)

        try:
            if self._is_ultra10_track(job):
                self._process_ultra_job(job_id, log_path, max_runtime_hours=10, mode_tag="ULTRA10")
                return
            if self._is_ultra_track(job):
                self._process_ultra_job(job_id, log_path)
                return
            if self._is_long_track(job):
                self._process_long_job(job_id, log_path)
                return

            last_error: Optional[str] = None
            initial_attempt = max(1, int(job.attempt or 0) + 1)
            for attempt in range(initial_attempt, job.max_attempts + 1):
                self.store.update_job(
                    job_id,
                    attempt=attempt,
                    error_message=None,
                    heartbeat_at=utc_now_iso(),
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Attempt {attempt}/{job.max_attempts} started",
                )
                self._touch_job_heartbeat(force=True)

                try:
                    self._run_single_attempt(job_id, log_path)
                    self.store.update_job(
                        job_id,
                        status=JobStatus.DONE.value,
                        stage=JobStage.DONE.value,
                        finished_at=utc_now_iso(),
                        heartbeat_at=utc_now_iso(),
                        error_message=None,
                    )
                    self._append_actor_log(log_path, "ORCHESTRATOR", "Job finished successfully")
                    return
                except Exception as error:  # noqa: BLE001 - we want resilient orchestration.
                    last_error = str(error)
                    self.store.update_job(
                        job_id,
                        error_message=last_error,
                        heartbeat_at=utc_now_iso(),
                    )
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Attempt {attempt} failed: {last_error}",
                    )

                    if self._is_escalation_enabled() and self.command_templates.has_template(
                        "escalation"
                    ):
                        self._run_optional_escalation(job_id, log_path, last_error)

                    if attempt < job.max_attempts:
                        self._append_actor_log(
                            log_path,
                            "ORCHESTRATOR",
                            "Retrying with a fresh attempt.",
                        )
                    else:
                        self._append_actor_log(
                            log_path,
                            "ORCHESTRATOR",
                            "Maximum retry count reached. Finalizing as failed.",
                        )

            self._finalize_failed_job(job_id, log_path, last_error or "Unknown error")
        finally:
            self._active_job_id = None
            self._last_heartbeat_monotonic = 0.0

    def _process_long_job(self, job_id: str, log_path: Path) -> None:
        """Run long-track mode with fixed 3 rounds of full workflow."""

        job = self._require_job(job_id)
        total_rounds = 3
        last_error: Optional[str] = None
        start_round = max(1, int(job.attempt or 0) + 1)
        for round_index in range(start_round, total_rounds + 1):
            self.store.update_job(
                job_id,
                attempt=round_index,
                error_message=None,
                heartbeat_at=utc_now_iso(),
            )
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[LONG] Round {round_index}/{total_rounds} started",
            )
            self._touch_job_heartbeat(force=True)
            try:
                self._run_single_attempt(job_id, log_path)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} completed",
                )
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self.store.update_job(
                    job_id,
                    error_message=last_error,
                    heartbeat_at=utc_now_iso(),
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} failed: {last_error}",
                )
                if self._is_escalation_enabled() and self._find_configured_template_for_route("escalation"):
                    self._run_optional_escalation(job_id, log_path, last_error)
                self._finalize_failed_job(job_id, log_path, last_error)
                return

        self.store.update_job(
            job_id,
            status=JobStatus.DONE.value,
            stage=JobStage.DONE.value,
            finished_at=utc_now_iso(),
            heartbeat_at=utc_now_iso(),
            error_message=None,
        )
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[LONG] Completed all {total_rounds} rounds successfully",
        )

    def _process_ultra_job(
        self,
        job_id: str,
        log_path: Path,
        max_runtime_hours: int = 5,
        mode_tag: str = "ULTRA",
    ) -> None:
        """Run ultra-long mode with round loop and graceful stop."""

        job = self._require_job(job_id)
        ultra_started = time.monotonic()
        round_index = int(job.attempt or 0)
        last_error: Optional[str] = None
        max_runtime_seconds = max_runtime_hours * 60 * 60

        while True:
            elapsed = time.monotonic() - ultra_started
            if elapsed >= max_runtime_seconds:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{mode_tag} mode max runtime ({max_runtime_hours}h) reached. "
                    "Finishing after current rounds.",
                )
                self.store.update_job(
                    job_id,
                    status=JobStatus.DONE.value,
                    stage=JobStage.DONE.value,
                    finished_at=utc_now_iso(),
                    heartbeat_at=utc_now_iso(),
                    error_message=None,
                )
                return

            if self._is_stop_requested(job_id):
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Stop requested before next round. Finishing ultra job.",
                )
                self._clear_stop_requested(job_id)
                self.store.update_job(
                    job_id,
                    status=JobStatus.DONE.value,
                    stage=JobStage.DONE.value,
                    finished_at=utc_now_iso(),
                    heartbeat_at=utc_now_iso(),
                    error_message=None,
                )
                return

            round_index += 1
            self.store.update_job(
                job_id,
                attempt=round_index,
                error_message=None,
                heartbeat_at=utc_now_iso(),
            )
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[{mode_tag}] Round {round_index} started",
            )
            self._touch_job_heartbeat(force=True)

            try:
                self._agent_profile = "primary"
                self._run_single_attempt(job_id, log_path)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Round {round_index} completed with primary agents.",
                )
            except Exception as primary_error:  # noqa: BLE001
                last_error = str(primary_error)
                self.store.update_job(
                    job_id,
                    error_message=last_error,
                    heartbeat_at=utc_now_iso(),
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Primary agents failed in round {round_index}: {last_error}",
                )

                if self._is_escalation_enabled() and self._find_configured_template_for_route("escalation"):
                    self._run_optional_escalation(job_id, log_path, last_error)

                try:
                    self._agent_profile = "fallback"
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Trying fallback agents for round {round_index}.",
                    )
                    self._run_single_attempt(job_id, log_path)
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Round {round_index} recovered by fallback agents.",
                    )
                except Exception as fallback_error:  # noqa: BLE001
                    last_error = str(fallback_error)
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Fallback agents also failed in round {round_index}: {last_error}",
                    )
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[{mode_tag}] Two-agent failure reached. Ending this ultra job.",
                    )
                    self._agent_profile = "primary"
                    self._finalize_failed_job(job_id, log_path, last_error)
                    return
                finally:
                    self._agent_profile = "primary"

            if self._is_stop_requested(job_id):
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Stop requested. Ending after round {round_index}.",
                )
                self._clear_stop_requested(job_id)
                self.store.update_job(
                    job_id,
                    status=JobStatus.DONE.value,
                    stage=JobStage.DONE.value,
                    finished_at=utc_now_iso(),
                    error_message=None,
                )
                return

    def _run_single_attempt(self, job_id: str, log_path: Path) -> None:
        """Execute one attempt with workflow-config first, fixed flow fallback."""

        job = self._require_job(job_id)
        repository_path = self._stage_prepare_repo(job, log_path)
        workflow = self._load_active_workflow(job, log_path)
        if workflow is None:
            self._run_fixed_pipeline(job, repository_path, log_path)
            return

        ordered_nodes = self._linearize_workflow_nodes(workflow)
        if not ordered_nodes:
            raise CommandExecutionError("Workflow has no executable nodes.")
        resume_state = self._resolve_workflow_resume_state(
            job=job,
            repository_path=repository_path,
            workflow=workflow,
            ordered_nodes=ordered_nodes,
        )

        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Using workflow '{workflow.get('workflow_id', 'unknown')}'",
        )
        if resume_state["mode"] == "resume":
            skipped_count = len(resume_state.get("skipped_nodes", []))
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                (
                    "Workflow resume active: "
                    f"attempt={resume_state['current_attempt']} "
                    f"source_attempt={resume_state['source_attempt']} "
                    f"from={resume_state['resume_from_node_id']} "
                    f"({resume_state['resume_from_node_type']}) "
                    f"skipped={skipped_count}"
                ),
            )
        elif int(resume_state.get("source_attempt", 0)) > 0:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow resume skipped: {resume_state.get('reason_code', 'full_rerun')}",
            )
        self._run_workflow_pipeline(
            job,
            repository_path,
            workflow,
            ordered_nodes,
            log_path,
            resume_state=resume_state,
        )

    def _run_fixed_pipeline(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Run legacy hard-coded pipeline (fallback path)."""

        issue = self._stage_read_issue(job, repository_path, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.READ_ISSUE.value, log_path
        )
        paths = self._stage_write_spec(job, repository_path, issue, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.WRITE_SPEC.value, log_path
        )
        self._stage_idea_to_product_brief(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IDEA_TO_PRODUCT_BRIEF.value, log_path
        )
        self._stage_generate_user_flows(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.GENERATE_USER_FLOWS.value, log_path
        )
        self._stage_define_mvp_scope(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DEFINE_MVP_SCOPE.value, log_path
        )
        self._stage_architecture_planning(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.ARCHITECTURE_PLANNING.value, log_path
        )
        self._stage_project_scaffolding(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PROJECT_SCAFFOLDING.value, log_path
        )

        self._stage_plan_with_gemini(job, repository_path, paths, log_path)
        self._snapshot_plan_variant(repository_path, paths, "general", log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PLAN_WITH_GEMINI.value, log_path
        )
        self._stage_design_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DESIGN_WITH_CODEX.value, log_path
        )
        self._stage_publish_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, "publisher_task", log_path
        )
        self._stage_implement_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IMPLEMENT_WITH_CODEX.value, log_path
        )
        self._stage_summarize_code_changes(job, repository_path, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.SUMMARIZE_CODE_CHANGES.value, log_path
        )
        self._run_test_hard_gate(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label="after_implement",
        )
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.TEST_AFTER_IMPLEMENT.value, log_path
        )
        self._stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.COMMIT_IMPLEMENT.value, log_path
        )

        self._stage_review_with_gemini(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.REVIEW_WITH_GEMINI.value, log_path
        )
        self._stage_product_review(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PRODUCT_REVIEW.value, log_path
        )
        self._stage_improvement_stage(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IMPROVEMENT_STAGE.value, log_path
        )
        self._stage_fix_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.FIX_WITH_CODEX.value, log_path
        )
        self._run_test_hard_gate(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label="after_fix",
        )
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.TEST_AFTER_FIX.value, log_path
        )
        self._stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.COMMIT_FIX.value, log_path
        )
        self._stage_documentation_with_claude(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DOCUMENTATION_TASK.value, log_path
        )

        self._stage_push_branch(job, repository_path, log_path)
        self._stage_create_pr(job, repository_path, paths, log_path)
        self._set_stage(job.job_id, JobStage.FINALIZE, log_path)

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

        context: Dict[str, Any] = {
            "issue": None,
            "paths": None,
            "last_node_result": None,
            "results": {},
            "loop_counters": {},
        }
        current_node_id = str(workflow.get("entry_node_id", "")).strip()
        if not current_node_id:
            current_node_id = str(ordered_nodes[0].get("id", "")).strip() if ordered_nodes else ""

        if isinstance(resume_state, dict) and resume_state.get("enabled"):
            current_node_id = str(resume_state.get("resume_from_node_id", "")).strip() or current_node_id
            context["paths"] = build_workflow_artifact_paths(repository_path)
            skipped_nodes = resume_state.get("skipped_nodes", [])
            if isinstance(skipped_nodes, list) and skipped_nodes:
                skipped_labels = ", ".join(
                    str(item.get("id", "")).strip() or str(item.get("type", "")).strip()
                    for item in skipped_nodes
                    if isinstance(item, dict)
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow resume reuses completed nodes: {skipped_labels}",
                )

        nodes_by_id, edges_by_source = self._build_workflow_runtime_maps(workflow, ordered_nodes)
        if not current_node_id:
            raise CommandExecutionError("Workflow has no entry node to execute.")

        step_limit = max(64, len(nodes_by_id) * 8)
        step_count = 0
        while current_node_id:
            step_count += 1
            if step_count > step_limit:
                raise CommandExecutionError(
                    f"Workflow exceeded step limit ({step_limit}). "
                    "Next action: inspect loop edges and loop_until_pass settings."
                )

            node = nodes_by_id.get(current_node_id)
            if not isinstance(node, dict):
                raise CommandExecutionError(f"Workflow node not found during execution: {current_node_id}")

            node_id = str(node.get("id", ""))
            node_type = str(node.get("type", ""))
            node_notes = str(node.get("notes", "")).strip()
            previous_agent_profile = self._agent_profile
            previous_route_role_overrides = dict(self._workflow_route_role_overrides)
            effective_agent_profile = self._workflow_node_agent_profile(node)
            node_route_role_overrides = self._workflow_node_route_role_overrides(node)
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow node start: {node_id} ({node_type})",
            )
            if node_notes:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node note: {node_notes}",
                )
            if effective_agent_profile != previous_agent_profile:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node agent profile override: {previous_agent_profile} -> {effective_agent_profile}",
                )
            self._agent_profile = effective_agent_profile
            self._workflow_route_role_overrides = node_route_role_overrides
            if node_route_role_overrides:
                binding_items = ", ".join(
                    f"{route_name}->{role_code}"
                    for route_name, role_code in sorted(node_route_role_overrides.items())
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node role binding: {binding_items}",
                )

            executor = self._resolve_workflow_node_executor(node_type)
            if executor is None:
                self._agent_profile = previous_agent_profile
                self._workflow_route_role_overrides = previous_route_role_overrides
                raise CommandExecutionError(f"Unsupported workflow node type: {node_type}")
            node_run: NodeRunRecord | None = None
            node_event = "success"
            node_status = "success"
            node_error_message: str | None = None
            node_message = ""
            node_exception: Exception | None = None
            try:
                node_run = self._start_node_run(job, workflow, node)
                result = executor(
                    job=job,
                    repository_path=repository_path,
                    node=node,
                    context=context,
                    log_path=log_path,
                )
                normalized_result = self._normalize_workflow_node_result(result)
                node_event = normalized_result["event"]
                node_status = normalized_result["status"]
                node_error_message = normalized_result.get("error_message")
                node_message = normalized_result.get("message", "")
            except Exception as error:
                node_event = "failure"
                node_status = "failed"
                node_error_message = str(error)
                node_exception = error
            finally:
                self._agent_profile = previous_agent_profile
                self._workflow_route_role_overrides = previous_route_role_overrides
            if node_run is not None:
                self._finish_node_run(
                    node_run,
                    status=node_status,
                    error_message=node_error_message,
                )

            if node_message:
                self._append_actor_log(log_path, "ORCHESTRATOR", node_message)

            context["last_node_result"] = {
                "node_id": node_id,
                "node_type": node_type,
                "node_title": str(node.get("title", "")).strip(),
                "event": node_event,
                "status": node_status,
                "error_message": node_error_message or "",
            }
            self._record_workflow_node_result(
                context=context,
                node=node,
                node_run=node_run,
                event=node_event,
                status=node_status,
                error_message=node_error_message,
                route_role_overrides=node_route_role_overrides,
            )

            if node_status == "success" and node_type not in WORKFLOW_NODE_SKIP_AUTO_COMMIT:
                self._commit_markdown_changes_after_stage(job, repository_path, node_type, log_path)

            next_node_id = self._resolve_next_workflow_node_id(
                edges_by_source=edges_by_source,
                node_id=node_id,
                event=node_event,
            )
            if next_node_id:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow edge selected: {node_id} --{node_event}--> {next_node_id}",
                )
                current_node_id = next_node_id
                continue

            if node_event == "failure":
                if node_exception is not None:
                    raise node_exception
                raise CommandExecutionError(
                    f"Workflow node {node_id} produced failure event without failure edge."
                )

            current_node_id = ""

        self._set_stage(job.job_id, JobStage.FINALIZE, log_path)

    def _resolve_workflow_resume_state(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Resolve whether the current attempt should resume from a failed node."""

        improvement_runtime = self._read_improvement_runtime_context(
            build_workflow_artifact_paths(repository_path)
        )
        resume_state = compute_workflow_resume_state(
            workflow_id=str(workflow.get("workflow_id", "")).strip(),
            ordered_nodes=ordered_nodes,
            node_runs=self.store.list_node_runs(job.job_id),
            current_attempt=max(1, int(job.attempt or 1)),
            strategy=str(improvement_runtime.get("strategy", "")).strip(),
            scope_restriction=str(improvement_runtime.get("scope_restriction", "")).strip(),
            manual_mode=str(job.manual_resume_mode or "").strip(),
            manual_node_id=str(job.manual_resume_node_id or "").strip(),
            manual_note=str(job.manual_resume_note or "").strip(),
        )
        if str(job.manual_resume_mode or "").strip():
            self.store.update_job(
                job.job_id,
                manual_resume_mode="",
                manual_resume_node_id="",
                manual_resume_requested_at=None,
                manual_resume_note="",
            )
        return resume_state

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
        return None

    def _workflow_node_agent_profile(self, node: Dict[str, Any]) -> str:
        """Return the effective agent profile for one workflow node."""

        requested = str(node.get("agent_profile", "")).strip().lower()
        if requested in {"primary", "fallback"}:
            return requested
        return self._agent_profile

    @staticmethod
    def _normalize_workflow_binding_id(value: str, *, max_length: int = 64) -> str:
        """Normalize one workflow role-binding identifier."""

        lowered = str(value or "").strip().lower()
        filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
        return filtered[:max_length]

    def _workflow_node_route_names(self, node: Dict[str, Any]) -> tuple[str, ...]:
        """Return logical AI routes affected by one workflow node."""

        node_type = str(node.get("type", "")).strip()
        return WORKFLOW_NODE_ROUTE_NAMES.get(node_type, ())

    def _workflow_node_route_role_overrides(self, node: Dict[str, Any]) -> Dict[str, str]:
        """Resolve route->role overrides requested by one workflow node."""

        route_names = self._workflow_node_route_names(node)
        if not route_names:
            return {}

        explicit_role_code = self._normalize_workflow_binding_id(str(node.get("role_code", "")))
        preset_id = self._normalize_workflow_binding_id(str(node.get("role_preset_id", "")))
        if not explicit_role_code and not preset_id:
            return {}

        overrides: Dict[str, str] = {}
        for route_name in route_names:
            if explicit_role_code:
                resolved = self.ai_role_router.resolve(route_name, role_code_override=explicit_role_code)
                if resolved.role_code == explicit_role_code:
                    overrides[route_name] = resolved.role_code
                continue

            resolved = self.ai_role_router.resolve(route_name, preset_id=preset_id)
            if resolved.role_code:
                overrides[route_name] = resolved.role_code
        return overrides

    def _start_node_run(
        self,
        job: JobRecord,
        workflow: Dict[str, Any],
        node: Dict[str, Any],
    ) -> NodeRunRecord:
        """Persist one workflow node start event."""

        node_run = NodeRunRecord(
            node_run_id=uuid4().hex,
            job_id=job.job_id,
            workflow_id=str(workflow.get("workflow_id", "")).strip(),
            node_id=str(node.get("id", "")).strip(),
            node_type=str(node.get("type", "")).strip(),
            node_title=str(node.get("title", "")).strip(),
            status="running",
            attempt=max(1, int(job.attempt or 0)),
            started_at=utc_now_iso(),
            agent_profile=self._agent_profile,
        )
        self.store.upsert_node_run(node_run)
        return node_run

    def _finish_node_run(
        self,
        node_run: NodeRunRecord,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Persist one workflow node completion event."""

        updated = replace(
            node_run,
            status=status,
            finished_at=utc_now_iso(),
            error_message=error_message,
        )
        self.store.upsert_node_run(updated)

    @staticmethod
    def _build_workflow_runtime_maps(
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
        """Build runtime node/edge maps while preserving declared edge order."""

        raw_nodes = workflow.get("nodes", [])
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        source_nodes = raw_nodes if isinstance(raw_nodes, list) and raw_nodes else ordered_nodes
        for node in source_nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", "")).strip()
            if node_id:
                nodes_by_id[node_id] = node

        edges_by_source: Dict[str, List[Dict[str, str]]] = {}
        raw_edges = workflow.get("edges", [])
        if isinstance(raw_edges, list):
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    continue
                src = str(edge.get("from", "")).strip()
                dst = str(edge.get("to", "")).strip()
                event = str(edge.get("on", "success")).strip().lower() or "success"
                if not src or not dst:
                    continue
                edges_by_source.setdefault(src, []).append({"to": dst, "on": event})

        return nodes_by_id, edges_by_source

    @staticmethod
    def _normalize_workflow_node_result(result: Any) -> Dict[str, str]:
        """Normalize handler return into one workflow event/status payload."""

        if result is None:
            return {"event": "success", "status": "success", "message": "", "error_message": ""}
        if isinstance(result, str):
            event = result.strip().lower() or "success"
            return {"event": event, "status": "success", "message": "", "error_message": ""}
        if isinstance(result, dict):
            event = str(result.get("event", "success")).strip().lower() or "success"
            status = str(result.get("status", "success")).strip().lower() or "success"
            message = str(result.get("message", "")).strip()
            error_message = str(result.get("error_message", "")).strip()
            return {
                "event": event,
                "status": status,
                "message": message,
                "error_message": error_message,
            }
        return {"event": "success", "status": "success", "message": "", "error_message": ""}

    @staticmethod
    def _resolve_next_workflow_node_id(
        *,
        edges_by_source: Dict[str, List[Dict[str, str]]],
        node_id: str,
        event: str,
    ) -> str:
        """Return the next node id for one outcome event with always-edge fallback."""

        outgoing = edges_by_source.get(node_id, [])
        normalized_event = str(event or "success").strip().lower() or "success"
        for edge in outgoing:
            if str(edge.get("on", "")).strip().lower() == normalized_event:
                return str(edge.get("to", "")).strip()
        for edge in outgoing:
            if str(edge.get("on", "")).strip().lower() == "always":
                return str(edge.get("to", "")).strip()
        return ""

    def _workflow_context_issue(self, context: Dict[str, Any]) -> IssueDetails:
        issue = context.get("issue")
        if not isinstance(issue, IssueDetails):
            raise CommandExecutionError("Workflow requires issue context before write_spec.")
        return issue

    def _workflow_context_paths(self, context: Dict[str, Any]) -> Dict[str, Path]:
        paths = context.get("paths")
        if not isinstance(paths, dict):
            raise CommandExecutionError("Workflow requires paths context before AI/test/git stages.")
        return paths

    def _record_workflow_node_result(
        self,
        *,
        context: Dict[str, Any],
        node: Dict[str, Any],
        node_run: NodeRunRecord | None,
        event: str,
        status: str,
        error_message: str | None,
        route_role_overrides: Optional[Dict[str, str]] = None,
    ) -> None:
        """Persist one normalized node result into workflow context."""

        results = context.setdefault("results", {})
        if not isinstance(results, dict):
            results = {}
            context["results"] = results

        artifact_info = self._workflow_result_artifact_info(context)
        result_payload = {
            "node_id": str(node.get("id", "")).strip(),
            "node_type": str(node.get("type", "")).strip(),
            "node_title": str(node.get("title", "")).strip(),
            "event": str(event or "success").strip().lower() or "success",
            "status": str(status or "success").strip().lower() or "success",
            "error_message": str(error_message or "").strip(),
            "attempt": int(getattr(node_run, "attempt", 0) or 0),
            "started_at": str(getattr(node_run, "started_at", "") or ""),
            "finished_at": str(getattr(node_run, "finished_at", "") or ""),
            "agent_profile": str(getattr(node_run, "agent_profile", self._agent_profile) or self._agent_profile),
            "role_code": str(node.get("role_code", "")).strip(),
            "role_preset_id": str(node.get("role_preset_id", "")).strip(),
            "route_role_overrides": dict(route_role_overrides or {}),
            "artifact_keys": artifact_info["keys"],
            "artifacts": artifact_info["paths"],
        }
        results[result_payload["node_id"]] = result_payload

    @staticmethod
    def _workflow_result_artifact_info(context: Dict[str, Any]) -> Dict[str, List[str]]:
        """Return currently available artifact keys and paths for workflow results."""

        paths = context.get("paths")
        if not isinstance(paths, dict):
            return {"keys": [], "paths": []}

        artifact_keys: List[str] = []
        artifact_paths: List[str] = []
        for key, raw_path in paths.items():
            if not isinstance(raw_path, Path) or not raw_path.exists():
                continue
            artifact_keys.append(str(key))
            artifact_paths.append(str(raw_path))
        artifact_keys.sort()
        artifact_paths.sort()
        return {"keys": artifact_keys, "paths": artifact_paths}

    def _workflow_node_read_issue(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        context["issue"] = self._stage_read_issue(job, repository_path, log_path)

    def _workflow_node_if_label_match(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> Dict[str, str]:
        issue = self._workflow_context_issue(context)
        raw_match_labels = str(node.get("match_labels", "")).strip()
        requested_labels = [item.strip().lower() for item in raw_match_labels.split(",") if item.strip()]
        if not requested_labels:
            raise CommandExecutionError("if_label_match requires match_labels metadata.")

        match_mode = str(node.get("match_mode", "any")).strip().lower() or "any"
        issue_labels = {label.strip().lower() for label in issue.labels if str(label).strip()}
        matched = False
        if match_mode == "all":
            matched = all(label in issue_labels for label in requested_labels)
        elif match_mode == "none":
            matched = all(label not in issue_labels for label in requested_labels)
        else:
            matched = any(label in issue_labels for label in requested_labels)

        result_event = "success" if matched else "failure"
        return {
            "event": result_event,
            "status": "success",
            "message": (
                f"if_label_match evaluated labels={sorted(issue_labels)} "
                f"against required={requested_labels} mode={match_mode} -> {result_event}"
            ),
        }

    def _workflow_node_loop_until_pass(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> Dict[str, str]:
        last_result = context.get("last_node_result")
        if not isinstance(last_result, dict):
            raise CommandExecutionError("loop_until_pass requires previous node result context.")

        last_event = str(last_result.get("event", "success")).strip().lower() or "success"
        loop_key = str(node.get("id", "")).strip() or str(node.get("title", "")).strip() or "loop"
        raw_limit = node.get("loop_max_iterations", 3)
        try:
            max_iterations = int(raw_limit or 3)
        except (TypeError, ValueError) as error:
            raise CommandExecutionError("loop_until_pass requires integer loop_max_iterations.") from error
        max_iterations = max(1, min(10, max_iterations))

        loop_counters = context.setdefault("loop_counters", {})
        if not isinstance(loop_counters, dict):
            loop_counters = {}
            context["loop_counters"] = loop_counters

        if last_event == "success":
            loop_counters[loop_key] = 0
            return {
                "event": "success",
                "status": "success",
                "message": f"loop_until_pass exit: previous node succeeded, loop={loop_key}",
            }

        current_count = int(loop_counters.get(loop_key, 0) or 0) + 1
        loop_counters[loop_key] = current_count
        if current_count <= max_iterations:
            return {
                "event": "failure",
                "status": "success",
                "message": f"loop_until_pass retry {current_count}/{max_iterations} for loop={loop_key}",
            }

        raise CommandExecutionError(
            f"loop_until_pass exceeded max iterations ({max_iterations}) for loop={loop_key}"
        )

    def _workflow_node_write_spec(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        issue = self._workflow_context_issue(context)
        context["paths"] = self._stage_write_spec(job, repository_path, issue, log_path)

    def _workflow_node_gemini_plan(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self._workflow_context_paths(context)
        node_title = str(node.get("title", "")).strip().lower()
        requested_mode = str(node.get("planning_mode", "")).strip().lower()
        planning_mode = "general"
        if requested_mode in {"general", "dev_planning", "big_picture"}:
            planning_mode = requested_mode
        elif "개발 기획" in node_title or "development" in node_title:
            planning_mode = "dev_planning"
        elif "큰틀" in node_title or "big picture" in node_title:
            planning_mode = "big_picture"
        self._stage_plan_with_gemini(
            job,
            repository_path,
            paths,
            log_path,
            planning_mode=planning_mode,
        )
        self._snapshot_plan_variant(repository_path, paths, planning_mode, log_path)

    def _workflow_node_idea_to_product_brief(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_idea_to_product_brief(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_generate_user_flows(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_generate_user_flows(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_define_mvp_scope(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_define_mvp_scope(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_architecture_planning(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_architecture_planning(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_project_scaffolding(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_project_scaffolding(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_designer_task(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        if self._is_design_system_locked(repository_path, paths):
            self._set_stage(job.job_id, JobStage.DESIGN_WITH_CODEX, log_path)
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "designer_task skipped by decision lock (_docs/DECISIONS.json).",
            )
            return
        self._stage_design_with_codex(job, repository_path, paths, log_path)
        self._lock_design_system_decision(repository_path, paths, log_path)

    def _workflow_node_publisher_task(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_publish_with_codex(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_copywriter_task(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_copywriter_with_codex(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_documentation_task(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_documentation_with_claude(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_codex_implement(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_implement_with_codex(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_code_change_summary(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._workflow_context_paths(context)
        self._stage_summarize_code_changes(job, repository_path, log_path)

    def _workflow_node_test_after_implement(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label=f"after_implement_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_tester_task(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label=f"tester_task_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_commit_implement(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._workflow_context_paths(context)
        self._stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")

    def _workflow_node_gemini_review(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_review_with_gemini(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_product_review(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_product_review(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_improvement_stage(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_improvement_stage(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_codex_fix(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_fix_with_codex(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_coder_fix_from_test_report(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_fix_with_codex(job, repository_path, self._workflow_context_paths(context), log_path)

    def _workflow_node_test_after_fix(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"after_fix_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_tester_run_e2e(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self._run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=JobStage.TEST_AFTER_FIX,
                gate_label="tester_run_e2e_web",
            )
            return
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"tester_run_e2e routed for app_type={app_type}. Running non-web test gate by policy.",
        )
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"tester_nonweb_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_ux_e2e_review(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self._stage_ux_e2e_review(job, repository_path, paths, log_path)
            return
        self._stage_skip_ux_review_for_non_web(job, repository_path, paths, log_path, app_type=app_type)

    def _workflow_node_test_after_fix_final(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"after_fix_final_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_tester_retest_e2e(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        paths = self._workflow_context_paths(context)
        app_type = self._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self._run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=JobStage.TEST_AFTER_FIX,
                gate_label="tester_retest_e2e_web",
            )
            return
        self._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"tester_retest_nonweb_{app_type}",
            app_type=app_type,
        )

    def _workflow_node_commit_fix(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._workflow_context_paths(context)
        self._stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")

    def _workflow_node_push_branch(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._workflow_context_paths(context)
        self._stage_push_branch(job, repository_path, log_path)

    def _workflow_node_create_pr(self, *, job: JobRecord, repository_path: Path, node: Dict[str, Any], context: Dict[str, Any], log_path: Path) -> None:
        self._stage_create_pr(job, repository_path, self._workflow_context_paths(context), log_path)

    def _load_active_workflow(self, job: JobRecord, log_path: Path) -> Optional[Dict[str, Any]]:
        """Resolve and load one workflow config; fallback to fixed pipeline on any error."""

        workflow_path = Path.cwd() / "config" / "workflows.json"
        apps_path = Path.cwd() / "config" / "apps.json"
        try:
            default_id, workflows_by_id = load_workflow_catalog(workflow_path)
            if not default_id or not workflows_by_id:
                return None
            selection = resolve_workflow_selection(
                requested_workflow_id=job.workflow_id,
                app_code=job.app_code,
                repository=job.repository,
                apps_path=apps_path,
                workflows_path=workflow_path,
            )
            selected = workflows_by_id.get(selection.workflow_id)
            if selection.warning:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow resolution warning: {selection.warning}",
                )
            if selected is None and selection.workflow_id != default_id:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Resolved workflow '{selection.workflow_id}' missing. Falling back to default '{default_id}'.",
                )
                selected = workflows_by_id.get(default_id)
            if not isinstance(selected, dict):
                return None
            ok, errors = validate_workflow(selected)
            if not ok:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Workflow validation failed; fallback to fixed pipeline: "
                    + "; ".join(errors),
                )
                return None
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow resolved: source={selection.source}, workflow_id={selected.get('workflow_id', default_id)}",
            )
            return selected
        except Exception as error:  # noqa: BLE001 - fallback is intentional
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow load failed; fallback to fixed pipeline: {error}",
            )
            return None

    @staticmethod
    def _linearize_workflow_nodes(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return linear execution order from entry node over success/always edges."""
        return linearize_workflow_nodes(workflow)

    @staticmethod
    def _job_execution_repository(job: JobRecord) -> str:
        """Return repository used for clone/build/push for one job."""

        source_repository = str(job.source_repository or "").strip()
        return source_repository or str(job.repository or "").strip()

    def _job_workspace_path(self, job: JobRecord) -> Path:
        """Resolve workspace path using execution repository."""

        return self.settings.repository_workspace_path(self._job_execution_repository(job), job.app_code)

    def _issue_reference_line(self, job: JobRecord) -> str:
        """Return PR-safe issue reference text.

        If execution repository differs from the issue hub repository, using
        `Closes #<n>` would target the wrong repository issue. In that case we
        keep a full tracking URL instead.
        """

        if self._job_execution_repository(job) != str(job.repository or "").strip():
            return f"Tracking issue: {job.issue_url}"
        return f"Closes #{job.issue_number}"

    def _stage_prepare_repo(self, job: JobRecord, log_path: Path) -> Path:
        self._set_stage(job.job_id, JobStage.PREPARE_REPO, log_path)
        repository_path = self._job_workspace_path(job)
        execution_repository = self._job_execution_repository(job)

        if not execution_repository:
            raise CommandExecutionError(
                "No execution repository is configured for this job. "
                "Set app.source_repository or job.source_repository before running."
            )

        if not repository_path.exists():
            self._clone_repository_to_workspace(execution_repository, repository_path, log_path)
        elif not self._workspace_has_git_metadata(repository_path):
            backup_path = self._backup_invalid_workspace(repository_path, log_path)
            self._append_log(
                log_path,
                f"Workspace existed without git metadata. Backed up to {backup_path} and recloning.",
            )
            self._clone_repository_to_workspace(execution_repository, repository_path, log_path)
        else:
            current_origin = self._read_workspace_origin_repository(repository_path, log_path)
            if current_origin and current_origin != execution_repository:
                backup_path = self._backup_invalid_workspace(repository_path, log_path)
                self._append_log(
                    log_path,
                    f"Workspace origin mismatch ({current_origin} != {execution_repository}). "
                    f"Backed up to {backup_path} and recloning.",
                )
                self._clone_repository_to_workspace(execution_repository, repository_path, log_path)
            else:
                self._append_log(log_path, f"Repository already exists at {repository_path}")

        self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} fetch origin",
            cwd=repository_path,
            log_path=log_path,
            purpose="git fetch",
        )

        default_remote_ref = f"origin/{self.settings.default_branch}"
        job_remote_ref = f"origin/{job.branch_name}"
        remote_ref = default_remote_ref

        # Why this matters:
        # On retries (or re-processing the same job), the remote job branch may
        # already contain earlier commits. If we always reset from default branch,
        # push can fail with non-fast-forward. Using the remote job branch when it
        # exists keeps history linear for this job branch.
        if self._ref_exists(repository_path, job_remote_ref, log_path):
            remote_ref = job_remote_ref

        self._append_log(log_path, f"Branch base selected: {remote_ref}")
        checkout_command = (
            f"git -C {shlex.quote(str(repository_path))} checkout -B "
            f"{shlex.quote(job.branch_name)} {shlex.quote(remote_ref)}"
        )

        try:
            self._run_shell(
                command=checkout_command,
                cwd=repository_path,
                log_path=log_path,
                purpose="branch checkout",
            )
        except CommandExecutionError:
            # Why we retry with fallback:
            # Some repos might not expose the configured default branch remotely,
            # especially in fresh forks. We still create a working local branch.
            self._append_log(
                log_path,
                "Default branch checkout failed. Falling back to local branch creation.",
            )
            self._run_shell(
                command=(
                    f"git -C {shlex.quote(str(repository_path))} checkout -B "
                    f"{shlex.quote(job.branch_name)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="fallback branch checkout",
            )

        self._ensure_workspace_git_excludes(repository_path, log_path)
        return repository_path

    def _clone_repository_to_workspace(self, execution_repository: str, repository_path: Path, log_path: Path) -> None:
        """Clone the configured execution repository into the job workspace."""

        self._run_shell(
            command=f"gh repo clone {shlex.quote(execution_repository)} {shlex.quote(str(repository_path))}",
            cwd=self.settings.workspace_dir,
            log_path=log_path,
            purpose="repository clone",
        )

    @staticmethod
    def _workspace_has_git_metadata(repository_path: Path) -> bool:
        """Return True when the workspace already contains git metadata."""

        git_path = repository_path / ".git"
        return git_path.exists()

    def _read_workspace_origin_repository(self, repository_path: Path, log_path: Path) -> str:
        """Return normalized `owner/repo` from workspace origin remote if available."""

        try:
            result = self._run_shell(
                command=f"git -C {shlex.quote(str(repository_path))} remote get-url origin",
                cwd=repository_path,
                log_path=log_path,
                purpose="git remote origin",
            )
        except CommandExecutionError:
            return ""
        return self._normalize_repository_ref(str(result.stdout or "").strip())

    @staticmethod
    def _normalize_repository_ref(value: str) -> str:
        """Normalize GitHub repository references to `owner/repo`."""

        normalized = (value or "").strip()
        if not normalized:
            return ""
        normalized = normalized.removesuffix(".git")
        https_match = re.match(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)$", normalized)
        if https_match:
            return f"{https_match.group('owner')}/{https_match.group('repo')}"
        ssh_match = re.match(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+)$", normalized)
        if ssh_match:
            return f"{ssh_match.group('owner')}/{ssh_match.group('repo')}"
        simple_match = re.match(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$", normalized)
        if simple_match:
            return f"{simple_match.group('owner')}/{simple_match.group('repo')}"
        return normalized

    def _backup_invalid_workspace(self, repository_path: Path, log_path: Path) -> Path:
        """Move aside a non-git workspace so clone can recreate it safely."""

        parent = repository_path.parent
        base_name = repository_path.name
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        candidate = parent / f"{base_name}__invalid_{timestamp}"
        suffix = 1
        while candidate.exists():
            suffix += 1
            candidate = parent / f"{base_name}__invalid_{timestamp}_{suffix}"
        shutil.move(str(repository_path), str(candidate))
        self._append_log(log_path, f"Moved invalid workspace to backup path: {candidate}")
        return candidate

    def _ensure_workspace_git_excludes(self, repository_path: Path, log_path: Path) -> None:
        """Apply one shared workspace ignore file to each cloned repository."""

        shared_ignore = self.settings.workspace_dir / ".agenthub-global.gitignore"
        patterns = [
            "node_modules/",
            "**/node_modules/",
            ".venv/",
            "**/.venv/",
            "__pycache__/",
            "**/__pycache__/",
            "*.pyc",
            ".pytest_cache/",
            "**/.pytest_cache/",
            ".mypy_cache/",
            "**/.mypy_cache/",
            ".next/",
            "**/.next/",
            ".turbo/",
            "**/.turbo/",
            "dist/",
            "**/dist/",
            "build/",
            "**/build/",
            ".DS_Store",
            "*.log",
        ]
        desired = "\n".join(patterns).rstrip() + "\n"
        current = ""
        if shared_ignore.exists():
            current = shared_ignore.read_text(encoding="utf-8", errors="replace")
        if current != desired:
            shared_ignore.parent.mkdir(parents=True, exist_ok=True)
            shared_ignore.write_text(desired, encoding="utf-8")
            self._append_log(log_path, f"Workspace shared ignore updated: {shared_ignore}")

        self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} "
                f"config --local core.excludesfile {shlex.quote(str(shared_ignore))}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="set workspace shared git excludes",
        )

    def _stage_read_issue(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> IssueDetails:
        self._set_stage(job.job_id, JobStage.READ_ISSUE, log_path)
        result = self._run_shell(
            command=(
                f"gh issue view {job.issue_number} --repo {shlex.quote(job.repository)} "
                "--json title,body,url,labels"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="read issue",
        )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise CommandExecutionError(
                "Could not parse issue details from gh issue view output. "
                "Next action: run the same command manually and verify gh CLI auth."
            ) from error

        labels_payload = payload.get("labels", [])
        labels: List[str] = []
        if isinstance(labels_payload, list):
            for item in labels_payload:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                else:
                    name = str(item).strip()
                if name:
                    labels.append(name)

        return IssueDetails(
            title=str(payload.get("title", job.issue_title)),
            body=str(payload.get("body", "")),
            url=str(payload.get("url", job.issue_url)),
            labels=tuple(labels),
        )

    def _stage_write_spec(
        self,
        job: JobRecord,
        repository_path: Path,
        issue: IssueDetails,
        log_path: Path,
    ) -> Dict[str, Path]:
        self._set_stage(job.job_id, JobStage.WRITE_SPEC, log_path)
        paths = build_workflow_artifact_paths(repository_path)
        spec_path = paths["spec"]
        spec_json_path = paths["spec_json"]
        spec_quality_path = paths["spec_quality"]
        stage_contracts_path = paths["stage_contracts"]
        stage_contracts_json_path = paths["stage_contracts_json"]
        pipeline_analysis_path = paths["pipeline_analysis"]
        pipeline_analysis_json_path = paths["pipeline_analysis_json"]

        spec_content = build_spec_markdown(
            repository=job.repository,
            issue_number=job.issue_number,
            issue_url=issue.url,
            issue_title=issue.title,
            issue_body=issue.body,
            preview_host=self.settings.docker_preview_host,
            preview_port_start=self.settings.docker_preview_port_start,
            preview_port_end=self.settings.docker_preview_port_end,
            preview_cors_origins=self.settings.docker_preview_cors_origins,
        )
        spec_path.write_text(spec_content, encoding="utf-8")
        spec_json = build_spec_json(
            repository=job.repository,
            issue_number=job.issue_number,
            issue_url=issue.url,
            issue_title=issue.title,
            issue_body=issue.body,
        )
        issue_context = issue_reader(
            issue_title=issue.title,
            issue_body=issue.body,
            issue_url=issue.url,
        )
        repo_context = repo_context_reader(repository_path)
        risk_report = risk_policy_checker(spec_json)
        validation = spec_schema_validator(spec_json)
        rewrites: List[Dict[str, Any]] = []
        max_rewrite_rounds = 2
        for round_index in range(1, max_rewrite_rounds + 1):
            if validation.get("passed"):
                break
            revised, actions = spec_rewriter(spec_json, validation)
            if not actions:
                break
            rewrites.append(
                {
                    "round": round_index,
                    "actions": actions,
                    "reject_codes": validation.get("reject_codes", []),
                }
            )
            spec_json = revised
            validation = spec_schema_validator(spec_json)

        spec_json["_quality"] = {
            "validation": validation,
            "rewrites": rewrites,
            "risk_report": risk_report,
            "issue_context": {
                "keywords": issue_context.get("keywords", []),
                "line_count": issue_context.get("line_count", 0),
            },
            "repo_context": {
                "stack": repo_context.get("stack", []),
                "has_readme_excerpt": bool(repo_context.get("readme_excerpt", "")),
            },
        }
        spec_json_path.write_text(
            json.dumps(spec_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        spec_quality_path.write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "issue_number": job.issue_number,
                    "validation": validation,
                    "rewrites": rewrites,
                    "risk_report": risk_report,
                    "issue_context": issue_context,
                    "repo_context": repo_context,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC.md at {spec_path}")
        self._append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC.json at {spec_json_path}")
        self._append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC_QUALITY.json at {spec_quality_path}")
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            (
                "SPEC quality check: "
                f"passed={validation.get('passed')} score={validation.get('score')} "
                f"reject_codes={','.join(validation.get('reject_codes', [])) or '-'}"
            ),
        )
        if not bool(validation.get("passed")):
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "SPEC quality gate not passed, but continuing by non-blocking assist policy.",
            )
        self._write_stage_contracts_doc(stage_contracts_path, stage_contracts_json_path)
        self._write_pipeline_analysis_doc(pipeline_analysis_path, pipeline_analysis_json_path)

        # Keep job metadata in sync with canonical issue data.
        self.store.update_job(
            job.job_id,
            issue_title=issue.title,
            issue_url=issue.url,
        )
        return paths

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
        """Run one markdown-generation stage with one refinement retry before fallback."""

        retry_feedback = ""
        max_rounds = 2
        last_error: str | None = None
        for round_index in range(1, max_rounds + 1):
            prompt_path.write_text(prompt_builder(retry_feedback), encoding="utf-8")
            if round_index > 1 and output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            try:
                result = self.command_templates.run_template(
                    template_name=self._template_for_route("planner"),
                    variables={
                        **self._build_template_variables(job, paths, prompt_path),
                        "plan_path": str(output_path),
                    },
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, actor),
                )
                if result.stdout.strip() and not output_path.exists():
                    output_path.write_text(result.stdout, encoding="utf-8")
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{stage_name} AI call failed on round {round_index}, using fallback: {error}",
                )
                break

            missing = self._missing_markdown_sections(
                output_path,
                required_sections,
                required_evidence=required_evidence,
            )
            if not missing:
                return
            last_error = f"missing sections: {', '.join(missing)}"
            if round_index >= max_rounds:
                break
            retry_feedback = (
                "이전 출력이 계약을 충족하지 못했습니다.\n"
                f"- 보완 필요 항목: {', '.join(missing)}\n"
                f"- 문서에 반드시 다음 값을 정확히 포함: {', '.join(required_evidence)}"
            )
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{stage_name} refinement retry requested: {', '.join(missing)}",
            )

        fallback_writer()
        self._ensure_markdown_stage_contract(
            stage_name=stage_name,
            path=output_path,
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=None,
            log_path=log_path,
        )
        if last_error:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{stage_name} fallback applied after AI refinement failure: {last_error}",
            )

    def _stage_idea_to_product_brief(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create PRODUCT_BRIEF.md — AI-generated from spec context."""

        self._set_stage(job.job_id, JobStage.IDEA_TO_PRODUCT_BRIEF, log_path)
        product_brief_path = paths.get("product_brief", self._docs_file(repository_path, "PRODUCT_BRIEF.md"))
        prompt_path = self._docs_file(repository_path, "PRODUCT_BRIEF_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "product_goal": ["product goal", "제품 목표", "goal"],
            "problem_statement": ["problem statement", "문제 정의", "pain"],
            "target_users": ["target users", "타겟 사용자", "사용자"],
            "core_value": ["core value", "핵심 가치", "차별 가치"],
            "scope_inputs": ["scope inputs", "in scope", "범위"],
            "success_metrics": ["success metrics", "성공 지표", "지표"],
            "non_goals": ["non-goals", "non goals", "비범위", "제외"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self._run_markdown_generation_with_refinement(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage_name=JobStage.IDEA_TO_PRODUCT_BRIEF.value,
            actor="PRODUCT_BRIEF",
            output_path=product_brief_path,
            prompt_path=prompt_path,
            prompt_builder=lambda retry_feedback: build_product_brief_prompt(
                spec_path=str(paths.get("spec", "")),
                product_brief_path=str(product_brief_path),
                job_id=job.job_id,
                issue_title=job.issue_title,
                retry_feedback=retry_feedback,
            ),
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=lambda: self._write_product_brief_fallback(job, paths, product_brief_path),
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"PRODUCT_BRIEF.md ready: {product_brief_path}")

    def _stage_generate_user_flows(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create USER_FLOWS.md — AI-generated from product brief."""

        self._set_stage(job.job_id, JobStage.GENERATE_USER_FLOWS, log_path)
        user_flows_path = paths.get("user_flows", self._docs_file(repository_path, "USER_FLOWS.md"))
        product_brief_path = paths.get("product_brief", self._docs_file(repository_path, "PRODUCT_BRIEF.md"))
        prompt_path = self._docs_file(repository_path, "USER_FLOWS_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "primary_flow": ["primary flow", "핵심 흐름", "user journey"],
            "secondary_flows": ["secondary flows", "보조 흐름", "엣지"],
            "ux_state_checklist": ["ux state checklist", "loading", "empty", "error", "상태"],
            "entry_exit_points": ["entry/exit points", "entry", "exit", "진입", "종료"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self._run_markdown_generation_with_refinement(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage_name=JobStage.GENERATE_USER_FLOWS.value,
            actor="USER_FLOWS",
            output_path=user_flows_path,
            prompt_path=prompt_path,
            prompt_builder=lambda retry_feedback: build_user_flows_prompt(
                product_brief_path=str(product_brief_path),
                user_flows_path=str(user_flows_path),
                job_id=job.job_id,
                issue_title=job.issue_title,
                retry_feedback=retry_feedback,
            ),
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=lambda: self._write_user_flows_fallback(job, paths, user_flows_path),
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"USER_FLOWS.md ready: {user_flows_path}")

    def _stage_define_mvp_scope(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create MVP_SCOPE.md — AI-generated with in/out scope and acceptance gates."""

        self._set_stage(job.job_id, JobStage.DEFINE_MVP_SCOPE, log_path)
        mvp_scope_path = paths.get("mvp_scope", self._docs_file(repository_path, "MVP_SCOPE.md"))
        product_brief_path = paths.get("product_brief", self._docs_file(repository_path, "PRODUCT_BRIEF.md"))
        user_flows_path = paths.get("user_flows", self._docs_file(repository_path, "USER_FLOWS.md"))
        prompt_path = self._docs_file(repository_path, "MVP_SCOPE_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "in_scope": ["in scope", "포함", "범위"],
            "out_of_scope": ["out of scope", "비범위", "제외"],
            "acceptance_gates": ["acceptance gate", "완료 조건", "게이트"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self._run_markdown_generation_with_refinement(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage_name=JobStage.DEFINE_MVP_SCOPE.value,
            actor="MVP_SCOPE",
            output_path=mvp_scope_path,
            prompt_path=prompt_path,
            prompt_builder=lambda retry_feedback: build_mvp_scope_prompt(
                product_brief_path=str(product_brief_path),
                user_flows_path=str(user_flows_path),
                spec_json_path=str(paths.get("spec_json", "")),
                mvp_scope_path=str(mvp_scope_path),
                job_id=job.job_id,
                issue_title=job.issue_title,
                retry_feedback=retry_feedback,
            ),
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=lambda: self._write_mvp_scope_fallback(job, paths, mvp_scope_path),
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"MVP_SCOPE.md ready: {mvp_scope_path}")

    def _stage_architecture_planning(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create ARCHITECTURE_PLAN.md — AI-generated for implementation constraints."""

        self._set_stage(job.job_id, JobStage.ARCHITECTURE_PLANNING, log_path)
        architecture_plan_path = paths.get("architecture_plan", self._docs_file(repository_path, "ARCHITECTURE_PLAN.md"))
        mvp_scope_path = paths.get("mvp_scope", self._docs_file(repository_path, "MVP_SCOPE.md"))
        user_flows_path = paths.get("user_flows", self._docs_file(repository_path, "USER_FLOWS.md"))
        prompt_path = self._docs_file(repository_path, "ARCHITECTURE_PLAN_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "layer_structure": ["layer structure", "레이어", "layer"],
            "component_boundaries": ["component boundaries", "컴포넌트 경계", "boundary"],
            "data_contracts": ["data contracts", "데이터 계약", "contract"],
            "quality_gates": ["quality gates", "품질 게이트", "quality gate"],
            "loop_safety_rules": ["loop safety", "루프 안전", "regression", "stagnation"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self._run_markdown_generation_with_refinement(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage_name=JobStage.ARCHITECTURE_PLANNING.value,
            actor="ARCHITECTURE",
            output_path=architecture_plan_path,
            prompt_path=prompt_path,
            prompt_builder=lambda retry_feedback: build_architecture_plan_prompt(
                mvp_scope_path=str(mvp_scope_path),
                user_flows_path=str(user_flows_path),
                architecture_plan_path=str(architecture_plan_path),
                job_id=job.job_id,
                issue_title=job.issue_title,
                retry_feedback=retry_feedback,
            ),
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=lambda: self._write_architecture_plan_fallback(job, paths, architecture_plan_path),
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"ARCHITECTURE_PLAN.md ready: {architecture_plan_path}")

    def _stage_project_scaffolding(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create explicit scaffold-plan artifacts before implementation."""

        self._set_stage(job.job_id, JobStage.PROJECT_SCAFFOLDING, log_path)
        scaffold_plan_path = paths.get("scaffold_plan", self._docs_file(repository_path, "SCAFFOLD_PLAN.md"))
        bootstrap_report_path = paths.get("bootstrap_report", self._docs_file(repository_path, "BOOTSTRAP_REPORT.json"))
        architecture_plan_path = paths.get("architecture_plan", self._docs_file(repository_path, "ARCHITECTURE_PLAN.md"))
        mvp_scope_path = paths.get("mvp_scope", self._docs_file(repository_path, "MVP_SCOPE.md"))
        spec_json_path = paths.get("spec_json", self._docs_file(repository_path, "SPEC.json"))

        repo_context = repo_context_reader(repository_path)
        bootstrap_report = self._build_bootstrap_report(
            repository_path=repository_path,
            spec_json_path=spec_json_path,
            repo_context=repo_context,
        )
        bootstrap_report_path.write_text(
            json.dumps(bootstrap_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        prompt_path = self._docs_file(repository_path, "SCAFFOLD_PLAN_PROMPT.md")
        prompt_path.write_text(
            build_project_scaffolding_prompt(
                architecture_plan_path=str(architecture_plan_path),
                mvp_scope_path=str(mvp_scope_path),
                spec_json_path=str(spec_json_path),
                bootstrap_report_path=str(bootstrap_report_path),
                scaffold_plan_path=str(scaffold_plan_path),
            ),
            encoding="utf-8",
        )
        template_vars = {
            **self._build_template_variables(job, paths, prompt_path),
            "plan_path": str(scaffold_plan_path),
        }
        try:
            result = self.command_templates.run_template(
                template_name=self._template_for_route("planner"),
                variables=template_vars,
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "SCAFFOLD"),
            )
            if not scaffold_plan_path.exists() and result.stdout.strip():
                scaffold_plan_path.write_text(result.stdout, encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"SCAFFOLD_PLAN AI call failed, using template fallback: {error}",
            )
            self._write_project_scaffolding_fallback(bootstrap_report, scaffold_plan_path)
        self._ensure_markdown_stage_contract(
            stage_name=JobStage.PROJECT_SCAFFOLDING.value,
            path=scaffold_plan_path,
            required_sections={
                "repository_state": ["repository state", "레포 상태", "repo state"],
                "bootstrap_mode": ["bootstrap mode", "부트스트랩 모드", "mode"],
                "target_structure": ["target structure", "목표 구조", "directory"],
                "required_setup_commands": ["required setup commands", "초기 명령", "setup commands"],
                "verification_checklist": ["verification checklist", "검증 체크리스트", "checklist"],
            },
            required_evidence=None,
            fallback_writer=lambda: self._write_project_scaffolding_fallback(bootstrap_report, scaffold_plan_path),
            log_path=log_path,
        )
        self._append_actor_log(log_path, "ORCHESTRATOR", f"SCAFFOLD_PLAN.md ready: {scaffold_plan_path}")
        self._append_actor_log(log_path, "ORCHESTRATOR", f"BOOTSTRAP_REPORT.json ready: {bootstrap_report_path}")

    @staticmethod
    def _build_bootstrap_report(
        *,
        repository_path: Path,
        spec_json_path: Optional[Path],
        repo_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Summarize current repo bootstrap state for scaffold planning."""

        top_level_entries = sorted(
            path.name
            for path in repository_path.iterdir()
            if path.name != ".git"
        ) if repository_path.exists() else []
        non_docs_entries = [
            item for item in top_level_entries
            if item not in {"README.md", "_docs", ".github", ".gitignore"}
        ]
        has_runtime_files = any(
            item in top_level_entries
            for item in {
                "package.json",
                "pyproject.toml",
                "requirements.txt",
                "src",
                "app",
                "pages",
                "components",
                "android",
                "ios",
            }
        )
        stack = list(repo_context.get("stack", [])) if isinstance(repo_context.get("stack"), list) else []
        if not non_docs_entries and not stack and not has_runtime_files:
            repository_state = "greenfield"
            bootstrap_mode = "create"
        elif stack or has_runtime_files:
            repository_state = "existing"
            bootstrap_mode = "extend"
        else:
            repository_state = "partial"
            bootstrap_mode = "stabilize"

        spec_payload: Dict[str, Any] = {}
        if isinstance(spec_json_path, Path) and spec_json_path.exists():
            try:
                spec_payload = json.loads(spec_json_path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                spec_payload = {}
        app_type = str(spec_payload.get("app_type", "")).strip() or "unknown"

        recommended_actions: List[str] = []
        if bootstrap_mode == "create":
            recommended_actions.extend(
                [
                    "앱 유형에 맞는 최소 실행 엔트리포인트를 생성한다.",
                    "테스트/실행/문서 기본 파일을 함께 만든다.",
                    "MVP 범위 밖 구조 재작성은 금지한다.",
                ]
            )
        elif bootstrap_mode == "extend":
            recommended_actions.extend(
                [
                    "기존 엔트리포인트와 빌드 체인을 유지한다.",
                    "현재 구조를 최대한 재사용하면서 MVP 기능만 추가한다.",
                    "누락된 테스트/문서만 최소 보강한다.",
                ]
            )
        else:
            recommended_actions.extend(
                [
                    "불완전한 기본 구조를 정리하고 단일 실행 경로를 만든다.",
                    "중복/미사용 scaffold 조각을 정리한다.",
                    "새로운 대규모 프레임워크 교체는 금지한다.",
                ]
            )

        return {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "repository_state": repository_state,
            "bootstrap_mode": bootstrap_mode,
            "app_type": app_type,
            "detected_stack": stack,
            "repo_exists": bool(repo_context.get("exists")),
            "top_level_entries": top_level_entries[:30],
            "has_readme_excerpt": bool(repo_context.get("readme_excerpt", "")),
            "recommended_actions": recommended_actions,
        }

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
        """Validate markdown contract for one stage and optionally recover via fallback."""

        missing = self._missing_markdown_sections(
            path,
            required_sections,
            required_evidence=required_evidence,
        )
        if not missing:
            return
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"{stage_name} contract missing sections: {', '.join(missing)}",
        )
        if fallback_writer is not None:
            fallback_writer()
            missing = self._missing_markdown_sections(
                path,
                required_sections,
                required_evidence=required_evidence,
            )
            if not missing:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{stage_name} contract recovered by fallback writer.",
                )
                return
        raise CommandExecutionError(
            f"{stage_name} contract validation failed. Missing sections: {', '.join(missing)}"
        )

    @staticmethod
    def _missing_markdown_sections(
        path: Path,
        required_sections: Dict[str, List[str]],
        *,
        required_evidence: Optional[List[str]] = None,
    ) -> List[str]:
        """Return missing section keys based on keyword presence checks."""

        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        lowered = text.lower()
        if not lowered.strip():
            missing_items = list(required_sections.keys())
            if required_evidence:
                missing_items.append("source_evidence")
            return missing_items
        missing: List[str] = []
        for section_key, keywords in required_sections.items():
            matched = any(keyword.lower() in lowered for keyword in keywords if keyword.strip())
            if not matched:
                missing.append(section_key)
        normalized_evidence = [term.strip().lower() for term in (required_evidence or []) if term and term.strip()]
        if normalized_evidence and not all(term in lowered for term in normalized_evidence):
            missing.append("source_evidence")
        return missing

    def _ensure_product_definition_ready(
        self,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Hard gate: block implementation when product-definition artifacts are weak."""

        validations = [
            (
                "PRODUCT_BRIEF.md",
                paths.get("product_brief"),
                {
                    "product_goal": ["product goal", "goal"],
                    "target_users": ["target users", "사용자"],
                    "success_metrics": ["success metrics", "지표"],
                },
            ),
            (
                "USER_FLOWS.md",
                paths.get("user_flows"),
                {
                    "primary_flow": ["primary flow", "핵심 흐름"],
                    "ux_state_checklist": ["loading", "empty", "error", "상태"],
                },
            ),
            (
                "MVP_SCOPE.md",
                paths.get("mvp_scope"),
                {
                    "in_scope": ["in scope", "범위"],
                    "out_of_scope": ["out of scope", "비범위"],
                    "acceptance_gates": ["acceptance gate", "완료 조건", "게이트"],
                },
            ),
            (
                "ARCHITECTURE_PLAN.md",
                paths.get("architecture_plan"),
                {
                    "component_boundaries": ["component boundaries", "경계", "boundary"],
                    "quality_gates": ["quality gate", "품질 게이트"],
                    "loop_safety_rules": ["loop safety", "루프 안전", "stagnation", "regression"],
                },
            ),
            (
                "SCAFFOLD_PLAN.md",
                paths.get("scaffold_plan"),
                {
                    "repository_state": ["repository state", "레포 상태", "repo state"],
                    "bootstrap_mode": ["bootstrap mode", "부트스트랩 모드"],
                    "verification_checklist": ["verification checklist", "검증 체크리스트"],
                },
            ),
        ]
        failures: List[str] = []
        for label, path, required in validations:
            if not isinstance(path, Path):
                failures.append(f"{label}: file path missing")
                continue
            missing = self._missing_markdown_sections(path, required)
            if missing:
                failures.append(f"{label}: missing {', '.join(missing)}")
        if failures:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Product-definition hard gate blocked implementation "
                "(principle_1_mvp_first / principle_2_design_first): "
                + " | ".join(failures),
            )
            raise CommandExecutionError(
                "Product-definition artifacts are insufficient under MVP-first/design-first policy. "
                + " ; ".join(failures)
            )

    # ------------------------------------------------------------------
    # Fallback writers — used when AI call fails for product-def stages
    # ------------------------------------------------------------------

    def _write_product_brief_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        product_brief_path: Path,
    ) -> None:
        spec_json = self._read_json_file(paths.get("spec_json"))
        goal = str(spec_json.get("goal", "")).strip() if isinstance(spec_json, dict) else ""
        goal = goal or job.issue_title
        scope_in = spec_json.get("scope_in", []) if isinstance(spec_json, dict) else []
        lines: List[str] = [
            "# PRODUCT BRIEF",
            "",
            "## Context Anchor",
            f"- Job ID: {job.job_id}",
            f"- Issue Title: {job.issue_title}",
            "",
            "## Product Goal",
            f"- {goal}",
            "",
            "## Problem Statement",
            "- 이슈 아이디어를 단발성 코드 생성이 아닌 제품 단위 개발 루프로 전환한다.",
            "",
            "## Target Users",
            "- 문제를 직접 겪는 1차 사용자",
            "- 기능 품질을 유지보수하는 운영/개발 사용자",
            "",
            "## Core Value",
            "- 아이디어 입력부터 MVP 구현, 품질 리뷰, 반복 개선까지 한 파이프라인으로 수행한다.",
            "- 코드 생성보다 품질 평가와 개선 우선순위 결정을 시스템적으로 강제한다.",
            "",
            "## Scope Inputs",
        ]
        for item in (scope_in[:7] if isinstance(scope_in, list) else []):
            if str(item).strip():
                lines.append(f"- {str(item).strip()}")
        lines.extend([
            "",
            "## Success Metrics",
            "- MVP 핵심 시나리오 1개 이상이 재현 가능해야 함",
            "- 테스트 리포트와 제품 리뷰 점수가 누적 저장되어야 함",
            "- 다음 개선 작업이 자동 우선순위로 생성되어야 함",
            "",
            "## Non-Goals",
            "- 이번 MVP 범위 외 신규 대기능 추가",
            "- 자동 배포, 자동 머지",
            "",
        ])
        product_brief_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_user_flows_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        user_flows_path: Path,
    ) -> None:
        spec_json = self._read_json_file(paths.get("spec_json"))
        scope_in = spec_json.get("scope_in", []) if isinstance(spec_json, dict) else []
        first_scope = ""
        if isinstance(scope_in, list):
            for item in scope_in:
                if str(item).strip():
                    first_scope = str(item).strip()
                    break
        first_scope = first_scope or "핵심 MVP 기능"
        lines = [
            "# USER FLOWS",
            "",
            "## Context Anchor",
            f"- Job ID: {job.job_id}",
            f"- Issue Title: {job.issue_title}",
            "",
            "## Primary Flow",
            f"1. 사용자가 `{job.issue_title}` 해결을 위한 작업을 시작한다.",
            f"2. 시스템이 `{first_scope}` 를 이번 MVP 핵심 기능으로 정의한다.",
            "3. 시스템이 제품 정의 문서와 구현 계획을 생성하고 핵심 흐름을 정리한다.",
            "4. 사용자는 핵심 기능을 실행하고 시스템은 즉시 결과나 다음 행동을 보여준다.",
            "5. 실패나 데이터 없음이 발생하면 복구 액션과 대안 경로를 안내한다.",
            "6. 테스트와 리뷰 결과를 바탕으로 품질 이슈를 정리한다.",
            "7. 다음 개선 루프에서 우선순위가 높은 문제부터 다시 수정한다.",
            "",
            "## Secondary Flows",
            f"- 오류 복구 흐름: `{job.issue_title}` 관련 실패 시 재시도, 상태 기록, 복구 액션을 제공한다.",
            "- 품질 정체 흐름: 같은 문제 반복 시 범위를 줄이고 전략을 다시 정의한다.",
            "- 품질 하락 흐름: 이전 안정 상태를 비교해 롤백 후보와 안정화 작업을 기록한다.",
            "",
            "## UX State Checklist",
            "- Loading 상태: 스피너/스켈레톤/진행 메시지 존재 여부",
            "- Empty 상태: 데이터 없음 시 안내/유도 문구 존재 여부",
            "- Error 상태: 실패 사유/복구 액션/재시도 경로 존재 여부",
            "",
            "## Entry/Exit Points",
            "- 진입: GitHub 이슈 생성 또는 웹훅 트리거",
            "- 종료: PR 생성 완료 또는 최대 재시도 횟수 초과",
            "",
        ]
        user_flows_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_mvp_scope_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        mvp_scope_path: Path,
    ) -> None:
        spec_json = self._read_json_file(paths.get("spec_json"))
        scope_in = spec_json.get("scope_in", []) if isinstance(spec_json, dict) else []
        scope_out = spec_json.get("scope_out", []) if isinstance(spec_json, dict) else []
        lines = [
            "# MVP SCOPE",
            "",
            "## Context Anchor",
            f"- Job ID: {job.job_id}",
            f"- Issue Title: {job.issue_title}",
            "",
            "## In Scope",
        ]
        for item in (scope_in[:8] if isinstance(scope_in, list) else []):
            if str(item).strip():
                lines.append(f"- [P1] {str(item).strip()} — 완료 조건: 기능 재현 가능")
        if lines[-1] == "## In Scope":
            lines.append(
                f"- [P1] `{job.issue_title}` 해결에 직접 필요한 핵심 기능 — 완료 조건: 사용자가 목적을 달성 가능"
            )
        lines.extend(["", "## Out of Scope"])
        for item in (scope_out[:8] if isinstance(scope_out, list) else []):
            if str(item).strip():
                lines.append(f"- {str(item).strip()}")
        if lines[-1] == "## Out of Scope":
            lines.append(f"- `{job.issue_title}` 와 직접 관련 없는 확장 기능")
        lines.extend([
            "",
            "## MVP Acceptance Gates",
            "- [G1] 핵심 사용자 플로우 1개 이상이 end-to-end로 동작한다.",
            "- [G2] PRODUCT_REVIEW.json이 생성되고 필수 카테고리 점수가 기록된다.",
            "- [G3] 최소 1개 테스트 리포트가 생성된다.",
            "- [G4] 에러/빈 상태/로딩 상태 처리가 각각 1개 이상 구현된다.",
            "",
            "## Post-MVP Candidates",
            "- 성능 최적화, 리팩토링, 고급 UX polish",
            "- 추가 사용자 플로우, 확장 기능",
            "",
            "## Scope Decision Rationale",
            "- 최소 기능으로 빠른 검증 후 반복 개선하는 MVP 전략을 따른다.",
            "",
        ])
        mvp_scope_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _write_architecture_plan_fallback(
        job: JobRecord,
        paths: Dict[str, Path],
        architecture_plan_path: Path,
    ) -> None:
        del paths
        lines = [
            "# ARCHITECTURE PLAN",
            "",
            "## Context Anchor",
            f"- Job ID: {job.job_id}",
            f"- Issue Title: {job.issue_title}",
            "",
            "## Layer Structure",
            "- Product Definition Layer: PRODUCT_BRIEF.md / USER_FLOWS.md / MVP_SCOPE.md",
            "- Delivery Layer: PLAN.md / 구현 코드 / TEST_REPORT_*",
            "- Review Layer: REVIEW.md / PRODUCT_REVIEW.json",
            "- Improvement Loop Layer: REVIEW_HISTORY.json / IMPROVEMENT_BACKLOG.json / IMPROVEMENT_PLAN.md",
            "",
            "## Component Boundaries",
            "- Orchestrator: 단계 순서, 재시도 정책, 종료 조건 결정 (AI 사용 금지)",
            "- AI Workers: 프롬프트 입력 -> 산출물 파일 출력 (제어 로직 금지)",
            "- Store: 잡 상태, 단계, 에러 메시지 영속화",
            "",
            "## Data Contracts",
            "- 각 단계는 `_docs` 아래 파일(또는 JSON) 산출물을 남긴다.",
            "- 다음 단계는 직전 산출물을 입력으로 사용한다.",
            "- 실패 시 STATUS.md에 중단 원인과 재개 액션을 기록한다.",
            "",
            "## Quality Gates",
            "- 설계 산출물(brief/flows/mvp/architecture) 누락 시 구현 단계 진행 금지",
            "- PRODUCT_REVIEW overall < 3.0 이면 improvement_stage에서 전략 변경 검토",
            "",
            "## Loop Safety Rules",
            "- 동일 top issue 2회 이상 연속 → repeated_issue_limit_hit = True",
            "- 최근 3회 overall 변화폭 ≤ 0.15 → score_stagnation_detected = True",
            "- 직전 대비 overall 0.2 이상 하락 → quality_regression_detected = True",
            "- 위 3가지 중 1개라도 True → strategy_change_required = True (범위 축소 전략)",
            "- 복구 후보: git HEAD sha 기록, 품질 하락 시 롤백 검토",
            "",
            "## Technology Decisions",
            "- 웹: React/Nuxt 기반 프레임워크",
            "- API: FastAPI 기반",
            "- 모바일: React Native",
            "- AI 에이전트: Gemini(계획/리뷰) / Codex(구현/수정)",
            "",
            "## Extension Points",
            "- 새 단계: workflow_design.py SUPPORTED_NODE_TYPES에 타입 추가",
            "- 새 에이전트: config/ai_commands.json에 템플릿 추가",
            "- 새 평가 기준: _stage_product_review scores 딕셔너리에 카테고리 추가",
            "",
        ]
        architecture_plan_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _write_project_scaffolding_fallback(
        bootstrap_report: Dict[str, Any],
        scaffold_plan_path: Path,
    ) -> None:
        """Fallback writer for SCAFFOLD_PLAN.md."""

        repository_state = str(bootstrap_report.get("repository_state", "partial"))
        bootstrap_mode = str(bootstrap_report.get("bootstrap_mode", "stabilize"))
        stack = ", ".join(str(item) for item in bootstrap_report.get("detected_stack", [])) or "unknown"
        actions = bootstrap_report.get("recommended_actions", [])
        if not isinstance(actions, list):
            actions = []

        lines = [
            "# SCAFFOLD PLAN",
            "",
            "## Repository State",
            f"- Current state: `{repository_state}`",
            f"- Detected stack: `{stack}`",
            "",
            "## Bootstrap Mode",
            f"- Selected mode: `{bootstrap_mode}`",
            "- 목적: MVP 구현 전에 최소 실행 구조와 테스트/문서 뼈대를 정리한다.",
            "",
            "## Target Structure",
            "- 현재 스택에 맞는 엔트리포인트 파일을 유지 또는 생성한다.",
            "- 실행 설정 파일과 기본 테스트 경로를 고정한다.",
            "- 제품 문서와 구현 문서를 `_docs`와 루트 문서에서 연결한다.",
            "",
            "## Required Setup Commands",
            "- 의존성 설치 명령을 현재 스택 기준으로 정리한다.",
            "- 기본 실행 명령과 테스트 명령을 문서에 명시한다.",
            "- 신규 대규모 프레임워크 교체는 금지한다.",
            "",
            "## App Skeleton Contracts",
            "- entrypoint / config / test / docs의 최소 계약을 명시한다.",
            "- 기존 파일이 있으면 재사용하고 누락분만 보강한다.",
            "",
            "## Verification Checklist",
            "- [ ] 단일 실행 명령으로 프로젝트 기동 가능",
            "- [ ] 기본 테스트 명령 존재",
            "- [ ] README/개발 문서가 현재 구조를 설명",
            "- [ ] MVP 범위 밖 재구성 없이 시작 가능",
            "",
            "## Risks And Deferrals",
        ]
        if actions:
            lines.extend(f"- {str(item)}" for item in actions[:5])
        else:
            lines.append("- 현재 레포 상태를 기준으로 최소 scaffold만 적용한다.")
        scaffold_plan_path.write_text("\n".join(lines), encoding="utf-8")

    def _stage_product_review(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create PRODUCT_REVIEW.json and improvement backlog base."""

        self._set_stage(job.job_id, JobStage.PRODUCT_REVIEW, log_path)
        review_path = paths.get("review", self._docs_file(repository_path, "REVIEW.md"))
        review_text = self._read_text_file(review_path)
        todo_items = self._extract_review_todo_items(review_text)
        test_report_paths = sorted(repository_path.glob("TEST_REPORT_*.md"))
        test_failures = 0
        test_passes = 0
        for report in test_report_paths:
            text = self._read_text_file(report)
            if "Status: `PASS`" in text:
                test_passes += 1
            elif "Status: `FAIL`" in text:
                test_failures += 1

        architecture_exists = bool(self._read_text_file(paths.get("architecture_plan")))
        user_flows_exists = bool(self._read_text_file(paths.get("user_flows")))
        mvp_scope_exists = bool(self._read_text_file(paths.get("mvp_scope")))
        product_brief_exists = bool(self._read_text_file(paths.get("product_brief")))
        ux_review_text = self._read_text_file(self._docs_file(repository_path, "UX_REVIEW.md"))
        spec_text = self._read_text_file(paths.get("spec"))
        plan_text = self._read_text_file(paths.get("plan"))
        review_lower = review_text.lower()
        spec_lower = spec_text.lower()
        plan_lower = plan_text.lower()
        todo_penalty = min(3, len(todo_items) // 2)
        review_evidence = self._collect_product_review_evidence(
            repository_path=repository_path,
            paths=paths,
            spec_text=spec_text,
            plan_text=plan_text,
            review_text=review_text,
            ux_review_text=ux_review_text,
            test_report_paths=test_report_paths,
            todo_items=todo_items,
        )
        source_summary = review_evidence.get("source_summary", {})
        state_signals = review_evidence.get("state_signals", {})
        artifact_health = review_evidence.get("artifact_health", {})
        source_todo_count = int(source_summary.get("todo_markers", 0) or 0)
        source_file_count = int(source_summary.get("source_file_count", 0) or 0)
        test_file_count = int(source_summary.get("test_file_count", 0) or 0)
        readme_exists = bool(source_summary.get("readme_exists"))
        runtime_manifest_count = int(source_summary.get("runtime_manifest_count", 0) or 0)
        error_source_hits = int(state_signals.get("error", {}).get("source_hits", 0) or 0)
        error_doc_hits = int(state_signals.get("error", {}).get("doc_hits", 0) or 0)
        empty_source_hits = int(state_signals.get("empty", {}).get("source_hits", 0) or 0)
        empty_doc_hits = int(state_signals.get("empty", {}).get("doc_hits", 0) or 0)
        loading_source_hits = int(state_signals.get("loading", {}).get("source_hits", 0) or 0)
        loading_doc_hits = int(state_signals.get("loading", {}).get("doc_hits", 0) or 0)

        # ── 코드 품질 ────────────────────────────────────────────────────
        # TODO 수, REVIEW.md 내 버그/보안/크래시 언급 여부로 가중치 계산
        critical_keywords = ["bug", "보안", "security", "crash", "크래시", "취약", "취약점"]
        has_critical = any(kw in review_lower for kw in critical_keywords)
        code_quality_score = max(
            1,
            5 - todo_penalty - (1 if has_critical else 0) - min(1, source_todo_count // 3),
        )
        code_quality_reason = (
            f"TODO {len(todo_items)}개"
            + (", 크리티컬 이슈(버그/보안/크래시) 감지" if has_critical else "")
            + f", 소스 TODO/FIXME {source_todo_count}개"
            + f", 소스 파일 {source_file_count}개"
        )

        # ── 아키텍처 구조 ─────────────────────────────────────────────────
        # ARCHITECTURE_PLAN 존재 + 레이어/게이트/루프안전 섹션 포함 여부
        arch_text = self._read_text_file(paths.get("architecture_plan")).lower()
        arch_has_layers = "layer" in arch_text or "레이어" in arch_text
        arch_has_gates = "quality gate" in arch_text or "품질 게이트" in arch_text
        arch_has_loop_safety = "loop safety" in arch_text or "루프 안전" in arch_text or "loop_safety" in arch_text
        arch_bonus = sum([arch_has_layers, arch_has_gates, arch_has_loop_safety])
        architecture_score = min(5, (3 if architecture_exists else 1) + (arch_bonus if architecture_exists else 0))
        architecture_reason = (
            f"ARCHITECTURE_PLAN {'있음' if architecture_exists else '없음'}"
            + (f", 레이어{'O' if arch_has_layers else 'X'}"
               f"/게이트{'O' if arch_has_gates else 'X'}"
               f"/루프안전{'O' if arch_has_loop_safety else 'X'}")
        )

        # ── 유지보수성 ────────────────────────────────────────────────────
        # MVP_SCOPE + 비범위 명시 여부 + PRODUCT_BRIEF 존재 여부
        mvp_text = self._read_text_file(paths.get("mvp_scope")).lower()
        mvp_has_out_of_scope = "out of scope" in mvp_text or "비범위" in mvp_text or "out_of_scope" in mvp_text
        mvp_has_gates = "acceptance gate" in mvp_text or "완료 조건" in mvp_text
        maintainability_score = (
            (3 if mvp_scope_exists else 1)
            + (1 if mvp_has_out_of_scope else 0)
            + (1 if product_brief_exists else 0)
            + (1 if readme_exists else 0)
            + (1 if runtime_manifest_count > 0 else 0)
        )
        maintainability_score = min(5, maintainability_score)
        maintainability_reason = (
            f"MVP_SCOPE {'있음' if mvp_scope_exists else '없음'}"
            + (f", 비범위정의{'O' if mvp_has_out_of_scope else 'X'}"
               f", PRODUCT_BRIEF{'O' if product_brief_exists else 'X'}"
               f", 완료조건{'O' if mvp_has_gates else 'X'}"
               f", README{'O' if readme_exists else 'X'}"
               f", 런타임매니페스트{'O' if runtime_manifest_count > 0 else 'X'}")
        )

        # ── 사용성 ────────────────────────────────────────────────────────
        # USER_FLOWS 존재 + primary flow 단계 수 + 진입/종료 조건 명시
        flows_text = self._read_text_file(paths.get("user_flows")).lower()
        flows_has_primary = "primary flow" in flows_text or "primary" in flows_text
        flows_has_entry_exit = ("entry" in flows_text and "exit" in flows_text) or "진입" in flows_text
        usability_score = (
            (3 if user_flows_exists else 1)
            + (1 if flows_has_primary else 0)
            + (1 if flows_has_entry_exit else 0)
        )
        usability_score = min(5, usability_score)
        usability_reason = (
            f"USER_FLOWS {'있음' if user_flows_exists else '없음'}"
            + (f", primary flow{'O' if flows_has_primary else 'X'}"
               f", entry/exit{'O' if flows_has_entry_exit else 'X'}")
        )

        # ── UX 명확성 ─────────────────────────────────────────────────────
        # UX_REVIEW 존재 + 실패 없음 + UX state checklist 포함 여부
        ux_lower = ux_review_text.lower()
        ux_no_failure = bool(ux_review_text) and ("실패/누락 없음" in ux_review_text or "all pass" in ux_lower)
        ux_has_state_check = "loading" in ux_lower or "empty" in ux_lower or "로딩" in ux_lower
        ux_clarity_score = (
            (2 if ux_review_text else 1)
            + (2 if ux_no_failure else 0)
            + (1 if ux_has_state_check else 0)
        )
        ux_clarity_score = min(5, ux_clarity_score)
        ux_clarity_reason = (
            f"UX_REVIEW {'있음' if ux_review_text else '없음'}"
            + (f", 실패없음{'O' if ux_no_failure else 'X'}"
               f", 상태체크리스트{'O' if ux_has_state_check else 'X'}")
        )

        # ── 테스트 커버리지 ───────────────────────────────────────────────
        # 리포트 수, 실패 수, PLAN에 테스트 전략 포함 여부
        plan_has_test_strategy = "test strategy" in plan_lower or "테스트 전략" in plan_lower or "test_strategy" in plan_lower
        test_base = 3 if (test_report_paths or test_file_count > 0) else 1
        test_score = max(1, test_base - min(2, test_failures) + (1 if plan_has_test_strategy else 0))
        if test_file_count >= 2:
            test_score = min(5, test_score + 1)
        test_score = min(5, test_score)
        test_reason = (
            f"테스트 리포트 {len(test_report_paths)}개 (pass={test_passes}, fail={test_failures}), 테스트 파일 {test_file_count}개"
            + (", PLAN 테스트전략 있음" if plan_has_test_strategy else "")
        )

        # ── Error/Empty/Loading 상태 처리 ─────────────────────────────────
        # 각각 spec, review, plan에서 키워드 조합으로 점수 산출
        def _state_score(
            keywords_spec: List[str],
            keywords_review: List[str],
            keywords_plan: List[str],
            *,
            source_hits: int,
            doc_hits: int,
        ) -> int:
            spec_signal = int(any(k in spec_lower for k in keywords_spec))
            review_signal = int(any(k in review_lower for k in keywords_review))
            plan_signal = int(any(k in plan_lower for k in keywords_plan))
            ui_signal = int(source_hits > 0)
            doc_signal = int(doc_hits > 0)
            return min(
                5,
                max(1, 1 + spec_signal + review_signal + plan_signal + ui_signal + doc_signal),
            )

        error_score = _state_score(
            ["error", "오류", "에러", "exception"],
            ["오류", "error", "에러", "실패", "fail"],
            ["error handling", "에러 처리", "오류 처리"],
            source_hits=error_source_hits,
            doc_hits=error_doc_hits,
        )
        empty_score = _state_score(
            ["empty", "빈 상태", "데이터 없음"],
            ["빈 상태", "empty state", "empty"],
            ["empty state", "빈 상태 처리"],
            source_hits=empty_source_hits,
            doc_hits=empty_doc_hits,
        )
        loading_score = _state_score(
            ["loading", "로딩", "spinner"],
            ["로딩", "loading", "스피너"],
            ["loading state", "로딩 처리", "skeleton"],
            source_hits=loading_source_hits,
            doc_hits=loading_doc_hits,
        )

        scores = {
            "code_quality": code_quality_score,
            "architecture_structure": architecture_score,
            "maintainability": maintainability_score,
            "usability": usability_score,
            "ux_clarity": ux_clarity_score,
            "test_coverage": test_score,
            "error_state_handling": error_score,
            "empty_state_handling": empty_score,
            "loading_state_handling": loading_score,
        }
        overall = round(sum(scores.values()) / float(len(scores)), 2)

        score_reasons = {
            "code_quality": code_quality_reason,
            "architecture_structure": architecture_reason,
            "maintainability": maintainability_reason,
            "usability": usability_reason,
            "ux_clarity": ux_clarity_reason,
            "test_coverage": test_reason,
            "error_state_handling": f"에러 상태 점수: {error_score}/5 (source_hits={error_source_hits}, doc_hits={error_doc_hits})",
            "empty_state_handling": f"빈 상태 점수: {empty_score}/5 (source_hits={empty_source_hits}, doc_hits={empty_doc_hits})",
            "loading_state_handling": f"로딩 상태 점수: {loading_score}/5 (source_hits={loading_source_hits}, doc_hits={loading_doc_hits})",
        }
        category_evidence = {
            "code_quality": {
                "signals": ["review_todos", "critical_keywords", "source_todo_markers"],
                "metrics": {
                    "review_todo_count": len(todo_items),
                    "source_todo_markers": source_todo_count,
                    "critical_review_keywords": int(has_critical),
                    "source_file_count": source_file_count,
                },
            },
            "architecture_structure": {
                "signals": ["architecture_plan_sections"],
                "metrics": {
                    "architecture_plan_exists": int(architecture_exists),
                    "layer_section": int(arch_has_layers),
                    "quality_gate_section": int(arch_has_gates),
                    "loop_safety_section": int(arch_has_loop_safety),
                },
            },
            "maintainability": {
                "signals": ["mvp_scope_contract", "readme_presence", "runtime_manifest_presence"],
                "metrics": {
                    "mvp_scope_exists": int(mvp_scope_exists),
                    "out_of_scope_defined": int(mvp_has_out_of_scope),
                    "acceptance_gates_defined": int(mvp_has_gates),
                    "product_brief_exists": int(product_brief_exists),
                    "readme_exists": int(readme_exists),
                    "runtime_manifest_count": runtime_manifest_count,
                },
            },
            "usability": {
                "signals": ["user_flows_contract"],
                "metrics": {
                    "user_flows_exists": int(user_flows_exists),
                    "primary_flow_defined": int(flows_has_primary),
                    "entry_exit_defined": int(flows_has_entry_exit),
                },
            },
            "ux_clarity": {
                "signals": ["ux_review", "ux_state_checklist"],
                "metrics": {
                    "ux_review_exists": int(bool(ux_review_text)),
                    "ux_review_all_pass": int(ux_no_failure),
                    "ux_state_checklist": int(ux_has_state_check),
                },
            },
            "test_coverage": {
                "signals": ["test_reports", "test_files", "plan_test_strategy"],
                "metrics": {
                    "test_report_count": len(test_report_paths),
                    "test_passes_count": test_passes,
                    "test_failures_count": test_failures,
                    "test_file_count": test_file_count,
                    "plan_test_strategy": int(plan_has_test_strategy),
                },
            },
            "error_state_handling": state_signals.get("error", {}),
            "empty_state_handling": state_signals.get("empty", {}),
            "loading_state_handling": state_signals.get("loading", {}),
        }

        findings = [
            {
                "category": cat,
                "score": scores[cat],
                "max_score": 5,
                "summary": score_reasons[cat],
                "action_needed": scores[cat] <= 2,
                "evidence": category_evidence.get(cat, {}),
            }
            for cat in scores
        ]

        # ── 개선 후보 생성 ────────────────────────────────────────────────
        # TODO 항목 + 점수 ≤ 2 카테고리 → P1/P2 분류
        candidates: List[Dict[str, Any]] = []
        p1_keywords = ["bug", "fail", "error", "security", "crash", "보안", "크래시", "취약"]
        for item in todo_items:
            priority = "P1" if any(k in item.lower() for k in p1_keywords) else "P2"
            candidates.append({
                "id": self._stable_issue_id(item),
                "source": "review_todo",
                "title": item,
                "priority": priority,
                "reason": "REVIEW.md TODO 항목",
                "action": "REVIEW.md의 해당 TODO를 해소하는 코드 수정",
            })
        for category, score in scores.items():
            if score <= 2:
                action_map = {
                    "code_quality": "TODO 항목 해소 및 크리티컬 이슈 수정",
                    "architecture_structure": "ARCHITECTURE_PLAN.md에 레이어/게이트/루프안전 섹션 추가",
                    "maintainability": "MVP_SCOPE.md에 비범위 정의 및 완료 조건 보강",
                    "usability": "USER_FLOWS.md에 Primary Flow 및 진입/종료 조건 추가",
                    "ux_clarity": "UX_REVIEW.md 생성 또는 UX 상태 체크리스트 보강",
                    "test_coverage": "테스트 리포트 추가 및 PLAN에 테스트 전략 명시",
                    "error_state_handling": "에러 상태 UI 컴포넌트 및 메시지 구현",
                    "empty_state_handling": "빈 상태 UI 컴포넌트 및 안내 문구 구현",
                    "loading_state_handling": "로딩 스피너/스켈레톤 컴포넌트 구현",
                }
                candidates.append({
                    "id": self._stable_issue_id(category),
                    "source": "quality_score",
                    "title": f"{category} 점수 개선 (현재 {score}/5)",
                    "priority": "P1",
                    "reason": score_reasons[category],
                    "action": action_map.get(category, f"{category} 개선"),
                })
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            dedup[item["id"]] = item
        ordered_candidates = sorted(
            dedup.values(),
            key=lambda x: (0 if x.get("priority") == "P1" else 1, str(x.get("title", ""))),
        )
        priority_summary = {
            "P0": sum(1 for item in ordered_candidates if item.get("priority") == "P0"),
            "P1": sum(1 for item in ordered_candidates if item.get("priority") == "P1"),
            "P2": sum(1 for item in ordered_candidates if item.get("priority") == "P2"),
            "P3": sum(1 for item in ordered_candidates if item.get("priority") == "P3"),
        }
        recommended_next_tasks = [
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "priority": str(item.get("priority", "P2")),
                "reason": str(item.get("reason", "")),
                "action": str(item.get("action", "")),
            }
            for item in ordered_candidates[:5]
        ]
        quality_signals = {
            "todo_items_count": len(todo_items),
            "critical_issue_keywords_detected": has_critical,
            "test_report_count": len(test_report_paths),
            "test_failures_count": test_failures,
            "test_passes_count": test_passes,
            "has_product_brief": product_brief_exists,
            "has_user_flows": user_flows_exists,
            "has_mvp_scope": mvp_scope_exists,
            "has_architecture_plan": architecture_exists,
            "has_ux_review": bool(ux_review_text),
        }
        evidence_summary = {
            "source_file_count": source_file_count,
            "test_file_count": test_file_count,
            "runtime_manifest_count": runtime_manifest_count,
            "readme_exists": readme_exists,
            "generated_doc_count": int(artifact_health.get("docs", {}).get("generated_count", 0) or 0),
            "state_signal_totals": {
                "error": error_source_hits + error_doc_hits,
                "empty": empty_source_hits + empty_doc_hits,
                "loading": loading_source_hits + loading_doc_hits,
            },
        }
        principle_alignment = self._build_operating_principle_alignment(
            product_brief_exists=product_brief_exists,
            user_flows_exists=user_flows_exists,
            mvp_scope_exists=mvp_scope_exists,
            architecture_exists=architecture_exists,
            mvp_has_out_of_scope=mvp_has_out_of_scope,
            mvp_has_gates=mvp_has_gates,
            flows_has_primary=flows_has_primary,
            flows_has_entry_exit=flows_has_entry_exit,
            review_exists=bool(review_text),
            ux_review_exists=bool(ux_review_text),
            test_report_count=len(test_report_paths),
            todo_items_count=len(todo_items),
            priority_summary=priority_summary,
            candidate_count=len(ordered_candidates),
            scores=scores,
            overall=overall,
        )
        operating_policy = self._summarize_operating_policy(principle_alignment)

        payload = {
            "schema_version": "1.1",
            "generated_at": utc_now_iso(),
            "job_id": job.job_id,
            "review_basis": {
                "spec": str(paths.get("spec", "")),
                "plan": str(paths.get("plan", "")),
                "review": str(review_path),
                "product_brief": str(paths.get("product_brief", "")),
                "user_flows": str(paths.get("user_flows", "")),
                "mvp_scope": str(paths.get("mvp_scope", "")),
                "architecture_plan": str(paths.get("architecture_plan", "")),
            },
            "scores": {**scores, "overall": overall},
            "score_reasons": score_reasons,
            "findings": findings,
            "improvement_candidates": ordered_candidates,
            "priority_summary": priority_summary,
            "recommended_next_tasks": recommended_next_tasks,
            "quality_signals": quality_signals,
            "artifact_health": artifact_health,
            "category_evidence": category_evidence,
            "evidence_summary": evidence_summary,
            "principle_alignment": principle_alignment,
            "operating_policy": operating_policy,
            "quality_gate": {
                "passed": overall >= 3.0,
                "threshold": 3.0,
                "reason": "overall >= 3.0 (1~5 척도, 각 카테고리 키워드+문서 존재 기반)",
                "categories_below_threshold": [c for c, s in scores.items() if s <= 2],
            },
        }
        validation = self._validate_product_review_payload(payload)
        payload["validation"] = validation
        if not bool(validation.get("passed")):
            raise CommandExecutionError(
                "PRODUCT_REVIEW payload validation failed: "
                + "; ".join(str(item) for item in validation.get("errors", []))
            )
        product_review_path = paths.get("product_review", self._docs_file(repository_path, "PRODUCT_REVIEW.json"))
        product_review_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        review_history_path = paths.get("review_history", self._docs_file(repository_path, "REVIEW_HISTORY.json"))
        history_payload = self._read_json_file(review_history_path)
        history_entries = history_payload.get("entries", []) if isinstance(history_payload, dict) else []
        if not isinstance(history_entries, list):
            history_entries = []
        previous_level = ""
        if history_entries:
            previous_level = str(history_entries[-1].get("maturity_level", "")).strip()
        maturity_snapshot = self._build_repo_maturity_snapshot(
            job_id=job.job_id,
            scores=scores,
            overall=overall,
            artifact_health=artifact_health,
            quality_gate=payload["quality_gate"],
            principle_alignment=principle_alignment,
            previous_level=previous_level,
        )
        history_entries.append(
            {
                "generated_at": payload["generated_at"],
                "job_id": job.job_id,
                "overall": overall,
                "scores": dict(scores),
                "maturity_level": maturity_snapshot["level"],
                "maturity_score": maturity_snapshot["score"],
                "top_issue_ids": [item["id"] for item in ordered_candidates[:3]],
            }
        )
        # Persist fix triplet for cross-job memory learning.
        _prev_overall = history_entries[-2]["overall"] if len(history_entries) >= 2 else overall
        _score_delta = round(overall - _prev_overall, 3)
        _problem_text = "; ".join(item.get("id", "") for item in ordered_candidates[:3])
        _diff_text = str(priority_summary or findings or "")[:800]
        self._fix_store.upsert(
            job_id=job.job_id,
            problem=_problem_text,
            diff_summary=_diff_text,
            score_delta=_score_delta,
        )
        review_history_path.write_text(
            json.dumps({"entries": history_entries[-30:]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        trend_snapshot = self._build_quality_trend_snapshot(
            job_id=job.job_id,
            history_entries=history_entries[-30:],
            maturity_snapshot=maturity_snapshot,
        )
        repo_maturity_path = paths.get("repo_maturity", self._docs_file(repository_path, "REPO_MATURITY.json"))
        repo_maturity_path.write_text(
            json.dumps(maturity_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        quality_trend_path = paths.get("quality_trend", self._docs_file(repository_path, "QUALITY_TREND.json"))
        quality_trend_path.write_text(
            json.dumps(trend_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        backlog_path = paths.get("improvement_backlog", self._docs_file(repository_path, "IMPROVEMENT_BACKLOG.json"))
        backlog_path.write_text(
            json.dumps(
                {
                    "generated_at": payload["generated_at"],
                    "source_review": str(product_review_path),
                    "items": ordered_candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _stage_improvement_stage(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create next-loop improvement plan and loop guard signals."""

        self._set_stage(job.job_id, JobStage.IMPROVEMENT_STAGE, log_path)
        product_review_path = paths.get("product_review", self._docs_file(repository_path, "PRODUCT_REVIEW.json"))
        review_payload = self._read_json_file(product_review_path)
        review_history_path = paths.get("review_history", self._docs_file(repository_path, "REVIEW_HISTORY.json"))
        history_payload = self._read_json_file(review_history_path)
        history_entries = history_payload.get("entries", []) if isinstance(history_payload, dict) else []
        if not isinstance(history_entries, list):
            history_entries = []
        backlog_payload = self._read_json_file(paths.get("improvement_backlog"))
        backlog_items = backlog_payload.get("items", []) if isinstance(backlog_payload, dict) else []
        if not isinstance(backlog_items, list):
            backlog_items = []
        maturity_payload = self._read_json_file(paths.get("repo_maturity"))
        trend_payload = self._read_json_file(paths.get("quality_trend"))
        operating_policy = review_payload.get("operating_policy", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(operating_policy, dict):
            operating_policy = {}

        top_issue_id = str(backlog_items[0].get("id", "")) if backlog_items else ""
        recent_top_ids = [str(item.get("top_issue_ids", [""])[0]) for item in history_entries[-3:] if item.get("top_issue_ids")]
        repeated_issue_limit_hit = bool(top_issue_id) and recent_top_ids.count(top_issue_id) >= 2

        recent_scores = [float(item.get("overall", 0.0)) for item in history_entries[-3:] if item.get("overall") is not None]
        score_stagnation_detected = len(recent_scores) >= 3 and (max(recent_scores) - min(recent_scores) <= 0.15)
        quality_regression_detected = False
        if len(history_entries) >= 2:
            prev = float(history_entries[-2].get("overall", 0.0))
            current = float(history_entries[-1].get("overall", 0.0))
            quality_regression_detected = current < (prev - 0.2)
        design_reset_required = bool(operating_policy.get("requires_design_reset"))
        scope_reset_required = bool(operating_policy.get("requires_scope_reset"))
        quality_focus_required = bool(operating_policy.get("requires_quality_focus"))
        strategy_change_required = (
            repeated_issue_limit_hit
            or score_stagnation_detected
            or quality_regression_detected
            or design_reset_required
            or scope_reset_required
        )

        git_head = ""
        result = self._execute_shell_command(
            command=f"git -C {shlex.quote(str(repository_path))} rev-parse HEAD",
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "GIT"),
            check=False,
            command_purpose="read current git head",
        )
        if int(getattr(result, "exit_code", 1)) == 0:
            git_head = str(getattr(result, "stdout", "")).strip()

        loop_state = {
            "generated_at": utc_now_iso(),
            "same_issue_repeat_limit": 2,
            "repeated_issue_limit_hit": repeated_issue_limit_hit,
            "score_stagnation_detected": score_stagnation_detected,
            "quality_regression_detected": quality_regression_detected,
            "strategy_change_required": strategy_change_required,
            "principle_enforcement": {
                "blocked_principles": operating_policy.get("blocked_principles", []),
                "warning_principles": operating_policy.get("warning_principles", []),
                "requires_design_reset": design_reset_required,
                "requires_scope_reset": scope_reset_required,
                "requires_quality_focus": quality_focus_required,
            },
            "rollback": {
                "last_known_head": git_head,
                "rollback_candidate": bool(git_head),
            },
            "strategy": "normal_iterative_improvement",
        }
        loop_state_path = paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
        loop_state_path.write_text(
            json.dumps(loop_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        overall_score = float(review_payload.get("scores", {}).get("overall", 0.0)) if isinstance(review_payload, dict) else 0.0
        scores_payload = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores_payload, dict):
            scores_payload = {}
        artifact_health = review_payload.get("artifact_health", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(artifact_health, dict):
            artifact_health = {}
        categories_below = (
            review_payload.get("quality_gate", {}).get("categories_below_threshold", [])
            if isinstance(review_payload, dict)
            else []
        )
        if not isinstance(categories_below, list):
            categories_below = []
        quality_gate_payload = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate_payload, dict):
            quality_gate_payload = {}

        strategy_inputs = self._build_improvement_strategy_inputs(
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            categories_below=categories_below,
        )
        strategy_decision = self._select_improvement_strategy(
            overall_score=overall_score,
            strategy_inputs=strategy_inputs,
            repeated_issue_limit_hit=repeated_issue_limit_hit,
            score_stagnation_detected=score_stagnation_detected,
            quality_regression_detected=quality_regression_detected,
            design_reset_required=design_reset_required,
            scope_reset_required=scope_reset_required,
            quality_focus_required=quality_focus_required,
        )

        strategy = str(strategy_decision.get("strategy", "normal_iterative_improvement")).strip() or "normal_iterative_improvement"
        next_scope_restriction = str(strategy_decision.get("next_scope_restriction", "normal")).strip() or "normal"
        strategy_focus = str(strategy_decision.get("focus", "balanced")).strip() or "balanced"
        strategy_mode_shift = strategy != "normal_iterative_improvement"

        loop_state["strategy"] = strategy
        loop_state["strategy_focus"] = strategy_focus
        rollback_recommended = quality_regression_detected and bool(git_head)
        loop_state["next_scope_restriction"] = next_scope_restriction
        loop_state["rollback_recommended"] = rollback_recommended
        loop_state["categories_below_threshold"] = categories_below
        loop_state["overall_score"] = overall_score
        loop_state["strategy_inputs"] = strategy_inputs
        # 전략 변경 이유를 명시적으로 기록
        change_reasons: List[str] = []
        if repeated_issue_limit_hit:
            change_reasons.append(f"동일 이슈 {top_issue_id!r}가 최근 3회 내 2회 이상 반복됨")
        if score_stagnation_detected:
            scores_str = ", ".join(f"{s:.2f}" for s in recent_scores)
            change_reasons.append(f"최근 3회 점수 정체 감지 ({scores_str}) — 변화폭 ≤ 0.15")
        if quality_regression_detected and len(history_entries) >= 2:
            prev_s = float(history_entries[-2].get("overall", 0.0))
            curr_s = float(history_entries[-1].get("overall", 0.0))
            change_reasons.append(f"품질 하락 감지: {prev_s:.2f} → {curr_s:.2f} (0.2 이상 하락)")
        if design_reset_required:
            change_reasons.append("설계 선행 원칙 위반: 제품 정의/설계 문서를 다시 정렬해야 함")
        if scope_reset_required:
            change_reasons.append("MVP 우선/작은 단위 개발 원칙 위반: 범위 축소 또는 재정의 필요")
        if quality_focus_required:
            change_reasons.append("평가 우선/안정성 보호 원칙 기준에서 품질 근거가 부족함")
        for reason in strategy_decision.get("reasons", []):
            if reason not in change_reasons:
                change_reasons.append(reason)
        loop_state["strategy_change_reasons"] = change_reasons

        loop_state_path = paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
        loop_state_path.write_text(
            json.dumps(loop_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 전략 변경 시 P1만 처리하도록 backlog를 필터링
        if design_reset_required:
            next_items = [
                {
                    "id": "policy_design_rebaseline",
                    "priority": "P0",
                    "title": "제품 정의/설계 문서 재정렬",
                    "reason": "설계 선행 원칙 위반 또는 문서 계약 약화 감지",
                    "action": "PRODUCT_BRIEF/USER_FLOWS/MVP_SCOPE/ARCHITECTURE_PLAN을 재정리한 뒤 다시 계획 수립",
                }
            ]
        else:
            next_items = self._select_next_improvement_items(
                strategy=strategy,
                backlog_items=backlog_items,
                categories_below=categories_below,
                scores=scores_payload,
                artifact_health=artifact_health,
                quality_gate=quality_gate_payload,
            )
        next_tasks_payload = {
            "generated_at": utc_now_iso(),
            "strategy": loop_state.get("strategy", "normal_iterative_improvement"),
            "strategy_focus": strategy_focus,
            "scope_restriction": next_scope_restriction,
            "strategy_inputs": strategy_inputs,
            "tasks": [
                {
                    "task_id": f"next_{index + 1}",
                    "source_issue_id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "priority": str(item.get("priority", "P2")),
                    "reason": str(item.get("reason", "")),
                    "action": str(item.get("action", "")),
                    "selected_by_strategy": strategy,
                    "recommended_node_type": (
                        "gemini_plan"
                        if design_reset_required or scope_reset_required
                        else "coder_fix_from_test_report"
                        if str(item.get("priority", "P2")) in {"P0", "P1"}
                        else "gemini_plan"
                    ),
                }
                for index, item in enumerate(next_items)
            ],
        }
        next_tasks_path = paths.get(
            "next_improvement_tasks",
            self._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
        )
        next_tasks_path.write_text(
            json.dumps(next_tasks_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        plan_lines = [
            "# IMPROVEMENT PLAN",
            "",
            f"- Generated at: {loop_state['generated_at']}",
            f"- Strategy: `{loop_state['strategy']}`",
            f"- Current overall score: `{overall_score}`",
            f"- Next scope restriction: `{next_scope_restriction}`",
            "",
            "## Loop Guard Signals",
            f"- repeated_issue_limit_hit: `{repeated_issue_limit_hit}`",
            f"- score_stagnation_detected: `{score_stagnation_detected}`",
            f"- quality_regression_detected: `{quality_regression_detected}`",
            f"- strategy_change_required: `{strategy_change_required}`",
            f"- rollback_recommended: `{rollback_recommended}`",
            f"- strategy_focus: `{strategy_focus}`",
        ]
        plan_lines.extend([
            "",
            "## Strategy Inputs",
            f"- maturity_level: `{strategy_inputs.get('maturity_level', '')}`",
            f"- maturity_progression: `{strategy_inputs.get('maturity_progression', '')}`",
            f"- quality_trend_direction: `{strategy_inputs.get('quality_trend_direction', '')}`",
            f"- review_round_count: `{strategy_inputs.get('review_round_count', 0)}`",
            f"- quality_gate_passed: `{strategy_inputs.get('quality_gate_passed', False)}`",
            f"- persistent_low_categories: `{', '.join(strategy_inputs.get('persistent_low_categories', [])) or '-'}`",
            f"- stagnant_categories: `{', '.join(strategy_inputs.get('stagnant_categories', [])) or '-'}`",
        ])
        if change_reasons:
            plan_lines.extend(["", "## Strategy Change Reasons"])
            for reason in change_reasons:
                plan_lines.append(f"- {reason}")

        plan_lines.extend(["", "## Next Improvements"])
        if strategy_change_required or strategy_mode_shift:
            plan_lines.append("> **전략 변경 모드**: P1 항목만 처리합니다. 범위를 축소하고 안정화 작업을 우선 수행하세요.")
        for item in next_items:
            action = str(item.get("action", "")).strip()
            plan_lines.append(
                f"- [{item.get('priority', 'P2')}] {str(item.get('title', '')).strip()}"
                + (f"\n  - 원인: {item.get('reason', '')}" if item.get("reason") else "")
                + (f"\n  - 액션: {action}" if action else "")
            )
        if not next_items:
            plan_lines.append("- 개선 백로그 항목 없음 (품질 목표 달성)")

        if categories_below:
            plan_lines.extend(["", "## Categories Below Threshold (≤2/5)"])
            for cat in categories_below:
                plan_lines.append(f"- {cat}")

        plan_lines.extend([
            "",
            "## Recovery Option",
            f"- last_known_head: `{git_head or 'unavailable'}`",
            f"- next_tasks_file: `{next_tasks_path}`",
        ])
        if rollback_recommended:
            plan_lines.append(
                f"- **롤백 권장**: 품질 하락이 감지되었습니다. `git reset --hard {git_head}` 검토 후 P1 항목만 수정하세요."
            )
        else:
            plan_lines.append("- 전략 변경이 필요하면 범위를 축소하고 P1 항목부터 안정화 작업을 우선 수행한다.")
        plan_lines.append("")

        improvement_plan_path = paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
        improvement_plan_path.write_text("\n".join(plan_lines), encoding="utf-8")
        self._write_structured_memory_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
            review_payload=review_payload,
            maturity_payload=maturity_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
            next_tasks_payload=next_tasks_payload,
        )
        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        self._write_strategy_shadow_report(
            job=job,
            repository_path=repository_path,
            paths=paths,
            strategy_inputs=strategy_inputs,
            selected_strategy=strategy,
            selected_focus=strategy_focus,
        )
        self._ingest_memory_runtime_artifacts(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
        )
        self._append_actor_log(
            log_path, "ORCHESTRATOR",
            f"IMPROVEMENT_PLAN.md 생성 완료 — strategy={loop_state['strategy']}, "
            f"next_scope={next_scope_restriction}, rollback={rollback_recommended}",
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
        """Write Phase 2-B structured memory artifacts from review/improvement outputs."""

        memory_logging_enabled = self._feature_enabled("memory_logging")
        convention_extraction_enabled = self._feature_enabled("convention_extraction")
        memory_scoring_enabled = self._feature_enabled("memory_scoring")
        generated_at = str(loop_state.get("generated_at", "")).strip() or utc_now_iso()
        scores = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores, dict):
            scores = {}
        overall = float(scores.get("overall", 0.0) or 0.0)
        recommended_tasks = next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload, dict) else []
        if not isinstance(recommended_tasks, list):
            recommended_tasks = []
        categories_below = loop_state.get("categories_below_threshold", []) if isinstance(loop_state, dict) else []
        if not isinstance(categories_below, list):
            categories_below = []

        memory_log_path = paths.get("memory_log", self._docs_file(repository_path, "MEMORY_LOG.jsonl"))
        decision_history_path = paths.get("decision_history", self._docs_file(repository_path, "DECISION_HISTORY.json"))
        failure_patterns_path = paths.get("failure_patterns", self._docs_file(repository_path, "FAILURE_PATTERNS.json"))
        conventions_path = paths.get("conventions", self._docs_file(repository_path, "CONVENTIONS.json"))
        memory_feedback_path = paths.get("memory_feedback", self._docs_file(repository_path, "MEMORY_FEEDBACK.json"))
        memory_rankings_path = paths.get("memory_rankings", self._docs_file(repository_path, "MEMORY_RANKINGS.json"))

        base_payload = {
            "job_id": job.job_id,
            "app_code": job.app_code,
            "repository": job.repository,
            "execution_repository": self._job_execution_repository(job),
            "workflow_id": str(job.workflow_id or "").strip(),
            "issue_number": int(job.issue_number or 0),
            "issue_title": str(job.issue_title or "").strip(),
            "issue_url": str(job.issue_url or "").strip(),
            "generated_at": generated_at,
        }

        episodic_entry = {
            "memory_id": f"episodic_job_summary:{job.job_id}",
            "memory_type": "episodic",
            **base_payload,
            "signals": {
                "strategy": str(loop_state.get("strategy", "")).strip(),
                "strategy_focus": str(loop_state.get("strategy_focus", "")).strip(),
                "scope_restriction": str(loop_state.get("next_scope_restriction", "")).strip(),
                "overall": overall,
                "quality_trend_direction": str(trend_payload.get("trend_direction", "")).strip(),
                "delta_from_previous": float(trend_payload.get("delta_from_previous", 0.0) or 0.0),
                "maturity_level": str(maturity_payload.get("level", "")).strip(),
                "maturity_progression": str(maturity_payload.get("progression", "")).strip(),
                "persistent_low_categories": list(trend_payload.get("persistent_low_categories", []) or []),
                "stagnant_categories": list(trend_payload.get("stagnant_categories", []) or []),
                "categories_below_threshold": categories_below,
                "recovery_mode": "resume"
                if str(loop_state.get("next_scope_restriction", "")).strip() != "normal"
                else "normal",
            },
            "artifacts": {
                "product_review": str(paths.get("product_review", Path("_docs/PRODUCT_REVIEW.json"))),
                "review_history": str(paths.get("review_history", Path("_docs/REVIEW_HISTORY.json"))),
                "repo_maturity": str(paths.get("repo_maturity", Path("_docs/REPO_MATURITY.json"))),
                "quality_trend": str(paths.get("quality_trend", Path("_docs/QUALITY_TREND.json"))),
                "improvement_loop_state": str(paths.get("improvement_loop_state", Path("_docs/IMPROVEMENT_LOOP_STATE.json"))),
                "next_improvement_tasks": str(paths.get("next_improvement_tasks", Path("_docs/NEXT_IMPROVEMENT_TASKS.json"))),
            },
            "outcome": {
                "quality_gate_passed": bool(review_payload.get("quality_gate", {}).get("passed", False)),
                "task_count": len(recommended_tasks),
                "recommended_task_titles": [
                    str(item.get("title", "")).strip()
                    for item in recommended_tasks[:5]
                    if isinstance(item, dict) and str(item.get("title", "")).strip()
                ],
            },
        }
        decision_entry = {
            "decision_id": f"improvement_strategy:{job.job_id}",
            **base_payload,
            "decision_type": "improvement_strategy",
            "chosen_strategy": str(loop_state.get("strategy", "")).strip(),
            "strategy_focus": str(loop_state.get("strategy_focus", "")).strip(),
            "scope_restriction": str(loop_state.get("next_scope_restriction", "")).strip(),
            "trigger_signals": dict(loop_state.get("strategy_inputs", {}) or {}),
            "change_reasons": list(loop_state.get("strategy_change_reasons", []) or []),
            "selected_task_ids": [
                str(item.get("source_issue_id", "")).strip()
                for item in recommended_tasks
                if isinstance(item, dict) and str(item.get("source_issue_id", "")).strip()
            ],
            "selected_task_titles": [
                str(item.get("title", "")).strip()
                for item in recommended_tasks
                if isinstance(item, dict) and str(item.get("title", "")).strip()
            ],
        }

        if memory_logging_enabled:
            self._upsert_jsonl_entries(memory_log_path, [episodic_entry], key_field="memory_id")
            self._upsert_json_history_entries(
                decision_history_path,
                [decision_entry],
                key_field="decision_id",
                root_key="entries",
                max_entries=200,
            )
            self._update_failure_patterns_artifact(
                failure_patterns_path=failure_patterns_path,
                review_payload=review_payload,
                loop_state=loop_state,
                trend_payload=trend_payload,
                next_tasks_payload=next_tasks_payload,
                generated_at=generated_at,
            )
        if convention_extraction_enabled:
            self._write_conventions_artifact(
                repository_path=repository_path,
                conventions_path=conventions_path,
                job=job,
                generated_at=generated_at,
            )
        else:
            self._write_json_artifact(
                conventions_path,
                {"generated_at": generated_at, "enabled": False, "rules": []},
            )
        if memory_scoring_enabled:
            self._write_memory_quality_artifacts(
                job=job,
                paths=paths,
                review_payload=review_payload,
                trend_payload=trend_payload,
                loop_state=loop_state,
                generated_at=generated_at,
                current_memory_ids=[episodic_entry["memory_id"], decision_entry["decision_id"]],
                memory_feedback_path=memory_feedback_path,
                memory_rankings_path=memory_rankings_path,
            )
        else:
            self._write_json_artifact(
                memory_feedback_path,
                {"generated_at": generated_at, "enabled": False, "entries": []},
            )
            self._write_json_artifact(
                memory_rankings_path,
                {"generated_at": generated_at, "enabled": False, "items": []},
            )

    @staticmethod
    def _upsert_jsonl_entries(path: Path, entries: List[Dict[str, Any]], *, key_field: str) -> None:
        """Upsert deterministic records into a JSONL file while keeping append-only shape."""

        existing: List[Dict[str, Any]] = []
        if path.exists():
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    existing.append(payload)

        merged: Dict[str, Dict[str, Any]] = {}
        ordered_keys: List[str] = []
        for item in existing + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered_keys.append(item_id)
            merged[item_id] = item

        lines = [json.dumps(merged[item_id], ensure_ascii=False) for item_id in ordered_keys]
        path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    @staticmethod
    def _upsert_json_history_entries(
        path: Path,
        entries: List[Dict[str, Any]],
        *,
        key_field: str,
        root_key: str,
        max_entries: int,
    ) -> None:
        """Upsert deterministic history entries into one JSON document."""

        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(raw, dict):
                    payload = raw
            except json.JSONDecodeError:
                payload = {}

        current_entries = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(current_entries, list):
            current_entries = []
        merged: Dict[str, Dict[str, Any]] = {}
        ordered_keys: List[str] = []
        for item in current_entries + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered_keys.append(item_id)
            merged[item_id] = item
        if max_entries > 0 and len(ordered_keys) > max_entries:
            ordered_keys = ordered_keys[-max_entries:]
        payload[root_key] = [merged[item_id] for item_id in ordered_keys]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _write_json_artifact(path: Optional[Path], payload: Dict[str, Any]) -> None:
        """Persist one JSON artifact if path exists."""

        if path is None:
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        """Accumulate recurring failure/quality patterns in one structured file."""

        existing_payload = self._read_json_file(failure_patterns_path)
        current_items = existing_payload.get("items", []) if isinstance(existing_payload, dict) else []
        if not isinstance(current_items, list):
            current_items = []
        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_items:
            if not isinstance(item, dict):
                continue
            pattern_id = str(item.get("pattern_id", "")).strip()
            if pattern_id:
                merged[pattern_id] = item

        categories_below = review_payload.get("quality_gate", {}).get("categories_below_threshold", [])
        if not isinstance(categories_below, list):
            categories_below = []
        persistent_low = trend_payload.get("persistent_low_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(persistent_low, list):
            persistent_low = []
        stagnant = trend_payload.get("stagnant_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(stagnant, list):
            stagnant = []
        next_titles = [
            str(item.get("title", "")).strip()
            for item in (next_tasks_payload.get("tasks", []) if isinstance(next_tasks_payload, dict) else [])
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]

        pattern_candidates: List[Dict[str, Any]] = []
        for category in categories_below:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"low_category:{cat}",
                    "pattern_type": "low_category",
                    "category": cat,
                    "trigger": "quality_gate_below_threshold",
                    "recommended_actions": next_titles[:3],
                }
            )
        for category in persistent_low:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"persistent_low:{cat}",
                    "pattern_type": "persistent_low",
                    "category": cat,
                    "trigger": "trend_persistent_low",
                    "recommended_actions": next_titles[:3],
                }
            )
        for category in stagnant:
            cat = str(category).strip()
            if not cat:
                continue
            pattern_candidates.append(
                {
                    "pattern_id": f"stagnant:{cat}",
                    "pattern_type": "stagnant_category",
                    "category": cat,
                    "trigger": "trend_stagnation",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("repeated_issue_limit_hit")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:repeated_issue",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "repeated_issue_limit_hit",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("score_stagnation_detected")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:score_stagnation",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "score_stagnation_detected",
                    "recommended_actions": next_titles[:3],
                }
            )
        if bool(loop_state.get("quality_regression_detected")):
            pattern_candidates.append(
                {
                    "pattern_id": "loop_guard:quality_regression",
                    "pattern_type": "loop_guard",
                    "category": "",
                    "trigger": "quality_regression_detected",
                    "recommended_actions": next_titles[:3],
                }
            )

        for candidate in pattern_candidates:
            pattern_id = str(candidate.get("pattern_id", "")).strip()
            if not pattern_id:
                continue
            current = merged.get(
                pattern_id,
                {
                    "pattern_id": pattern_id,
                    "pattern_type": str(candidate.get("pattern_type", "")).strip(),
                    "category": str(candidate.get("category", "")).strip(),
                    "trigger": str(candidate.get("trigger", "")).strip(),
                    "count": 0,
                    "first_seen_at": generated_at,
                    "last_seen_at": generated_at,
                    "recommended_actions": [],
                },
            )
            current["count"] = int(current.get("count", 0) or 0) + 1
            current["last_seen_at"] = generated_at
            current["recommended_actions"] = list(candidate.get("recommended_actions", []) or [])
            merged[pattern_id] = current

        ordered_items = sorted(
            merged.values(),
            key=lambda item: (-int(item.get("count", 0) or 0), str(item.get("pattern_id", ""))),
        )
        failure_patterns_path.write_text(
            json.dumps({"generated_at": generated_at, "items": ordered_items[:100]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_conventions_artifact(
        self,
        *,
        repository_path: Path,
        conventions_path: Path,
        job: JobRecord,
        generated_at: str,
    ) -> None:
        """Write a richer write-only convention snapshot from repo structure and manifests."""

        rules: List[Dict[str, Any]] = []
        detected_stack: List[str] = []

        def add_rule(rule_id: str, convention_type: str, rule: str, evidence_paths: List[str], confidence: float) -> None:
            rules.append(
                {
                    "id": rule_id,
                    "type": convention_type,
                    "rule": rule,
                    "evidence_paths": evidence_paths,
                    "confidence": confidence,
                }
            )

        def add_stack(tag: str) -> None:
            normalized = str(tag or "").strip().lower()
            if normalized and normalized not in detected_stack:
                detected_stack.append(normalized)

        package_json = self._read_json_file(repository_path / "package.json")
        package_deps = self._package_dependency_map(package_json)
        pyproject_text = self._read_text_file(repository_path / "pyproject.toml")
        requirements_text = self._read_text_file(repository_path / "requirements.txt")

        if (repository_path / "tests").exists():
            add_rule("conv_tests_dir", "filesystem", "Tests live under tests/", ["tests"], 0.74)
        if (repository_path / "tests" / "e2e").exists():
            add_rule("conv_tests_e2e_dir", "testing", "End-to-end tests live under tests/e2e/", ["tests/e2e"], 0.8)
        if (repository_path / "src").exists():
            add_rule("conv_src_dir", "filesystem", "Primary source files live under src/", ["src"], 0.72)
        if (repository_path / "app").exists():
            add_rule("conv_app_dir", "filesystem", "Primary application code lives under app/", ["app"], 0.72)
        if (repository_path / "app" / "components").exists():
            add_rule(
                "conv_app_components",
                "ui_structure",
                "Reusable UI components live under app/components/",
                ["app/components"],
                0.82,
            )
        if (repository_path / "components").exists():
            add_rule(
                "conv_components_dir",
                "ui_structure",
                "Reusable UI components live under components/",
                ["components"],
                0.8,
            )
        if (repository_path / "src" / "components").exists():
            add_rule(
                "conv_src_components_dir",
                "ui_structure",
                "Reusable UI components live under src/components/",
                ["src/components"],
                0.82,
            )
        if (repository_path / "package.json").exists():
            add_rule("conv_node_runtime", "runtime", "Node package manifest is package.json", ["package.json"], 0.9)
            add_stack("node")
        if (repository_path / "pyproject.toml").exists():
            add_rule("conv_pyproject", "runtime", "Python project metadata is pyproject.toml", ["pyproject.toml"], 0.9)
            add_stack("python")
        elif (repository_path / "requirements.txt").exists():
            add_rule("conv_requirements", "runtime", "Python dependencies are managed with requirements.txt", ["requirements.txt"], 0.86)
            add_stack("python")
        if (repository_path / "README.md").exists():
            add_rule("conv_readme", "documentation", "Repository keeps top-level README.md", ["README.md"], 0.66)

        if package_deps:
            if "next" in package_deps:
                add_stack("nextjs")
                add_rule("conv_nextjs", "framework", "Frontend framework is Next.js", ["package.json"], 0.92)
            if "react" in package_deps:
                add_stack("react")
                add_rule("conv_react", "framework", "UI layer is based on React", ["package.json"], 0.9)
            if "react-native" in package_deps:
                add_stack("react-native")
                add_rule("conv_react_native", "framework", "App layer is based on React Native", ["package.json"], 0.92)
            if "tailwindcss" in package_deps:
                add_stack("tailwindcss")
                add_rule("conv_tailwindcss", "styling", "Styling uses Tailwind CSS utilities", ["package.json"], 0.9)
            if "framer-motion" in package_deps:
                add_stack("framer-motion")
                add_rule("conv_framer_motion", "animation", "Motion/animation uses framer-motion", ["package.json"], 0.88)
            if "lucide-react" in package_deps:
                add_stack("lucide-react")
                add_rule("conv_lucide_react", "icons", "Icons use lucide-react", ["package.json"], 0.88)
            if "@playwright/test" in package_deps or "playwright" in package_deps:
                add_stack("playwright")
                add_rule("conv_playwright", "testing", "Browser/E2E tests use Playwright", ["package.json"], 0.9)
            if "vitest" in package_deps:
                add_stack("vitest")
                add_rule("conv_vitest", "testing", "Unit/integration tests use Vitest", ["package.json"], 0.88)
            if "jest" in package_deps:
                add_stack("jest")
                add_rule("conv_jest", "testing", "Unit/integration tests use Jest", ["package.json"], 0.88)
            if "typescript" in package_deps or (repository_path / "tsconfig.json").exists():
                add_stack("typescript")
                add_rule("conv_typescript", "language", "Source is authored in TypeScript", ["package.json", "tsconfig.json"], 0.86)

        py_lower = pyproject_text.lower()
        req_lower = requirements_text.lower()
        if "fastapi" in py_lower or "fastapi" in req_lower:
            add_stack("fastapi")
            add_rule(
                "conv_fastapi",
                "framework",
                "Backend/API layer uses FastAPI",
                ["pyproject.toml" if pyproject_text else "requirements.txt"],
                0.9,
            )
        if "pytest" in py_lower or "pytest" in req_lower:
            add_stack("pytest")
            add_rule(
                "conv_pytest",
                "testing",
                "Python tests use pytest",
                ["pyproject.toml" if pyproject_text else "requirements.txt"],
                0.88,
            )

        if (repository_path / "app" / "layout.tsx").exists() or (repository_path / "app" / "page.tsx").exists():
            add_rule("conv_next_app_router", "routing", "Next.js app router uses app/ directory entrypoints", ["app/layout.tsx", "app/page.tsx"], 0.84)
        if (repository_path / "pages").exists():
            add_rule("conv_pages_router", "routing", "Page routes live under pages/", ["pages"], 0.78)

        component_extensions = self._detect_component_extension_preference(repository_path)
        if component_extensions["tsx"] > 0 and component_extensions["tsx"] >= component_extensions["jsx"]:
            add_rule(
                "conv_component_tsx",
                "language",
                "Component implementations prefer .tsx files",
                component_extensions["evidence_paths"][:3],
                0.76,
            )
        elif component_extensions["jsx"] > 0:
            add_rule(
                "conv_component_jsx",
                "language",
                "Component implementations prefer .jsx files",
                component_extensions["evidence_paths"][:3],
                0.72,
            )

        test_convention = self._detect_test_file_conventions(repository_path)
        if test_convention["python"] > 0:
            add_rule(
                "conv_pytest_file_pattern",
                "testing",
                "Python tests follow test_*.py naming under tests/",
                test_convention["python_paths"][:3],
                0.78,
            )
        if test_convention["js"] > 0:
            add_rule(
                "conv_js_test_pattern",
                "testing",
                "Frontend tests use *.test.* or *.spec.* naming",
                test_convention["js_paths"][:3],
                0.76,
            )

        payload = {
            "generated_at": generated_at,
            "job_id": job.job_id,
            "app_code": job.app_code,
            "repository": self._job_execution_repository(job),
            "detected_stack": sorted(detected_stack),
            "rules": sorted(rules, key=lambda item: str(item.get("id", ""))),
        }
        conventions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        """Write memory feedback history and aggregated rankings."""

        outcome = self._build_memory_feedback_outcome(
            review_payload=review_payload,
            trend_payload=trend_payload,
            loop_state=loop_state,
        )
        selection_payload = self._read_json_file(paths.get("memory_selection"))

        used_by_routes: Dict[str, List[str]] = {}
        for route_key in ("planner_context", "reviewer_context", "coder_context"):
            route_name = route_key.replace("_context", "")
            route_ids = selection_payload.get(route_key, []) if isinstance(selection_payload, dict) else []
            if not isinstance(route_ids, list):
                continue
            for raw_id in route_ids:
                memory_id = str(raw_id or "").strip()
                if not memory_id:
                    continue
                used_by_routes.setdefault(memory_id, [])
                if route_name not in used_by_routes[memory_id]:
                    used_by_routes[memory_id].append(route_name)

        for memory_id in current_memory_ids:
            normalized = str(memory_id or "").strip()
            if not normalized:
                continue
            used_by_routes.setdefault(normalized, [])
            if "generated" not in used_by_routes[normalized]:
                used_by_routes[normalized].append("generated")

        feedback_entries: List[Dict[str, Any]] = []
        for memory_id, routes in sorted(used_by_routes.items()):
            feedback_entries.append(
                {
                    "feedback_id": f"{memory_id}:{job.job_id}",
                    "memory_id": memory_id,
                    "memory_kind": self._memory_kind_from_id(memory_id),
                    "job_id": job.job_id,
                    "app_code": job.app_code,
                    "repository": self._job_execution_repository(job),
                    "generated_at": generated_at,
                    "routes": sorted(routes),
                    "verdict": outcome["verdict"],
                    "score_delta": outcome["score_delta"],
                    "evidence": outcome["evidence"],
                }
            )

        self._upsert_json_history_entries(
            memory_feedback_path,
            feedback_entries,
            key_field="feedback_id",
            root_key="entries",
            max_entries=800,
        )
        self._update_memory_rankings_artifact(
            memory_rankings_path=memory_rankings_path,
            feedback_entries=feedback_entries,
            generated_at=generated_at,
        )

    @staticmethod
    def _build_memory_feedback_outcome(
        *,
        review_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        loop_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Translate current run quality signals into one simple memory verdict."""

        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate, dict):
            quality_gate = {}

        delta = float(trend_payload.get("delta_from_previous", 0.0) or 0.0) if isinstance(trend_payload, dict) else 0.0
        regression = bool(loop_state.get("quality_regression_detected"))
        stagnation = bool(loop_state.get("score_stagnation_detected"))
        repeated = bool(loop_state.get("repeated_issue_limit_hit"))
        gate_passed = bool(quality_gate.get("passed", False))

        if regression or delta <= -0.2:
            verdict = "decay"
            score_delta = -2
        elif repeated:
            verdict = "decay"
            score_delta = -2
        elif stagnation:
            verdict = "decay"
            score_delta = -1
        elif gate_passed and delta >= 0.3:
            verdict = "promote"
            score_delta = 2
        elif delta > 0.0:
            verdict = "promote"
            score_delta = 1
        else:
            verdict = "keep"
            score_delta = 0

        return {
            "verdict": verdict,
            "score_delta": score_delta,
            "evidence": {
                "quality_gate_passed": gate_passed,
                "trend_direction": str(trend_payload.get("trend_direction", "")).strip()
                if isinstance(trend_payload, dict)
                else "",
                "delta_from_previous": delta,
                "quality_regression_detected": regression,
                "score_stagnation_detected": stagnation,
                "repeated_issue_limit_hit": repeated,
                "persistent_low_categories": list(trend_payload.get("persistent_low_categories", []) or [])
                if isinstance(trend_payload, dict)
                else [],
            },
        }

    def _update_memory_rankings_artifact(
        self,
        *,
        memory_rankings_path: Path,
        feedback_entries: List[Dict[str, Any]],
        generated_at: str,
    ) -> None:
        """Aggregate feedback history into durable memory rankings."""

        existing_payload = self._read_json_file(memory_rankings_path)
        current_items = existing_payload.get("items", []) if isinstance(existing_payload, dict) else []
        if not isinstance(current_items, list):
            current_items = []

        merged: Dict[str, Dict[str, Any]] = {}
        for item in current_items:
            if not isinstance(item, dict):
                continue
            memory_id = str(item.get("memory_id", "")).strip()
            if memory_id:
                merged[memory_id] = item

        for feedback in feedback_entries:
            memory_id = str(feedback.get("memory_id", "")).strip()
            if not memory_id:
                continue
            current = merged.get(
                memory_id,
                {
                    "memory_id": memory_id,
                    "memory_kind": str(feedback.get("memory_kind", "")).strip(),
                    "score": 0.0,
                    "usage_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                    "confidence": 0.5,
                    "state": "active",
                    "last_feedback_at": generated_at,
                },
            )
            score_delta = float(feedback.get("score_delta", 0.0) or 0.0)
            current["usage_count"] = int(current.get("usage_count", 0) or 0) + 1
            current["score"] = max(-6.0, min(6.0, float(current.get("score", 0.0) or 0.0) + score_delta))
            if score_delta > 0:
                current["positive_count"] = int(current.get("positive_count", 0) or 0) + 1
            elif score_delta < 0:
                current["negative_count"] = int(current.get("negative_count", 0) or 0) + 1
            else:
                current["neutral_count"] = int(current.get("neutral_count", 0) or 0) + 1
            current["last_feedback_at"] = generated_at
            current["last_routes"] = list(feedback.get("routes", []) or [])
            current["last_verdict"] = str(feedback.get("verdict", "")).strip()
            current["confidence"] = round(
                max(
                    0.05,
                    min(
                        0.98,
                        0.5
                        + float(current.get("score", 0.0) or 0.0) * 0.05
                        + int(current.get("positive_count", 0) or 0) * 0.02
                        - int(current.get("negative_count", 0) or 0) * 0.03,
                    ),
                ),
                3,
            )
            current["state"] = self._memory_ranking_state(
                score=float(current.get("score", 0.0) or 0.0),
                positive_count=int(current.get("positive_count", 0) or 0),
                negative_count=int(current.get("negative_count", 0) or 0),
            )
            merged[memory_id] = current

        ordered_items = sorted(
            merged.values(),
            key=lambda item: (
                str(item.get("state", "")) == "banned",
                -float(item.get("score", 0.0) or 0.0),
                -float(item.get("confidence", 0.0) or 0.0),
                -int(item.get("usage_count", 0) or 0),
                str(item.get("memory_id", "")),
            ),
        )
        memory_rankings_path.write_text(
            json.dumps({"generated_at": generated_at, "items": ordered_items[:400]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _memory_ranking_state(*, score: float, positive_count: int, negative_count: int) -> str:
        """Map aggregate score history to one compact ranking state."""

        if negative_count >= 3 and score <= -3.0:
            return "banned"
        if score >= 3.0 or positive_count >= 3:
            return "promoted"
        if score < 0.0:
            return "decayed"
        return "active"

    @staticmethod
    def _memory_kind_from_id(memory_id: str) -> str:
        """Infer one stable memory kind from stored identifier shape."""

        raw = str(memory_id or "").strip()
        if raw.startswith("episodic_"):
            return "episodic"
        if raw.startswith("improvement_strategy:"):
            return "decision"
        if raw.startswith("low_category:") or raw.startswith("persistent_low:") or raw.startswith("stagnant:") or raw.startswith("loop_guard:"):
            return "failure_pattern"
        if raw.startswith("conv_"):
            return "convention"
        return "unknown"

    @staticmethod
    def _package_dependency_map(package_json: Dict[str, Any]) -> Dict[str, str]:
        """Return merged dependency map from package.json payload."""

        merged: Dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            payload = package_json.get(section, {}) if isinstance(package_json, dict) else {}
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                merged[str(key)] = str(value)
        return merged

    @staticmethod
    def _detect_component_extension_preference(repository_path: Path) -> Dict[str, Any]:
        """Detect preferred component file extension under conventional component dirs."""

        candidate_dirs = [
            repository_path / "app" / "components",
            repository_path / "src" / "components",
            repository_path / "components",
        ]
        counts = {"tsx": 0, "jsx": 0, "evidence_paths": []}
        for candidate_dir in candidate_dirs:
            if not candidate_dir.exists():
                continue
            for pattern, key in (("*.tsx", "tsx"), ("*.jsx", "jsx")):
                for path in sorted(candidate_dir.rglob(pattern))[:10]:
                    counts[key] += 1
                    if len(counts["evidence_paths"]) < 6:
                        counts["evidence_paths"].append(str(path.relative_to(repository_path)))
        return counts

    @staticmethod
    def _detect_test_file_conventions(repository_path: Path) -> Dict[str, Any]:
        """Detect conventional Python/JS test file naming patterns."""

        python_paths = [str(path.relative_to(repository_path)) for path in sorted((repository_path / "tests").rglob("test_*.py"))[:6]] if (repository_path / "tests").exists() else []
        js_patterns = ["*.test.ts", "*.test.tsx", "*.test.js", "*.test.jsx", "*.spec.ts", "*.spec.tsx", "*.spec.js", "*.spec.jsx"]
        js_paths: List[str] = []
        for base_dir in [repository_path / "tests", repository_path / "src", repository_path / "app", repository_path]:
            if not base_dir.exists():
                continue
            for pattern in js_patterns:
                for path in sorted(base_dir.rglob(pattern)):
                    relative = str(path.relative_to(repository_path))
                    if relative not in js_paths:
                        js_paths.append(relative)
                    if len(js_paths) >= 6:
                        break
                if len(js_paths) >= 6:
                    break
            if len(js_paths) >= 6:
                break
        return {
            "python": len(python_paths),
            "python_paths": python_paths,
            "js": len(js_paths),
            "js_paths": js_paths,
        }

    def _write_memory_retrieval_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
    ) -> None:
        """Build route-specific memory selection/context files for prompt injection."""

        selection_path = paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))
        context_path = paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))
        trace_path = paths.get("memory_trace", self._docs_file(repository_path, "MEMORY_TRACE.json"))
        if not self._feature_enabled("memory_retrieval"):
            generated_at = utc_now_iso()
            self._write_json_artifact(
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
            self._write_json_artifact(
                context_path,
                {
                    "generated_at": generated_at,
                    "job_id": job.job_id,
                    "enabled": False,
                    "repository": self._job_execution_repository(job),
                    "planner_context": [],
                    "reviewer_context": [],
                    "coder_context": [],
                },
            )
            self._write_json_artifact(
                trace_path,
                {
                    "generated_at": generated_at,
                    "job_id": job.job_id,
                    "enabled": False,
                    "source": "disabled",
                    "fallback_used": False,
                    "repository": self._job_execution_repository(job),
                    "corpus_counts": {},
                    "selected_total": 0,
                    "selected_memory_ids": [],
                    "routes": {},
                },
            )
            return

        retrieval_corpus = self._load_memory_retrieval_corpus_from_db(job=job)
        source = "db"
        if retrieval_corpus is None:
            source = "file"
            retrieval_corpus = self._load_memory_retrieval_corpus_from_files(paths=paths)

        planner_context = self._build_route_memory_context(
            route="planner",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
        )
        reviewer_context = self._build_route_memory_context(
            route="reviewer",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
        )
        coder_context = self._build_route_memory_context(
            route="coder",
            memory_log_entries=retrieval_corpus["memory_log_entries"],
            decision_entries=retrieval_corpus["decision_entries"],
            failure_pattern_entries=retrieval_corpus["failure_pattern_entries"],
            convention_entries=retrieval_corpus["convention_entries"],
            rankings_map=retrieval_corpus["rankings_map"],
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
        }
        context_payload = {
            "generated_at": selection_payload["generated_at"],
            "job_id": job.job_id,
            "repository": self._job_execution_repository(job),
            "source": source,
            "planner_context": planner_context,
            "reviewer_context": reviewer_context,
            "coder_context": coder_context,
        }
        route_traces = {
            "planner": self._memory_route_trace_payload(planner_context),
            "reviewer": self._memory_route_trace_payload(reviewer_context),
            "coder": self._memory_route_trace_payload(coder_context),
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
            "repository": self._job_execution_repository(job),
            "corpus_counts": dict(selection_payload["corpus_counts"]),
            "selected_total": len(selected_memory_ids),
            "selected_memory_ids": selected_memory_ids,
            "routes": route_traces,
        }

        selection_path.write_text(json.dumps(selection_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        context_path.write_text(json.dumps(context_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        trace_path.write_text(json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_memory_retrieval_corpus_from_db(self, *, job: JobRecord) -> Optional[Dict[str, Any]]:
        """Return retrieval corpus from the canonical memory DB when available."""

        try:
            runtime_store = self._get_memory_runtime_store()
            runtime_store.refresh_rankings(as_of=utc_now_iso())
            runtime_entries = runtime_store.query_entries_for_retrieval(
                repository=job.repository,
                execution_repository=self._job_execution_repository(job),
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
            payload = self._memory_runtime_entry_payload(entry)
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
        }

    def _load_memory_retrieval_corpus_from_files(self, *, paths: Dict[str, Path]) -> Dict[str, Any]:
        """Return retrieval corpus from legacy file artifacts."""

        memory_log_entries = self._read_jsonl_entries(paths.get("memory_log"))
        decision_entries = self._read_json_history_entries(paths.get("decision_history"))
        failure_patterns_payload = self._read_json_file(paths.get("failure_patterns"))
        failure_pattern_entries = failure_patterns_payload.get("items", []) if isinstance(failure_patterns_payload, dict) else []
        if not isinstance(failure_pattern_entries, list):
            failure_pattern_entries = []
        conventions_payload = self._read_json_file(paths.get("conventions"))
        convention_entries = conventions_payload.get("rules", []) if isinstance(conventions_payload, dict) else []
        if not isinstance(convention_entries, list):
            convention_entries = []
        rankings_payload = self._read_json_file(paths.get("memory_rankings"))
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
        }

    @staticmethod
    def _memory_runtime_entry_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
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
    def _memory_route_trace_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                }
            )
        return {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
            "kind_counts": kind_counts,
            "selected_items": selected_items,
        }

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
        """Compute one memory-aware shadow strategy without affecting runtime behavior."""

        report_path = paths.get("strategy_shadow_report", self._docs_file(repository_path, "STRATEGY_SHADOW_REPORT.json"))
        if not self._feature_enabled("strategy_shadow"):
            self._write_json_artifact(
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

        context_payload = self._read_json_file(paths.get("memory_context"))
        rankings_payload = self._read_json_file(paths.get("memory_rankings"))
        ranking_entries = rankings_payload.get("items", []) if isinstance(rankings_payload, dict) else []
        if not isinstance(ranking_entries, list):
            ranking_entries = []
        rankings_map = {
            str(item.get("memory_id", "")).strip(): item
            for item in ranking_entries
            if isinstance(item, dict) and str(item.get("memory_id", "")).strip()
        }

        report_payload = self._build_strategy_shadow_report_payload(
            job=job,
            context_payload=context_payload if isinstance(context_payload, dict) else {},
            rankings_map=rankings_map,
            strategy_inputs=strategy_inputs,
            selected_strategy=selected_strategy,
            selected_focus=selected_focus,
        )
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _ingest_memory_runtime_artifacts(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Sync file-based memory artifacts into the canonical SQLite store."""

        try:
            sync_counts = ingest_memory_runtime_artifacts(
                self._get_memory_runtime_store(),
                job=job,
                execution_repository=self._job_execution_repository(job),
                paths=paths,
            )
        except Exception as exc:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Memory runtime ingest skipped: {exc}",
            )
            return

        if any(sync_counts.values()):
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Memory runtime ingest synced "
                f"(entries={sync_counts['entries']}, "
                f"feedback={sync_counts['feedback']}, "
                f"retrieval_runs={sync_counts['retrieval_runs']})",
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
                route_weight = self._strategy_shadow_route_weight(route_label)
                recommended = self._strategy_shadow_recommendations(item)
                if not recommended:
                    continue
                ranking = rankings_map.get(memory_id, {})
                weight_multiplier = self._strategy_shadow_ranking_weight(ranking)
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

        shadow_focus = self._strategy_focus_for_name(shadow_strategy)
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
            "repository": self._job_execution_repository(job),
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
    def _strategy_shadow_route_weight(route_name: str) -> float:
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
    def _strategy_shadow_ranking_weight(ranking: Dict[str, Any]) -> float:
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
    def _strategy_shadow_recommendations(item: Dict[str, Any]) -> List[Dict[str, str]]:
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
    def _strategy_focus_for_name(strategy: str) -> str:
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
        """Select compact top-k memory items for one route."""

        route_name = str(route or "").strip().lower()

        def ranking_state(memory_id: str) -> str:
            item = rankings_map.get(str(memory_id or "").strip(), {})
            return str(item.get("state", "active")).strip() or "active"

        def ranking_tuple(memory_id: str) -> Tuple[float, float, int]:
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
                selected.append(self._memory_log_context_entry(episodic_sorted[0]))
            if decision_sorted:
                selected.append(self._decision_context_entry(decision_sorted[0]))
            selected.extend(self._failure_pattern_context_entry(item) for item in pattern_sorted[:2])
            selected.extend(self._convention_context_entry(item) for item in convention_sorted[:2])
        elif route_name == "reviewer":
            if episodic_sorted:
                selected.append(self._memory_log_context_entry(episodic_sorted[0]))
            selected.extend(self._failure_pattern_context_entry(item) for item in pattern_sorted[:3])
            selected.extend(self._convention_context_entry(item) for item in convention_sorted[:2])
        else:  # coder / fixer
            if decision_sorted:
                selected.append(self._decision_context_entry(decision_sorted[0]))
            if episodic_sorted:
                selected.append(self._memory_log_context_entry(episodic_sorted[0]))
            selected.extend(self._failure_pattern_context_entry(item) for item in pattern_sorted[:2])
            selected.extend(self._convention_context_entry(item) for item in convention_sorted[:3])

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
    def _memory_log_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
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
    def _decision_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
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
    def _failure_pattern_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
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
    def _convention_context_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "convention",
            "id": str(entry.get("id", "")).strip(),
            "summary": str(entry.get("rule", "")).strip(),
            "type": str(entry.get("type", "")).strip(),
            "confidence": float(entry.get("confidence", 0.0) or 0.0),
            "evidence_paths": list(entry.get("evidence_paths", []) or [])[:3],
        }

    @staticmethod
    def _read_jsonl_entries(path: Optional[Path]) -> List[Dict[str, Any]]:
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

    @staticmethod
    def _read_json_history_entries(path: Optional[Path], *, root_key: str = "entries") -> List[Dict[str, Any]]:
        """Read one JSON history file with list entries."""

        payload = Orchestrator._read_json_file(path)
        entries = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            return []
        return [item for item in entries if isinstance(item, dict)]

    @staticmethod
    def _build_improvement_strategy_inputs(
        *,
        review_payload: Dict[str, Any],
        maturity_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        categories_below: List[str],
    ) -> Dict[str, Any]:
        """Collect strategy-selection inputs in one explicit structure."""

        scores = review_payload.get("scores", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(scores, dict):
            scores = {}
        artifact_health = review_payload.get("artifact_health", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(artifact_health, dict):
            artifact_health = {}
        tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}
        if not isinstance(tests_info, dict):
            tests_info = {}
        quality_gate = review_payload.get("quality_gate", {}) if isinstance(review_payload, dict) else {}
        if not isinstance(quality_gate, dict):
            quality_gate = {}
        persistent_low_categories = trend_payload.get("persistent_low_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(persistent_low_categories, list):
            persistent_low_categories = []
        stagnant_categories = trend_payload.get("stagnant_categories", []) if isinstance(trend_payload, dict) else []
        if not isinstance(stagnant_categories, list):
            stagnant_categories = []

        ux_categories = {
            "usability",
            "ux_clarity",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
        }
        engineering_categories = {"architecture_structure", "maintainability", "code_quality"}

        return {
            "maturity_level": str(maturity_payload.get("level", "bootstrap") or "bootstrap"),
            "maturity_progression": str(
                trend_payload.get("maturity_progression", maturity_payload.get("progression", "unchanged")) or "unchanged"
            ),
            "quality_trend_direction": str(trend_payload.get("trend_direction", "stable") or "stable"),
            "review_round_count": int(
                trend_payload.get("review_round_count", 0)
                or 0
            ),
            "quality_gate_passed": bool(quality_gate.get("passed")),
            "categories_below": list(categories_below),
            "persistent_low_categories": list(persistent_low_categories),
            "stagnant_categories": list(stagnant_categories),
            "has_test_gap": (
                "test_coverage" in categories_below
                or "test_coverage" in persistent_low_categories
                or "test_coverage" in stagnant_categories
                or int(tests_info.get("test_file_count", 0) or 0) == 0
                or int(tests_info.get("report_count", 0) or 0) == 0
            ),
            "has_ux_gap": any(
                category in ux_categories
                for category in [*categories_below, *persistent_low_categories, *stagnant_categories]
            ),
            "has_engineering_gap": any(
                category in engineering_categories
                for category in [*categories_below, *persistent_low_categories, *stagnant_categories]
            ),
            "overall_score": float(scores.get("overall", 0.0) or 0.0),
            "test_score": int(scores.get("test_coverage", 0) or 0),
            "ux_score_floor": min(
                int(scores.get("usability", 0) or 0),
                int(scores.get("ux_clarity", 0) or 0),
                int(scores.get("error_state_handling", 0) or 0),
                int(scores.get("empty_state_handling", 0) or 0),
                int(scores.get("loading_state_handling", 0) or 0),
            ),
        }

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
        """Choose next-loop strategy from maturity/trend/policy signals."""

        maturity_level = str(strategy_inputs.get("maturity_level", "bootstrap") or "bootstrap")
        maturity_progression = str(strategy_inputs.get("maturity_progression", "unchanged") or "unchanged")
        trend_direction = str(strategy_inputs.get("quality_trend_direction", "stable") or "stable")
        review_round_count = int(strategy_inputs.get("review_round_count", 0) or 0)
        quality_gate_passed = bool(strategy_inputs.get("quality_gate_passed"))
        has_test_gap = bool(strategy_inputs.get("has_test_gap"))
        has_ux_gap = bool(strategy_inputs.get("has_ux_gap"))
        has_engineering_gap = bool(strategy_inputs.get("has_engineering_gap"))
        categories_below = strategy_inputs.get("categories_below", [])
        if not isinstance(categories_below, list):
            categories_below = []
        persistent_low_categories = strategy_inputs.get("persistent_low_categories", [])
        if not isinstance(persistent_low_categories, list):
            persistent_low_categories = []
        stagnant_categories = strategy_inputs.get("stagnant_categories", [])
        if not isinstance(stagnant_categories, list):
            stagnant_categories = []

        reasons: List[str] = []

        if design_reset_required:
            return {
                "strategy": "design_rebaseline",
                "next_scope_restriction": "MVP_redefinition",
                "focus": "design",
                "reasons": ["제품 정의/설계 문서 재정렬이 우선입니다."],
            }
        if quality_regression_detected:
            return {
                "strategy": "rollback_or_stabilize",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": ["품질이 하락해 기능 확장보다 안정화와 복구가 우선입니다."],
            }
        if scope_reset_required:
            return {
                "strategy": "narrow_scope_stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "scope",
                "reasons": ["범위가 커졌기 때문에 MVP 범위 재정렬과 안정화가 필요합니다."],
            }

        if repeated_issue_limit_hit or score_stagnation_detected:
            if has_test_gap:
                reasons.append(
                    "반복/정체 구간에서 테스트 격차가 보입니다."
                    + (f" persistent_low={persistent_low_categories}" if "test_coverage" in persistent_low_categories else "")
                )
                return {
                    "strategy": "test_hardening",
                    "next_scope_restriction": "P1_only",
                    "focus": "testing",
                    "reasons": reasons,
                }
            if has_ux_gap:
                reasons.append("반복/정체 구간에서 UX 상태 처리 격차가 보여 화면 명확성 개선이 우선입니다.")
                return {
                    "strategy": "ux_clarity_improvement",
                    "next_scope_restriction": "P1_only",
                    "focus": "ux",
                    "reasons": reasons,
                }
            reasons.append("반복/정체 구간이므로 기능 확대보다 구조 안정화가 우선입니다.")
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": reasons,
            }

        if "test_coverage" in persistent_low_categories:
            return {
                "strategy": "test_hardening",
                "next_scope_restriction": "P1_only",
                "focus": "testing",
                "reasons": ["test_coverage가 최근 3라운드 연속 저점이라 테스트 강화가 우선입니다."],
            }

        if any(
            category in persistent_low_categories
            for category in {"ux_clarity", "usability", "error_state_handling", "empty_state_handling", "loading_state_handling"}
        ):
            return {
                "strategy": "ux_clarity_improvement",
                "next_scope_restriction": "P1_only",
                "focus": "ux",
                "reasons": [f"UX 관련 카테고리 저점이 지속됨: {', '.join(persistent_low_categories)}"],
            }

        if has_test_gap and (quality_focus_required or trend_direction in {"stable", "declining"} or review_round_count >= 2):
            return {
                "strategy": "test_hardening",
                "next_scope_restriction": "P1_only",
                "focus": "testing",
                "reasons": ["테스트/리포트 증거가 부족해 회귀 방지와 커버리지 보강이 우선입니다."],
            }

        if has_ux_gap:
            return {
                "strategy": "ux_clarity_improvement",
                "next_scope_restriction": "P1_only",
                "focus": "ux",
                "reasons": [f"UX 관련 저점 카테고리({', '.join(categories_below)})가 존재해 사용 흐름 명확화가 우선입니다."],
            }

        if quality_focus_required or overall_score < 3.0 or (maturity_level in {"bootstrap", "mvp"} and not quality_gate_passed):
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": ["현재 성숙도/품질 상태에서는 기능 확장보다 안정화가 우선입니다."],
            }

        if any(
            category in stagnant_categories
            for category in {"code_quality", "architecture_structure", "maintainability"}
        ) and trend_direction in {"stable", "declining"}:
            return {
                "strategy": "stabilization",
                "next_scope_restriction": "P1_only",
                "focus": "stability",
                "reasons": [f"엔지니어링 카테고리 정체가 지속됨: {', '.join(stagnant_categories)}"],
            }

        if (
            quality_gate_passed
            and overall_score >= 3.6
            and trend_direction == "improving"
            and maturity_level in {"usable", "stable", "product_grade"}
            and not categories_below
            and not has_engineering_gap
        ):
            return {
                "strategy": "feature_expansion",
                "next_scope_restriction": "normal",
                "focus": "feature",
                "reasons": ["품질 게이트를 통과했고 추세가 상승 중이므로 다음 핵심 사용자 가치를 확장할 수 있습니다."],
            }

        return {
            "strategy": "normal_iterative_improvement",
            "next_scope_restriction": "normal",
            "focus": "balanced",
            "reasons": [
                f"성숙도={maturity_level}, 추세={trend_direction}, progression={maturity_progression} 기준에서 균형 개선을 유지합니다."
            ],
        }

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
        """Choose strategy-aligned next tasks from backlog, with synthetic fallback."""

        if not isinstance(backlog_items, list):
            backlog_items = []
        if not isinstance(categories_below, list):
            categories_below = []

        def _priority_rank(value: str) -> int:
            return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(value or "P3"), 3)

        def _candidate_text(item: Dict[str, Any]) -> str:
            return " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("reason", "")),
                    str(item.get("action", "")),
                    str(item.get("source", "")),
                ]
            ).lower()

        def _pick_by_keywords(keywords: List[str], *, allowed_priorities: set[str], limit: int = 3) -> List[Dict[str, Any]]:
            scored: List[tuple[int, int, Dict[str, Any]]] = []
            for item in backlog_items:
                text = _candidate_text(item)
                match_count = sum(1 for keyword in keywords if keyword in text)
                if match_count <= 0:
                    continue
                priority = str(item.get("priority", "P3"))
                if priority not in allowed_priorities:
                    continue
                scored.append((-match_count, _priority_rank(priority), item))
            scored.sort(key=lambda row: (row[0], row[1], str(row[2].get("title", ""))))
            return [row[2] for row in scored[:limit]]

        if strategy == "feature_expansion":
            selected = [item for item in backlog_items if str(item.get("priority", "P3")) in {"P1", "P2"}][:3]
            if selected:
                return selected
            return [
                {
                    "id": "strategy_feature_expansion",
                    "priority": "P1",
                    "title": "다음 핵심 사용자 가치 1개 확장",
                    "reason": "품질 게이트 통과 및 추세 상승 상태에서 기능 확장을 진행합니다.",
                    "action": "MVP_SCOPE 기준에서 사용자 가치가 높은 기능 1개만 추가 구현하고 테스트를 함께 보강",
                }
            ]

        if strategy == "test_hardening":
            selected = _pick_by_keywords(
                ["test", "coverage", "regression", "spec", "e2e", "integration", "playwright", "pytest"],
                allowed_priorities={"P0", "P1", "P2"},
            )
            if selected:
                return selected
            tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}
            if not isinstance(tests_info, dict):
                tests_info = {}
            return [
                {
                    "id": "strategy_test_hardening",
                    "priority": "P1",
                    "title": "회귀 테스트 및 테스트 전략 보강",
                    "reason": (
                        f"test_file_count={int(tests_info.get('test_file_count', 0) or 0)}, "
                        f"report_count={int(tests_info.get('report_count', 0) or 0)}"
                    ),
                    "action": "핵심 사용자 흐름 기준 회귀 테스트를 추가하고 PLAN/리뷰 문서의 테스트 전략을 구체화",
                }
            ]

        if strategy == "ux_clarity_improvement":
            selected = _pick_by_keywords(
                ["ux", "usability", "empty", "loading", "error", "ui", "flow", "copy", "message", "spinner", "skeleton"],
                allowed_priorities={"P0", "P1", "P2"},
            )
            if selected:
                return selected
            return [
                {
                    "id": "strategy_ux_clarity_improvement",
                    "priority": "P1",
                    "title": "UX 상태 처리와 화면 안내 문구 정리",
                    "reason": f"낮은 UX 관련 카테고리: {', '.join(categories_below) or 'ux_clarity'}",
                    "action": "error/empty/loading 상태 UI와 안내 문구를 정리하고 USER_FLOWS 기준으로 사용자 흐름을 더 명확하게 다듬기",
                }
            ]

        if strategy in {"stabilization", "rollback_or_stabilize", "narrow_scope_stabilization"}:
            selected = [item for item in backlog_items if str(item.get("priority", "P3")) in {"P0", "P1"}][:3]
            if selected:
                return selected
            return [
                {
                    "id": "strategy_stabilization",
                    "priority": "P1",
                    "title": "구조 안정화 및 회귀 방지 작업",
                    "reason": "품질 게이트 미통과 또는 구조적 약점이 남아 있습니다.",
                    "action": "기능 확장 없이 현재 저점 카테고리를 보강하고 회귀 테스트를 추가",
                }
            ]

        if strategy == "normal_iterative_improvement":
            return backlog_items[:5]

        return backlog_items[:3]

    def _stage_plan_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self._set_stage(job.job_id, JobStage.PLAN_WITH_GEMINI, log_path)

        if not self._planner_graph_enabled():
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Planner graph MVP disabled by env. Using legacy one-shot planner.",
            )
            self._run_planner_legacy_one_shot(job, repository_path, paths, log_path, planning_mode=planning_mode)
            return

        try:
            self._run_planner_graph_mvp(job, repository_path, paths, log_path, planning_mode=planning_mode)
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Planner graph MVP failed. Fallback to legacy one-shot planner: {error}",
            )
            self._run_planner_legacy_one_shot(job, repository_path, paths, log_path, planning_mode=planning_mode)

    def _run_planner_legacy_one_shot(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        """Run original single-shot planner flow as safe fallback."""

        planner_prompt_path = self._docs_file(repository_path, "PLANNER_PROMPT.md")
        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        review_ready = paths["review"].exists() and bool(
            paths["review"].read_text(encoding="utf-8", errors="replace").strip()
        )
        planner_prompt_path.write_text(
            build_planner_prompt(
                str(paths["spec"]),
                str(paths["plan"]),
                review_path=str(paths["review"]),
                improvement_plan_path=str(paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))),
                improvement_loop_state_path=str(paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))),
                next_improvement_tasks_path=str(paths.get("next_improvement_tasks", self._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))),
                memory_selection_path=str(paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))),
                memory_context_path=str(paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                role_context=self._build_route_runtime_context("planner"),
                is_long_term=self._is_long_track(self._require_job(job.job_id)),
                is_refinement_round=review_ready,
                planning_mode=planning_mode,
            ),
            encoding="utf-8",
        )
        result = self.command_templates.run_template(
            template_name=self._template_for_route("planner"),
            variables=self._build_template_variables(job, paths, planner_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "PLANNER"),
        )
        if not paths["plan"].exists() and result.stdout.strip():
            paths["plan"].write_text(result.stdout, encoding="utf-8")
        if not paths["plan"].exists():
            raise CommandExecutionError(
                "Planner did not produce PLAN.md. Next action: ensure planner command "
                "writes to PLAN.md or emits plan content on stdout."
            )

    def _snapshot_plan_variant(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        planning_mode: str,
        log_path: Path,
    ) -> None:
        """Preserve plan snapshots so big-picture/dev planning are both traceable."""

        plan_path = paths.get("plan")
        if not isinstance(plan_path, Path) or not plan_path.exists():
            return
        mode = (planning_mode or "general").strip().lower()
        target_name = ""
        if mode == "big_picture":
            target_name = "PLAN_BIG.md"
        elif mode == "dev_planning":
            target_name = "PLAN_DEV.md"
        else:
            return
        target_path = self._docs_file(repository_path, target_name)
        target_path.write_text(
            plan_path.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Plan snapshot saved: {target_path.name}",
        )

    def _run_planner_graph_mvp(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        """Run planner through draft->quality-check->refine loop (graph-style MVP)."""

        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        review_ready = paths["review"].exists() and bool(
            paths["review"].read_text(encoding="utf-8", errors="replace").strip()
        )
        base_prompt = build_planner_prompt(
            str(paths["spec"]),
            str(paths["plan"]),
            review_path=str(paths["review"]),
            improvement_plan_path=str(paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))),
            improvement_loop_state_path=str(paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))),
            next_improvement_tasks_path=str(paths.get("next_improvement_tasks", self._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))),
            memory_selection_path=str(paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))),
            memory_context_path=str(paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))),
            role_context=self._build_route_runtime_context("planner"),
            is_long_term=self._is_long_track(self._require_job(job.job_id)),
            is_refinement_round=review_ready,
            planning_mode=planning_mode,
        )

        max_rounds = self._planner_graph_max_rounds()
        rounds: List[Dict[str, Any]] = []
        plan_quality_path = self._docs_file(repository_path, "PLAN_QUALITY.json")
        for round_index in range(1, max_rounds + 1):
            is_refine = round_index > 1
            prompt_path = (
                self._docs_file(
                    repository_path,
                    "PLANNER_PROMPT.md" if round_index == 1 else f"PLANNER_PROMPT_REFINE_{round_index}.md",
                )
            )
            prompt_text = base_prompt
            if is_refine and rounds:
                prompt_text += build_refinement_instruction(
                    round_index=round_index,
                    quality=rounds[-1].get("quality", {}),
                )
            tool_context_addendum = ""
            tool_request_count = 0
            max_tool_requests = 2
            while True:
                prompt_path.write_text(prompt_text + tool_context_addendum, encoding="utf-8")

                result = self.command_templates.run_template(
                    template_name=self._template_for_route("planner"),
                    variables=self._build_template_variables(job, paths, prompt_path),
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "PLANNER"),
                )
                if not paths["plan"].exists() and result.stdout.strip():
                    paths["plan"].write_text(result.stdout, encoding="utf-8")
                if not paths["plan"].exists():
                    raise CommandExecutionError(
                        "Planner did not produce PLAN.md in graph mode. "
                        "Next action: verify planner template writes PLAN.md."
                    )

                plan_text = paths["plan"].read_text(encoding="utf-8", errors="replace")
                tool_request = self._parse_planner_tool_request(plan_text)
                if not tool_request:
                    break
                if not self._route_allows_tool("planner", tool_request.tool):
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Planner requested disallowed tool '{tool_request.tool}'. Ignoring tool request.",
                    )
                    break
                if tool_request_count >= max_tool_requests:
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "Planner tool-request loop cap reached. Continuing without further search calls.",
                    )
                    break
                search_outcome = self._execute_planner_tool_request(
                    job=job,
                    repository_path=repository_path,
                    paths=paths,
                    log_path=log_path,
                    tool_request=tool_request,
                )
                tool_request_count += 1
                tool_context_addendum += self._build_planner_tool_context_addendum(
                    tool_request=tool_request,
                    outcome=search_outcome,
                )
                # Planner emitted tool request content. Clear it before re-run.
                paths["plan"].write_text("", encoding="utf-8")

            plan_text = paths["plan"].read_text(encoding="utf-8", errors="replace")
            quality = evaluate_plan_markdown(plan_text)
            rounds.append(
                {
                    "round": round_index,
                    "mode": "refine" if is_refine else "draft",
                    "tool_requests": tool_request_count,
                    "quality": quality,
                }
            )
            self._append_actor_log(
                log_path,
                "PLANNER",
                (
                    f"PlannerGraph round {round_index}/{max_rounds}: "
                    f"passed={quality.get('passed')} score={quality.get('score')} "
                    f"missing={','.join(quality.get('missing_sections', [])) or '-'}"
                ),
            )
            if quality.get("passed"):
                break

        final_quality = rounds[-1]["quality"] if rounds else {"passed": False, "score": 0}
        plan_quality_path.write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "issue_number": job.issue_number,
                    "max_rounds": max_rounds,
                    "rounds": rounds,
                    "final": final_quality,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        self._append_actor_log(
            log_path,
            "PLANNER",
            (
                "PlannerGraph final quality: "
                f"passed={final_quality.get('passed')} score={final_quality.get('score')}"
            ),
        )
        if not bool(final_quality.get("passed")):
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "PLAN quality gate not passed, but continuing by non-blocking assist policy.",
            )

    @staticmethod
    def _planner_graph_max_rounds() -> int:
        """Read planner graph round cap from env with safe defaults."""

        raw = (os.getenv("AGENTHUB_PLANNER_GRAPH_MAX_ROUNDS", "3") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 3
        return max(1, min(5, value))

    @staticmethod
    def _planner_graph_enabled() -> bool:
        """Enable/disable planner graph MVP by env."""

        raw = (os.getenv("AGENTHUB_PLANNER_GRAPH_ENABLED", "true") or "").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _parse_planner_tool_request(plan_text: str) -> Optional[ToolRequest]:
        """Parse planner TOOL_REQUEST block from PLAN output."""

        return ToolRuntime.parse_planner_tool_request(plan_text)

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
        """Create fallback evidence payload when external search is unavailable."""

        repo_context = repo_context_reader(repository_path)
        spec_excerpt = ""
        spec_path = paths.get("spec")
        if spec_path and Path(spec_path).exists():
            spec_excerpt = Path(spec_path).read_text(encoding="utf-8", errors="replace")
            spec_excerpt = "\n".join(spec_excerpt.splitlines()[:80]).strip()

        readme_excerpt = str(repo_context.get("readme_excerpt", "")).strip()
        stack = ", ".join(repo_context.get("stack", []) or [])
        context_text = (
            "# SEARCH CONTEXT (Fallback Local Evidence)\n\n"
            f"- query: {query}\n"
            "- mode: fallback_local\n"
            f"- reason: external_search_unavailable ({error_text[:400]})\n"
            f"- detected_stack: {stack or '(none)'}\n\n"
            "## SPEC excerpt\n\n"
            f"{spec_excerpt or '(SPEC excerpt unavailable)'}\n\n"
            "## README excerpt\n\n"
            f"{readme_excerpt or '(README excerpt unavailable)'}\n"
        ).strip() + "\n"
        return {"context_text": context_text}

    @staticmethod
    def _build_planner_tool_context_addendum(
        *,
        tool_request: ToolRequest,
        outcome: Dict[str, Any],
    ) -> str:
        """Build addendum prompt after tool execution."""

        return ToolRuntime.build_planner_tool_context_addendum(
            request=tool_request,
            result=ToolResult(
                ok=bool(outcome.get("ok")),
                mode=str(outcome.get("mode", "unknown")),
                context_path=str(outcome.get("context_path", "SEARCH_CONTEXT.md")),
                result_path=str(outcome.get("result_path", "SEARCH_RESULT.json")),
                context_text=str(outcome.get("context_text", "")).strip(),
                error=str(outcome.get("error", "")).strip(),
            ),
        )

    def _stage_implement_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)
        self._ensure_product_definition_ready(paths, log_path)
        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

        coder_prompt_path = self._docs_file(repository_path, "CODER_PROMPT_IMPLEMENT.md")
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal="PLAN.md 기반 MVP 구현",
                design_path=str(paths.get("design", "")),
                design_tokens_path=str(paths.get("design_tokens", self._docs_file(repository_path, "DESIGN_TOKENS.json"))),
                token_handoff_path=str(paths.get("token_handoff", self._docs_file(repository_path, "TOKEN_HANDOFF.md"))),
                publish_handoff_path=str(paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))),
                improvement_plan_path=str(paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))),
                improvement_loop_state_path=str(paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))),
                next_improvement_tasks_path=str(paths.get("next_improvement_tasks", self._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))),
                memory_selection_path=str(paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))),
                memory_context_path=str(paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                role_context=self._build_route_runtime_context("coder"),
            ),
            encoding="utf-8",
        )

        self.command_templates.run_template(
            template_name=self._template_for_route("coder"),
            variables=self._build_template_variables(job, paths, coder_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "CODER"),
        )

    def _stage_design_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.DESIGN_WITH_CODEX, log_path)

        designer_prompt_path = self._docs_file(repository_path, "DESIGNER_PROMPT.md")
        designer_prompt_path.write_text(
            build_designer_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
            ),
            encoding="utf-8",
        )

        result = self.command_templates.run_template(
            template_name=self._template_for_route("designer"),
            variables=self._build_template_variables(job, paths, designer_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "DESIGNER"),
        )

        if not paths["design"].exists() and result.stdout.strip():
            paths["design"].write_text(result.stdout, encoding="utf-8")

        if not paths["design"].exists():
            raise CommandExecutionError(
                "Designer did not produce DESIGN_SYSTEM.md. Next action: ensure designer command "
                "writes to DESIGN_SYSTEM.md or emits markdown on stdout."
            )
        self._ensure_design_artifacts(repository_path, paths, log_path)

    def _stage_publish_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Run publisher-specific codex step and enforce handoff artifacts."""

        self._set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)
        self._ensure_product_definition_ready(paths, log_path)
        prompt_path = self._docs_file(repository_path, "CODER_PROMPT_PUBLISH.md")
        prompt_path.write_text(
            build_publisher_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
                publish_checklist_path=str(paths.get("publish_checklist", self._docs_file(repository_path, "PUBLISH_CHECKLIST.md"))),
                publish_handoff_path=str(paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))),
            ),
            encoding="utf-8",
        )
        self.command_templates.run_template(
            template_name=self._template_for_route("publisher"),
            variables=self._build_template_variables(job, paths, prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "PUBLISHER"),
        )
        self._ensure_publisher_artifacts(repository_path, paths, log_path)

    def _stage_copywriter_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Run copywriter step and produce customer-facing Korean copy docs."""

        self._set_stage(job.job_id, JobStage.COPYWRITER_TASK, log_path)
        prompt_path = self._docs_file(repository_path, "CODER_PROMPT_COPYWRITER.md")
        prompt_path.write_text(
            build_copywriter_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
                publish_handoff_path=str(paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))),
                copy_plan_path=str(paths.get("copy_plan", self._docs_file(repository_path, "COPYWRITING_PLAN.md"))),
                copy_deck_path=str(paths.get("copy_deck", self._docs_file(repository_path, "COPY_DECK.md"))),
            ),
            encoding="utf-8",
        )
        self.command_templates.run_template(
            template_name=self._template_for_route("copywriter"),
            variables=self._build_template_variables(job, paths, prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "COPYWRITER"),
        )
        self._ensure_copywriter_artifacts(repository_path, paths, log_path)

    def _stage_documentation_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Run documentation stage with configured documentation route, then coder fallback."""

        self._set_stage(job.job_id, JobStage.DOCUMENTATION_TASK, log_path)
        prompt_path = self._docs_file(repository_path, "DOCUMENTATION_PROMPT.md")
        bundle_path = self._docs_file(repository_path, "DOCUMENTATION_BUNDLE.md")
        prompt_path.write_text(
            build_documentation_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                readme_path=str(paths.get("readme", repository_path / "README.md")),
                copyright_path=str(paths.get("copyright", repository_path / "COPYRIGHT.md")),
                development_guide_path=str(
                    paths.get("development_guide", repository_path / "DEVELOPMENT_GUIDE.md")
                ),
                documentation_plan_path=str(
                    paths.get("documentation_plan", self._docs_file(repository_path, "DOCUMENTATION_PLAN.md"))
                ),
            ),
            encoding="utf-8",
        )

        route_error: Optional[str] = None
        bundle_applied = False
        for resolved_template in self._template_candidates_for_route("documentation"):
            if not self.command_templates.has_template(resolved_template):
                continue
            route_vars = {
                **self._build_template_variables(job, paths, prompt_path),
                "docs_bundle_path": str(bundle_path),
                "pr_summary_path": str(bundle_path),
                "commit_message_path": str(bundle_path),
            }
            try:
                result = self.command_templates.run_template(
                    template_name=resolved_template,
                    variables=route_vars,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "TECH_WRITER"),
                )
                if not bundle_path.exists() and str(result.stdout).strip():
                    bundle_path.write_text(str(result.stdout).strip() + "\n", encoding="utf-8")
                bundle_applied = self._apply_documentation_bundle(repository_path, bundle_path, paths, log_path)
                if bundle_applied:
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Documentation generated by route template: {resolved_template}",
                    )
                    break
            except CommandExecutionError as error:
                route_error = str(error)

        if not bundle_applied:
            if route_error:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Documentation route failed. Fallback to coder route: {route_error}",
                )
            else:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Documentation route unavailable or output invalid. Fallback to coder route.",
                )
            fallback_prompt = self._docs_file(repository_path, "CODER_PROMPT_DOCUMENTATION_FALLBACK.md")
            fallback_prompt.write_text(
                (
                    "Goal: 루트 기술 문서 3종과 문서 계획 파일을 최신화하세요.\n\n"
                    f"- {paths.get('readme', repository_path / 'README.md')}\n"
                    f"- {paths.get('copyright', repository_path / 'COPYRIGHT.md')}\n"
                    f"- {paths.get('development_guide', repository_path / 'DEVELOPMENT_GUIDE.md')}\n"
                    f"- {paths.get('documentation_plan', self._docs_file(repository_path, 'DOCUMENTATION_PLAN.md'))}\n\n"
                    "규칙:\n"
                    "- 한국어로 작성.\n"
                    "- 프로젝트 구조/실행/테스트/운영 플로우를 반영.\n"
                    "- 문서만 수정하고 불필요한 코드 변경 금지.\n"
                ),
                encoding="utf-8",
            )
            self.command_templates.run_template(
                template_name=self._template_for_route("coder"),
                variables=self._build_template_variables(job, paths, fallback_prompt),
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "TECH_WRITER_CODEX"),
            )

        self._ensure_documentation_artifacts(repository_path, paths, log_path)

    def _apply_documentation_bundle(
        self,
        repository_path: Path,
        bundle_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> bool:
        """Parse Claude bundle output and write each target documentation file."""

        if not bundle_path.exists():
            return False
        raw = bundle_path.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(
            r"(?ms)^<<<FILE:(?P<path>[^\n>]+)>>>\n(?P<body>.*?)(?=^<<<FILE:|\Z)"
        )
        matches = list(pattern.finditer(raw))
        if not matches:
            return False

        allowed_targets = {
            "README.md": paths.get("readme", repository_path / "README.md"),
            "COPYRIGHT.md": paths.get("copyright", repository_path / "COPYRIGHT.md"),
            "DEVELOPMENT_GUIDE.md": paths.get("development_guide", repository_path / "DEVELOPMENT_GUIDE.md"),
            "_docs/DOCUMENTATION_PLAN.md": paths.get(
                "documentation_plan", self._docs_file(repository_path, "DOCUMENTATION_PLAN.md")
            ),
        }
        written_count = 0
        for matched in matches:
            key = str(matched.group("path") or "").strip()
            target = allowed_targets.get(key)
            if target is None:
                continue
            body = str(matched.group("body") or "").strip()
            if not body:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + "\n", encoding="utf-8")
            written_count += 1
        if written_count > 0:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Documentation bundle applied: {written_count} file(s)",
            )
        return written_count > 0

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
        """Resolve app_type from SPEC.json with safe fallback."""

        spec_json_path = paths.get("spec_json", self._docs_file(repository_path, "SPEC.json"))
        if isinstance(spec_json_path, Path) and spec_json_path.exists():
            try:
                payload = json.loads(spec_json_path.read_text(encoding="utf-8"))
                value = str(payload.get("app_type", "")).strip().lower()
                if value in {"web", "api", "cli", "app"}:
                    return value
            except Exception:  # noqa: BLE001
                pass
        return "web"

    def _stage_skip_ux_review_for_non_web(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        *,
        app_type: str,
    ) -> None:
        """Write skip record when UX E2E stage is not applicable."""

        self._set_stage(job.job_id, JobStage.UX_E2E_REVIEW, log_path)
        review_path = self._docs_file(repository_path, "UX_REVIEW.md")
        review_path.write_text(
            (
                "# UX REVIEW\n\n"
                "## Summary\n"
                f"- Stage: `{JobStage.UX_E2E_REVIEW.value}`\n"
                "- Verdict: `SKIPPED`\n"
                f"- Reason: `non-web app_type ({app_type})`\n\n"
                "## Next Action\n"
                "- non-web 타입은 UX 스크린샷 E2E를 수행하지 않습니다.\n"
                "- API/CLI 전용 검증 결과를 우선 확인하세요.\n"
            ),
            encoding="utf-8",
        )
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"ux_e2e_review skipped for app_type={app_type}",
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
        """Run UX-focused E2E checks with PC/mobile screenshots and summary markdown."""

        tests_passed = self._stage_run_tests(
            job=job,
            repository_path=repository_path,
            stage=JobStage.UX_E2E_REVIEW,
            log_path=log_path,
        )
        preview_info = self._deploy_preview_and_smoke_test(job, repository_path, log_path)
        screenshot_info = self._capture_ux_screenshots(
            repository_path=repository_path,
            preview_info=preview_info,
            log_path=log_path,
        )
        self._write_ux_review_markdown(
            repository_path=repository_path,
            spec_path=paths.get("spec"),
            preview_info=preview_info,
            screenshot_info=screenshot_info,
            tests_passed=tests_passed,
        )

    def _capture_ux_screenshots(
        self,
        repository_path: Path,
        preview_info: Dict[str, str],
        log_path: Path,
    ) -> Dict[str, Dict[str, str]]:
        """Capture desktop/mobile screenshots against preview URL."""

        artifacts_dir = repository_path / "artifacts" / "ux"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        screenshot_url = str(preview_info.get("local_url", "")).strip() or str(
            preview_info.get("external_url", "")
        ).strip()

        results: Dict[str, Dict[str, str]] = {
            "pc": {"status": "skipped", "path": "artifacts/ux/pc.png", "note": "preview unavailable"},
            "mobile": {"status": "skipped", "path": "artifacts/ux/mobile.png", "note": "preview unavailable"},
        }
        if not screenshot_url:
            return results

        targets = [
            ("pc", "Desktop Chrome", artifacts_dir / "pc.png"),
            ("mobile", "iPhone 13", artifacts_dir / "mobile.png"),
        ]
        for key, device, target_path in targets:
            command = (
                "npx -y playwright screenshot "
                f"--device={shlex.quote(device)} "
                f"{shlex.quote(screenshot_url)} "
                f"{shlex.quote(str(target_path))}"
            )
            try:
                self._run_shell(
                    command=command,
                    cwd=repository_path,
                    log_path=log_path,
                    purpose=f"ux screenshot capture ({key})",
                )
                results[key] = {
                    "status": "captured",
                    "path": str(target_path.relative_to(repository_path)),
                    "note": f"{device} capture completed",
                }
            except CommandExecutionError as error:
                results[key] = {
                    "status": "failed",
                    "path": str(target_path.relative_to(repository_path)),
                    "note": str(error),
                }
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"UX screenshot capture failed ({key}): {error}",
                )
        return results

    def _write_ux_review_markdown(
        self,
        repository_path: Path,
        spec_path: Optional[Path],
        preview_info: Dict[str, str],
        screenshot_info: Dict[str, Dict[str, str]],
        tests_passed: bool,
    ) -> None:
        """Write UX_REVIEW.md with screenshot status and next action guidance."""

        checklist = self._extract_spec_checklist(spec_path)
        verdict = (
            "PASS"
            if tests_passed
            and screenshot_info.get("pc", {}).get("status") == "captured"
            and screenshot_info.get("mobile", {}).get("status") == "captured"
            else "NEEDS_FIX"
        )
        review_lines = [
            "# UX REVIEW",
            "",
            "## Summary",
            f"- Stage: `{JobStage.UX_E2E_REVIEW.value}`",
            f"- Verdict: `{verdict}`",
            f"- Test status: `{'PASS' if tests_passed else 'FAIL'}`",
            f"- Preview URL: {preview_info.get('external_url', 'n/a')}",
            f"- Health URL: {preview_info.get('health_url', 'n/a')}",
            "",
            "## Screenshot Artifacts",
            (
                f"- PC: `{screenshot_info.get('pc', {}).get('path', 'n/a')}` "
                f"({screenshot_info.get('pc', {}).get('status', 'unknown')}) "
                f"- {screenshot_info.get('pc', {}).get('note', '')}"
            ),
            (
                f"- Mobile: `{screenshot_info.get('mobile', {}).get('path', 'n/a')}` "
                f"({screenshot_info.get('mobile', {}).get('status', 'unknown')}) "
                f"- {screenshot_info.get('mobile', {}).get('note', '')}"
            ),
            "",
            "## Intent Checklist (from SPEC)",
        ]
        if checklist:
            review_lines.extend(f"- {line}" for line in checklist)
        else:
            review_lines.append("- SPEC에서 체크리스트 항목을 찾지 못했습니다. 핵심 요구사항 수동 확인 필요.")
        review_lines.extend(
            [
                "",
                "## Next Action",
                "- 다음 코더 단계에서 UX_REVIEW.md의 실패/누락 항목을 우선 수정한다.",
                "- PC/Mobile 스크린샷이 모두 captured 상태가 될 때까지 반복한다.",
                "",
            ]
        )
        self._docs_file(repository_path, "UX_REVIEW.md").write_text(
            "\n".join(review_lines),
            encoding="utf-8",
        )

    @staticmethod
    def _extract_spec_checklist(spec_path: Optional[Path]) -> List[str]:
        """Extract concise checklist lines from SPEC.md."""

        if spec_path is None or not spec_path.exists():
            return []
        try:
            lines = spec_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

        checklist: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- ") or re.match(r"^\d+\.\s+", stripped):
                checklist.append(stripped.lstrip("- ").strip())
            if len(checklist) >= 8:
                break
        return checklist

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
        """Summarize current working tree changes into CODE_CHANGE_SUMMARY.md."""

        self._set_stage(job.job_id, JobStage.SUMMARIZE_CODE_CHANGES, log_path)
        status_result = self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
            cwd=repository_path,
            log_path=log_path,
            purpose="git status for code change summary",
        )
        numstat_result = self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} diff --numstat",
            cwd=repository_path,
            log_path=log_path,
            purpose="git diff --numstat for code change summary",
        )

        changed_files: List[Dict[str, str]] = []
        for raw_line in status_result.stdout.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            status_code = line[:2].strip() or line[:2]
            path_text = line[3:].strip() if len(line) > 3 else "(unknown)"
            changed_files.append(
                {
                    "status": status_code,
                    "path": path_text,
                }
            )

        numstats: Dict[str, Dict[str, str]] = {}
        for raw_line in numstat_result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            numstats[parts[2]] = {"added": parts[0], "deleted": parts[1]}

        summary_path = self._docs_file(repository_path, "CODE_CHANGE_SUMMARY.md")
        fallback_lines = [
            "# CODE CHANGE SUMMARY",
            "",
            f"- Job: `{job.job_id}`",
            f"- Issue: `#{job.issue_number}`",
            f"- Stage: `{JobStage.SUMMARIZE_CODE_CHANGES.value}`",
            f"- Generated at: `{utc_now_iso()}`",
            "",
        ]

        if not changed_files:
            fallback_lines.extend(
                [
                    "## Changed Files",
                    "- 변경 파일이 감지되지 않았습니다.",
                    "",
                ]
            )
        else:
            fallback_lines.extend(
                [
                    "## Changed Files",
                    "| Status | Path | Added | Deleted |",
                    "|---|---|---:|---:|",
                ]
            )
            for item in changed_files:
                path_key = item["path"]
                stat = numstats.get(path_key, {"added": "-", "deleted": "-"})
                fallback_lines.append(
                    f"| `{item['status']}` | `{path_key}` | `{stat['added']}` | `{stat['deleted']}` |"
                )
            fallback_lines.append("")

        fallback_lines.extend(
            [
                "## Notes",
                "- 본 문서는 구현 직후 변경 파일을 빠르게 검토하기 위한 자동 요약입니다.",
                "- 이후 테스트/리뷰/수정 단계에서 변경 내역이 추가될 수 있습니다.",
                "",
            ]
        )
        prompt = self._build_code_change_summary_prompt(
            job=job,
            changed_files=changed_files,
            numstats=numstats,
        )
        copilot_summary = self._summarize_changes_with_copilot(
            job=job,
            prompt=prompt,
            repository_path=repository_path,
            log_path=log_path,
        )
        if copilot_summary:
            summary_path.write_text(copilot_summary.rstrip() + "\n", encoding="utf-8")
            self._append_actor_log(
                log_path,
                "CODEX_HELPER",
                f"Wrote code change summary via helper route: {summary_path.name}",
            )
            return

        summary_path.write_text("\n".join(fallback_lines), encoding="utf-8")
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Wrote code change summary with fallback: {summary_path.name}",
        )

    def _build_code_change_summary_prompt(
        self,
        job: JobRecord,
        changed_files: List[Dict[str, str]],
        numstats: Dict[str, Dict[str, str]],
    ) -> str:
        """Create Copilot prompt for CODE_CHANGE_SUMMARY.md generation."""

        lines = [
            "다음 변경 내역을 바탕으로 CODE_CHANGE_SUMMARY.md 본문(markdown)만 생성하세요.",
            "",
            "형식 규칙:",
            "- 제목은 반드시 '# CODE CHANGE SUMMARY'",
            "- 한국어로 작성",
            "- 다음 섹션 포함: Changed Files, Notes",
            "- Changed Files는 표 형식(Status, Path, Added, Deleted)",
            "- 불필요한 서론/결론/코드블록 금지",
            "",
            "메타:",
            f"- Job: {job.job_id}",
            f"- Issue: #{job.issue_number}",
            f"- Stage: {JobStage.SUMMARIZE_CODE_CHANGES.value}",
            "",
            "변경 파일 목록:",
        ]
        if not changed_files:
            lines.append("- 변경 파일 없음")
        else:
            for item in changed_files:
                path_key = item["path"]
                stat = numstats.get(path_key, {"added": "-", "deleted": "-"})
                lines.append(
                    f"- {item['status']} | {path_key} | +{stat['added']} / -{stat['deleted']}"
                )
        lines.append("")
        return "\n".join(lines)

    def _summarize_changes_with_copilot(
        self,
        job: JobRecord,
        prompt: str,
        repository_path: Path,
        log_path: Path,
    ) -> Optional[str]:
        """Try helper-route summary generation and return markdown text."""

        prompt_path = self._docs_file(repository_path, "CODEX_HELPER_SUMMARY_PROMPT.md")
        prompt_path.write_text(prompt, encoding="utf-8")

        if self._find_configured_template_for_route("codex_helper"):
            template_variables = {
                "repository": job.repository,
                "issue_number": str(job.issue_number),
                "issue_title": job.issue_title,
                "issue_url": job.issue_url,
                "branch_name": job.branch_name,
                "work_dir": str(repository_path),
                "prompt_file": str(prompt_path),
            }
            try:
                result = self.command_templates.run_template(
                    template_name=self._template_for_route("codex_helper"),
                    variables=template_variables,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "CODEX_HELPER"),
                )
            except Exception as error:  # noqa: BLE001 - fallback to built-in command
                self._append_actor_log(
                    log_path,
                    "CODEX_HELPER",
                    f"Helper route failed without external fallback: {error}",
                )
                return None
        else:
            return None
        if int(getattr(result, "exit_code", 1)) != 0:
            return None
        output = str(getattr(result, "stdout", "")).strip()
        if not output:
            return None
        if "# CODE CHANGE SUMMARY" not in output:
            output = "# CODE CHANGE SUMMARY\n\n" + output
        return output

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
        self._set_stage(job.job_id, stage, log_path)

        status_result = self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
            cwd=repository_path,
            log_path=log_path,
            purpose="git status",
        )

        if not status_result.stdout.strip():
            self._append_log(log_path, f"No changes to commit at stage {stage.value}")
            return

        changed_paths = []
        for raw_line in status_result.stdout.splitlines():
            path = self._parse_porcelain_path(raw_line)
            if path:
                changed_paths.append(path)

        self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} add -A",
            cwd=repository_path,
            log_path=log_path,
            purpose="git add",
        )

        summary = self._prepare_commit_summary_with_ai(
            job=job,
            repository_path=repository_path,
            stage_name=stage.value,
            commit_type=commit_type,
            changed_paths=changed_paths,
            log_path=log_path,
        )
        if summary:
            commit_message = f"{commit_type}: {summary}"
        else:
            commit_message = f"{commit_type}: apply {stage.value} for issue #{job.issue_number}"
        self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} commit -m "
                f"{shlex.quote(commit_message)}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="git commit",
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
        """Generate one-line commit summary using configured helper routes."""

        summary = self._prepare_commit_summary_with_copilot(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            changed_paths=changed_paths,
            log_path=log_path,
        )
        if self._is_usable_commit_summary(summary):
            return summary

        summary = self._prepare_commit_summary_with_claude(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            log_path=log_path,
        )
        if self._is_usable_commit_summary(summary):
            return summary
        return ""

    def _prepare_commit_summary_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        log_path: Path,
    ) -> str:
        """Generate one-line Korean commit summary using configured summary route."""

        template_name = self._find_configured_template_for_route("commit_summary")
        if not template_name:
            return ""

        prompt_path = self._docs_file(
            repository_path,
            f"COMMIT_MESSAGE_PROMPT_{stage_name.upper()}.md",
        )
        output_path = self._docs_file(
            repository_path,
            f"COMMIT_MESSAGE_{stage_name.upper()}.txt",
        )
        prompt_path.write_text(
            build_commit_message_prompt(
                spec_path=str(self._docs_file(repository_path, "SPEC.md")),
                plan_path=str(self._docs_file(repository_path, "PLAN.md")),
                review_path=str(self._docs_file(repository_path, "REVIEW.md")),
                design_path=str(self._docs_file(repository_path, "DESIGN_SYSTEM.md")),
                stage_name=stage_name,
                commit_type=commit_type,
            ),
            encoding="utf-8",
        )

        try:
            self.command_templates.run_template(
                template_name=template_name,
                variables={
                    **self._build_template_variables(
                        job,
                        {
                            "spec": self._docs_file(repository_path, "SPEC.md"),
                            "plan": self._docs_file(repository_path, "PLAN.md"),
                            "review": self._docs_file(repository_path, "REVIEW.md"),
                            "design": self._docs_file(repository_path, "DESIGN_SYSTEM.md"),
                            "status": self._docs_file(repository_path, "STATUS.md"),
                        },
                        prompt_path,
                    ),
                    "commit_message_path": str(output_path),
                    "last_error": "",
                    "pr_summary_path": str(self._docs_file(repository_path, "PR_SUMMARY.md")),
                },
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "TECH_WRITER"),
            )
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(
                log_path,
                "TECH_WRITER",
                f"Commit summary generation failed: {error}",
            )
            return ""

        candidate = ""
        if output_path.exists():
            candidate = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if not candidate:
            return ""
        return self._sanitize_commit_summary(candidate)

    def _prepare_commit_summary_with_copilot(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        changed_paths: List[str],
        log_path: Path,
    ) -> str:
        """Try to generate one-line commit summary with helper route."""

        prompt_lines = [
            "다음 변경사항의 커밋 제목 요약 1줄만 작성하세요.",
            "규칙:",
            "- 한국어",
            "- 12~72자",
            "- 접두어(feat:, fix:, docs:)는 제외",
            "- 불필요한 따옴표/코드블록/번호 금지",
            "",
            f"메타: issue #{job.issue_number}, stage={stage_name}, type={commit_type}",
            "변경 파일:",
        ]
        unique_paths = []
        seen = set()
        for path in changed_paths:
            key = str(path).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_paths.append(key)
            if len(unique_paths) >= 24:
                break
        if not unique_paths:
            prompt_lines.append("- 변경 파일 정보를 찾지 못함")
        else:
            for path in unique_paths:
                prompt_lines.append(f"- {path}")
        prompt = "\n".join(prompt_lines).strip() + "\n"
        prompt_path = self._docs_file(repository_path, f"CODEX_HELPER_COMMIT_PROMPT_{stage_name.upper()}.md")
        prompt_path.write_text(prompt, encoding="utf-8")

        if self._find_configured_template_for_route("codex_helper"):
            template_variables = {
                "repository": job.repository,
                "issue_number": str(job.issue_number),
                "issue_title": job.issue_title,
                "issue_url": job.issue_url,
                "branch_name": job.branch_name,
                "work_dir": str(repository_path),
                "prompt_file": str(prompt_path),
            }
            try:
                result = self.command_templates.run_template(
                    template_name=self._template_for_route("codex_helper"),
                    variables=template_variables,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "CODEX_HELPER"),
                )
            except Exception as error:  # noqa: BLE001
                self._append_actor_log(
                    log_path,
                    "CODEX_HELPER",
                    f"Helper commit summary template failed: {error}",
                )
                return ""
        else:
            return ""

        if int(getattr(result, "exit_code", 1)) != 0:
            return ""
        output = str(getattr(result, "stdout", "")).strip()
        return self._sanitize_commit_summary(output)

    @staticmethod
    def _sanitize_commit_summary(raw: str) -> str:
        """Normalize model output into a clean one-line commit summary."""

        text = str(raw or "").strip()
        if not text:
            return ""
        first = text.splitlines()[0].strip()
        first = first.strip("`").strip()
        first = re.sub(r"^\s*[-*#>\d\.\)\(]+\s*", "", first)
        first = re.sub(r"^\s*(feat|fix|docs|chore|refactor|style|test)\s*:\s*", "", first, flags=re.IGNORECASE)
        first = re.sub(r"\s+", " ", first).strip()
        return first[:120]

    @staticmethod
    def _is_usable_commit_summary(summary: str) -> bool:
        """Validate summary quality before using it as commit title body."""

        text = str(summary or "").strip()
        if len(text) < 8:
            return False
        lowered = text.lower()
        blocked = {
            "n/a",
            "없음",
            "none",
            "commit message",
            "요약 없음",
            "변경사항 없음",
        }
        if lowered in blocked:
            return False
        if "```" in text:
            return False
        return True

    def _stage_review_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.REVIEW_WITH_GEMINI, log_path)
        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

        reviewer_prompt_path = self._docs_file(repository_path, "REVIEWER_PROMPT.md")
        reviewer_prompt_path.write_text(
            build_reviewer_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                memory_selection_path=str(paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))),
                memory_context_path=str(paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                role_context=self._build_route_runtime_context("reviewer"),
            ),
            encoding="utf-8",
        )

        result = self.command_templates.run_template(
            template_name=self._template_for_route("reviewer"),
            variables=self._build_template_variables(job, paths, reviewer_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "REVIEWER"),
        )

        if not paths["review"].exists() and result.stdout.strip():
            paths["review"].write_text(result.stdout, encoding="utf-8")

        if not paths["review"].exists():
            raise CommandExecutionError(
                "Reviewer did not produce REVIEW.md. Next action: ensure reviewer "
                "template writes to REVIEW.md or outputs markdown to stdout."
            )

    def _stage_fix_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.FIX_WITH_CODEX, log_path)
        self._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        improvement_runtime = self._read_improvement_runtime_context(paths)
        strategy = str(improvement_runtime.get("strategy", "")).strip()
        scope_restriction = str(improvement_runtime.get("scope_restriction", "")).strip()

        if strategy == "design_rebaseline" or scope_restriction == "MVP_redefinition":
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Improvement strategy requires re-planning. Routing fix stage to planner instead of coder.",
            )
            self._stage_plan_with_gemini(
                job,
                repository_path,
                paths,
                log_path,
                planning_mode="dev_planning",
            )
            return

        coding_goal = "REVIEW.md TODO 반영 및 테스트 안정화"
        next_titles = improvement_runtime.get("task_titles", [])
        if next_titles:
            coding_goal = (
                "NEXT_IMPROVEMENT_TASKS.json 기반 우선 개선 항목 반영 및 테스트 안정화: "
                + ", ".join(str(title) for title in next_titles[:3])
            )

        coder_prompt_path = self._docs_file(repository_path, "CODER_PROMPT_FIX.md")
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal=coding_goal,
                design_path=str(paths.get("design", "")),
                design_tokens_path=str(paths.get("design_tokens", self._docs_file(repository_path, "DESIGN_TOKENS.json"))),
                token_handoff_path=str(paths.get("token_handoff", self._docs_file(repository_path, "TOKEN_HANDOFF.md"))),
                publish_handoff_path=str(paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))),
                improvement_plan_path=str(paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))),
                improvement_loop_state_path=str(paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))),
                next_improvement_tasks_path=str(paths.get("next_improvement_tasks", self._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))),
                memory_selection_path=str(paths.get("memory_selection", self._docs_file(repository_path, "MEMORY_SELECTION.json"))),
                memory_context_path=str(paths.get("memory_context", self._docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                role_context=self._build_route_runtime_context("coder"),
            ),
            encoding="utf-8",
        )

        self.command_templates.run_template(
            template_name=self._template_for_route("coder"),
            variables=self._build_template_variables(job, paths, coder_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "CODER"),
        )

    @staticmethod
    def _read_improvement_runtime_context(paths: Dict[str, Path]) -> Dict[str, Any]:
        """Read current improvement strategy and next-task summary."""
        return read_improvement_runtime_context(paths)

    def _stage_push_branch(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        self._set_stage(job.job_id, JobStage.PUSH_BRANCH, log_path)
        self._push_branch_with_recovery(
            repository_path=repository_path,
            branch_name=job.branch_name,
            log_path=log_path,
            purpose="git push",
        )

    def _stage_create_pr(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.CREATE_PR, log_path)
        refreshed_job = self._require_job(job.job_id)
        execution_repository = self._job_execution_repository(refreshed_job)
        preview_info = self._deploy_preview_and_smoke_test(refreshed_job, repository_path, log_path)

        pr_body_path = self._docs_file(repository_path, "PR_BODY.md")
        generated_summary_path = self._stage_prepare_pr_summary_with_claude(
            refreshed_job,
            repository_path,
            paths,
            log_path,
        )
        if generated_summary_path is not None and generated_summary_path.exists():
            content = generated_summary_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                pr_body_path.write_text(content + "\n", encoding="utf-8")
            else:
                pr_body_path.write_text(
                    (
                        "## Summary\n"
                        "- Automated by AgentHub worker\n"
                        "- Generated from deterministic stage pipeline\n\n"
                        f"{self._issue_reference_line(refreshed_job)}\n"
                    ),
                    encoding="utf-8",
                )
        else:
            pr_body_path.write_text(
                (
                    "## Summary\n"
                    "- Automated by AgentHub worker\n"
                    "- Generated from deterministic stage pipeline\n\n"
                    f"{self._issue_reference_line(refreshed_job)}\n"
                    ),
                    encoding="utf-8",
                )

        self._append_preview_section_to_pr_body(pr_body_path, preview_info)

        title = f"AgentHub: {refreshed_job.issue_title}"
        create_command = (
            f"gh pr create --repo {shlex.quote(execution_repository)} "
            f"--head {shlex.quote(job.branch_name)} "
            f"--base {shlex.quote(self.settings.default_branch)} "
            f"--title {shlex.quote(title)} "
            f"--body-file {shlex.quote(str(pr_body_path))}"
        )

        create_result = None
        try:
            create_result = self._run_shell(
                command=create_command,
                cwd=repository_path,
                log_path=log_path,
                purpose="create pull request",
            )
        except CommandExecutionError as error:
            if "already exists" not in str(error).lower():
                raise
            self._append_actor_log(
                log_path,
                "GITHUB",
                "PR already exists. Will update body and fetch existing PR URL.",
            )
            self._run_shell(
                command=(
                    f"gh pr edit --repo {shlex.quote(execution_repository)} "
                    f"{shlex.quote(job.branch_name)} "
                    f"--body-file {shlex.quote(str(pr_body_path))}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="update existing pull request body",
            )

        pr_url = self._get_pr_url(job, repository_path, log_path, create_result)
        if pr_url:
            self.store.update_job(job.job_id, pr_url=pr_url)
        else:
            raise CommandExecutionError(
                "PR creation appears to have succeeded but URL was not found. "
                "Next action: run `gh pr view <branch> --json url` manually."
            )

    def _deploy_preview_and_smoke_test(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> Dict[str, str]:
        """Build/run Docker preview and return metadata for PR body."""

        info: Dict[str, str] = {
            "status": "skipped",
            "reason": "",
            "container_name": "",
            "image_tag": "",
            "port": "",
            "external_url": "",
            "local_url": "",
            "health_url": "",
            "cors_origins": self.settings.docker_preview_cors_origins,
        }

        if not self.settings.docker_preview_enabled:
            info["reason"] = "Docker preview is disabled by configuration."
            self._write_preview_markdown(repository_path, info)
            return info

        dockerfile_path = repository_path / "Dockerfile"
        if not dockerfile_path.exists():
            info["reason"] = "Dockerfile not found in repository root."
            self._append_actor_log(log_path, "DOCKER", info["reason"])
            self._write_preview_markdown(repository_path, info)
            return info

        port = self._allocate_preview_port()
        if port is None:
            info["reason"] = (
                f"No available preview port in range "
                f"{self.settings.docker_preview_port_start}-{self.settings.docker_preview_port_end}."
            )
            self._append_actor_log(log_path, "DOCKER", info["reason"])
            self._write_preview_markdown(repository_path, info)
            return info

        container_name = f"agenthub-preview-{job.job_id[:8]}"
        image_tag = f"agenthub/{job.app_code}-{job.job_id[:8]}:latest"
        container_port = self._detect_container_port(repository_path)
        external_url = f"http://{self.settings.docker_preview_host}:{port}"
        local_url = f"http://127.0.0.1:{port}"
        health_url = f"{local_url}{self.settings.docker_preview_health_path}"

        info.update(
            {
                "container_name": container_name,
                "image_tag": image_tag,
                "port": str(port),
                "container_port": str(container_port),
                "external_url": external_url,
                "local_url": local_url,
                "health_url": health_url,
            }
        )

        try:
            self._run_shell(
                command="docker --version",
                cwd=repository_path,
                log_path=log_path,
                purpose="check docker cli",
            )

            self._execute_shell_command(
                command=f"docker rm -f {shlex.quote(container_name)}",
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "DOCKER"),
                check=False,
                command_purpose="cleanup previous preview container",
            )

            self._run_shell(
                command=f"docker build -t {shlex.quote(image_tag)} .",
                cwd=repository_path,
                log_path=log_path,
                purpose="docker build preview image",
            )
            self._run_shell(
                command=(
                    f"docker run -d --name {shlex.quote(container_name)} "
                    f"-p {port}:{container_port} "
                    f"-e PORT={container_port} "
                    f"-e CORS_ALLOWED_ORIGINS={shlex.quote(self.settings.docker_preview_cors_origins)} "
                    f"{shlex.quote(image_tag)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="docker run preview container",
            )

            is_healthy = False
            for _ in range(20):
                if self._probe_http(health_url):
                    is_healthy = True
                    break
                time.sleep(1)

            if is_healthy:
                info["status"] = "running"
                info["reason"] = "Preview container is reachable."
                self._append_actor_log(
                    log_path,
                    "DOCKER",
                    f"Preview running at {external_url} (health: {health_url})",
                )
            else:
                info["status"] = "failed"
                info["reason"] = "Container started but health check did not pass in time."
                self._append_actor_log(log_path, "DOCKER", info["reason"])
        except Exception as error:  # noqa: BLE001
            info["status"] = "failed"
            info["reason"] = f"Docker preview failed: {error}"
            self._append_actor_log(log_path, "DOCKER", info["reason"])

        self._write_preview_markdown(repository_path, info)
        return info

    def _detect_container_port(self, repository_path: Path) -> int:
        """Detect container port from Dockerfile EXPOSE, fallback to configured default."""

        dockerfile = repository_path / "Dockerfile"
        if not dockerfile.exists():
            return int(self.settings.docker_preview_container_port)
        try:
            content = dockerfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return int(self.settings.docker_preview_container_port)

        match = re.search(r"^\s*EXPOSE\s+(\d+)", content, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            return int(self.settings.docker_preview_container_port)
        try:
            parsed = int(match.group(1))
        except ValueError:
            return int(self.settings.docker_preview_container_port)
        if parsed < 1 or parsed > 65535:
            return int(self.settings.docker_preview_container_port)
        return parsed

    def _append_preview_section_to_pr_body(self, pr_body_path: Path, preview_info: Dict[str, str]) -> None:
        """Append deployment preview metadata so PR always includes pod/container info."""

        current = ""
        if pr_body_path.exists():
            current = pr_body_path.read_text(encoding="utf-8", errors="replace").rstrip() + "\n\n"

        section = self._build_preview_pr_section(preview_info)
        pr_body_path.write_text(current + section, encoding="utf-8")

    def _build_preview_pr_section(self, preview_info: Dict[str, str]) -> str:
        """Render markdown section for Docker preview status."""

        status = preview_info.get("status", "skipped")
        reason = preview_info.get("reason", "")
        container_name = preview_info.get("container_name", "")
        port = preview_info.get("port", "")
        container_port = preview_info.get("container_port", "")
        external_url = preview_info.get("external_url", "")
        health_url = preview_info.get("health_url", "")
        cors_origins = preview_info.get("cors_origins", "")

        lines = [
            "## Deployment Preview",
            f"- Docker Pod/Container: `{container_name or 'n/a'}`",
            f"- Status: `{status}`",
        ]
        if port:
            lines.append(f"- External port: `{port}` (7000 range policy)")
        if container_port:
            lines.append(f"- Container port: `{container_port}`")
        if external_url:
            lines.append(f"- External URL: {external_url}")
        if health_url:
            lines.append(f"- Health probe: {health_url}")
        if cors_origins:
            lines.append(f"- CORS allow list: `{cors_origins}`")
        if reason:
            lines.append(f"- Note: {reason}")
        lines.append("")
        return "\n".join(lines)

    def _write_preview_markdown(self, repository_path: Path, preview_info: Dict[str, str]) -> None:
        """Persist preview metadata inside workspace for audit/debug."""

        path = self._docs_file(repository_path, "PREVIEW.md")
        lines = [
            "# PREVIEW",
            "",
            f"- Status: `{preview_info.get('status', 'unknown')}`",
            f"- Docker Pod/Container: `{preview_info.get('container_name', 'n/a')}`",
            f"- Image: `{preview_info.get('image_tag', 'n/a')}`",
            f"- Container Port: `{preview_info.get('container_port', 'n/a')}`",
            f"- External URL: {preview_info.get('external_url', 'n/a')}",
            f"- Health URL: {preview_info.get('health_url', 'n/a')}",
            f"- CORS: `{preview_info.get('cors_origins', '')}`",
            f"- Note: {preview_info.get('reason', '')}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

    def _allocate_preview_port(self) -> Optional[int]:
        """Allocate one free host port in configured preview range."""

        for port in range(self.settings.docker_preview_port_start, self.settings.docker_preview_port_end + 1):
            if self._is_local_port_in_use(port):
                continue
            return port
        return None

    @staticmethod
    def _is_local_port_in_use(port: int) -> bool:
        """Check localhost TCP port usage."""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _probe_http(url: str) -> bool:
        """Return True when preview endpoint returns a non-5xx response."""

        req = urlrequest.Request(url, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=2) as resp:
                code = int(getattr(resp, "status", 0))
                return 200 <= code < 500
        except urlerror.URLError:
            return False

    def _get_pr_url(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        create_result: Optional[object],
    ) -> Optional[str]:
        """Resolve PR URL from gh output or fallback query."""

        if create_result is not None:
            for candidate in re.findall(r"https://\S+", getattr(create_result, "stdout", "")):
                if "/pull/" in candidate:
                    return candidate.strip()

        query_result = self._run_shell(
            command=(
                f"gh pr view --repo {shlex.quote(self._job_execution_repository(job))} "
                f"{shlex.quote(job.branch_name)} --json url --jq .url"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="read pull request url",
        )

        url = query_result.stdout.strip()
        return url or None

    def _stage_prepare_pr_summary_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> Optional[Path]:
        """Generate PR summary markdown with configured summary route before PR creation."""

        template_name = self._find_configured_template_for_route("pr_summary")
        if not template_name:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "PR summary template not configured; using default PR body.",
            )
            return None

        prompt_path = self._docs_file(repository_path, "PR_SUMMARY_PROMPT.md")
        output_path = self._docs_file(repository_path, "PR_SUMMARY.md")
        prompt_path.write_text(
            build_pr_summary_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                design_path=str(paths.get("design", self._docs_file(repository_path, "DESIGN_SYSTEM.md"))),
                issue_title=job.issue_title,
                issue_number=job.issue_number,
                is_long_term=self._is_long_track(job),
            ),
            encoding="utf-8",
        )

        self._append_actor_log(log_path, "ORCHESTRATOR", "Running PR summary route.")
        try:
            result = self.command_templates.run_template(
                template_name=template_name,
                variables={
                    **self._build_template_variables(
                        job,
                        paths,
                        prompt_path,
                    ),
                    "last_error": "",
                    "pr_summary_path": str(output_path),
                },
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "PR_SUMMARY"),
            )
            if not output_path.exists() and result.stdout.strip():
                output_path.write_text(result.stdout, encoding="utf-8")
            if output_path.exists():
                self._append_actor_log(
                    log_path,
                    "PR_SUMMARY",
                    f"PR summary written: {output_path.name}",
                )
                return output_path
            self._append_actor_log(
                log_path,
                "PR_SUMMARY",
                "PR summary output missing; fallback to default PR body.",
            )
            return None
        except Exception as error:  # noqa: BLE001 - summary should not block PR creation
            self._append_actor_log(
                log_path,
                "PR_SUMMARY",
                f"PR summary generation failed: {error}. Fallback to default PR body.",
            )
            return None

    def _commit_markdown_changes_after_stage(
        self,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        log_path: Path,
    ) -> None:
        """Create stage snapshots and docs commit when markdown files changed."""

        if not self.settings.enable_stage_md_commits:
            return

        status_all = self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} status --porcelain"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git status all changes ({stage_name})",
        )
        changed_lines_all = [line for line in status_all.stdout.splitlines() if line.strip()]
        if not changed_lines_all:
            return

        status_md = self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} status --porcelain -- "
                f"{shlex.quote(':(glob)**/*.md')}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git status md changes ({stage_name})",
        )
        changed_lines_md = [line for line in status_md.stdout.splitlines() if line.strip()]

        canonical_stage = self._canonical_stage_name(stage_name)
        self._write_stage_md_snapshot(
            job=job,
            repository_path=repository_path,
            stage_name=canonical_stage,
            changed_lines=changed_lines_md,
            changed_lines_all=changed_lines_all,
            log_path=log_path,
        )
        if not changed_lines_md:
            return

        changed_md_paths = [
            self._parse_porcelain_path(line)
            for line in changed_lines_md
            if self._parse_porcelain_path(line)
        ]
        if self._should_skip_md_commit(changed_md_paths):
            self._append_actor_log(
                log_path,
                "GIT",
                f"Skipped markdown commit for stage '{stage_name}' (prompt/temporary docs only).",
            )
            return

        self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} add -- "
                f"{shlex.quote(':(glob)**/*.md')}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git add md changes ({stage_name})",
        )

        display_stage = self._format_stage_display_name(canonical_stage)
        summary = self._prepare_commit_summary_with_ai(
            job=job,
            repository_path=repository_path,
            stage_name=canonical_stage,
            commit_type="docs(stage)",
            changed_paths=changed_md_paths,
            log_path=log_path,
        )
        if summary:
            commit_message = f"docs(stage): {summary}"
        else:
            commit_message = f"docs(stage): {display_stage} (issue #{job.issue_number})"
        self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} commit -m "
                f"{shlex.quote(commit_message)}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git commit md changes ({stage_name})",
        )
        self._append_actor_log(
            log_path,
            "GIT",
            f"Markdown snapshot committed after stage '{stage_name}'",
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
        """Persist per-stage markdown + file snapshot for dashboard stage toggle."""

        snapshot_root = self.settings.data_dir / "md_snapshots" / job.job_id
        snapshot_root.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage_name).strip("_") or "stage"
        snapshot_path = snapshot_root / f"attempt_{job.attempt}_{safe_stage}.json"

        md_files: List[Dict[str, str]] = []
        md_paths: List[Path] = []
        md_paths.extend(sorted(repository_path.glob("*.md")))
        docs_dir = repository_path / "_docs"
        if docs_dir.exists():
            md_paths.extend(sorted(docs_dir.glob("*.md")))
        seen_md = set()
        for path in md_paths:
            if not path.is_file():
                continue
            rel = str(path.relative_to(repository_path))
            if rel in seen_md:
                continue
            seen_md.add(rel)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            md_files.append(
                {
                    "path": rel,
                    "content": content,
                }
            )
        file_snapshots = self._collect_stage_file_snapshots(repository_path, changed_lines_all)

        payload = {
            "job_id": job.job_id,
            "attempt": job.attempt,
            "stage": stage_name,
            "created_at": utc_now_iso(),
            "changed_files": [line.strip() for line in changed_lines],
            "changed_files_all": [line.strip() for line in changed_lines_all],
            "md_files": md_files,
            "file_snapshots": file_snapshots,
        }
        snapshot_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Stage snapshot saved: {snapshot_path.name}",
        )

    def _collect_stage_file_snapshots(
        self,
        repository_path: Path,
        changed_lines_all: List[str],
    ) -> List[Dict[str, Any]]:
        """Capture changed file contents at stage boundary for point-in-time audit."""

        snapshots: List[Dict[str, Any]] = []
        seen_paths = set()
        max_files = 24
        max_bytes = 200_000

        for raw in changed_lines_all:
            if len(snapshots) >= max_files:
                break
            status = raw[:2].strip()
            rel_path = self._parse_porcelain_path(raw)
            if not rel_path or rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            abs_path = (repository_path / rel_path).resolve()
            if repository_path.resolve() not in abs_path.parents and abs_path != repository_path.resolve():
                continue

            item: Dict[str, Any] = {
                "path": rel_path,
                "status": status or "??",
                "exists": abs_path.exists() and abs_path.is_file(),
                "truncated": False,
                "binary": False,
                "content": "",
            }
            if not item["exists"]:
                snapshots.append(item)
                continue
            try:
                blob = abs_path.read_bytes()
            except OSError:
                snapshots.append(item)
                continue
            if b"\x00" in blob:
                item["binary"] = True
                snapshots.append(item)
                continue
            if len(blob) > max_bytes:
                blob = blob[:max_bytes]
                item["truncated"] = True
            item["content"] = blob.decode("utf-8", errors="replace")
            snapshots.append(item)
        return snapshots

    @staticmethod
    def _parse_porcelain_path(raw_line: str) -> str:
        """Extract normalized file path from `git status --porcelain` one line."""

        line = str(raw_line or "").rstrip()
        if len(line) < 4:
            return ""
        payload = line[3:].strip()
        if not payload:
            return ""
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1].strip()
        return payload

    @staticmethod
    def _should_skip_md_commit(changed_md_paths: List[str]) -> bool:
        """Skip noisy docs commits when only transient prompt files changed."""

        if not changed_md_paths:
            return True
        transient_prefixes = (
            "_docs/PLANNER_PROMPT",
            "_docs/CODER_PROMPT",
            "_docs/DESIGNER_PROMPT",
            "_docs/REVIEWER_PROMPT",
            "_docs/CODEX_HELPER_",
            "_docs/COPILOT_",
            "_docs/PR_SUMMARY_PROMPT",
            "_docs/COMMIT_MESSAGE_PROMPT_",
            "_docs/PLANNER_TOOL_REQUEST",
            "_docs/ESCALATION_PROMPT",
            "_docs/DOCUMENTATION_PROMPT",
            "_docs/DOCUMENTATION_BUNDLE",
            "_docs/SCAFFOLD_PLAN_PROMPT",
        )
        normalized = [str(path).strip() for path in changed_md_paths if str(path).strip()]
        if not normalized:
            return True
        return all(any(path.startswith(prefix) for prefix in transient_prefixes) for path in normalized)

    @staticmethod
    def _canonical_stage_name(stage_name: str) -> str:
        """Normalize workflow node types into JobStage-compatible stage names."""

        node_to_stage = {
            "gh_read_issue": JobStage.READ_ISSUE.value,
            "write_spec": JobStage.WRITE_SPEC.value,
            "idea_to_product_brief": JobStage.IDEA_TO_PRODUCT_BRIEF.value,
            "generate_user_flows": JobStage.GENERATE_USER_FLOWS.value,
            "define_mvp_scope": JobStage.DEFINE_MVP_SCOPE.value,
            "architecture_planning": JobStage.ARCHITECTURE_PLANNING.value,
            "project_scaffolding": JobStage.PROJECT_SCAFFOLDING.value,
            "gemini_plan": JobStage.PLAN_WITH_GEMINI.value,
            "designer_task": JobStage.DESIGN_WITH_CODEX.value,
            "publisher_task": "publisher_task",
            "copywriter_task": "copywriter_task",
            "documentation_task": JobStage.DOCUMENTATION_TASK.value,
            "codex_implement": JobStage.IMPLEMENT_WITH_CODEX.value,
            "code_change_summary": JobStage.SUMMARIZE_CODE_CHANGES.value,
            "test_after_implement": JobStage.TEST_AFTER_IMPLEMENT.value,
            "ux_e2e_review": JobStage.UX_E2E_REVIEW.value,
            "commit_implement": JobStage.COMMIT_IMPLEMENT.value,
            "gemini_review": JobStage.REVIEW_WITH_GEMINI.value,
            "product_review": JobStage.PRODUCT_REVIEW.value,
            "improvement_stage": JobStage.IMPROVEMENT_STAGE.value,
            "codex_fix": JobStage.FIX_WITH_CODEX.value,
            "coder_fix_from_test_report": JobStage.FIX_WITH_CODEX.value,
            "test_after_fix": JobStage.TEST_AFTER_FIX.value,
            "test_after_fix_final": JobStage.TEST_AFTER_FIX.value,
            "tester_run_e2e": JobStage.TEST_AFTER_FIX.value,
            "tester_retest_e2e": JobStage.TEST_AFTER_FIX.value,
            "commit_fix": JobStage.COMMIT_FIX.value,
        }
        return node_to_stage.get(stage_name, stage_name)

    @staticmethod
    def _format_stage_display_name(stage_name: str) -> str:
        """Return short Korean labels for markdown snapshot commit messages."""

        stage_map = {
            JobStage.READ_ISSUE.value: "이슈 읽기 문서 반영",
            JobStage.WRITE_SPEC.value: "스펙 문서 작성",
            JobStage.IDEA_TO_PRODUCT_BRIEF.value: "제품 정의 브리프 작성",
            JobStage.GENERATE_USER_FLOWS.value: "사용자 흐름 작성",
            JobStage.DEFINE_MVP_SCOPE.value: "MVP 범위 정의",
            JobStage.ARCHITECTURE_PLANNING.value: "아키텍처 계획 작성",
            JobStage.PROJECT_SCAFFOLDING.value: "프로젝트 스캐폴딩 작성",
            JobStage.PLAN_WITH_GEMINI.value: "제미나이 플래너 작성",
            JobStage.DESIGN_WITH_CODEX.value: "코덱스 디자이너 작성",
            JobStage.COPYWRITER_TASK.value: "카피라이터 작성",
            JobStage.DOCUMENTATION_TASK.value: "기술 문서 작성",
            JobStage.IMPLEMENT_WITH_CODEX.value: "코덱스 구현자 작성",
            JobStage.SUMMARIZE_CODE_CHANGES.value: "코드 변경 요약 작성",
            JobStage.TEST_AFTER_IMPLEMENT.value: "구현 후 테스트 리포트 작성",
            JobStage.UX_E2E_REVIEW.value: "UX E2E 검수 리포트 작성",
            JobStage.COMMIT_IMPLEMENT.value: "구현 커밋 단계 문서 정리",
            JobStage.REVIEW_WITH_GEMINI.value: "제미나이 리뷰어 작성",
            JobStage.PRODUCT_REVIEW.value: "제품 품질 리뷰 작성",
            JobStage.IMPROVEMENT_STAGE.value: "개선 루프 계획 작성",
            JobStage.FIX_WITH_CODEX.value: "코덱스 수정자 작성",
            JobStage.TEST_AFTER_FIX.value: "수정 후 테스트 리포트 작성",
            JobStage.COMMIT_FIX.value: "수정 커밋 단계 문서 정리",
            "gh_read_issue": "이슈 읽기 문서 반영",
            "write_spec": "스펙 문서 작성",
            "idea_to_product_brief": "제품 정의 브리프 작성",
            "generate_user_flows": "사용자 흐름 작성",
            "define_mvp_scope": "MVP 범위 정의",
            "architecture_planning": "아키텍처 계획 작성",
            "project_scaffolding": "프로젝트 스캐폴딩 작성",
            "gemini_plan": "제미나이 플래너 작성",
            "designer_task": "코덱스 디자이너 작성",
            "publisher_task": "퍼블리셔 작성",
            "copywriter_task": "카피라이터 작성",
            "documentation_task": "기술 문서 작성",
            "codex_implement": "코덱스 구현자 작성",
            "code_change_summary": "코드 변경 요약 작성",
            "test_after_implement": "구현 후 테스트 리포트 작성",
            "ux_e2e_review": "UX E2E 검수 리포트 작성",
            "commit_implement": "구현 커밋 단계 문서 정리",
            "gemini_review": "제미나이 리뷰어 작성",
            "product_review": "제품 품질 리뷰 작성",
            "improvement_stage": "개선 루프 계획 작성",
            "codex_fix": "코덱스 수정자 작성",
            "coder_fix_from_test_report": "코덱스 수정자 작성",
            "test_after_fix": "수정 후 테스트 리포트 작성",
            "test_after_fix_final": "수정 후 테스트 리포트 작성",
            "tester_run_e2e": "E2E/타입별 검증 리포트 작성",
            "tester_retest_e2e": "E2E/타입별 재검증 리포트 작성",
            "commit_fix": "수정 커밋 단계 문서 정리",
        }
        return stage_map.get(stage_name, f"{stage_name} 문서 반영")

    def _run_optional_escalation(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Run optional escalation template after a failure."""

        job = self._require_job(job_id)
        repository_path = self._job_workspace_path(job)
        if not repository_path.exists():
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Escalation skipped because repository directory is not ready yet.",
            )
            return

        escalation_prompt_path = self._docs_file(repository_path, "ESCALATION_PROMPT.md")
        escalation_prompt_path.write_text(
            (
                "The main loop failed. Provide a short unblock plan.\n\n"
                f"Last error:\n{last_error}\n"
            ),
            encoding="utf-8",
        )

        self._append_actor_log(log_path, "ORCHESTRATOR", "Running optional escalation template.")
        try:
            self.command_templates.run_template(
                template_name=self._template_for_route("escalation"),
                variables={
                    **self._build_template_variables(
                        job,
                        {
                            "spec": self._docs_file(repository_path, "SPEC.md"),
                            "plan": self._docs_file(repository_path, "PLAN.md"),
                            "review": self._docs_file(repository_path, "REVIEW.md"),
                            "design": self._docs_file(repository_path, "DESIGN_SYSTEM.md"),
                            "status": self._docs_file(repository_path, "STATUS.md"),
                        },
                        escalation_prompt_path,
                    ),
                    "last_error": last_error,
                },
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "ESCALATION"),
            )
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(log_path, "ORCHESTRATOR", f"Escalation template failed: {error}")

    def _finalize_failed_job(self, job_id: str, log_path: Path, last_error: str) -> None:
        """Best-effort cleanup when all retries are exhausted."""

        job = self._require_job(job_id)
        repository_path = self._job_workspace_path(job)
        self._set_stage(job_id, JobStage.FAILED, log_path)

        if repository_path.exists():
            status_path = self._docs_file(repository_path, "STATUS.md")
            status_path.write_text(
                build_status_markdown(
                    last_error=last_error,
                    next_actions=[
                        "Check failed command in job log and reproduce locally.",
                        "Fix root cause, then rerun by re-labeling issue with agent:run.",
                        "If needed, enable escalation template for extra guidance.",
                    ],
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", f"Wrote failure status file at {status_path}")
            self._try_create_wip_pr(job, repository_path, log_path)

        self.store.update_job(
            job_id,
            status=JobStatus.FAILED.value,
            stage=JobStage.FAILED.value,
            error_message=last_error,
            finished_at=utc_now_iso(),
            heartbeat_at=utc_now_iso(),
        )

    def _try_create_wip_pr(self, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        """Try to commit STATUS.md and open a draft PR after fatal failure."""

        try:
            status_result = self._run_shell(
                command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
                cwd=repository_path,
                log_path=log_path,
                purpose="git status before WIP PR",
            )
            if status_result.stdout.strip():
                self._run_shell(
                    command=f"git -C {shlex.quote(str(repository_path))} add -A",
                    cwd=repository_path,
                    log_path=log_path,
                    purpose="git add for WIP PR",
                )
                self._run_shell(
                    command=(
                        f"git -C {shlex.quote(str(repository_path))} commit -m "
                        f"{shlex.quote(f'chore: add failure status for issue #{job.issue_number}')}"
                    ),
                    cwd=repository_path,
                    log_path=log_path,
                    purpose="git commit for WIP PR",
                )

            self._push_branch_with_recovery(
                repository_path=repository_path,
                branch_name=job.branch_name,
                log_path=log_path,
                purpose="push WIP branch",
            )

            wip_title = f"[WIP] AgentHub failed for issue #{job.issue_number}"
            wip_body = (
                "Automated run failed after max retries.\n\n"
                "Please check STATUS.md and job logs for next actions.\n"
                f"{self._issue_reference_line(job)}\n"
            )

            create_result = self._run_shell(
                command=(
                    f"gh pr create --draft --repo {shlex.quote(self._job_execution_repository(job))} "
                    f"--head {shlex.quote(job.branch_name)} "
                    f"--base {shlex.quote(self.settings.default_branch)} "
                    f"--title {shlex.quote(wip_title)} --body {shlex.quote(wip_body)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="create WIP pull request",
            )

            pr_url = self._get_pr_url(job, repository_path, log_path, create_result)
            if pr_url:
                self.store.update_job(job.job_id, pr_url=pr_url)
        except Exception as error:  # noqa: BLE001
            # Failure finalization should never crash the worker loop.
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"WIP PR creation skipped due to error: {error}",
            )

    def _build_template_variables(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        prompt_file_path: Path,
    ) -> Dict[str, str]:
        """Provide a consistent variable set for all AI templates."""

        return {
            "repository": job.repository,
            "execution_repository": self._job_execution_repository(job),
            "issue_number": str(job.issue_number),
            "issue_title": job.issue_title,
            "issue_url": job.issue_url,
            "branch_name": job.branch_name,
            "work_dir": str(self._job_workspace_path(job)),
            "spec_path": str(paths["spec"]),
            "plan_path": str(paths["plan"]),
            "review_path": str(paths["review"]),
            "design_path": str(paths.get("design", Path("_docs/DESIGN_SYSTEM.md"))),
            "design_tokens_path": str(paths.get("design_tokens", Path("_docs/DESIGN_TOKENS.json"))),
            "token_handoff_path": str(paths.get("token_handoff", Path("_docs/TOKEN_HANDOFF.md"))),
            "publish_checklist_path": str(paths.get("publish_checklist", Path("_docs/PUBLISH_CHECKLIST.md"))),
            "publish_handoff_path": str(paths.get("publish_handoff", Path("_docs/PUBLISH_HANDOFF.md"))),
            "copy_plan_path": str(paths.get("copy_plan", Path("_docs/COPYWRITING_PLAN.md"))),
            "copy_deck_path": str(paths.get("copy_deck", Path("_docs/COPY_DECK.md"))),
            "documentation_plan_path": str(paths.get("documentation_plan", Path("_docs/DOCUMENTATION_PLAN.md"))),
            "product_brief_path": str(paths.get("product_brief", Path("_docs/PRODUCT_BRIEF.md"))),
            "user_flows_path": str(paths.get("user_flows", Path("_docs/USER_FLOWS.md"))),
            "mvp_scope_path": str(paths.get("mvp_scope", Path("_docs/MVP_SCOPE.md"))),
            "architecture_plan_path": str(paths.get("architecture_plan", Path("_docs/ARCHITECTURE_PLAN.md"))),
            "scaffold_plan_path": str(paths.get("scaffold_plan", Path("_docs/SCAFFOLD_PLAN.md"))),
            "bootstrap_report_path": str(paths.get("bootstrap_report", Path("_docs/BOOTSTRAP_REPORT.json"))),
            "product_review_json_path": str(paths.get("product_review", Path("_docs/PRODUCT_REVIEW.json"))),
            "review_history_path": str(paths.get("review_history", Path("_docs/REVIEW_HISTORY.json"))),
            "improvement_backlog_path": str(paths.get("improvement_backlog", Path("_docs/IMPROVEMENT_BACKLOG.json"))),
            "improvement_loop_state_path": str(paths.get("improvement_loop_state", Path("_docs/IMPROVEMENT_LOOP_STATE.json"))),
            "improvement_plan_path": str(paths.get("improvement_plan", Path("_docs/IMPROVEMENT_PLAN.md"))),
            "next_improvement_tasks_path": str(paths.get("next_improvement_tasks", Path("_docs/NEXT_IMPROVEMENT_TASKS.json"))),
            "memory_log_path": str(paths.get("memory_log", Path("_docs/MEMORY_LOG.jsonl"))),
            "decision_history_path": str(paths.get("decision_history", Path("_docs/DECISION_HISTORY.json"))),
            "failure_patterns_path": str(paths.get("failure_patterns", Path("_docs/FAILURE_PATTERNS.json"))),
            "conventions_path": str(paths.get("conventions", Path("_docs/CONVENTIONS.json"))),
            "memory_selection_path": str(paths.get("memory_selection", Path("_docs/MEMORY_SELECTION.json"))),
            "memory_context_path": str(paths.get("memory_context", Path("_docs/MEMORY_CONTEXT.json"))),
            "memory_feedback_path": str(paths.get("memory_feedback", Path("_docs/MEMORY_FEEDBACK.json"))),
            "memory_rankings_path": str(paths.get("memory_rankings", Path("_docs/MEMORY_RANKINGS.json"))),
            "strategy_shadow_report_path": str(paths.get("strategy_shadow_report", Path("_docs/STRATEGY_SHADOW_REPORT.json"))),
            "stage_contracts_path": str(paths.get("stage_contracts", Path("_docs/STAGE_CONTRACTS.md"))),
            "stage_contracts_json_path": str(paths.get("stage_contracts_json", Path("_docs/STAGE_CONTRACTS.json"))),
            "pipeline_analysis_path": str(paths.get("pipeline_analysis", Path("_docs/PIPELINE_ANALYSIS.md"))),
            "pipeline_analysis_json_path": str(paths.get("pipeline_analysis_json", Path("_docs/PIPELINE_ANALYSIS.json"))),
            "readme_path": str(paths.get("readme", Path("README.md"))),
            "copyright_path": str(paths.get("copyright", Path("COPYRIGHT.md"))),
            "development_guide_path": str(paths.get("development_guide", Path("DEVELOPMENT_GUIDE.md"))),
            "docs_bundle_path": str(self._docs_file(self._job_workspace_path(job), "DOCUMENTATION_BUNDLE.md")),
            "status_path": str(paths.get("status", Path("_docs/STATUS.md"))),
            "prompt_file": str(prompt_file_path),
        }

    def _ensure_design_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure design token/handoff artifacts exist after design planning step."""

        design_tokens = paths.get("design_tokens", self._docs_file(repository_path, "DESIGN_TOKENS.json"))
        token_handoff = paths.get("token_handoff", self._docs_file(repository_path, "TOKEN_HANDOFF.md"))
        if not design_tokens.exists():
            fallback_tokens = {
                "meta": {"source": "fallback", "note": "Designer output missing structured tokens"},
                "theme": {
                    "light": {"color": {"bg": "#FFFFFF", "fg": "#111827", "primary": "#2563EB"}},
                    "dark": {"color": {"bg": "#0B1220", "fg": "#E5E7EB", "primary": "#60A5FA"}},
                },
                "typography": {"font_family": "Pretendard, sans-serif", "scale": {"body": "16px", "title": "24px"}},
                "spacing": {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24},
            }
            design_tokens.write_text(
                json.dumps(fallback_tokens, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback DESIGN_TOKENS.json generated.")
        if not token_handoff.exists():
            token_handoff.write_text(
                (
                    "# TOKEN HANDOFF\n\n"
                    "## 적용 대상\n"
                    "- CSS 변수/테마 파일에 DESIGN_TOKENS.json 매핑\n"
                    "- 라이트/다크 테마 토큰 동시 반영\n\n"
                    "## 체크리스트\n"
                    "- [ ] 색상 토큰 적용\n"
                    "- [ ] 타이포 토큰 적용\n"
                    "- [ ] 간격 토큰 적용\n"
                    "- [ ] 접근성(명도/포커스) 확인\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback TOKEN_HANDOFF.md generated.")

    def _ensure_publisher_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure publisher checklist/handoff artifacts exist after publishing step."""

        checklist = paths.get("publish_checklist", self._docs_file(repository_path, "PUBLISH_CHECKLIST.md"))
        handoff = paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))
        if not checklist.exists():
            checklist.write_text(
                (
                    "# PUBLISH CHECKLIST\n\n"
                    "- [ ] DESIGN_SYSTEM.md 규칙 반영\n"
                    "- [ ] DESIGN_TOKENS.json 토큰 연결\n"
                    "- [ ] 라이트/다크 모드 동작\n"
                    "- [ ] 반응형(모바일 우선) 확인\n"
                    "- [ ] 접근성 기본 점검\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback PUBLISH_CHECKLIST.md generated.")
        if not handoff.exists():
            handoff.write_text(
                (
                    "# PUBLISH HANDOFF\n\n"
                    "## 변경 요약\n"
                    "- 퍼블리싱 단계에서 UI 구조/스타일을 반영했습니다.\n\n"
                    "## 개발자 후속 작업\n"
                    "- 기능 로직 연결\n"
                    "- 테스트 케이스 보강\n"
                    "- 리뷰 코멘트 반영\n\n"
                    "## 확인 방법\n"
                    "- 로컬 실행 후 라이트/다크, 모바일/데스크톱 화면 확인\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback PUBLISH_HANDOFF.md generated.")

    def _ensure_copywriter_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure copywriter artifacts exist for downstream coding/review."""

        copy_plan = paths.get("copy_plan", self._docs_file(repository_path, "COPYWRITING_PLAN.md"))
        copy_deck = paths.get("copy_deck", self._docs_file(repository_path, "COPY_DECK.md"))
        if not copy_plan.exists():
            copy_plan.write_text(
                (
                    "# COPYWRITING PLAN\n\n"
                    "## 기획 의도\n"
                    "- 사용자가 한눈에 이해하는 쉬운 문구를 우선합니다.\n\n"
                    "## 톤앤매너\n"
                    "- 한국어 중심, 친근하고 명확한 표현\n"
                    "- 짧은 문장, 행동 유도 중심\n\n"
                    "## 화면별 전략\n"
                    "- 헤드라인: 가치 제안 1문장\n"
                    "- 버튼: 동사형 CTA(2~8자)\n"
                    "- 오류/빈상태: 원인 + 다음 행동 제시\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPYWRITING_PLAN.md generated.")
        if not copy_deck.exists():
            copy_deck.write_text(
                (
                    "# COPY DECK\n\n"
                    "## 헤드라인\n"
                    "- 오늘 뭐 먹을지, 빠르게 정해드릴게요.\n\n"
                    "## 버튼 문구\n"
                    "- 추천받기\n"
                    "- 다시 고르기\n\n"
                    "## 안내/오류 문구\n"
                    "- 잠시 문제가 생겼어요. 다시 시도해 주세요.\n"
                    "- 먼저 카테고리를 선택해 주세요.\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPY_DECK.md generated.")

    def _ensure_documentation_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure root-level development documents exist before PR stage."""

        readme_path = paths.get("readme", repository_path / "README.md")
        copyright_path = paths.get("copyright", repository_path / "COPYRIGHT.md")
        development_guide_path = paths.get("development_guide", repository_path / "DEVELOPMENT_GUIDE.md")
        documentation_plan_path = paths.get(
            "documentation_plan", self._docs_file(repository_path, "DOCUMENTATION_PLAN.md")
        )

        if not readme_path.exists() or not readme_path.read_text(encoding="utf-8", errors="replace").strip():
            readme_path.write_text(
                (
                    "# Project README\n\n"
                    "## Overview\n"
                    "- 프로젝트 목적과 핵심 기능을 요약하세요.\n\n"
                    "## Quick Start\n"
                    "1. 의존성 설치\n"
                    "2. 로컬 실행\n"
                    "3. 테스트 실행\n\n"
                    "## Environment\n"
                    "- 필수 환경변수와 기본값을 정리하세요.\n\n"
                    "## Structure\n"
                    "- 주요 디렉토리/역할을 정리하세요.\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback README.md generated.")

        if not copyright_path.exists() or not copyright_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip():
            copyright_path.write_text(
                (
                    "# COPYRIGHT\n\n"
                    "Copyright (c) 2026 Project Contributors. All rights reserved.\n\n"
                    "## Third-party licenses\n"
                    "- 사용 라이브러리의 라이선스 고지를 여기에 정리하세요.\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPYRIGHT.md generated.")

        if not development_guide_path.exists() or not development_guide_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip():
            development_guide_path.write_text(
                (
                    "# DEVELOPMENT GUIDE\n\n"
                    "## Workflow\n"
                    "1. 이슈 확인\n"
                    "2. 스펙/플랜 확인\n"
                    "3. 구현/테스트\n"
                    "4. 리뷰/PR\n\n"
                    "## AgentHub usage\n"
                    "- 오케스트레이션 단계와 역할별 책임을 정리하세요.\n\n"
                    "## Troubleshooting\n"
                    "- 자주 발생하는 실패 유형과 복구 방법을 정리하세요.\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback DEVELOPMENT_GUIDE.md generated.")

        if not documentation_plan_path.exists() or not documentation_plan_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip():
            documentation_plan_path.write_text(
                (
                    "# DOCUMENTATION PLAN\n\n"
                    "## Updated in this run\n"
                    "- README.md\n"
                    "- COPYRIGHT.md\n"
                    "- DEVELOPMENT_GUIDE.md\n\n"
                    "## Next maintenance points\n"
                    "- 기능/환경변수 변경 시 README 갱신\n"
                    "- 라이선스 변경 시 COPYRIGHT 갱신\n"
                    "- 워크플로우 변경 시 DEVELOPMENT_GUIDE 갱신\n"
                ),
                encoding="utf-8",
            )
            self._append_actor_log(log_path, "ORCHESTRATOR", "Fallback DOCUMENTATION_PLAN.md generated.")

    def _is_design_system_locked(self, repository_path: Path, paths: Dict[str, Path]) -> bool:
        """Return True when design-system decision is locked and reusable."""

        payload = self._read_decisions_payload(repository_path)
        node = payload.get("design_system", {})
        if not isinstance(node, dict) or not bool(node.get("locked")):
            return False
        design_path = paths.get("design")
        if not isinstance(design_path, Path) or not design_path.exists():
            return False
        spec_path = paths.get("spec")
        plan_path = paths.get("plan")
        current_spec_hash = self._sha256_file(spec_path) if isinstance(spec_path, Path) else ""
        current_plan_hash = self._sha256_file(plan_path) if isinstance(plan_path, Path) else ""
        locked_spec_hash = str(node.get("spec_sha256", "")).strip()
        locked_plan_hash = str(node.get("plan_sha256", "")).strip()
        if not locked_spec_hash or not locked_plan_hash:
            return False
        if current_spec_hash != locked_spec_hash or current_plan_hash != locked_plan_hash:
            return False
        return True

    def _lock_design_system_decision(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Persist decision lock so repeated rounds skip design-system regeneration."""

        payload = self._read_decisions_payload(repository_path)
        spec_path = paths.get("spec")
        plan_path = paths.get("plan")
        design_path = paths.get("design")
        payload["design_system"] = {
            "locked": True,
            "locked_at": utc_now_iso(),
            "spec_sha256": self._sha256_file(spec_path) if isinstance(spec_path, Path) else "",
            "plan_sha256": self._sha256_file(plan_path) if isinstance(plan_path, Path) else "",
            "design_path": str(design_path) if isinstance(design_path, Path) else "_docs/DESIGN_SYSTEM.md",
            "note": "자동 잠금: 디자인 시스템이 1회 생성되면 반복 라운드에서 재생성을 스킵합니다.",
        }
        self._write_decisions_payload(repository_path, payload)
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            "Design-system decision locked at _docs/DECISIONS.json",
        )

    def _read_decisions_payload(self, repository_path: Path) -> Dict[str, Any]:
        """Read decisions payload with safe fallback."""

        path = self._docs_file(repository_path, "DECISIONS.json")
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return loaded

    def _write_decisions_payload(self, repository_path: Path, payload: Dict[str, Any]) -> None:
        """Write decisions payload to _docs/DECISIONS.json."""

        path = self._docs_file(repository_path, "DECISIONS.json")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json_file(path: Optional[Path]) -> Dict[str, Any]:
        """Read JSON file safely and return object fallback."""

        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _read_text_file(path: Optional[Path]) -> str:
        """Read one text file safely."""

        if path is None or not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _extract_review_todo_items(review_text: str) -> List[str]:
        """Extract actionable TODO lines from REVIEW.md."""

        items: List[str] = []
        for raw in str(review_text or "").splitlines():
            line = raw.strip()
            match = re.match(r"^[-*]\s*\[\s?\]\s*(.+)$", line)
            if match:
                todo = match.group(1).strip()
                if todo:
                    items.append(todo)
        return items

    @staticmethod
    def _stable_issue_id(raw_text: str) -> str:
        """Generate deterministic issue id from text."""

        normalized = re.sub(r"\s+", " ", str(raw_text or "").strip().lower())
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
        return f"issue_{digest}"

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
        """Evaluate top-level operating principles with explicit evidence."""

        alignment: Dict[str, Dict[str, Any]] = {}

        def add(
            principle_id: str,
            title: str,
            status: str,
            summary: str,
            evidence: List[str],
            enforced_by: str,
        ) -> None:
            alignment[principle_id] = {
                "title": title,
                "status": status,
                "summary": summary,
                "evidence": evidence,
                "enforced_by": enforced_by,
            }

        add(
            "principle_1_mvp_first",
            "MVP 우선 원칙",
            "aligned" if (mvp_scope_exists and mvp_has_out_of_scope and mvp_has_gates) else "blocked",
            "MVP 범위와 완료 게이트가 문서로 고정되어야 구현이 안정된다.",
            [
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"OutOfScope={'O' if mvp_has_out_of_scope else 'X'}",
                f"AcceptanceGates={'O' if mvp_has_gates else 'X'}",
            ],
            "MVP_SCOPE.md + implementation hard gate",
        )
        add(
            "principle_2_design_first",
            "설계 선행 원칙",
            "aligned" if all([product_brief_exists, user_flows_exists, mvp_scope_exists, architecture_exists]) else "blocked",
            "제품 정의와 설계 문서가 구현보다 먼저 준비되어야 한다.",
            [
                f"PRODUCT_BRIEF={'O' if product_brief_exists else 'X'}",
                f"USER_FLOWS={'O' if user_flows_exists else 'X'}",
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
            ],
            "product-definition hard gate",
        )
        add(
            "principle_3_small_batch",
            "작은 단위 개발 원칙",
            "aligned"
            if priority_summary.get("P1", 0) <= 3 and candidate_count <= 8
            else "warning",
            "한 라운드의 우선 개선 항목이 과도하게 많으면 범위 축소가 필요하다.",
            [
                f"P1={priority_summary.get('P1', 0)}",
                f"candidate_count={candidate_count}",
                f"todo_items={todo_items_count}",
            ],
            "improvement backlog prioritization",
        )
        add(
            "principle_4_evaluation_first",
            "평가 우선 원칙",
            "aligned" if (review_exists and (test_report_count > 0 or ux_review_exists)) else "warning",
            "리뷰, 테스트, UX 근거가 있어야 생성보다 평가를 우선할 수 있다.",
            [
                f"REVIEW={'O' if review_exists else 'X'}",
                f"TEST_REPORTS={test_report_count}",
                f"UX_REVIEW={'O' if ux_review_exists else 'X'}",
            ],
            "REVIEW.md + TEST_REPORT + UX_REVIEW",
        )
        add(
            "principle_5_iterative_improvement",
            "반복 개선 원칙",
            "aligned",
            "리뷰 결과를 backlog와 next tasks로 변환해 다음 라운드 입력으로 사용한다.",
            [f"candidate_count={candidate_count}"],
            "PRODUCT_REVIEW -> IMPROVEMENT_BACKLOG -> NEXT_IMPROVEMENT_TASKS",
        )
        add(
            "principle_6_no_repeat_same_fix",
            "반복 오류 금지 원칙",
            "runtime",
            "같은 문제 반복 여부는 improvement_stage에서 히스토리 기반으로 판단한다.",
            ["repeat-limit/stagnation/regression signals handled at runtime"],
            "improvement_stage loop guard",
        )
        product_quality_ok = all(
            scores.get(key, 0) >= 3
            for key in [
                "usability",
                "ux_clarity",
                "error_state_handling",
                "empty_state_handling",
                "loading_state_handling",
            ]
        )
        add(
            "principle_7_product_quality_bar",
            "제품 품질 기준 원칙",
            "aligned" if (overall >= 3.0 and product_quality_ok and flows_has_primary and flows_has_entry_exit) else "warning",
            "기능 동작뿐 아니라 사용 흐름, UX 명확성, 상태 처리가 함께 충족되어야 한다.",
            [
                f"overall={overall}",
                f"usability={scores.get('usability', 0)}",
                f"ux_clarity={scores.get('ux_clarity', 0)}",
                f"error={scores.get('error_state_handling', 0)}",
                f"empty={scores.get('empty_state_handling', 0)}",
                f"loading={scores.get('loading_state_handling', 0)}",
            ],
            "product_review score gate",
        )
        add(
            "principle_8_record_decisions",
            "기록 원칙",
            "aligned" if all([product_brief_exists, user_flows_exists, mvp_scope_exists, architecture_exists, review_exists]) else "warning",
            "제품 정의와 리뷰 문서가 남아 있어야 이후 개선이 설명 가능하다.",
            [
                f"PRODUCT_BRIEF={'O' if product_brief_exists else 'X'}",
                f"USER_FLOWS={'O' if user_flows_exists else 'X'}",
                f"MVP_SCOPE={'O' if mvp_scope_exists else 'X'}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
                f"REVIEW={'O' if review_exists else 'X'}",
            ],
            "_docs artifact set",
        )
        add(
            "principle_9_stability_protection",
            "안정성 보호 원칙",
            "aligned" if (test_report_count > 0 and architecture_exists and mvp_has_gates) else "warning",
            "테스트와 품질 게이트가 있어야 품질 하락을 방지할 수 있다.",
            [
                f"test_report_count={test_report_count}",
                f"ARCHITECTURE_PLAN={'O' if architecture_exists else 'X'}",
                f"MVP_gates={'O' if mvp_has_gates else 'X'}",
            ],
            "test gate + architecture quality gate",
        )
        add(
            "principle_10_continuous_evolution",
            "지속 진화 원칙",
            "aligned",
            "생성 후 종료하지 않고 review/history/backlog를 통해 다음 개선 루프로 연결한다.",
            [
                f"candidate_count={candidate_count}",
                "review history is appended every round",
            ],
            "review_history + improvement_stage",
        )

        return alignment

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
        """Collect evidence-backed signals for PRODUCT_REVIEW scoring."""

        excluded_dirs = {
            ".git",
            "_docs",
            "node_modules",
            ".next",
            "dist",
            "build",
            ".venv",
            "venv",
            "__pycache__",
            ".pytest_cache",
            "coverage",
        }
        source_exts = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".vue",
            ".html",
            ".css",
            ".scss",
            ".sass",
            ".json",
            ".md",
        }
        source_paths: List[Path] = []
        test_paths: List[Path] = []
        manifest_names = {"package.json", "pyproject.toml", "requirements.txt", "deno.json", "Cargo.toml"}
        runtime_manifest_count = 0

        for path in repository_path.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(repository_path)
            if any(part in excluded_dirs for part in relative.parts):
                continue
            if path.name in manifest_names:
                runtime_manifest_count += 1
            if path.suffix.lower() in source_exts:
                source_paths.append(path)
                lowered_name = path.name.lower()
                lowered_parts = "/".join(part.lower() for part in relative.parts)
                if (
                    lowered_name.startswith("test_")
                    or lowered_name.endswith(".test.ts")
                    or lowered_name.endswith(".test.tsx")
                    or lowered_name.endswith(".spec.ts")
                    or lowered_name.endswith(".spec.tsx")
                    or lowered_name.endswith(".test.js")
                    or lowered_name.endswith(".spec.js")
                    or "/tests/" in f"/{lowered_parts}/"
                ):
                    test_paths.append(path)

        def _read_limited_text(file_path: Path, *, max_chars: int = 64000) -> str:
            try:
                with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                    return handle.read(max_chars).lower()
            except OSError:
                return ""

        def _is_ui_layer_file(file_path: Path) -> bool:
            relative = file_path.relative_to(repository_path)
            lowered_parts = [part.lower() for part in relative.parts]
            lowered_name = file_path.name.lower()
            if file_path.suffix.lower() in {".tsx", ".jsx", ".vue", ".html"}:
                return True
            ui_dirs = {"components", "component", "pages", "views", "screens", "templates", "ui", "widgets"}
            if any(part in ui_dirs for part in lowered_parts):
                return file_path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx", ".vue", ".html"}
            return lowered_name.endswith((".page.tsx", ".screen.tsx", ".view.tsx"))

        source_todo_markers = 0
        analyzed_source_file_count = 0
        analyzed_ui_file_count = 0
        state_source_hits = {"error": 0, "empty": 0, "loading": 0}
        state_source_keywords = {
            "error": ["error", "failed", "retry", "alert", "toast", "fallback"],
            "empty": ["empty", "no data", "no results", "placeholder", "not found", "데이터 없음"],
            "loading": ["loading", "spinner", "skeleton", "pending", "isloading", "aria-busy"],
        }
        for file_path in source_paths[:400]:
            text = _read_limited_text(file_path)
            if not text:
                continue
            analyzed_source_file_count += 1
            source_todo_markers += sum(text.count(marker) for marker in ["todo", "fixme", "hack"])
            if not _is_ui_layer_file(file_path):
                continue
            analyzed_ui_file_count += 1
            for state_name, keywords in state_source_keywords.items():
                if any(keyword in text for keyword in keywords):
                    state_source_hits[state_name] += 1

        user_flows_text = self._read_text_file(paths.get("user_flows")).lower()
        state_doc_sources = [user_flows_text, ux_review_text.lower()]

        generated_docs = {
            "product_brief": bool(self._read_text_file(paths.get("product_brief"))),
            "user_flows": bool(self._read_text_file(paths.get("user_flows"))),
            "mvp_scope": bool(self._read_text_file(paths.get("mvp_scope"))),
            "architecture_plan": bool(self._read_text_file(paths.get("architecture_plan"))),
            "scaffold_plan": bool(self._read_text_file(paths.get("scaffold_plan"))),
            "review": bool(review_text),
            "ux_review": bool(ux_review_text),
            "test_reports": len(test_report_paths),
        }

        def _state_signal_payload(name: str, doc_keywords: List[str]) -> Dict[str, Any]:
            source_hits = int(state_source_hits.get(name, 0) or 0)
            doc_hits = sum(
                1
                for source_text in state_doc_sources
                if source_text and any(keyword in source_text for keyword in doc_keywords)
            )
            return {
                "signals": ["ui_file_presence", "document_presence"],
                "metrics": {
                    "ui_candidate_file_count": analyzed_ui_file_count,
                    "source_hits": source_hits,
                    "doc_hits": doc_hits,
                    "keywords": state_source_keywords.get(name, []),
                },
                "source_hits": source_hits,
                "doc_hits": doc_hits,
            }

        return {
            "source_summary": {
                "source_file_count": len(source_paths),
                "test_file_count": len(test_paths),
                "analyzed_source_file_count": analyzed_source_file_count,
                "analyzed_ui_file_count": analyzed_ui_file_count,
                "runtime_manifest_count": runtime_manifest_count,
                "readme_exists": (repository_path / "README.md").exists(),
                "todo_markers": source_todo_markers,
                "review_todo_count": len(todo_items),
            },
            "artifact_health": {
                "docs": {
                    **generated_docs,
                    "generated_count": sum(
                        int(value) if isinstance(value, bool) else (1 if value else 0)
                        for value in generated_docs.values()
                    ),
                },
                "repo": {
                    "source_file_count": len(source_paths),
                    "test_file_count": len(test_paths),
                    "runtime_manifest_count": runtime_manifest_count,
                    "readme_exists": (repository_path / "README.md").exists(),
                },
                "tests": {
                    "report_count": len(test_report_paths),
                    "test_file_count": len(test_paths),
                },
            },
            "state_signals": {
                "error": _state_signal_payload(
                    "error",
                    ["error", "오류", "에러", "실패"],
                ),
                "empty": _state_signal_payload(
                    "empty",
                    ["empty", "빈 상태", "데이터 없음"],
                ),
                "loading": _state_signal_payload(
                    "loading",
                    ["loading", "로딩", "spinner", "skeleton"],
                ),
            },
        }

    @staticmethod
    def _summarize_operating_policy(
        principle_alignment: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Summarize which operating principles require action."""

        blocked = [
            key for key, value in principle_alignment.items()
            if str(value.get("status", "")) == "blocked"
        ]
        warnings = [
            key for key, value in principle_alignment.items()
            if str(value.get("status", "")) == "warning"
        ]
        runtime = [
            key for key, value in principle_alignment.items()
            if str(value.get("status", "")) == "runtime"
        ]
        return {
            "blocked_principles": blocked,
            "warning_principles": warnings,
            "runtime_principles": runtime,
            "requires_design_reset": "principle_2_design_first" in blocked,
            "requires_scope_reset": (
                "principle_1_mvp_first" in blocked
                or "principle_3_small_batch" in warnings
            ),
            "requires_quality_focus": (
                "principle_4_evaluation_first" in warnings
                or "principle_7_product_quality_bar" in warnings
                or "principle_9_stability_protection" in warnings
            ),
        }

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
        """Derive one repo maturity snapshot from review evidence."""

        docs_info = artifact_health.get("docs", {}) if isinstance(artifact_health, dict) else {}
        repo_info = artifact_health.get("repo", {}) if isinstance(artifact_health, dict) else {}
        tests_info = artifact_health.get("tests", {}) if isinstance(artifact_health, dict) else {}

        docs_generated = int(docs_info.get("generated_count", 0) or 0)
        source_file_count = int(repo_info.get("source_file_count", 0) or 0)
        test_file_count = int(tests_info.get("test_file_count", 0) or 0)
        test_report_count = int(tests_info.get("report_count", 0) or 0)
        quality_gate_passed = bool(quality_gate.get("passed"))
        blocked_principles = sum(
            1 for item in principle_alignment.values()
            if isinstance(item, dict) and str(item.get("status", "")) == "blocked"
        )
        categories_below = quality_gate.get("categories_below_threshold", [])
        if not isinstance(categories_below, list):
            categories_below = []

        score_all_ge_4 = all(int(value or 0) >= 4 for value in scores.values())
        score_product_ok = all(
            int(scores.get(key, 0) or 0) >= 3
            for key in [
                "usability",
                "ux_clarity",
                "error_state_handling",
                "empty_state_handling",
                "loading_state_handling",
            ]
        )

        level = "bootstrap"
        if overall >= 2.4 and docs_generated >= 4 and source_file_count >= 1:
            level = "mvp"
        if (
            overall >= 3.0
            and quality_gate_passed
            and docs_generated >= 6
            and test_file_count >= 1
            and score_product_ok
        ):
            level = "usable"
        if (
            overall >= 3.7
            and quality_gate_passed
            and docs_generated >= 7
            and test_file_count >= 2
            and test_report_count >= 1
            and score_product_ok
            and blocked_principles == 0
            and len(categories_below) == 0
        ):
            level = "stable"
        if (
            overall >= 4.4
            and quality_gate_passed
            and docs_generated >= 7
            and test_file_count >= 2
            and test_report_count >= 1
            and blocked_principles == 0
            and len(categories_below) == 0
            and score_all_ge_4
        ):
            level = "product_grade"

        level_order = ["bootstrap", "mvp", "usable", "stable", "product_grade"]
        level_rank = {name: idx for idx, name in enumerate(level_order)}
        previous_rank = level_rank.get(previous_level or "bootstrap", 0)
        current_rank = level_rank.get(level, 0)
        progression = "unchanged"
        if current_rank > previous_rank:
            progression = "up"
        elif current_rank < previous_rank:
            progression = "down"

        docs_ratio = min(1.0, docs_generated / 8.0)
        tests_ratio = min(1.0, (test_file_count + test_report_count) / 4.0)
        penalty = min(12, blocked_principles * 4 + len(categories_below) * 2)
        maturity_score = int(
            round(
                min(
                    100.0,
                    max(
                        0.0,
                        (overall / 5.0) * 65.0
                        + docs_ratio * 20.0
                        + tests_ratio * 15.0
                        - penalty,
                    ),
                )
            )
        )

        return {
            "generated_at": utc_now_iso(),
            "job_id": job_id,
            "level": level,
            "score": maturity_score,
            "previous_level": previous_level or "",
            "progression": progression,
            "quality_gate_passed": quality_gate_passed,
            "evidence": {
                "overall": overall,
                "source_file_count": source_file_count,
                "generated_doc_count": docs_generated,
                "test_file_count": test_file_count,
                "test_report_count": test_report_count,
                "blocked_principles": blocked_principles,
                "categories_below_threshold": len(categories_below),
            },
        }

    @staticmethod
    def _build_quality_trend_snapshot(
        *,
        job_id: str,
        history_entries: List[Dict[str, Any]],
        maturity_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Summarize quality movement across recent review history."""

        current_overall = float(history_entries[-1].get("overall", 0.0)) if history_entries else 0.0
        previous_overall = float(history_entries[-2].get("overall", 0.0)) if len(history_entries) >= 2 else 0.0
        delta_from_previous = round(current_overall - previous_overall, 2) if len(history_entries) >= 2 else 0.0
        recent_scores = [
            float(item.get("overall", 0.0))
            for item in history_entries[-5:]
            if item.get("overall") is not None
        ]
        rolling_average_3 = round(
            sum(float(item.get("overall", 0.0)) for item in history_entries[-3:]) / max(1, len(history_entries[-3:])),
            2,
        ) if history_entries else 0.0
        best_overall = round(max(recent_scores), 2) if recent_scores else current_overall
        worst_overall = round(min(recent_scores), 2) if recent_scores else current_overall
        trend_direction = "stable"
        if delta_from_previous >= 0.2:
            trend_direction = "improving"
        elif delta_from_previous <= -0.2:
            trend_direction = "declining"

        improving_streak = 0
        for older, newer in zip(history_entries[:-1], history_entries[1:]):
            if float(newer.get("overall", 0.0)) > float(older.get("overall", 0.0)):
                improving_streak += 1

        tracked_categories = [
            "code_quality",
            "architecture_structure",
            "maintainability",
            "usability",
            "ux_clarity",
            "test_coverage",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
        ]
        category_latest_scores: Dict[str, int] = {}
        category_deltas: Dict[str, int] = {}
        category_trend_direction: Dict[str, str] = {}
        persistent_low_categories: List[str] = []
        stagnant_categories: List[str] = []
        declining_categories: List[str] = []

        for category in tracked_categories:
            category_history: List[int] = []
            for entry in history_entries:
                scores_payload = entry.get("scores", {})
                if not isinstance(scores_payload, dict):
                    continue
                value = scores_payload.get(category)
                if value is None:
                    continue
                try:
                    category_history.append(int(value))
                except (TypeError, ValueError):
                    continue

            if not category_history:
                continue

            category_latest_scores[category] = int(category_history[-1])
            if len(category_history) >= 2:
                delta = int(category_history[-1]) - int(category_history[-2])
                category_deltas[category] = delta
                if delta > 0:
                    category_trend_direction[category] = "improving"
                elif delta < 0:
                    category_trend_direction[category] = "declining"
                    declining_categories.append(category)
                else:
                    category_trend_direction[category] = "stable"
            else:
                category_trend_direction[category] = "stable"

            recent_window = category_history[-3:]
            if len(recent_window) >= 3 and all(score <= 2 for score in recent_window):
                persistent_low_categories.append(category)
            if len(recent_window) >= 3 and max(recent_window) == min(recent_window):
                stagnant_categories.append(category)

        return {
            "generated_at": utc_now_iso(),
            "job_id": job_id,
            "review_round_count": len(history_entries),
            "current_overall": current_overall,
            "previous_overall": previous_overall if len(history_entries) >= 2 else None,
            "delta_from_previous": delta_from_previous if len(history_entries) >= 2 else None,
            "rolling_average_3": rolling_average_3,
            "best_overall": best_overall,
            "worst_overall": worst_overall,
            "trend_direction": trend_direction,
            "score_stagnation_detected": len(recent_scores) >= 3 and (max(recent_scores) - min(recent_scores) <= 0.15),
            "quality_regression_detected": delta_from_previous <= -0.2 if len(history_entries) >= 2 else False,
            "maturity_level": str(maturity_snapshot.get("level", "")).strip(),
            "previous_maturity_level": str(maturity_snapshot.get("previous_level", "")).strip(),
            "maturity_progression": str(maturity_snapshot.get("progression", "unchanged")).strip(),
            "improving_streak": improving_streak,
            "category_latest_scores": category_latest_scores,
            "category_deltas": category_deltas,
            "category_trend_direction": category_trend_direction,
            "persistent_low_categories": persistent_low_categories,
            "stagnant_categories": stagnant_categories,
            "declining_categories": declining_categories,
        }

    @staticmethod
    def _validate_product_review_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate PRODUCT_REVIEW payload at runtime without external schema libs."""

        required_scores = [
            "code_quality",
            "architecture_structure",
            "maintainability",
            "usability",
            "ux_clarity",
            "test_coverage",
            "error_state_handling",
            "empty_state_handling",
            "loading_state_handling",
            "overall",
        ]
        errors: List[str] = []
        if not isinstance(payload, dict):
            return {"passed": False, "errors": ["payload must be object"]}
        scores = payload.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores must be object")
            scores = {}
        for key in required_scores:
            value = scores.get(key)
            if not isinstance(value, (int, float)):
                errors.append(f"scores.{key} must be number")
                continue
            if value < 0 or value > 5:
                errors.append(f"scores.{key} out of range (0..5): {value}")
        findings = payload.get("findings")
        if not isinstance(findings, list) or not findings:
            errors.append("findings must be non-empty array")
        candidates = payload.get("improvement_candidates")
        if not isinstance(candidates, list):
            errors.append("improvement_candidates must be array")
        artifact_health = payload.get("artifact_health")
        if artifact_health is not None and not isinstance(artifact_health, dict):
            errors.append("artifact_health must be object when present")
        category_evidence = payload.get("category_evidence")
        if category_evidence is not None and not isinstance(category_evidence, dict):
            errors.append("category_evidence must be object when present")
        evidence_summary = payload.get("evidence_summary")
        if evidence_summary is not None and not isinstance(evidence_summary, dict):
            errors.append("evidence_summary must be object when present")
        principle_alignment = payload.get("principle_alignment")
        if not isinstance(principle_alignment, dict) or not principle_alignment:
            errors.append("principle_alignment must be non-empty object")
        operating_policy = payload.get("operating_policy")
        if not isinstance(operating_policy, dict):
            errors.append("operating_policy must be object")
        gate = payload.get("quality_gate")
        if not isinstance(gate, dict) or "passed" not in gate:
            errors.append("quality_gate.passed is required")
        return {
            "passed": not errors,
            "errors": errors,
            "checked_at": utc_now_iso(),
        }

    @staticmethod
    def _write_stage_contracts_doc(path: Path, json_path: Path) -> None:
        """Persist stage contracts in markdown and machine-readable JSON."""

        stages = [
            {
                "name": "idea_to_product_brief",
                "input": ["SPEC.md", "SPEC.json", "issue metadata"],
                "output": ["PRODUCT_BRIEF.md"],
                "success_condition": "Goal/Problem/Target Users/Core Value/Success Metrics 섹션 존재",
                "failure_condition": "문서 미생성 또는 핵심 섹션 누락",
                "handoff_data": ["goal", "target_users", "scope_inputs", "success_metrics"],
            },
            {
                "name": "generate_user_flows",
                "input": ["PRODUCT_BRIEF.md"],
                "output": ["USER_FLOWS.md"],
                "success_condition": "Primary/Secondary Flow + UX State Checklist(loading/empty/error) 존재",
                "failure_condition": "흐름 단계 또는 상태 정의 누락",
                "handoff_data": ["primary_flow_steps", "secondary_flows", "ux_state_checklist"],
            },
            {
                "name": "define_mvp_scope",
                "input": ["PRODUCT_BRIEF.md", "USER_FLOWS.md", "SPEC.json"],
                "output": ["MVP_SCOPE.md"],
                "success_condition": "In Scope / Out of Scope / Acceptance Gates 명시",
                "failure_condition": "범위 구분 누락 또는 게이트 미정의",
                "handoff_data": ["in_scope", "out_of_scope", "acceptance_gates"],
            },
            {
                "name": "architecture_planning",
                "input": ["MVP_SCOPE.md", "USER_FLOWS.md"],
                "output": ["ARCHITECTURE_PLAN.md"],
                "success_condition": "Layer/Component/Data Contract/Quality Gate/Loop Safety 섹션 존재",
                "failure_condition": "아키텍처 경계나 루프 안전 규칙 누락",
                "handoff_data": ["component_boundaries", "quality_gates", "loop_safety_rules"],
            },
            {
                "name": "project_scaffolding",
                "input": ["ARCHITECTURE_PLAN.md", "MVP_SCOPE.md", "SPEC.json", "repo context"],
                "output": ["SCAFFOLD_PLAN.md", "BOOTSTRAP_REPORT.json"],
                "success_condition": "Repository State / Bootstrap Mode / Target Structure / Verification Checklist 존재",
                "failure_condition": "스캐폴딩 전략 또는 레포 상태 판단 누락",
                "handoff_data": ["repository_state", "bootstrap_mode", "required_setup_commands"],
            },
            {
                "name": "product_review",
                "input": ["REVIEW.md", "TEST_REPORT_*.md", "UX_REVIEW.md", "ARCHITECTURE_PLAN.md"],
                "output": [
                    "PRODUCT_REVIEW.json",
                    "REVIEW_HISTORY.json",
                    "IMPROVEMENT_BACKLOG.json",
                    "REPO_MATURITY.json",
                    "QUALITY_TREND.json",
                ],
                "success_condition": "9개 품질 카테고리 점수 + 개선 후보 + quality gate 생성",
                "failure_condition": "필수 점수 누락 또는 payload validation 실패",
                "handoff_data": [
                    "overall_score",
                    "categories_below_threshold",
                    "improvement_candidates",
                    "maturity_level",
                    "trend_direction",
                ],
            },
            {
                "name": "improvement_stage",
                "input": ["PRODUCT_REVIEW.json", "REVIEW_HISTORY.json", "IMPROVEMENT_BACKLOG.json"],
                "output": ["IMPROVEMENT_LOOP_STATE.json", "IMPROVEMENT_PLAN.md", "NEXT_IMPROVEMENT_TASKS.json"],
                "success_condition": "반복/정체/하락 감지 + 전략 변경 여부 + 다음 작업 리스트 생성",
                "failure_condition": "루프 가드 계산 실패 또는 개선 작업 산출물 미생성",
                "handoff_data": ["strategy", "next_scope_restriction", "rollback_recommended", "next_tasks"],
            },
        ]
        payload = {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "stages": stages,
        }
        lines: List[str] = [
            "# STAGE CONTRACTS",
            "",
            "자동 생성 문서입니다. 각 단계의 입출력 계약을 정의합니다.",
            "",
        ]
        for stage in stages:
            lines.append(f"## {stage['name']}")
            lines.append(f"- 입력: {', '.join(stage['input'])}")
            lines.append(f"- 출력: {', '.join(stage['output'])}")
            lines.append(f"- 성공 조건: {stage['success_condition']}")
            lines.append(f"- 실패 조건: {stage['failure_condition']}")
            lines.append(f"- 다음 단계 전달 데이터: {', '.join(stage['handoff_data'])}")
            lines.append("")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_pipeline_analysis_doc(path: Path, json_path: Path) -> None:
        """Persist current pipeline analysis in markdown and JSON."""

        current_pipeline = [
            "read_issue",
            "write_spec",
            "idea_to_product_brief",
            "generate_user_flows",
            "define_mvp_scope",
            "architecture_planning",
            "project_scaffolding",
            "plan_with_gemini",
            "implement_with_codex",
            "review_with_gemini",
            "product_review",
            "improvement_stage",
            "fix_with_codex",
            "test_after_fix",
        ]
        missing_or_weak = [
            "project_scaffolding은 추가되었지만 아직 실제 scaffold executor 없이 계획 문서/리포트 생성 단계에 머뭄",
            "제품 품질 리뷰 점수는 아직 키워드/문서 기반 휴리스틱 중심",
            "개선 백로그 자동 실행 노드는 2차 개선 대상",
        ]
        product_gaps = [
            "아이디어→제품 정의→MVP→아키텍처→스캐폴딩 흐름은 반영됨",
            "반복 개선 루프의 자동 실행기는 미도입",
            "장기 리포지토리 단위 품질 추세 분석은 미도입",
        ]
        payload = {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "current_pipeline": current_pipeline,
            "missing_or_weak_stages": missing_or_weak,
            "product_gaps": product_gaps,
            "phase1_focus": [
                "제품 개발형 파이프라인 뼈대 구축",
                "품질 평가 체계 구축",
                "반복 개선 루프 기반 구축",
                "2차 고도화를 위한 계약/산출물 표준화",
            ],
        }
        lines = [
            "# PIPELINE ANALYSIS",
            "",
            "## Current Pipeline",
            " -> ".join(current_pipeline),
            "",
            "## Missing Or Weak Stages",
        ]
        for item in missing_or_weak:
            lines.append(f"- {item}")
        lines.extend(["", "## Product Gaps"])
        for item in product_gaps:
            lines.append(f"- {item}")
        lines.extend(["", "## Phase-1 Focus"])
        for item in payload["phase1_focus"]:
            lines.append(f"- {item}")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _sha256_file(path: Optional[Path]) -> str:
        """Return SHA256 of one file, empty string when unavailable."""

        if path is None or not path.exists() or not path.is_file():
            return ""
        try:
            blob = path.read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def _docs_file(repository_path: Path, name: str) -> Path:
        """Return a generated-document path under repository '_docs' directory."""

        docs_dir = repository_path / "_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / name

    def _ref_exists(self, repository_path: Path, ref_name: str, log_path: Path) -> bool:
        """Return True when a git ref exists locally (e.g., origin/branch)."""

        check_command = (
            f"git -C {shlex.quote(str(repository_path))} rev-parse --verify "
            f"{shlex.quote(ref_name)}"
        )
        result = self._execute_shell_command(
            command=check_command,
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "GIT"),
            check=False,
            command_purpose=f"check ref {ref_name}",
        )
        return result.exit_code == 0

    def _push_branch_with_recovery(
        self,
        repository_path: Path,
        branch_name: str,
        log_path: Path,
        purpose: str,
    ) -> None:
        """Push branch with automatic non-fast-forward recovery.

        For job-owned branches, force-with-lease is a safe recovery path when the
        remote branch diverged due to earlier retries.
        """

        normal_push = (
            f"git -C {shlex.quote(str(repository_path))} push -u origin "
            f"{shlex.quote(branch_name)}"
        )
        try:
            self._run_shell(
                command=normal_push,
                cwd=repository_path,
                log_path=log_path,
                purpose=purpose,
            )
            return
        except CommandExecutionError as error:
            message = str(error).lower()
            if "non-fast-forward" not in message and "failed to push some refs" not in message:
                raise

        self._append_actor_log(
            log_path,
            "GIT",
            "Detected push divergence. Retrying with --force-with-lease for job branch.",
        )
        self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} fetch origin",
            cwd=repository_path,
            log_path=log_path,
            purpose="git fetch before force push",
        )
        self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} push --force-with-lease "
                f"-u origin {shlex.quote(branch_name)}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"{purpose} (force-with-lease)",
        )

    def _set_stage(self, job_id: str, stage: JobStage, log_path: Path) -> None:
        """Update stage in persistent store and write readable log line."""

        self.store.update_job(job_id, stage=stage.value, heartbeat_at=utc_now_iso())
        self._append_actor_log(log_path, "ORCHESTRATOR", f"[STAGE] {stage.value}")

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

        return lambda message: self._append_actor_log(log_path, actor, message)

    @staticmethod
    def _infer_actor_from_command(command: str, purpose: str) -> str:
        """Infer execution actor from command/purpose for richer log context."""

        lowered = command.lower()
        purpose_lowered = purpose.lower()
        if "codex" in lowered:
            return "CODER"
        if "planner" in purpose_lowered or "plan" in purpose_lowered and "gemini" in lowered:
            return "PLANNER"
        if "review" in purpose_lowered and "gemini" in lowered:
            return "REVIEWER"
        if lowered.startswith("gh ") or " gh " in lowered:
            return "GITHUB"
        if lowered.startswith("git ") or " git " in lowered:
            return "GIT"
        return "SYSTEM"

    def _append_actor_log(self, log_path: Path, actor: str, message: str) -> None:
        """Append one timestamped actor-tagged line to job log file."""

        normalized_actor = (actor or "ORCHESTRATOR").strip().upper()
        if message.startswith("[ACTOR:"):
            tagged = message
        else:
            tagged = f"[ACTOR:{normalized_actor}] {message}"
        debug_log_path = self._channel_log_path(log_path, "debug")
        user_log_path = self._channel_log_path(log_path, "user")
        self._append_log(debug_log_path, tagged)
        if self._should_emit_user_log(message):
            self._append_log(user_log_path, tagged)
        self._touch_job_heartbeat()

    def _touch_job_heartbeat(self, *, force: bool = False) -> None:
        """Persist one lightweight heartbeat for the active job."""

        if not self._active_job_id:
            return
        now_monotonic = time.monotonic()
        if not force and (now_monotonic - self._last_heartbeat_monotonic) < 15.0:
            return
        self._last_heartbeat_monotonic = now_monotonic
        try:
            self.store.update_job(self._active_job_id, heartbeat_at=utc_now_iso())
        except Exception:
            return

    @staticmethod
    def _channel_log_path(log_path: Path, channel: str) -> Path:
        """Return channel-specific log path from any legacy/debug/user path."""

        normalized = "user" if channel == "user" else "debug"
        parent = log_path.parent
        if parent.name == normalized:
            return log_path
        if parent.name in {"debug", "user"}:
            return parent.parent / normalized / log_path.name
        return parent / normalized / log_path.name

    @staticmethod
    def _should_emit_user_log(message: str) -> bool:
        """Return True when one log message should appear in user-friendly channel."""

        msg = (message or "").strip()
        if not msg:
            return False
        if msg.startswith("[RUN] ") or msg.startswith("[STDOUT]") or msg.startswith("[STDERR]"):
            return False
        if msg.startswith("[STAGE] "):
            return True
        if msg.startswith("Attempt "):
            return True
        if msg.startswith("Starting job ") or msg.startswith("Job finished"):
            return True
        if msg.startswith("[DONE] "):
            return True
        if msg.startswith("Wrote ") or "snapshot saved" in msg.lower():
            return True
        if "failed" in msg.lower() or "error" in msg.lower():
            return True
        if msg.startswith("Entering fix/test retry loop") or msg.startswith("[FIX_LOOP]"):
            return True
        return False

    def _is_escalation_enabled(self) -> bool:
        """Read escalation toggle from .env at runtime (fallback to boot setting)."""

        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            return self.settings.enable_escalation

        try:
            for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("AGENTHUB_ENABLE_ESCALATION="):
                    continue
                raw_value = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
                return raw_value in {"1", "true", "yes", "on"}
        except OSError:
            return self.settings.enable_escalation

        # fallback to process env for compatibility
        raw_env = os.getenv("AGENTHUB_ENABLE_ESCALATION", "")
        if raw_env:
            return raw_env.strip().lower() in {"1", "true", "yes", "on"}
        return self.settings.enable_escalation

    @staticmethod
    def _is_recovery_mode_enabled() -> bool:
        """Read recovery mode toggle from environment with default enabled."""

        raw = (os.getenv("AGENTHUB_RECOVERY_MODE", "true") or "true").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_long_track(job: JobRecord) -> bool:
        """Return True when job should use long-horizon planning mode."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "long":
            return True
        return "[장기]" in title or "[long]" in title

    @staticmethod
    def _is_ultra_track(job: JobRecord) -> bool:
        """Return True when ultra-long autonomous round mode is enabled."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "ultra":
            return True
        return "[초장기]" in title or "[ultra]" in title

    @staticmethod
    def _is_ultra10_track(job: JobRecord) -> bool:
        """Return True when 10-hour ultra-long autonomous round mode is enabled."""

        track = (job.track or "").strip().lower()
        title = (job.issue_title or "").strip().lower()
        if track == "ultra10":
            return True
        return "[초초장기]" in title or "[ultra10]" in title

    def _resolve_ai_route(self, route_name: str):
        """Resolve one logical route with active workflow-node role overrides."""

        override_role_code = self._workflow_route_role_overrides.get(str(route_name or "").strip(), "")
        if override_role_code:
            return self.ai_role_router.resolve(route_name, role_code_override=override_role_code)
        return self.ai_role_router.resolve(route_name)

    def _template_candidates_for_route(self, route_name: str) -> List[str]:
        """Return ordered template candidates for one logical AI route."""

        route = self._resolve_ai_route(route_name)
        candidates: List[str] = []
        for base_template in route.template_keys:
            per_provider = ""
            if route.cli:
                per_provider = f"{base_template}__{route.cli}"
            if self._agent_profile == "fallback":
                if per_provider:
                    candidates.append(f"{per_provider}_fallback")
                candidates.append(f"{base_template}_fallback")
            if per_provider:
                candidates.append(per_provider)
            candidates.append(base_template)

        deduped: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _build_route_runtime_context(self, route_name: str) -> str:
        """Describe one route's runtime profile for prompt injection."""

        route = self._resolve_ai_route(route_name)
        lines = [
            f"- route: {route.route_name}",
            f"- role_code: {route.role_code}",
            f"- role_name: {route.role_name}",
            f"- cli: {route.cli or '(unspecified)'}",
        ]
        if route.description:
            lines.append(f"- route_description: {route.description}")
        if route.objective:
            lines.append(f"- objective: {route.objective}")
        if route.inputs:
            lines.append(f"- expected_inputs: {route.inputs}")
        if route.outputs:
            lines.append(f"- expected_outputs: {route.outputs}")
        if route.skills:
            lines.append(f"- attached_skills: {', '.join(route.skills)}")
        if route.allowed_tools:
            lines.append(f"- allowed_tools: {', '.join(route.allowed_tools)}")
        checklist_items = [
            item.strip()
            for item in re.split(r"[\n,]+", route.checklist)
            if item.strip()
        ]
        if checklist_items:
            lines.append("- role_checklist:")
            lines.extend(f"  - {item}" for item in checklist_items[:8])
        elif route.checklist:
            lines.append(f"- role_checklist: {route.checklist}")
        return "\n".join(lines)

    def _route_allows_tool(self, route_name: str, tool_name: str) -> bool:
        """Return True when one route may request one tool."""

        normalized_tool = str(tool_name or "").strip().lower()
        if not normalized_tool:
            return False
        route = self._resolve_ai_route(route_name)
        if not route.allowed_tools:
            return normalized_tool == "research_search" if route_name == "planner" else False
        return normalized_tool in route.allowed_tools

    def _template_for_route(self, route_name: str) -> str:
        """Resolve one logical AI route to the best available template key."""

        candidates = self._template_candidates_for_route(route_name)
        for candidate in candidates:
            if self.command_templates.has_template(candidate):
                return candidate
        return candidates[0]

    def _find_configured_template_for_route(self, route_name: str) -> Optional[str]:
        """Return the first configured template for one route, if any."""

        for candidate in self._template_candidates_for_route(route_name):
            if self.command_templates.has_template(candidate):
                return candidate
        return None

    def _stop_signal_path(self, job_id: str) -> Path:
        """Return path of stop signal file for one job."""

        return self.settings.data_dir / "control" / f"stop_{job_id}.flag"

    def _is_stop_requested(self, job_id: str) -> bool:
        """Check whether user requested graceful stop for this job."""

        return self._stop_signal_path(job_id).exists()

    def _clear_stop_requested(self, job_id: str) -> None:
        """Remove stop signal file after graceful termination."""

        path = self._stop_signal_path(job_id)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _append_log(log_path: Path, message: str) -> None:
        """Append one timestamped line to job log file."""

        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now_iso()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def _require_job(self, job_id: str) -> JobRecord:
        """Return job or raise a clear error."""

        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        return job
