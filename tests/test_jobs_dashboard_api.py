"""Tests for dashboard job filtering and pagination."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord
from app.workflow_resume import build_workflow_artifact_paths


def _make_job(
    job_id: str,
    *,
    issue_number: int,
    issue_title: str,
    status: str,
    stage: str,
    app_code: str,
    track: str,
    created_at: str,
    updated_at: str,
    error_message: str | None = None,
    workflow_id: str | None = None,
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=f"https://github.com/owner/repo/issues/{issue_number}",
        status=status,
        stage=stage,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/{app_code}/issue-{issue_number}",
        pr_url=None,
        error_message=error_message,
        log_file=f"{app_code}--{job_id}.log",
        created_at=created_at,
        updated_at=updated_at,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
        workflow_id=workflow_id,
    )


def test_jobs_api_supports_pagination_and_latest_updated_order(app_components):
    _, store, app = app_components
    client = TestClient(app)

    store.create_job(
        _make_job(
            "job-a",
            issue_number=101,
            issue_title="Old queued job",
            status="queued",
            stage="queued",
            app_code="default",
            track="enhance",
            created_at="2026-03-08T00:00:00+00:00",
            updated_at="2026-03-08T00:05:00+00:00",
        )
    )
    store.create_job(
        _make_job(
            "job-b",
            issue_number=102,
            issue_title="Running dashboard work",
            status="running",
            stage="implement_with_codex",
            app_code="web",
            track="enhance",
            created_at="2026-03-08T00:10:00+00:00",
            updated_at="2026-03-08T00:20:00+00:00",
        )
    )
    store.create_job(
        _make_job(
            "job-c",
            issue_number=103,
            issue_title="Failed login flow",
            status="failed",
            stage="product_review",
            app_code="admin",
            track="bug",
            created_at="2026-03-08T00:15:00+00:00",
            updated_at="2026-03-08T00:30:00+00:00",
            error_message="Traceback: login handler failed",
        )
    )

    response = client.get("/api/jobs?page=1&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert [item["job_id"] for item in payload["jobs"]] == ["job-c", "job-b"]
    assert payload["summary"] == {
        "total": 3,
        "queued": 1,
        "running": 1,
        "done": 0,
        "failed": 1,
    }
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total_items": 3,
        "total_pages": 2,
        "has_prev": False,
        "has_next": True,
        "start_index": 1,
        "end_index": 2,
    }


def test_jobs_api_filters_by_status_stage_app_track_and_query(app_components):
    _, store, app = app_components
    client = TestClient(app)

    store.create_job(
        _make_job(
            "job-failed",
            issue_number=201,
            issue_title="Login page crash",
            status="failed",
            stage="product_review",
            app_code="admin",
            track="bug",
            created_at="2026-03-08T01:00:00+00:00",
            updated_at="2026-03-08T01:30:00+00:00",
            error_message="Traceback: empty state missing",
        )
    )
    store.create_job(
        _make_job(
            "job-running",
            issue_number=202,
            issue_title="Dashboard filter work",
            status="running",
            stage="implement_with_codex",
            app_code="web",
            track="enhance",
            created_at="2026-03-08T01:10:00+00:00",
            updated_at="2026-03-08T01:20:00+00:00",
        )
    )

    response = client.get(
        "/api/jobs",
        params={
            "status": "failed",
            "stage": "product_review",
            "app_code": "admin",
            "track": "bug",
            "q": "empty state",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["job_id"] for item in payload["jobs"]] == ["job-failed"]
    assert payload["filtered_summary"] == {
        "total": 1,
        "queued": 0,
        "running": 0,
        "done": 0,
        "failed": 1,
    }
    assert payload["filters"] == {
        "status": "failed",
        "track": "bug",
        "app_code": "admin",
        "stage": "product_review",
        "recovery_status": "",
        "strategy": "",
        "q": "empty state",
        "applied": True,
    }
    assert "product_review" in payload["filter_options"]["stages"]


def test_jobs_api_includes_normalized_failure_class(app_components):
    _, store, app = app_components
    client = TestClient(app)

    store.create_job(
        _make_job(
            "job-git-conflict",
            issue_number=203,
            issue_title="Push rejected after review",
            status="failed",
            stage="push_branch",
            app_code="admin",
            track="bug",
            created_at="2026-03-08T01:40:00+00:00",
            updated_at="2026-03-08T01:45:00+00:00",
            error_message="git push rejected: non-fast-forward update failed",
        )
    )

    response = client.get("/api/jobs", params={"q": "git_conflict"})

    assert response.status_code == 200
    payload = response.json()
    assert [item["job_id"] for item in payload["jobs"]] == ["job-git-conflict"]
    assert payload["jobs"][0]["failure_class"] == "git_conflict"
    assert payload["jobs"][0]["failure_classification"]["source"] == "job_record"
    assert payload["jobs"][0]["failure_provider_hint"] == "git"
    assert payload["jobs"][0]["failure_stage_family"] == "git_provider"


def test_job_options_api_returns_compact_combobox_items(app_components):
    _, store, app = app_components
    client = TestClient(app)

    store.create_job(
        _make_job(
            "job-select-1",
            issue_number=301,
            issue_title="Thumbnail selection flow broken on mobile viewport",
            status="failed",
            stage="ux_e2e_review",
            app_code="mvp-1",
            track="bug",
            created_at="2026-03-08T02:00:00+00:00",
            updated_at="2026-03-08T02:30:00+00:00",
        )
    )
    store.create_job(
        _make_job(
            "job-select-2",
            issue_number=302,
            issue_title="Another task",
            status="done",
            stage="done",
            app_code="mvp-2",
            track="enhance",
            created_at="2026-03-08T02:10:00+00:00",
            updated_at="2026-03-08T02:20:00+00:00",
        )
    )

    response = client.get("/api/jobs/options", params={"q": "thumbnail", "limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "thumbnail"
    assert payload["limit"] == 10
    assert len(payload["items"]) == 1
    assert payload["items"][0]["job_id"] == "job-select-1"
    assert payload["items"][0]["stage"] == "ux_e2e_review"
    assert payload["items"][0]["app_code"] == "mvp-1"


def test_jobs_api_supports_recovery_and_strategy_filters(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job(
        "job-runtime-signals",
        issue_number=401,
        issue_title="Recovery and strategy visibility",
        status="failed",
        stage="improvement_stage",
        app_code="default",
        track="enhance",
        created_at="2026-03-08T03:00:00+00:00",
        updated_at="2026-03-08T03:10:00+00:00",
    )
    job.recovery_status = "auto_recovered"
    store.create_job(job)

    docs_dir = settings.repository_workspace_path(job.repository, job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "PRODUCT_REVIEW.json").write_text(
        json.dumps(
            {
                "scores": {"overall": 2.8},
                "quality_gate": {
                    "passed": False,
                    "categories_below_threshold": ["architecture_structure"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "IMPROVEMENT_LOOP_STATE.json").write_text(
        json.dumps(
            {
                "strategy": "quality_hardening",
                "strategy_change_required": True,
                "next_scope_restriction": "P1_only",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "NEXT_IMPROVEMENT_TASKS.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "title": "에러 상태 보강",
                        "recommended_node_type": "codex_fix",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "REPO_MATURITY.json").write_text(
        json.dumps(
            {
                "level": "mvp",
                "score": 58,
                "progression": "up",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "QUALITY_TREND.json").write_text(
        json.dumps(
            {
                "trend_direction": "improving",
                "delta_from_previous": 0.35,
                "review_round_count": 4,
                "persistent_low_categories": ["test_coverage"],
                "stagnant_categories": ["test_coverage"],
                "category_deltas": {"test_coverage": 0},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "STRATEGY_SHADOW_REPORT.json").write_text(
        json.dumps(
            {
                "shadow_strategy": "test_hardening",
                "decision_mode": "memory_confirms_current",
                "diverged": False,
                "confidence": 0.71,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get(
        "/api/jobs",
        params={
            "recovery_status": "auto_recovered",
            "strategy": "quality_hardening",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["job_id"] for item in payload["jobs"]] == ["job-runtime-signals"]
    assert payload["jobs"][0]["runtime_signals"]["strategy"] == "quality_hardening"
    assert payload["jobs"][0]["runtime_signals"]["review_overall"] == 2.8
    assert payload["jobs"][0]["runtime_signals"]["maturity_level"] == "mvp"
    assert payload["jobs"][0]["runtime_signals"]["quality_trend_direction"] == "improving"
    assert payload["jobs"][0]["runtime_signals"]["persistent_low_categories"] == ["test_coverage"]
    assert payload["jobs"][0]["runtime_signals"]["category_deltas"]["test_coverage"] == 0
    assert payload["jobs"][0]["runtime_signals"]["shadow_strategy"] == "test_hardening"
    assert payload["jobs"][0]["runtime_signals"]["shadow_diverged"] is False
    assert payload["filters"]["recovery_status"] == "auto_recovered"
    assert payload["filters"]["strategy"] == "quality_hardening"
    assert "auto_recovered" in payload["filter_options"]["recovery_statuses"]
    assert "quality_hardening" in payload["filter_options"]["strategies"]


def test_dashboard_root_renders_shell_without_preloading_jobs(app_components):
    _, store, app = app_components
    client = TestClient(app)

    def fail_list_jobs():
        raise AssertionError("job list should not be loaded during initial shell render")

    store.list_jobs = fail_list_jobs  # type: ignore[assignment]

    response = client.get("/")

    assert response.status_code == 200
    assert "작업 목록을 불러오는 중..." in response.text
    assert "앱 목록 불러오는 중..." in response.text
    assert "Codex 위험 모드" in response.text
    assert "위험 플래그 제거" in response.text
    assert "현재 입력 기준 위험 플래그를 점검합니다." in response.text
    assert "상태 / 실패 분류" in response.text


def test_agent_models_api_reports_dangerous_codex_templates(app_components):
    settings, _, app = app_components
    settings.command_config.write_text(
        json.dumps(
            {
                "planner": "cat {prompt_file} | gemini --model gemini-3.1-pro-preview > {plan_path}",
                "coder": "cat {prompt_file} | codex exec - --dangerously-bypass-approvals-and-sandbox -C {work_dir} --color never",
                "reviewer": "cat {prompt_file} | gemini --model gemini-3.1-pro-preview > {review_path}",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    client = TestClient(app)

    response = client.get("/api/agents/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["codex"]["danger_mode"] is True
    assert payload["codex"]["danger_template_keys"] == ["coder"]
    assert payload["gemini"]["danger_mode"] is False


def test_admin_metrics_api_aggregates_system_quality_and_memory_signals(app_components, monkeypatch, tmp_path: Path):
    settings, store, app = app_components
    client = TestClient(app)

    apps_path = tmp_path / "config" / "apps.json"
    apps_path.parent.mkdir(parents=True, exist_ok=True)
    apps_path.write_text(
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
                    "code": "food",
                    "name": "Food",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                    "source_repository": "manbalboy/Food",
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    workflows_path = tmp_path / "config" / "workflows.json"
    workflows_path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [
                    {
                        "workflow_id": "wf-default",
                        "name": "Default Flow",
                        "version": 1,
                        "entry_node_id": "n1",
                        "nodes": [{"id": "n1", "type": "gh_read_issue"}],
                        "edges": [],
                    },
                    {
                        "workflow_id": "wf-review-loop",
                        "name": "Review Loop",
                        "version": 1,
                        "entry_node_id": "n1",
                        "nodes": [{"id": "n1", "type": "gh_read_issue"}],
                        "edges": [],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    roles_path = tmp_path / "config" / "roles.json"
    roles_path.write_text(
        json.dumps(
            {
                "roles": [
                    {"code": "planner", "name": "Planner", "cli": "gemini", "template_key": "planner", "enabled": True},
                    {"code": "coder", "name": "Coder", "cli": "codex", "template_key": "coder", "enabled": True},
                ],
                "presets": [
                    {"preset_id": "core", "name": "Core", "role_codes": ["planner", "coder"]},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.write_text(
        json.dumps(
            {
                "flags": {
                    "memory_logging": True,
                    "memory_retrieval": False,
                    "convention_extraction": True,
                    "memory_scoring": True,
                    "strategy_shadow": True,
                    "assistant_diagnosis_loop": False,
                    "vector_memory_shadow": False,
                    "vector_memory_retrieval": False,
                    "langgraph_planner_shadow": False,
                    "langgraph_recovery_shadow": False,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_APPS_CONFIG_PATH", apps_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)
    monkeypatch.setattr(dashboard, "_ROLES_CONFIG_PATH", roles_path)
    monkeypatch.setattr(dashboard, "_FEATURE_FLAGS_CONFIG_PATH", feature_flags_path)

    job = _make_job(
        "job-admin-metrics",
        issue_number=501,
        issue_title="Admin metrics quality visibility",
        status="failed",
        stage="improvement_stage",
        app_code="food",
        track="enhance",
        created_at="2026-03-08T05:00:00+00:00",
        updated_at="2026-03-08T05:10:00+00:00",
        workflow_id="adaptive_quality_loop_v1",
    )
    job.recovery_status = "dead_letter"
    job.recovery_reason = "dead-letter after retry budget exhausted: snapshot mismatch"
    store.create_job(job)
    default_job = _make_job(
        "job-admin-default",
        issue_number=502,
        issue_title="Default workflow baseline",
        status="done",
        stage="done",
        app_code="default",
        track="enhance",
        created_at="2026-03-07T05:10:00+00:00",
        updated_at="2026-03-07T05:40:00+00:00",
        workflow_id="wf-default",
    )
    store.create_job(default_job)

    docs_dir = settings.repository_workspace_path(job.repository, job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "PRODUCT_REVIEW.json").write_text(
        json.dumps(
            {
                "scores": {"overall": 3.6},
                "quality_gate": {"passed": True, "categories_below_threshold": ["test_coverage"]},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "REPO_MATURITY.json").write_text(
        json.dumps({"level": "usable", "score": 74, "progression": "up"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "QUALITY_TREND.json").write_text(
        json.dumps(
            {
                "trend_direction": "improving",
                "delta_from_previous": 0.4,
                "review_round_count": 3,
                "persistent_low_categories": ["test_coverage"],
                "stagnant_categories": [],
                "category_deltas": {"test_coverage": 1},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "IMPROVEMENT_LOOP_STATE.json").write_text(
        json.dumps(
            {
                "strategy": "test_hardening",
                "strategy_change_required": True,
                "next_scope_restriction": "P1_only",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "NEXT_IMPROVEMENT_TASKS.json").write_text(
        json.dumps(
            {"tasks": [{"title": "회귀 테스트 보강", "recommended_node_type": "codex_fix"}]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "MEMORY_LOG.jsonl").write_text(
        json.dumps({"memory_id": "episodic_job_summary:job-admin-metrics", "memory_type": "episodic"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "DECISION_HISTORY.json").write_text(
        json.dumps({"entries": [{"decision_id": "improvement_strategy:job-admin-metrics"}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "FAILURE_PATTERNS.json").write_text(
        json.dumps({"items": [{"pattern_id": "persistent_low:test_coverage"}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "CONVENTIONS.json").write_text(
        json.dumps({"rules": [{"id": "conv_nextjs"}, {"id": "conv_tailwindcss"}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "MEMORY_FEEDBACK.json").write_text(
        json.dumps({"entries": [{"feedback_id": "episodic_job_summary:job-admin-metrics:job-admin-metrics"}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "MEMORY_RANKINGS.json").write_text(
        json.dumps(
            {
                "items": [
                    {"memory_id": "episodic_job_summary:job-admin-metrics", "state": "promoted"},
                    {"memory_id": "persistent_low:test_coverage", "state": "decayed"},
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "STRATEGY_SHADOW_REPORT.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:20:00+00:00",
                "shadow_strategy": "feature_expansion",
                "decision_mode": "memory_divergence",
                "diverged": True,
                "confidence": 0.82,
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
                "generated_at": "2026-03-10T05:21:00+00:00",
                "enabled": True,
                "job_id": job.job_id,
                "assistant_scope": "log_analysis",
                "question": "최근 실패 원인 분석",
                "combined_context_length": 320,
                "tool_runs": [
                    {"tool": "log_lookup", "ok": True, "mode": "internal"},
                    {"tool": "repo_search", "ok": True, "mode": "internal"},
                    {"tool": "memory_search", "ok": False, "mode": "error"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(docs_dir.parent)["provider_failure_counters"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:21:00+00:00",
                "latest_updated_at": "2026-03-10T05:21:00+00:00",
                "providers": {
                    "codex": {
                        "provider_hint": "codex",
                        "total_failures": 3,
                        "recent_failure_count": 3,
                        "last_failure_class": "provider_quota",
                        "last_stage_family": "implementation",
                        "last_reason_code": "provider_quota",
                        "last_reason": "402 quota exceeded",
                        "last_job_id": job.job_id,
                        "last_attempt": 1,
                        "last_failed_at": "2026-03-10T05:21:00+00:00",
                        "recent_failures": [],
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(docs_dir.parent)["runtime_recovery_trace"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:26:00+00:00",
                "latest_event_at": "2026-03-10T05:26:00+00:00",
                "event_count": 3,
                "events": [
                    {
                        "generated_at": "2026-03-10T05:26:00+00:00",
                        "source": "dashboard_dead_letter_retry",
                        "job_id": job.job_id,
                        "attempt": 2,
                        "stage": "improvement_stage",
                        "reason_code": "manual_retry",
                        "reason": "operator approved retry from dead-letter",
                        "decision": "retry_from_dead_letter",
                        "recovery_status": "dead_letter_requeued",
                        "failure_class": "unknown_runtime",
                        "provider_hint": "runtime",
                        "stage_family": "runtime_recovery",
                        "details": {
                            "operator_note": "retry after fixture update",
                            "previous_recovery_status": "dead_letter",
                        },
                    },
                    {
                        "generated_at": "2026-03-10T05:25:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 2,
                        "stage": "improvement_stage",
                        "reason_code": "dead_letter",
                        "reason": "dead-letter after retry budget exhausted: snapshot mismatch",
                        "decision": "dead_letter",
                        "recovery_status": "dead_letter",
                        "failure_class": "test_failure",
                        "provider_hint": "test_runner",
                        "stage_family": "test",
                    },
                    {
                        "generated_at": "2026-03-10T05:24:00+00:00",
                        "source": "job_failure_runtime",
                        "job_id": job.job_id,
                        "attempt": 2,
                        "stage": "improvement_stage",
                        "reason_code": "provider_timeout",
                        "reason": "codex provider circuit open after 6/6 provider_timeout failure(s)",
                        "decision": "provider_circuit_open",
                        "recovery_status": "provider_circuit_open",
                        "failure_class": "provider_timeout",
                        "provider_hint": "codex",
                        "stage_family": "implementation",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    default_docs_dir = settings.repository_workspace_path(default_job.repository, default_job.app_code) / "_docs"
    default_docs_dir.mkdir(parents=True, exist_ok=True)
    build_workflow_artifact_paths(default_docs_dir.parent)["assistant_diagnosis_trace"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:05:00+00:00",
                "enabled": True,
                "job_id": default_job.job_id,
                "assistant_scope": "chat",
                "question": "이전 실패 요약",
                "combined_context_length": 180,
                "tool_runs": [
                    {"tool": "log_lookup", "ok": True, "mode": "internal"},
                    {"tool": "memory_search", "ok": True, "mode": "internal"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(default_docs_dir.parent)["provider_failure_counters"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:05:00+00:00",
                "latest_updated_at": "2026-03-10T05:05:00+00:00",
                "providers": {
                    "github": {
                        "provider_hint": "github",
                        "total_failures": 1,
                        "recent_failure_count": 1,
                        "last_failure_class": "provider_auth",
                        "last_stage_family": "git_provider",
                        "last_reason_code": "provider_auth",
                        "last_reason": "403 forbidden",
                        "last_job_id": default_job.job_id,
                        "last_attempt": 1,
                        "last_failed_at": "2026-03-10T05:05:00+00:00",
                        "recent_failures": [],
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(default_docs_dir.parent)["runtime_recovery_trace"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:06:00+00:00",
                "latest_event_at": "2026-03-10T05:06:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-10T05:06:00+00:00",
                        "source": "worker_stale_recovery",
                        "job_id": default_job.job_id,
                        "attempt": 1,
                        "stage": "done",
                        "reason_code": "stale_heartbeat",
                        "reason": "running heartbeat stale detected after 1800s",
                        "decision": "requeue",
                        "recovery_status": "auto_recovered",
                        "failure_class": "stale_heartbeat",
                        "provider_hint": "runtime",
                        "stage_family": "runtime_recovery",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (settings.data_dir / "worker_startup_sweep_trace.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:30:00+00:00",
                "latest_event_at": "2026-03-10T05:30:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-10T05:30:00+00:00",
                        "orphan_running_node_runs_interrupted": 1,
                        "stale_running_jobs_recovered": 2,
                        "orphan_queued_jobs_recovered": 0,
                        "running_node_job_mismatches_detected": 3,
                        "running_node_job_mismatches_remaining": 1,
                        "queue_size_before": 0,
                        "queue_size_after": 2,
                        "details": {
                            "mismatch_audit_before": {
                                "counts": {
                                    "running_job_missing_current_running_node": 2,
                                    "non_running_job_has_running_node_runs": 1,
                                }
                            },
                            "mismatch_audit_after": {
                                "counts": {
                                    "running_job_missing_current_running_node": 1,
                                }
                            },
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
    pids_dir = settings.data_dir / "pids"
    pids_dir.mkdir(parents=True, exist_ok=True)
    (pids_dir / "app_food.json").write_text(
        json.dumps(
            {
                "app_code": "food",
                "repository": "owner/repo",
                "mode": "expo-android",
                "command": "exec npx expo start --android",
                "log_file": str(settings.data_dir / "logs" / "apps" / "food.log"),
                "pid": str(os.getpid()),
                "port": "",
                "updated_at": "2026-03-10T05:31:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (pids_dir / "app_default.json").write_text(
        json.dumps(
            {
                "app_code": "default",
                "repository": "owner/repo",
                "mode": "web",
                "command": "exec npm start",
                "log_file": str(settings.data_dir / "logs" / "apps" / "default.log"),
                "pid": "999999",
                "port": "3100",
                "updated_at": "2026-03-10T05:29:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/api/admin/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["system"]["apps_count"] == 2
    assert payload["system"]["workflows_count"] == 2
    assert payload["system"]["roles_count"] == 2
    assert payload["system"]["role_presets_count"] == 1
    assert payload["runtime"]["job_summary"]["failed"] == 1
    assert payload["runtime"]["strategy_counts"][0]["name"] == "test_hardening"
    assert {item["name"] for item in payload["runtime"]["app_counts"]} == {"food", "default"}
    assert {item["name"] for item in payload["runtime"]["stage_counts"]} == {"improvement_stage", "done"}
    assert payload["runtime"]["track_counts"][0]["name"] == "enhance"
    assert payload["runtime"]["workflow_counts"][0]["name"] == "adaptive_quality_loop_v1"
    assert payload["runtime"]["adaptive_job_count"] == 1
    assert payload["runtime"]["default_job_count"] == 1
    assert payload["runtime"]["provider_failure_counts"][0]["name"] == "codex"
    assert payload["runtime"]["provider_failure_counts"][0]["count"] == 3
    assert payload["runtime"]["provider_failure_workspaces"] == 2
    assert payload["runtime"]["dead_letter_jobs"][0]["job_id"] == job.job_id
    assert payload["runtime"]["dead_letter_jobs"][0]["recovery_status"] == "dead_letter"
    assert payload["runtime"]["dead_letter_summary"]["app_counts"][0]["name"] == "food"
    assert payload["runtime"]["dead_letter_summary"]["failure_class_counts"][0]["name"] == "test_failure"
    assert payload["runtime"]["dead_letter_jobs"][0]["failure_provider_hint"] in {
        item["name"] for item in payload["runtime"]["dead_letter_summary"]["provider_counts"]
    }
    assert payload["runtime"]["recovery_history"]["event_counts"][0]["name"] == "dead_letter"
    assert "codex" in {item["name"] for item in payload["runtime"]["recovery_history"]["provider_counts"]}
    assert "implementation" in {item["name"] for item in payload["runtime"]["recovery_history"]["stage_family_counts"]}
    assert payload["runtime"]["recovery_history"]["recent_events"][0]["job_id"] == job.job_id
    assert payload["runtime"]["recovery_history"]["recent_events"][0]["decision"] == "retry_from_dead_letter"
    assert payload["runtime"]["recovery_action_groups"]["action_counts"][0]["name"] in {
        "requeue",
        "dead_letter",
        "provider_outage",
    }
    assert "dashboard_dead_letter_retry" in {
        item["name"] for item in payload["runtime"]["recovery_action_groups"]["source_counts"]
    }
    assert payload["runtime"]["operator_action_trail"]["recent_events"][0]["job_id"] == job.job_id
    assert payload["runtime"]["operator_action_trail"]["recent_events"][0]["operator_note"] == "retry after fixture update"
    assert payload["runtime"]["operator_action_trail"]["recent_events"][0]["decision"] == "retry_from_dead_letter"
    assert payload["runtime"]["provider_outage_history"]["event_counts"][0]["name"] == "provider_circuit_open"
    assert payload["runtime"]["provider_outage_history"]["provider_counts"][0]["name"] == "codex"
    assert payload["runtime"]["provider_outage_history"]["recent_events"][0]["job_id"] == job.job_id
    assert payload["runtime"]["provider_outage_history"]["recent_events"][0]["provider_hint"] == "codex"
    assert payload["runtime"]["provider_outage_history"]["recent_events"][0]["decision"] == "provider_circuit_open"
    assert payload["runtime"]["app_runner_status"]["active_count"] == 2
    assert payload["runtime"]["app_runner_status"]["mobile_count"] == 1
    assert payload["runtime"]["app_runner_status"]["web_count"] == 1
    assert {item["name"] for item in payload["runtime"]["app_runner_status"]["mode_counts"]} == {"expo-android", "web"}
    assert {item["name"] for item in payload["runtime"]["app_runner_status"]["state_counts"]} == {"running", "stopped"}
    assert {item["app_code"] for item in payload["runtime"]["app_runner_status"]["recent_runs"]} == {"food", "default"}
    assert payload["runtime"]["startup_sweep"]["stale_running_jobs_recovered"] == 2
    assert payload["runtime"]["startup_sweep"]["running_node_job_mismatches_detected"] == 3
    assert payload["runtime"]["startup_sweep"]["running_node_job_mismatches_remaining"] == 1
    assert payload["runtime"]["startup_sweep"]["mismatch_counts_before"][0]["name"] == "running_job_missing_current_running_node"
    assert payload["runtime"]["startup_sweep_history"][0]["stale_running_jobs_recovered"] == 2
    assert payload["runtime"]["startup_sweep_history"][0]["running_node_job_mismatches_detected"] == 3
    assert payload["runtime"]["startup_sweep_history"][0]["mismatch_counts_before"][0]["name"] == "running_job_missing_current_running_node"
    assert payload["quality"]["average_review_overall"] == 3.6
    assert payload["quality"]["average_maturity_score"] == 74.0
    assert payload["quality"]["trend_direction_counts"][0]["name"] == "improving"
    assert payload["workflow_adoption"]["apps_using_adaptive_workflow"] == 0
    assert payload["workflow_adoption"]["apps_using_default_workflow"] == 2
    assert payload["workflow_adoption"]["app_workflow_counts"][0]["name"] == "wf-default"
    assert len(payload["workflow_adoption"]["timeline"]) == 7
    assert payload["workflow_adoption"]["timeline"][-1]["day"] == "2026-03-08"
    assert payload["workflow_adoption"]["timeline"][-1]["adaptive_count"] == 1
    assert payload["workflow_adoption"]["timeline"][-1]["default_count"] == 0
    assert payload["workflow_adoption"]["timeline"][-2]["day"] == "2026-03-07"
    assert payload["workflow_adoption"]["timeline"][-2]["default_count"] == 1
    assert payload["memory"]["episodic_entries"] == 1
    assert payload["memory"]["decision_entries"] == 1
    assert payload["memory"]["feedback_entries"] == 1
    assert payload["feature_flags"]["memory_retrieval"] is False
    assert {item["name"] for item in payload["memory"]["ranking_state_counts"]} == {"promoted", "decayed"}
    capability_map = {item["id"]: item for item in payload["capabilities"]}
    assert capability_map["workflow_control_nodes"]["enabled"] is True
    assert capability_map["memory_retrieval"]["enabled"] is False
    assert capability_map["memory_scoring"]["enabled"] is True
    assert capability_map["strategy_shadow"]["enabled"] is True
    assert capability_map["assistant_diagnosis_loop"]["enabled"] is False
    assert capability_map["mcp_tools_shadow"]["enabled"] is False
    assert capability_map["vector_memory_shadow"]["enabled"] is False
    assert capability_map["vector_memory_retrieval"]["enabled"] is False
    assert capability_map["langgraph_planner_shadow"]["enabled"] is False
    assert capability_map["langgraph_recovery_shadow"]["enabled"] is False
    phase_map = {item["phase"]: item for item in payload["phase_status"]}
    assert phase_map["Phase 1"]["status"] == "closed"
    assert phase_map["Phase 2-F"]["status"] == "implemented"
    assert payload["retrieval"]["enabled"] is False
    assert payload["scoring"]["enabled"] is True
    assert payload["shadow"]["enabled"] is True
    assert payload["shadow"]["divergence_count"] == 1
    assert payload["runtime"]["shadow_strategy_counts"][0]["name"] == "feature_expansion"
    assert payload["runtime"]["shadow_decision_counts"][0]["name"] == "memory_divergence"
    assert payload["assistant_diagnosis"]["trace_count"] == 2
    assert payload["assistant_diagnosis"]["active"] is True
    assert payload["assistant_diagnosis"]["latest_generated_at"] == "2026-03-10T05:21:00+00:00"
    assert {item["name"] for item in payload["assistant_diagnosis"]["scope_counts"]} == {"log_analysis", "chat"}
    assert payload["assistant_diagnosis"]["tool_counts"][0]["name"] == "log_lookup"
    assert payload["assistant_diagnosis"]["failed_tool_counts"][0]["name"] == "memory_search"
    assert payload["assistant_diagnosis"]["recent_traces"][0]["job_id"] == job.job_id
    assert payload["assistant_diagnosis"]["recent_traces"][0]["failed_tool_count"] == 1
    assert payload["assistant_diagnosis"]["recent_traces"][0]["combined_context_length"] == 320
    assert payload["assistant_diagnosis"]["recent_traces"][0]["tool_runs"][2]["tool"] == "memory_search"
    assert payload["assistant_diagnosis"]["recent_traces"][0]["tool_runs"][2]["ok"] is False


def test_admin_memory_search_detail_and_override_api(app_components):
    settings, _, app = app_components
    client = TestClient(app)
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "conv_pytest_file_pattern",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "job_id": "job-memory-ui",
            "title": "pytest file pattern",
            "summary": "tests live under tests/test_*.py",
            "baseline_score": 2.4,
            "baseline_confidence": 0.8,
            "score": 2.4,
            "confidence": 0.8,
            "updated_at": "2026-03-11T00:00:00+00:00",
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
                "created_at": "2026-03-11T00:01:00+00:00",
            }
        ],
    )
    runtime_store.upsert_feedback(
        {
            "feedback_id": "fb-1",
            "memory_id": "conv_pytest_file_pattern",
            "job_id": "job-memory-ui",
            "generated_at": "2026-03-11T00:02:00+00:00",
            "verdict": "promote",
            "score_delta": 1.2,
            "routes": ["planner", "reviewer"],
        }
    )
    runtime_store.refresh_rankings(as_of="2026-03-11T00:10:00+00:00")

    response = client.get("/api/admin/memory/search", params={"q": "pytest", "state": "promoted"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["memory_id"] == "conv_pytest_file_pattern"
    assert payload["items"][0]["state_reason"] == "high cumulative score"

    response = client.get("/api/admin/memory/conv_pytest_file_pattern")
    assert response.status_code == 200
    detail = response.json()
    assert detail["entry"]["memory_id"] == "conv_pytest_file_pattern"
    assert detail["evidence"][0]["source_path"] == "tests/test_jobs_dashboard_api.py"
    assert detail["feedback"][0]["verdict"] == "promote"

    response = client.post(
        "/api/admin/memory/conv_pytest_file_pattern/override",
        json={"state": "banned", "note": "manual regression check"},
    )
    assert response.status_code == 200
    override_payload = response.json()
    assert override_payload["saved"] is True
    assert override_payload["entry"]["state"] == "banned"
    assert override_payload["entry"]["manual_state_override"] == "banned"
    assert override_payload["detail"]["entry"]["state_reason"] == "manual override: manual regression check"

    response = client.get("/api/admin/memory/search", params={"state": "banned"})
    assert response.status_code == 200
    banned_payload = response.json()
    assert banned_payload["items"][0]["memory_id"] == "conv_pytest_file_pattern"


def test_admin_memory_backlog_api_returns_candidates(app_components):
    settings, _, app = app_components
    client = TestClient(app)
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
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
            "payload": {
                "source_kind": "strategy_shadow",
                "job_id": "job-backlog",
                "shadow_strategy": "feature_expansion",
                "decision_mode": "memory_divergence",
            },
            "created_at": "2026-03-11T01:00:00+00:00",
            "updated_at": "2026-03-11T01:00:00+00:00",
        }
    )
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": "quality_trend_persistent_low:job-backlog:test_coverage",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "지속 저점 개선: test_coverage",
            "summary": "최근 3회 리뷰에서 저점이 지속됨",
            "priority": "P1",
            "state": "candidate",
            "payload": {
                "source_kind": "quality_trend_persistent_low",
                "job_id": "job-backlog",
                "category": "test_coverage",
            },
            "created_at": "2026-03-11T01:01:00+00:00",
            "updated_at": "2026-03-11T01:01:00+00:00",
        }
    )

    response = client.get("/api/admin/memory/backlog", params={"q": "shadow", "priority": "P1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["candidate_id"] == "strategy_shadow:job-backlog:feature_expansion"
    assert payload["items"][0]["payload"]["source_kind"] == "strategy_shadow"


def test_admin_memory_backlog_action_api_queues_followup_job_and_artifact(app_components):
    settings, store, app = app_components
    client = TestClient(app)
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")

    source_job = _make_job(
        "job-backlog-source",
        issue_number=701,
        issue_title="Original backlog source issue",
        status="done",
        stage="done",
        app_code="default",
        track="enhance",
        created_at="2026-03-12T01:00:00+00:00",
        updated_at="2026-03-12T01:10:00+00:00",
        workflow_id="wf-default",
    )
    store.create_job(source_job)

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
            "payload": {
                "source_kind": "next_improvement_task",
                "job_id": source_job.job_id,
                "issue_number": source_job.issue_number,
                "issue_title": source_job.issue_title,
                "recommended_node_type": "coder_fix_from_test_report",
                "action": "failing regression을 먼저 고정한다",
            },
            "created_at": "2026-03-12T01:11:00+00:00",
            "updated_at": "2026-03-12T01:11:00+00:00",
        }
    )

    approve_response = client.post(
        f"/api/admin/memory/backlog/{candidate_id}/action",
        json={"action": "approve", "note": "valid next step"},
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["candidate"]["state"] == "approved"

    queue_response = client.post(
        f"/api/admin/memory/backlog/{candidate_id}/action",
        json={"action": "queue", "note": "run next loop"},
    )
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    queued_job_id = queue_payload["queued_job_id"]

    queued_job = store.get_job(queued_job_id)
    assert queued_job is not None
    assert queued_job.status == "queued"
    assert queued_job.issue_number == source_job.issue_number
    assert queued_job.issue_title.startswith("[Follow-up] ")
    assert queued_job.workflow_id == "wf-default"
    assert queued_job.job_kind == "followup_backlog"
    assert queued_job.parent_job_id == source_job.job_id
    assert queued_job.backlog_candidate_id == candidate_id

    updated_candidate = runtime_store.get_backlog_candidate(candidate_id)
    assert updated_candidate is not None
    assert updated_candidate["state"] == "queued"
    assert updated_candidate["payload"]["queued_job_id"] == queued_job_id
    assert updated_candidate["payload"]["queued_job_kind"] == "followup_backlog"
    assert updated_candidate["payload"]["parent_job_id"] == source_job.job_id

    followup_artifact = settings.repository_workspace_path("owner/repo", "default") / "_docs" / "FOLLOWUP_BACKLOG_TASK.json"
    assert followup_artifact.exists()
    artifact_payload = json.loads(followup_artifact.read_text(encoding="utf-8"))
    assert artifact_payload["candidate_id"] == candidate_id
    assert artifact_payload["queued_job_id"] == queued_job_id
    assert artifact_payload["job_contract"]["kind"] == "followup_backlog"
    assert artifact_payload["parent_job_id"] == source_job.job_id
    assert artifact_payload["recommended_node_type"] == "coder_fix_from_test_report"


def test_admin_memory_backlog_action_api_dismisses_candidate(app_components):
    settings, _, app = app_components
    client = TestClient(app)
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    candidate_id = "quality_trend_persistent_low:job-dismiss:test_coverage"
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": candidate_id,
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "title": "지속 저점 개선: test_coverage",
            "summary": "최근 3회 리뷰에서 저점이 지속됨",
            "priority": "P1",
            "state": "candidate",
            "payload": {"source_kind": "quality_trend_persistent_low", "job_id": "job-dismiss"},
            "created_at": "2026-03-12T01:20:00+00:00",
            "updated_at": "2026-03-12T01:20:00+00:00",
        }
    )

    response = client.post(
        f"/api/admin/memory/backlog/{candidate_id}/action",
        json={"action": "dismiss", "note": "noise candidate"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate"]["state"] == "dismissed"
    assert payload["candidate"]["payload"]["operator_note"] == "noise candidate"


def test_admin_runtime_inputs_request_list_and_provide_api(app_components):
    _, store, app = app_components
    client = TestClient(app)

    job = _make_job(
        "job-runtime-input",
        issue_number=715,
        issue_title="Runtime input target job",
        status="queued",
        stage="queued",
        app_code="maps",
        track="enhance",
        created_at="2026-03-12T02:00:00+00:00",
        updated_at="2026-03-12T02:01:00+00:00",
    )
    store.create_job(job)

    create_response = client.post(
        "/api/admin/runtime-inputs/request",
        json={
            "scope": "job",
            "job_id": job.job_id,
            "key": "google_maps_api_key",
            "label": "Google Maps API Key",
            "description": "지도 SDK 초기화에 필요",
            "value_type": "secret",
            "env_var_name": "GOOGLE_MAPS_API_KEY",
            "placeholder": "추후 입력",
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()["item"]
    request_id = created["request_id"]
    assert created["repository"] == "owner/repo"
    assert created["app_code"] == "maps"
    assert created["job_id"] == job.job_id
    assert created["status"] == "requested"
    assert created["sensitive"] is True
    assert created["display_value"] == ""

    list_response = client.get("/api/admin/runtime-inputs", params={"scope": "job", "job_id": job.job_id})
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["count"] == 1
    assert list_payload["items"][0]["request_id"] == request_id

    provide_response = client.post(
        f"/api/admin/runtime-inputs/{request_id}/provide",
        json={"value": "secret-value-123", "note": "operator provided"},
    )
    assert provide_response.status_code == 200
    provided = provide_response.json()["item"]
    assert provided["status"] == "provided"
    assert provided["has_value"] is True
    assert provided["value"] == ""
    assert "*" in provided["display_value"]

    metrics_response = client.get("/api/admin/metrics")
    assert metrics_response.status_code == 200
    metrics_payload = metrics_response.json()
    assert metrics_payload["runtime_inputs"]["total"] == 1
    assert metrics_payload["runtime_inputs"]["requested"] == 0
    assert metrics_payload["runtime_inputs"]["provided"] == 1
    capability_map = {item["id"]: item for item in metrics_payload["capabilities"]}
    assert capability_map["operator_runtime_inputs"]["enabled"] is True


def test_admin_runtime_input_draft_api_uses_job_context_without_persisting(app_components):
    _, store, app = app_components
    client = TestClient(app)

    job = _make_job(
        "job-runtime-draft",
        issue_number=716,
        issue_title="Google Maps 장소 검색 화면 만들기",
        status="queued",
        stage="queued",
        app_code="maps",
        track="enhance",
        created_at="2026-03-12T02:10:00+00:00",
        updated_at="2026-03-12T02:11:00+00:00",
    )
    store.create_job(job)

    response = client.post(
        "/api/admin/runtime-inputs/draft",
        json={"job_id": job.job_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["items"][0]["key"] == "google_maps_api_key"
    assert payload["items"][0]["scope"] == "job"
    assert payload["items"][0]["requested_by"] == "assistant_draft"
    assert store.list_runtime_inputs() == []


def test_admin_runtime_inputs_request_api_accepts_assistant_draft_origin(app_components):
    _, store, app = app_components
    client = TestClient(app)

    response = client.post(
        "/api/admin/runtime-inputs/request",
        json={
            "scope": "repository",
            "repository": "owner/repo",
            "key": "stripe_secret_key",
            "label": "Stripe Secret Key",
            "description": "결제 연동에 필요",
            "value_type": "secret",
            "env_var_name": "STRIPE_SECRET_KEY",
            "requested_by": "assistant_draft",
            "note": "문맥에서 stripe 결제 요구 감지",
        },
    )

    assert response.status_code == 200
    payload = response.json()["item"]
    assert payload["requested_by"] == "assistant_draft"
    stored = store.get_runtime_input(payload["request_id"])
    assert stored is not None
    assert stored.requested_by == "assistant_draft"


def test_jobs_api_query_matches_runtime_quality_signals(app_components):
    settings, store, app = app_components
    client = TestClient(app)

    job = _make_job(
        "job-runtime-query",
        issue_number=601,
        issue_title="Runtime signal search",
        status="failed",
        stage="product_review",
        app_code="default",
        track="enhance",
        created_at="2026-03-08T06:00:00+00:00",
        updated_at="2026-03-08T06:10:00+00:00",
        workflow_id="adaptive_quality_loop_v1",
    )
    store.create_job(job)

    docs_dir = settings.repository_workspace_path(job.repository, job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "PRODUCT_REVIEW.json").write_text(
        json.dumps(
            {
                "scores": {"overall": 2.9},
                "quality_gate": {"passed": False, "categories_below_threshold": ["test_coverage"]},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (docs_dir / "REPO_MATURITY.json").write_text(
        json.dumps({"level": "mvp", "score": 58, "progression": "up"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "QUALITY_TREND.json").write_text(
        json.dumps(
            {
                "trend_direction": "improving",
                "delta_from_previous": 0.2,
                "review_round_count": 2,
                "persistent_low_categories": ["test_coverage"],
                "stagnant_categories": [],
                "category_deltas": {"test_coverage": 0},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/api/jobs", params={"q": "improving"})
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["jobs"]] == ["job-runtime-query"]

    response = client.get("/api/jobs", params={"q": "mvp"})
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["jobs"]] == ["job-runtime-query"]

    response = client.get("/api/jobs", params={"q": "test_coverage"})
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["jobs"]] == ["job-runtime-query"]

    response = client.get("/api/jobs", params={"q": "adaptive_quality_loop_v1"})
    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["jobs"]] == ["job-runtime-query"]


def test_roles_api_persists_skills_and_allowed_tools(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    roles_path = tmp_path / "config" / "roles.json"
    roles_path.parent.mkdir(parents=True, exist_ok=True)
    roles_path.write_text("{\"roles\": [], \"presets\": []}\n", encoding="utf-8")
    monkeypatch.setattr(dashboard, "_ROLES_CONFIG_PATH", roles_path)

    client = TestClient(app)
    response = client.post(
        "/api/roles",
        json={
            "code": "planner",
            "name": "Planner",
            "cli": "gemini",
            "template_key": "planner",
            "skills": ["repo-reading", "mvp-planning", "repo-reading"],
            "allowed_tools": ["research_search", "research_search"],
            "enabled": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    role = next(item for item in payload["roles"] if item["code"] == "planner")
    assert role["skills"] == ["repo-reading", "mvp-planning"]
    assert role["allowed_tools"] == ["research_search"]

    persisted = json.loads(roles_path.read_text(encoding="utf-8"))
    role = next(item for item in persisted["roles"] if item["code"] == "planner")
    assert role["skills"] == ["repo-reading", "mvp-planning"]
    assert role["allowed_tools"] == ["research_search"]


def test_roles_api_default_catalog_hides_legacy_provider_roles(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    roles_path = tmp_path / "config" / "missing-roles.json"
    monkeypatch.setattr(dashboard, "_ROLES_CONFIG_PATH", roles_path)

    client = TestClient(app)
    response = client.get("/api/roles")

    assert response.status_code == 200
    payload = response.json()
    role_codes = {item["code"] for item in payload["roles"]}
    assert "log-analyzer-codex" in role_codes
    assert "log-analyzer-gemini" in role_codes
    assert "log-analyzer-claude" not in role_codes
    assert "log-analyzer-copilot" not in role_codes
    helper_templates = {item["code"]: item.get("template_key", "") for item in payload["roles"]}
    helper_tools = {item["code"]: item.get("allowed_tools", []) for item in payload["roles"]}
    assert helper_templates["ai-helper"] == "codex_helper"
    assert helper_templates["incident-analyst"] == "codex_helper"
    assert helper_templates["orchestration-helper"] == "codex_helper"
    assert helper_templates["data-ai-engineer"] == "codex_helper"
    assert helper_tools["ai-helper"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["incident-analyst"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["orchestration-helper"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["data-ai-engineer"] == ["log_lookup", "repo_search", "memory_search"]
