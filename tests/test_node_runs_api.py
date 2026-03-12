"""Tests for persisted workflow node run API exposure."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, RuntimeInputRecord, utc_now_iso
from app.workflow_design import default_workflow_template
from app.workflow_resume import build_workflow_artifact_paths


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
                        "nodes": [{"id": "n1", "type": "gh_read_issue", "title": "기본 이슈 읽기"}],
                        "edges": [],
                    },
                    {
                        "workflow_id": "wf-app",
                        "name": "App Workflow",
                        "version": 2,
                        "entry_node_id": "n1",
                        "nodes": [
                            {"id": "n1", "type": "gh_read_issue", "title": "앱 이슈 읽기"},
                            {"id": "n2", "type": "write_spec", "title": "앱 SPEC"},
                            {"id": "n3", "type": "codex_implement", "title": "앱 구현"},
                        ],
                        "edges": [
                            {"from": "n1", "to": "n2", "on": "success"},
                            {"from": "n2", "to": "n3", "on": "success"},
                        ],
                    },
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
                    "source_repository": "",
                },
                {
                    "code": "web",
                    "name": "Web",
                    "repository": "owner/repo",
                    "workflow_id": "wf-app",
                    "source_repository": "",
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
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


def test_job_detail_api_includes_resolved_workflow_runtime_from_app_mapping(
    app_components,
    monkeypatch,
    tmp_path: Path,
):
    _, store, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflow_catalog(workflows_path)
    _write_apps(apps_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)
    monkeypatch.setattr(dashboard, "_APPS_CONFIG_PATH", apps_path)

    job = _make_job("job-detail-workflow-runtime")
    job.app_code = "web"
    job.workflow_id = ""
    store.create_job(job)

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["workflow_runtime"]
    assert runtime["requested_workflow_id"] == ""
    assert runtime["resolved_workflow_id"] == "wf-app"
    assert runtime["resolution_source"] == "app"
    assert runtime["definition_valid"] is True
    assert [item["id"] for item in runtime["nodes"]] == ["n1", "n2", "n3"]


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


def test_job_detail_api_extracts_workflow_fallback_events(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-workflow-fallback")
    store.create_job(job)

    debug_log_path = settings.logs_dir / "debug" / job.log_file
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text(
        "\n".join(
            [
                "[2026-03-10T00:00:00+00:00] Workflow resolution warning: Requested workflow_id not found: wf-missing",
                "[2026-03-10T00:00:01+00:00] Workflow validation failed; fallback to fixed pipeline: entry_node_id is required",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    runtime = payload["workflow_runtime"]
    assert runtime["uses_fixed_pipeline"] is True
    assert [item["kind"] for item in runtime["fallback_events"]] == [
        "resolution_warning",
        "validation_failure",
    ]
    assert runtime["fallback_events"][0]["message"] == "Requested workflow_id not found: wf-missing"
    assert runtime["fallback_events"][1]["message"] == "entry_node_id is required"


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
    (docs_dir / "STRATEGY_SHADOW_REPORT.json").write_text(
        '{\n  "shadow_strategy": "test_hardening",\n  "diverged": true,\n  "decision_mode": "memory_divergence",\n  "confidence": 0.84\n}\n',
        encoding="utf-8",
    )
    (docs_dir / "MEMORY_TRACE.json").write_text(
        (
            '{\n'
            '  "generated_at": "2026-03-10T02:00:00+00:00",\n'
            '  "enabled": true,\n'
            '  "source": "db",\n'
            '  "fallback_used": false,\n'
            '  "selected_total": 4,\n'
            '  "routes": {\n'
            '    "planner": {"selected_count": 2},\n'
            '    "reviewer": {"selected_count": 1},\n'
            '    "coder": {"selected_count": 1}\n'
            '  }\n'
            '}\n'
        ),
        encoding="utf-8",
    )
    build_workflow_artifact_paths(docs_dir.parent)["runtime_recovery_trace"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T02:05:00+00:00",
                "latest_event_at": "2026-03-10T02:05:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-10T02:05:00+00:00",
                        "source": "worker_stale_recovery",
                        "job_id": job.job_id,
                        "attempt": job.attempt,
                        "stage": job.stage,
                        "gate_label": "",
                        "reason_code": "stale_heartbeat",
                        "reason": "running heartbeat stale detected after 1803s",
                        "decision": "requeue",
                        "failure_class": "stale_heartbeat",
                        "recovery_status": "auto_recovered",
                        "recovery_count": 1,
                        "details": {"stale_seconds": 1803},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
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
    assert payload["runtime_signals"]["shadow_strategy"] == "test_hardening"
    assert payload["runtime_signals"]["shadow_diverged"] is True
    assert payload["runtime_signals"]["shadow_decision_mode"] == "memory_divergence"
    assert payload["runtime_signals"]["retrieval_enabled"] is True
    assert payload["runtime_signals"]["retrieval_source"] == "db"
    assert payload["runtime_signals"]["retrieval_fallback_used"] is False
    assert payload["runtime_signals"]["retrieval_selected_total"] == 4
    assert payload["runtime_signals"]["retrieval_route_counts"]["planner"] == 2
    assert payload["memory_trace"]["source"] == "db"
    assert payload["memory_trace"]["routes"]["reviewer"]["selected_count"] == 1
    assert payload["runtime_recovery_trace"]["event_count"] == 1
    assert payload["runtime_recovery_trace"]["latest_failure_class"] == "stale_heartbeat"
    assert payload["runtime_recovery_trace"]["latest_provider_hint"] == "runtime"
    assert payload["runtime_recovery_trace"]["latest_stage_family"] == "runtime_recovery"
    assert payload["runtime_recovery_trace"]["events"][0]["reason_code"] == "stale_heartbeat"
    assert payload["runtime_recovery_trace"]["events"][0]["decision"] == "requeue"
    assert payload["failure_classification"]["failure_class"] == "stale_heartbeat"
    assert payload["failure_classification"]["source"] == "runtime_recovery_trace"
    assert payload["failure_classification"]["provider_hint"] == "runtime"
    assert payload["failure_classification"]["stage_family"] == "runtime_recovery"


def test_job_detail_page_renders_failure_visibility_shell(app_components):
    _, store, app = app_components
    client = TestClient(app)
    job = _make_job("job-detail-failure-shell")
    store.create_job(job)

    response = client.get(f"/jobs/{job.job_id}")

    assert response.status_code == 200
    assert "실패 분류" in response.text
    assert "실패 공급자" in response.text
    assert "실패 단계군" in response.text


def test_job_detail_api_includes_needs_human_summary(app_components):
    _, store, app = app_components
    client = TestClient(app)
    job = _make_job("job-detail-needs-human")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    job.error_message = "402 You have no quota remaining"
    job.recovery_status = "needs_human"
    job.recovery_reason = "provider_quota -> needs_human_candidate"
    store.create_job(job)

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_human_summary"]["active"] is True
    assert payload["needs_human_summary"]["failure_class"] == "provider_quota"
    assert payload["needs_human_summary"]["manual_resume_recommended"] is True


def test_job_detail_api_includes_provider_quarantine_summary(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-provider-quarantine")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.IMPLEMENT_WITH_CODEX.value
    job.error_message = "codex provider quarantined after repeated timeout failures"
    job.recovery_status = "provider_quarantined"
    job.recovery_reason = "codex provider quarantined after 4/4 provider_timeout failure(s)"
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T00:00:00+00:00",
                "latest_event_at": "2026-03-12T00:00:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-12T00:00:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 2,
                        "stage": "implement_with_codex",
                        "gate_label": "",
                        "reason_code": "provider_timeout",
                        "reason": "codex provider quarantined after 4/4 provider_timeout failure(s)",
                        "decision": "provider_quarantined",
                        "recovery_status": "provider_quarantined",
                        "recovery_count": 0,
                        "details": {
                            "retry_policy": {
                                "failure_class": "provider_timeout",
                                "retry_budget": 2,
                                "recovery_path": "provider_quarantine",
                                "cooldown_seconds": 120,
                                "needs_human_recommended": False,
                            }
                        },
                        "failure_class": "provider_timeout",
                        "provider_hint": "codex",
                        "stage_family": "implementation",
                        "needs_human_summary": {
                            "active": True,
                            "title": "공급자 문제로 운영자 확인 필요",
                            "summary": "현재 실패는 공급자 timeout 반복으로 격리됐습니다.",
                            "failure_class": "provider_timeout",
                            "provider_hint": "codex",
                            "stage_family": "implementation",
                            "reason_code": "provider_timeout",
                            "reason": "codex provider quarantined after 4/4 provider_timeout failure(s)",
                            "recovery_path": "provider_quarantine",
                            "source": "job_failure_runtime",
                            "generated_at": "2026-03-12T00:00:00+00:00",
                            "recommended_actions": ["공급자 route를 바꾸거나 운영자가 확인합니다."],
                            "manual_resume_recommended": True,
                            "cooldown_seconds": 120,
                            "effective_retry_budget": 2,
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_human_summary"]["active"] is True
    assert payload["needs_human_summary"]["recovery_path"] == "provider_quarantine"
    assert payload["failure_classification"]["provider_hint"] == "codex"


def test_job_detail_api_includes_provider_circuit_open_summary(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-provider-circuit-open")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.PLAN_WITH_GEMINI.value
    job.error_message = "gemini provider circuit open after repeated timeout failures"
    job.recovery_status = "provider_circuit_open"
    job.recovery_reason = "gemini provider circuit open after 6/6 provider_timeout failure(s)"
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-13T00:00:00+00:00",
                "latest_event_at": "2026-03-13T00:00:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-13T00:00:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 2,
                        "stage": "plan_with_gemini",
                        "gate_label": "",
                        "reason_code": "provider_timeout",
                        "reason": "gemini provider circuit open after 6/6 provider_timeout failure(s)",
                        "decision": "provider_circuit_open",
                        "recovery_status": "provider_circuit_open",
                        "recovery_count": 0,
                        "details": {
                            "retry_policy": {
                                "failure_class": "provider_timeout",
                                "retry_budget": 2,
                                "recovery_path": "provider_circuit_breaker",
                                "cooldown_seconds": 120,
                                "needs_human_recommended": False,
                            }
                        },
                        "failure_class": "provider_timeout",
                        "provider_hint": "gemini",
                        "stage_family": "planning",
                        "needs_human_summary": {
                            "active": True,
                            "title": "공급자 장애 또는 지연 확인 필요",
                            "summary": "현재 실패는 공급자 응답 지연 또는 장애 징후로 분류되었습니다. 같은 공급자에 계속 재시도하기보다 route 전환 또는 잠시 격리가 우선입니다.",
                            "failure_class": "provider_timeout",
                            "provider_hint": "gemini",
                            "stage_family": "planning",
                            "reason_code": "provider_timeout",
                            "reason": "gemini provider circuit open after 6/6 provider_timeout failure(s)",
                            "recovery_path": "provider_circuit_breaker",
                            "source": "job_failure_runtime",
                            "generated_at": "2026-03-13T00:00:00+00:00",
                            "recommended_actions": ["fallback route 또는 대체 공급자로 전환합니다."],
                            "manual_resume_recommended": True,
                            "cooldown_seconds": 120,
                            "effective_retry_budget": 2,
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["needs_human_summary"]["active"] is True
    assert payload["needs_human_summary"]["recovery_path"] == "provider_circuit_breaker"
    assert payload["failure_classification"]["provider_hint"] == "gemini"


def test_job_detail_api_includes_dead_letter_summary(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-dead-letter")
    job.status = JobStatus.FAILED.value
    job.stage = JobStage.FAILED.value
    job.error_message = "snapshot mismatch"
    job.recovery_status = "dead_letter"
    job.recovery_reason = "dead-letter after retry budget exhausted: snapshot mismatch"
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T00:00:00+00:00",
                "latest_event_at": "2026-03-12T00:00:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-12T00:00:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 3,
                        "stage": "failed",
                        "gate_label": "",
                        "reason_code": "dead_letter",
                        "reason": "dead-letter after retry budget exhausted: snapshot mismatch",
                        "decision": "dead_letter",
                        "recovery_status": "dead_letter",
                        "recovery_count": 0,
                        "details": {"upstream_recovery_status": ""},
                        "failure_class": "test_failure",
                        "provider_hint": "unknown",
                        "stage_family": "test",
                        "dead_letter_summary": {
                            "active": True,
                            "title": "테스트 반복 실패로 작업이 격리됨",
                            "summary": "dead-letter after retry budget exhausted: snapshot mismatch",
                            "failure_class": "test_failure",
                            "provider_hint": "unknown",
                            "stage_family": "test",
                            "reason_code": "dead_letter",
                            "source": "job_failure_runtime",
                            "generated_at": "2026-03-12T00:00:00+00:00",
                            "upstream_recovery_status": "",
                            "manual_resume_recommended": True,
                            "retry_from_scratch_recommended": True,
                            "recommended_actions": ["원인 로그와 STATUS.md를 확인한 뒤 재실행 여부를 결정합니다."],
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dead_letter_summary"]["active"] is True
    assert payload["dead_letter_summary"]["failure_class"] == "test_failure"
    assert payload["dead_letter_summary"]["retry_from_scratch_recommended"] is True


def test_job_detail_api_hides_dead_letter_summary_after_requeue(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-dead-letter-hidden")
    job.status = JobStatus.QUEUED.value
    job.stage = JobStage.QUEUED.value
    job.recovery_status = "dead_letter_requeued"
    job.recovery_reason = "운영자가 dead-letter 작업을 다시 큐에 넣었습니다."
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T00:00:00+00:00",
                "latest_event_at": "2026-03-12T00:01:00+00:00",
                "event_count": 2,
                "events": [
                    {
                        "generated_at": "2026-03-12T00:00:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 3,
                        "stage": "failed",
                        "gate_label": "",
                        "reason_code": "dead_letter",
                        "reason": "dead-letter after retry budget exhausted: snapshot mismatch",
                        "decision": "dead_letter",
                        "recovery_status": "dead_letter",
                        "recovery_count": 0,
                        "details": {"upstream_recovery_status": ""},
                        "failure_class": "test_failure",
                        "provider_hint": "unknown",
                        "stage_family": "test",
                        "dead_letter_summary": {
                            "active": True,
                            "title": "테스트 반복 실패로 작업이 격리됨",
                            "summary": "dead-letter after retry budget exhausted: snapshot mismatch",
                            "failure_class": "test_failure",
                            "provider_hint": "unknown",
                            "stage_family": "test",
                            "reason_code": "dead_letter",
                            "source": "job_failure_runtime",
                            "generated_at": "2026-03-12T00:00:00+00:00",
                            "upstream_recovery_status": "",
                            "manual_resume_recommended": True,
                            "retry_from_scratch_recommended": True,
                            "recommended_actions": ["원인 로그와 STATUS.md를 확인한 뒤 재실행 여부를 결정합니다."],
                        },
                    },
                    {
                        "generated_at": "2026-03-12T00:01:00+00:00",
                        "source": "dashboard_dead_letter_retry",
                        "job_id": job.job_id,
                        "attempt": 0,
                        "stage": "queued",
                        "gate_label": "",
                        "reason_code": "dead_letter_retry",
                        "reason": "운영자가 dead-letter 작업을 다시 큐에 넣었습니다.",
                        "decision": "retry_from_dead_letter",
                        "recovery_status": "dead_letter_requeued",
                        "recovery_count": 0,
                        "details": {
                            "previous_recovery_status": "dead_letter",
                            "retry_from_scratch": True,
                        },
                        "failure_class": "unknown_runtime",
                        "provider_hint": "unknown",
                        "stage_family": "unknown",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dead_letter_summary"] == {}
    assert len(payload["dead_letter_action_trail"]) == 2
    assert payload["dead_letter_action_trail"][0]["decision"] == "retry_from_dead_letter"
    assert payload["dead_letter_action_trail"][0]["previous_recovery_status"] == "dead_letter"


def test_job_detail_api_includes_requeue_reason_summary(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-requeue-summary")
    job.status = JobStatus.QUEUED.value
    job.stage = JobStage.QUEUED.value
    job.recovery_status = "dead_letter_requeued"
    job.recovery_reason = "운영자가 dead-letter 작업을 다시 큐에 넣었습니다."
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T00:00:00+00:00",
                "latest_event_at": "2026-03-12T00:01:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-12T00:01:00+00:00",
                        "source": "dashboard_dead_letter_retry",
                        "job_id": job.job_id,
                        "attempt": 0,
                        "stage": "queued",
                        "gate_label": "",
                        "reason_code": "dead_letter_retry",
                        "reason": "운영자가 dead-letter 작업을 다시 큐에 넣었습니다.",
                        "decision": "retry_from_dead_letter",
                        "recovery_status": "dead_letter_requeued",
                        "recovery_count": 0,
                        "details": {
                            "previous_recovery_status": "dead_letter",
                            "previous_reason": "dead-letter after retry budget exhausted",
                            "operator_note": "API key를 넣었으니 다시 시도",
                            "retry_from_scratch": True,
                        },
                        "failure_class": "unknown_runtime",
                        "provider_hint": "unknown",
                        "stage_family": "unknown",
                        "requeue_reason_summary": {
                            "active": True,
                            "title": "운영자 판단으로 dead-letter 작업을 다시 큐에 넣음",
                            "summary": "운영자가 dead-letter 작업을 다시 큐에 넣었습니다.",
                            "source": "dashboard_dead_letter_retry",
                            "reason_code": "dead_letter_retry",
                            "decision": "retry_from_dead_letter",
                            "recovery_status": "dead_letter_requeued",
                            "generated_at": "2026-03-12T00:01:00+00:00",
                            "trigger": "operator_dead_letter_retry",
                            "retry_from_scratch": True,
                            "operator_note": "API key를 넣었으니 다시 시도",
                            "previous_recovery_status": "dead_letter",
                            "previous_reason": "dead-letter after retry budget exhausted",
                            "target_node_id": "",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requeue_reason_summary"]["active"] is True
    assert payload["requeue_reason_summary"]["source"] == "dashboard_dead_letter_retry"
    assert payload["requeue_reason_summary"]["trigger"] == "operator_dead_letter_retry"
    assert payload["requeue_reason_summary"]["previous_recovery_status"] == "dead_letter"


def test_job_detail_api_includes_dead_letter_action_trail_with_operator_note(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-dead-letter-trail")
    job.status = JobStatus.QUEUED.value
    job.stage = JobStage.QUEUED.value
    job.recovery_status = "dead_letter_requeued"
    job.recovery_reason = "운영자가 다시 큐에 넣었습니다."
    store.create_job(job)

    workspace_path = dashboard._job_workspace_path(job, settings)
    trace_path = build_workflow_artifact_paths(workspace_path)["runtime_recovery_trace"]
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T00:00:00+00:00",
                "latest_event_at": "2026-03-12T00:01:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-12T00:01:00+00:00",
                        "source": "dashboard_dead_letter_retry",
                        "job_id": job.job_id,
                        "attempt": 0,
                        "stage": "queued",
                        "gate_label": "",
                        "reason_code": "dead_letter_retry",
                        "reason": "운영자가 다시 큐에 넣었습니다.",
                        "decision": "retry_from_dead_letter",
                        "recovery_status": "dead_letter_requeued",
                        "recovery_count": 0,
                        "details": {
                            "previous_recovery_status": "dead_letter",
                            "previous_reason": "dead-letter after retry budget exhausted",
                            "operator_note": "API key를 넣었으니 다시 시도",
                            "retry_from_scratch": True,
                        },
                        "failure_class": "unknown_runtime",
                        "provider_hint": "unknown",
                        "stage_family": "unknown",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dead_letter_summary"] == {}
    assert len(payload["dead_letter_action_trail"]) == 1
    assert payload["dead_letter_action_trail"][0]["operator_note"] == "API key를 넣었으니 다시 시도"
    assert payload["dead_letter_action_trail"][0]["previous_reason"] == "dead-letter after retry budget exhausted"


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


def test_job_detail_api_includes_job_lineage_and_log_summary(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    parent_job = _make_job("job-lineage-parent")
    parent_job.workflow_id = "wf-default"
    parent_job.backlog_candidate_id = "next_improvement_task:job-lineage-parent:next_1"
    store.create_job(parent_job)

    child_job = _make_job("job-lineage-child")
    child_job.job_kind = "followup_backlog"
    child_job.parent_job_id = parent_job.job_id
    child_job.backlog_candidate_id = parent_job.backlog_candidate_id
    child_job.issue_title = "[Follow-up] regression hardening"
    store.create_job(child_job)

    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": parent_job.backlog_candidate_id,
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "회귀 테스트 보강",
            "summary": "반복 실패 케이스를 follow-up으로 고정",
            "priority": "P1",
            "state": "queued",
            "payload": {
                "source_kind": "next_improvement_task",
                "queued_job_id": child_job.job_id,
                "recommended_node_type": "coder_fix_from_test_report",
            },
            "created_at": "2026-03-12T02:00:00+00:00",
            "updated_at": "2026-03-12T02:05:00+00:00",
        }
    )

    docs_dir = settings.repository_workspace_path(parent_job.repository, parent_job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "FOLLOWUP_BACKLOG_TASK.json").write_text(
        json.dumps(
            {
                "candidate_id": parent_job.backlog_candidate_id,
                "queued_job_id": child_job.job_id,
                "source_job_id": parent_job.job_id,
                "recommended_node_type": "coder_fix_from_test_report",
                "job_contract": {"kind": "followup_backlog", "version": "v1"},
                "generated_at": "2026-03-12T02:06:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(docs_dir.parent)["assistant_diagnosis_trace"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-12T02:07:00+00:00",
                "enabled": True,
                "job_id": parent_job.job_id,
                "assistant_scope": "log_analysis",
                "question": "최근 실패 원인 분석",
                "combined_context_length": 248,
                "tool_runs": [
                    {
                        "tool": "log_lookup",
                        "query": "heartbeat stale detected",
                        "ok": True,
                        "mode": "internal",
                        "context_path": str(docs_dir / "ASSISTANT_LOG_LOOKUP_CONTEXT.md"),
                        "result_path": str(docs_dir / "ASSISTANT_LOG_LOOKUP_RESULT.json"),
                        "error": "",
                    },
                    {
                        "tool": "repo_search",
                        "query": "implement codex exec implement",
                        "ok": False,
                        "mode": "error",
                        "context_path": "",
                        "result_path": "",
                        "error": "repo path missing",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    debug_log_path = settings.logs_dir / "debug" / parent_job.log_file
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text(
        "\n".join(
            [
                "[2026-03-12T02:10:00+00:00] [RUN] codex exec implement",
                "[2026-03-12T02:10:02+00:00] [STDERR] regression test failed",
                "[2026-03-12T02:10:04+00:00] [DONE] exit_code=1 elapsed=2.40s",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    user_log_path = settings.logs_dir / "user" / parent_job.log_file
    user_log_path.parent.mkdir(parents=True, exist_ok=True)
    user_log_path.write_text("[2026-03-12T02:10:00+00:00] user-visible line\n", encoding="utf-8")

    response = client.get(f"/api/jobs/{parent_job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    lineage = payload["job_lineage"]
    assert lineage["job_kind"] == "issue"
    assert lineage["backlog_candidate"]["candidate_id"] == parent_job.backlog_candidate_id
    assert lineage["child_count"] == 1
    assert lineage["child_jobs"][0]["job_id"] == child_job.job_id
    assert lineage["followup_artifact"]["queued_job_id"] == child_job.job_id
    assert lineage["followup_artifact"]["recommended_node_type"] == "coder_fix_from_test_report"

    log_summary = payload["log_summary"]
    assert log_summary["event_count"] == 3
    assert log_summary["error_count"] == 2
    assert log_summary["nonzero_done_count"] == 1
    assert log_summary["latest_command"]["message"] == "codex exec implement"
    assert log_summary["latest_error"]["message"] == "[DONE] exit_code=1 elapsed=2.40s"
    assert log_summary["channels"]["debug"]["exists"] is True
    assert log_summary["channels"]["user"]["exists"] is True

    diagnosis_trace = payload["assistant_diagnosis_trace"]
    assert diagnosis_trace["enabled"] is True
    assert diagnosis_trace["assistant_scope"] == "log_analysis"
    assert diagnosis_trace["question"] == "최근 실패 원인 분석"
    assert diagnosis_trace["combined_context_length"] == 248
    assert diagnosis_trace["trace_path"].endswith("ASSISTANT_DIAGNOSIS_TRACE.json")
    assert len(diagnosis_trace["tool_runs"]) == 2
    assert diagnosis_trace["tool_runs"][0]["tool"] == "log_lookup"
    assert diagnosis_trace["tool_runs"][1]["error"] == "repo path missing"


def test_job_detail_api_includes_operator_inputs(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job("job-detail-operator-inputs")
    job.app_code = "maps"
    store.create_job(job)
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-ready",
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            scope="job",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 구현에 필요",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="secret-value-123",
            placeholder="",
            note="provided",
            requested_by="operator",
            requested_at="2026-03-12T03:00:00+00:00",
            provided_at="2026-03-12T03:01:00+00:00",
            updated_at="2026-03-12T03:01:00+00:00",
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-pending",
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            scope="job",
            key="google_places_dataset",
            label="Google Places Dataset",
            description="장소 autocomplete 고도화용",
            value_type="text",
            env_var_name="GOOGLE_PLACES_DATASET",
            sensitive=False,
            status="requested",
            value="",
            placeholder="dataset id",
            note="",
            requested_by="operator",
            requested_at="2026-03-12T03:02:00+00:00",
            provided_at=None,
            updated_at="2026-03-12T03:02:00+00:00",
        )
    )

    response = client.get(f"/api/jobs/{job.job_id}")

    assert response.status_code == 200
    payload = response.json()
    operator_inputs = payload["operator_inputs"]
    assert operator_inputs["available_count"] == 1
    assert operator_inputs["pending_count"] == 1
    assert operator_inputs["available_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert operator_inputs["artifact_path"].endswith("OPERATOR_INPUTS.json")
    assert operator_inputs["resolved_inputs"][0]["env_var_name"] == "GOOGLE_MAPS_API_KEY"
    assert operator_inputs["resolved_inputs"][0]["value"] == ""
    assert operator_inputs["resolved_inputs"][0]["display_value"] != ""
    assert operator_inputs["pending_inputs"][0]["env_var_name"] == "GOOGLE_PLACES_DATASET"
