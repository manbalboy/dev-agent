"""Tests for dashboard job filtering and pagination."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

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
    assert payload["filters"]["recovery_status"] == "auto_recovered"
    assert payload["filters"]["strategy"] == "quality_hardening"
    assert "auto_recovered" in payload["filter_options"]["recovery_statuses"]
    assert "quality_hardening" in payload["filter_options"]["strategies"]
