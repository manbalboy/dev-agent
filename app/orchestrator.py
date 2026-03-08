"""Rule-based orchestration engine for AgentHub jobs.

Important design principle:
- This module is the conductor.
- AI CLIs are workers called at fixed points.
- The order, retries, and termination conditions are code-driven.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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

from app.command_runner import (
    CommandExecutionError,
    CommandTemplateRunner,
    run_shell_command,
)
from app.config import AppSettings
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.prompt_builder import (
    build_commit_message_prompt,
    build_copywriter_prompt,
    build_coder_prompt,
    build_documentation_prompt,
    build_designer_prompt,
    build_planner_prompt,
    build_publisher_prompt,
    build_pr_summary_prompt,
    build_reviewer_prompt,
    build_spec_json,
    build_spec_markdown,
    build_status_markdown,
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
from app.workflow_design import load_workflows, validate_workflow


ShellExecutor = Callable[..., object]


@dataclass
class IssueDetails:
    """Issue data loaded from GitHub CLI."""

    title: str
    body: str
    url: str


class Orchestrator:
    """Consume queued jobs and execute the fixed orchestration pipeline."""

    def __init__(
        self,
        settings: AppSettings,
        store: JobStore,
        command_templates: CommandTemplateRunner,
        shell_executor: ShellExecutor = run_shell_command,
    ) -> None:
        self.settings = settings
        self.store = store
        self.command_templates = command_templates
        self.shell_executor = shell_executor
        self._agent_profile = "primary"

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
            error_message=None,
        )

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
        for attempt in range(1, job.max_attempts + 1):
            self.store.update_job(job_id, attempt=attempt, error_message=None)
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Attempt {attempt}/{job.max_attempts} started",
            )

            try:
                self._run_single_attempt(job_id, log_path)
                self.store.update_job(
                    job_id,
                    status=JobStatus.DONE.value,
                    stage=JobStage.DONE.value,
                    finished_at=utc_now_iso(),
                    error_message=None,
                )
                self._append_actor_log(log_path, "ORCHESTRATOR", "Job finished successfully")
                return
            except Exception as error:  # noqa: BLE001 - we want resilient orchestration.
                last_error = str(error)
                self.store.update_job(job_id, error_message=last_error)
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

    def _process_long_job(self, job_id: str, log_path: Path) -> None:
        """Run long-track mode with fixed 3 rounds of full workflow."""

        total_rounds = 3
        last_error: Optional[str] = None
        for round_index in range(1, total_rounds + 1):
            self.store.update_job(job_id, attempt=round_index, error_message=None)
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[LONG] Round {round_index}/{total_rounds} started",
            )
            try:
                self._run_single_attempt(job_id, log_path)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} completed",
                )
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self.store.update_job(job_id, error_message=last_error)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[LONG] Round {round_index}/{total_rounds} failed: {last_error}",
                )
                if self._is_escalation_enabled() and self.command_templates.has_template("escalation"):
                    self._run_optional_escalation(job_id, log_path, last_error)
                self._finalize_failed_job(job_id, log_path, last_error)
                return

        self.store.update_job(
            job_id,
            status=JobStatus.DONE.value,
            stage=JobStage.DONE.value,
            finished_at=utc_now_iso(),
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

        ultra_started = time.monotonic()
        round_index = 0
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
                    error_message=None,
                )
                return

            round_index += 1
            self.store.update_job(job_id, attempt=round_index, error_message=None)
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[{mode_tag}] Round {round_index} started",
            )

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
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[{mode_tag}] Primary agents failed in round {round_index}: {last_error}",
                )

                if self._is_escalation_enabled() and self.command_templates.has_template("escalation"):
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
        workflow = self._load_active_workflow(log_path)
        if workflow is None:
            self._run_fixed_pipeline(job, repository_path, log_path)
            return

        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Using workflow '{workflow.get('workflow_id', 'unknown')}'",
        )
        self._run_workflow_pipeline(job, repository_path, workflow, log_path)

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
        log_path: Path,
    ) -> None:
        """Run phase-1 workflow by linearized node order."""

        ordered_nodes = self._linearize_workflow_nodes(workflow)
        if not ordered_nodes:
            raise CommandExecutionError("Workflow has no executable nodes.")

        context: Dict[str, Any] = {
            "issue": None,
            "paths": None,
        }

        for index, node in enumerate(ordered_nodes):
            node_id = str(node.get("id", ""))
            node_type = str(node.get("type", ""))
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow node start: {node_id} ({node_type})",
            )

            if node_type == "gh_read_issue":
                context["issue"] = self._stage_read_issue(job, repository_path, log_path)
                self._commit_markdown_changes_after_stage(job, repository_path, node_type, log_path)
                continue

            if node_type == "write_spec":
                issue = context.get("issue")
                if not isinstance(issue, IssueDetails):
                    raise CommandExecutionError("Workflow requires issue context before write_spec.")
                context["paths"] = self._stage_write_spec(job, repository_path, issue, log_path)
                self._commit_markdown_changes_after_stage(job, repository_path, node_type, log_path)
                continue

            paths = context.get("paths")
            if not isinstance(paths, dict):
                raise CommandExecutionError("Workflow requires paths context before AI/test/git stages.")

            if node_type == "gemini_plan":
                node_title = str(node.get("title", "")).strip().lower()
                planning_mode = "general"
                if "개발 기획" in node_title or "development" in node_title:
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
            elif node_type == "idea_to_product_brief":
                self._stage_idea_to_product_brief(job, repository_path, paths, log_path)
            elif node_type == "generate_user_flows":
                self._stage_generate_user_flows(job, repository_path, paths, log_path)
            elif node_type == "define_mvp_scope":
                self._stage_define_mvp_scope(job, repository_path, paths, log_path)
            elif node_type == "architecture_planning":
                self._stage_architecture_planning(job, repository_path, paths, log_path)
            elif node_type == "designer_task":
                if self._is_design_system_locked(repository_path, paths):
                    self._set_stage(job.job_id, JobStage.DESIGN_WITH_CODEX, log_path)
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "designer_task skipped by decision lock (_docs/DECISIONS.json).",
                    )
                else:
                    self._stage_design_with_codex(job, repository_path, paths, log_path)
                    self._lock_design_system_decision(repository_path, paths, log_path)
            elif node_type == "publisher_task":
                self._stage_publish_with_codex(job, repository_path, paths, log_path)
            elif node_type == "copywriter_task":
                self._stage_copywriter_with_codex(job, repository_path, paths, log_path)
            elif node_type == "documentation_task":
                self._stage_documentation_with_claude(job, repository_path, paths, log_path)
            elif node_type == "codex_implement":
                self._stage_implement_with_codex(job, repository_path, paths, log_path)
            elif node_type == "code_change_summary":
                self._stage_summarize_code_changes(job, repository_path, log_path)
            elif node_type == "test_after_implement":
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
            elif node_type == "tester_task":
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
            elif node_type == "commit_implement":
                self._stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")
            elif node_type == "gemini_review":
                self._stage_review_with_gemini(job, repository_path, paths, log_path)
            elif node_type == "product_review":
                self._stage_product_review(job, repository_path, paths, log_path)
            elif node_type == "improvement_stage":
                self._stage_improvement_stage(job, repository_path, paths, log_path)
            elif node_type == "codex_fix":
                self._stage_fix_with_codex(job, repository_path, paths, log_path)
            elif node_type == "coder_fix_from_test_report":
                self._stage_fix_with_codex(job, repository_path, paths, log_path)
            elif node_type == "test_after_fix":
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
            elif node_type == "tester_run_e2e":
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
                else:
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
            elif node_type == "ux_e2e_review":
                app_type = self._resolve_app_type(repository_path, paths)
                if app_type == "web":
                    self._stage_ux_e2e_review(job, repository_path, paths, log_path)
                else:
                    self._stage_skip_ux_review_for_non_web(job, repository_path, paths, log_path, app_type=app_type)
            elif node_type == "test_after_fix_final":
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
            elif node_type == "tester_retest_e2e":
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
                else:
                    self._run_test_gate_by_policy(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        log_path=log_path,
                        stage=JobStage.TEST_AFTER_FIX,
                        gate_label=f"tester_retest_nonweb_{app_type}",
                        app_type=app_type,
                    )
            elif node_type == "commit_fix":
                self._stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")
            elif node_type == "push_branch":
                self._stage_push_branch(job, repository_path, log_path)
            elif node_type == "create_pr":
                self._stage_create_pr(job, repository_path, paths, log_path)
            else:
                raise CommandExecutionError(f"Unsupported workflow node type: {node_type}")

            if node_type not in {"push_branch", "create_pr"}:
                self._commit_markdown_changes_after_stage(job, repository_path, node_type, log_path)

        self._set_stage(job.job_id, JobStage.FINALIZE, log_path)

    def _load_active_workflow(self, log_path: Path) -> Optional[Dict[str, Any]]:
        """Load default workflow config; fallback to fixed pipeline on any error."""

        workflow_path = Path.cwd() / "config" / "workflows.json"
        try:
            payload = load_workflows(workflow_path)
            default_id = str(payload.get("default_workflow_id", "")).strip()
            workflows = payload.get("workflows", [])
            if not default_id or not isinstance(workflows, list):
                return None
            selected = next(
                (
                    item
                    for item in workflows
                    if isinstance(item, dict) and str(item.get("workflow_id", "")).strip() == default_id
                ),
                None,
            )
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

        raw_nodes = workflow.get("nodes", [])
        raw_edges = workflow.get("edges", [])
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return []

        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", "")).strip()
            if node_id:
                nodes_by_id[node_id] = node
        if not nodes_by_id:
            return []

        entry = str(workflow.get("entry_node_id", "")).strip()
        if not entry or entry not in nodes_by_id:
            entry = next(iter(nodes_by_id.keys()))

        adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes_by_id}
        if isinstance(raw_edges, list):
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    continue
                src = str(edge.get("from", "")).strip()
                dst = str(edge.get("to", "")).strip()
                event = str(edge.get("on", "success")).strip()
                if event not in {"success", "always"}:
                    continue
                if src in adjacency and dst in nodes_by_id:
                    adjacency[src].append(dst)

        reachable: Set[str] = set()
        stack: List[str] = [entry]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for nxt in adjacency.get(node_id, []):
                if nxt not in reachable:
                    stack.append(nxt)

        indegree: Dict[str, int] = {node_id: 0 for node_id in reachable}
        for src, targets in adjacency.items():
            if src not in reachable:
                continue
            for dst in targets:
                if dst in indegree:
                    indegree[dst] += 1

        queue: List[str] = sorted([node_id for node_id, degree in indegree.items() if degree == 0])
        ordered_ids: List[str] = []
        while queue:
            current = queue.pop(0)
            ordered_ids.append(current)
            for nxt in adjacency.get(current, []):
                if nxt not in indegree:
                    continue
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        if len(ordered_ids) != len(reachable):
            return [nodes_by_id[node_id] for node_id in nodes_by_id]
        return [nodes_by_id[node_id] for node_id in ordered_ids]

    def _stage_prepare_repo(self, job: JobRecord, log_path: Path) -> Path:
        self._set_stage(job.job_id, JobStage.PREPARE_REPO, log_path)
        repository_path = self.settings.repository_workspace_path(job.repository, job.app_code)

        if not repository_path.exists():
            self._run_shell(
                command=f"gh repo clone {shlex.quote(job.repository)} {shlex.quote(str(repository_path))}",
                cwd=self.settings.workspace_dir,
                log_path=log_path,
                purpose="repository clone",
            )
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
                "--json title,body,url"
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

        return IssueDetails(
            title=str(payload.get("title", job.issue_title)),
            body=str(payload.get("body", "")),
            url=str(payload.get("url", job.issue_url)),
        )

    def _stage_write_spec(
        self,
        job: JobRecord,
        repository_path: Path,
        issue: IssueDetails,
        log_path: Path,
    ) -> Dict[str, Path]:
        self._set_stage(job.job_id, JobStage.WRITE_SPEC, log_path)

        spec_path = self._docs_file(repository_path, "SPEC.md")
        spec_json_path = self._docs_file(repository_path, "SPEC.json")
        spec_quality_path = self._docs_file(repository_path, "SPEC_QUALITY.json")
        plan_path = self._docs_file(repository_path, "PLAN.md")
        review_path = self._docs_file(repository_path, "REVIEW.md")
        design_path = self._docs_file(repository_path, "DESIGN_SYSTEM.md")
        design_tokens_path = self._docs_file(repository_path, "DESIGN_TOKENS.json")
        token_handoff_path = self._docs_file(repository_path, "TOKEN_HANDOFF.md")
        publish_checklist_path = self._docs_file(repository_path, "PUBLISH_CHECKLIST.md")
        publish_handoff_path = self._docs_file(repository_path, "PUBLISH_HANDOFF.md")
        copy_plan_path = self._docs_file(repository_path, "COPYWRITING_PLAN.md")
        copy_deck_path = self._docs_file(repository_path, "COPY_DECK.md")
        documentation_plan_path = self._docs_file(repository_path, "DOCUMENTATION_PLAN.md")
        product_brief_path = self._docs_file(repository_path, "PRODUCT_BRIEF.md")
        user_flows_path = self._docs_file(repository_path, "USER_FLOWS.md")
        mvp_scope_path = self._docs_file(repository_path, "MVP_SCOPE.md")
        architecture_plan_path = self._docs_file(repository_path, "ARCHITECTURE_PLAN.md")
        product_review_path = self._docs_file(repository_path, "PRODUCT_REVIEW.json")
        review_history_path = self._docs_file(repository_path, "REVIEW_HISTORY.json")
        improvement_backlog_path = self._docs_file(repository_path, "IMPROVEMENT_BACKLOG.json")
        improvement_loop_state_path = self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json")
        improvement_plan_path = self._docs_file(repository_path, "IMPROVEMENT_PLAN.md")
        stage_contracts_path = self._docs_file(repository_path, "STAGE_CONTRACTS.md")
        pipeline_analysis_path = self._docs_file(repository_path, "PIPELINE_ANALYSIS.md")
        readme_path = repository_path / "README.md"
        copyright_path = repository_path / "COPYRIGHT.md"
        development_guide_path = repository_path / "DEVELOPMENT_GUIDE.md"
        status_path = self._docs_file(repository_path, "STATUS.md")

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
        self._write_stage_contracts_doc(stage_contracts_path)
        self._write_pipeline_analysis_doc(pipeline_analysis_path)

        # Keep job metadata in sync with canonical issue data.
        self.store.update_job(
            job.job_id,
            issue_title=issue.title,
            issue_url=issue.url,
        )

        return {
            "spec": spec_path,
            "spec_json": spec_json_path,
            "spec_quality": spec_quality_path,
            "plan": plan_path,
            "review": review_path,
            "design": design_path,
            "design_tokens": design_tokens_path,
            "token_handoff": token_handoff_path,
            "publish_checklist": publish_checklist_path,
            "publish_handoff": publish_handoff_path,
            "copy_plan": copy_plan_path,
            "copy_deck": copy_deck_path,
            "documentation_plan": documentation_plan_path,
            "product_brief": product_brief_path,
            "user_flows": user_flows_path,
            "mvp_scope": mvp_scope_path,
            "architecture_plan": architecture_plan_path,
            "product_review": product_review_path,
            "review_history": review_history_path,
            "improvement_backlog": improvement_backlog_path,
            "improvement_loop_state": improvement_loop_state_path,
            "improvement_plan": improvement_plan_path,
            "stage_contracts": stage_contracts_path,
            "pipeline_analysis": pipeline_analysis_path,
            "readme": readme_path,
            "copyright": copyright_path,
            "development_guide": development_guide_path,
            "status": status_path,
        }

    def _stage_idea_to_product_brief(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create PRODUCT_BRIEF.md from issue/spec context."""

        self._set_stage(job.job_id, JobStage.IDEA_TO_PRODUCT_BRIEF, log_path)
        product_brief_path = paths.get("product_brief", self._docs_file(repository_path, "PRODUCT_BRIEF.md"))
        spec_json = self._read_json_file(paths.get("spec_json"))
        goal = str(spec_json.get("goal", "")).strip() or job.issue_title
        scope_in = spec_json.get("scope_in", []) if isinstance(spec_json, dict) else []
        target_users = [
            "문제를 직접 겪는 1차 사용자",
            "기능 품질을 유지보수하는 운영/개발 사용자",
        ]
        success_metrics = [
            "MVP 핵심 시나리오 1개 이상이 재현 가능해야 함",
            "테스트 리포트와 제품 리뷰 점수가 누적 저장되어야 함",
            "다음 개선 작업이 자동 우선순위로 생성되어야 함",
        ]
        lines: List[str] = [
            "# PRODUCT BRIEF",
            "",
            "## Product Goal",
            f"- {goal}",
            "",
            "## Problem Statement",
            "- 이슈 아이디어를 단발성 코드 생성이 아닌 제품 단위 개발 루프로 전환한다.",
            "",
            "## Target Users",
        ]
        lines.extend(f"- {item}" for item in target_users)
        lines.extend(
            [
                "",
                "## Core Value",
                "- 아이디어 입력부터 MVP 구현, 품질 리뷰, 반복 개선까지 한 파이프라인으로 수행한다.",
                "- 코드 생성보다 품질 평가와 개선 우선순위 결정을 시스템적으로 강제한다.",
                "",
                "## Scope Inputs",
            ]
        )
        for item in (scope_in[:7] if isinstance(scope_in, list) else []):
            if str(item).strip():
                lines.append(f"- {str(item).strip()}")
        lines.extend(
            [
                "",
                "## Success Metrics",
            ]
        )
        lines.extend(f"- {item}" for item in success_metrics)
        lines.append("")
        product_brief_path.write_text("\n".join(lines), encoding="utf-8")

    def _stage_generate_user_flows(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create USER_FLOWS.md with explicit product/user flows."""

        self._set_stage(job.job_id, JobStage.GENERATE_USER_FLOWS, log_path)
        user_flows_path = paths.get("user_flows", self._docs_file(repository_path, "USER_FLOWS.md"))
        lines = [
            "# USER FLOWS",
            "",
            "## Flow 1: 아이디어 입력 -> 제품 정의",
            "1. 사용자가 이슈/아이디어를 입력한다.",
            "2. 시스템이 PRODUCT_BRIEF.md를 생성한다.",
            "3. 목표/문제/사용자/성공지표가 합의 가능한 형태로 정리된다.",
            "",
            "## Flow 2: 제품 정의 -> MVP 구현",
            "1. USER_FLOWS.md, MVP_SCOPE.md, ARCHITECTURE_PLAN.md를 순차 생성한다.",
            "2. PLAN.md를 작성하고 범위 내 구현을 진행한다.",
            "3. 테스트/리뷰 단계로 이동 가능한 실행 산출물이 생긴다.",
            "",
            "## Flow 3: 리뷰 -> 개선 루프",
            "1. PRODUCT_REVIEW.json으로 품질 점수를 계산한다.",
            "2. IMPROVEMENT_BACKLOG.json에 우선순위 작업을 생성한다.",
            "3. IMPROVEMENT_PLAN.md로 다음 루프 전략을 확정한다.",
            "",
            "## UX State Checklist",
            "- Loading 상태: 스피너/스켈레톤/진행 메시지 존재 여부",
            "- Empty 상태: 데이터 없음 시 안내/유도 문구 존재 여부",
            "- Error 상태: 실패 사유/복구 액션/재시도 경로 존재 여부",
            "",
        ]
        user_flows_path.write_text("\n".join(lines), encoding="utf-8")

    def _stage_define_mvp_scope(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create MVP_SCOPE.md with in/out scope and acceptance gates."""

        self._set_stage(job.job_id, JobStage.DEFINE_MVP_SCOPE, log_path)
        mvp_scope_path = paths.get("mvp_scope", self._docs_file(repository_path, "MVP_SCOPE.md"))
        spec_json = self._read_json_file(paths.get("spec_json"))
        scope_in = spec_json.get("scope_in", []) if isinstance(spec_json, dict) else []
        scope_out = spec_json.get("scope_out", []) if isinstance(spec_json, dict) else []
        lines = [
            "# MVP SCOPE",
            "",
            "## In Scope",
        ]
        for item in (scope_in[:8] if isinstance(scope_in, list) else []):
            if str(item).strip():
                lines.append(f"- {str(item).strip()}")
        lines.extend(
            [
                "",
                "## Out of Scope",
            ]
        )
        for item in (scope_out[:8] if isinstance(scope_out, list) else []):
            if str(item).strip():
                lines.append(f"- {str(item).strip()}")
        lines.extend(
            [
                "",
                "## MVP Acceptance Gates",
                "- 핵심 사용자 플로우 1개 이상이 end-to-end로 동작한다.",
                "- PRODUCT_REVIEW.json이 생성되고 필수 카테고리 점수가 기록된다.",
                "- 최소 1개 테스트 리포트가 생성된다.",
                "",
                "## Post-MVP Candidate",
                "- 성능 최적화, 리팩토링, 고급 UX polish는 개선 루프에서 처리한다.",
                "",
            ]
        )
        mvp_scope_path.write_text("\n".join(lines), encoding="utf-8")

    def _stage_architecture_planning(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Create ARCHITECTURE_PLAN.md for implementation constraints."""

        self._set_stage(job.job_id, JobStage.ARCHITECTURE_PLANNING, log_path)
        architecture_plan_path = paths.get("architecture_plan", self._docs_file(repository_path, "ARCHITECTURE_PLAN.md"))
        lines = [
            "# ARCHITECTURE PLAN",
            "",
            "## Components",
            "- Product Definition Layer: PRODUCT_BRIEF.md / USER_FLOWS.md / MVP_SCOPE.md",
            "- Delivery Layer: PLAN.md / 구현 코드 / TEST_REPORT_*",
            "- Review Layer: REVIEW.md / PRODUCT_REVIEW.json",
            "- Improvement Loop Layer: REVIEW_HISTORY.json / IMPROVEMENT_BACKLOG.json / IMPROVEMENT_PLAN.md",
            "",
            "## Data Contracts",
            "- 각 단계는 `_docs` 아래 파일(또는 JSON) 산출물을 남긴다.",
            "- 다음 단계는 직전 산출물을 입력으로 사용한다.",
            "- 실패 시 STATUS.md에 중단 원인과 재개 액션을 기록한다.",
            "",
            "## Quality Gates",
            "- 설계 산출물(brief/flows/mvp/architecture) 누락 시 구현 단계 진행 금지",
            "- 제품 리뷰 점수 하락/정체/반복 이슈 발생 시 전략 변경 플래그 활성화",
            "",
            "## Loop Safety",
            "- 같은 문제 반복 제한: 동일 top issue 연속 반복 감지",
            "- 품질 점수 정체 감지: 최근 N회 개선폭 임계치 이하",
            "- 품질 하락 감지: 직전 대비 점수 하락",
            "- 복구 고려: 마지막 안정 상태(git sha) 기록",
            "",
        ]
        architecture_plan_path.write_text("\n".join(lines), encoding="utf-8")

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
        ux_review_text = self._read_text_file(self._docs_file(repository_path, "UX_REVIEW.md"))
        spec_text = self._read_text_file(paths.get("spec"))
        review_lower = review_text.lower()
        spec_lower = spec_text.lower()
        todo_penalty = min(3, len(todo_items) // 2)

        scores = {
            "code_quality": max(1, 5 - todo_penalty),
            "architecture_structure": 4 if architecture_exists else 2,
            "maintainability": 4 if mvp_scope_exists else 2,
            "usability": 4 if user_flows_exists else 2,
            "ux_clarity": 4 if ux_review_text and "실패/누락 없음" in ux_review_text else (3 if ux_review_text else 2),
            "test_coverage": max(1, 4 - min(2, test_failures)) if test_report_paths else 1,
            "error_state_handling": 4 if ("error" in spec_lower or "오류" in review_lower) else 2,
            "empty_state_handling": 4 if ("empty" in spec_lower or "빈 상태" in review_lower) else 2,
            "loading_state_handling": 4 if ("loading" in spec_lower or "로딩" in review_lower) else 2,
        }
        overall = round(sum(scores.values()) / float(len(scores)), 2)

        findings = [
            {"category": "code_quality", "summary": f"TODO 항목 {len(todo_items)}개 감지"},
            {"category": "architecture_structure", "summary": "ARCHITECTURE_PLAN.md 존재 여부 기반 평가"},
            {"category": "maintainability", "summary": "MVP/문서 산출물 존재 여부 기반 평가"},
            {"category": "usability", "summary": "USER_FLOWS.md 존재 여부 기반 평가"},
            {"category": "ux_clarity", "summary": "UX_REVIEW.md 내용 기반 평가"},
            {
                "category": "test_coverage",
                "summary": f"테스트 리포트 {len(test_report_paths)}개, 실패 {test_failures}개",
            },
            {"category": "error_state_handling", "summary": "오류 상태 안내 관련 키워드 기반 점검"},
            {"category": "empty_state_handling", "summary": "빈 상태 안내 관련 키워드 기반 점검"},
            {"category": "loading_state_handling", "summary": "로딩 상태 안내 관련 키워드 기반 점검"},
        ]

        candidates: List[Dict[str, Any]] = []
        for item in todo_items:
            priority = "P1" if any(key in item.lower() for key in ["bug", "fail", "error", "security", "crash"]) else "P2"
            candidates.append(
                {
                    "id": self._stable_issue_id(item),
                    "source": "review_todo",
                    "title": item,
                    "priority": priority,
                    "reason": "REVIEW.md TODO 항목",
                }
            )
        for category, score in scores.items():
            if score <= 2:
                candidates.append(
                    {
                        "id": self._stable_issue_id(category),
                        "source": "quality_score",
                        "title": f"{category} 점수 개선",
                        "priority": "P1",
                        "reason": f"{category} 점수 {score}/5",
                    }
                )
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            dedup[item["id"]] = item
        ordered_candidates = sorted(
            dedup.values(),
            key=lambda x: (0 if x.get("priority") == "P1" else 1, str(x.get("title", ""))),
        )

        payload = {
            "schema_version": "1.0",
            "generated_at": utc_now_iso(),
            "job_id": job.job_id,
            "review_basis": {
                "spec": str(paths.get("spec", "")),
                "plan": str(paths.get("plan", "")),
                "review": str(review_path),
            },
            "scores": {**scores, "overall": overall},
            "findings": findings,
            "improvement_candidates": ordered_candidates,
            "quality_gate": {
                "passed": overall >= 3.0,
                "reason": "overall >= 3.0",
            },
        }
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
        history_entries.append(
            {
                "generated_at": payload["generated_at"],
                "job_id": job.job_id,
                "overall": overall,
                "top_issue_ids": [item["id"] for item in ordered_candidates[:3]],
            }
        )
        review_history_path.write_text(
            json.dumps({"entries": history_entries[-30:]}, ensure_ascii=False, indent=2) + "\n",
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
        strategy_change_required = repeated_issue_limit_hit or score_stagnation_detected or quality_regression_detected

        git_head = ""
        result = self.shell_executor(
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
            "rollback": {
                "last_known_head": git_head,
                "rollback_candidate": bool(git_head),
            },
            "strategy": (
                "narrow_scope_stabilization"
                if strategy_change_required
                else "normal_iterative_improvement"
            ),
        }
        loop_state_path = paths.get("improvement_loop_state", self._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
        loop_state_path.write_text(
            json.dumps(loop_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        plan_lines = [
            "# IMPROVEMENT PLAN",
            "",
            f"- Generated at: {loop_state['generated_at']}",
            f"- Strategy: `{loop_state['strategy']}`",
            f"- Current overall score: `{review_payload.get('scores', {}).get('overall', 'n/a')}`",
            "",
            "## Loop Guard Signals",
            f"- repeated_issue_limit_hit: `{repeated_issue_limit_hit}`",
            f"- score_stagnation_detected: `{score_stagnation_detected}`",
            f"- quality_regression_detected: `{quality_regression_detected}`",
            f"- strategy_change_required: `{strategy_change_required}`",
            "",
            "## Next Improvements (Top 5)",
        ]
        for item in backlog_items[:5]:
            plan_lines.append(
                f"- [{item.get('priority', 'P2')}] {str(item.get('title', '')).strip()} ({item.get('reason', '')})"
            )
        if not backlog_items:
            plan_lines.append("- 개선 백로그 항목 없음")
        plan_lines.extend(
            [
                "",
                "## Recovery Option",
                f"- last_known_head: `{git_head or 'unavailable'}`",
                "- 전략 변경이 필요하면 범위를 축소하고 안정화 작업을 우선 수행한다.",
                "",
            ]
        )
        improvement_plan_path = paths.get("improvement_plan", self._docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
        improvement_plan_path.write_text("\n".join(plan_lines), encoding="utf-8")

    def _stage_plan_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self._set_stage(job.job_id, JobStage.COPYWRITER_TASK, log_path)

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
        review_ready = paths["review"].exists() and bool(
            paths["review"].read_text(encoding="utf-8", errors="replace").strip()
        )
        planner_prompt_path.write_text(
            build_planner_prompt(
                str(paths["spec"]),
                str(paths["plan"]),
                review_path=str(paths["review"]),
                is_long_term=self._is_long_track(self._require_job(job.job_id)),
                is_refinement_round=review_ready,
                planning_mode=planning_mode,
            ),
            encoding="utf-8",
        )
        result = self.command_templates.run_template(
            template_name=self._template_for_profile("planner"),
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

        review_ready = paths["review"].exists() and bool(
            paths["review"].read_text(encoding="utf-8", errors="replace").strip()
        )
        base_prompt = build_planner_prompt(
            str(paths["spec"]),
            str(paths["plan"]),
            review_path=str(paths["review"]),
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
                    template_name=self._template_for_profile("planner"),
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
    def _parse_planner_tool_request(plan_text: str) -> Optional[Dict[str, str]]:
        """Parse planner TOOL_REQUEST block from PLAN output."""

        text = str(plan_text or "").strip()
        if not text:
            return None
        block_match = re.search(
            r"\[TOOL_REQUEST\](.*?)\[/TOOL_REQUEST\]",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        payload = block_match.group(1) if block_match else text

        tool_match = re.search(r"^\s*tool\s*:\s*([a-zA-Z0-9_\-]+)\s*$", payload, flags=re.IGNORECASE | re.MULTILINE)
        query_match = re.search(r"^\s*query\s*:\s*(.+?)\s*$", payload, flags=re.IGNORECASE | re.MULTILINE)
        reason_match = re.search(r"^\s*reason\s*:\s*(.+?)\s*$", payload, flags=re.IGNORECASE | re.MULTILINE)
        if not tool_match or not query_match:
            return None

        tool = tool_match.group(1).strip().lower()
        query = query_match.group(1).strip()
        reason = reason_match.group(1).strip() if reason_match else ""
        if tool != "research_search" or not query:
            return None
        return {"tool": tool, "query": query[:240], "reason": reason[:240]}

    def _execute_planner_tool_request(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        tool_request: Dict[str, str],
    ) -> Dict[str, Any]:
        """Execute planner-requested research_search with robust fallback."""

        query = str(tool_request.get("query", "")).strip()
        search_context_path = self._docs_file(repository_path, "SEARCH_CONTEXT.md")
        search_result_path = self._docs_file(repository_path, "SEARCH_RESULT.json")
        prompt_path = self._docs_file(repository_path, "PLANNER_TOOL_REQUEST.md")
        prompt_path.write_text(
            (
                "# Planner Tool Request\n\n"
                f"- tool: research_search\n"
                f"- query: {query}\n"
                f"- reason: {tool_request.get('reason', '')}\n"
            ),
            encoding="utf-8",
        )

        variables = self._build_template_variables(job, paths, prompt_path)
        variables["query"] = query
        try:
            self.command_templates.run_template(
                template_name=self._template_for_profile("research_search"),
                variables=variables,
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "PLANNER"),
            )
            legacy_context_path = repository_path / "SEARCH_CONTEXT.md"
            legacy_result_path = repository_path / "SEARCH_RESULT.json"
            if not search_context_path.exists() and legacy_context_path.exists():
                search_context_path.write_text(
                    legacy_context_path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            if not search_result_path.exists() and legacy_result_path.exists():
                search_result_path.write_text(
                    legacy_result_path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            context_text = ""
            if search_context_path.exists():
                context_text = search_context_path.read_text(encoding="utf-8", errors="replace").strip()
            if not context_text:
                context_text = "검색 도구가 실행되었지만 SEARCH_CONTEXT.md 본문이 비어 있습니다."
            return {
                "ok": True,
                "mode": "search_api",
                "context_path": str(search_context_path),
                "result_path": str(search_result_path),
                "context_text": context_text[:20_000],
            }
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"research_search failed. Fallback to local evidence pack: {error}",
            )
            fallback = self._build_local_evidence_fallback(repository_path, paths, query, str(error))
            search_context_path.write_text(fallback["context_text"], encoding="utf-8")
            search_result_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "mode": "fallback_local",
                        "query": query,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            return {
                "ok": False,
                "mode": "fallback_local",
                "context_path": str(search_context_path),
                "result_path": str(search_result_path),
                "context_text": fallback["context_text"][:20_000],
            }

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
        tool_request: Dict[str, str],
        outcome: Dict[str, Any],
    ) -> str:
        """Build addendum prompt after tool execution."""

        mode = str(outcome.get("mode", "unknown"))
        context_path = str(outcome.get("context_path", "SEARCH_CONTEXT.md"))
        context_text = str(outcome.get("context_text", "")).strip()
        return (
            "\n\n[Tool response context]\n"
            f"- requested_tool: {tool_request.get('tool', '')}\n"
            f"- query: {tool_request.get('query', '')}\n"
            f"- mode: {mode}\n"
            f"- context_file: {context_path}\n"
            "- 아래 근거를 반영해 TOOL_REQUEST가 아닌 최종 PLAN.md 본문을 작성하세요.\n\n"
            f"{context_text}\n"
        )

    def _stage_implement_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)

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
            ),
            encoding="utf-8",
        )

        self.command_templates.run_template(
            template_name=self._template_for_profile("coder"),
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
            template_name=self._template_for_profile("coder"),
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
            template_name=self._template_for_profile("coder"),
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

        self._set_stage(job.job_id, JobStage.PLAN_WITH_GEMINI, log_path)
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
            template_name=self._template_for_profile("coder"),
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
        """Run documentation stage with Claude first, then Codex fallback."""

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

        claude_templates = ["documentation_writer", "pr_summary", "commit_summary", "escalation"]
        claude_error: Optional[str] = None
        bundle_applied = False
        for template_name in claude_templates:
            resolved_template = self._template_for_profile(template_name)
            if not self.command_templates.has_template(resolved_template):
                continue
            claude_vars = {
                **self._build_template_variables(job, paths, prompt_path),
                "docs_bundle_path": str(bundle_path),
                "pr_summary_path": str(bundle_path),
                "commit_message_path": str(bundle_path),
            }
            try:
                result = self.command_templates.run_template(
                    template_name=resolved_template,
                    variables=claude_vars,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "TECH_WRITER_CLAUDE"),
                )
                if not bundle_path.exists() and str(result.stdout).strip():
                    bundle_path.write_text(str(result.stdout).strip() + "\n", encoding="utf-8")
                bundle_applied = self._apply_documentation_bundle(repository_path, bundle_path, paths, log_path)
                if bundle_applied:
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Documentation generated by Claude template: {resolved_template}",
                    )
                    break
            except CommandExecutionError as error:
                claude_error = str(error)

        if not bundle_applied:
            if claude_error:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Claude documentation step failed. Fallback to Codex: {claude_error}",
                )
            else:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Claude documentation template unavailable or output invalid. Fallback to Codex.",
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
                template_name=self._template_for_profile("coder"),
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
        self._set_stage(job.job_id, stage, log_path)
        test_results: List[Dict[str, Any]] = []
        primary_command = self._resolve_test_command(stage, secondary=False)
        primary_command = self._wrap_test_command_with_timeout(primary_command, log_path)

        primary_name = self.settings.tester_primary_name
        primary_result = self.shell_executor(
            command=primary_command,
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, f"TESTER_{self._safe_slug(primary_name).upper()}"),
            check=False,
            command_purpose=f"tests ({stage.value}) [{primary_name}]",
        )
        primary_report = self._write_test_report(
            repository_path=repository_path,
            stage=stage,
            command_result=primary_result,
            tester_name=primary_name,
            report_suffix="",
        )
        self._append_actor_log(
            log_path,
            f"TESTER_{self._safe_slug(primary_name).upper()}",
            f"Test report written: {primary_report.name}",
        )
        test_results.append({"name": primary_name, "result": primary_result, "report": primary_report})

        if self._is_long_track(job):
            secondary_command = self._resolve_test_command(stage, secondary=True)
            secondary_command = self._wrap_test_command_with_timeout(secondary_command, log_path)
            secondary_name = self.settings.tester_secondary_name
            secondary_result = self.shell_executor(
                command=secondary_command,
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, f"TESTER_{self._safe_slug(secondary_name).upper()}"),
                check=False,
                command_purpose=f"tests ({stage.value}) [{secondary_name}]",
            )
            secondary_report = self._write_test_report(
                repository_path=repository_path,
                stage=stage,
                command_result=secondary_result,
                tester_name=secondary_name,
                report_suffix=self._safe_slug(secondary_name).upper(),
            )
            self._append_actor_log(
                log_path,
                f"TESTER_{self._safe_slug(secondary_name).upper()}",
                f"Test report written: {secondary_report.name}",
            )
            test_results.append({"name": secondary_name, "result": secondary_result, "report": secondary_report})

        failed_reports = [
            str(item["report"].name)
            for item in test_results
            if int(getattr(item["result"], "exit_code", 1)) != 0
        ]
        if failed_reports:
            reason = (
                f"Tests failed at stage '{stage.value}'. "
                f"See {', '.join(failed_reports)} and job logs for details."
            )
            self._write_test_failure_reason(
                repository_path=repository_path,
                stage=stage,
                reason=reason,
            )
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{reason} Continuing workflow by policy.",
            )
            return False
        return True

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
        """Run test gate with bounded retry/timebox and repeated-error detection."""

        max_attempts = self._hard_gate_max_attempts()
        timebox_seconds = self._hard_gate_timebox_seconds()
        start = time.monotonic()
        signatures: Dict[str, int] = {}

        for attempt in range(1, max_attempts + 1):
            passed = self._stage_run_tests(job, repository_path, stage, log_path)
            if passed:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[HARD_GATE:{gate_label}] passed on attempt {attempt}/{max_attempts}",
                )
                return

            signature = self._latest_test_failure_signature(repository_path, stage)
            if signature:
                signatures[signature] = signatures.get(signature, 0) + 1

            elapsed = int(time.monotonic() - start)
            if elapsed >= timebox_seconds:
                # Timeout is treated as non-fatal by policy. We analyze and continue.
                self._run_failure_assistant(
                    job=job,
                    repository_path=repository_path,
                    log_path=log_path,
                    reason=(
                        f"Hard gate timeout at {gate_label} ({elapsed}s/{timebox_seconds}s). "
                        "Do not fail the run. Summarize root cause and next unblock actions."
                    ),
                )
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[SOFT_TIMEOUT:{gate_label}] timeout reached ({elapsed}s). Continuing workflow by policy.",
                )
                return
            if signature and signatures.get(signature, 0) >= 2:
                if self._is_recovery_mode_enabled():
                    recovered = self._try_recovery_flow(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        log_path=log_path,
                        stage=stage,
                        gate_label=gate_label,
                        reason=(
                            f"Hard gate repeated failure signature at {gate_label}. "
                            "Analyze recoverability and attempt one recovery cycle."
                        ),
                    )
                    if recovered:
                        return
                    self._append_actor_log(log_path, "ORCHESTRATOR", f"[RECOVERY_MODE:{gate_label}] not recovered. Continuing workflow by policy.")
                    return
                self._run_failure_assistant(
                    job=job,
                    repository_path=repository_path,
                    log_path=log_path,
                    reason=(
                        f"Hard gate repeated failure signature at {gate_label}. "
                        "Summarize root cause and concrete fix plan."
                    ),
                )
                raise CommandExecutionError(
                    f"Hard gate '{gate_label}' stopped due to repeated failure signature. "
                    "Next action: resolve root cause before retrying."
                )
            if attempt >= max_attempts:
                break

            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[HARD_GATE:{gate_label}] failed attempt {attempt}/{max_attempts}. Running fix and retry.",
            )
            self._stage_fix_with_codex(job, repository_path, paths, log_path)
            self._commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.FIX_WITH_CODEX.value,
                log_path,
            )

        if self._is_recovery_mode_enabled():
            recovered = self._try_recovery_flow(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=stage,
                gate_label=gate_label,
                reason=(
                    f"Hard gate max attempts reached at {gate_label}. "
                    "Analyze recoverability and attempt one recovery cycle."
                ),
            )
            if recovered:
                return
            self._append_actor_log(log_path, "ORCHESTRATOR", f"[RECOVERY_MODE:{gate_label}] not recovered. Continuing workflow by policy.")
            return
        raise CommandExecutionError(
            f"Hard gate '{gate_label}' failed after {max_attempts} attempts. "
            "Next action: inspect test reports and apply targeted fix."
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
        """Run hard/soft test gate by policy. Default keeps non-web as soft gate."""

        # Default policy keeps legacy behavior: do not stop pipeline on test gate failure.
        policy = (os.getenv("AGENTHUB_TEST_GATE_POLICY", "soft") or "soft").strip().lower()
        use_hard_gate = policy == "hard" or (policy == "mixed" and (app_type or "").strip().lower() == "web")
        if policy in {"soft", "continue"}:
            use_hard_gate = False

        if use_hard_gate:
            self._run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=stage,
                gate_label=gate_label,
            )
            return

        passed = self._stage_run_tests(job, repository_path, stage, log_path)
        if not passed:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[SOFT_GATE:{gate_label}] test failed but continuing by policy.",
            )
            if self._is_recovery_mode_enabled():
                recovered = self._try_recovery_flow(
                    job=job,
                    repository_path=repository_path,
                    paths=paths,
                    log_path=log_path,
                    stage=stage,
                    gate_label=gate_label,
                    reason=(
                        f"Soft gate failure at {gate_label}. "
                        "Analyze recoverability and attempt one recovery cycle."
                    ),
                )
                if recovered:
                    return
            self._run_failure_assistant(
                job=job,
                repository_path=repository_path,
                log_path=log_path,
                reason=(
                    f"Soft gate failure at {gate_label}. Workflow continues by policy. "
                    "Analyze probable root cause and recommend next fixes."
                ),
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
        """Analyze recoverability and run one fix+retest cycle when worth trying."""

        self._run_failure_assistant(
            job=job,
            repository_path=repository_path,
            log_path=log_path,
            reason=reason,
        )
        if not self._is_recoverable_failure(repository_path, stage):
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[RECOVERY_MODE:{gate_label}] not recoverable by heuristic. Skip auto-recovery.",
            )
            return False
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[RECOVERY_MODE:{gate_label}] recoverable. Running fix + retest once.",
        )
        self._stage_fix_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job,
            repository_path,
            JobStage.FIX_WITH_CODEX.value,
            log_path,
        )
        passed = self._stage_run_tests(job, repository_path, stage, log_path)
        if passed:
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[RECOVERY_MODE:{gate_label}] recovery succeeded.",
            )
            return True
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"[RECOVERY_MODE:{gate_label}] recovery attempt failed.",
        )
        return False

    @staticmethod
    def _is_recoverable_failure(repository_path: Path, stage: JobStage) -> bool:
        """Cheap heuristic for auto-recovery eligibility."""

        reason_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}.md"
        text = ""
        if reason_path.exists():
            text += "\n" + reason_path.read_text(encoding="utf-8", errors="replace")
        if report_path.exists():
            text += "\n" + report_path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if any(token in lowered for token in ["auth", "permission denied", "rate limit", "quota", "repository not found", "dns", "network is unreachable"]):
            return False
        if any(token in lowered for token in ["test failed", "lint", "type error", "module not found", "assert", "failed"]):
            return True
        return bool(lowered.strip())

    def _run_failure_assistant(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        reason: str,
    ) -> None:
        """Run copilot/escalation helper on failure and persist analysis markdown."""

        prompt_path = self._docs_file(repository_path, "FAILURE_ANALYSIS_PROMPT.md")
        output_path = self._docs_file(repository_path, "FAILURE_ANALYSIS.md")
        prompt_path.write_text(
            (
                "실패 원인 분석을 작성하세요.\n"
                "- 한국어\n"
                "- 재현 단서 3개 이내\n"
                "- 근본 원인(가설) 1~3개\n"
                "- 즉시 조치 3개(명령/파일 기준)\n"
                "- 다음 라운드 체크리스트\n\n"
                f"job_id: {job.job_id}\n"
                f"issue: #{job.issue_number}\n"
                f"reason: {reason}\n"
            ),
            encoding="utf-8",
        )

        if self.command_templates.has_template("copilot"):
            try:
                result = self.command_templates.run_template(
                    template_name=self._template_for_profile("copilot"),
                    variables=self._build_template_variables(
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
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "COPILOT"),
                )
                analysis = str(getattr(result, "stdout", "")).strip()
                if analysis:
                    output_path.write_text(analysis + "\n", encoding="utf-8")
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Failure analysis written: {output_path.name}",
                    )
                    return
            except Exception as error:  # noqa: BLE001
                self._append_actor_log(log_path, "ORCHESTRATOR", f"Failure assistant failed: {error}")

        if self._is_escalation_enabled() and self.command_templates.has_template("escalation"):
            self._run_optional_escalation(job.job_id, log_path, reason)

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
        """Read hard-gate max attempts from env with safe bounds."""

        raw = (os.getenv("AGENTHUB_HARD_GATE_MAX_ATTEMPTS", "3") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 3
        return max(1, min(5, value))

    @staticmethod
    def _hard_gate_timebox_seconds() -> int:
        """Read hard-gate timebox seconds from env with safe bounds."""

        raw = (os.getenv("AGENTHUB_HARD_GATE_TIMEBOX_SECONDS", "1200") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 1200
        return max(120, min(7200, value))

    def _latest_test_failure_signature(self, repository_path: Path, stage: JobStage) -> str:
        """Build compact signature from latest failure reason/report text."""

        reason_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        text = ""
        if reason_path.exists():
            try:
                text = reason_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        if not text:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip().lower()[:600]
        return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]

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
        """Run codex_fix -> test_after_fix loop up to 3 rounds after E2E failure."""

        max_rounds = 3
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Entering fix/test retry loop after E2E failure. max_rounds={max_rounds}",
        )
        for round_index in range(1, max_rounds + 1):
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"[FIX_LOOP] Round {round_index}/{max_rounds} start",
            )
            self._stage_fix_with_codex(job, repository_path, paths, log_path)
            self._commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.FIX_WITH_CODEX.value,
                log_path,
            )
            passed = self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
            self._commit_markdown_changes_after_stage(
                job,
                repository_path,
                JobStage.TEST_AFTER_FIX.value,
                log_path,
            )
            if passed:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[FIX_LOOP] Round {round_index} succeeded. Proceeding to review stage.",
                )
                return

        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            "[FIX_LOOP] Reached max rounds with remaining failures. Proceeding by policy.",
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
                "COPILOT",
                f"Wrote code change summary via Copilot: {summary_path.name}",
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
        """Try Copilot CLI summary generation and return markdown text."""

        prompt_path = self._docs_file(repository_path, "COPILOT_SUMMARY_PROMPT.md")
        prompt_path.write_text(prompt, encoding="utf-8")

        if self.command_templates.has_template("copilot"):
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
                    template_name=self._template_for_profile("copilot"),
                    variables=template_variables,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "COPILOT"),
                )
            except Exception as error:  # noqa: BLE001 - fallback to built-in command
                self._append_actor_log(
                    log_path,
                    "COPILOT",
                    f"Copilot template failed. Fallback to built-in command: {error}",
                )
                result = self.shell_executor(
                    command=f"gh copilot -p {shlex.quote(prompt)}",
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "COPILOT"),
                    check=False,
                    command_purpose="copilot code change summary fallback",
                )
        else:
            command = f"gh copilot -p {shlex.quote(prompt)}"
            result = self.shell_executor(
                command=command,
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "COPILOT"),
                check=False,
                command_purpose="copilot code change summary",
            )
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

        report_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        content = [
            "# TEST FAILURE REASON",
            "",
            f"- Stage: `{stage.value}`",
            f"- Reason: {reason}",
            "",
            "## Next Step",
            "- Continue workflow and let following stages address issues.",
            "",
        ]
        report_path.write_text("\n".join(content), encoding="utf-8")

    def _resolve_test_command(self, stage: JobStage, secondary: bool) -> str:
        """Pick stage-aware tester command with conservative fallbacks."""

        if stage == JobStage.TEST_AFTER_IMPLEMENT:
            if secondary:
                return (
                    self.settings.test_command_secondary_implement
                    or self.settings.test_command_secondary
                    or self.settings.test_command
                )
            return self.settings.test_command_implement or self.settings.test_command

        if stage == JobStage.TEST_AFTER_FIX:
            if secondary:
                return (
                    self.settings.test_command_secondary_fix
                    or self.settings.test_command_secondary
                    or self.settings.test_command
                )
            return self.settings.test_command_fix or self.settings.test_command

        if stage == JobStage.UX_E2E_REVIEW:
            if secondary:
                return (
                    self.settings.test_command_secondary_fix
                    or self.settings.test_command_secondary
                    or self.settings.test_command
                )
            return self.settings.test_command_fix or self.settings.test_command

        if secondary:
            return self.settings.test_command_secondary or self.settings.test_command
        return self.settings.test_command

    def _wrap_test_command_with_timeout(self, command: str, log_path: Path) -> str:
        """Wrap test command with shell timeout when available."""

        timeout_seconds = self._test_command_timeout_seconds()
        if timeout_seconds <= 0:
            return command
        if not self._has_timeout_utility():
            self._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "timeout utility not found. Running tests without process-level timeout wrapper.",
            )
            return command
        return f"timeout --preserve-status {timeout_seconds}s {command}"

    @staticmethod
    def _has_timeout_utility() -> bool:
        """Return True when GNU/BSD timeout utility is available."""

        return shutil.which("timeout") is not None

    @staticmethod
    def _test_command_timeout_seconds() -> int:
        """Read per-test-command timeout in seconds (0 disables wrapping)."""

        raw = (os.getenv("AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS", "900") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 900
        return max(0, min(7200, value))

    def _write_test_report(
        self,
        repository_path: Path,
        stage: JobStage,
        command_result: object,
        tester_name: str,
        report_suffix: str,
    ) -> Path:
        """Persist stage-level test summary in markdown for dashboard visibility."""

        command = str(getattr(command_result, "command", self.settings.test_command))
        exit_code = int(getattr(command_result, "exit_code", 1))
        duration = float(getattr(command_result, "duration_seconds", 0.0))
        stdout = str(getattr(command_result, "stdout", ""))
        stderr = str(getattr(command_result, "stderr", ""))
        passed = exit_code == 0

        counters = self._extract_test_counters(stdout + "\n" + stderr)
        passed_count = counters.get("passed", 0)
        failed_count = counters.get("failed", 0)
        skipped_count = counters.get("skipped", 0)
        errors_count = counters.get("errors", 0)

        pass_lines: List[str] = []
        fail_lines: List[str] = []
        if passed:
            pass_lines.append("테스트 명령이 종료코드 0으로 완료되었습니다.")
        else:
            fail_lines.append(f"테스트 명령이 종료코드 {exit_code}로 실패했습니다.")
            if exit_code == 124:
                fail_lines.append(
                    "테스트 명령이 시간 제한으로 종료되었습니다(timeout, exit 124)."
                )
        if passed_count > 0:
            pass_lines.append(f"통과된 테스트 수를 감지했습니다: {passed_count}")
        if skipped_count > 0:
            pass_lines.append(f"스킵된 테스트 수를 감지했습니다: {skipped_count}")
        if failed_count > 0:
            fail_lines.append(f"실패한 테스트 수를 감지했습니다: {failed_count}")
        if errors_count > 0:
            fail_lines.append(f"에러 테스트 수를 감지했습니다: {errors_count}")
        if not pass_lines:
            pass_lines.append("출력에서 명시적인 통과 카운트를 찾지 못했습니다.")
        if not fail_lines:
            fail_lines.append("출력에서 명시적인 실패 카운트를 찾지 못했습니다.")

        report = [
            "# TEST REPORT",
            "",
            f"- Stage: `{stage.value}`",
            f"- Tester: `{tester_name}`",
            f"- Status: `{'PASS' if passed else 'FAIL'}`",
            f"- Exit code: `{exit_code}`",
            f"- Duration: `{duration:.2f}s`",
            f"- Command: `{command}`",
            "",
            "## 통과한 항목",
        ]
        report.extend(f"- {line}" for line in pass_lines)
        report.append("")
        report.append("## 통과하지 못한 항목")
        report.extend(f"- {line}" for line in fail_lines)
        report.append("")
        report.append("## 요약 카운트")
        report.append(f"- passed: `{passed_count}`")
        report.append(f"- failed: `{failed_count}`")
        report.append(f"- skipped: `{skipped_count}`")
        report.append(f"- errors: `{errors_count}`")
        report.append("")
        report.append("## stdout (tail)")
        report.append("```text")
        report.append(self._tail_text(stdout, 120))
        report.append("```")
        report.append("")
        report.append("## stderr (tail)")
        report.append("```text")
        report.append(self._tail_text(stderr, 120))
        report.append("```")
        report.append("")

        if report_suffix:
            report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}_{report_suffix}.md"
        else:
            report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}.md"
        report_path.write_text("\n".join(report), encoding="utf-8")
        return report_path

    @staticmethod
    def _extract_test_counters(text: str) -> Dict[str, int]:
        """Extract common test counters from pytest/jest/vitest-like outputs."""

        lowered = text.lower()
        counters: Dict[str, int] = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        }
        for key, pattern in {
            "passed": r"(\d+)\s+passed",
            "failed": r"(\d+)\s+failed",
            "skipped": r"(\d+)\s+skipped",
            "errors": r"(\d+)\s+errors?",
        }.items():
            matches = re.findall(pattern, lowered)
            if matches:
                counters[key] = int(matches[-1])
        return counters

    @staticmethod
    def _tail_text(text: str, max_lines: int) -> str:
        """Return only tail lines so report size stays readable."""

        stripped = text.strip()
        if not stripped:
            return "(empty)"
        lines = stripped.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def _safe_slug(value: str) -> str:
        """Convert label text to safe uppercase slug."""

        cleaned = "".join(ch if ch.isalnum() else "_" for ch in (value or "").strip().lower())
        normalized = re.sub(r"_+", "_", cleaned).strip("_")
        return normalized or "tester"

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
        """Generate one-line commit summary with Copilot-first strategy."""

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
        """Generate one-line Korean commit summary using Claude templates."""

        template_name = ""
        if self.command_templates.has_template("commit_summary"):
            template_name = "commit_summary"
        elif self.command_templates.has_template("pr_summary"):
            template_name = "pr_summary"
        elif self.command_templates.has_template("escalation"):
            template_name = "escalation"
        else:
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
                template_name=self._template_for_profile(template_name),
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
                log_writer=self._actor_log_writer(log_path, "CLAUDE"),
            )
        except Exception as error:  # noqa: BLE001
            self._append_actor_log(
                log_path,
                "CLAUDE",
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
        """Try to generate one-line commit summary with Copilot."""

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
        prompt_path = self._docs_file(repository_path, f"COPILOT_COMMIT_PROMPT_{stage_name.upper()}.md")
        prompt_path.write_text(prompt, encoding="utf-8")

        if self.command_templates.has_template("copilot"):
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
                    template_name=self._template_for_profile("copilot"),
                    variables=template_variables,
                    cwd=repository_path,
                    log_writer=self._actor_log_writer(log_path, "COPILOT"),
                )
            except Exception as error:  # noqa: BLE001
                self._append_actor_log(
                    log_path,
                    "COPILOT",
                    f"Copilot commit summary template failed: {error}",
                )
                return ""
        else:
            result = self.shell_executor(
                command=f"gh copilot -p {shlex.quote(prompt)}",
                cwd=repository_path,
                log_writer=self._actor_log_writer(log_path, "COPILOT"),
                check=False,
                command_purpose="copilot commit summary",
            )

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

        reviewer_prompt_path = self._docs_file(repository_path, "REVIEWER_PROMPT.md")
        reviewer_prompt_path.write_text(
            build_reviewer_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
            ),
            encoding="utf-8",
        )

        result = self.command_templates.run_template(
            template_name=self._template_for_profile("reviewer"),
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

        coder_prompt_path = self._docs_file(repository_path, "CODER_PROMPT_FIX.md")
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal="REVIEW.md TODO 반영 및 테스트 안정화",
                design_path=str(paths.get("design", "")),
                design_tokens_path=str(paths.get("design_tokens", self._docs_file(repository_path, "DESIGN_TOKENS.json"))),
                token_handoff_path=str(paths.get("token_handoff", self._docs_file(repository_path, "TOKEN_HANDOFF.md"))),
                publish_handoff_path=str(paths.get("publish_handoff", self._docs_file(repository_path, "PUBLISH_HANDOFF.md"))),
            ),
            encoding="utf-8",
        )

        self.command_templates.run_template(
            template_name=self._template_for_profile("coder"),
            variables=self._build_template_variables(job, paths, coder_prompt_path),
            cwd=repository_path,
            log_writer=self._actor_log_writer(log_path, "CODER"),
        )

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
                        f"Closes #{refreshed_job.issue_number}\n"
                    ),
                    encoding="utf-8",
                )
        else:
            pr_body_path.write_text(
                (
                    "## Summary\n"
                    "- Automated by AgentHub worker\n"
                    "- Generated from deterministic stage pipeline\n\n"
                    f"Closes #{refreshed_job.issue_number}\n"
                    ),
                    encoding="utf-8",
                )

        self._append_preview_section_to_pr_body(pr_body_path, preview_info)

        title = f"AgentHub: {refreshed_job.issue_title}"
        create_command = (
            f"gh pr create --repo {shlex.quote(job.repository)} "
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
                    f"gh pr edit --repo {shlex.quote(job.repository)} "
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

            self.shell_executor(
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
                f"gh pr view --repo {shlex.quote(job.repository)} "
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
        """Generate PR summary markdown with Claude before PR creation."""

        template_name = ""
        if self.command_templates.has_template("pr_summary"):
            template_name = "pr_summary"
        elif self.command_templates.has_template("escalation"):
            # backward compatibility: reuse claude escalation template when pr_summary is absent
            template_name = "escalation"
        else:
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

        self._append_actor_log(log_path, "ORCHESTRATOR", "Running Claude PR summary template.")
        try:
            result = self.command_templates.run_template(
                template_name=self._template_for_profile(template_name),
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
                log_writer=self._actor_log_writer(log_path, "CLAUDE"),
            )
            if not output_path.exists() and result.stdout.strip():
                output_path.write_text(result.stdout, encoding="utf-8")
            if output_path.exists():
                self._append_actor_log(
                    log_path,
                    "CLAUDE",
                    f"PR summary written: {output_path.name}",
                )
                return output_path
            self._append_actor_log(
                log_path,
                "CLAUDE",
                "PR summary output missing; fallback to default PR body.",
            )
            return None
        except Exception as error:  # noqa: BLE001 - summary should not block PR creation
            self._append_actor_log(
                log_path,
                "CLAUDE",
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
            "_docs/COPILOT_",
            "_docs/PR_SUMMARY_PROMPT",
            "_docs/COMMIT_MESSAGE_PROMPT_",
            "_docs/PLANNER_TOOL_REQUEST",
            "_docs/ESCALATION_PROMPT",
            "_docs/DOCUMENTATION_PROMPT",
            "_docs/DOCUMENTATION_BUNDLE",
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
        """Run optional escalation template (for example Claude) after a failure."""

        job = self._require_job(job_id)
        repository_path = self.settings.repository_workspace_path(job.repository, job.app_code)
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
                template_name=self._template_for_profile("escalation"),
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
        repository_path = self.settings.repository_workspace_path(job.repository, job.app_code)
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
            )

            create_result = self._run_shell(
                command=(
                    f"gh pr create --draft --repo {shlex.quote(job.repository)} "
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
            "issue_number": str(job.issue_number),
            "issue_title": job.issue_title,
            "issue_url": job.issue_url,
            "branch_name": job.branch_name,
            "work_dir": str(self.settings.repository_workspace_path(job.repository, job.app_code)),
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
            "product_review_json_path": str(paths.get("product_review", Path("_docs/PRODUCT_REVIEW.json"))),
            "review_history_path": str(paths.get("review_history", Path("_docs/REVIEW_HISTORY.json"))),
            "improvement_backlog_path": str(paths.get("improvement_backlog", Path("_docs/IMPROVEMENT_BACKLOG.json"))),
            "improvement_loop_state_path": str(paths.get("improvement_loop_state", Path("_docs/IMPROVEMENT_LOOP_STATE.json"))),
            "improvement_plan_path": str(paths.get("improvement_plan", Path("_docs/IMPROVEMENT_PLAN.md"))),
            "readme_path": str(paths.get("readme", Path("README.md"))),
            "copyright_path": str(paths.get("copyright", Path("COPYRIGHT.md"))),
            "development_guide_path": str(paths.get("development_guide", Path("DEVELOPMENT_GUIDE.md"))),
            "docs_bundle_path": str(self._docs_file(self.settings.repository_workspace_path(job.repository, job.app_code), "DOCUMENTATION_BUNDLE.md")),
            "status_path": str(paths["status"]),
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
    def _write_stage_contracts_doc(path: Path) -> None:
        """Persist stage IO/success/failure contracts for product-dev pipeline."""

        if path.exists():
            return
        content = (
            "# STAGE CONTRACTS\n\n"
            "## idea_to_product_brief\n"
            "- 입력: SPEC.md, SPEC.json, issue metadata\n"
            "- 출력: PRODUCT_BRIEF.md\n"
            "- 성공 조건: 목표/문제/사용자/가치/지표 섹션 존재\n"
            "- 실패 조건: 파일 미생성 또는 핵심 섹션 누락\n"
            "- 다음 단계 전달 데이터: goal, target_users, constraints\n\n"
            "## generate_user_flows\n"
            "- 입력: PRODUCT_BRIEF.md\n"
            "- 출력: USER_FLOWS.md\n"
            "- 성공 조건: 최소 3개 흐름 + UX 상태 체크리스트\n"
            "- 실패 조건: 흐름 단계 정의 부재\n"
            "- 다음 단계 전달 데이터: flow list, UX states\n\n"
            "## define_mvp_scope\n"
            "- 입력: PRODUCT_BRIEF.md, USER_FLOWS.md, SPEC.json\n"
            "- 출력: MVP_SCOPE.md\n"
            "- 성공 조건: in-scope / out-of-scope / acceptance gates 명시\n"
            "- 실패 조건: 범위 구분 미정의\n"
            "- 다음 단계 전달 데이터: 구현 범위, 비범위, 게이트\n\n"
            "## architecture_planning\n"
            "- 입력: MVP_SCOPE.md, USER_FLOWS.md\n"
            "- 출력: ARCHITECTURE_PLAN.md\n"
            "- 성공 조건: 컴포넌트/데이터계약/품질게이트/루프안전 정의\n"
            "- 실패 조건: 품질 게이트/루프 가드 누락\n"
            "- 다음 단계 전달 데이터: 아키텍처 제약과 품질 정책\n\n"
            "## product_review\n"
            "- 입력: REVIEW.md, TEST_REPORT_*.md, UX_REVIEW.md, ARCHITECTURE_PLAN.md\n"
            "- 출력: PRODUCT_REVIEW.json, REVIEW_HISTORY.json, IMPROVEMENT_BACKLOG.json\n"
            "- 성공 조건: 9개 품질 카테고리 점수 + 개선 후보 생성\n"
            "- 실패 조건: 필수 카테고리 누락 또는 JSON 구조 파손\n"
            "- 다음 단계 전달 데이터: overall score, top issues, backlog\n\n"
            "## improvement_stage\n"
            "- 입력: PRODUCT_REVIEW.json, REVIEW_HISTORY.json, IMPROVEMENT_BACKLOG.json\n"
            "- 출력: IMPROVEMENT_LOOP_STATE.json, IMPROVEMENT_PLAN.md\n"
            "- 성공 조건: 반복/정체/하락 감지 및 전략 결정\n"
            "- 실패 조건: 루프 가드 계산 불가\n"
            "- 다음 단계 전달 데이터: next strategy, top priorities, rollback candidate\n"
        )
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_pipeline_analysis_doc(path: Path) -> None:
        """Persist phase-1 pipeline analysis and missing-stage summary."""

        if path.exists():
            return
        content = (
            "# PIPELINE ANALYSIS\n\n"
            "## 현재 구조 요약\n"
            "- 기존 파이프라인은 issue -> spec -> plan -> implement -> test -> review -> fix 중심.\n"
            "- 제품 정의(사용자 흐름, MVP 경계, 아키텍처 의사결정)가 코드 생성 전에 충분히 분리되지 않음.\n"
            "- 리뷰 결과를 다음 개선 작업으로 구조화해 넘기는 계약이 약함.\n\n"
            "## 부족 단계\n"
            "- idea_to_product_brief\n"
            "- generate_user_flows\n"
            "- define_mvp_scope\n"
            "- architecture_planning\n"
            "- product_review(정량 평가)\n"
            "- improvement_stage(자동 우선순위/루프가드)\n\n"
            "## 제품 개발 관점의 공백\n"
            "- MVP 우선 정책 강제 장치 부족\n"
            "- UX empty/loading/error 상태 점검이 일관된 점수 체계로 연결되지 않음\n"
            "- 동일 문제 반복/점수 정체/품질 하락 감지 규칙이 표준화되지 않음\n"
        )
        path.write_text(content, encoding="utf-8")

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
        result = self.shell_executor(
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

        self.store.update_job(job_id, stage=stage.value)
        self._append_actor_log(log_path, "ORCHESTRATOR", f"[STAGE] {stage.value}")

    def _run_shell(
        self,
        command: str,
        cwd: Path,
        log_path: Path,
        purpose: str,
    ):
        """Run shell command with shared logging and strict error handling."""

        return self.shell_executor(
            command=command,
            cwd=cwd,
            log_writer=self._actor_log_writer(
                log_path,
                self._infer_actor_from_command(command, purpose),
            ),
            check=True,
            command_purpose=purpose,
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

    def _template_for_profile(self, base_template: str) -> str:
        """Return profile-specific template when fallback profile is active."""

        if self._agent_profile != "fallback":
            return base_template
        fallback_key = f"{base_template}_fallback"
        if self.command_templates.has_template(fallback_key):
            return fallback_key
        return base_template

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
