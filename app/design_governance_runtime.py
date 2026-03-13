"""Design governance and pipeline contract helper runtime for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.models import utc_now_iso


class DesignGovernanceRuntime:
    """Encapsulate design lock and pipeline contract artifact helpers."""

    def __init__(
        self,
        *,
        docs_file: Callable[[Path, str], Path],
        sha256_file: Callable[[Path | None], str],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.docs_file = docs_file
        self.sha256_file = sha256_file
        self.append_actor_log = append_actor_log

    def is_design_system_locked(self, repository_path: Path, paths: Dict[str, Path]) -> bool:
        """Return True when design-system decision is locked and reusable."""

        payload = self.read_decisions_payload(repository_path)
        node = payload.get("design_system", {})
        if not isinstance(node, dict) or not bool(node.get("locked")):
            return False
        design_path = paths.get("design")
        if not isinstance(design_path, Path) or not design_path.exists():
            return False
        spec_path = paths.get("spec")
        plan_path = paths.get("plan")
        current_spec_hash = self.sha256_file(spec_path) if isinstance(spec_path, Path) else ""
        current_plan_hash = self.sha256_file(plan_path) if isinstance(plan_path, Path) else ""
        locked_spec_hash = str(node.get("spec_sha256", "")).strip()
        locked_plan_hash = str(node.get("plan_sha256", "")).strip()
        if not locked_spec_hash or not locked_plan_hash:
            return False
        if current_spec_hash != locked_spec_hash or current_plan_hash != locked_plan_hash:
            return False
        return True

    def lock_design_system_decision(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Persist decision lock so repeated rounds skip design-system regeneration."""

        payload = self.read_decisions_payload(repository_path)
        spec_path = paths.get("spec")
        plan_path = paths.get("plan")
        design_path = paths.get("design")
        payload["design_system"] = {
            "locked": True,
            "locked_at": utc_now_iso(),
            "spec_sha256": self.sha256_file(spec_path) if isinstance(spec_path, Path) else "",
            "plan_sha256": self.sha256_file(plan_path) if isinstance(plan_path, Path) else "",
            "design_path": str(design_path) if isinstance(design_path, Path) else "_docs/DESIGN_SYSTEM.md",
            "note": "자동 잠금: 디자인 시스템이 1회 생성되면 반복 라운드에서 재생성을 스킵합니다.",
        }
        self.write_decisions_payload(repository_path, payload)
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            "Design-system decision locked at _docs/DECISIONS.json",
        )

    def read_decisions_payload(self, repository_path: Path) -> Dict[str, Any]:
        """Read decisions payload with safe fallback."""

        path = self.docs_file(repository_path, "DECISIONS.json")
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(loaded, dict):
            return {}
        return loaded

    def write_decisions_payload(self, repository_path: Path, payload: Dict[str, Any]) -> None:
        """Write decisions payload to _docs/DECISIONS.json."""

        path = self.docs_file(repository_path, "DECISIONS.json")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def write_stage_contracts_doc(path: Path, json_path: Path) -> None:
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
    def write_pipeline_analysis_doc(path: Path, json_path: Path) -> None:
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
