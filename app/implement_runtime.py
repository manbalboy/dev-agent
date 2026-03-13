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
        write_integration_guide_summary_artifact: Callable[..., Dict[str, object]] | None,
        write_integration_code_patterns_artifact: Callable[..., Dict[str, object]] | None,
        write_integration_verification_checklist_artifact: Callable[..., Dict[str, object]] | None,
        docs_file: Callable[[Path, str], Path],
        build_route_runtime_context: Callable[[str], str],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
        append_integration_usage_trail_event: Callable[..., Dict[str, object]] | None = None,
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.ensure_product_definition_ready = ensure_product_definition_ready
        self.write_memory_retrieval_artifacts = write_memory_retrieval_artifacts
        self.write_integration_guide_summary_artifact = write_integration_guide_summary_artifact
        self.write_integration_code_patterns_artifact = write_integration_code_patterns_artifact
        self.write_integration_verification_checklist_artifact = (
            write_integration_verification_checklist_artifact
        )
        self.append_integration_usage_trail_event = append_integration_usage_trail_event
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
        if self.write_integration_guide_summary_artifact is not None:
            self.write_integration_guide_summary_artifact(
                repository_path=repository_path,
                paths=paths,
            )
        if self.write_integration_code_patterns_artifact is not None:
            self.write_integration_code_patterns_artifact(
                repository_path=repository_path,
                paths=paths,
            )
        if self.write_integration_verification_checklist_artifact is not None:
            self.write_integration_verification_checklist_artifact(
                repository_path=repository_path,
                paths=paths,
            )

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
                integration_guide_summary_path=str(
                    paths.get(
                        "integration_guide_summary",
                        self.docs_file(repository_path, "INTEGRATION_GUIDE_SUMMARY.md"),
                    )
                ),
                integration_code_patterns_path=str(
                    paths.get(
                        "integration_code_patterns",
                        self.docs_file(repository_path, "INTEGRATION_CODE_PATTERNS.md"),
                    )
                ),
                integration_verification_checklist_path=str(
                    paths.get(
                        "integration_verification_checklist",
                        self.docs_file(repository_path, "INTEGRATION_VERIFICATION_CHECKLIST.md"),
                    )
                ),
                role_context=self.build_route_runtime_context("coder"),
            ),
            encoding="utf-8",
        )
        if self.append_integration_usage_trail_event is not None:
            self.append_integration_usage_trail_event(
                job=job,
                repository_path=repository_path,
                paths=paths,
                stage=JobStage.IMPLEMENT_WITH_CODEX.value,
                route="coder",
                prompt_path=coder_prompt_path,
            )
        self.command_templates.run_template(
            template_name=self.template_for_route("coder"),
            variables=self.build_template_variables(job, paths, coder_prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "CODER"),
        )
