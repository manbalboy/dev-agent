"""Tests for shell/test runtime extraction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.shell_test_runtime import ShellTestRuntime


def _make_job(job_id: str = "job-shell-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=101,
        issue_title="shell runtime test",
        issue_url="https://github.com/owner/repo/issues/101",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-101-shell-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(
    settings,
    *,
    shell_executor,
    shell_executor_accepts_heartbeat: bool = False,
    shell_executor_accepts_env: bool = False,
    is_long_track=None,
    actor_log_messages=None,
    write_mobile_quality_artifact=None,
):
    actor_log_messages = actor_log_messages if actor_log_messages is not None else []
    is_long_track = is_long_track or (lambda job: False)

    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_log_messages.append((str(log_path), actor, message))

    def append_actor_log(log_path: Path, actor: str, message: str) -> None:
        actor_log_messages.append((str(log_path), actor, message))

    return ShellTestRuntime(
        settings=settings,
        shell_executor=shell_executor,
        shell_executor_accepts_heartbeat=shell_executor_accepts_heartbeat,
        shell_executor_accepts_env=shell_executor_accepts_env,
        touch_job_heartbeat=lambda *args, **kwargs: None,
        actor_log_writer=actor_log_writer,
        infer_actor_from_command=lambda command, purpose: "SHELL",
        set_stage=lambda job_id, stage, log_path: actor_log_messages.append(
            (str(log_path), "STAGE", f"{job_id}:{stage.value}")
        ),
        append_actor_log=append_actor_log,
        is_long_track=is_long_track,
        write_mobile_quality_artifact=write_mobile_quality_artifact,
    )


def test_shell_test_runtime_resolves_stage_specific_commands(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(
        settings,
        shell_executor=lambda **kwargs: CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        ),
    )

    assert runtime.resolve_test_command(JobStage.TEST_AFTER_IMPLEMENT, secondary=False) == "echo test implement"
    assert runtime.resolve_test_command(JobStage.TEST_AFTER_IMPLEMENT, secondary=True) == "echo test implement secondary"
    assert runtime.resolve_test_command(JobStage.TEST_AFTER_FIX, secondary=False) == "echo test fix"
    assert runtime.resolve_test_command(JobStage.TEST_AFTER_FIX, secondary=True) == "echo test fix secondary"
    assert runtime.resolve_test_command(JobStage.UX_E2E_REVIEW, secondary=False) == "echo test fix"


def test_shell_test_runtime_wraps_timeout_and_logs_when_unavailable(app_components, monkeypatch, tmp_path: Path) -> None:
    settings, _, _ = app_components
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        settings,
        shell_executor=lambda **kwargs: CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        ),
        actor_log_messages=actor_logs,
    )

    monkeypatch.setenv("AGENTHUB_TEST_COMMAND_TIMEOUT_SECONDS", "321")
    monkeypatch.setattr(ShellTestRuntime, "has_timeout_utility", staticmethod(lambda: True))
    assert runtime.wrap_test_command_with_timeout("pytest -q", tmp_path / "job.log") == "timeout --preserve-status 321s pytest -q"

    monkeypatch.setattr(ShellTestRuntime, "has_timeout_utility", staticmethod(lambda: False))
    assert runtime.wrap_test_command_with_timeout("pytest -q", tmp_path / "job.log") == "pytest -q"
    assert any("timeout utility not found" in message for _, _, message in actor_logs)


def test_shell_test_runtime_write_test_report_tracks_counts_and_timeout(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(
        settings,
        shell_executor=lambda **kwargs: CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        ),
    )
    command_result = CommandResult(
        command="pytest -q",
        exit_code=124,
        stdout="2 passed, 1 skipped\n",
        stderr="1 failed, 1 error\n",
        duration_seconds=12.5,
    )

    report_path = runtime.write_test_report(
        repository_path=tmp_path,
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        command_result=command_result,
        tester_name="gpt",
        report_suffix="",
    )

    report_text = report_path.read_text(encoding="utf-8")
    assert "Status: `FAIL`" in report_text
    assert "시간 제한으로 종료" in report_text
    assert "- passed: `2`" in report_text
    assert "- failed: `1`" in report_text
    assert "- skipped: `1`" in report_text
    assert "- errors: `1`" in report_text


def test_shell_test_runtime_stage_run_tests_writes_failure_reason_for_failed_reports(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    settings = replace(
        settings,
        tester_primary_name="gpt",
        tester_secondary_name="gemini",
    )
    commands: list[str] = []

    def fake_shell(**kwargs):
        commands.append(kwargs["command"])
        return CommandResult(
            command=kwargs["command"],
            exit_code=1,
            stdout="[agenthub-test] no executable e2e/test command found\n",
            stderr="",
            duration_seconds=0.01,
        )

    runtime = _build_runtime(
        settings,
        shell_executor=fake_shell,
        is_long_track=lambda job: True,
    )

    passed = runtime.stage_run_tests(
        job=_make_job(),
        repository_path=tmp_path,
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        log_path=tmp_path / "job.log",
    )

    assert passed is False
    assert len(commands) == 2
    failure_reason = (tmp_path / "TEST_FAILURE_REASON_TEST_AFTER_IMPLEMENT.md").read_text(encoding="utf-8")
    assert "Tests failed at stage 'test_after_implement'." in failure_reason
    assert (tmp_path / "TEST_REPORT_TEST_AFTER_IMPLEMENT.md").exists()
    assert (tmp_path / "TEST_REPORT_TEST_AFTER_IMPLEMENT_GEMINI.md").exists()


def test_shell_test_runtime_passes_extra_env_when_supported(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    captured: dict[str, object] = {}

    def fake_shell(**kwargs):
        captured.update(kwargs)
        return CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    runtime = _build_runtime(
        settings,
        shell_executor=fake_shell,
        shell_executor_accepts_env=True,
    )
    runtime.extra_env = {"GOOGLE_MAPS_API_KEY": "secret-value"}  # type: ignore[attr-defined]

    runtime.run_shell(
        command="echo env",
        cwd=tmp_path,
        log_path=tmp_path / "runtime.log",
        purpose="runtime env smoke",
    )

    assert captured["extra_env"] == {"GOOGLE_MAPS_API_KEY": "secret-value"}


def test_shell_test_runtime_calls_mobile_quality_callback_after_tests(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    callback_calls: list[dict[str, object]] = []

    def fake_shell(**kwargs):
        return CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="1 passed\n",
            stderr="",
            duration_seconds=0.02,
        )

    def fake_mobile_artifact(**kwargs) -> None:
        callback_calls.append(kwargs)

    runtime = _build_runtime(
        settings,
        shell_executor=fake_shell,
        write_mobile_quality_artifact=fake_mobile_artifact,
    )
    job = _make_job("job-mobile-quality")

    passed = runtime.stage_run_tests(
        job=job,
        repository_path=tmp_path,
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        log_path=tmp_path / "job.log",
    )

    assert passed is True
    assert len(callback_calls) == 1
    callback = callback_calls[0]
    assert callback["job"] == job
    assert callback["repository_path"] == tmp_path
    assert callback["stage"] == JobStage.TEST_AFTER_IMPLEMENT
    assert isinstance(callback["test_results"], list)
    assert callback["test_results"][0]["name"] == settings.tester_primary_name
