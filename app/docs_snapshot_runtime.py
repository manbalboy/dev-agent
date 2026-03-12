"""Stage markdown snapshot/runtime helpers for orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from typing import Any, Dict, List

from app.models import JobStage, utc_now_iso


class DocsSnapshotRuntime:
    """Handle plan variant snapshots and per-stage markdown commits."""

    def __init__(
        self,
        *,
        settings,
        run_shell,
        docs_file,
        append_actor_log,
        prepare_commit_summary_with_ai,
    ) -> None:
        self.settings = settings
        self.run_shell = run_shell
        self.docs_file = docs_file
        self.append_actor_log = append_actor_log
        self.prepare_commit_summary_with_ai = prepare_commit_summary_with_ai

    def snapshot_plan_variant(
        self,
        *,
        repository_path: Path,
        paths: Dict[str, Path],
        planning_mode: str,
        log_path: Path,
    ) -> None:
        """Preserve plan snapshots so big-picture/dev planning are both traceable."""

        plan_path = paths.get("plan")
        if not isinstance(plan_path, Path) or not plan_path.exists():
            return
        mode = (planning_mode or "general").strip().lower()
        target_name = ""
        if mode == "big_picture":
            target_name = "PLAN_BIG.md"
        elif mode == "dev_planning":
            target_name = "PLAN_DEV.md"
        else:
            return
        target_path = self.docs_file(repository_path, target_name)
        target_path.write_text(
            plan_path.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Plan snapshot saved: {target_path.name}",
        )

    def commit_markdown_changes_after_stage(
        self,
        *,
        job,
        repository_path: Path,
        stage_name: str,
        log_path: Path,
    ) -> None:
        """Create stage snapshots and docs commit when markdown files changed."""

        if not self.settings.enable_stage_md_commits:
            return

        status_all = self.run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git status all changes ({stage_name})",
        )
        changed_lines_all = [line for line in status_all.stdout.splitlines() if line.strip()]
        if not changed_lines_all:
            return

        status_md = self.run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} status --porcelain -- "
                f"{shlex.quote(':(glob)**/*.md')}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git status md changes ({stage_name})",
        )
        changed_lines_md = [line for line in status_md.stdout.splitlines() if line.strip()]

        canonical_stage = self.canonical_stage_name(stage_name)
        self.write_stage_md_snapshot(
            job=job,
            repository_path=repository_path,
            stage_name=canonical_stage,
            changed_lines=changed_lines_md,
            changed_lines_all=changed_lines_all,
            log_path=log_path,
        )
        if not changed_lines_md:
            return

        changed_md_paths = [
            self.parse_porcelain_path(line)
            for line in changed_lines_md
            if self.parse_porcelain_path(line)
        ]
        if self.should_skip_md_commit(changed_md_paths):
            self.append_actor_log(
                log_path,
                "GIT",
                f"Skipped markdown commit for stage '{stage_name}' (prompt/temporary docs only).",
            )
            return

        self.run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} add -- "
                f"{shlex.quote(':(glob)**/*.md')}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git add md changes ({stage_name})",
        )

        display_stage = self.format_stage_display_name(canonical_stage)
        summary = self.prepare_commit_summary_with_ai(
            job=job,
            repository_path=repository_path,
            stage_name=canonical_stage,
            commit_type="docs(stage)",
            changed_paths=changed_md_paths,
            log_path=log_path,
        )
        if summary:
            commit_message = f"docs(stage): {summary}"
        else:
            commit_message = f"docs(stage): {display_stage} (issue #{job.issue_number})"
        self.run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} commit -m "
                f"{shlex.quote(commit_message)}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"git commit md changes ({stage_name})",
        )
        self.append_actor_log(
            log_path,
            "GIT",
            f"Markdown snapshot committed after stage '{stage_name}'",
        )

    def write_stage_md_snapshot(
        self,
        *,
        job,
        repository_path: Path,
        stage_name: str,
        changed_lines: List[str],
        changed_lines_all: List[str],
        log_path: Path,
    ) -> None:
        """Persist per-stage markdown + file snapshot for dashboard stage toggle."""

        snapshot_root = self.settings.data_dir / "md_snapshots" / job.job_id
        snapshot_root.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage_name).strip("_") or "stage"
        snapshot_path = snapshot_root / f"attempt_{job.attempt}_{safe_stage}.json"

        md_files: List[Dict[str, str]] = []
        md_paths: List[Path] = []
        md_paths.extend(sorted(repository_path.glob("*.md")))
        docs_dir = repository_path / "_docs"
        if docs_dir.exists():
            md_paths.extend(sorted(docs_dir.glob("*.md")))
        seen_md = set()
        for path in md_paths:
            if not path.is_file():
                continue
            rel = str(path.relative_to(repository_path))
            if rel in seen_md:
                continue
            seen_md.add(rel)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            md_files.append({"path": rel, "content": content})
        file_snapshots = self.collect_stage_file_snapshots(repository_path, changed_lines_all)

        payload = {
            "job_id": job.job_id,
            "attempt": job.attempt,
            "stage": stage_name,
            "created_at": utc_now_iso(),
            "changed_files": [line.strip() for line in changed_lines],
            "changed_files_all": [line.strip() for line in changed_lines_all],
            "md_files": md_files,
            "file_snapshots": file_snapshots,
        }
        snapshot_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Stage snapshot saved: {snapshot_path.name}",
        )

    @classmethod
    def collect_stage_file_snapshots(
        cls,
        repository_path: Path,
        changed_lines_all: List[str],
    ) -> List[Dict[str, Any]]:
        """Capture changed file contents at stage boundary for point-in-time audit."""

        snapshots: List[Dict[str, Any]] = []
        seen_paths = set()
        max_files = 24
        max_bytes = 200_000
        repository_root = repository_path.resolve()

        for raw in changed_lines_all:
            if len(snapshots) >= max_files:
                break
            status = raw[:2].strip()
            rel_path = cls.parse_porcelain_path(raw)
            if not rel_path or rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            abs_path = (repository_path / rel_path).resolve()
            if repository_root not in abs_path.parents and abs_path != repository_root:
                continue

            item: Dict[str, Any] = {
                "path": rel_path,
                "status": status or "??",
                "exists": abs_path.exists() and abs_path.is_file(),
                "truncated": False,
                "binary": False,
                "content": "",
            }
            if not item["exists"]:
                snapshots.append(item)
                continue
            try:
                blob = abs_path.read_bytes()
            except OSError:
                snapshots.append(item)
                continue
            if b"\x00" in blob:
                item["binary"] = True
                snapshots.append(item)
                continue
            if len(blob) > max_bytes:
                blob = blob[:max_bytes]
                item["truncated"] = True
            item["content"] = blob.decode("utf-8", errors="replace")
            snapshots.append(item)
        return snapshots

    @staticmethod
    def parse_porcelain_path(raw_line: str) -> str:
        """Extract normalized file path from `git status --porcelain` one line."""

        line = str(raw_line or "").rstrip()
        if len(line) < 4:
            return ""
        payload = line[3:].strip()
        if not payload:
            return ""
        if " -> " in payload:
            payload = payload.split(" -> ", 1)[1].strip()
        return payload

    @staticmethod
    def should_skip_md_commit(changed_md_paths: List[str]) -> bool:
        """Skip noisy docs commits when only transient prompt files changed."""

        if not changed_md_paths:
            return True
        transient_prefixes = (
            "_docs/PLANNER_PROMPT",
            "_docs/CODER_PROMPT",
            "_docs/DESIGNER_PROMPT",
            "_docs/REVIEWER_PROMPT",
            "_docs/CODEX_HELPER_",
            "_docs/COPILOT_",
            "_docs/PR_SUMMARY_PROMPT",
            "_docs/COMMIT_MESSAGE_PROMPT_",
            "_docs/PLANNER_TOOL_REQUEST",
            "_docs/ESCALATION_PROMPT",
            "_docs/DOCUMENTATION_PROMPT",
            "_docs/DOCUMENTATION_BUNDLE",
            "_docs/SCAFFOLD_PLAN_PROMPT",
        )
        normalized = [str(path).strip() for path in changed_md_paths if str(path).strip()]
        if not normalized:
            return True
        return all(any(path.startswith(prefix) for prefix in transient_prefixes) for path in normalized)

    @staticmethod
    def canonical_stage_name(stage_name: str) -> str:
        """Normalize workflow node types into JobStage-compatible stage names."""

        node_to_stage = {
            "gh_read_issue": JobStage.READ_ISSUE.value,
            "write_spec": JobStage.WRITE_SPEC.value,
            "idea_to_product_brief": JobStage.IDEA_TO_PRODUCT_BRIEF.value,
            "generate_user_flows": JobStage.GENERATE_USER_FLOWS.value,
            "define_mvp_scope": JobStage.DEFINE_MVP_SCOPE.value,
            "architecture_planning": JobStage.ARCHITECTURE_PLANNING.value,
            "project_scaffolding": JobStage.PROJECT_SCAFFOLDING.value,
            "gemini_plan": JobStage.PLAN_WITH_GEMINI.value,
            "designer_task": JobStage.DESIGN_WITH_CODEX.value,
            "publisher_task": "publisher_task",
            "copywriter_task": "copywriter_task",
            "documentation_task": JobStage.DOCUMENTATION_TASK.value,
            "codex_implement": JobStage.IMPLEMENT_WITH_CODEX.value,
            "code_change_summary": JobStage.SUMMARIZE_CODE_CHANGES.value,
            "test_after_implement": JobStage.TEST_AFTER_IMPLEMENT.value,
            "ux_e2e_review": JobStage.UX_E2E_REVIEW.value,
            "commit_implement": JobStage.COMMIT_IMPLEMENT.value,
            "gemini_review": JobStage.REVIEW_WITH_GEMINI.value,
            "product_review": JobStage.PRODUCT_REVIEW.value,
            "improvement_stage": JobStage.IMPROVEMENT_STAGE.value,
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
    def format_stage_display_name(stage_name: str) -> str:
        """Return short Korean labels for markdown snapshot commit messages."""

        stage_map = {
            JobStage.READ_ISSUE.value: "이슈 읽기 문서 반영",
            JobStage.WRITE_SPEC.value: "스펙 문서 작성",
            JobStage.IDEA_TO_PRODUCT_BRIEF.value: "제품 정의 브리프 작성",
            JobStage.GENERATE_USER_FLOWS.value: "사용자 흐름 작성",
            JobStage.DEFINE_MVP_SCOPE.value: "MVP 범위 정의",
            JobStage.ARCHITECTURE_PLANNING.value: "아키텍처 계획 작성",
            JobStage.PROJECT_SCAFFOLDING.value: "프로젝트 스캐폴딩 작성",
            JobStage.PLAN_WITH_GEMINI.value: "제미나이 플래너 작성",
            JobStage.DESIGN_WITH_CODEX.value: "코덱스 디자이너 작성",
            JobStage.COPYWRITER_TASK.value: "카피라이터 작성",
            JobStage.DOCUMENTATION_TASK.value: "기술 문서 작성",
            JobStage.IMPLEMENT_WITH_CODEX.value: "코덱스 구현자 작성",
            JobStage.SUMMARIZE_CODE_CHANGES.value: "코드 변경 요약 작성",
            JobStage.TEST_AFTER_IMPLEMENT.value: "구현 후 테스트 리포트 작성",
            JobStage.UX_E2E_REVIEW.value: "UX E2E 검수 리포트 작성",
            JobStage.COMMIT_IMPLEMENT.value: "구현 커밋 단계 문서 정리",
            JobStage.REVIEW_WITH_GEMINI.value: "제미나이 리뷰어 작성",
            JobStage.PRODUCT_REVIEW.value: "제품 품질 리뷰 작성",
            JobStage.IMPROVEMENT_STAGE.value: "개선 루프 계획 작성",
            JobStage.FIX_WITH_CODEX.value: "코덱스 수정자 작성",
            JobStage.TEST_AFTER_FIX.value: "수정 후 테스트 리포트 작성",
            JobStage.COMMIT_FIX.value: "수정 커밋 단계 문서 정리",
            "gh_read_issue": "이슈 읽기 문서 반영",
            "write_spec": "스펙 문서 작성",
            "idea_to_product_brief": "제품 정의 브리프 작성",
            "generate_user_flows": "사용자 흐름 작성",
            "define_mvp_scope": "MVP 범위 정의",
            "architecture_planning": "아키텍처 계획 작성",
            "project_scaffolding": "프로젝트 스캐폴딩 작성",
            "gemini_plan": "제미나이 플래너 작성",
            "designer_task": "코덱스 디자이너 작성",
            "publisher_task": "퍼블리셔 작성",
            "copywriter_task": "카피라이터 작성",
            "documentation_task": "기술 문서 작성",
            "codex_implement": "코덱스 구현자 작성",
            "code_change_summary": "코드 변경 요약 작성",
            "test_after_implement": "구현 후 테스트 리포트 작성",
            "ux_e2e_review": "UX E2E 검수 리포트 작성",
            "commit_implement": "구현 커밋 단계 문서 정리",
            "gemini_review": "제미나이 리뷰어 작성",
            "product_review": "제품 품질 리뷰 작성",
            "improvement_stage": "개선 루프 계획 작성",
            "codex_fix": "코덱스 수정자 작성",
            "coder_fix_from_test_report": "코덱스 수정자 작성",
            "test_after_fix": "수정 후 테스트 리포트 작성",
            "test_after_fix_final": "수정 후 테스트 리포트 작성",
            "tester_run_e2e": "E2E/타입별 검증 리포트 작성",
            "tester_retest_e2e": "E2E/타입별 재검증 리포트 작성",
            "commit_fix": "수정 커밋 단계 문서 정리",
        }
        return stage_map.get(stage_name, stage_name.replace("_", " "))
