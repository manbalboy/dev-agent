"""Rule-based orchestration engine for AgentHub jobs.

Important design principle:
- This module is the conductor.
- AI CLIs are workers called at fixed points.
- The order, retries, and termination conditions are code-driven.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
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
    build_coder_prompt,
    build_designer_prompt,
    build_planner_prompt,
    build_pr_summary_prompt,
    build_reviewer_prompt,
    build_spec_markdown,
    build_status_markdown,
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
        log_path = self.settings.logs_dir / job.log_file
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

    def _process_ultra_job(self, job_id: str, log_path: Path) -> None:
        """Run ultra-long mode with round loop and graceful stop."""

        ultra_started = time.monotonic()
        round_index = 0
        last_error: Optional[str] = None

        while True:
            elapsed = time.monotonic() - ultra_started
            if elapsed >= 5 * 60 * 60:
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Ultra mode max runtime (5h) reached. Finishing after current rounds.",
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
                f"[ULTRA] Round {round_index} started",
            )

            try:
                self._agent_profile = "primary"
                self._run_single_attempt(job_id, log_path)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[ULTRA] Round {round_index} completed with primary agents.",
                )
            except Exception as primary_error:  # noqa: BLE001
                last_error = str(primary_error)
                self._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"[ULTRA] Primary agents failed in round {round_index}: {last_error}",
                )

                if self._is_escalation_enabled() and self.command_templates.has_template("escalation"):
                    self._run_optional_escalation(job_id, log_path, last_error)

                try:
                    self._agent_profile = "fallback"
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[ULTRA] Trying fallback agents for round {round_index}.",
                    )
                    self._run_single_attempt(job_id, log_path)
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[ULTRA] Round {round_index} recovered by fallback agents.",
                    )
                except Exception as fallback_error:  # noqa: BLE001
                    last_error = str(fallback_error)
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"[ULTRA] Fallback agents also failed in round {round_index}: {last_error}",
                    )
                    self._append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "[ULTRA] Two-agent failure reached. Ending this ultra job.",
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
                    f"[ULTRA] Stop requested. Ending after round {round_index}.",
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

        self._stage_plan_with_gemini(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.PLAN_WITH_GEMINI.value, log_path
        )
        self._stage_design_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.DESIGN_WITH_CODEX.value, log_path
        )
        self._stage_implement_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.IMPLEMENT_WITH_CODEX.value, log_path
        )
        self._stage_summarize_code_changes(job, repository_path, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.SUMMARIZE_CODE_CHANGES.value, log_path
        )
        self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_IMPLEMENT, log_path)
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
        self._stage_fix_with_codex(job, repository_path, paths, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.FIX_WITH_CODEX.value, log_path
        )
        self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.TEST_AFTER_FIX.value, log_path
        )
        self._stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")
        self._commit_markdown_changes_after_stage(
            job, repository_path, JobStage.COMMIT_FIX.value, log_path
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
                self._stage_plan_with_gemini(job, repository_path, paths, log_path)
            elif node_type == "designer_task":
                self._stage_design_with_codex(job, repository_path, paths, log_path)
            elif node_type == "codex_implement":
                self._stage_implement_with_codex(job, repository_path, paths, log_path)
            elif node_type == "code_change_summary":
                self._stage_summarize_code_changes(job, repository_path, log_path)
            elif node_type == "test_after_implement":
                self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_IMPLEMENT, log_path)
            elif node_type == "tester_task":
                self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_IMPLEMENT, log_path)
            elif node_type == "commit_implement":
                self._stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")
            elif node_type == "gemini_review":
                self._stage_review_with_gemini(job, repository_path, paths, log_path)
            elif node_type == "codex_fix":
                self._stage_fix_with_codex(job, repository_path, paths, log_path)
            elif node_type == "coder_fix_from_test_report":
                self._stage_fix_with_codex(job, repository_path, paths, log_path)
            elif node_type == "test_after_fix":
                passed = self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
                previous_type = ""
                if index > 0:
                    previous_type = str(ordered_nodes[index - 1].get("type", ""))
                if (not passed) and previous_type in {"codex_fix", "coder_fix_from_test_report"}:
                    self._run_fix_retry_loop_after_test_failure(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        log_path=log_path,
                    )
            elif node_type == "tester_run_e2e":
                passed = self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
                previous_type = ""
                if index > 0:
                    previous_type = str(ordered_nodes[index - 1].get("type", ""))
                if (not passed) and previous_type in {"codex_fix", "coder_fix_from_test_report"}:
                    self._run_fix_retry_loop_after_test_failure(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        log_path=log_path,
                    )
            elif node_type == "ux_e2e_review":
                self._stage_ux_e2e_review(job, repository_path, paths, log_path)
            elif node_type == "test_after_fix_final":
                self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
            elif node_type == "tester_retest_e2e":
                self._stage_run_tests(job, repository_path, JobStage.TEST_AFTER_FIX, log_path)
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

        return repository_path

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

        spec_path = repository_path / "SPEC.md"
        plan_path = repository_path / "PLAN.md"
        review_path = repository_path / "REVIEW.md"
        design_path = repository_path / "DESIGN_SYSTEM.md"
        status_path = repository_path / "STATUS.md"

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
        self._append_actor_log(log_path, "ORCHESTRATOR", f"Wrote SPEC.md at {spec_path}")

        # Keep job metadata in sync with canonical issue data.
        self.store.update_job(
            job.job_id,
            issue_title=issue.title,
            issue_url=issue.url,
        )

        return {
            "spec": spec_path,
            "plan": plan_path,
            "review": review_path,
            "design": design_path,
            "status": status_path,
        }

    def _stage_plan_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.PLAN_WITH_GEMINI, log_path)

        planner_prompt_path = repository_path / "PLANNER_PROMPT.md"
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

    def _stage_implement_with_codex(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)

        coder_prompt_path = repository_path / "CODER_PROMPT_IMPLEMENT.md"
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal="PLAN.md 기반 MVP 구현",
                design_path=str(paths.get("design", "")),
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

        designer_prompt_path = repository_path / "DESIGNER_PROMPT.md"
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
        (repository_path / "UX_REVIEW.md").write_text("\n".join(review_lines), encoding="utf-8")

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

        summary_path = repository_path / "CODE_CHANGE_SUMMARY.md"
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
        copilot_summary = self._summarize_changes_with_copilot(prompt, repository_path, log_path)
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
        prompt: str,
        repository_path: Path,
        log_path: Path,
    ) -> Optional[str]:
        """Try Copilot CLI summary generation and return markdown text."""

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

        self._run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} add -A",
            cwd=repository_path,
            log_path=log_path,
            purpose="git add",
        )

        commit_message = self._prepare_commit_message_with_claude(
            job=job,
            repository_path=repository_path,
            stage=stage,
            commit_type=commit_type,
            log_path=log_path,
        )
        if not commit_message:
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

    def _prepare_commit_message_with_claude(
        self,
        job: JobRecord,
        repository_path: Path,
        stage: JobStage,
        commit_type: str,
        log_path: Path,
    ) -> str:
        """Generate one-line Korean commit summary using Claude."""

        template_name = ""
        if self.command_templates.has_template("commit_summary"):
            template_name = "commit_summary"
        elif self.command_templates.has_template("pr_summary"):
            template_name = "pr_summary"
        elif self.command_templates.has_template("escalation"):
            template_name = "escalation"
        else:
            return ""

        prompt_path = repository_path / f"COMMIT_MESSAGE_PROMPT_{stage.value.upper()}.md"
        output_path = repository_path / f"COMMIT_MESSAGE_{stage.value.upper()}.txt"
        prompt_path.write_text(
            build_commit_message_prompt(
                spec_path=str(repository_path / "SPEC.md"),
                plan_path=str(repository_path / "PLAN.md"),
                review_path=str(repository_path / "REVIEW.md"),
                design_path=str(repository_path / "DESIGN_SYSTEM.md"),
                stage_name=stage.value,
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
                            "spec": repository_path / "SPEC.md",
                            "plan": repository_path / "PLAN.md",
                            "review": repository_path / "REVIEW.md",
                            "design": repository_path / "DESIGN_SYSTEM.md",
                            "status": repository_path / "STATUS.md",
                        },
                        prompt_path,
                    ),
                    "commit_message_path": str(output_path),
                    "last_error": "",
                    "pr_summary_path": str(repository_path / "PR_SUMMARY.md"),
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
        first_line = candidate.splitlines()[0].strip().strip("`").strip()
        if not first_line:
            return ""
        return f"{commit_type}: {first_line[:120]}"

    def _stage_review_with_gemini(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self._set_stage(job.job_id, JobStage.REVIEW_WITH_GEMINI, log_path)

        reviewer_prompt_path = repository_path / "REVIEWER_PROMPT.md"
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

        coder_prompt_path = repository_path / "CODER_PROMPT_FIX.md"
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal="REVIEW.md TODO 반영 및 테스트 안정화",
                design_path=str(paths.get("design", "")),
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

        pr_body_path = repository_path / "PR_BODY.md"
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

        path = repository_path / "PREVIEW.md"
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

        prompt_path = repository_path / "PR_SUMMARY_PROMPT.md"
        output_path = repository_path / "PR_SUMMARY.md"
        prompt_path.write_text(
            build_pr_summary_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                design_path=str(paths.get("design", repository_path / "DESIGN_SYSTEM.md")),
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
        """Create a docs-only commit when markdown files changed in a stage."""

        if not self.settings.enable_stage_md_commits:
            return

        status_result = self._run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} status --porcelain -- "
                f"{shlex.quote(':(glob)**/*.md')}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git status md changes ({stage_name})",
        )
        changed_lines = [line for line in status_result.stdout.splitlines() if line.strip()]
        if not changed_lines:
            return

        canonical_stage = self._canonical_stage_name(stage_name)
        self._write_stage_md_snapshot(job, repository_path, canonical_stage, changed_lines, log_path)

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
        log_path: Path,
    ) -> None:
        """Persist per-stage markdown snapshot for dashboard stage toggle."""

        snapshot_root = self.settings.data_dir / "md_snapshots" / job.job_id
        snapshot_root.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage_name).strip("_") or "stage"
        snapshot_path = snapshot_root / f"attempt_{job.attempt}_{safe_stage}.json"

        md_files: List[Dict[str, str]] = []
        for path in sorted(repository_path.glob("*.md")):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            md_files.append(
                {
                    "path": path.name,
                    "content": content,
                }
            )

        payload = {
            "job_id": job.job_id,
            "attempt": job.attempt,
            "stage": stage_name,
            "created_at": utc_now_iso(),
            "changed_files": [line.strip() for line in changed_lines],
            "md_files": md_files,
        }
        snapshot_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Stage markdown snapshot saved: {snapshot_path.name}",
        )

    @staticmethod
    def _canonical_stage_name(stage_name: str) -> str:
        """Normalize workflow node types into JobStage-compatible stage names."""

        node_to_stage = {
            "gh_read_issue": JobStage.READ_ISSUE.value,
            "write_spec": JobStage.WRITE_SPEC.value,
            "gemini_plan": JobStage.PLAN_WITH_GEMINI.value,
            "designer_task": JobStage.DESIGN_WITH_CODEX.value,
            "codex_implement": JobStage.IMPLEMENT_WITH_CODEX.value,
            "code_change_summary": JobStage.SUMMARIZE_CODE_CHANGES.value,
            "test_after_implement": JobStage.TEST_AFTER_IMPLEMENT.value,
            "ux_e2e_review": JobStage.UX_E2E_REVIEW.value,
            "commit_implement": JobStage.COMMIT_IMPLEMENT.value,
            "gemini_review": JobStage.REVIEW_WITH_GEMINI.value,
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
            JobStage.PLAN_WITH_GEMINI.value: "제미나이 플래너 작성",
            JobStage.DESIGN_WITH_CODEX.value: "코덱스 디자이너 작성",
            JobStage.IMPLEMENT_WITH_CODEX.value: "코덱스 구현자 작성",
            JobStage.SUMMARIZE_CODE_CHANGES.value: "코드 변경 요약 작성",
            JobStage.TEST_AFTER_IMPLEMENT.value: "구현 후 테스트 리포트 작성",
            JobStage.UX_E2E_REVIEW.value: "UX E2E 검수 리포트 작성",
            JobStage.COMMIT_IMPLEMENT.value: "구현 커밋 단계 문서 정리",
            JobStage.REVIEW_WITH_GEMINI.value: "제미나이 리뷰어 작성",
            JobStage.FIX_WITH_CODEX.value: "코덱스 수정자 작성",
            JobStage.TEST_AFTER_FIX.value: "수정 후 테스트 리포트 작성",
            JobStage.COMMIT_FIX.value: "수정 커밋 단계 문서 정리",
            "gh_read_issue": "이슈 읽기 문서 반영",
            "write_spec": "스펙 문서 작성",
            "gemini_plan": "제미나이 플래너 작성",
            "designer_task": "코덱스 디자이너 작성",
            "codex_implement": "코덱스 구현자 작성",
            "code_change_summary": "코드 변경 요약 작성",
            "test_after_implement": "구현 후 테스트 리포트 작성",
            "ux_e2e_review": "UX E2E 검수 리포트 작성",
            "commit_implement": "구현 커밋 단계 문서 정리",
            "gemini_review": "제미나이 리뷰어 작성",
            "codex_fix": "코덱스 수정자 작성",
            "coder_fix_from_test_report": "코덱스 수정자 작성",
            "test_after_fix": "수정 후 테스트 리포트 작성",
            "test_after_fix_final": "수정 후 테스트 리포트 작성",
            "tester_run_e2e": "수정 후 테스트 리포트 작성",
            "tester_retest_e2e": "수정 후 테스트 리포트 작성",
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

        escalation_prompt_path = repository_path / "ESCALATION_PROMPT.md"
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
                            "spec": repository_path / "SPEC.md",
                            "plan": repository_path / "PLAN.md",
                            "review": repository_path / "REVIEW.md",
                            "design": repository_path / "DESIGN_SYSTEM.md",
                            "status": repository_path / "STATUS.md",
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
            status_path = repository_path / "STATUS.md"
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
            "design_path": str(paths.get("design", Path("DESIGN_SYSTEM.md"))),
            "status_path": str(paths["status"]),
            "prompt_file": str(prompt_file_path),
        }

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

    @staticmethod
    def _append_actor_log(log_path: Path, actor: str, message: str) -> None:
        """Append one timestamped actor-tagged line to job log file."""

        normalized_actor = (actor or "ORCHESTRATOR").strip().upper()
        if message.startswith("[ACTOR:"):
            tagged = message
        else:
            tagged = f"[ACTOR:{normalized_actor}] {message}"
        Orchestrator._append_log(log_path, tagged)

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
