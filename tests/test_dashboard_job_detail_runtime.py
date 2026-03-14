from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.dashboard_job_detail_runtime import DashboardJobDetailRuntime
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso


class _FakeStore:
    def __init__(self, job: JobRecord | None, node_runs: list[NodeRunRecord]) -> None:
        self._job = job
        self._node_runs = node_runs

    def get_job(self, job_id: str) -> JobRecord | None:
        if self._job is None or self._job.job_id != job_id:
            return None
        return self._job

    def list_node_runs(self, job_id: str) -> list[NodeRunRecord]:
        if self._job is None or self._job.job_id != job_id:
            return []
        return list(self._node_runs)


def _make_job(job_id: str = "job-detail-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="Job detail runtime",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.FAILED.value,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/default/issue-77",
        pr_url=None,
        error_message="spec mismatch",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )


def _make_node_run(job_id: str) -> NodeRunRecord:
    return NodeRunRecord(
        node_run_id="nr-1",
        job_id=job_id,
        workflow_id="wf-default",
        node_id="n1",
        node_type="gh_read_issue",
        node_title="Read issue",
        status="success",
        attempt=1,
        started_at="2026-03-14T00:00:00+00:00",
        finished_at="2026-03-14T00:00:01+00:00",
    )


def _build_runtime(tmp_path: Path, job: JobRecord | None, node_runs: list[NodeRunRecord]) -> DashboardJobDetailRuntime:
    log_path = tmp_path / "job.log"
    log_path.write_text("debug log\n", encoding="utf-8")
    return DashboardJobDetailRuntime(
        store=_FakeStore(job, node_runs),
        resolve_debug_log_path=lambda _job: log_path,
        parse_log_events=lambda _path: [{"timestamp": "t1", "kind": "info", "message": "hello"}],
        job_workspace_path=lambda _job: tmp_path / "workspace",
        read_agent_md_files=lambda _workspace: [{"name": "STATUS.md", "content": "ok"}],
        read_stage_md_snapshots=lambda _job_id: [{"attempt": 1, "stage": "implement"}],
        resolve_job_workflow_runtime=lambda _job: ({"resolved_workflow_id": "wf-default", "uses_fixed_pipeline": False}, {}, []),
        extract_workflow_fallback_events=lambda _events: [{"kind": "resolution_warning", "uses_fixed_pipeline": True}],
        compute_job_resume_state=lambda _job, _node_runs: {"enabled": True, "mode": "resume_failed_node"},
        build_job_runtime_signals=lambda _job: {"resume_mode": "resume_failed_node"},
        read_job_memory_trace=lambda _job: {"enabled": True},
        read_job_assistant_diagnosis_trace=lambda _job: {"enabled": True},
        read_job_runtime_recovery_trace=lambda _job: {"event_count": 1},
        build_failure_classification_summary=lambda _job, _trace: {"failure_class": "spec"},
        build_job_needs_human_summary=lambda _job, _trace, _classification: {"active": True},
        build_job_dead_letter_summary=lambda _job, _trace, _classification: {"active": False},
        build_job_dead_letter_action_trail=lambda _trace: [{"decision": "retry"}],
        build_job_requeue_reason_summary=lambda _job, _trace: {"active": True},
        build_job_self_growing_effectiveness=lambda _job: {"active": True},
        build_job_mobile_e2e_result=lambda _job: {"active": False},
        build_manual_retry_options=lambda _job, _node_runs: {"default_mode": "resume_failed_node"},
        build_job_lineage=lambda _job: {"job_kind": "issue"},
        build_job_log_summary=lambda _job, _events: {"channels": ["debug"]},
        build_job_operator_inputs=lambda _job: {"count": 0},
        build_job_integration_operator_boundary=lambda _job: {"blocked": False},
        build_job_integration_usage_trail=lambda _job: {"count": 0},
        build_job_integration_health_facets=lambda _job, _boundary, _trail, _summary, _classification: {"status": "ok"},
        stop_signal_exists=lambda _job_id: True,
    )


def test_dashboard_job_detail_runtime_returns_detail_payload(tmp_path: Path) -> None:
    job = _make_job()
    runtime = _build_runtime(tmp_path, job, [_make_node_run(job.job_id)])

    payload = runtime.get_job_detail_payload(job.job_id)

    assert payload["job"]["job_id"] == job.job_id
    assert payload["events"][0]["message"] == "hello"
    assert payload["workflow_runtime"]["fallback_events"][0]["kind"] == "resolution_warning"
    assert payload["workflow_runtime"]["uses_fixed_pipeline"] is True
    assert payload["manual_retry_options"]["default_mode"] == "resume_failed_node"
    assert payload["stop_requested"] is True


def test_dashboard_job_detail_runtime_returns_node_run_payload(tmp_path: Path) -> None:
    job = _make_job("job-node-runs-runtime")
    node_run = _make_node_run(job.job_id)
    runtime = _build_runtime(tmp_path, job, [node_run])

    payload = runtime.get_job_node_runs_payload(job.job_id)

    assert payload["job_id"] == job.job_id
    assert payload["node_runs"][0]["node_run_id"] == "nr-1"
    assert payload["resume_state"]["mode"] == "resume_failed_node"
    assert payload["manual_retry_options"]["default_mode"] == "resume_failed_node"


def test_dashboard_job_detail_runtime_raises_when_job_missing(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, None, [])

    with pytest.raises(HTTPException) as exc_info:
        runtime.get_job_detail_payload("missing-job")

    assert exc_info.value.status_code == 404
    assert "Job not found" in str(exc_info.value.detail)
