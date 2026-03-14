from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.dashboard_memory_admin_runtime import DashboardMemoryAdminRuntime
from app.memory.runtime_store import MemoryRuntimeStore


def _build_runtime(app_components, queue_followup=None) -> tuple[DashboardMemoryAdminRuntime, MemoryRuntimeStore, Path]:
    settings, store, _ = app_components
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    artifact_path = settings.repository_workspace_path("owner/repo", "default") / "_docs" / "FOLLOWUP_BACKLOG_TASK.json"
    runtime = DashboardMemoryAdminRuntime(
        store=store,
        settings=settings,
        get_memory_runtime_store=lambda _settings: runtime_store,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        queue_followup_job_from_backlog_candidate=queue_followup
        or (
            lambda **kwargs: (
                type("QueuedJob", (), {"job_id": "queued-job-1"})(),
                kwargs["runtime_store"].set_backlog_candidate_state(
                    kwargs["candidate"]["candidate_id"],
                    state="queued",
                    payload_updates={"queued_job_id": "queued-job-1"},
                )
                and artifact_path,
            )
        ),
    )
    return runtime, runtime_store, artifact_path


def test_dashboard_memory_admin_runtime_search_entries_returns_filtered_payload(app_components) -> None:
    runtime, runtime_store, _ = _build_runtime(app_components)
    runtime_store.upsert_entry(
        {
            "memory_id": "conv_pytest_file_pattern",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "pytest file naming",
            "summary": "tests use pytest naming",
            "state": "promoted",
            "score": 2.0,
            "confidence": 0.9,
            "updated_at": "2026-03-13T00:00:00+00:00",
        }
    )
    runtime_store.upsert_feedback(
        {
            "feedback_id": "fb-search-1",
            "memory_id": "conv_pytest_file_pattern",
            "job_id": "job-memory-search",
            "generated_at": "2026-03-13T00:02:00+00:00",
            "verdict": "promote",
            "score_delta": 1.2,
            "routes": ["planner"],
        }
    )
    runtime_store.refresh_rankings(as_of="2026-03-13T00:10:00+00:00")

    payload = runtime.search_entries(
        q="pytest",
        state="promoted",
        memory_type="convention",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
        limit=12,
    )

    assert payload["count"] == 1
    assert payload["items"][0]["memory_id"] == "conv_pytest_file_pattern"
    assert payload["filters"]["state"] == "promoted"


def test_dashboard_memory_admin_runtime_gets_detail_with_evidence_and_feedback(app_components) -> None:
    runtime, runtime_store, _ = _build_runtime(app_components)
    runtime_store.upsert_entry(
        {
            "memory_id": "conv_pytest_file_pattern",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "pytest file naming",
            "summary": "tests use pytest naming",
            "state": "promoted",
            "score": 2.0,
            "confidence": 0.9,
            "updated_at": "2026-03-13T00:00:00+00:00",
        }
    )
    runtime_store.replace_evidence(
        "conv_pytest_file_pattern",
        [
            {
                "evidence_id": "ev-1",
                "evidence_type": "source_path",
                "source_path": "tests/test_jobs_dashboard_api.py",
                "content": "dashboard api tests use pytest naming",
                "created_at": "2026-03-13T00:01:00+00:00",
            }
        ],
    )
    runtime_store.upsert_feedback(
        {
            "feedback_id": "fb-1",
            "memory_id": "conv_pytest_file_pattern",
            "job_id": "job-memory-ui",
            "generated_at": "2026-03-13T00:02:00+00:00",
            "verdict": "promote",
            "score_delta": 1.2,
            "routes": ["planner"],
        }
    )
    runtime_store.refresh_rankings(as_of="2026-03-13T00:10:00+00:00")

    payload = runtime.get_memory_detail(memory_id="conv_pytest_file_pattern")

    assert payload["entry"]["memory_id"] == "conv_pytest_file_pattern"
    assert payload["evidence"][0]["source_path"] == "tests/test_jobs_dashboard_api.py"
    assert payload["feedback"][0]["verdict"] == "promote"


def test_dashboard_memory_admin_runtime_override_updates_entry_state(app_components) -> None:
    runtime, runtime_store, _ = _build_runtime(app_components)
    runtime_store.upsert_entry(
        {
            "memory_id": "conv_pytest_file_pattern",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "pytest file naming",
            "summary": "tests use pytest naming",
            "state": "promoted",
            "score": 2.0,
            "confidence": 0.9,
            "updated_at": "2026-03-13T00:00:00+00:00",
        }
    )

    payload = runtime.override_memory(
        memory_id="conv_pytest_file_pattern",
        state="banned",
        note="manual regression check",
    )

    assert payload["saved"] is True
    assert payload["entry"]["state"] == "banned"
    assert payload["entry"]["manual_state_override"] == "banned"
    assert payload["detail"]["entry"]["state_reason"] == "manual override: manual regression check"


def test_dashboard_memory_admin_runtime_lists_backlog_candidates(app_components) -> None:
    runtime, runtime_store, _ = _build_runtime(app_components)
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": "strategy_shadow:job-backlog:feature_expansion",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "전략 재검토: feature_expansion",
            "summary": "현재 전략과 shadow 전략이 갈라짐",
            "priority": "P1",
            "state": "candidate",
            "payload": {"source_kind": "strategy_shadow", "job_id": "job-backlog"},
            "created_at": "2026-03-13T01:00:00+00:00",
            "updated_at": "2026-03-13T01:00:00+00:00",
        }
    )

    payload = runtime.list_backlog_candidates(
        q="shadow",
        state="candidate",
        priority="P1",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
        limit=12,
    )

    assert payload["count"] == 1
    assert payload["items"][0]["candidate_id"] == "strategy_shadow:job-backlog:feature_expansion"
    assert payload["filters"]["priority"] == "P1"


def test_dashboard_memory_admin_runtime_queues_backlog_candidate(app_components) -> None:
    runtime, runtime_store, artifact_path = _build_runtime(app_components)
    candidate_id = "next_improvement_task:job-backlog-source:next_1"
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": candidate_id,
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "회귀 테스트 보강",
            "summary": "실패 재현 케이스를 고정한다",
            "priority": "P1",
            "state": "candidate",
            "payload": {"source_kind": "next_improvement_task", "job_id": "job-backlog-source"},
            "created_at": "2026-03-13T01:11:00+00:00",
            "updated_at": "2026-03-13T01:11:00+00:00",
        }
    )

    payload = runtime.apply_backlog_action(candidate_id=candidate_id, action="queue", note="run next loop")

    assert payload["ok"] is True
    assert payload["queued_job_id"] == "queued-job-1"
    assert payload["artifact_path"] == str(artifact_path)
    assert payload["candidate"]["state"] == "queued"


def test_dashboard_memory_admin_runtime_rejects_invalid_override_state(app_components) -> None:
    runtime, _, _ = _build_runtime(app_components)

    with pytest.raises(HTTPException) as exc_info:
        runtime.override_memory(memory_id="missing", state="invalid", note="")

    assert exc_info.value.status_code == 400
    assert "지원하지 않는 memory override state" in str(exc_info.value.detail)
