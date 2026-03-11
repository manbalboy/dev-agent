"""Tests for recovery runtime extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.command_runner import CommandExecutionError, CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.recovery_runtime import RecoveryRuntime


class FakeTemplateRunner:
    """Minimal template runner for failure-assistant tests."""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.calls: list[str] = []

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        self.calls.append(template_name)
        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout=self.stdout,
            stderr="",
            duration_seconds=0.0,
        )


def _make_job(job_id: str = "job-recovery-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=101,
        issue_title="recovery runtime test",
        issue_url="https://github.com/owner/repo/issues/101",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-101-recovery-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(
    tmp_path: Path,
    *,
    stage_results: list[bool] | None = None,
    recovery_enabled: bool = False,
    helper_stdout: str = "",
    helper_available: bool = False,
    escalation_enabled: bool = False,
):
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "job.log"
    log_entries: list[tuple[str, str]] = []
    fix_calls: list[str] = []
    commit_calls: list[str] = []
    escalation_calls: list[tuple[str, str]] = []
    runner = FakeTemplateRunner(stdout=helper_stdout)

    results = list(stage_results or [True])
    call_state = {"index": 0}

    def docs_file(base_path: Path, name: str) -> Path:
        target = base_path / "_docs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def stage_run_tests(*, job, repository_path: Path, stage: JobStage, log_path: Path) -> bool:
        index = min(call_state["index"], len(results) - 1)
        passed = results[index]
        call_state["index"] += 1
        if not passed:
            (repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md").write_text(
                "# TEST FAILURE REASON\n\n- Reason: test failed\n",
                encoding="utf-8",
            )
        return passed

    runtime = RecoveryRuntime(
        command_templates=runner,
        stage_run_tests=stage_run_tests,
        append_actor_log=lambda log_path, actor, message: log_entries.append((actor, message)),
        stage_fix_with_codex=lambda job, repository_path, paths, log_path: fix_calls.append(job.job_id),
        commit_markdown_changes_after_stage=lambda job, repository_path, stage_name, log_path: commit_calls.append(stage_name),
        is_recovery_mode_enabled=lambda: recovery_enabled,
        find_configured_template_for_route=lambda route: route if helper_available and route == "codex_helper" else None,
        template_for_route=lambda route: route,
        build_template_variables=lambda job, docs, prompt_path: {"prompt_path": str(prompt_path)},
        docs_file=docs_file,
        actor_log_writer=lambda log_path, actor: lambda message: log_entries.append((actor, message)),
        is_escalation_enabled=lambda: escalation_enabled,
        run_optional_escalation=lambda job_id, log_path, reason: escalation_calls.append((job_id, reason)),
    )

    return {
        "runtime": runtime,
        "job": _make_job(),
        "repository_path": repository_path,
        "log_path": log_path,
        "logs": log_entries,
        "fix_calls": fix_calls,
        "commit_calls": commit_calls,
        "template_runner": runner,
        "escalation_calls": escalation_calls,
    }


def test_recovery_runtime_failure_assistant_writes_analysis_from_helper(tmp_path: Path) -> None:
    built = _build_runtime(
        tmp_path,
        helper_available=True,
        helper_stdout="# FAILURE ANALYSIS\n\n- root cause\n",
    )

    built["runtime"].run_failure_assistant(
        job=built["job"],
        repository_path=built["repository_path"],
        log_path=built["log_path"],
        reason="tests failed",
    )

    output_path = built["repository_path"] / "_docs" / "FAILURE_ANALYSIS.md"
    prompt_path = built["repository_path"] / "_docs" / "FAILURE_ANALYSIS_PROMPT.md"
    assert output_path.read_text(encoding="utf-8") == "# FAILURE ANALYSIS\n\n- root cause\n"
    assert "reason: tests failed" in prompt_path.read_text(encoding="utf-8")
    assert built["template_runner"].calls == ["codex_helper"]
    assert any("Failure analysis written" in message for _, message in built["logs"])


def test_recovery_runtime_is_recoverable_failure_filters_auth_and_keeps_test_failures(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    stage = JobStage.TEST_AFTER_IMPLEMENT

    (repo_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md").write_text(
        "permission denied while fetching artifact",
        encoding="utf-8",
    )
    assert RecoveryRuntime.is_recoverable_failure(repo_path, stage) is False

    (repo_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md").write_text(
        "module not found and test failed",
        encoding="utf-8",
    )
    assert RecoveryRuntime.is_recoverable_failure(repo_path, stage) is True


def test_recovery_runtime_hard_gate_retries_fix_once_before_success(tmp_path: Path, monkeypatch) -> None:
    built = _build_runtime(tmp_path, stage_results=[False, True])
    monkeypatch.setenv("AGENTHUB_HARD_GATE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("AGENTHUB_HARD_GATE_TIMEBOX_SECONDS", "1200")

    built["runtime"].run_test_hard_gate(
        job=built["job"],
        repository_path=built["repository_path"],
        paths={},
        log_path=built["log_path"],
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        gate_label="after_implement",
    )

    assert built["fix_calls"] == [built["job"].job_id]
    assert built["commit_calls"] == [JobStage.FIX_WITH_CODEX.value]
    assert any("[HARD_GATE:after_implement] passed on attempt 2/3" in message for _, message in built["logs"])


def test_recovery_runtime_hard_gate_repeated_signature_raises_without_recovery(tmp_path: Path, monkeypatch) -> None:
    built = _build_runtime(tmp_path, stage_results=[False, False, False])
    monkeypatch.setenv("AGENTHUB_HARD_GATE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("AGENTHUB_HARD_GATE_TIMEBOX_SECONDS", "1200")

    with pytest.raises(CommandExecutionError, match="repeated failure signature"):
        built["runtime"].run_test_hard_gate(
            job=built["job"],
            repository_path=built["repository_path"],
            paths={},
            log_path=built["log_path"],
            stage=JobStage.TEST_AFTER_IMPLEMENT,
            gate_label="after_implement",
        )

    assert built["fix_calls"] == [built["job"].job_id]
    assert (built["repository_path"] / "_docs" / "FAILURE_ANALYSIS_PROMPT.md").exists()


def test_recovery_runtime_soft_gate_uses_recovery_flow_when_enabled(tmp_path: Path, monkeypatch) -> None:
    built = _build_runtime(tmp_path, stage_results=[False, True], recovery_enabled=True)
    monkeypatch.setenv("AGENTHUB_TEST_GATE_POLICY", "soft")

    built["runtime"].run_test_gate_by_policy(
        job=built["job"],
        repository_path=built["repository_path"],
        paths={},
        log_path=built["log_path"],
        stage=JobStage.TEST_AFTER_FIX,
        gate_label="after_fix",
        app_type="web",
    )

    assert built["fix_calls"] == [built["job"].job_id]
    assert built["commit_calls"] == [JobStage.FIX_WITH_CODEX.value]
    assert any("[SOFT_GATE:after_fix] test failed but continuing by policy." in message for _, message in built["logs"])
    assert any("[RECOVERY_MODE:after_fix] recovery succeeded." in message for _, message in built["logs"])


def test_recovery_runtime_fix_retry_loop_stops_after_success(tmp_path: Path) -> None:
    built = _build_runtime(tmp_path, stage_results=[False, True])

    built["runtime"].run_fix_retry_loop_after_test_failure(
        job=built["job"],
        repository_path=built["repository_path"],
        paths={},
        log_path=built["log_path"],
    )

    assert built["fix_calls"] == [built["job"].job_id, built["job"].job_id]
    assert built["commit_calls"] == [
        JobStage.FIX_WITH_CODEX.value,
        JobStage.TEST_AFTER_FIX.value,
        JobStage.FIX_WITH_CODEX.value,
        JobStage.TEST_AFTER_FIX.value,
    ]
    assert any("[FIX_LOOP] Round 2 succeeded." in message for _, message in built["logs"])
