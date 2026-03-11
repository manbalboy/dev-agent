"""Shell execution and test-stage runtime helpers."""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
from typing import Callable, Dict, List

from app.models import JobRecord, JobStage


class ShellTestRuntime:
    """Encapsulate shell execution and test reporting behavior."""

    def __init__(
        self,
        *,
        settings,
        shell_executor,
        shell_executor_accepts_heartbeat: bool,
        shell_executor_accepts_env: bool,
        touch_job_heartbeat: Callable[..., None],
        actor_log_writer,
        infer_actor_from_command,
        set_stage,
        append_actor_log,
        is_long_track,
    ) -> None:
        self.settings = settings
        self.shell_executor = shell_executor
        self.shell_executor_accepts_heartbeat = shell_executor_accepts_heartbeat
        self.shell_executor_accepts_env = shell_executor_accepts_env
        self.touch_job_heartbeat = touch_job_heartbeat
        self.actor_log_writer = actor_log_writer
        self.infer_actor_from_command = infer_actor_from_command
        self.set_stage = set_stage
        self.append_actor_log = append_actor_log
        self.is_long_track = is_long_track

    def execute_shell_command(
        self,
        *,
        command: str,
        cwd: Path,
        log_writer,
        check: bool,
        command_purpose: str,
    ):
        """Run one shell command and attach heartbeat hooks when supported."""

        kwargs = {
            "command": command,
            "cwd": cwd,
            "log_writer": log_writer,
            "check": check,
            "command_purpose": command_purpose,
        }
        if self.shell_executor_accepts_heartbeat:
            kwargs["heartbeat_callback"] = self.touch_job_heartbeat
            kwargs["heartbeat_interval_seconds"] = 10.0
        if self.shell_executor_accepts_env:
            kwargs["extra_env"] = dict(getattr(self, "extra_env", {}) or {})
        return self.shell_executor(**kwargs)

    def run_shell(
        self,
        *,
        command: str,
        cwd: Path,
        log_path: Path,
        purpose: str,
    ):
        """Run shell command with shared logging and strict error handling."""

        self.touch_job_heartbeat(force=True)
        result = self.execute_shell_command(
            command=command,
            cwd=cwd,
            log_writer=self.actor_log_writer(
                log_path,
                self.infer_actor_from_command(command, purpose),
            ),
            check=True,
            command_purpose=purpose,
        )
        self.touch_job_heartbeat(force=True)
        return result

    def stage_run_tests(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        stage: JobStage,
        log_path: Path,
    ) -> bool:
        """Run stage-aware tests and persist markdown reports."""

        self.set_stage(job.job_id, stage, log_path)
        test_results: List[Dict[str, object]] = []
        primary_command = self.resolve_test_command(stage, secondary=False)
        primary_command = self.wrap_test_command_with_timeout(primary_command, log_path)

        primary_name = self.settings.tester_primary_name
        primary_slug = self.safe_slug(primary_name).upper()
        primary_result = self.execute_shell_command(
            command=primary_command,
            cwd=repository_path,
            log_writer=self.actor_log_writer(log_path, f"TESTER_{primary_slug}"),
            check=False,
            command_purpose=f"tests ({stage.value}) [{primary_name}]",
        )
        primary_report = self.write_test_report(
            repository_path=repository_path,
            stage=stage,
            command_result=primary_result,
            tester_name=primary_name,
            report_suffix="",
        )
        self.append_actor_log(
            log_path,
            f"TESTER_{primary_slug}",
            f"Test report written: {primary_report.name}",
        )
        test_results.append({"name": primary_name, "result": primary_result, "report": primary_report})

        if self.is_long_track(job):
            secondary_command = self.resolve_test_command(stage, secondary=True)
            secondary_command = self.wrap_test_command_with_timeout(secondary_command, log_path)
            secondary_name = self.settings.tester_secondary_name
            secondary_slug = self.safe_slug(secondary_name).upper()
            secondary_result = self.execute_shell_command(
                command=secondary_command,
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, f"TESTER_{secondary_slug}"),
                check=False,
                command_purpose=f"tests ({stage.value}) [{secondary_name}]",
            )
            secondary_report = self.write_test_report(
                repository_path=repository_path,
                stage=stage,
                command_result=secondary_result,
                tester_name=secondary_name,
                report_suffix=secondary_slug,
            )
            self.append_actor_log(
                log_path,
                f"TESTER_{secondary_slug}",
                f"Test report written: {secondary_report.name}",
            )
            test_results.append({"name": secondary_name, "result": secondary_result, "report": secondary_report})

        failed_reports = [
            str(item["report"].name)
            for item in test_results
            if int(getattr(item["result"], "exit_code", 1)) != 0
        ]
        if failed_reports:
            reason = (
                f"Tests failed at stage '{stage.value}'. "
                f"See {', '.join(failed_reports)} and job logs for details."
            )
            self.write_test_failure_reason(
                repository_path=repository_path,
                stage=stage,
                reason=reason,
            )
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"{reason} Continuing workflow by policy.",
            )
            return False
        return True

    def resolve_test_command(self, stage: JobStage, secondary: bool) -> str:
        """Pick stage-aware tester command with conservative fallbacks."""

        if stage == JobStage.TEST_AFTER_IMPLEMENT:
            if secondary:
                return (
                    self.settings.test_command_secondary_implement
                    or self.settings.test_command_secondary
                    or self.settings.test_command
                )
            return self.settings.test_command_implement or self.settings.test_command

        if stage in {JobStage.TEST_AFTER_FIX, JobStage.UX_E2E_REVIEW}:
            if secondary:
                return (
                    self.settings.test_command_secondary_fix
                    or self.settings.test_command_secondary
                    or self.settings.test_command
                )
            return self.settings.test_command_fix or self.settings.test_command

        if secondary:
            return self.settings.test_command_secondary or self.settings.test_command
        return self.settings.test_command

    def wrap_test_command_with_timeout(self, command: str, log_path: Path) -> str:
        """Wrap test command with shell timeout when available."""

        timeout_seconds = self.test_command_timeout_seconds()
        if timeout_seconds <= 0:
            return command
        if not self.has_timeout_utility():
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                "timeout utility not found. Running tests without process-level timeout wrapper.",
            )
            return command
        return f"timeout --preserve-status {timeout_seconds}s {command}"

    @staticmethod
    def has_timeout_utility() -> bool:
        """Return True when GNU/BSD timeout utility is available."""

        return shutil.which("timeout") is not None

    @staticmethod
    def test_command_timeout_seconds() -> int:
        """Read per-test-command timeout in seconds (0 disables wrapping)."""

        raw = (os.getenv("AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS", "900") or "").strip()
        try:
            value = int(raw)
        except ValueError:
            return 900
        return max(0, min(7200, value))

    def write_test_failure_reason(
        self,
        *,
        repository_path: Path,
        stage: JobStage,
        reason: str,
    ) -> None:
        """Persist test failure reason without aborting the workflow."""

        report_path = repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md"
        content = [
            "# TEST FAILURE REASON",
            "",
            f"- Stage: `{stage.value}`",
            f"- Reason: {reason}",
            "",
            "## Next Step",
            "- Continue workflow and let following stages address issues.",
            "",
        ]
        report_path.write_text("\n".join(content), encoding="utf-8")

    def write_test_report(
        self,
        *,
        repository_path: Path,
        stage: JobStage,
        command_result: object,
        tester_name: str,
        report_suffix: str,
    ) -> Path:
        """Persist stage-level test summary in markdown for dashboard visibility."""

        command = str(getattr(command_result, "command", self.settings.test_command))
        exit_code = int(getattr(command_result, "exit_code", 1))
        duration = float(getattr(command_result, "duration_seconds", 0.0))
        stdout = str(getattr(command_result, "stdout", ""))
        stderr = str(getattr(command_result, "stderr", ""))
        passed = exit_code == 0

        counters = self.extract_test_counters(stdout + "\n" + stderr)
        passed_count = counters.get("passed", 0)
        failed_count = counters.get("failed", 0)
        skipped_count = counters.get("skipped", 0)
        errors_count = counters.get("errors", 0)

        pass_lines: List[str] = []
        fail_lines: List[str] = []
        if passed:
            pass_lines.append("테스트 명령이 종료코드 0으로 완료되었습니다.")
        else:
            fail_lines.append(f"테스트 명령이 종료코드 {exit_code}로 실패했습니다.")
            if exit_code == 124:
                fail_lines.append(
                    "테스트 명령이 시간 제한으로 종료되었습니다(timeout, exit 124)."
                )
        if passed_count > 0:
            pass_lines.append(f"통과된 테스트 수를 감지했습니다: {passed_count}")
        if skipped_count > 0:
            pass_lines.append(f"스킵된 테스트 수를 감지했습니다: {skipped_count}")
        if failed_count > 0:
            fail_lines.append(f"실패한 테스트 수를 감지했습니다: {failed_count}")
        if errors_count > 0:
            fail_lines.append(f"에러 테스트 수를 감지했습니다: {errors_count}")
        if not pass_lines:
            pass_lines.append("출력에서 명시적인 통과 카운트를 찾지 못했습니다.")
        if not fail_lines:
            fail_lines.append("출력에서 명시적인 실패 카운트를 찾지 못했습니다.")

        report = [
            "# TEST REPORT",
            "",
            f"- Stage: `{stage.value}`",
            f"- Tester: `{tester_name}`",
            f"- Status: `{'PASS' if passed else 'FAIL'}`",
            f"- Exit code: `{exit_code}`",
            f"- Duration: `{duration:.2f}s`",
            f"- Command: `{command}`",
            "",
            "## 통과한 항목",
        ]
        report.extend(f"- {line}" for line in pass_lines)
        report.append("")
        report.append("## 통과하지 못한 항목")
        report.extend(f"- {line}" for line in fail_lines)
        report.append("")
        report.append("## 요약 카운트")
        report.append(f"- passed: `{passed_count}`")
        report.append(f"- failed: `{failed_count}`")
        report.append(f"- skipped: `{skipped_count}`")
        report.append(f"- errors: `{errors_count}`")
        report.append("")
        report.append("## stdout (tail)")
        report.append("```text")
        report.append(self.tail_text(stdout, 120))
        report.append("```")
        report.append("")
        report.append("## stderr (tail)")
        report.append("```text")
        report.append(self.tail_text(stderr, 120))
        report.append("```")
        report.append("")

        if report_suffix:
            report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}_{report_suffix}.md"
        else:
            report_path = repository_path / f"TEST_REPORT_{stage.value.upper()}.md"
        report_path.write_text("\n".join(report), encoding="utf-8")
        return report_path

    @staticmethod
    def extract_test_counters(text: str) -> Dict[str, int]:
        """Extract common test counters from pytest/jest/vitest-like outputs."""

        lowered = text.lower()
        counters: Dict[str, int] = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        }
        for key, pattern in {
            "passed": r"(\d+)\s+passed",
            "failed": r"(\d+)\s+failed",
            "skipped": r"(\d+)\s+skipped",
            "errors": r"(\d+)\s+errors?",
        }.items():
            matches = re.findall(pattern, lowered)
            if matches:
                counters[key] = int(matches[-1])
        return counters

    @staticmethod
    def tail_text(text: str, max_lines: int) -> str:
        """Return only tail lines so report size stays readable."""

        stripped = text.strip()
        if not stripped:
            return "(empty)"
        lines = stripped.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[-max_lines:])

    @staticmethod
    def safe_slug(value: str) -> str:
        """Convert label text to safe uppercase slug."""

        cleaned = "".join(ch if ch.isalnum() else "_" for ch in (value or "").strip().lower())
        normalized = re.sub(r"_+", "_", cleaned).strip("_")
        return normalized or "tester"
