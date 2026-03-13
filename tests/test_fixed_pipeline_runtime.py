"""Tests for fixed pipeline runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.fixed_pipeline_runtime import FixedPipelineRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job() -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id="job-fixed-pipeline",
        repository="owner/repo",
        issue_number=15,
        issue_title="fixed pipeline",
        issue_url="https://github.com/owner/repo/issues/15",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-15",
        pr_url=None,
        error_message=None,
        log_file="job-fixed-pipeline.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_fixed_pipeline_runtime_runs_core_stages_in_order(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    job = _make_job()
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    log_path = tmp_path / "job.log"
    paths = {"spec": repository_path / "_docs" / "SPEC.md"}

    def _record(name: str):
        def _inner(*args, **kwargs):
            del args, kwargs
            calls.append((name, "called"))
            return None

        return _inner

    runtime = FixedPipelineRuntime(
        stage_read_issue=lambda *_args, **_kwargs: {"title": "Issue"},
        commit_markdown_changes_after_stage=lambda _job, _repo, stage_name, _log: calls.append(("commit_md", stage_name)),
        stage_write_spec=lambda *_args, **_kwargs: paths,
        stage_idea_to_product_brief=_record("idea"),
        stage_generate_user_flows=_record("flows"),
        stage_define_mvp_scope=_record("mvp"),
        stage_architecture_planning=_record("arch"),
        stage_project_scaffolding=_record("scaffold"),
        stage_plan_with_gemini=_record("plan"),
        snapshot_plan_variant=lambda *_args, **_kwargs: calls.append(("snapshot", "general")),
        stage_design_with_codex=_record("design"),
        stage_publish_with_codex=_record("publish"),
        stage_implement_with_codex=_record("implement"),
        stage_summarize_code_changes=_record("summary"),
        run_test_hard_gate=lambda **kwargs: calls.append(("hard_gate", kwargs["stage"].value)),
        stage_commit=lambda _job, _repo, stage, _log, commit_type: calls.append(("commit", f"{stage.value}:{commit_type}")),
        stage_review_with_gemini=_record("review"),
        stage_product_review=_record("product_review"),
        stage_improvement_stage=_record("improvement"),
        stage_fix_with_codex=_record("fix"),
        stage_documentation_with_claude=_record("documentation"),
        stage_push_branch=lambda *_args, **_kwargs: calls.append(("push", "called")),
        stage_create_pr=lambda *_args, **_kwargs: calls.append(("pr", "called")),
        set_stage=lambda _job_id, stage, _log: calls.append(("set_stage", stage.value)),
    )

    runtime.run_fixed_pipeline(job, repository_path, log_path)

    assert ("idea", "called") in calls
    assert ("plan", "called") in calls
    assert ("hard_gate", JobStage.TEST_AFTER_IMPLEMENT.value) in calls
    assert ("commit", f"{JobStage.COMMIT_IMPLEMENT.value}:feat") in calls
    assert ("hard_gate", JobStage.TEST_AFTER_FIX.value) in calls
    assert ("commit", f"{JobStage.COMMIT_FIX.value}:fix") in calls
    assert ("documentation", "called") in calls
    assert calls[-1] == ("set_stage", JobStage.FINALIZE.value)
