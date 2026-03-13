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
        write_integration_guide_summary_artifact=None,
        write_integration_code_patterns_artifact=None,
        write_integration_verification_checklist_artifact=None,
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


def test_run_planner_legacy_one_shot_embeds_integration_recommendations(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runner = _FakeTemplateRunner(stdout_by_template={"planner": "# PLAN\n- recommendation\n"})
    runtime = _build_runtime(template_runner=runner, actor_logs=actor_logs)
    runtime.write_integration_recommendation_artifact = lambda **kwargs: (
        kwargs["paths"]["integration_recommendations"].write_text(
            '{"items":[{"integration_id":"google_maps","reason":"지도 기능 후보"}]}\n',
            encoding="utf-8",
        )
        or {"count": 1}
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "integration_recommendations": docs_path / "INTEGRATION_RECOMMENDATIONS.json",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")

    runtime.run_planner_legacy_one_shot(
        job=_make_job("job-planner-recommendation"),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    prompt_text = (docs_path / "PLANNER_PROMPT.md").read_text(encoding="utf-8")
    assert "Integration Recommendations" in prompt_text
    assert "INTEGRATION_RECOMMENDATIONS.json" in prompt_text
    assert "도입 검토 후보" in prompt_text
    assert "google_maps" in prompt_text


def test_run_planner_legacy_one_shot_embeds_integration_guide_summary(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runner = _FakeTemplateRunner(stdout_by_template={"planner": "# PLAN\n- guide\n"})
    runtime = _build_runtime(template_runner=runner, actor_logs=actor_logs)
    runtime.write_integration_guide_summary_artifact = lambda **kwargs: (
        kwargs["paths"]["integration_guide_summary"].write_text(
            "# INTEGRATION_GUIDE_SUMMARY\n\n## Google Maps\n\n- required_env_keys: GOOGLE_MAPS_API_KEY\n",
            encoding="utf-8",
        )
        or {"count": 1}
    )
    runtime.write_integration_code_patterns_artifact = lambda **kwargs: (
        kwargs["paths"]["integration_code_patterns"].write_text(
            "# INTEGRATION_CODE_PATTERNS\n\n## Google Maps\n\n- MapLoader 래퍼 사용\n",
            encoding="utf-8",
        )
        or {"count": 1}
    )
    runtime.write_integration_verification_checklist_artifact = lambda **kwargs: (
        kwargs["paths"]["integration_verification_checklist"].write_text(
            "# INTEGRATION_VERIFICATION_CHECKLIST\n\n## Google Maps\n\n- [ ] 지도 로딩 검증\n",
            encoding="utf-8",
        )
        or {"count": 1}
    )
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "integration_guide_summary": docs_path / "INTEGRATION_GUIDE_SUMMARY.md",
        "integration_code_patterns": docs_path / "INTEGRATION_CODE_PATTERNS.md",
        "integration_verification_checklist": docs_path / "INTEGRATION_VERIFICATION_CHECKLIST.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")

    runtime.run_planner_legacy_one_shot(
        job=_make_job("job-planner-guide-summary"),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    prompt_text = (docs_path / "PLANNER_PROMPT.md").read_text(encoding="utf-8")
    assert "Integration Guide Summary" in prompt_text
    assert "INTEGRATION_GUIDE_SUMMARY.md" in prompt_text
    assert "Integration Code Patterns" in prompt_text
    assert "INTEGRATION_CODE_PATTERNS.md" in prompt_text
    assert "Integration Verification Checklist" in prompt_text
    assert "INTEGRATION_VERIFICATION_CHECKLIST.md" in prompt_text
    assert "Google Maps" in prompt_text


def test_run_planner_legacy_one_shot_appends_integration_usage_trail(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    actor_logs: list[tuple[str, str, str]] = []
    runner = _FakeTemplateRunner(stdout_by_template={"planner": "# PLAN\n- guide\n"})
    runtime = _build_runtime(template_runner=runner, actor_logs=actor_logs)
    usage_events: list[dict[str, str]] = []
    runtime.write_integration_guide_summary_artifact = lambda **kwargs: (
        kwargs["paths"]["integration_guide_summary"].write_text(
            "# INTEGRATION_GUIDE_SUMMARY\n\n## Google Maps\n",
            encoding="utf-8",
        )
        or {"count": 1}
    )
    runtime.append_integration_usage_trail_event = lambda **kwargs: usage_events.append(
        {
            "stage": str(kwargs["stage"]),
            "route": str(kwargs["route"]),
            "prompt_path": str(kwargs["prompt_path"]),
        }
    ) or {"active": True}
    paths = {
        "spec": docs_path / "SPEC.md",
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "integration_guide_summary": docs_path / "INTEGRATION_GUIDE_SUMMARY.md",
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")

    runtime.run_planner_legacy_one_shot(
        job=_make_job("job-planner-usage-trail"),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    assert usage_events == [
        {
            "stage": "plan_with_gemini",
            "route": "planner",
            "prompt_path": str(docs_path / "PLANNER_PROMPT.md"),
        }
    ]
