"""UX review and screenshot runtime helpers for orchestrator."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Dict, List, Optional

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage


class UxReviewRuntime:
    """Encapsulate UX E2E screenshot capture and markdown summary helpers."""

    def __init__(
        self,
        *,
        stage_run_tests,
        deploy_preview_and_smoke_test,
        run_shell,
        append_actor_log,
        docs_file,
    ) -> None:
        self.stage_run_tests = stage_run_tests
        self.deploy_preview_and_smoke_test = deploy_preview_and_smoke_test
        self.run_shell = run_shell
        self.append_actor_log = append_actor_log
        self.docs_file = docs_file

    def stage_ux_e2e_review(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        """Run UX-focused E2E checks with PC/mobile screenshots and summary markdown."""

        tests_passed = self.stage_run_tests(
            job=job,
            repository_path=repository_path,
            stage=JobStage.UX_E2E_REVIEW,
            log_path=log_path,
        )
        preview_info = self.deploy_preview_and_smoke_test(job, repository_path, log_path)
        screenshot_info = self.capture_ux_screenshots(
            repository_path=repository_path,
            preview_info=preview_info,
            log_path=log_path,
        )
        self.write_ux_review_markdown(
            repository_path=repository_path,
            spec_path=paths.get("spec"),
            preview_info=preview_info,
            screenshot_info=screenshot_info,
            tests_passed=tests_passed,
        )

    def capture_ux_screenshots(
        self,
        *,
        repository_path: Path,
        preview_info: Dict[str, str],
        log_path: Path,
    ) -> Dict[str, Dict[str, str]]:
        """Capture desktop/mobile screenshots against preview URL."""

        artifacts_dir = repository_path / "artifacts" / "ux"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        screenshot_url = str(preview_info.get("local_url", "")).strip() or str(
            preview_info.get("external_url", "")
        ).strip()

        results: Dict[str, Dict[str, str]] = {
            "pc": {"status": "skipped", "path": "artifacts/ux/pc.png", "note": "preview unavailable"},
            "mobile": {"status": "skipped", "path": "artifacts/ux/mobile.png", "note": "preview unavailable"},
        }
        if not screenshot_url:
            return results

        targets = [
            ("pc", "Desktop Chrome", artifacts_dir / "pc.png"),
            ("mobile", "iPhone 13", artifacts_dir / "mobile.png"),
        ]
        for key, device, target_path in targets:
            command = (
                "npx -y playwright screenshot "
                f"--device={shlex.quote(device)} "
                f"{shlex.quote(screenshot_url)} "
                f"{shlex.quote(str(target_path))}"
            )
            try:
                self.run_shell(
                    command=command,
                    cwd=repository_path,
                    log_path=log_path,
                    purpose=f"ux screenshot capture ({key})",
                )
                results[key] = {
                    "status": "captured",
                    "path": str(target_path.relative_to(repository_path)),
                    "note": f"{device} capture completed",
                }
            except CommandExecutionError as error:
                results[key] = {
                    "status": "failed",
                    "path": str(target_path.relative_to(repository_path)),
                    "note": str(error),
                }
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"UX screenshot capture failed ({key}): {error}",
                )
        return results

    def write_ux_review_markdown(
        self,
        *,
        repository_path: Path,
        spec_path: Optional[Path],
        preview_info: Dict[str, str],
        screenshot_info: Dict[str, Dict[str, str]],
        tests_passed: bool,
    ) -> None:
        """Write UX_REVIEW.md with screenshot status and next action guidance."""

        checklist = self.extract_spec_checklist(spec_path)
        verdict = (
            "PASS"
            if tests_passed
            and screenshot_info.get("pc", {}).get("status") == "captured"
            and screenshot_info.get("mobile", {}).get("status") == "captured"
            else "NEEDS_FIX"
        )
        review_lines = [
            "# UX REVIEW",
            "",
            "## Summary",
            f"- Stage: `{JobStage.UX_E2E_REVIEW.value}`",
            f"- Verdict: `{verdict}`",
            f"- Test status: `{'PASS' if tests_passed else 'FAIL'}`",
            f"- Preview URL: {preview_info.get('external_url', 'n/a')}",
            f"- Health URL: {preview_info.get('health_url', 'n/a')}",
            "",
            "## Screenshot Artifacts",
            (
                f"- PC: `{screenshot_info.get('pc', {}).get('path', 'n/a')}` "
                f"({screenshot_info.get('pc', {}).get('status', 'unknown')}) "
                f"- {screenshot_info.get('pc', {}).get('note', '')}"
            ),
            (
                f"- Mobile: `{screenshot_info.get('mobile', {}).get('path', 'n/a')}` "
                f"({screenshot_info.get('mobile', {}).get('status', 'unknown')}) "
                f"- {screenshot_info.get('mobile', {}).get('note', '')}"
            ),
            "",
            "## Intent Checklist (from SPEC)",
        ]
        if checklist:
            review_lines.extend(f"- {line}" for line in checklist)
        else:
            review_lines.append("- SPEC에서 체크리스트 항목을 찾지 못했습니다. 핵심 요구사항 수동 확인 필요.")
        review_lines.extend(
            [
                "",
                "## Next Action",
                "- 다음 코더 단계에서 UX_REVIEW.md의 실패/누락 항목을 우선 수정한다.",
                "- PC/Mobile 스크린샷이 모두 captured 상태가 될 때까지 반복한다.",
                "",
            ]
        )
        self.docs_file(repository_path, "UX_REVIEW.md").write_text(
            "\n".join(review_lines),
            encoding="utf-8",
        )

    @staticmethod
    def extract_spec_checklist(spec_path: Optional[Path]) -> List[str]:
        """Extract concise checklist lines from SPEC.md."""

        if spec_path is None or not spec_path.exists():
            return []
        try:
            lines = spec_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

        checklist: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- ") or re.match(r"^\d+\.\s+", stripped):
                checklist.append(stripped.lstrip("- ").strip())
            if len(checklist) >= 8:
                break
        return checklist
