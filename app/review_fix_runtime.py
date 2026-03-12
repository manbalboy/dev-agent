"""Review and fix stage runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage
from app.prompt_builder import build_coder_prompt, build_reviewer_prompt


class ReviewFixRuntime:
    """Encapsulate reviewer/fix stage execution outside the main orchestrator."""

    def __init__(
        self,
        *,
        command_templates,
        set_stage: Callable[[str, JobStage, Path], None],
        write_memory_retrieval_artifacts: Callable[..., None],
        docs_file: Callable[[Path, str], Path],
        build_route_runtime_context: Callable[[str], str],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
        template_for_route_in_repository: Callable[[str, Path, Path | None], str] | None = None,
        read_improvement_runtime_context: Callable[[Dict[str, Path]], Dict[str, Any]],
        stage_plan_with_gemini: Callable[..., None],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.write_memory_retrieval_artifacts = write_memory_retrieval_artifacts
        self.docs_file = docs_file
        self.build_route_runtime_context = build_route_runtime_context
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route
        self.template_for_route_in_repository = template_for_route_in_repository
        self.read_improvement_runtime_context = read_improvement_runtime_context
        self.stage_plan_with_gemini = stage_plan_with_gemini
        self.append_actor_log = append_actor_log

    def stage_review_with_gemini(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.REVIEW_WITH_GEMINI, log_path)
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        template_name = (
            self.template_for_route_in_repository("reviewer", repository_path, log_path)
            if self.template_for_route_in_repository is not None
            else self.template_for_route("reviewer")
        )

        reviewer_prompt_path = self.docs_file(repository_path, "REVIEWER_PROMPT.md")
        reviewer_prompt_path.write_text(
            build_reviewer_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                memory_selection_path=str(
                    paths.get("memory_selection", self.docs_file(repository_path, "MEMORY_SELECTION.json"))
                ),
                memory_context_path=str(
                    paths.get("memory_context", self.docs_file(repository_path, "MEMORY_CONTEXT.json"))
                ),
                role_context=self.build_route_runtime_context("reviewer"),
            ),
            encoding="utf-8",
        )

        result = self.command_templates.run_template(
            template_name=template_name,
            variables=self.build_template_variables(job, paths, reviewer_prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "REVIEWER"),
        )
        if not paths["review"].exists() and result.stdout.strip():
            paths["review"].write_text(result.stdout, encoding="utf-8")
        if not paths["review"].exists():
            raise CommandExecutionError(
                "Reviewer did not produce REVIEW.md. Next action: ensure reviewer "
                "template writes to REVIEW.md or outputs markdown to stdout."
            )

    def stage_fix_with_codex(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.FIX_WITH_CODEX, log_path)
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        improvement_runtime = self.read_improvement_runtime_context(paths)
        strategy = str(improvement_runtime.get("strategy", "")).strip()
        scope_restriction = str(improvement_runtime.get("scope_restriction", "")).strip()

        if strategy == "design_rebaseline" or scope_restriction == "MVP_redefinition":
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Improvement strategy requires re-planning. Routing fix stage to planner instead of coder.",
            )
            self.stage_plan_with_gemini(
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

        coder_prompt_path = self.docs_file(repository_path, "CODER_PROMPT_FIX.md")
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal=coding_goal,
                design_path=str(paths.get("design", "")),
                design_tokens_path=str(
                    paths.get("design_tokens", self.docs_file(repository_path, "DESIGN_TOKENS.json"))
                ),
                token_handoff_path=str(
                    paths.get("token_handoff", self.docs_file(repository_path, "TOKEN_HANDOFF.md"))
                ),
                publish_handoff_path=str(
                    paths.get("publish_handoff", self.docs_file(repository_path, "PUBLISH_HANDOFF.md"))
                ),
                improvement_plan_path=str(
                    paths.get("improvement_plan", self.docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
                ),
                improvement_loop_state_path=str(
                    paths.get("improvement_loop_state", self.docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
                ),
                next_improvement_tasks_path=str(
                    paths.get("next_improvement_tasks", self.docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))
                ),
                memory_selection_path=str(
                    paths.get("memory_selection", self.docs_file(repository_path, "MEMORY_SELECTION.json"))
                ),
                memory_context_path=str(paths.get("memory_context", self.docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                operator_inputs_path=str(paths.get("operator_inputs", self.docs_file(repository_path, "OPERATOR_INPUTS.json"))),
                role_context=self.build_route_runtime_context("coder"),
            ),
            encoding="utf-8",
        )

        self.command_templates.run_template(
            template_name=self.template_for_route("coder"),
            variables=self.build_template_variables(job, paths, coder_prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "CODER"),
        )
