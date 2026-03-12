from __future__ import annotations

from pathlib import Path

from app.config import AppSettings
from app.dashboard_job_runtime import DashboardJobRuntime
from app.models import JobRecord, RuntimeInputRecord, utc_now_iso


class _Store:
    def __init__(self, runtime_inputs=None) -> None:
        self._runtime_inputs = list(runtime_inputs or [])

    def list_runtime_inputs(self):
        return list(self._runtime_inputs)


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


def _build_job() -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id="job-1",
        repository="owner/repo",
        issue_number=17,
        issue_url="https://github.com/owner/repo/issues/17",
        issue_title="maps integration",
        branch_name="agenthub/app/issue-17",
        attempt=1,
        max_attempts=3,
        log_file="app--job-1.log",
        pr_url="",
        app_code="app",
        status="running",
        stage="implement_with_codex",
        error_message="",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        source_repository="owner/repo",
    )


def test_build_job_log_summary_tracks_latest_command_and_nonzero_done(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    debug_log_path = settings.logs_dir / "debug" / "app--job-1.log"
    user_log_path = settings.logs_dir / "user" / "app--job-1.log"
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    user_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text("debug\n", encoding="utf-8")
    user_log_path.write_text("user\n", encoding="utf-8")

    runtime = DashboardJobRuntime(
        store=None,
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    summary = runtime.build_job_log_summary(
        _build_job(),
        events=[
            {"kind": "run", "timestamp": "t1", "receiver": "codex", "message": "codex exec implement"},
            {"kind": "stderr", "timestamp": "t2", "speaker": "codex", "message": "quota exceeded"},
            {"kind": "done", "timestamp": "t3", "speaker": "codex", "message": "[DONE] exit_code=1 elapsed=2.40s"},
        ],
    )

    assert summary["event_count"] == 3
    assert summary["error_count"] == 2
    assert summary["nonzero_done_count"] == 1
    assert summary["latest_command"]["message"] == "codex exec implement"
    assert summary["latest_error"]["message"] == "[DONE] exit_code=1 elapsed=2.40s"
    assert summary["channels"]["debug"]["exists"] is True
    assert summary["channels"]["user"]["exists"] is True


def test_build_job_log_summary_downgrades_optional_helper_failures_and_extracts_auth_hint(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    debug_log_path = settings.logs_dir / "debug" / "app--job-1.log"
    user_log_path = settings.logs_dir / "user" / "app--job-1.log"
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    user_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text("debug\n", encoding="utf-8")
    user_log_path.write_text("user\n", encoding="utf-8")

    runtime = DashboardJobRuntime(
        store=None,
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    summary = runtime.build_job_log_summary(
        _build_job(),
        events=[
            {"kind": "run", "timestamp": "t1", "receiver": "TECH_WRITER", "message": "codex exec commit-summary"},
            {"kind": "done", "timestamp": "t2", "speaker": "TECH_WRITER", "message": "[DONE] exit_code=1 elapsed=2.06s"},
            {
                "kind": "info",
                "timestamp": "t3",
                "speaker": "TECH_WRITER",
                "message": (
                    "Commit summary route unavailable; using deterministic fallback: "
                    "commit_summary failed with exit code 1. Next action: run the logged command manually "
                    "in the same repository directory and verify CLI login/state. stderr preview: (no stderr output)"
                ),
            },
        ],
    )

    assert summary["error_count"] == 0
    assert summary["optional_error_count"] == 1
    assert summary["total_error_signal_count"] == 1
    assert summary["nonzero_done_count"] == 1
    assert summary["auth_hint_count"] == 1
    assert summary["latest_auth_hint"]["message"] == "Codex CLI 로그인/인증 상태 확인 필요"
    assert summary["latest_optional_error"]["message"] == "[DONE] exit_code=1 elapsed=2.06s"


def test_build_job_operator_inputs_returns_masked_env_inventory(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    runtime_input = RuntimeInputRecord(
        request_id="ri-1",
        repository="owner/repo",
        app_code="app",
        job_id="",
        scope="repository",
        key="google_maps_api_key",
        label="Google Maps API Key",
        description="maps",
        value_type="secret",
        env_var_name="GOOGLE_MAPS_API_KEY",
        sensitive=True,
        status="provided",
        value="secret-value",
        requested_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    pending_input = RuntimeInputRecord(
        request_id="ri-2",
        repository="owner/repo",
        app_code="app",
        job_id="",
        scope="repository",
        key="google_places_dataset",
        label="Google Places Dataset",
        description="dataset",
        value_type="text",
        env_var_name="GOOGLE_PLACES_DATASET",
        sensitive=False,
        status="requested",
        value="",
        requested_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    runtime = DashboardJobRuntime(
        store=_Store(runtime_inputs=[runtime_input, pending_input]),
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    payload = runtime.build_job_operator_inputs(_build_job())

    assert payload["available_count"] == 1
    assert payload["pending_count"] == 1
    assert payload["available_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["resolved_inputs"][0]["value"] == ""
    assert payload["resolved_inputs"][0]["display_value"] != ""
    assert payload["pending_inputs"][0]["env_var_name"] == "GOOGLE_PLACES_DATASET"
