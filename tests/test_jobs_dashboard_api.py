"""Tests for dashboard job filtering and pagination."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord


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
    job.recovery_status = "auto_recovered"
    store.create_job(job)
    store.create_job(
        _make_job(
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
    )

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
    phase_map = {item["phase"]: item for item in payload["phase_status"]}
    assert phase_map["Phase 1"]["status"] == "closed"
    assert phase_map["Phase 2-F"]["status"] == "implemented"
    assert payload["retrieval"]["enabled"] is False
    assert payload["scoring"]["enabled"] is True
    assert payload["shadow"]["enabled"] is True
    assert payload["shadow"]["divergence_count"] == 1
    assert payload["runtime"]["shadow_strategy_counts"][0]["name"] == "feature_expansion"
    assert payload["runtime"]["shadow_decision_counts"][0]["name"] == "memory_divergence"


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
    assert helper_templates["ai-helper"] == "codex_helper"
    assert helper_templates["incident-analyst"] == "codex_helper"
    assert helper_templates["orchestration-helper"] == "codex_helper"
    assert helper_templates["data-ai-engineer"] == "codex_helper"
