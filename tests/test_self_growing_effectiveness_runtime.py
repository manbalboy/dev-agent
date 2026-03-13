from __future__ import annotations

import json

from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.self_growing_effectiveness_runtime import SelfGrowingEffectivenessRuntime
from app.workflow_resume import build_workflow_artifact_paths


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="follow-up effectiveness",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.DONE.value,
        stage=JobStage.DONE.value,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/issue-77-{job_id}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )


def test_self_growing_effectiveness_runtime_marks_followup_improved(app_components):
    settings, store, _ = app_components
    parent_job = _make_job("job-parent-effect")
    child_job = _make_job("job-child-effect")
    child_job.job_kind = "followup_backlog"
    child_job.parent_job_id = parent_job.job_id
    child_job.backlog_candidate_id = "next_improvement_task:job-parent-effect:1"
    store.create_job(parent_job)
    store.create_job(child_job)

    repository_path = settings.repository_workspace_path(child_job.repository, child_job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)

    runtime = SelfGrowingEffectivenessRuntime(store=store)
    payload = runtime.write_self_growing_effectiveness_artifact(
        job=child_job,
        repository_path=repository_path,
        paths=paths,
        review_payload={"scores": {"overall": 3.8}, "quality_gate": {"passed": True, "categories_below_threshold": []}},
        maturity_snapshot={"level": "usable", "score": 78, "progression": "up"},
        trend_snapshot={"trend_direction": "improving", "review_round_count": 3, "delta_from_previous": 0.4},
        review_history_entries=[
            {
                "generated_at": "2026-03-13T00:00:00+00:00",
                "job_id": parent_job.job_id,
                "overall": 3.1,
                "maturity_level": "mvp",
                "maturity_score": 68,
            },
            {
                "generated_at": "2026-03-13T01:00:00+00:00",
                "job_id": child_job.job_id,
                "overall": 3.8,
                "maturity_level": "usable",
                "maturity_score": 78,
            },
        ],
    )

    assert payload["active"] is True
    assert payload["status"] == "improved"
    assert payload["deltas"]["review_overall"] == 0.7
    assert payload["deltas"]["maturity_score"] == 10
    saved = json.loads(paths["self_growing_effectiveness"].read_text(encoding="utf-8"))
    assert saved["status"] == "improved"
    assert saved["parent_job_id"] == parent_job.job_id


def test_self_growing_effectiveness_runtime_marks_followup_insufficient_baseline(app_components):
    settings, store, _ = app_components
    child_job = _make_job("job-child-no-baseline")
    child_job.job_kind = "followup_backlog"
    child_job.parent_job_id = "job-parent-missing"
    child_job.backlog_candidate_id = "next_improvement_task:job-parent-missing:1"
    store.create_job(child_job)

    repository_path = settings.repository_workspace_path(child_job.repository, child_job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)

    runtime = SelfGrowingEffectivenessRuntime(store=store)
    payload = runtime.write_self_growing_effectiveness_artifact(
        job=child_job,
        repository_path=repository_path,
        paths=paths,
        review_payload={"scores": {"overall": 3.2}, "quality_gate": {"passed": True, "categories_below_threshold": []}},
        maturity_snapshot={"level": "mvp", "score": 64, "progression": "unchanged"},
        trend_snapshot={"trend_direction": "stable", "review_round_count": 1, "delta_from_previous": 0.0},
        review_history_entries=[
            {
                "generated_at": "2026-03-13T01:00:00+00:00",
                "job_id": child_job.job_id,
                "overall": 3.2,
                "maturity_level": "mvp",
                "maturity_score": 64,
            }
        ],
    )

    assert payload["status"] == "insufficient_baseline"
    assert "parent_review_history_entry" in payload["baseline_missing"]
