"""Tests for content/design/documentation stage runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import CommandExecutionError
from app.command_runner import CommandResult
from app.content_stage_runtime import ContentStageRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-content-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="content runtime test",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-88-content-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class _FakeTemplateRunner:
    def __init__(self, *, stdout_by_template: dict[str, str] | None = None, enabled_templates: set[str] | None = None) -> None:
        self.stdout_by_template = stdout_by_template or {}
        self.enabled_templates = enabled_templates or set(self.stdout_by_template)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def has_template(self, template_name: str) -> bool:
        return template_name in self.enabled_templates

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
        if template_name == "documentation":
            del cwd
            self.calls.append((template_name, dict(variables)))
            log_writer(f"[FAKE_TEMPLATE] {template_name}")
            raise CommandExecutionError(
                "documentation failed with exit code 1. Next action: run the logged command manually in the same "
                "repository directory and verify CLI login/state. stderr preview: (no stderr output)"
            )
        return super().run_template(template_name, variables, cwd, log_writer)


def _build_runtime(
    *,
    template_runner: _FakeTemplateRunner,
    actor_logs: list[tuple[str, str, str]],
    ensure_hits: list[str],
) -> ContentStageRuntime:
    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_logs.append((str(log_path), actor, message))

    return ContentStageRuntime(
        command_templates=template_runner,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((str(log_path), "STAGE", f"{job_id}:{stage.value}")),
        ensure_product_definition_ready=lambda paths, log_path: actor_logs.append((str(log_path), "ENSURE", str(paths["spec"]))),
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
        template_for_route=lambda route: {
            "designer": "designer",
            "publisher": "publisher",
            "copywriter": "copywriter",
            "coder": "coder",
        }.get(route, route),
        template_candidates_for_route=lambda route: ["documentation"] if route == "documentation" else [route],
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        ensure_design_artifacts=lambda repository_path, paths, log_path: ensure_hits.append("design"),
        ensure_publisher_artifacts=lambda repository_path, paths, log_path: ensure_hits.append("publish"),
        ensure_copywriter_artifacts=lambda repository_path, paths, log_path: ensure_hits.append("copy"),
        ensure_documentation_artifacts=lambda repository_path, paths, log_path: ensure_hits.append("documentation"),
    )


def test_stage_design_with_codex_writes_stdout_when_design_file_missing(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    ensure_hits: list[str] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(stdout_by_template={"designer": "# DESIGN SYSTEM\n"}),
        actor_logs=actor_logs,
        ensure_hits=ensure_hits,
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "design": docs_path / "DESIGN_SYSTEM.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")

    runtime.stage_design_with_codex(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert paths["design"].read_text(encoding="utf-8") == "# DESIGN SYSTEM\n"
    assert ensure_hits == ["design"]


def test_apply_documentation_bundle_writes_allowed_targets_only(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    ensure_hits: list[str] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(),
        actor_logs=actor_logs,
        ensure_hits=ensure_hits,
    )
    bundle_path = docs_path / "DOCUMENTATION_BUNDLE.md"
    bundle_path.write_text(
        "\n".join(
            [
                "<<<FILE:README.md>>>",
                "# README",
                "<<<FILE:_docs/DOCUMENTATION_PLAN.md>>>",
                "# DOC PLAN",
                "<<<FILE:docs/ignored.md>>>",
                "# ignored",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths = {
        "readme": repository_path / "README.md",
        "documentation_plan": docs_path / "DOCUMENTATION_PLAN.md",
    }

    applied = runtime.apply_documentation_bundle(
        repository_path=repository_path,
        bundle_path=bundle_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert applied is True
    assert paths["readme"].read_text(encoding="utf-8") == "# README\n"
    assert paths["documentation_plan"].read_text(encoding="utf-8") == "# DOC PLAN\n"
    assert not (repository_path / "docs" / "ignored.md").exists()


def test_stage_documentation_with_claude_falls_back_to_coder_when_route_missing(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    ensure_hits: list[str] = []
    runner = _FakeTemplateRunner(enabled_templates={"coder"})
    runtime = _build_runtime(
        template_runner=runner,
        actor_logs=actor_logs,
        ensure_hits=ensure_hits,
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "readme": repository_path / "README.md",
        "copyright": repository_path / "COPYRIGHT.md",
        "development_guide": repository_path / "DEVELOPMENT_GUIDE.md",
        "documentation_plan": docs_path / "DOCUMENTATION_PLAN.md",
    }
    for path in [paths["spec"], paths["plan"], paths["review"]]:
        path.write_text("# stub\n", encoding="utf-8")

    runtime.stage_documentation_with_claude(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert [call[0] for call in runner.calls] == ["coder"]
    assert ensure_hits == ["documentation"]
    assert any("Fallback to coder route" in message for _, actor, message in actor_logs if actor == "ORCHESTRATOR")


def test_stage_documentation_with_claude_compresses_login_hint_on_route_failure(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    ensure_hits: list[str] = []
    runner = _FailingTemplateRunner(enabled_templates={"documentation", "coder"})
    runtime = _build_runtime(
        template_runner=runner,
        actor_logs=actor_logs,
        ensure_hits=ensure_hits,
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "readme": repository_path / "README.md",
        "copyright": repository_path / "COPYRIGHT.md",
        "development_guide": repository_path / "DEVELOPMENT_GUIDE.md",
        "documentation_plan": docs_path / "DOCUMENTATION_PLAN.md",
    }
    for path in [paths["spec"], paths["plan"], paths["review"]]:
        path.write_text("# stub\n", encoding="utf-8")

    runtime.stage_documentation_with_claude(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert [call[0] for call in runner.calls] == ["documentation", "coder"]
    assert ensure_hits == ["documentation"]
    assert any(
        actor == "ORCHESTRATOR" and "로그인/인증 상태 확인 필요" in message
        for _, actor, message in actor_logs
    )
