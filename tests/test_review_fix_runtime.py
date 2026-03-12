"""Tests for review/fix runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.review_fix_runtime import ReviewFixRuntime


def _make_job(job_id: str = "job-review-fix-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=91,
        issue_title="review fix runtime test",
        issue_url="https://github.com/owner/repo/issues/91",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-91-review-fix-runtime",
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


def _build_runtime(
    *,
    template_runner: _FakeTemplateRunner,
    actor_logs: list[tuple[str, str, str]],
    planner_calls: list[dict[str, object]],
    improvement_runtime: dict[str, object] | None = None,
    template_for_route_in_repository=None,
) -> ReviewFixRuntime:
    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_logs.append((str(log_path), actor, message))

    def stage_plan_with_gemini(job, repository_path, paths, log_path, planning_mode="general"):
        planner_calls.append(
            {
                "job_id": job.job_id,
                "repository_path": str(repository_path),
                "planning_mode": planning_mode,
                "plan_path": str(paths["plan"]),
                "log_path": str(log_path),
            }
        )

    return ReviewFixRuntime(
        command_templates=template_runner,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((str(log_path), "STAGE", f"{job_id}:{stage.value}")),
        write_memory_retrieval_artifacts=lambda **kwargs: actor_logs.append(
            (str(kwargs["repository_path"]), "MEMORY", "written")
        ),
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
        build_route_runtime_context=lambda route: f"{route}-context",
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
        },
        actor_log_writer=actor_log_writer,
        template_for_route=lambda route: {"reviewer": "reviewer", "coder": "coder"}.get(route, route),
        template_for_route_in_repository=template_for_route_in_repository,
        read_improvement_runtime_context=lambda paths: dict(improvement_runtime or {}),
        stage_plan_with_gemini=stage_plan_with_gemini,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
    )


def test_stage_review_with_gemini_writes_stdout_when_review_missing(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    planner_calls: list[dict[str, object]] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(stdout_by_template={"reviewer": "# REVIEW\n- [ ] TODO\n"}),
        actor_logs=actor_logs,
        planner_calls=planner_calls,
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")

    runtime.stage_review_with_gemini(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert paths["review"].read_text(encoding="utf-8") == "# REVIEW\n- [ ] TODO\n"
    assert planner_calls == []


def test_stage_fix_with_codex_reroutes_to_planner_for_rebaseline(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    planner_calls: list[dict[str, object]] = []
    runtime = _build_runtime(
        template_runner=_FakeTemplateRunner(),
        actor_logs=actor_logs,
        planner_calls=planner_calls,
        improvement_runtime={"strategy": "design_rebaseline", "scope_restriction": "MVP_redefinition"},
    )
    paths = {
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
    }
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n", encoding="utf-8")

    runtime.stage_fix_with_codex(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert len(planner_calls) == 1
    assert planner_calls[0]["planning_mode"] == "dev_planning"
    assert any("Routing fix stage to planner" in message for _, actor, message in actor_logs if actor == "ORCHESTRATOR")


def test_stage_fix_with_codex_runs_coder_with_task_titles(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    planner_calls: list[dict[str, object]] = []
    runner = _FakeTemplateRunner()
    runtime = _build_runtime(
        template_runner=runner,
        actor_logs=actor_logs,
        planner_calls=planner_calls,
        improvement_runtime={"task_titles": ["모바일 레이아웃 정리", "로딩 상태 정리"]},
    )
    paths = {
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "design": docs_path / "DESIGN_SYSTEM.md",
    }
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n", encoding="utf-8")

    runtime.stage_fix_with_codex(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert planner_calls == []
    assert [call[0] for call in runner.calls] == ["coder"]
    prompt_path = docs_path / "CODER_PROMPT_FIX.md"
    assert "모바일 레이아웃 정리" in prompt_path.read_text(encoding="utf-8")


def test_stage_review_with_gemini_uses_repository_aware_fallback_template(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    planner_calls: list[dict[str, object]] = []
    runner = _FakeTemplateRunner(stdout_by_template={"reviewer_fallback": "# REVIEW\n- [ ] fallback\n"})
    runtime = _build_runtime(
        template_runner=runner,
        actor_logs=actor_logs,
        planner_calls=planner_calls,
        template_for_route_in_repository=lambda route, repo_path, log_path=None: "reviewer_fallback",
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")

    runtime.stage_review_with_gemini(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert [call[0] for call in runner.calls] == ["reviewer_fallback"]
    assert "fallback" in paths["review"].read_text(encoding="utf-8")
