"""Tests for planner runtime repository-aware template fallback."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.planner_runtime import PlannerRuntime


def _make_job(job_id: str = "job-planner-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="planner runtime fallback test",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-77-planner-runtime",
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
    template_for_route_in_repository=None,
) -> PlannerRuntime:
    def actor_log_writer(log_path: Path, actor: str):
        return lambda message: actor_logs.append((str(log_path), actor, message))

    return PlannerRuntime(
        command_templates=template_runner,
        set_stage=lambda job_id, stage, log_path: actor_logs.append((str(log_path), "STAGE", f"{job_id}:{stage.value}")),
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
        write_memory_retrieval_artifacts=lambda **kwargs: actor_logs.append(
            (str(kwargs["repository_path"]), "MEMORY", "written")
        ),
        build_route_runtime_context=lambda route: f"{route}-context",
        is_long_track_job=lambda job: False,
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
        template_for_route=lambda route: {"planner": "planner"}.get(route, route),
        template_for_route_in_repository=template_for_route_in_repository,
        route_allows_tool=lambda route, tool: False,
        execute_planner_tool_request=lambda **kwargs: {},
        feature_enabled=lambda name: False,
        planner_shadow_runner=None,
    )


def test_run_planner_legacy_one_shot_uses_repository_aware_fallback_template(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runner = _FakeTemplateRunner(stdout_by_template={"planner_fallback": "# PLAN\n- fallback\n"})
    runtime = _build_runtime(
        template_runner=runner,
        actor_logs=actor_logs,
        template_for_route_in_repository=lambda route, repo_path, log_path=None: "planner_fallback",
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")

    runtime.run_planner_legacy_one_shot(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert [call[0] for call in runner.calls] == ["planner_fallback"]
    assert "fallback" in paths["plan"].read_text(encoding="utf-8")
