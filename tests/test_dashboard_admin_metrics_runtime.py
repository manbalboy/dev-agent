from __future__ import annotations

from pathlib import Path

from app.config import AppSettings
from app.dashboard_admin_metrics_runtime import DashboardAdminMetricsRuntime
from app.models import JobRecord, utc_now_iso


class _Store:
    def __init__(self, jobs) -> None:
        self._jobs = list(jobs)

    def list_jobs(self):
        return list(self._jobs)

    def list_runtime_inputs(self):
        return []


def _build_settings(tmp_path: Path) -> AppSettings:
    data_dir = tmp_path / "data"
    workspace_dir = tmp_path / "workspaces"
    command_config = tmp_path / "ai_commands.json"
    command_config.write_text("{}\n", encoding="utf-8")
    settings = AppSettings(
        webhook_secret="test-secret",
        allowed_repository="owner/repo",
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        max_retries=3,
        test_command="echo test",
        test_command_secondary="echo test",
        test_command_implement="echo test implement",
        test_command_fix="echo test fix",
        test_command_secondary_implement="echo test implement secondary",
        test_command_secondary_fix="echo test fix secondary",
        tester_primary_name="gpt",
        tester_secondary_name="gemini",
        command_config=command_config,
        worker_poll_seconds=1,
        worker_stale_running_seconds=600,
        worker_max_auto_recoveries=2,
        default_branch="main",
        enable_escalation=False,
        enable_stage_md_commits=True,
        api_port=8321,
        store_backend="json",
        sqlite_file=data_dir / "agenthub.db",
        docker_preview_enabled=False,
    )
    settings.ensure_directories()
    return settings


def _make_job(job_id: str, *, updated_at: str, app_code: str = "default") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_url="https://github.com/owner/repo/issues/1",
        issue_title=f"Issue {job_id}",
        branch_name=f"agenthub/default/{job_id}",
        attempt=1,
        max_attempts=3,
        log_file=f"{job_id}.log",
        pr_url="",
        app_code=app_code,
        status="failed",
        stage="review_with_gemini",
        error_message="",
        created_at=now,
        updated_at=updated_at,
        started_at=now,
        finished_at=None,
        source_repository="owner/repo",
    )


def _top_counter_items(counter, *, limit=5):
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
        if str(name).strip()
    ]


def test_build_admin_assistant_diagnosis_metrics_aggregates_recent_traces(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    newer = _make_job("job-new", updated_at="2026-03-12T05:00:00+00:00", app_code="maps")
    older = _make_job("job-old", updated_at="2026-03-12T04:00:00+00:00", app_code="chat")
    store = _Store([older, newer])
    traces = {
        newer.job_id: {
            "enabled": True,
            "generated_at": "2026-03-12T05:01:00+00:00",
            "assistant_scope": "log_analysis",
            "question": "최근 실패 원인 분석",
            "trace_path": "/tmp/trace-new.json",
            "combined_context_length": 320,
            "tool_runs": [
                {"tool": "log_lookup", "ok": True, "mode": "internal"},
                {"tool": "memory_search", "ok": False, "mode": "fallback", "error": "timeout"},
            ],
        },
        older.job_id: {
            "enabled": True,
            "generated_at": "2026-03-12T04:01:00+00:00",
            "assistant_scope": "chat",
            "question": "이전 실패 요약",
            "trace_path": "/tmp/trace-old.json",
            "combined_context_length": 180,
            "tool_runs": [
                {"tool": "log_lookup", "ok": True, "mode": "internal"},
            ],
        },
    }
    runtime = DashboardAdminMetricsRuntime(
        store=store,
        settings=settings,
        feature_flags_config_path=tmp_path / "flags.json",
        apps_config_path=tmp_path / "apps.json",
        workflows_config_path=tmp_path / "workflows.json",
        roles_config_path=tmp_path / "roles.json",
        list_dashboard_jobs=lambda current_store, current_settings: [],
        build_job_summary=lambda jobs: {},
        read_default_workflow_id=lambda path: "",
        read_registered_apps=lambda path, repository, **kwargs: [],
        read_roles_payload=lambda path: {},
        get_memory_runtime_store=lambda current_settings: None,
        read_dashboard_json=lambda path: {},
        read_dashboard_jsonl=lambda path: [],
        job_workspace_path=lambda job, current_settings: current_settings.workspace_dir / job.job_id,
        read_job_assistant_diagnosis_trace=lambda job, current_settings: traces.get(job.job_id, {}),
        top_counter_items=_top_counter_items,
        safe_average=lambda values: None,
        latest_non_empty=lambda values: max([item for item in values if item], default=""),
        utc_now_iso=utc_now_iso,
    )

    payload = runtime.build_admin_assistant_diagnosis_metrics()

    assert payload["active"] is True
    assert payload["trace_count"] == 2
    assert payload["latest_generated_at"] == "2026-03-12T05:01:00+00:00"
    assert payload["scope_counts"][0]["name"] == "chat" or payload["scope_counts"][0]["name"] == "log_analysis"
    assert payload["tool_counts"][0]["name"] == "log_lookup"
    assert payload["failed_tool_counts"][0]["name"] == "memory_search"
    assert payload["recent_traces"][0]["job_id"] == "job-new"
    assert payload["recent_traces"][0]["failed_tool_count"] == 1
    assert payload["recent_traces"][0]["tool_runs"][1]["error"] == "timeout"
