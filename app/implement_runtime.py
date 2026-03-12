"""Implement/coder stage runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

from app.models import JobRecord, JobStage
from app.prompt_builder import build_coder_prompt


class ImplementRuntime:
    """Encapsulate coder implementation stage outside the main orchestrator."""

    def __init__(
        self,
        *,
        command_templates,
        set_stage: Callable[[str, JobStage, Path], None],
        ensure_product_definition_ready: Callable[[Dict[str, Path], Path], None],
        write_memory_retrieval_artifacts: Callable[..., None],
        docs_file: Callable[[Path, str], Path],
        build_route_runtime_context: Callable[[str], str],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.ensure_product_definition_ready = ensure_product_definition_ready
        self.write_memory_retrieval_artifacts = write_memory_retrieval_artifacts
        self.docs_file = docs_file
        self.build_route_runtime_context = build_route_runtime_context
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route

    def stage_implement_with_codex(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)
        self.ensure_product_definition_ready(paths, log_path)
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

        coder_prompt_path = self.docs_file(repository_path, "CODER_PROMPT_IMPLEMENT.md")
        coder_prompt_path.write_text(
            build_coder_prompt(
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                coding_goal="PLAN.md 기반 MVP 구현",
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
