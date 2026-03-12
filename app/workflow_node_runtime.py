"""Workflow node handler runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage
from app.workflow_registry import WORKFLOW_NODE_HANDLER_NAMES


class WorkflowNodeRuntime:
    """Encapsulate workflow node handlers outside the main orchestrator."""

    def __init__(self, *, owner: Any) -> None:
        self.owner = owner
        self._handlers: Dict[str, Callable[..., Any]] = {
            "gh_read_issue": self.workflow_node_read_issue,
            "if_label_match": self.workflow_node_if_label_match,
            "loop_until_pass": self.workflow_node_loop_until_pass,
            "write_spec": self.workflow_node_write_spec,
            "gemini_plan": self.workflow_node_gemini_plan,
            "idea_to_product_brief": self.workflow_node_idea_to_product_brief,
            "generate_user_flows": self.workflow_node_generate_user_flows,
            "define_mvp_scope": self.workflow_node_define_mvp_scope,
            "architecture_planning": self.workflow_node_architecture_planning,
            "project_scaffolding": self.workflow_node_project_scaffolding,
            "designer_task": self.workflow_node_designer_task,
            "publisher_task": self.workflow_node_publisher_task,
            "copywriter_task": self.workflow_node_copywriter_task,
            "documentation_task": self.workflow_node_documentation_task,
            "codex_implement": self.workflow_node_codex_implement,
            "code_change_summary": self.workflow_node_code_change_summary,
            "test_after_implement": self.workflow_node_test_after_implement,
            "tester_task": self.workflow_node_tester_task,
            "commit_implement": self.workflow_node_commit_implement,
            "gemini_review": self.workflow_node_gemini_review,
            "product_review": self.workflow_node_product_review,
            "improvement_stage": self.workflow_node_improvement_stage,
            "codex_fix": self.workflow_node_codex_fix,
            "coder_fix_from_test_report": self.workflow_node_coder_fix_from_test_report,
            "test_after_fix": self.workflow_node_test_after_fix,
            "tester_run_e2e": self.workflow_node_tester_run_e2e,
            "ux_e2e_review": self.workflow_node_ux_e2e_review,
            "test_after_fix_final": self.workflow_node_test_after_fix_final,
            "tester_retest_e2e": self.workflow_node_tester_retest_e2e,
            "commit_fix": self.workflow_node_commit_fix,
            "push_branch": self.workflow_node_push_branch,
            "create_pr": self.workflow_node_create_pr,
        }

    def resolve(self, node_type: str) -> Optional[Callable[..., Any]]:
        handler_name = WORKFLOW_NODE_HANDLER_NAMES.get(node_type)
        if not handler_name:
            return None
        return self._handlers.get(node_type)

    def workflow_node_read_issue(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        context["issue"] = self.owner._stage_read_issue(job, repository_path, log_path)

    def workflow_node_if_label_match(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> Dict[str, str]:
        issue = self.owner._workflow_context_issue(context)
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

    def workflow_node_loop_until_pass(
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

    def workflow_node_write_spec(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        issue = self.owner._workflow_context_issue(context)
        context["paths"] = self.owner._stage_write_spec(job, repository_path, issue, log_path)

    def workflow_node_gemini_plan(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        node_title = str(node.get("title", "")).strip().lower()
        requested_mode = str(node.get("planning_mode", "")).strip().lower()
        planning_mode = "general"
        if requested_mode in {"general", "dev_planning", "big_picture"}:
            planning_mode = requested_mode
        elif "개발 기획" in node_title or "development" in node_title:
            planning_mode = "dev_planning"
        elif "큰틀" in node_title or "big picture" in node_title:
            planning_mode = "big_picture"
        self.owner._stage_plan_with_gemini(
            job,
            repository_path,
            paths,
            log_path,
            planning_mode=planning_mode,
        )
        self.owner._snapshot_plan_variant(repository_path, paths, planning_mode, log_path)

    def workflow_node_idea_to_product_brief(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_idea_to_product_brief(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_generate_user_flows(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_generate_user_flows(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_define_mvp_scope(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_define_mvp_scope(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_architecture_planning(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_architecture_planning(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_project_scaffolding(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_project_scaffolding(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_designer_task(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        if self.owner._is_design_system_locked(repository_path, paths):
            self.owner._set_stage(job.job_id, JobStage.DESIGN_WITH_CODEX, log_path)
            self.owner._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "designer_task skipped by decision lock (_docs/DECISIONS.json).",
            )
            return
        self.owner._stage_design_with_codex(job, repository_path, paths, log_path)
        self.owner._lock_design_system_decision(repository_path, paths, log_path)

    def workflow_node_publisher_task(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_publish_with_codex(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_copywriter_task(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_copywriter_with_codex(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_documentation_task(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_documentation_with_claude(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_codex_implement(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_implement_with_codex(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_code_change_summary(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._workflow_context_paths(context)
        self.owner._stage_summarize_code_changes(job, repository_path, log_path)

    def workflow_node_test_after_implement(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label=f"after_implement_{app_type}",
            app_type=app_type,
        )

    def workflow_node_tester_task(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label=f"tester_task_{app_type}",
            app_type=app_type,
        )

    def workflow_node_commit_implement(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._workflow_context_paths(context)
        self.owner._stage_commit(job, repository_path, JobStage.COMMIT_IMPLEMENT, log_path, "feat")

    def workflow_node_gemini_review(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_review_with_gemini(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_product_review(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_product_review(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_improvement_stage(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_improvement_stage(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_codex_fix(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_fix_with_codex(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_coder_fix_from_test_report(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_fix_with_codex(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )

    def workflow_node_test_after_fix(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"after_fix_{app_type}",
            app_type=app_type,
        )

    def workflow_node_tester_run_e2e(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self.owner._run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=JobStage.TEST_AFTER_FIX,
                gate_label="tester_run_e2e_web",
            )
            return
        self.owner._append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"tester_run_e2e routed for app_type={app_type}. Running non-web test gate by policy.",
        )
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"tester_nonweb_{app_type}",
            app_type=app_type,
        )

    def workflow_node_ux_e2e_review(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self.owner._stage_ux_e2e_review(job, repository_path, paths, log_path)
            return
        self.owner._stage_skip_ux_review_for_non_web(
            job,
            repository_path,
            paths,
            log_path,
            app_type=app_type,
        )

    def workflow_node_test_after_fix_final(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"after_fix_final_{app_type}",
            app_type=app_type,
        )

    def workflow_node_tester_retest_e2e(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        paths = self.owner._workflow_context_paths(context)
        app_type = self.owner._resolve_app_type(repository_path, paths)
        if app_type == "web":
            self.owner._run_test_hard_gate(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                stage=JobStage.TEST_AFTER_FIX,
                gate_label="tester_retest_e2e_web",
            )
            return
        self.owner._run_test_gate_by_policy(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            stage=JobStage.TEST_AFTER_FIX,
            gate_label=f"tester_retest_nonweb_{app_type}",
            app_type=app_type,
        )

    def workflow_node_commit_fix(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._workflow_context_paths(context)
        self.owner._stage_commit(job, repository_path, JobStage.COMMIT_FIX, log_path, "fix")

    def workflow_node_push_branch(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._workflow_context_paths(context)
        self.owner._stage_push_branch(job, repository_path, log_path)

    def workflow_node_create_pr(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        node: Dict[str, Any],
        context: Dict[str, Any],
        log_path: Path,
    ) -> None:
        self.owner._stage_create_pr(
            job,
            repository_path,
            self.owner._workflow_context_paths(context),
            log_path,
        )
