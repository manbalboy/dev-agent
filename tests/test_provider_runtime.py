from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.command_runner import CommandExecutionError, CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.provider_runtime import ProviderRuntime


def _make_job(job_id: str = "job-provider-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="provider runtime test",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-77-provider-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class _Store:
    def __init__(self) -> None:
        self.updated: list[tuple[str, dict[str, str]]] = []

    def update_job(self, job_id: str, **kwargs) -> None:
        self.updated.append((job_id, kwargs))


def _build_runtime(*, run_shell, store: _Store, actor_logs: list[tuple[str, str]]) -> ProviderRuntime:
    job = _make_job()

    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def append_preview_section_to_pr_body(pr_body_path: Path, preview_info: dict[str, str]) -> None:
        current = pr_body_path.read_text(encoding="utf-8", errors="replace")
        pr_body_path.write_text(current + f"\n## Preview\n- {preview_info.get('status', 'skipped')}\n", encoding="utf-8")

    return ProviderRuntime(
        settings=SimpleNamespace(default_branch="main"),
        store=store,
        run_shell=run_shell,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((job_id, stage.value)),
        require_job=lambda job_id: job if job_id == job.job_id else _make_job(job_id),
        job_execution_repository=lambda _job: "owner/repo",
        deploy_preview_and_smoke_test=lambda _job, _repository_path, _log_path: {"status": "skipped"},
        docs_file=docs_file,
        stage_prepare_pr_summary=lambda _job, _repository_path, _paths, _log_path: None,
        issue_reference_line=lambda current_job: f"Tracking issue: {current_job.issue_url}",
        append_preview_section_to_pr_body=append_preview_section_to_pr_body,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((actor, message)),
    )


def test_push_branch_with_recovery_retries_force_with_lease(tmp_path: Path) -> None:
    actor_logs: list[tuple[str, str]] = []
    store = _Store()
    commands: list[str] = []

    def run_shell(*, command: str, cwd: Path, log_path: Path, purpose: str):
        del cwd, log_path, purpose
        commands.append(command)
        if command.endswith("push -u origin agenthub/issue-77-provider-runtime"):
            raise CommandExecutionError("failed to push some refs (non-fast-forward)")
        return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    runtime = _build_runtime(run_shell=run_shell, store=store, actor_logs=actor_logs)
    runtime.push_branch_with_recovery(
        repository_path=tmp_path,
        branch_name="agenthub/issue-77-provider-runtime",
        log_path=tmp_path / "job.log",
        purpose="git push",
    )

    assert any("fetch origin" in command for command in commands)
    assert any("--force-with-lease -u origin agenthub/issue-77-provider-runtime" in command for command in commands)
    assert any(actor == "GIT" and "force-with-lease" in message for actor, message in actor_logs)


def test_stage_create_pr_updates_existing_pull_request_body(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
    }
    for path in paths.values():
        path.write_text("# stub\n", encoding="utf-8")

    actor_logs: list[tuple[str, str]] = []
    store = _Store()
    job = _make_job()
    commands: list[str] = []

    def run_shell(*, command: str, cwd: Path, log_path: Path, purpose: str):
        del cwd, log_path, purpose
        commands.append(command)
        if command.startswith("gh pr create"):
            raise CommandExecutionError("pull request already exists")
        if command.startswith("gh pr edit"):
            return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )
        return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    runtime = ProviderRuntime(
        settings=SimpleNamespace(default_branch="main"),
        store=store,
        run_shell=run_shell,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((job_id, stage.value)),
        require_job=lambda job_id: job if job_id == job.job_id else _make_job(job_id),
        job_execution_repository=lambda _job: "owner/repo",
        deploy_preview_and_smoke_test=lambda _job, _repository_path, _log_path: {"status": "skipped"},
        docs_file=docs_file,
        stage_prepare_pr_summary=lambda _job, _repository_path, _paths, _log_path: None,
        issue_reference_line=lambda current_job: f"Tracking issue: {current_job.issue_url}",
        append_preview_section_to_pr_body=lambda pr_body_path, preview_info: pr_body_path.write_text(
            pr_body_path.read_text(encoding="utf-8") + f"\n## Preview\n- {preview_info.get('status', 'skipped')}\n",
            encoding="utf-8",
        ),
        append_actor_log=lambda log_path, actor, message: actor_logs.append((actor, message)),
    )

    runtime.stage_create_pr(
        job=job,
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert any(command.startswith("gh pr edit ") for command in commands)
    assert any(command.startswith("gh pr view ") for command in commands)
    assert store.updated == [(job.job_id, {"pr_url": "https://github.com/owner/repo/pull/999"})]
    assert any(actor == "GITHUB" and "PR already exists" in message for actor, message in actor_logs)
