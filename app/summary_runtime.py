"""Summary and helper-AI execution runtime for orchestrator stages."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any, Callable, Dict, List, Optional

from app.log_signal_utils import summarize_optional_route_error
from app.models import JobRecord, JobStage, utc_now_iso
from app.prompt_builder import build_commit_message_prompt, build_pr_summary_prompt


class SummaryRuntime:
    """Encapsulate summary/documentation helper execution used by orchestrator."""

    def __init__(
        self,
        *,
        command_templates,
        run_shell: Callable[..., object],
        append_log: Callable[[Path, str], None],
        append_actor_log: Callable[[Path, str, str], None],
        docs_file: Callable[[Path, str], Path],
        build_template_variables: Callable[[JobRecord, Dict[str, Path], Path], Dict[str, str]],
        actor_log_writer: Callable[[Path, str], Callable[[str], None]],
        template_for_route: Callable[[str], str],
        find_configured_template_for_route: Callable[[str], Optional[str]],
        set_stage: Callable[[str, JobStage, Path], None],
        parse_porcelain_path: Callable[[str], str],
        is_long_track: Callable[[JobRecord], bool],
    ) -> None:
        self.command_templates = command_templates
        self.run_shell = run_shell
        self.append_log = append_log
        self.append_actor_log = append_actor_log
        self.docs_file = docs_file
        self.build_template_variables = build_template_variables
        self.actor_log_writer = actor_log_writer
        self.template_for_route = template_for_route
        self.find_configured_template_for_route = find_configured_template_for_route
        self.set_stage = set_stage
        self.parse_porcelain_path = parse_porcelain_path
        self.is_long_track = is_long_track

    def stage_summarize_code_changes(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
    ) -> None:
        """Summarize current working tree changes into CODE_CHANGE_SUMMARY.md."""

        self.set_stage(job.job_id, JobStage.SUMMARIZE_CODE_CHANGES, log_path)
        status_result = self.run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} status --porcelain",
            cwd=repository_path,
            log_path=log_path,
            purpose="git status for code change summary",
        )
        numstat_result = self.run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} diff --numstat",
            cwd=repository_path,
            log_path=log_path,
            purpose="git diff --numstat for code change summary",
        )

        changed_files: List[Dict[str, str]] = []
        for raw_line in status_result.stdout.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            status_code = line[:2].strip() or line[:2]
            path_text = line[3:].strip() if len(line) > 3 else "(unknown)"
            changed_files.append({"status": status_code, "path": path_text})

        numstats: Dict[str, Dict[str, str]] = {}
        for raw_line in numstat_result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            numstats[parts[2]] = {"added": parts[0], "deleted": parts[1]}

        summary_path = self.docs_file(repository_path, "CODE_CHANGE_SUMMARY.md")
        fallback_lines = [
            "# CODE CHANGE SUMMARY",
            "",
            f"- Job: `{job.job_id}`",
            f"- Issue: `#{job.issue_number}`",
            f"- Stage: `{JobStage.SUMMARIZE_CODE_CHANGES.value}`",
            f"- Generated at: `{utc_now_iso()}`",
            "",
        ]

        if not changed_files:
            fallback_lines.extend(["## Changed Files", "- 변경 파일이 감지되지 않았습니다.", ""])
        else:
            fallback_lines.extend(
                [
                    "## Changed Files",
                    "| Status | Path | Added | Deleted |",
                    "|---|---|---:|---:|",
                ]
            )
            for item in changed_files:
                path_key = item["path"]
                stat = numstats.get(path_key, {"added": "-", "deleted": "-"})
                fallback_lines.append(
                    f"| `{item['status']}` | `{path_key}` | `{stat['added']}` | `{stat['deleted']}` |"
                )
            fallback_lines.append("")

        fallback_lines.extend(
            [
                "## Notes",
                "- 본 문서는 구현 직후 변경 파일을 빠르게 검토하기 위한 자동 요약입니다.",
                "- 이후 테스트/리뷰/수정 단계에서 변경 내역이 추가될 수 있습니다.",
                "",
            ]
        )

        prompt = self._build_code_change_summary_prompt(job=job, changed_files=changed_files, numstats=numstats)
        helper_summary = self._summarize_changes_with_helper(
            job=job,
            prompt=prompt,
            repository_path=repository_path,
            log_path=log_path,
        )
        if helper_summary:
            summary_path.write_text(helper_summary.rstrip() + "\n", encoding="utf-8")
            self.append_actor_log(
                log_path,
                "CODEX_HELPER",
                f"Wrote code change summary via helper route: {summary_path.name}",
            )
            return

        summary_path.write_text("\n".join(fallback_lines), encoding="utf-8")
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"Wrote code change summary with fallback: {summary_path.name}",
        )

    def prepare_commit_summary_with_ai(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        changed_paths: List[str],
        log_path: Path,
    ) -> str:
        """Generate one-line commit summary using configured helper routes."""

        summary = self._prepare_commit_summary_with_helper(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            changed_paths=changed_paths,
            log_path=log_path,
        )
        if self.is_usable_commit_summary(summary):
            return summary

        summary = self._prepare_commit_summary_with_template(
            job=job,
            repository_path=repository_path,
            stage_name=stage_name,
            commit_type=commit_type,
            log_path=log_path,
        )
        if self.is_usable_commit_summary(summary):
            return summary
        return ""

    def stage_prepare_pr_summary(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> Optional[Path]:
        """Generate PR summary markdown with configured summary route before PR creation."""

        template_name = self.find_configured_template_for_route("pr_summary")
        if not template_name:
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "PR summary template not configured; using default PR body.",
            )
            return None

        prompt_path = self.docs_file(repository_path, "PR_SUMMARY_PROMPT.md")
        output_path = self.docs_file(repository_path, "PR_SUMMARY.md")
        prompt_path.write_text(
            build_pr_summary_prompt(
                spec_path=str(paths["spec"]),
                plan_path=str(paths["plan"]),
                review_path=str(paths["review"]),
                design_path=str(paths.get("design", self.docs_file(repository_path, "DESIGN_SYSTEM.md"))),
                issue_title=job.issue_title,
                issue_number=job.issue_number,
                is_long_term=self.is_long_track(job),
            ),
            encoding="utf-8",
        )

        self.append_actor_log(log_path, "ORCHESTRATOR", "Running PR summary route.")
        try:
            result = self.command_templates.run_template(
                template_name=template_name,
                variables={
                    **self.build_template_variables(job, paths, prompt_path),
                    "last_error": "",
                    "pr_summary_path": str(output_path),
                },
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "PR_SUMMARY"),
            )
            if not output_path.exists() and result.stdout.strip():
                output_path.write_text(result.stdout, encoding="utf-8")
            if output_path.exists():
                self.append_actor_log(
                    log_path,
                    "PR_SUMMARY",
                    f"PR summary written: {output_path.name}",
                )
                return output_path
            self.append_actor_log(
                log_path,
                "PR_SUMMARY",
                "PR summary output missing; fallback to default PR body.",
            )
            return None
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "PR_SUMMARY",
                "PR summary route unavailable; using default PR body: "
                f"{summarize_optional_route_error(error, actor='PR_SUMMARY')}",
            )
            return None

    def _prepare_commit_summary_with_template(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        log_path: Path,
    ) -> str:
        """Generate one-line Korean commit summary using configured summary route."""

        template_name = self.find_configured_template_for_route("commit_summary")
        if not template_name:
            return ""

        prompt_path = self.docs_file(repository_path, f"COMMIT_MESSAGE_PROMPT_{stage_name.upper()}.md")
        output_path = self.docs_file(repository_path, f"COMMIT_MESSAGE_{stage_name.upper()}.txt")
        prompt_path.write_text(
            build_commit_message_prompt(
                spec_path=str(self.docs_file(repository_path, "SPEC.md")),
                plan_path=str(self.docs_file(repository_path, "PLAN.md")),
                review_path=str(self.docs_file(repository_path, "REVIEW.md")),
                design_path=str(self.docs_file(repository_path, "DESIGN_SYSTEM.md")),
                stage_name=stage_name,
                commit_type=commit_type,
            ),
            encoding="utf-8",
        )

        try:
            self.command_templates.run_template(
                template_name=template_name,
                variables={
                    **self.build_template_variables(
                        job,
                        {
                            "spec": self.docs_file(repository_path, "SPEC.md"),
                            "plan": self.docs_file(repository_path, "PLAN.md"),
                            "review": self.docs_file(repository_path, "REVIEW.md"),
                            "design": self.docs_file(repository_path, "DESIGN_SYSTEM.md"),
                            "status": self.docs_file(repository_path, "STATUS.md"),
                        },
                        prompt_path,
                    ),
                    "commit_message_path": str(output_path),
                    "last_error": "",
                    "pr_summary_path": str(self.docs_file(repository_path, "PR_SUMMARY.md")),
                },
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "TECH_WRITER"),
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "TECH_WRITER",
                "Commit summary route unavailable; using deterministic fallback: "
                f"{summarize_optional_route_error(error, actor='TECH_WRITER')}",
            )
            return ""

        candidate = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
        if not candidate:
            return ""
        return self.sanitize_commit_summary(candidate)

    def _prepare_commit_summary_with_helper(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        stage_name: str,
        commit_type: str,
        changed_paths: List[str],
        log_path: Path,
    ) -> str:
        """Try to generate one-line commit summary with helper route."""

        prompt_lines = [
            "다음 변경사항의 커밋 제목 요약 1줄만 작성하세요.",
            "규칙:",
            "- 한국어",
            "- 12~72자",
            "- 접두어(feat:, fix:, docs:)는 제외",
            "- 불필요한 따옴표/코드블록/번호 금지",
            "",
            f"메타: issue #{job.issue_number}, stage={stage_name}, type={commit_type}",
            "변경 파일:",
        ]
        unique_paths: List[str] = []
        seen = set()
        for path in changed_paths:
            key = str(path).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_paths.append(key)
            if len(unique_paths) >= 24:
                break
        if not unique_paths:
            prompt_lines.append("- 변경 파일 정보를 찾지 못함")
        else:
            for path in unique_paths:
                prompt_lines.append(f"- {path}")
        prompt = "\n".join(prompt_lines).strip() + "\n"
        prompt_path = self.docs_file(repository_path, f"CODEX_HELPER_COMMIT_PROMPT_{stage_name.upper()}.md")
        prompt_path.write_text(prompt, encoding="utf-8")

        if not self.find_configured_template_for_route("codex_helper"):
            return ""

        try:
            result = self.command_templates.run_template(
                template_name=self.template_for_route("codex_helper"),
                variables={
                    "repository": job.repository,
                    "issue_number": str(job.issue_number),
                    "issue_title": job.issue_title,
                    "issue_url": job.issue_url,
                    "branch_name": job.branch_name,
                    "work_dir": str(repository_path),
                    "prompt_file": str(prompt_path),
                },
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "CODEX_HELPER"),
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "CODEX_HELPER",
                "Helper commit summary route unavailable; falling back to template/default summary: "
                f"{summarize_optional_route_error(error, actor='CODEX_HELPER')}",
            )
            return ""

        if int(getattr(result, "exit_code", 1)) != 0:
            return ""
        return self.sanitize_commit_summary(str(getattr(result, "stdout", "")).strip())

    def _build_code_change_summary_prompt(
        self,
        *,
        job: JobRecord,
        changed_files: List[Dict[str, str]],
        numstats: Dict[str, Dict[str, str]],
    ) -> str:
        """Create helper prompt for CODE_CHANGE_SUMMARY.md generation."""

        lines = [
            "다음 변경 내역을 바탕으로 CODE_CHANGE_SUMMARY.md 본문(markdown)만 생성하세요.",
            "",
            "형식 규칙:",
            "- 제목은 반드시 '# CODE CHANGE SUMMARY'",
            "- 한국어로 작성",
            "- 다음 섹션 포함: Changed Files, Notes",
            "- Changed Files는 표 형식(Status, Path, Added, Deleted)",
            "- 불필요한 서론/결론/코드블록 금지",
            "",
            "메타:",
            f"- Job: {job.job_id}",
            f"- Issue: #{job.issue_number}",
            f"- Stage: {JobStage.SUMMARIZE_CODE_CHANGES.value}",
            "",
            "변경 파일 목록:",
        ]
        if not changed_files:
            lines.append("- 변경 파일 없음")
        else:
            for item in changed_files:
                path_key = item["path"]
                stat = numstats.get(path_key, {"added": "-", "deleted": "-"})
                lines.append(f"- {item['status']} | {path_key} | +{stat['added']} / -{stat['deleted']}")
        lines.append("")
        return "\n".join(lines)

    def _summarize_changes_with_helper(
        self,
        *,
        job: JobRecord,
        prompt: str,
        repository_path: Path,
        log_path: Path,
    ) -> Optional[str]:
        """Try helper-route summary generation and return markdown text."""

        prompt_path = self.docs_file(repository_path, "CODEX_HELPER_SUMMARY_PROMPT.md")
        prompt_path.write_text(prompt, encoding="utf-8")
        if not self.find_configured_template_for_route("codex_helper"):
            return None
        try:
            result = self.command_templates.run_template(
                template_name=self.template_for_route("codex_helper"),
                variables={
                    "repository": job.repository,
                    "issue_number": str(job.issue_number),
                    "issue_title": job.issue_title,
                    "issue_url": job.issue_url,
                    "branch_name": job.branch_name,
                    "work_dir": str(repository_path),
                    "prompt_file": str(prompt_path),
                },
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "CODEX_HELPER"),
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "CODEX_HELPER",
                "Helper route unavailable; using built-in code change summary fallback: "
                f"{summarize_optional_route_error(error, actor='CODEX_HELPER')}",
            )
            return None

        if int(getattr(result, "exit_code", 1)) != 0:
            return None
        output = str(getattr(result, "stdout", "")).strip()
        if not output:
            return None
        if "# CODE CHANGE SUMMARY" not in output:
            output = "# CODE CHANGE SUMMARY\n\n" + output
        return output

    @staticmethod
    def sanitize_commit_summary(raw: str) -> str:
        """Normalize model output into a clean one-line commit summary."""

        text = str(raw or "").strip()
        if not text:
            return ""
        first = text.splitlines()[0].strip()
        first = first.strip("`").strip()
        first = re.sub(r"^\s*[-*#>\d\.\)\(]+\s*", "", first)
        first = re.sub(r"^\s*(feat|fix|docs|chore|refactor|style|test)\s*:\s*", "", first, flags=re.IGNORECASE)
        first = re.sub(r"\s+", " ", first).strip()
        return first[:120]

    @staticmethod
    def is_usable_commit_summary(summary: str) -> bool:
        """Validate summary quality before using it as commit title body."""

        text = str(summary or "").strip()
        if len(text) < 8:
            return False
        lowered = text.lower()
        blocked = {"n/a", "없음", "none", "commit message", "요약 없음", "변경사항 없음"}
        if lowered in blocked:
            return False
        if "```" in text:
            return False
        return True
