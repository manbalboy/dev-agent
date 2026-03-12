"""Auxiliary content/design/documentation stage runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Callable, Dict, Optional

from app.command_runner import CommandExecutionError
from app.log_signal_utils import summarize_optional_route_error
from app.models import JobRecord, JobStage
from app.prompt_builder import (
    build_copywriter_prompt,
    build_designer_prompt,
    build_documentation_prompt,
    build_publisher_prompt,
)


class ContentStageRuntime:
    """Encapsulate auxiliary stage execution outside the main orchestrator."""

    def __init__(
        self,
        *,
        command_templates,
        set_stage: Callable[[str, JobStage, Path], None],
        ensure_product_definition_ready: Callable[[Dict[str, Path], Path], None],
        docs_file: Callable[[Path, str], Path],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
        template_candidates_for_route: Callable[[str], list[str]],
        append_actor_log: Callable[[Path, str, str], None],
        ensure_design_artifacts: Callable[[Path, Dict[str, Path], Path], None],
        ensure_publisher_artifacts: Callable[[Path, Dict[str, Path], Path], None],
        ensure_copywriter_artifacts: Callable[[Path, Dict[str, Path], Path], None],
        ensure_documentation_artifacts: Callable[[Path, Dict[str, Path], Path], None],
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.ensure_product_definition_ready = ensure_product_definition_ready
        self.docs_file = docs_file
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route
        self.template_candidates_for_route = template_candidates_for_route
        self.append_actor_log = append_actor_log
        self.ensure_design_artifacts = ensure_design_artifacts
        self.ensure_publisher_artifacts = ensure_publisher_artifacts
        self.ensure_copywriter_artifacts = ensure_copywriter_artifacts
        self.ensure_documentation_artifacts = ensure_documentation_artifacts

    def stage_design_with_codex(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.DESIGN_WITH_CODEX, log_path)

        designer_prompt_path = self.docs_file(repository_path, "DESIGNER_PROMPT.md")
        designer_prompt_path.write_text(
            build_designer_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
            ),
            encoding="utf-8",
        )

        result = self.command_templates.run_template(
            template_name=self.template_for_route("designer"),
            variables=self.build_template_variables(job, paths, designer_prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "DESIGNER"),
        )
        if not paths["design"].exists() and result.stdout.strip():
            paths["design"].write_text(result.stdout, encoding="utf-8")
        if not paths["design"].exists():
            raise CommandExecutionError(
                "Designer did not produce DESIGN_SYSTEM.md. Next action: ensure designer command "
                "writes to DESIGN_SYSTEM.md or emits markdown on stdout."
            )
        self.ensure_design_artifacts(repository_path, paths, log_path)

    def stage_publish_with_codex(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.IMPLEMENT_WITH_CODEX, log_path)
        self.ensure_product_definition_ready(paths, log_path)
        prompt_path = self.docs_file(repository_path, "CODER_PROMPT_PUBLISH.md")
        prompt_path.write_text(
            build_publisher_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
                publish_checklist_path=str(
                    paths.get("publish_checklist", self.docs_file(repository_path, "PUBLISH_CHECKLIST.md"))
                ),
                publish_handoff_path=str(
                    paths.get("publish_handoff", self.docs_file(repository_path, "PUBLISH_HANDOFF.md"))
                ),
            ),
            encoding="utf-8",
        )
        self.command_templates.run_template(
            template_name=self.template_for_route("publisher"),
            variables=self.build_template_variables(job, paths, prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "PUBLISHER"),
        )
        self.ensure_publisher_artifacts(repository_path, paths, log_path)

    def stage_copywriter_with_codex(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.COPYWRITER_TASK, log_path)
        prompt_path = self.docs_file(repository_path, "CODER_PROMPT_COPYWRITER.md")
        prompt_path.write_text(
            build_copywriter_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                design_path=str(paths["design"]),
                publish_handoff_path=str(
                    paths.get("publish_handoff", self.docs_file(repository_path, "PUBLISH_HANDOFF.md"))
                ),
                copy_plan_path=str(paths.get("copy_plan", self.docs_file(repository_path, "COPYWRITING_PLAN.md"))),
                copy_deck_path=str(paths.get("copy_deck", self.docs_file(repository_path, "COPY_DECK.md"))),
            ),
            encoding="utf-8",
        )
        self.command_templates.run_template(
            template_name=self.template_for_route("copywriter"),
            variables=self.build_template_variables(job, paths, prompt_path),
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, "COPYWRITER"),
        )
        self.ensure_copywriter_artifacts(repository_path, paths, log_path)

    def stage_documentation_with_claude(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.DOCUMENTATION_TASK, log_path)
        prompt_path = self.docs_file(repository_path, "DOCUMENTATION_PROMPT.md")
        bundle_path = self.docs_file(repository_path, "DOCUMENTATION_BUNDLE.md")
        prompt_path.write_text(
            build_documentation_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                readme_path=str(paths.get("readme", repository_path / "README.md")),
                copyright_path=str(paths.get("copyright", repository_path / "COPYRIGHT.md")),
                development_guide_path=str(paths.get("development_guide", repository_path / "DEVELOPMENT_GUIDE.md")),
                documentation_plan_path=str(
                    paths.get("documentation_plan", self.docs_file(repository_path, "DOCUMENTATION_PLAN.md"))
                ),
            ),
            encoding="utf-8",
        )

        route_error: Optional[str] = None
        bundle_applied = False
        for resolved_template in self.template_candidates_for_route("documentation"):
            if not self.command_templates.has_template(resolved_template):
                continue
            route_vars = {
                **self.build_template_variables(job, paths, prompt_path),
                "docs_bundle_path": str(bundle_path),
                "pr_summary_path": str(bundle_path),
                "commit_message_path": str(bundle_path),
            }
            try:
                result = self.command_templates.run_template(
                    template_name=resolved_template,
                    variables=route_vars,
                    cwd=repository_path,
                    log_writer=self.actor_log_writer(log_path, "TECH_WRITER"),
                )
                if not bundle_path.exists() and str(result.stdout).strip():
                    bundle_path.write_text(str(result.stdout).strip() + "\n", encoding="utf-8")
                bundle_applied = self.apply_documentation_bundle(
                    repository_path=repository_path,
                    bundle_path=bundle_path,
                    paths=paths,
                    log_path=log_path,
                )
                if bundle_applied:
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        f"Documentation generated by route template: {resolved_template}",
                    )
                    break
            except CommandExecutionError as error:
                route_error = str(error)

        if not bundle_applied:
            if route_error:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Documentation route failed. Fallback to coder route: "
                    f"{summarize_optional_route_error(route_error, actor='TECH_WRITER')}",
                )
            else:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Documentation route unavailable or output invalid. Fallback to coder route.",
                )
            fallback_prompt = self.docs_file(repository_path, "CODER_PROMPT_DOCUMENTATION_FALLBACK.md")
            fallback_prompt.write_text(
                (
                    "Goal: 루트 기술 문서 3종과 문서 계획 파일을 최신화하세요.\n\n"
                    f"- {paths.get('readme', repository_path / 'README.md')}\n"
                    f"- {paths.get('copyright', repository_path / 'COPYRIGHT.md')}\n"
                    f"- {paths.get('development_guide', repository_path / 'DEVELOPMENT_GUIDE.md')}\n"
                    f"- {paths.get('documentation_plan', self.docs_file(repository_path, 'DOCUMENTATION_PLAN.md'))}\n\n"
                    "규칙:\n"
                    "- 한국어로 작성.\n"
                    "- 프로젝트 구조/실행/테스트/운영 플로우를 반영.\n"
                    "- 문서만 수정하고 불필요한 코드 변경 금지.\n"
                ),
                encoding="utf-8",
            )
            self.command_templates.run_template(
                template_name=self.template_for_route("coder"),
                variables=self.build_template_variables(job, paths, fallback_prompt),
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "TECH_WRITER_CODEX"),
            )

        self.ensure_documentation_artifacts(repository_path, paths, log_path)

    def apply_documentation_bundle(
        self,
        *,
        repository_path: Path,
        bundle_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> bool:
        if not bundle_path.exists():
            return False
        raw = bundle_path.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(r"(?ms)^<<<FILE:(?P<path>[^\n>]+)>>>\n(?P<body>.*?)(?=^<<<FILE:|\Z)")
        matches = list(pattern.finditer(raw))
        if not matches:
            return False

        allowed_targets = {
            "README.md": paths.get("readme", repository_path / "README.md"),
            "COPYRIGHT.md": paths.get("copyright", repository_path / "COPYRIGHT.md"),
            "DEVELOPMENT_GUIDE.md": paths.get("development_guide", repository_path / "DEVELOPMENT_GUIDE.md"),
            "_docs/DOCUMENTATION_PLAN.md": paths.get(
                "documentation_plan", self.docs_file(repository_path, "DOCUMENTATION_PLAN.md")
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
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Documentation bundle applied: {written_count} file(s)",
            )
        return written_count > 0
