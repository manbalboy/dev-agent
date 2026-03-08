"""Tests for dashboard-driven manual workflow rerun/resume."""

from __future__ import annotations

import json
from pathlib import Path
import shlex

from fastapi.testclient import TestClient

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.orchestrator import Orchestrator
from app.workflow_design import default_workflow_template


class FakeTemplateRunner:
    """Minimal AI template runner for orchestration tests."""

    def has_template(self, template_name: str) -> bool:
        return False

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        raise AssertionError(f"unexpected template execution: {template_name}")


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=91,
        issue_title="Manual workflow retry",
        issue_url="https://github.com/owner/repo/issues/91",
        status=JobStatus.FAILED.value,
        stage=JobStage.FAILED.value,
        attempt=3,
        max_attempts=3,
        branch_name=f"agenthub/issue-91-{job_id}",
        pr_url=None,
        error_message="previous failure",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=now,
    )


def test_manual_retry_api_queues_selected_node_resume(app_components):
    _, store, app = app_components
    client = TestClient(app)

    workflow = default_workflow_template()
    job = _make_job("job-manual-selected-node")
    job.workflow_id = workflow["workflow_id"]
    store.create_job(job)
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="api-r1",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n1",
            node_type="gh_read_issue",
            node_title="이슈 읽기",
            status="success",
            attempt=3,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="api-r2",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC 작성",
            status="success",
            attempt=3,
            started_at="2026-03-08T00:00:03+00:00",
            finished_at="2026-03-08T00:00:04+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="api-r3",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n8",
            node_type="designer_task",
            node_title="디자인 시스템",
            status="failed",
            attempt=3,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
            error_message="design failed",
        )
    )

    response = client.post(
        f"/api/jobs/{job.job_id}/workflow/manual-retry",
        json={
            "mode": "resume_from_node",
            "node_id": "n7",
            "note": "큰틀 플랜부터 다시 검토",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued"] is True
    assert payload["mode"] == "resume_from_node"
    assert payload["target_node_id"] == "n7"
    assert payload["resume_state"]["override_active"] is True
    assert payload["resume_state"]["resume_from_node_id"] == "n7"

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.QUEUED.value
    assert stored.stage == JobStage.QUEUED.value
    assert stored.attempt == 3
    assert stored.max_attempts == 4
    assert stored.manual_resume_mode == "resume_from_node"
    assert stored.manual_resume_node_id == "n7"
    assert store.queue_size() == 1


def test_manual_retry_api_rejects_side_effect_node(app_components):
    _, store, app = app_components
    client = TestClient(app)

    workflow = default_workflow_template()
    job = _make_job("job-manual-unsafe-node")
    job.workflow_id = workflow["workflow_id"]
    store.create_job(job)

    response = client.post(
        f"/api/jobs/{job.job_id}/workflow/manual-retry",
        json={
            "mode": "resume_from_node",
            "node_id": "n28",
        },
    )

    assert response.status_code == 400
    assert "부작용이 있는 노드" in response.json()["detail"]


def test_manual_retry_override_is_consumed_by_orchestrator(app_components):
    settings, store, _ = app_components
    job = _make_job("job-manual-override-run")
    job.status = JobStatus.QUEUED.value
    job.stage = JobStage.QUEUED.value
    job.attempt = 1
    job.max_attempts = 2
    job.workflow_id = "test_manual_resume"
    job.manual_resume_mode = "resume_from_node"
    job.manual_resume_node_id = "n3"
    job.manual_resume_requested_at = utc_now_iso()
    job.manual_resume_note = "제품 정의부터 다시 시작"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="mr-n1",
            job_id=job.job_id,
            workflow_id=job.workflow_id,
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
            node_run_id="mr-n2",
            job_id=job.job_id,
            workflow_id=job.workflow_id,
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
            node_run_id="mr-n4",
            job_id=job.job_id,
            workflow_id=job.workflow_id,
            node_id="n4",
            node_type="generate_user_flows",
            node_title="사용자 흐름 정의",
            status="failed",
            attempt=1,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
            error_message="flows failed",
        )
    )

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")
        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            Path(parts[-1]).mkdir(parents=True, exist_ok=True)
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    calls: list[str] = []

    def fake_product_brief(job_obj, repo_path, paths, log_path):
        calls.append("idea_to_product_brief")
        paths["product_brief"].write_text(
            "# PRODUCT BRIEF\n\n## Context Anchor\n- Job ID: test\n- Issue Title: manual\n\n## Product Goal\n- retry\n",
            encoding="utf-8",
        )

    def fake_user_flows(job_obj, repo_path, paths, log_path):
        calls.append("generate_user_flows")
        paths["user_flows"].write_text(
            "# USER FLOWS\n\n## Primary Flow\n1. retry\n",
            encoding="utf-8",
        )

    orchestrator._stage_idea_to_product_brief = fake_product_brief  # type: ignore[method-assign]
    orchestrator._stage_generate_user_flows = fake_user_flows  # type: ignore[method-assign]
    orchestrator._load_active_workflow = lambda _job, _log_path: {
        "workflow_id": "test_manual_resume",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기"},
            {"id": "n2", "type": "write_spec", "title": "SPEC 작성"},
            {"id": "n3", "type": "idea_to_product_brief", "title": "제품 정의"},
            {"id": "n4", "type": "generate_user_flows", "title": "사용자 흐름 정의"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
        ],
    }  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()

    assert processed is True
    assert calls == ["idea_to_product_brief", "generate_user_flows"]

    node_runs = store.list_node_runs(job.job_id)
    assert [
        (item.attempt, item.node_type, item.status)
        for item in node_runs
    ] == [
        (1, "gh_read_issue", "success"),
        (1, "write_spec", "success"),
        (1, "generate_user_flows", "failed"),
        (2, "idea_to_product_brief", "success"),
        (2, "generate_user_flows", "success"),
    ]

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.manual_resume_mode == ""
    assert stored.manual_resume_node_id == ""
    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "Workflow resume active:" in log_text
    assert "from=n3" in log_text
