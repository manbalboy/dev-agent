"""Tests for persisted workflow node run API exposure."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.workflow_design import default_workflow_template


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="Node run API",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-88-node-runs",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_job_detail_api_includes_node_runs(app_components):
    _, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-node-runs")
    job.status = JobStatus.RUNNING.value
    job.workflow_id = "wf-default"
    store.create_job(job)

    started_at = utc_now_iso()
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-1",
            job_id=job.job_id,
            workflow_id="wf-default",
            node_id="n1",
            node_type="gh_read_issue",
            node_title="Issue read",
            status="success",
            attempt=1,
            started_at=started_at,
            finished_at=utc_now_iso(),
        )
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["job_id"] == job.job_id
    assert len(payload["node_runs"]) == 1
    assert payload["node_runs"][0]["node_type"] == "gh_read_issue"
    assert payload["node_runs"][0]["workflow_id"] == "wf-default"


def test_job_node_runs_api_returns_ordered_records(app_components):
    _, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-node-runs-endpoint")
    job.workflow_id = "wf-custom"
    store.create_job(job)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-2",
            job_id=job.job_id,
            workflow_id="wf-custom",
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:02+00:00",
            finished_at="2026-03-08T00:00:03+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-1",
            job_id=job.job_id,
            workflow_id="wf-custom",
            node_id="n1",
            node_type="gh_read_issue",
            node_title="Read issue",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        )
    )

    response = client.get(f"/api/jobs/{job.job_id}/node-runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == job.job_id
    assert payload["workflow_id"] == "wf-custom"
    assert [item["node_run_id"] for item in payload["node_runs"]] == ["nr-1", "nr-2"]


def test_job_detail_api_includes_resume_state(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    workflow = default_workflow_template()
    job = _make_job("job-detail-resume-state")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.FAILED.value
    job.attempt = 1
    job.max_attempts = 3
    job.workflow_id = workflow["workflow_id"]
    store.create_job(job)

    repo_path = settings.repository_workspace_path(job.repository, job.app_code)
    (repo_path / "_docs").mkdir(parents=True, exist_ok=True)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-r1",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n1",
            node_type="gh_read_issue",
            node_title="이슈 읽기",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-r2",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC 작성",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:03+00:00",
            finished_at="2026-03-08T00:00:04+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="nr-r3",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n16",
            node_type="ux_e2e_review",
            node_title="UX E2E 검수(PC/모바일 스샷)",
            status="failed",
            attempt=1,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
            error_message="ux review failed",
        )
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resume_state"]["enabled"] is True
    assert payload["resume_state"]["mode"] == "resume"
    assert payload["resume_state"]["failed_node_type"] == "ux_e2e_review"
    assert payload["resume_state"]["resume_from_node_id"] == "n16"
    assert len(payload["resume_state"]["skipped_nodes"]) == 16


def test_job_detail_api_includes_runtime_signals(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-runtime-signals")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.IMPROVEMENT_STAGE.value
    job.recovery_status = "auto_recovered"
    store.create_job(job)

    docs_dir = settings.repository_workspace_path(job.repository, job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "PRODUCT_REVIEW.json").write_text(
        '{\n  "scores": {"overall": 3.4},\n  "quality_gate": {"passed": true, "categories_below_threshold": []}\n}\n',
        encoding="utf-8",
    )
    (docs_dir / "IMPROVEMENT_LOOP_STATE.json").write_text(
        '{\n  "strategy": "quality_hardening",\n  "strategy_change_required": true,\n  "next_scope_restriction": "P1_only"\n}\n',
        encoding="utf-8",
    )
    (docs_dir / "NEXT_IMPROVEMENT_TASKS.json").write_text(
        '{\n  "tasks": [{"title": "에러 상태 보강", "recommended_node_type": "codex_fix"}]\n}\n',
        encoding="utf-8",
    )
    (docs_dir / "REPO_MATURITY.json").write_text(
        '{\n  "level": "usable",\n  "score": 71,\n  "progression": "up"\n}\n',
        encoding="utf-8",
    )
    (docs_dir / "QUALITY_TREND.json").write_text(
        '{\n  "trend_direction": "stable",\n  "delta_from_previous": 0.1,\n  "review_round_count": 3,\n  "persistent_low_categories": ["test_coverage"],\n  "stagnant_categories": ["test_coverage"],\n  "category_deltas": {"test_coverage": 0}\n}\n',
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_signals"]["review_overall"] == 3.4
    assert payload["runtime_signals"]["strategy"] == "quality_hardening"
    assert payload["runtime_signals"]["scope_restriction"] == "P1_only"
    assert payload["runtime_signals"]["next_task_title"] == "에러 상태 보강"
    assert payload["runtime_signals"]["maturity_level"] == "usable"
    assert payload["runtime_signals"]["quality_trend_direction"] == "stable"
    assert payload["runtime_signals"]["persistent_low_categories"] == ["test_coverage"]
    assert payload["runtime_signals"]["stagnant_categories"] == ["test_coverage"]
    assert payload["runtime_signals"]["category_deltas"]["test_coverage"] == 0


def test_job_detail_api_includes_manual_retry_options(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    workflow = default_workflow_template()
    job = _make_job("job-detail-manual-retry-options")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.FAILED.value
    job.attempt = 1
    job.max_attempts = 2
    job.workflow_id = workflow["workflow_id"]
    store.create_job(job)

    repo_path = settings.repository_workspace_path(job.repository, job.app_code)
    (repo_path / "_docs").mkdir(parents=True, exist_ok=True)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="mr-opt-1",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n1",
            node_type="gh_read_issue",
            node_title="이슈 읽기",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="mr-opt-2",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC 작성",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:03+00:00",
            finished_at="2026-03-08T00:00:04+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="mr-opt-3",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n16",
            node_type="ux_e2e_review",
            node_title="UX E2E 검수(PC/모바일 스샷)",
            status="failed",
            attempt=1,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
        )
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_retry_options"]["can_manual_retry"] is True
    assert payload["manual_retry_options"]["can_resume_failed_node"] is True
    assert payload["manual_retry_options"]["failed_node_id"] == "n16"
    assert any(item["id"] == "n16" for item in payload["manual_retry_options"]["safe_nodes"])
