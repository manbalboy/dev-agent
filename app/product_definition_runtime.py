"""Product-definition stage runtime for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage, utc_now_iso
from app.prompt_builder import (
    build_architecture_plan_prompt,
    build_mvp_scope_prompt,
    build_product_brief_prompt,
    build_project_scaffolding_prompt,
    build_user_flows_prompt,
)
from app.spec_tools import repo_context_reader


class ProductDefinitionRuntime:
    """Encapsulate product-definition document generation and validation."""

    def __init__(
        self,
        *,
        command_templates,
        set_stage: Callable[[str, JobStage, Path], None],
        docs_file: Callable[[Path, str], Path],
        build_template_variables,
        actor_log_writer,
        template_for_route: Callable[[str], str],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.command_templates = command_templates
        self.set_stage = set_stage
        self.docs_file = docs_file
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route
        self.append_actor_log = append_actor_log

    def run_markdown_generation_with_refinement(
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
                    template_name=self.template_for_route("planner"),
                    variables={
                        **self.build_template_variables(job, paths, prompt_path),
                        "plan_path": str(output_path),
                    },
                    cwd=repository_path,
                    log_writer=self.actor_log_writer(log_path, actor),
                )
                if result.stdout.strip() and not output_path.exists():
                    output_path.write_text(result.stdout, encoding="utf-8")
            except Exception as error:  # noqa: BLE001
                last_error = str(error)
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{stage_name} AI call failed on round {round_index}, using fallback: {error}",
                )
                break

            missing = self.missing_markdown_sections(
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
                "이전 출력 보정 지시\n"
                "이전 출력이 계약을 충족하지 못했습니다.\n"
                f"- 보완 필요 항목: {', '.join(missing)}\n"
                f"- 문서에 반드시 다음 값을 정확히 포함: {', '.join(required_evidence)}"
            )
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{stage_name} refinement retry requested: {', '.join(missing)}",
            )

        fallback_writer()
        self.ensure_markdown_stage_contract(
            stage_name=stage_name,
            path=output_path,
            required_sections=required_sections,
            required_evidence=required_evidence,
            fallback_writer=None,
            log_path=log_path,
        )
        if last_error:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{stage_name} fallback applied after AI refinement failure: {last_error}",
            )

    def stage_idea_to_product_brief(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.IDEA_TO_PRODUCT_BRIEF, log_path)
        product_brief_path = paths.get("product_brief", self.docs_file(repository_path, "PRODUCT_BRIEF.md"))
        prompt_path = self.docs_file(repository_path, "PRODUCT_BRIEF_PROMPT.md")
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
        self.run_markdown_generation_with_refinement(
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
            fallback_writer=lambda: self.write_product_brief_fallback(job, paths, product_brief_path),
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"PRODUCT_BRIEF.md ready: {product_brief_path}")

    def stage_generate_user_flows(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.GENERATE_USER_FLOWS, log_path)
        user_flows_path = paths.get("user_flows", self.docs_file(repository_path, "USER_FLOWS.md"))
        product_brief_path = paths.get("product_brief", self.docs_file(repository_path, "PRODUCT_BRIEF.md"))
        prompt_path = self.docs_file(repository_path, "USER_FLOWS_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "primary_flow": ["primary flow", "핵심 흐름", "user journey"],
            "secondary_flows": ["secondary flows", "보조 흐름", "엣지"],
            "ux_state_checklist": ["ux state checklist", "loading", "empty", "error", "상태"],
            "entry_exit_points": ["entry/exit points", "entry", "exit", "진입", "종료"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self.run_markdown_generation_with_refinement(
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
            fallback_writer=lambda: self.write_user_flows_fallback(job, paths, user_flows_path),
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"USER_FLOWS.md ready: {user_flows_path}")

    def stage_define_mvp_scope(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.DEFINE_MVP_SCOPE, log_path)
        mvp_scope_path = paths.get("mvp_scope", self.docs_file(repository_path, "MVP_SCOPE.md"))
        product_brief_path = paths.get("product_brief", self.docs_file(repository_path, "PRODUCT_BRIEF.md"))
        user_flows_path = paths.get("user_flows", self.docs_file(repository_path, "USER_FLOWS.md"))
        prompt_path = self.docs_file(repository_path, "MVP_SCOPE_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "in_scope": ["in scope", "포함", "범위"],
            "out_of_scope": ["out of scope", "비범위", "제외"],
            "acceptance_gates": ["acceptance gate", "완료 조건", "게이트"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self.run_markdown_generation_with_refinement(
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
            fallback_writer=lambda: self.write_mvp_scope_fallback(job, paths, mvp_scope_path),
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"MVP_SCOPE.md ready: {mvp_scope_path}")

    def stage_architecture_planning(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.ARCHITECTURE_PLANNING, log_path)
        architecture_plan_path = paths.get("architecture_plan", self.docs_file(repository_path, "ARCHITECTURE_PLAN.md"))
        mvp_scope_path = paths.get("mvp_scope", self.docs_file(repository_path, "MVP_SCOPE.md"))
        user_flows_path = paths.get("user_flows", self.docs_file(repository_path, "USER_FLOWS.md"))
        prompt_path = self.docs_file(repository_path, "ARCHITECTURE_PLAN_PROMPT.md")
        required_sections = {
            "context_anchor": ["context anchor", "job id", "issue title"],
            "layer_structure": ["layer structure", "레이어", "layer"],
            "component_boundaries": ["component boundaries", "컴포넌트 경계", "boundary"],
            "data_contracts": ["data contracts", "데이터 계약", "contract"],
            "quality_gates": ["quality gates", "품질 게이트", "quality gate"],
            "loop_safety_rules": ["loop safety", "루프 안전", "regression", "stagnation"],
        }
        required_evidence = [job.job_id, job.issue_title]
        self.run_markdown_generation_with_refinement(
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
            fallback_writer=lambda: self.write_architecture_plan_fallback(job, paths, architecture_plan_path),
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"ARCHITECTURE_PLAN.md ready: {architecture_plan_path}")

    def stage_project_scaffolding(
        self,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.PROJECT_SCAFFOLDING, log_path)
        scaffold_plan_path = paths.get("scaffold_plan", self.docs_file(repository_path, "SCAFFOLD_PLAN.md"))
        bootstrap_report_path = paths.get("bootstrap_report", self.docs_file(repository_path, "BOOTSTRAP_REPORT.json"))
        architecture_plan_path = paths.get("architecture_plan", self.docs_file(repository_path, "ARCHITECTURE_PLAN.md"))
        mvp_scope_path = paths.get("mvp_scope", self.docs_file(repository_path, "MVP_SCOPE.md"))
        spec_json_path = paths.get("spec_json", self.docs_file(repository_path, "SPEC.json"))

        repo_context = repo_context_reader(repository_path)
        bootstrap_report = self.build_bootstrap_report(
            repository_path=repository_path,
            spec_json_path=spec_json_path,
            repo_context=repo_context,
        )
        bootstrap_report_path.write_text(
            json.dumps(bootstrap_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        prompt_path = self.docs_file(repository_path, "SCAFFOLD_PLAN_PROMPT.md")
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
            **self.build_template_variables(job, paths, prompt_path),
            "plan_path": str(scaffold_plan_path),
        }
        try:
            result = self.command_templates.run_template(
                template_name=self.template_for_route("planner"),
                variables=template_vars,
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "SCAFFOLD"),
            )
            if not scaffold_plan_path.exists() and result.stdout.strip():
                scaffold_plan_path.write_text(result.stdout, encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"SCAFFOLD_PLAN AI call failed, using template fallback: {error}",
            )
            self.write_project_scaffolding_fallback(bootstrap_report, scaffold_plan_path)
        self.ensure_markdown_stage_contract(
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
            fallback_writer=lambda: self.write_project_scaffolding_fallback(bootstrap_report, scaffold_plan_path),
            log_path=log_path,
        )
        self.append_actor_log(log_path, "ORCHESTRATOR", f"SCAFFOLD_PLAN.md ready: {scaffold_plan_path}")
        self.append_actor_log(log_path, "ORCHESTRATOR", f"BOOTSTRAP_REPORT.json ready: {bootstrap_report_path}")

    @staticmethod
    def build_bootstrap_report(
        *,
        repository_path: Path,
        spec_json_path: Optional[Path],
        repo_context: Dict[str, Any],
    ) -> Dict[str, Any]:
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

    def ensure_markdown_stage_contract(
        self,
        *,
        stage_name: str,
        path: Path,
        required_sections: Dict[str, List[str]],
        required_evidence: Optional[List[str]],
        fallback_writer: Optional[Callable[[], None]],
        log_path: Path,
    ) -> None:
        missing = self.missing_markdown_sections(
            path,
            required_sections,
            required_evidence=required_evidence,
        )
        if not missing:
            return
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"{stage_name} contract missing sections: {', '.join(missing)}",
        )
        if fallback_writer is not None:
            fallback_writer()
            missing = self.missing_markdown_sections(
                path,
                required_sections,
                required_evidence=required_evidence,
            )
            if not missing:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"{stage_name} contract recovered by fallback writer.",
                )
                return
        raise CommandExecutionError(
            f"{stage_name} contract validation failed. Missing sections: {', '.join(missing)}"
        )

    @staticmethod
    def missing_markdown_sections(
        path: Path,
        required_sections: Dict[str, List[str]],
        *,
        required_evidence: Optional[List[str]] = None,
    ) -> List[str]:
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

    def ensure_product_definition_ready(
        self,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
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
            missing = self.missing_markdown_sections(path, required)
            if missing:
                failures.append(f"{label}: missing {', '.join(missing)}")
        if failures:
            self.append_actor_log(
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

    def write_product_brief_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        product_brief_path: Path,
    ) -> None:
        spec_json = self.read_json_file(paths.get("spec_json"))
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

    def write_user_flows_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        user_flows_path: Path,
    ) -> None:
        spec_json = self.read_json_file(paths.get("spec_json"))
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

    def write_mvp_scope_fallback(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        mvp_scope_path: Path,
    ) -> None:
        spec_json = self.read_json_file(paths.get("spec_json"))
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
    def write_architecture_plan_fallback(
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
    def write_project_scaffolding_fallback(
        bootstrap_report: Dict[str, Any],
        scaffold_plan_path: Path,
    ) -> None:
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

    @staticmethod
    def read_json_file(path: Optional[Path]) -> Dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
