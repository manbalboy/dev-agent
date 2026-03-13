"""Planner stage runtime extraction for orchestrator."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.command_runner import CommandExecutionError
from app.langgraph_planner_shadow import build_disabled_planner_shadow_payload
from app.models import JobRecord, JobStage
from app.planner_graph import build_refinement_instruction, evaluate_plan_markdown
from app.prompt_builder import build_planner_prompt
from app.tool_runtime import ToolRequest, ToolResult, ToolRuntime


class PlannerRuntime:
    """Encapsulate planner stage execution without changing external contracts."""

    def __init__(
        self,
        *,
        command_templates,
        set_stage: Callable[[str, JobStage, Path], None],
        append_actor_log: Callable[[Path, str, str], None],
        docs_file: Callable[[Path, str], Path],
        write_memory_retrieval_artifacts: Callable[..., None],
        build_route_runtime_context: Callable[[str], str],
        is_long_track_job: Callable[[JobRecord], bool],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
        template_for_route_in_repository: Callable[[str, Path, Path | None], str] | None = None,
        route_allows_tool: Callable[[str, str], bool],
        execute_planner_tool_request: Callable[..., Dict[str, Any]],
        feature_enabled: Callable[[str], bool],
        planner_shadow_runner,
        write_integration_recommendation_artifact: Callable[..., Dict[str, Any]] | None = None,
        write_integration_guide_summary_artifact: Callable[..., Dict[str, Any]] | None = None,
        write_integration_code_patterns_artifact: Callable[..., Dict[str, Any]] | None = None,
        write_integration_verification_checklist_artifact: Callable[..., Dict[str, Any]] | None = None,
        append_integration_usage_trail_event: Callable[..., Dict[str, Any]] | None = None,
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.append_actor_log = append_actor_log
        self.docs_file = docs_file
        self.write_memory_retrieval_artifacts = write_memory_retrieval_artifacts
        self.build_route_runtime_context = build_route_runtime_context
        self.is_long_track_job = is_long_track_job
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route
        self.template_for_route_in_repository = template_for_route_in_repository
        self.route_allows_tool = route_allows_tool
        self.execute_planner_tool_request = execute_planner_tool_request
        self.feature_enabled = feature_enabled
        self.planner_shadow_runner = planner_shadow_runner
        self.write_integration_recommendation_artifact = write_integration_recommendation_artifact
        self.write_integration_guide_summary_artifact = write_integration_guide_summary_artifact
        self.write_integration_code_patterns_artifact = write_integration_code_patterns_artifact
        self.write_integration_verification_checklist_artifact = (
            write_integration_verification_checklist_artifact
        )
        self.append_integration_usage_trail_event = append_integration_usage_trail_event

    def stage_plan_with_gemini(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        self.set_stage(job.job_id, JobStage.PLAN_WITH_GEMINI, log_path)

        if not self.planner_graph_enabled():
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "Planner graph MVP disabled by env. Using legacy one-shot planner.",
            )
            self.run_planner_legacy_one_shot(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                planning_mode=planning_mode,
            )
            return

        try:
            self.run_planner_graph_mvp(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                planning_mode=planning_mode,
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Planner graph MVP failed. Fallback to legacy one-shot planner: {error}",
            )
            self.run_planner_legacy_one_shot(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
                planning_mode=planning_mode,
            )

    def run_planner_legacy_one_shot(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        template_name = (
            self.template_for_route_in_repository("planner", repository_path, log_path)
            if self.template_for_route_in_repository is not None
            else self.template_for_route("planner")
        )
        planner_prompt_path = self.docs_file(repository_path, "PLANNER_PROMPT.md")
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        if self.write_integration_recommendation_artifact is not None:
            self.write_integration_recommendation_artifact(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
            )
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
        review_ready = paths["review"].exists() and bool(paths["review"].read_text(encoding="utf-8", errors="replace").strip())
        planner_prompt_path.write_text(
            build_planner_prompt(
                str(paths["spec"]),
                str(paths["plan"]),
                review_path=str(paths["review"]),
                improvement_plan_path=str(
                    paths.get("improvement_plan", self.docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
                ),
                improvement_loop_state_path=str(
                    paths.get("improvement_loop_state", self.docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
                ),
                next_improvement_tasks_path=str(
                    paths.get("next_improvement_tasks", self.docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))
                ),
                followup_backlog_task_path=str(
                    paths.get("followup_backlog_task", self.docs_file(repository_path, "FOLLOWUP_BACKLOG_TASK.json"))
                ),
                memory_selection_path=str(
                    paths.get("memory_selection", self.docs_file(repository_path, "MEMORY_SELECTION.json"))
                ),
                memory_context_path=str(paths.get("memory_context", self.docs_file(repository_path, "MEMORY_CONTEXT.json"))),
                operator_inputs_path=str(paths.get("operator_inputs", self.docs_file(repository_path, "OPERATOR_INPUTS.json"))),
                integration_recommendations_path=str(
                    paths.get(
                        "integration_recommendations",
                        self.docs_file(repository_path, "INTEGRATION_RECOMMENDATIONS.json"),
                    )
                ),
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
                role_context=self.build_route_runtime_context("planner"),
                is_long_term=self.is_long_track_job(job),
                is_refinement_round=review_ready,
                planning_mode=planning_mode,
            ),
            encoding="utf-8",
        )
        if self.append_integration_usage_trail_event is not None:
            self.append_integration_usage_trail_event(
                job=job,
                repository_path=repository_path,
                paths=paths,
                stage=JobStage.PLAN_WITH_GEMINI.value,
                route="planner",
                prompt_path=planner_prompt_path,
            )
        result = self.command_templates.run_template(
            template_name=template_name,
            variables=self.build_template_variables(job, paths, planner_prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "PLANNER"),
        )
        if not paths["plan"].exists() and result.stdout.strip():
            paths["plan"].write_text(result.stdout, encoding="utf-8")
        if not paths["plan"].exists():
            raise CommandExecutionError(
                "Planner did not produce PLAN.md. Next action: ensure planner command "
                "writes to PLAN.md or emits plan content on stdout."
            )

    def run_planner_graph_mvp(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        planning_mode: str = "general",
    ) -> None:
        template_name = (
            self.template_for_route_in_repository("planner", repository_path, log_path)
            if self.template_for_route_in_repository is not None
            else self.template_for_route("planner")
        )
        self.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)
        if self.write_integration_recommendation_artifact is not None:
            self.write_integration_recommendation_artifact(
                job=job,
                repository_path=repository_path,
                paths=paths,
                log_path=log_path,
            )
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
        review_ready = paths["review"].exists() and bool(paths["review"].read_text(encoding="utf-8", errors="replace").strip())
        base_prompt = build_planner_prompt(
            str(paths["spec"]),
            str(paths["plan"]),
            review_path=str(paths["review"]),
            improvement_plan_path=str(
                paths.get("improvement_plan", self.docs_file(repository_path, "IMPROVEMENT_PLAN.md"))
            ),
            improvement_loop_state_path=str(
                paths.get("improvement_loop_state", self.docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"))
            ),
            next_improvement_tasks_path=str(
                paths.get("next_improvement_tasks", self.docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"))
            ),
            followup_backlog_task_path=str(
                paths.get("followup_backlog_task", self.docs_file(repository_path, "FOLLOWUP_BACKLOG_TASK.json"))
            ),
            memory_selection_path=str(paths.get("memory_selection", self.docs_file(repository_path, "MEMORY_SELECTION.json"))),
            memory_context_path=str(paths.get("memory_context", self.docs_file(repository_path, "MEMORY_CONTEXT.json"))),
            operator_inputs_path=str(paths.get("operator_inputs", self.docs_file(repository_path, "OPERATOR_INPUTS.json"))),
            integration_recommendations_path=str(
                paths.get(
                    "integration_recommendations",
                    self.docs_file(repository_path, "INTEGRATION_RECOMMENDATIONS.json"),
                )
            ),
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
            role_context=self.build_route_runtime_context("planner"),
            is_long_term=self.is_long_track_job(job),
            is_refinement_round=review_ready,
            planning_mode=planning_mode,
        )

        max_rounds = self.planner_graph_max_rounds()
        rounds: List[Dict[str, Any]] = []
        plan_quality_path = self.docs_file(repository_path, "PLAN_QUALITY.json")
        for round_index in range(1, max_rounds + 1):
            is_refine = round_index > 1
            prompt_path = self.docs_file(
                repository_path,
                "PLANNER_PROMPT.md" if round_index == 1 else f"PLANNER_PROMPT_REFINE_{round_index}.md",
            )
            prompt_text = base_prompt
            if is_refine and rounds:
                prompt_text += build_refinement_instruction(round_index=round_index, quality=rounds[-1].get("quality", {}))
            tool_context_addendum = ""
            tool_request_count = 0
            max_tool_requests = 2
            while True:
                prompt_path.write_text(prompt_text + tool_context_addendum, encoding="utf-8")
                if self.append_integration_usage_trail_event is not None:
                    self.append_integration_usage_trail_event(
                        job=job,
                        repository_path=repository_path,
                        paths=paths,
                        stage=JobStage.PLAN_WITH_GEMINI.value,
                        route="planner",
                        prompt_path=prompt_path,
                    )

                result = self.command_templates.run_template(
                    template_name=template_name,
                    variables=self.build_template_variables(job, paths, prompt_path),
                    cwd=repository_path,
                    log_writer=self.actor_log_writer(log_path, "PLANNER"),
                )
                if not paths["plan"].exists() and result.stdout.strip():
                    paths["plan"].write_text(result.stdout, encoding="utf-8")
                if not paths["plan"].exists():
                    raise CommandExecutionError(
                        "Planner did not produce PLAN.md in graph mode. "
                        "Next action: verify planner template writes PLAN.md."
                    )

                plan_text = paths["plan"].read_text(encoding="utf-8", errors="replace")
                tool_request = self.parse_planner_tool_request(plan_text)
                if not tool_request:
                    break
                if not self.route_allows_tool("planner", tool_request.tool):
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Planner requested disallowed tool '{tool_request.tool}'. Ignoring tool request.",
                    )
                    break
                if tool_request_count >= max_tool_requests:
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        "Planner tool-request loop cap reached. Continuing without further search calls.",
                    )
                    break
                search_outcome = self.execute_planner_tool_request(
                    job=job,
                    repository_path=repository_path,
                    paths=paths,
                    log_path=log_path,
                    tool_request=tool_request,
                )
                tool_request_count += 1
                tool_context_addendum += self.build_planner_tool_context_addendum(
                    tool_request=tool_request,
                    outcome=search_outcome,
                )
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
            self.append_actor_log(
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
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_langgraph_planner_shadow_trace(
            repository_path=repository_path,
            paths=paths,
            rounds=rounds,
            max_rounds=max_rounds,
            planning_mode=planning_mode,
        )
        self.append_actor_log(
            log_path,
            "PLANNER",
            f"PlannerGraph final quality: passed={final_quality.get('passed')} score={final_quality.get('score')}",
        )
        if not bool(final_quality.get("passed")):
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "PLAN quality gate not passed, but continuing by non-blocking assist policy.",
            )

    def write_langgraph_planner_shadow_trace(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
        rounds: List[Dict[str, Any]],
        max_rounds: int,
        planning_mode: str,
    ) -> None:
        shadow_path = paths.get(
            "langgraph_planner_shadow",
            self.docs_file(repository_path, "LANGGRAPH_PLANNER_SHADOW.json"),
        )
        if not self.feature_enabled("langgraph_planner_shadow"):
            shadow_path.write_text(
                json.dumps(
                    build_disabled_planner_shadow_payload(detail="feature_flag_disabled"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return

        payload = self.planner_shadow_runner.run(
            rounds=rounds,
            max_rounds=max_rounds,
            planning_mode=planning_mode,
            plan_path=paths["plan"],
            plan_quality_path=self.docs_file(repository_path, "PLAN_QUALITY.json"),
        )
        shadow_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def planner_graph_max_rounds() -> int:
        raw = (os.getenv("AGENTHUB_PLANNER_GRAPH_MAX_ROUNDS", "3") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 3
        return max(1, min(5, value))

    @staticmethod
    def planner_graph_enabled() -> bool:
        raw = (os.getenv("AGENTHUB_PLANNER_GRAPH_ENABLED", "true") or "").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def parse_planner_tool_request(plan_text: str) -> Optional[ToolRequest]:
        return ToolRuntime.parse_planner_tool_request(plan_text)

    @staticmethod
    def build_planner_tool_context_addendum(
        *,
        tool_request: ToolRequest,
        outcome: Dict[str, Any],
    ) -> str:
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
