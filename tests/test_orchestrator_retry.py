"""Tests for orchestration retry behavior and stage order."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shlex

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.orchestrator import Orchestrator


class FakeTemplateRunner:
    """Fake AI template runner used for deterministic tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def has_template(self, template_name: str) -> bool:
        return False

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        self.calls.append(template_name)

        if template_name == "planner":
            Path(variables["plan_path"]).write_text("# PLAN\n", encoding="utf-8")
        elif template_name == "coder":
            design_path = variables.get("design_path", "")
            if design_path and not Path(design_path).exists():
                Path(design_path).write_text("# DESIGN SYSTEM\n", encoding="utf-8")
        elif template_name == "reviewer":
            Path(variables["review_path"]).write_text("# REVIEW\n- [ ] TODO\n", encoding="utf-8")

        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )



def _make_job(job_id: str = "job-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=55,
        issue_title="Retry behavior",
        issue_url="https://github.com/owner/repo/issues/55",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-55-job1",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )



def test_orchestrator_retries_three_times_and_fails(app_components):
    settings, store, _ = app_components
    job = _make_job("job-retry")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    call_count = {"value": 0}

    def always_fail(job_id: str, log_path: Path) -> None:
        call_count["value"] += 1
        raise RuntimeError("simulated stage failure")

    orchestrator._run_single_attempt = always_fail  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert call_count["value"] == 3

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED.value
    assert stored.stage == JobStage.FAILED.value
    assert stored.attempt == 3



def test_long_track_runs_three_full_rounds(app_components):
    settings, store, _ = app_components
    job = _make_job("job-long-rounds")
    job.track = "long"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    call_count = {"value": 0}

    def always_success(job_id: str, log_path: Path) -> None:
        call_count["value"] += 1

    orchestrator._run_single_attempt = always_success  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert call_count["value"] == 3

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.attempt == 3


def test_orchestrator_runs_stages_in_fixed_order(app_components):
    settings, store, _ = app_components
    job = _make_job("job-success")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.pr_url == "https://github.com/owner/repo/pull/999"
    repo_path = settings.repository_workspace_path(job.repository, job.app_code)
    pr_body = (repo_path / "_docs" / "PR_BODY.md").read_text(encoding="utf-8")
    assert "## Deployment Preview" in pr_body
    assert "Docker Pod/Container" in pr_body
    summary_md = (repo_path / "_docs" / "CODE_CHANGE_SUMMARY.md").read_text(encoding="utf-8")
    assert "# CODE CHANGE SUMMARY" in summary_md
    assert (repo_path / "_docs" / "PRODUCT_BRIEF.md").exists()
    assert (repo_path / "_docs" / "USER_FLOWS.md").exists()
    assert (repo_path / "_docs" / "MVP_SCOPE.md").exists()
    assert (repo_path / "_docs" / "ARCHITECTURE_PLAN.md").exists()
    assert (repo_path / "_docs" / "PRODUCT_REVIEW.json").exists()
    assert (repo_path / "_docs" / "IMPROVEMENT_PLAN.md").exists()

    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    stage_lines = [
        line.split("[STAGE] ", 1)[1].strip()
        for line in log_text.splitlines()
        if "[STAGE]" in line
    ]

    assert stage_lines == [
        JobStage.PREPARE_REPO.value,
        JobStage.READ_ISSUE.value,
        JobStage.WRITE_SPEC.value,
        JobStage.IDEA_TO_PRODUCT_BRIEF.value,
        JobStage.GENERATE_USER_FLOWS.value,
        JobStage.DEFINE_MVP_SCOPE.value,
        JobStage.ARCHITECTURE_PLANNING.value,
        JobStage.COPYWRITER_TASK.value,
        JobStage.DESIGN_WITH_CODEX.value,
        JobStage.IMPLEMENT_WITH_CODEX.value,
        JobStage.IMPLEMENT_WITH_CODEX.value,
        JobStage.SUMMARIZE_CODE_CHANGES.value,
        JobStage.TEST_AFTER_IMPLEMENT.value,
        JobStage.COMMIT_IMPLEMENT.value,
        JobStage.REVIEW_WITH_GEMINI.value,
        JobStage.PRODUCT_REVIEW.value,
        JobStage.IMPROVEMENT_STAGE.value,
        JobStage.FIX_WITH_CODEX.value,
        JobStage.TEST_AFTER_FIX.value,
        JobStage.COMMIT_FIX.value,
        JobStage.DOCUMENTATION_TASK.value,
        JobStage.PUSH_BRANCH.value,
        JobStage.CREATE_PR.value,
        JobStage.FINALIZE.value,
    ]


def test_stage_specific_tester_commands_are_used(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command="echo base",
        test_command_secondary="echo base secondary",
        test_command_implement="echo implement",
        test_command_fix="echo fix",
        test_command_secondary_implement="echo implement secondary",
        test_command_secondary_fix="echo fix secondary",
    )

    job = _make_job("job-stage-commands")
    job.track = "long"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    executed_commands: list[str] = []

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        executed_commands.append(str(command))
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert any("echo implement" in cmd for cmd in executed_commands)
    assert any("echo implement secondary" in cmd for cmd in executed_commands)
    assert any("echo fix" in cmd for cmd in executed_commands)
    assert any("echo fix secondary" in cmd for cmd in executed_commands)


def test_workflow_tester_task_node_runs_test_stage(app_components):
    settings, store, _ = app_components
    job = _make_job("job-tester-task")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    workflow = {
        "workflow_id": "test_tester_task",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "tester_task"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value

    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "[STAGE] test_after_implement" in log_text


def test_workflow_ux_e2e_review_node_runs_stage(app_components):
    settings, store, _ = app_components
    job = _make_job("job-ux-review-task")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    called = {"ux": False}

    def fake_ux_stage(job_obj, repo_path, paths, log_path):
        called["ux"] = True
        orchestrator._set_stage(job_obj.job_id, JobStage.UX_E2E_REVIEW, log_path)
        (repo_path / "UX_REVIEW.md").write_text("# UX REVIEW\n", encoding="utf-8")

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]

    workflow = {
        "workflow_id": "test_ux_stage",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "ux_e2e_review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert called["ux"] is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value


def test_test_failure_does_not_abort_workflow(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command="run-tests-fail",
        test_command_secondary="run-tests-fail-secondary",
        test_command_implement="run-tests-fail-implement",
        test_command_fix="run-tests-fail-fix",
        test_command_secondary_implement="run-tests-fail-implement-secondary",
        test_command_secondary_fix="run-tests-fail-fix-secondary",
    )

    job = _make_job("job-test-failure-continue")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "run-tests-fail" in command:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="1 failed, 0 passed",
                stderr="failing by design",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.pr_url == "https://github.com/owner/repo/pull/999"

    repo_path = tuned_settings.repository_workspace_path(job.repository, job.app_code)
    assert (repo_path / "TEST_FAILURE_REASON_TEST_AFTER_IMPLEMENT.md").exists()
    assert (repo_path / "TEST_FAILURE_REASON_TEST_AFTER_FIX.md").exists()


def test_e2e_failure_runs_fix_loop_then_enters_review(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command_fix="run-e2e-loop",
    )

    job = _make_job("job-e2e-loop")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    e2e_calls = {"count": 0}

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "run-e2e-loop" in command:
            e2e_calls["count"] += 1
            should_fail = e2e_calls["count"] < 3
            return CommandResult(
                command=command,
                exit_code=1 if should_fail else 0,
                stdout="1 failed" if should_fail else "1 passed",
                stderr="loop failure" if should_fail else "",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    workflow = {
        "workflow_id": "test_fix_loop",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "codex_fix"},
            {"id": "n4", "type": "test_after_fix"},
            {"id": "n5", "type": "gemini_review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
            {"from": "n4", "to": "n5", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert e2e_calls["count"] == 2

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value

    log_text = (tuned_settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "[RECOVERY_MODE:after_fix_web] recoverable. Running fix + retest once." in log_text
    assert "[RECOVERY_MODE:after_fix_web] recovery attempt failed." in log_text
    assert "[STAGE] review_with_gemini" in log_text
