"""Tests for dashboard job filtering and pagination."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import IntegrationRegistryRecord, JobRecord, RuntimeInputRecord, utc_now_iso
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


def test_admin_patch_status_api_returns_runtime_payload(app_components, monkeypatch):
    _, _, app = app_components
    client = TestClient(app)
    observed = {}

    class DummyPatchRuntime:
        def build_patch_status(self, *, refresh: bool = False):
            observed["refresh"] = refresh
            return {
                "status": "update_available",
                "current_branch": "master",
                "upstream_ref": "origin/master",
                "behind_count": 2,
                "ahead_count": 0,
                "working_tree_dirty": False,
                "message": "패치가 있습니다. 진행하시겠습니까?",
            }

    monkeypatch.setattr(dashboard, "_build_patch_control_runtime", lambda: DummyPatchRuntime())

    response = client.get("/api/admin/patch-status?refresh=1")

    assert response.status_code == 200
    payload = response.json()
    assert observed["refresh"] is True
    assert payload["status"] == "update_available"
    assert payload["behind_count"] == 2
    assert payload["message"] == "패치가 있습니다. 진행하시겠습니까?"


def test_agent_cli_check_api_returns_git_and_gh(app_components, monkeypatch):
    _, _, app = app_components
    client = TestClient(app)

    monkeypatch.setattr(
        dashboard,
        "collect_agent_cli_status",
        lambda command_config: {
            "gemini": {"ok": True, "command": "gemini --version", "output": "gemini"},
            "codex": {"ok": True, "command": "codex --version", "output": "codex"},
            "git": {"ok": True, "command": "git --version", "output": "git version 2.50.1"},
            "gh": {"ok": True, "command": "gh --version", "output": "gh version 2.80.0"},
        },
    )

    response = client.get("/api/agents/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["git"]["ok"] is True
    assert payload["git"]["command"] == "git --version"
    assert payload["gh"]["ok"] is True
    assert payload["gh"]["command"] == "gh --version"


def test_admin_patch_run_latest_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyPatchRunRuntime:
        def get_latest_patch_run_payload(self):
            return {
                "active": True,
                "patch_run_id": "patch-1",
                "status": "waiting_updater",
                "current_step_label": "업데이트 대기",
                "progress_percent": 20,
            }

    monkeypatch.setattr(
        dashboard,
        "_build_dashboard_patch_runtime",
        lambda current_store, current_settings: DummyPatchRunRuntime(),
    )

    response = client.get("/api/admin/patch-runs/latest")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["patch_run_id"] == "patch-1"
    assert payload["status"] == "waiting_updater"
    assert payload["progress_percent"] == 20


def test_admin_patch_updater_status_api_returns_runtime_payload(app_components, monkeypatch):
    settings, store, app = app_components
    client = TestClient(app)

    class DummyPatchUpdaterRuntime:
        def read_status_payload(self):
            return {
                "service_name": "agenthub-updater",
                "status": "tracking",
                "pid": 4242,
                "active_patch_run_id": "patch-1",
                "last_heartbeat_at": "2026-03-13T10:00:00+09:00",
                "updated_at": "2026-03-13T10:00:00+09:00",
                "message": "Updater service가 patch run을 감지했습니다.",
                "details": {"next_action": "service_drain_restart_not_implemented"},
            }

    monkeypatch.setattr(
        dashboard,
        "_build_patch_updater_runtime",
        lambda current_store, current_settings: DummyPatchUpdaterRuntime(),
    )

    response = client.get("/api/admin/patch-updater-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "tracking"
    assert payload["active_patch_run_id"] == "patch-1"
    assert payload["details"]["next_action"] == "service_drain_restart_not_implemented"


def test_admin_security_governance_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummySecurityGovernanceRuntime:
        def build_status(self):
            return {
                "overall_status": "warning",
                "warning_count": 2,
                "transport": {
                    "public_base_url": "https://agenthub.example.com",
                    "https_enforced": True,
                },
                "warnings": [{"code": "cors_too_permissive", "message": "CORS too wide"}],
            }

    monkeypatch.setattr(
        dashboard,
        "_build_security_governance_runtime",
        lambda current_settings: DummySecurityGovernanceRuntime(),
    )

    response = client.get("/api/admin/security-governance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "warning"
    assert payload["warning_count"] == 2
    assert payload["transport"]["https_enforced"] is True


def test_admin_durable_runtime_hygiene_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyDurableRuntimeHygieneRuntime:
        def build_hygiene_status(self):
            return {
                "generated_at": "2026-03-14T00:00:00+00:00",
                "retention_days": 7,
                "message": "정리 후보 3건을 찾았습니다.",
                "summary": {
                    "cleanup_candidate_count": 3,
                    "queue_cleanup_candidate_count": 1,
                    "workspace_warning_count": 2,
                },
                "patch_lock": {"stale_active_lock": False},
            }

    monkeypatch.setattr(
        dashboard,
        "_build_durable_runtime_hygiene_runtime",
        lambda current_store, current_settings: DummyDurableRuntimeHygieneRuntime(),
    )

    response = client.get("/api/admin/durable-runtime-hygiene")

    assert response.status_code == 200
    payload = response.json()
    assert payload["retention_days"] == 7
    assert payload["summary"]["cleanup_candidate_count"] == 3


def test_admin_durable_runtime_hygiene_cleanup_api_returns_cleanup_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyDurableRuntimeHygieneRuntime:
        def cleanup(self):
            return {
                "cleanup_applied": True,
                "message": "durable runtime hygiene 정리를 적용했습니다.",
                "summary": {"cleanup_candidate_count": 0},
                "cleanup": {"patch_lock_cleared": True},
                "patch_lock": {"stale_active_lock": False},
            }

    monkeypatch.setattr(
        dashboard,
        "_build_durable_runtime_hygiene_runtime",
        lambda current_store, current_settings: DummyDurableRuntimeHygieneRuntime(),
    )

    response = client.post("/api/admin/durable-runtime-hygiene/cleanup")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cleanup_applied"] is True
    assert payload["cleanup"]["patch_lock_cleared"] is True


def test_admin_durable_runtime_self_check_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyDurableRuntimeSelfCheckRuntime:
        def read_status(self):
            return {
                "generated_at": "2026-03-14T00:00:00+00:00",
                "overall_status": "warning",
                "message": "periodic self-check 경고 2건이 있습니다.",
                "summary": {
                    "patch_health_failed_check_count": 1,
                    "cleanup_candidate_count": 2,
                    "security_warning_count": 1,
                },
                "report_meta": {"stale": False, "path": "data/report.json"},
                "patch_health": {"failed_checks": ["updater_status"]},
                "warnings": [{"code": "patch_updater_offline", "message": "offline"}],
            }

    monkeypatch.setattr(
        dashboard,
        "_build_durable_runtime_self_check_runtime",
        lambda current_store, current_settings: DummyDurableRuntimeSelfCheckRuntime(),
    )

    response = client.get("/api/admin/durable-runtime-self-check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "warning"
    assert payload["summary"]["patch_health_failed_check_count"] == 1
    assert payload["report_meta"]["stale"] is False


def test_admin_durable_runtime_self_check_run_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyDurableRuntimeSelfCheckRuntime:
        def run_check(self, *, trigger: str = "manual"):
            assert trigger == "manual"
            return {
                "generated_at": "2026-03-14T00:00:00+00:00",
                "overall_status": "ready",
                "message": "periodic self-check 기준을 충족합니다.",
                "summary": {"patch_health_failed_check_count": 0},
                "report_meta": {"stale": False},
                "warnings": [],
            }

    monkeypatch.setattr(
        dashboard,
        "_build_durable_runtime_self_check_runtime",
        lambda current_store, current_settings: DummyDurableRuntimeSelfCheckRuntime(),
    )

    response = client.post("/api/admin/durable-runtime-self-check/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "ready"
    assert payload["summary"]["patch_health_failed_check_count"] == 0


def test_admin_durable_runtime_self_check_alert_acknowledge_api_returns_runtime_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)

    class DummyDurableRuntimeSelfCheckRuntime:
        def acknowledge_alert(self, *, acted_by: str = "operator", note: str = ""):
            assert acted_by == "dashboard"
            assert note == "handled"
            return {
                "generated_at": "2026-03-14T00:00:00+00:00",
                "overall_status": "warning",
                "message": "periodic self-check 경고를 확인했습니다.",
                "summary": {"patch_health_failed_check_count": 1},
                "report_meta": {"stale": False},
                "alert": {
                    "active": True,
                    "state": "acknowledged",
                    "acknowledged": True,
                    "acknowledged_by": "dashboard",
                    "note": "handled",
                },
                "warnings": [{"code": "patch_updater_offline", "message": "offline"}],
            }

    monkeypatch.setattr(
        dashboard,
        "_build_durable_runtime_self_check_runtime",
        lambda current_store, current_settings: DummyDurableRuntimeSelfCheckRuntime(),
    )

    response = client.post(
        "/api/admin/durable-runtime-self-check/alert/acknowledge",
        json={"acted_by": "dashboard", "note": "handled"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["alert"]["state"] == "acknowledged"
    assert payload["alert"]["acknowledged_by"] == "dashboard"


def test_admin_patch_run_create_api_returns_created_patch_run(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)
    observed = {}

    class DummyPatchRunRuntime:
        def create_patch_run(self, *, refresh: bool = False, note: str = ""):
            observed["refresh"] = refresh
            observed["note"] = note
            return {
                "created": True,
                "patch_run": {
                    "patch_run_id": "patch-2",
                    "status": "waiting_updater",
                    "progress_percent": 20,
                    "note": note,
                },
            }

    monkeypatch.setattr(
        dashboard,
        "_build_dashboard_patch_runtime",
        lambda current_store, current_settings: DummyPatchRunRuntime(),
    )

    response = client.post(
        "/api/admin/patch-runs",
        json={"refresh": True, "note": "야간 반영"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert observed == {"refresh": True, "note": "야간 반영"}
    assert payload["created"] is True
    assert payload["patch_run"]["patch_run_id"] == "patch-2"
    assert payload["patch_run"]["status"] == "waiting_updater"
    assert payload["patch_run"]["note"] == "야간 반영"


def test_admin_patch_run_rollback_api_returns_requested_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)
    observed = {}

    class DummyPatchRunRuntime:
        def request_rollback(self, *, patch_run_id: str, note: str = ""):
            observed["patch_run_id"] = patch_run_id
            observed["note"] = note
            return {
                "rollback_requested": True,
                "patch_run": {
                    "patch_run_id": patch_run_id,
                    "status": "rollback_requested",
                    "progress_percent": 15,
                    "details": {"rollback_note": note},
                },
            }

    monkeypatch.setattr(
        dashboard,
        "_build_dashboard_patch_runtime",
        lambda current_store, current_settings: DummyPatchRunRuntime(),
    )

    response = client.post(
        "/api/admin/patch-runs/patch-2/rollback",
        json={"note": "직전 커밋으로 복구"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert observed == {"patch_run_id": "patch-2", "note": "직전 커밋으로 복구"}
    assert payload["rollback_requested"] is True
    assert payload["patch_run"]["status"] == "rollback_requested"


def test_admin_patch_run_restore_api_returns_requested_payload(app_components, monkeypatch):
    _, store, app = app_components
    client = TestClient(app)
    observed = {}

    class DummyPatchRunRuntime:
        def request_restore(self, *, patch_run_id: str, note: str = ""):
            observed["patch_run_id"] = patch_run_id
            observed["note"] = note
            return {
                "restore_requested": True,
                "patch_run": {
                    "patch_run_id": patch_run_id,
                    "status": "restore_requested",
                    "progress_percent": 15,
                    "details": {"restore_note": note},
                },
            }

    monkeypatch.setattr(
        dashboard,
        "_build_dashboard_patch_runtime",
        lambda current_store, current_settings: DummyPatchRunRuntime(),
    )

    response = client.post(
        "/api/admin/patch-runs/patch-2/restore",
        json={"note": "백업 상태로 복원"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert observed == {"patch_run_id": "patch-2", "note": "백업 상태로 복원"}
    assert payload["restore_requested"] is True
    assert payload["patch_run"]["status"] == "restore_requested"


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
    assert "서드파티 통합 레지스트리" in response.text
    assert "Google Maps, Supabase, Stripe 같은 외부 통합을 읽기 전용으로 조회합니다." in response.text


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
    followup_parent = _make_job(
        "job-followup-parent",
        issue_number=503,
        issue_title="Parent baseline",
        status="done",
        stage="done",
        app_code="maps",
        track="enhance",
        created_at="2026-03-07T06:00:00+00:00",
        updated_at="2026-03-07T06:30:00+00:00",
        workflow_id="wf-default",
    )
    store.create_job(followup_parent)
    followup_job = _make_job(
        "job-followup-child",
        issue_number=504,
        issue_title="Follow-up Google Maps hardening",
        status="done",
        stage="product_review",
        app_code="maps",
        track="enhance",
        created_at="2026-03-08T05:00:00+00:00",
        updated_at="2026-03-08T05:45:00+00:00",
        workflow_id="wf-default",
    )
    followup_job.job_kind = "followup_backlog"
    followup_job.parent_job_id = followup_parent.job_id
    followup_job.backlog_candidate_id = "failure_pattern_cluster:job-followup-parent:loop_guard_repeated_issue"
    store.create_job(followup_job)
    prior_followup_job = _make_job(
        "job-followup-child-prior",
        issue_number=505,
        issue_title="Follow-up regression sample",
        status="done",
        stage="product_review",
        app_code="maps",
        track="enhance",
        created_at="2026-03-06T05:00:00+00:00",
        updated_at="2026-03-06T05:30:00+00:00",
        workflow_id="wf-default",
    )
    prior_followup_job.job_kind = "followup_backlog"
    prior_followup_job.repository = "owner/repo-alt"
    prior_followup_job.parent_job_id = followup_parent.job_id
    prior_followup_job.backlog_candidate_id = "next_improvement_task:job-followup-parent:0"
    store.create_job(prior_followup_job)
    insufficient_followup_job = _make_job(
        "job-followup-child-insufficient",
        issue_number=506,
        issue_title="Follow-up insufficient baseline sample",
        status="done",
        stage="product_review",
        app_code="maps",
        track="enhance",
        created_at="2026-03-09T07:00:00+00:00",
        updated_at="2026-03-09T07:30:00+00:00",
        workflow_id="wf-default",
    )
    insufficient_followup_job.job_kind = "followup_backlog"
    insufficient_followup_job.repository = "owner/repo-insufficient"
    insufficient_followup_job.parent_job_id = followup_parent.job_id
    insufficient_followup_job.backlog_candidate_id = "next_improvement_task:job-followup-parent:1"
    store.create_job(insufficient_followup_job)
    now = utc_now_iso()
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": followup_job.backlog_candidate_id,
            "repository": followup_job.repository,
            "execution_repository": followup_job.repository,
            "app_code": followup_job.app_code,
            "workflow_id": followup_job.workflow_id or "wf-default",
            "title": "반복 실패 클러스터: loop_guard_repeated_issue",
            "summary": "loop guard 관련 반복 실패를 follow-up으로 보강",
            "priority": "P1",
            "state": "queued",
            "payload": {
                "source_kind": "failure_pattern_cluster",
                "job_id": followup_parent.job_id,
                "pattern_id": "loop_guard_repeated_issue",
                "count": 4,
            },
            "created_at": "2026-03-08T05:20:00+00:00",
            "updated_at": "2026-03-08T05:20:00+00:00",
        }
    )
    runtime_store.upsert_backlog_candidate(
        {
            "candidate_id": prior_followup_job.backlog_candidate_id,
            "repository": prior_followup_job.repository,
            "execution_repository": prior_followup_job.repository,
            "app_code": prior_followup_job.app_code,
            "workflow_id": prior_followup_job.workflow_id or "wf-default",
            "title": "회귀 테스트 보강",
            "summary": "기본 follow-up 샘플",
            "priority": "P1",
            "state": "queued",
            "payload": {
                "source_kind": "next_improvement_task",
                "job_id": followup_parent.job_id,
            },
            "created_at": "2026-03-06T05:20:00+00:00",
            "updated_at": "2026-03-06T05:20:00+00:00",
        }
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps", "places"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="지도 로딩 확인",
            approval_required=True,
            approval_status="approved",
            approval_note="지도 기능 도입 승인",
            approval_updated_at=now,
            approval_updated_by="operator",
            approval_trail=[],
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-google-maps",
            repository=job.repository,
            app_code=job.app_code,
            job_id="",
            scope="repository",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 기능 구현에 필요",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="requested",
            value="",
            requested_by="operator",
            requested_at=now,
            provided_at="",
            updated_at=now,
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
    build_workflow_artifact_paths(docs_dir.parent)["integration_usage_trail"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:22:00+00:00",
                "repository": job.repository,
                "job_id": job.job_id,
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-10T05:22:00+00:00",
                        "stage": "implement_with_codex",
                        "route": "coder",
                        "prompt_path": str(docs_dir / "CODER_PROMPT_IMPLEMENT.md"),
                        "integration_count": 1,
                        "blocked_integration_count": 1,
                        "blocked_env_vars": ["GOOGLE_MAPS_API_KEY"],
                        "items": [
                            {
                                "integration_id": "google_maps",
                                "display_name": "Google Maps",
                                "category": "mapping",
                                "required_input_summary": {
                                    "total": 1,
                                    "provided": 0,
                                    "requested": 1,
                                    "missing": 0,
                                },
                                "approval_status": "approved",
                                "input_readiness_status": "input_requested",
                                "input_readiness_reason": "필수 env가 요청 상태입니다.",
                                "usage_status": "prompt_injected",
                                "blocked_inputs": [
                                    {
                                        "env_var_name": "GOOGLE_MAPS_API_KEY",
                                        "bridge_reason": "필수 env가 아직 provided 상태가 아니라 runtime env bridge에 포함되지 않았습니다.",
                                        "status": "requested",
                                    }
                                ],
                            }
                        ],
                        "active": True,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(docs_dir.parent)["integration_recommendations"].write_text(
        json.dumps(
            {
                "items": [
                    {
                        "integration_id": "google_maps",
                        "display_name": "Google Maps",
                        "recommendation_status": "operator_review_and_input_required",
                        "reason": "지도 화면 구현에는 지도 SDK 도입 검토가 필요합니다.",
                        "required_env_keys": ["GOOGLE_MAPS_API_KEY"],
                        "input_readiness_status": "input_requested",
                        "input_readiness_reason": "필수 env가 요청 상태라 값 제공 전까지 구현을 진행할 수 없습니다.",
                        "approval_status": "approved",
                        "approval_required": True,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    followup_docs_dir = settings.repository_workspace_path(followup_job.repository, followup_job.app_code) / "_docs"
    followup_docs_dir.mkdir(parents=True, exist_ok=True)
    build_workflow_artifact_paths(followup_docs_dir.parent)["self_growing_effectiveness"].write_text(
        json.dumps(
            {
                "active": True,
                "generated_at": "2026-03-10T05:24:00+00:00",
                "job_id": followup_job.job_id,
                "job_kind": "followup_backlog",
                "parent_job_id": followup_parent.job_id,
                "backlog_candidate_id": followup_job.backlog_candidate_id,
                "status": "improved",
                "status_label": "개선됨",
                "summary": "follow-up 작업이 부모 작업 대비 개선되었습니다.",
                "deltas": {
                    "review_overall": 0.6,
                    "maturity_score": 8,
                    "quality_gate_passed": 1,
                    "maturity_level_order": 1,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    build_workflow_artifact_paths(followup_docs_dir.parent)["failure_patterns"].write_text(
        json.dumps(
            {
                "items": [
                    {
                        "pattern_id": "loop_guard:repeated_issue",
                        "count": 2,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    prior_followup_docs_dir = settings.repository_workspace_path(prior_followup_job.repository, prior_followup_job.app_code) / "_docs"
    prior_followup_docs_dir.mkdir(parents=True, exist_ok=True)
    build_workflow_artifact_paths(prior_followup_docs_dir.parent)["self_growing_effectiveness"].write_text(
        json.dumps(
            {
                "active": True,
                "generated_at": "2026-03-09T05:24:00+00:00",
                "job_id": prior_followup_job.job_id,
                "job_kind": "followup_backlog",
                "parent_job_id": followup_parent.job_id,
                "backlog_candidate_id": prior_followup_job.backlog_candidate_id,
                "status": "regressed",
                "status_label": "회귀됨",
                "summary": "follow-up 작업이 부모 작업 대비 회귀했습니다.",
                "deltas": {
                    "review_overall": -0.3,
                    "maturity_score": -2,
                    "quality_gate_passed": 0,
                    "maturity_level_order": 0,
                },
                "status_reasons": ["성숙도 점수 -2"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    insufficient_followup_docs_dir = settings.repository_workspace_path(insufficient_followup_job.repository, insufficient_followup_job.app_code) / "_docs"
    insufficient_followup_docs_dir.mkdir(parents=True, exist_ok=True)
    build_workflow_artifact_paths(insufficient_followup_docs_dir.parent)["self_growing_effectiveness"].write_text(
        json.dumps(
            {
                "active": True,
                "generated_at": "2026-03-09T09:24:00+00:00",
                "job_id": insufficient_followup_job.job_id,
                "job_kind": "followup_backlog",
                "parent_job_id": followup_parent.job_id,
                "backlog_candidate_id": insufficient_followup_job.backlog_candidate_id,
                "status": "insufficient_baseline",
                "status_label": "비교 기준 부족",
                "summary": "부모 작업 기준 산출물이 부족해 follow-up 효과를 비교할 수 없습니다.",
                "baseline_missing": ["parent_review_history_entry", "parent_maturity_score"],
                "status_reasons": ["부모 작업 기준 산출물이 부족합니다."],
                "deltas": {},
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
    food_docs_dir = settings.repository_workspace_path("owner/repo", "food") / "_docs"
    food_docs_dir.mkdir(parents=True, exist_ok=True)
    build_workflow_artifact_paths(food_docs_dir.parent)["mobile_e2e_result"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T05:32:00+00:00",
                "platform": "android",
                "target_name": "Pixel 8",
                "target_id": "emulator-5554",
                "booted": True,
                "command": "npm run test:e2e:android",
                "exit_code": 0,
                "status": "passed",
                "runner": "npm_script",
                "notes": "reused already booted android emulator",
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
    assert {item["name"] for item in payload["runtime"]["app_counts"]} == {"food", "default", "maps"}
    assert {item["name"] for item in payload["runtime"]["stage_counts"]} == {"improvement_stage", "done", "product_review"}
    assert payload["runtime"]["track_counts"][0]["name"] == "enhance"
    assert {item["name"] for item in payload["runtime"]["workflow_counts"]} == {"adaptive_quality_loop_v1", "wf-default"}
    assert payload["runtime"]["adaptive_job_count"] == 1
    assert payload["runtime"]["default_job_count"] == 5
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
    assert payload["runtime"]["app_runner_status"]["mobile_e2e_count"] == 1
    assert payload["runtime"]["app_runner_status"]["mobile_e2e_status_counts"][0]["name"] == "passed"
    assert payload["runtime"]["app_runner_status"]["recent_mobile_e2e_runs"][0]["app_code"] == "food"
    assert payload["runtime"]["app_runner_status"]["recent_mobile_e2e_runs"][0]["platform"] == "android"
    assert payload["runtime"]["startup_sweep"]["stale_running_jobs_recovered"] == 2
    assert payload["runtime"]["startup_sweep"]["running_node_job_mismatches_detected"] == 3
    assert payload["runtime"]["startup_sweep"]["running_node_job_mismatches_remaining"] == 1
    assert payload["runtime"]["startup_sweep"]["mismatch_counts_before"][0]["name"] == "running_job_missing_current_running_node"
    assert payload["runtime"]["startup_sweep_history"][0]["stale_running_jobs_recovered"] == 2
    assert payload["runtime"]["startup_sweep_history"][0]["running_node_job_mismatches_detected"] == 3
    assert payload["runtime"]["startup_sweep_history"][0]["mismatch_counts_before"][0]["name"] == "running_job_missing_current_running_node"
    assert payload["runtime"]["integration_health_summary"]["total_integrations"] == 1
    assert payload["runtime"]["integration_health_summary"]["enabled_integrations"] == 1
    assert {item["name"] for item in payload["runtime"]["integration_health_summary"]["approval_counts"]} == {"approved"}
    assert {item["name"] for item in payload["runtime"]["integration_health_summary"]["readiness_counts"]} == {"input_requested"}
    assert payload["runtime"]["integration_health_summary"]["used_integration_counts"][0]["name"] == "google_maps"
    assert payload["runtime"]["integration_health_summary"]["used_integration_counts"][0]["count"] == 1
    assert payload["runtime"]["integration_health_summary"]["blocked_boundary_counts"][0]["name"] == "approval_and_input_required"
    assert payload["runtime"]["integration_health_summary"]["blocked_env_counts"][0]["name"] == "GOOGLE_MAPS_API_KEY"
    assert payload["runtime"]["integration_health_summary"]["recent_blocked_jobs"][0]["job_id"] == job.job_id
    assert payload["runtime"]["integration_health_summary"]["recent_blocked_jobs"][0]["boundary_status"] == "approval_and_input_required"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["followup_job_count"] == 3
    assert payload["runtime"]["self_growing_effectiveness_summary"]["active_artifact_jobs"] == 3
    assert payload["runtime"]["self_growing_effectiveness_summary"]["improved_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["regressed_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["insufficient_baseline_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["status_counts"][0]["name"] == "improved"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_items"][0]["job_id"] == insufficient_followup_job.job_id
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_items"][0]["parent_job_id"] == followup_parent.job_id
    assert payload["runtime"]["self_growing_effectiveness_summary"]["latest_generated_day"] == "2026-03-10"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_timeline"][-1]["day"] == "2026-03-10"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_timeline"][-1]["improved_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_timeline"][-2]["day"] == "2026-03-09"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_timeline"][-2]["regressed_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_timeline"][-2]["insufficient_baseline_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["app_status_breakdown"][0]["app_code"] == "maps"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["app_status_breakdown"][0]["total"] == 3
    assert payload["runtime"]["self_growing_effectiveness_summary"]["app_status_breakdown"][0]["improved_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["app_status_breakdown"][0]["regressed_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["app_status_breakdown"][0]["insufficient_baseline_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_linked_followup_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_improved_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_regressed_count"] == 0
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_insufficient_baseline_count"] == 0
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recurrence_reduced_count"] == 1
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recurrence_unchanged_count"] == 0
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recurrence_increased_count"] == 0
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_reduced_occurrences_total"] == 2
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_pattern_counts"][0]["name"] == "loop_guard_repeated_issue"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recent_items"][0]["candidate_id"] == followup_job.backlog_candidate_id
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recent_items"][0]["pattern_count"] == 4
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recurrence_status_counts"][0]["name"] == "reduced"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recent_recurrence_items"][0]["pattern_id"] == "loop_guard_repeated_issue"
    assert payload["runtime"]["self_growing_effectiveness_summary"]["cluster_recent_recurrence_items"][0]["current_count"] == 2
    assert payload["runtime"]["self_growing_effectiveness_summary"]["regressed_reason_counts"][0]["name"] == "성숙도 점수 -2"
    assert {
        item["name"]
        for item in payload["runtime"]["self_growing_effectiveness_summary"]["insufficient_baseline_reasons"][:2]
    } == {"parent_review_history_entry", "parent_maturity_score"}
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_regressed_items"][0]["job_id"] == prior_followup_job.job_id
    assert payload["runtime"]["self_growing_effectiveness_summary"]["recent_insufficient_baseline_items"][0]["job_id"] == insufficient_followup_job.job_id
    assert payload["quality"]["average_review_overall"] == 3.6
    assert payload["quality"]["average_maturity_score"] == 74.0
    assert payload["quality"]["trend_direction_counts"][0]["name"] == "improving"
    assert payload["workflow_adoption"]["apps_using_adaptive_workflow"] == 0
    assert payload["workflow_adoption"]["apps_using_default_workflow"] == 2
    assert payload["workflow_adoption"]["app_workflow_counts"][0]["name"] == "wf-default"
    assert len(payload["workflow_adoption"]["timeline"]) == 7
    workflow_timeline = {
        item["day"]: item for item in payload["workflow_adoption"]["timeline"]
    }
    assert workflow_timeline["2026-03-09"]["adaptive_count"] == 0
    assert workflow_timeline["2026-03-09"]["default_count"] == 1
    assert workflow_timeline["2026-03-08"]["adaptive_count"] == 1
    assert workflow_timeline["2026-03-08"]["default_count"] == 1
    assert workflow_timeline["2026-03-07"]["default_count"] == 2
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


def test_admin_integrations_api_persists_and_lists_registry_entries(app_components):
    _, store, app = app_components
    client = TestClient(app)

    create_response = client.post(
        "/api/admin/integrations",
        json={
            "integration_id": "google_maps",
            "display_name": "Google Maps",
            "category": "mapping",
            "supported_app_types": ["web", "app"],
            "tags": ["maps", "places"],
            "required_env_keys": ["google_maps_api_key"],
            "optional_env_keys": ["google_maps_map_id"],
            "operator_guide_markdown": "운영자 가이드",
            "implementation_guide_markdown": "구현 가이드",
            "verification_notes": "지도 로딩 확인",
            "approval_required": True,
            "enabled": True,
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()["item"]
    assert created["integration_id"] == "google_maps"
    assert created["required_env_keys"] == ["GOOGLE_MAPS_API_KEY"]
    assert created["supported_app_types"] == ["web", "app"]

    list_response = client.get(
        "/api/admin/integrations",
        params={"q": "maps", "category": "mapping", "app_type": "web", "enabled": "true"},
    )
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["integration_id"] == "google_maps"
    assert payload["items"][0]["required_input_summary"] == {
        "total": 1,
        "provided": 0,
        "requested": 0,
        "missing": 1,
    }
    assert payload["items"][0]["input_readiness_status"] == "input_required"
    assert "운영자 입력이 필요합니다" in payload["items"][0]["input_readiness_reason"]
    stored = store.get_integration_registry_entry("google_maps")
    assert stored is not None
    assert stored.display_name == "Google Maps"

    request_response = client.post(
        "/api/admin/runtime-inputs/request",
        json={
            "scope": "repository",
            "repository": "owner/repo",
            "app_code": "default",
            "job_id": "",
            "key": "google_maps_api_key",
            "label": "Google Maps API Key",
            "description": "지도 기능 구현용 키",
            "value_type": "secret",
            "env_var_name": "GOOGLE_MAPS_API_KEY",
            "sensitive": True,
            "placeholder": "나중에 입력",
        },
    )
    assert request_response.status_code == 200

    linked_response = client.get("/api/admin/integrations", params={"q": "maps"})
    assert linked_response.status_code == 200
    linked_item = linked_response.json()["items"][0]
    assert linked_item["required_input_summary"] == {
        "total": 1,
        "provided": 0,
        "requested": 1,
        "missing": 0,
    }
    assert linked_item["input_readiness_status"] == "input_requested"
    assert "준비 대기" in linked_item["input_readiness_reason"]
    assert linked_item["required_input_links"][0]["latest_request"]["label"] == "Google Maps API Key"


def test_admin_integrations_approval_action_api_updates_status(app_components):
    _, store, app = app_components
    client = TestClient(app)

    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="운영자 가이드",
            implementation_guide_markdown="구현 가이드",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )

    reject_response = client.post(
        "/api/admin/integrations/google_maps/approval",
        json={
            "action": "reject",
            "note": "현재 범위에서는 지도 기능 제외",
            "acted_by": "dashboard_operator",
        },
    )
    assert reject_response.status_code == 200
    rejected = reject_response.json()["item"]
    assert rejected["approval_status"] == "rejected"
    assert rejected["input_readiness_status"] == "approval_rejected"
    assert "현재 범위에서는 지도 기능 제외" in rejected["input_readiness_reason"]
    assert rejected["approval_trail_count"] == 1
    assert rejected["approval_trail"][0]["action"] == "reject"

    approve_response = client.post(
        "/api/admin/integrations/google_maps/approval",
        json={
            "action": "approve",
            "note": "도입 승인",
            "acted_by": "dashboard_operator",
        },
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()["item"]
    assert approved["approval_status"] == "approved"
    assert approved["input_readiness_status"] == "input_required"
    assert approved["approval_trail_count"] == 2
    assert approved["approval_trail"][0]["action"] == "approve"

    stored = store.get_integration_registry_entry("google_maps")
    assert stored is not None
    assert stored.approval_status == "approved"
    assert stored.approval_note == "도입 승인"
    assert len(stored.approval_trail) == 2


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
    assert "summary-reviewer" in role_codes
    assert "test-reviewer" in role_codes
    helper_templates = {item["code"]: item.get("template_key", "") for item in payload["roles"]}
    helper_tools = {item["code"]: item.get("allowed_tools", []) for item in payload["roles"]}
    assert helper_templates["ai-helper"] == "codex_helper"
    assert helper_templates["incident-analyst"] == "codex_helper"
    assert helper_templates["orchestration-helper"] == "codex_helper"
    assert helper_templates["data-ai-engineer"] == "codex_helper"
    assert helper_templates["summary-reviewer"] == "reviewer"
    assert helper_templates["test-reviewer"] == "reviewer"
    assert helper_templates["architect"] == "planner"
    assert helper_tools["architect"] == ["research_search", "repo_search", "memory_search"]
    assert helper_tools["ai-helper"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["incident-analyst"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["orchestration-helper"] == ["log_lookup", "repo_search", "memory_search"]
    assert helper_tools["data-ai-engineer"] == ["log_lookup", "repo_search", "memory_search"]
