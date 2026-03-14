from __future__ import annotations

import json
from pathlib import Path

from app.dashboard_job_enqueue_runtime import DashboardJobEnqueueRuntime
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.workflow_resolution import resolve_workflow_selection
from app.workflow_resume import build_workflow_artifact_paths


def _write_workflow_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [
                    {
                        "workflow_id": "wf-default",
                        "name": "Default",
                        "version": 1,
                        "entry_node_id": "n1",
                        "nodes": [{"id": "n1", "type": "gh_read_issue"}],
                        "edges": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_apps(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "code": "default",
                    "name": "Default",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                    "source_repository": "manbalboy/Food",
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _make_job(job_id: str, *, issue_number: int, status: str = JobStatus.QUEUED.value) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=issue_number,
        issue_title="source issue",
        issue_url=f"https://github.com/owner/repo/issues/{issue_number}",
        status=status,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name=f"agenthub/default/issue-{issue_number}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
        track="enhance",
        workflow_id="wf-default",
        source_repository="manbalboy/Food",
    )


def test_dashboard_job_enqueue_runtime_normalizes_track_and_builds_branch_name() -> None:
    assert DashboardJobEnqueueRuntime.normalize_app_code("Food") == "food"
    assert DashboardJobEnqueueRuntime.normalize_track("ultra-10") == "ultra10"
    assert DashboardJobEnqueueRuntime.detect_title_track("[long] retry flow") == "long"
    assert (
        DashboardJobEnqueueRuntime.build_branch_name(
            "food",
            77,
            "enhance",
            "job-uuid-12345678",
            keep_branch=False,
        )
        == "agenthub/food/issue-77-enhance"
    )
    assert (
        DashboardJobEnqueueRuntime.build_branch_name(
            "food",
            77,
            "bug",
            "job-uuid-12345678",
            requested_branch_name=" feature/fix branch ",
        )
        == "feature/fix-branch"
    )


def test_dashboard_job_enqueue_runtime_finds_active_job(app_components) -> None:
    _, store, _ = app_components
    store.create_job(_make_job("job-running", issue_number=77, status=JobStatus.RUNNING.value))
    store.create_job(_make_job("job-done", issue_number=88, status=JobStatus.DONE.value))

    found = DashboardJobEnqueueRuntime.find_active_job(store, "owner/repo", 77)
    missing = DashboardJobEnqueueRuntime.find_active_job(store, "owner/repo", 88)

    assert found is not None
    assert found.job_id == "job-running"
    assert missing is None


def test_dashboard_job_enqueue_runtime_queues_followup_job_and_writes_artifact(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    apps_path = tmp_path / "config" / "apps.json"
    workflows_path = tmp_path / "config" / "workflows.json"
    _write_apps(apps_path)
    _write_workflow_catalog(workflows_path)

    runtime = DashboardJobEnqueueRuntime(
        store=store,
        settings=settings,
        apps_config_path=apps_path,
        workflows_config_path=workflows_path,
        resolve_workflow_selection=resolve_workflow_selection,
        build_workflow_artifact_paths=build_workflow_artifact_paths,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        uuid_factory=lambda: "queued-job-1",
    )
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")

    source_job = _make_job("job-source", issue_number=501, status=JobStatus.DONE.value)
    store.create_job(source_job)
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": "next_improvement_task:job-source:next_1",
            "repository": "owner/repo",
            "execution_repository": "manbalboy/Food",
            "app_code": "default",
            "workflow_id": "",
            "title": "회귀 테스트 보강",
            "summary": "실패 재현 케이스를 고정한다",
            "priority": "P1",
            "state": "approved",
            "payload": {
                "source_kind": "next_improvement_task",
                "job_id": "job-source",
                "issue_number": 501,
                "recommended_action": "add_regression_test",
            },
            "created_at": "2026-03-13T01:11:00+00:00",
            "updated_at": "2026-03-13T01:11:00+00:00",
        }
    )
    candidate = runtime_store.get_backlog_candidate("next_improvement_task:job-source:next_1")
    assert candidate is not None

    queued_job, artifact_path = runtime.queue_followup_job_from_backlog_candidate(
        candidate=candidate,
        runtime_store=runtime_store,
        note="run next loop",
    )

    updated_candidate = runtime_store.get_backlog_candidate("next_improvement_task:job-source:next_1")
    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert queued_job.job_id == "queued-job-1"
    assert queued_job.job_kind == "followup_backlog"
    assert queued_job.source_repository == "manbalboy/Food"
    assert queued_job.branch_name == "agenthub/default/issue-501"
    assert artifact_path.name == "FOLLOWUP_BACKLOG_TASK.json"
    assert artifact_payload["queued_job_id"] == "queued-job-1"
    assert artifact_payload["operator_note"] == "run next loop"
    assert updated_candidate is not None
    assert updated_candidate["state"] == "queued"
    assert updated_candidate["payload"]["queued_job_id"] == "queued-job-1"
