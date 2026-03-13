"""Template variable and fallback artifact runtime for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict

from app.models import JobRecord


class TemplateArtifactRuntime:
    """Build template variables and ensure downstream fallback artifacts exist."""

    def __init__(
        self,
        *,
        docs_file: Callable[[Path, str], Path],
        job_workspace_path: Callable[[JobRecord], Path],
        job_execution_repository: Callable[[JobRecord], str],
        write_operator_inputs_artifact: Callable[[JobRecord, Path], Dict[str, object]],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.docs_file = docs_file
        self.job_workspace_path = job_workspace_path
        self.job_execution_repository = job_execution_repository
        self.write_operator_inputs_artifact = write_operator_inputs_artifact
        self.append_actor_log = append_actor_log

    def build_template_variables(
        self,
        job: JobRecord,
        paths: Dict[str, Path],
        prompt_file_path: Path,
    ) -> Dict[str, str]:
        """Provide a consistent variable set for AI templates."""

        workspace_path = self.job_workspace_path(job)
        operator_inputs_path = paths.get("operator_inputs", self.docs_file(workspace_path, "OPERATOR_INPUTS.json"))
        self.write_operator_inputs_artifact(job, operator_inputs_path)

        return {
            "repository": job.repository,
            "execution_repository": self.job_execution_repository(job),
            "issue_number": str(job.issue_number),
            "issue_title": job.issue_title,
            "issue_url": job.issue_url,
            "branch_name": job.branch_name,
            "work_dir": str(workspace_path),
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
            "scaffold_plan_path": str(paths.get("scaffold_plan", Path("_docs/SCAFFOLD_PLAN.md"))),
            "bootstrap_report_path": str(paths.get("bootstrap_report", Path("_docs/BOOTSTRAP_REPORT.json"))),
            "product_review_json_path": str(paths.get("product_review", Path("_docs/PRODUCT_REVIEW.json"))),
            "review_history_path": str(paths.get("review_history", Path("_docs/REVIEW_HISTORY.json"))),
            "improvement_backlog_path": str(paths.get("improvement_backlog", Path("_docs/IMPROVEMENT_BACKLOG.json"))),
            "improvement_loop_state_path": str(paths.get("improvement_loop_state", Path("_docs/IMPROVEMENT_LOOP_STATE.json"))),
            "improvement_plan_path": str(paths.get("improvement_plan", Path("_docs/IMPROVEMENT_PLAN.md"))),
            "next_improvement_tasks_path": str(paths.get("next_improvement_tasks", Path("_docs/NEXT_IMPROVEMENT_TASKS.json"))),
            "memory_log_path": str(paths.get("memory_log", Path("_docs/MEMORY_LOG.jsonl"))),
            "decision_history_path": str(paths.get("decision_history", Path("_docs/DECISION_HISTORY.json"))),
            "failure_patterns_path": str(paths.get("failure_patterns", Path("_docs/FAILURE_PATTERNS.json"))),
            "conventions_path": str(paths.get("conventions", Path("_docs/CONVENTIONS.json"))),
            "memory_selection_path": str(paths.get("memory_selection", Path("_docs/MEMORY_SELECTION.json"))),
            "memory_context_path": str(paths.get("memory_context", Path("_docs/MEMORY_CONTEXT.json"))),
            "memory_feedback_path": str(paths.get("memory_feedback", Path("_docs/MEMORY_FEEDBACK.json"))),
            "memory_rankings_path": str(paths.get("memory_rankings", Path("_docs/MEMORY_RANKINGS.json"))),
            "operator_inputs_path": str(operator_inputs_path),
            "strategy_shadow_report_path": str(paths.get("strategy_shadow_report", Path("_docs/STRATEGY_SHADOW_REPORT.json"))),
            "stage_contracts_path": str(paths.get("stage_contracts", Path("_docs/STAGE_CONTRACTS.md"))),
            "stage_contracts_json_path": str(paths.get("stage_contracts_json", Path("_docs/STAGE_CONTRACTS.json"))),
            "pipeline_analysis_path": str(paths.get("pipeline_analysis", Path("_docs/PIPELINE_ANALYSIS.md"))),
            "pipeline_analysis_json_path": str(paths.get("pipeline_analysis_json", Path("_docs/PIPELINE_ANALYSIS.json"))),
            "readme_path": str(paths.get("readme", Path("README.md"))),
            "copyright_path": str(paths.get("copyright", Path("COPYRIGHT.md"))),
            "development_guide_path": str(paths.get("development_guide", Path("DEVELOPMENT_GUIDE.md"))),
            "docs_bundle_path": str(self.docs_file(workspace_path, "DOCUMENTATION_BUNDLE.md")),
            "status_path": str(paths.get("status", Path("_docs/STATUS.md"))),
            "prompt_file": str(prompt_file_path),
        }

    def ensure_design_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure design token/handoff artifacts exist after design planning step."""

        design_tokens = paths.get("design_tokens", self.docs_file(repository_path, "DESIGN_TOKENS.json"))
        token_handoff = paths.get("token_handoff", self.docs_file(repository_path, "TOKEN_HANDOFF.md"))
        if not design_tokens.exists():
            design_tokens.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback DESIGN_TOKENS.json generated.")
        if not token_handoff.exists():
            token_handoff.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback TOKEN_HANDOFF.md generated.")

    def ensure_publisher_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure publisher checklist/handoff artifacts exist after publishing step."""

        checklist = paths.get("publish_checklist", self.docs_file(repository_path, "PUBLISH_CHECKLIST.md"))
        handoff = paths.get("publish_handoff", self.docs_file(repository_path, "PUBLISH_HANDOFF.md"))
        if not checklist.exists():
            checklist.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback PUBLISH_CHECKLIST.md generated.")
        if not handoff.exists():
            handoff.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback PUBLISH_HANDOFF.md generated.")

    def ensure_copywriter_artifacts(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Ensure copywriter artifacts exist for downstream coding/review."""

        copy_plan = paths.get("copy_plan", self.docs_file(repository_path, "COPYWRITING_PLAN.md"))
        copy_deck = paths.get("copy_deck", self.docs_file(repository_path, "COPY_DECK.md"))
        if not copy_plan.exists():
            copy_plan.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPYWRITING_PLAN.md generated.")
        if not copy_deck.exists():
            copy_deck.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPY_DECK.md generated.")

    def ensure_documentation_artifacts(
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
            "documentation_plan", self.docs_file(repository_path, "DOCUMENTATION_PLAN.md")
        )

        if not self._has_text(readme_path):
            readme_path.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback README.md generated.")

        if not self._has_text(copyright_path):
            copyright_path.parent.mkdir(parents=True, exist_ok=True)
            copyright_path.write_text(
                (
                    "# COPYRIGHT\n\n"
                    "Copyright (c) 2026 Project Contributors. All rights reserved.\n\n"
                    "## Third-party licenses\n"
                    "- 사용 라이브러리의 라이선스 고지를 여기에 정리하세요.\n"
                ),
                encoding="utf-8",
            )
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback COPYRIGHT.md generated.")

        if not self._has_text(development_guide_path):
            development_guide_path.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback DEVELOPMENT_GUIDE.md generated.")

        if not self._has_text(documentation_plan_path):
            documentation_plan_path.parent.mkdir(parents=True, exist_ok=True)
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
            self.append_actor_log(log_path, "ORCHESTRATOR", "Fallback DOCUMENTATION_PLAN.md generated.")

    @staticmethod
    def _has_text(path: Path) -> bool:
        return path.exists() and bool(path.read_text(encoding="utf-8", errors="replace").strip())
