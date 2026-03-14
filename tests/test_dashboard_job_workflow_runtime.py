from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.dashboard_job_workflow_runtime import DashboardJobWorkflowRuntime
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.workflow_design import default_workflow_template, validate_workflow
from app.workflow_resume import (
    build_workflow_artifact_paths,
    compute_workflow_resume_state,
    linearize_workflow_nodes,
    list_manual_resume_candidates,
    read_improvement_runtime_context,
)


def _make_job(job_id: str, *, workflow_id: str, status: str = JobStatus.FAILED.value, attempt: int = 3) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=91,
        issue_title="Workflow runtime",
        issue_url="https://github.com/owner/repo/issues/91",
        status=status,
        stage=JobStage.FAILED.value if status == JobStatus.FAILED.value else JobStage.QUEUED.value,
        attempt=attempt,
        max_attempts=max(3, attempt),
        branch_name=f"agenthub/test/{job_id}",
        pr_url=None,
        error_message="previous failure" if status == JobStatus.FAILED.value else None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=now if status == JobStatus.FAILED.value else None,
        workflow_id=workflow_id,
    )


def _build_runtime(
    tmp_path: Path,
    *,
    workflows_payload: dict,
    selected_workflow_id: str,
) -> DashboardJobWorkflowRuntime:
    workspace_path = tmp_path / "workspace"
    return DashboardJobWorkflowRuntime(
        apps_config_path=tmp_path / "apps.json",
        workflows_config_path=tmp_path / "workflows.json",
        load_workflows=lambda _path: workflows_payload,
        default_workflow_template=default_workflow_template,
        resolve_workflow_selection=lambda **_kwargs: SimpleNamespace(
            workflow_id=selected_workflow_id,
            source="requested",
            warning="",
        ),
        validate_workflow=validate_workflow,
        linearize_workflow_nodes=linearize_workflow_nodes,
        job_workspace_path=lambda _job: workspace_path,
        build_workflow_artifact_paths=build_workflow_artifact_paths,
        read_improvement_runtime_context=read_improvement_runtime_context,
        compute_workflow_resume_state=compute_workflow_resume_state,
        list_manual_resume_candidates=list_manual_resume_candidates,
    )


def test_dashboard_job_workflow_runtime_reports_invalid_workflow_as_fixed_pipeline(tmp_path: Path) -> None:
    broken_workflow = {
        "workflow_id": "broken-workflow",
        "name": "Broken Workflow",
        "nodes": [{"id": "n1", "type": "not_supported", "title": "깨진 노드"}],
        "edges": [],
    }
    runtime = _build_runtime(
        tmp_path,
        workflows_payload={
            "default_workflow_id": "broken-workflow",
            "workflows": [broken_workflow],
        },
        selected_workflow_id="broken-workflow",
    )

    payload, workflow, ordered_nodes = runtime.resolve_job_workflow_runtime(
        _make_job("job-broken", workflow_id="broken-workflow")
    )

    assert payload["resolved_workflow_id"] == "broken-workflow"
    assert payload["definition_valid"] is False
    assert payload["uses_fixed_pipeline"] is True
    assert payload["validation_errors"]
    assert payload["nodes"][0]["type"] == "not_supported"
    assert workflow == {}
    assert ordered_nodes == []


def test_dashboard_job_workflow_runtime_extracts_and_dedupes_fallback_events() -> None:
    events = [
        {"timestamp": "2026-03-10T00:00:00+00:00", "message": "Workflow resolution warning: Requested workflow_id not found: wf-missing"},
        {"timestamp": "2026-03-10T00:00:01+00:00", "message": "Workflow validation failed; fallback to fixed pipeline: entry_node_id is required"},
        {"timestamp": "2026-03-10T00:00:02+00:00", "message": "Workflow validation failed; fallback to fixed pipeline: entry_node_id is required"},
    ]

    payload = DashboardJobWorkflowRuntime.extract_workflow_fallback_events(events)

    assert [item["kind"] for item in payload] == ["resolution_warning", "validation_failure"]
    assert payload[0]["uses_fixed_pipeline"] is False
    assert payload[1]["uses_fixed_pipeline"] is True
    assert payload[1]["timestamp"] == "2026-03-10T00:00:01+00:00"


def test_dashboard_job_workflow_runtime_builds_resume_and_manual_retry_payload(tmp_path: Path) -> None:
    workflow = default_workflow_template()
    runtime = _build_runtime(
        tmp_path,
        workflows_payload={
            "default_workflow_id": workflow["workflow_id"],
            "workflows": [workflow],
        },
        selected_workflow_id=workflow["workflow_id"],
    )
    job = _make_job("job-resume", workflow_id=workflow["workflow_id"], attempt=3)
    node_runs = [
        NodeRunRecord(
            node_run_id="n1",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n1",
            node_type="gh_read_issue",
            node_title="이슈 읽기",
            status="success",
            attempt=3,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        ),
        NodeRunRecord(
            node_run_id="n2",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC 작성",
            status="success",
            attempt=3,
            started_at="2026-03-08T00:00:03+00:00",
            finished_at="2026-03-08T00:00:04+00:00",
        ),
        NodeRunRecord(
            node_run_id="n16",
            job_id=job.job_id,
            workflow_id=workflow["workflow_id"],
            node_id="n16",
            node_type="ux_e2e_review",
            node_title="UX E2E 검수(PC/모바일 스샷)",
            status="failed",
            attempt=3,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
            error_message="ux review failed",
        ),
    ]

    resume_state = runtime.compute_job_resume_payload(job, node_runs)
    manual_retry_options = runtime.build_manual_retry_options(job, node_runs=node_runs)

    assert resume_state["enabled"] is True
    assert resume_state["mode"] == "resume"
    assert resume_state["failed_node_id"] == "n16"
    assert resume_state["resume_from_node_id"] == "n16"
    assert manual_retry_options["workflow_id"] == workflow["workflow_id"]
    assert manual_retry_options["can_manual_retry"] is True
    assert manual_retry_options["can_resume_failed_node"] is True
    assert manual_retry_options["default_mode"] == "resume_failed_node"
    assert any(item["id"] == "n16" for item in manual_retry_options["safe_nodes"])
