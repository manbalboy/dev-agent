"""Tests for summary runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import CommandExecutionError
from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.summary_runtime import SummaryRuntime


def _make_job(job_id: str = "job-summary-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="summary runtime test",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-77-summary-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class _FakeTemplateRunner:
    def __init__(self, *, stdout_by_template: dict[str, str] | None = None) -> None:
        self.stdout_by_template = stdout_by_template or {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del cwd
        self.calls.append((template_name, dict(variables)))
        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout=self.stdout_by_template.get(template_name, ""),
            stderr="",
            duration_seconds=0.0,
        )


class _FailingTemplateRunner(_FakeTemplateRunner):
    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del template_name, variables, cwd, log_writer
        raise RuntimeError("quota exceeded")


class _LoginFailingTemplateRunner(_FakeTemplateRunner):
    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del template_name, variables, cwd, log_writer
        raise CommandExecutionError(
            "commit_summary failed with exit code 1. Next action: run the logged command manually in the same "
            "repository directory and verify CLI login/state. stderr preview: (no stderr output)"
        )


def _build_runtime(
    *,
    template_runner: _FakeTemplateRunner,
    actor_logs: list[tuple[str, str, str]],
) -> SummaryRuntime:
    def append_log(log_path: Path, message: str) -> None:
        actor_logs.append((str(log_path), "LOG", message))

    def append_actor_log(log_path: Path, actor: str, message: str) -> None:
        actor_logs.append((str(log_path), actor, message))

    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_logs.append((str(log_path), actor, message))

    return SummaryRuntime(
        command_templates=template_runner,
        run_shell=lambda **kwargs: CommandResult(
            command=kwargs["command"],
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        ),
        append_log=append_log,
        append_actor_log=append_actor_log,
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
        build_template_variables=lambda job, paths, prompt_path: {
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
        },
        actor_log_writer=actor_log_writer,
        template_for_route=lambda route: route,
        find_configured_template_for_route=lambda route: route if route in {"codex_helper", "commit_summary", "pr_summary"} else None,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((str(log_path), "STAGE", f"{job_id}:{stage.value}")),
        parse_porcelain_path=lambda raw: raw,
        is_long_track=lambda job: False,
    )


def test_stage_prepare_pr_summary_writes_output_file_from_stdout(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(stdout_by_template={"pr_summary": "# PR SUMMARY\n\n- body"}),
        actor_logs=actor_logs,
    )
    job = _make_job()
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "design": docs_path / "DESIGN_SYSTEM.md",
    }
    for path in paths.values():
        path.write_text("# stub\n", encoding="utf-8")

    output_path = runtime.stage_prepare_pr_summary(
        job=job,
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert output_path == docs_path / "PR_SUMMARY.md"
    assert output_path.read_text(encoding="utf-8") == "# PR SUMMARY\n\n- body"
    assert any(actor == "PR_SUMMARY" and "PR summary written" in message for _, actor, message in actor_logs)


def test_prepare_commit_summary_with_ai_prefers_helper_output(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    (repository_path / "_docs").mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(stdout_by_template={"codex_helper": "fix: 모바일 레이아웃 정리"}),
        actor_logs=actor_logs,
    )

    summary = runtime.prepare_commit_summary_with_ai(
        job=_make_job(),
        repository_path=repository_path,
        stage_name="implement_with_codex",
        commit_type="fix",
        changed_paths=["app/templates/index.html", "app/static/style.css"],
        log_path=tmp_path / "job.log",
    )

    assert summary == "모바일 레이아웃 정리"
    assert SummaryRuntime.is_usable_commit_summary(summary) is True


def test_commit_summary_sanitization_and_validation_rules() -> None:
    assert SummaryRuntime.sanitize_commit_summary("  fix: 모바일 버튼 간격 정리  \n두번째 줄") == "모바일 버튼 간격 정리"
    assert SummaryRuntime.is_usable_commit_summary("모바일 버튼 간격 정리") is True
    assert SummaryRuntime.is_usable_commit_summary("없음") is False
    assert SummaryRuntime.is_usable_commit_summary("```code```") is False


def test_stage_prepare_pr_summary_logs_fallback_instead_of_failure(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        template_runner=_FailingTemplateRunner(),
        actor_logs=actor_logs,
    )
    job = _make_job()
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "design": docs_path / "DESIGN_SYSTEM.md",
    }
    for path in paths.values():
        path.write_text("# stub\n", encoding="utf-8")

    output_path = runtime.stage_prepare_pr_summary(
        job=job,
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert output_path is None
    assert any(
        actor == "PR_SUMMARY" and "using default PR body" in message and "failed" not in message.lower()
        for _, actor, message in actor_logs
    )


def test_prepare_commit_summary_with_ai_logs_fallback_instead_of_failure(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    (repository_path / "_docs").mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        template_runner=_FailingTemplateRunner(),
        actor_logs=actor_logs,
    )

    summary = runtime.prepare_commit_summary_with_ai(
        job=_make_job(),
        repository_path=repository_path,
        stage_name="implement_with_codex",
        commit_type="fix",
        changed_paths=["app/templates/index.html"],
        log_path=tmp_path / "job.log",
    )

    assert summary == ""
    assert any(
        actor == "CODEX_HELPER" and "falling back" in message and "failed" not in message.lower()
        for _, actor, message in actor_logs
    )
    assert any(
        actor == "TECH_WRITER" and "using deterministic fallback" in message and "failed" not in message.lower()
        for _, actor, message in actor_logs
    )


def test_prepare_commit_summary_with_ai_compresses_login_hint_in_fallback_log(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    (repository_path / "_docs").mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runtime = _build_runtime(
        template_runner=_LoginFailingTemplateRunner(),
        actor_logs=actor_logs,
    )

    summary = runtime.prepare_commit_summary_with_ai(
        job=_make_job(),
        repository_path=repository_path,
        stage_name="implement_with_codex",
        commit_type="fix",
        changed_paths=["app/templates/index.html"],
        log_path=tmp_path / "job.log",
    )

    assert summary == ""
    assert any(
        actor == "CODEX_HELPER" and "로그인/인증 상태 확인 필요" in message
        for _, actor, message in actor_logs
    )
    assert any(
        actor == "TECH_WRITER" and "로그인/인증 상태 확인 필요" in message
        for _, actor, message in actor_logs
    )
