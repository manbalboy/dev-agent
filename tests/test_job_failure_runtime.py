from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.job_failure_runtime import JobFailureRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.provider_failure_counter_runtime import record_provider_failure


def _make_job(job_id: str = "job-failure-runtime", *, max_attempts: int = 2) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=91,
        issue_title="failure runtime test",
        issue_url="https://github.com/owner/repo/issues/91",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=max_attempts,
        branch_name="agenthub/issue-91-failure-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class _FakeTemplateRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], str]] = []

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        self.calls.append((template_name, dict(variables), str(cwd)))
        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


def _build_runtime(
    *,
    app_components,
    template_runner: _FakeTemplateRunner | None = None,
    run_single_attempt,
    actor_logs: list[tuple[str, str, str]],
    escalation_enabled: bool = False,
    stop_requested: list[bool] | None = None,
    profile_state: dict[str, str] | None = None,
) -> tuple[JobFailureRuntime, Path, object]:
    settings, store, _ = app_components
    template_runner = template_runner or _FakeTemplateRunner()
    stop_requested = stop_requested or [False]
    profile_state = profile_state or {"value": "primary"}

    def require_job(job_id: str) -> JobRecord:
        job = store.get_job(job_id)
        assert job is not None
        return job

    def append_actor_log(log_path: Path, actor: str, message: str) -> None:
        actor_logs.append((str(log_path), actor, message))

    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_logs.append((str(log_path), actor, message))

    def docs_file(repository_path: Path, name: str) -> Path:
        docs_dir = repository_path / "_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / name

    def build_template_variables(job: JobRecord, paths: dict[str, Path], prompt_path: Path) -> dict[str, str]:
        return {
            "repository": job.repository,
            "issue_number": str(job.issue_number),
            "issue_title": job.issue_title,
            "issue_url": job.issue_url,
            "branch_name": job.branch_name,
            "work_dir": str(prompt_path.parent.parent),
            "prompt_file": str(prompt_path),
            "spec_path": str(paths.get("spec", "")),
            "plan_path": str(paths.get("plan", "")),
            "review_path": str(paths.get("review", "")),
            "design_path": str(paths.get("design", "")),
            "status_path": str(paths.get("status", "")),
        }

    def run_shell(*, command: str, cwd: Path, log_path: Path, purpose: str):
        del command, cwd
        actor_logs.append((str(log_path), "SHELL", purpose))
        stdout = ""
        if purpose == "git status before WIP PR":
            stdout = " M _docs/STATUS.md\n"
        elif purpose == "create WIP pull request":
            stdout = "https://github.com/owner/repo/pull/91\n"
        return CommandResult(
            command=purpose,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_seconds=0.0,
        )

    runtime = JobFailureRuntime(
        settings=settings,
        store=store,
        command_templates=template_runner,
        require_job=require_job,
        run_single_attempt=run_single_attempt,
        touch_job_heartbeat=lambda **kwargs: actor_logs.append(("heartbeat", "SYSTEM", str(kwargs.get("force", False)))),
        append_actor_log=append_actor_log,
        is_escalation_enabled=lambda: escalation_enabled,
        find_configured_template_for_route=lambda route: route if escalation_enabled and route == "escalation" else None,
        docs_file=docs_file,
        job_workspace_path=lambda job: settings.repository_workspace_path(job.repository, job.app_code),
        build_template_variables=build_template_variables,
        template_for_route=lambda route: route,
        actor_log_writer=actor_log_writer,
        set_stage=lambda job_id, stage, log_path: (
            store.update_job(job_id, stage=stage.value),
            actor_logs.append((str(log_path), "STAGE", stage.value)),
        ),
        issue_reference_line=lambda job: f"Closes #{job.issue_number}",
        run_shell=run_shell,
        push_branch_with_recovery=lambda **kwargs: actor_logs.append((str(kwargs["log_path"]), "GIT", kwargs["purpose"])),
        job_execution_repository=lambda job: job.repository,
        get_pr_url=lambda job, repository_path, log_path, create_result: create_result.stdout.strip() or None,
        is_stop_requested=lambda job_id: stop_requested[0],
        clear_stop_requested=lambda job_id: stop_requested.__setitem__(0, False),
        set_agent_profile=lambda profile: profile_state.__setitem__("value", profile),
    )
    return runtime, settings, store


def test_standard_attempt_loop_retries_then_finalizes_failure(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=lambda job_id, log_path: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    job = _make_job("job-failure-standard", max_attempts=2)
    store.create_job(job)
    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)

    runtime.run_standard_attempt_loop(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.stage == JobStage.FAILED.value
    assert refreshed.error_message == "boom"
    assert refreshed.recovery_status == "dead_letter"
    assert "dead-letter after retry budget exhausted" in (refreshed.recovery_reason or "")
    assert refreshed.pr_url == "https://github.com/owner/repo/pull/91"
    assert (repository_path / "_docs" / "STATUS.md").exists()
    assert any("Retry policy selected" in message for _, _, message in actor_logs)
    assert any("Retry budget exhausted" in message for _, _, message in actor_logs)
    trace_payload = json.loads((repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json").read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "dead_letter"
    assert trace_payload["events"][0]["dead_letter_summary"]["active"] is True


def test_standard_attempt_loop_fast_fails_provider_quota(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    call_count = {"value": 0}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        call_count["value"] += 1
        raise RuntimeError("402 You have no quota remaining")

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
    )
    job = _make_job("job-failure-quota", max_attempts=3)
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    store.create_job(job)
    settings.repository_workspace_path(job.repository, job.app_code).mkdir(parents=True, exist_ok=True)

    runtime.run_standard_attempt_loop(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.recovery_status == "needs_human"
    assert call_count["value"] == 1
    assert any("class=provider_quota" in message for _, _, message in actor_logs)
    trace_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "needs_human"
    assert trace_payload["events"][0]["failure_class"] == "provider_quota"
    assert trace_payload["events"][0]["needs_human_summary"]["recovery_path"] == "needs_human_candidate"
    provider_counter_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "PROVIDER_FAILURE_COUNTERS.json"
    provider_payload = json.loads(provider_counter_path.read_text(encoding="utf-8"))
    assert provider_payload["providers"]["codex"]["total_failures"] == 1


def test_standard_attempt_loop_short_retries_provider_timeout(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    call_count = {"value": 0}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        call_count["value"] += 1
        raise RuntimeError("request timeout while waiting for codex response")

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
    )
    job = _make_job("job-failure-timeout", max_attempts=5)
    job.stage = JobStage.PLAN_WITH_GEMINI.value
    store.create_job(job)
    settings.repository_workspace_path(job.repository, job.app_code).mkdir(parents=True, exist_ok=True)

    runtime.run_standard_attempt_loop(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.recovery_status == "cooldown_wait"
    assert call_count["value"] == 2
    assert any("class=provider_timeout" in message for _, _, message in actor_logs)
    assert any("Provider cooldown active" in message for _, _, message in actor_logs)
    trace_path = settings.repository_workspace_path(job.repository, job.app_code) / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "cooldown_wait"
    assert trace_payload["events"][0]["recovery_status"] == "cooldown_wait"
    assert trace_payload["events"][0]["details"]["cooldown"]["active"] is True


def test_standard_attempt_loop_quarantines_provider_after_burst(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    call_count = {"value": 0}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        call_count["value"] += 1
        raise RuntimeError("request timeout while waiting for codex response")

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
    )
    job = _make_job("job-failure-provider-quarantine", max_attempts=5)
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    store.create_job(job)
    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 4):
        record_provider_failure(
            repository_path,
            provider_hint="codex",
            failure_class="provider_timeout",
            stage_family="implementation",
            reason_code="provider_timeout",
            reason="request timeout while waiting for codex response",
            job_id=f"older-job-{attempt}",
            attempt=attempt,
        )

    runtime.run_standard_attempt_loop(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.recovery_status == "provider_quarantined"
    assert call_count["value"] == 1
    assert any("Provider quarantine active" in message for _, _, message in actor_logs)
    trace_path = repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "provider_quarantined"
    assert trace_payload["events"][0]["needs_human_summary"]["recovery_path"] == "provider_quarantine"


def test_standard_attempt_loop_opens_provider_circuit_after_extended_burst(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    call_count = {"value": 0}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        call_count["value"] += 1
        raise RuntimeError("request timeout while waiting for codex response")

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
    )
    job = _make_job("job-failure-provider-circuit", max_attempts=5)
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    store.create_job(job)
    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 6):
        record_provider_failure(
            repository_path,
            provider_hint="codex",
            failure_class="provider_timeout",
            stage_family="implementation",
            reason_code="provider_timeout",
            reason="request timeout while waiting for codex response",
            job_id=f"older-job-circuit-{attempt}",
            attempt=attempt,
        )

    runtime.run_standard_attempt_loop(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.recovery_status == "provider_circuit_open"
    assert call_count["value"] == 1
    assert any("Provider circuit-breaker active" in message for _, _, message in actor_logs)
    trace_path = repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace_payload["events"][0]["decision"] == "provider_circuit_open"
    assert trace_payload["events"][0]["needs_human_summary"]["recovery_path"] == "provider_circuit_breaker"


def test_process_long_job_marks_done_after_three_rounds(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    call_count = {"value": 0}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        call_count["value"] += 1

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
    )
    job = _make_job("job-failure-long")
    store.create_job(job)

    runtime.process_long_job(job.job_id, settings.logs_debug_dir / job.log_file)

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.DONE.value
    assert refreshed.stage == JobStage.DONE.value
    assert refreshed.attempt == 3
    assert call_count["value"] == 3
    assert any("[LONG] Completed all 3 rounds successfully" in message for _, _, message in actor_logs)


def test_process_ultra_job_finalizes_after_primary_and_fallback_failures(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    profile_state = {"value": "primary"}

    def run_single_attempt(job_id: str, log_path: Path) -> None:
        del job_id, log_path
        raise RuntimeError(f"{profile_state['value']} boom")

    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=run_single_attempt,
        profile_state=profile_state,
    )
    job = _make_job("job-failure-ultra")
    store.create_job(job)
    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)

    runtime.process_ultra_job(job.job_id, settings.logs_debug_dir / job.log_file, max_runtime_hours=1, mode_tag="ULTRA")

    refreshed = store.get_job(job.job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert refreshed.error_message == "fallback boom"
    assert profile_state["value"] == "primary"
    assert any("Trying fallback agents" in message for _, _, message in actor_logs)
    assert any("Two-agent failure reached" in message for _, _, message in actor_logs)


def test_run_optional_escalation_writes_prompt_and_calls_template(app_components) -> None:
    actor_logs: list[tuple[str, str, str]] = []
    template_runner = _FakeTemplateRunner()
    runtime, settings, store = _build_runtime(
        app_components=app_components,
        actor_logs=actor_logs,
        run_single_attempt=lambda job_id, log_path: None,
        template_runner=template_runner,
        escalation_enabled=True,
    )
    job = _make_job("job-failure-escalation")
    store.create_job(job)
    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)

    runtime.run_optional_escalation(job.job_id, settings.logs_debug_dir / job.log_file, "quota exceeded")

    prompt_path = repository_path / "_docs" / "ESCALATION_PROMPT.md"
    assert prompt_path.exists()
    assert "quota exceeded" in prompt_path.read_text(encoding="utf-8")
    assert template_runner.calls[0][0] == "escalation"
