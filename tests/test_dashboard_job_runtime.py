from __future__ import annotations

from pathlib import Path
import json

from app.config import AppSettings
from app.dashboard_job_runtime import DashboardJobRuntime
from app.models import IntegrationRegistryRecord, JobRecord, RuntimeInputRecord, utc_now_iso
from app.workflow_resume import build_workflow_artifact_paths


class _Store:
    def __init__(self, runtime_inputs=None, integration_entries=None) -> None:
        self._runtime_inputs = list(runtime_inputs or [])
        self._integration_entries = list(integration_entries or [])

    def list_runtime_inputs(self):
        return list(self._runtime_inputs)

    def list_integration_registry_entries(self):
        return list(self._integration_entries)


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


def test_read_job_runtime_recovery_trace_enriches_failure_metadata(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    job = _build_job()
    workspace = settings.repository_workspace_path(job.repository, job.app_code)
    trace_path = build_workflow_artifact_paths(workspace)["runtime_recovery_trace"]
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-03-14T09:01:00+00:00",
                "latest_event_at": "2026-03-14T09:02:00+00:00",
                "event_count": 1,
                "events": [
                    {
                        "generated_at": "2026-03-14T09:02:00+00:00",
                        "source": "worker_startup_sweep",
                        "reason_code": "stale_heartbeat",
                        "reason": "stale heartbeat detected",
                        "stage": "implement_with_codex",
                        "decision": "needs_human",
                        "needs_human_summary": {
                            "active": True,
                            "recovery_path": "needs_human_candidate",
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    runtime = DashboardJobRuntime(
        store=None,
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    payload = runtime.read_job_runtime_recovery_trace(job)

    assert payload["trace_path"] == str(trace_path)
    assert payload["event_count"] == 1
    assert payload["latest_failure_class"] == "stale_heartbeat"
    assert payload["latest_provider_hint"] == "codex"
    assert payload["latest_stage_family"] == "implementation"
    assert payload["latest_needs_human_summary"]["recovery_path"] == "needs_human_candidate"
    assert payload["events"][0]["provider_hint"] == "codex"


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
    assert payload["blocked_count"] == 0
    assert payload["available_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["resolved_inputs"][0]["value"] == ""
    assert payload["resolved_inputs"][0]["display_value"] != ""
    assert payload["pending_inputs"][0]["env_var_name"] == "GOOGLE_PLACES_DATASET"


def test_build_job_operator_inputs_surfaces_blocked_env_by_integration_policy(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    now = utc_now_iso()
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
        requested_at=now,
        updated_at=now,
    )
    integration = IntegrationRegistryRecord(
        integration_id="google_maps",
        display_name="Google Maps",
        category="mapping",
        supported_app_types=["web", "app"],
        tags=["maps"],
        required_env_keys=["GOOGLE_MAPS_API_KEY"],
        optional_env_keys=[],
        operator_guide_markdown="",
        implementation_guide_markdown="",
        verification_notes="",
        approval_required=True,
        enabled=True,
        created_at=now,
        updated_at=now,
        approval_status="pending",
    )
    runtime = DashboardJobRuntime(
        store=_Store(runtime_inputs=[runtime_input], integration_entries=[integration]),
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    payload = runtime.build_job_operator_inputs(_build_job())

    assert payload["available_count"] == 0
    assert payload["blocked_count"] == 1
    assert payload["blocked_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["blocked_inputs"][0]["env_var_name"] == "GOOGLE_MAPS_API_KEY"
    assert payload["blocked_inputs"][0]["bridge_allowed"] is False
    assert "운영자 승인" in payload["blocked_inputs"][0]["bridge_reason"]


def test_build_job_integration_operator_boundary_surfaces_failed_job_gate(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    now = utc_now_iso()
    job = _build_job()
    job.status = "failed"
    job.recovery_status = "needs_human"
    runtime_input = RuntimeInputRecord(
        request_id="ri-1",
        repository="owner/repo",
        app_code="app",
        job_id=job.job_id,
        scope="job",
        key="google_maps_api_key",
        label="Google Maps API Key",
        description="maps",
        value_type="secret",
        env_var_name="GOOGLE_MAPS_API_KEY",
        sensitive=True,
        status="provided",
        value="secret-value",
        requested_at=now,
        updated_at=now,
    )
    integration = IntegrationRegistryRecord(
        integration_id="google_maps",
        display_name="Google Maps",
        category="mapping",
        supported_app_types=["web", "app"],
        tags=["maps"],
        required_env_keys=["GOOGLE_MAPS_API_KEY"],
        optional_env_keys=[],
        operator_guide_markdown="",
        implementation_guide_markdown="",
        verification_notes="",
        approval_required=True,
        enabled=True,
        created_at=now,
        updated_at=now,
        approval_status="pending",
    )
    runtime = DashboardJobRuntime(
        store=_Store(runtime_inputs=[runtime_input], integration_entries=[integration]),
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )
    workspace = settings.repository_workspace_path(job.repository, job.app_code)
    docs_path = workspace / "_docs"
    docs_path.mkdir(parents=True, exist_ok=True)
    (docs_path / "INTEGRATION_RECOMMENDATIONS.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "integration_id": "google_maps",
                        "display_name": "Google Maps",
                        "recommendation_status": "operator_review_and_input_required",
                        "input_readiness_status": "approval_required",
                        "input_readiness_reason": "승인이 필요합니다.",
                        "approval_status": "pending",
                        "approval_required": True,
                        "required_env_keys": ["GOOGLE_MAPS_API_KEY"],
                        "reason": "지도 기능 후보",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = runtime.build_job_integration_operator_boundary(job)

    assert payload["active"] is True
    assert payload["boundary_status"] == "approval_and_input_required"
    assert payload["candidate_count"] == 1
    assert payload["blocked_input_count"] == 1
    assert payload["candidates"][0]["integration_id"] == "google_maps"
    assert payload["candidates"][0]["blocked_inputs"][0]["env_var_name"] == "GOOGLE_MAPS_API_KEY"


def test_build_job_integration_usage_trail_summarizes_recent_events(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    runtime = DashboardJobRuntime(
        store=None,
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )
    workspace = settings.repository_workspace_path("owner/repo", "app")
    docs_path = workspace / "_docs"
    docs_path.mkdir(parents=True, exist_ok=True)
    (docs_path / "INTEGRATION_USAGE_TRAIL.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "generated_at": "2026-03-13T00:00:00+00:00",
                        "stage": "plan_with_gemini",
                        "route": "planner",
                        "prompt_path": str(docs_path / "PLANNER_PROMPT.md"),
                        "integration_count": 1,
                        "blocked_integration_count": 0,
                        "blocked_env_vars": [],
                        "items": [
                            {
                                "integration_id": "google_maps",
                                "display_name": "Google Maps",
                                "usage_status": "prompt_injected",
                                "required_input_summary": {
                                    "provided_count": 1,
                                    "requested_count": 0,
                                    "missing_count": 0,
                                },
                            }
                        ],
                    },
                    {
                        "generated_at": "2026-03-13T00:05:00+00:00",
                        "stage": "implement_with_codex",
                        "route": "coder",
                        "prompt_path": str(docs_path / "CODER_PROMPT_IMPLEMENT.md"),
                        "integration_count": 1,
                        "blocked_integration_count": 1,
                        "blocked_env_vars": ["GOOGLE_MAPS_DATASET"],
                        "items": [
                            {
                                "integration_id": "google_maps",
                                "display_name": "Google Maps",
                                "usage_status": "prompt_injected",
                                "required_input_summary": {
                                    "provided_count": 1,
                                    "requested_count": 1,
                                    "missing_count": 0,
                                },
                            }
                        ],
                    },
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = runtime.build_job_integration_usage_trail(_build_job())

    assert payload["active"] is True
    assert payload["event_count"] == 2
    assert payload["used_integration_count"] == 1
    assert payload["used_integration_ids"] == ["google_maps"]
    assert payload["latest_event"]["route"] == "coder"
    assert payload["latest_event"]["blocked_env_vars"] == ["GOOGLE_MAPS_DATASET"]
    assert payload["recent_events"][1]["route"] == "planner"


def test_build_job_integration_health_facets_combines_missing_input_and_quota(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    runtime = DashboardJobRuntime(
        store=None,
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    payload = runtime.build_job_integration_health_facets(
        job=_build_job(),
        integration_operator_boundary={
            "active": True,
            "candidates": [
                {
                    "integration_id": "google_maps",
                    "display_name": "Google Maps",
                    "input_readiness_status": "input_required",
                    "input_readiness_reason": "필수 env가 아직 없습니다.",
                    "blocked_inputs": [
                        {
                            "env_var_name": "GOOGLE_MAPS_API_KEY",
                            "bridge_reason": "운영자 입력 필요",
                            "status": "missing",
                        }
                    ],
                }
            ],
        },
        integration_usage_trail={
            "active": True,
            "used_integration_ids": ["google_maps"],
            "latest_event": {"blocked_env_vars": ["GOOGLE_MAPS_API_KEY"]},
        },
        log_summary={
            "latest_auth_hint": {
                "message": "Codex CLI 사용량/쿼터 확인 필요",
            }
        },
        failure_classification={
            "failure_class": "provider_quota",
            "provider_hint": "codex",
            "stage_family": "implementation",
            "reason": "quota exceeded",
            "source": "job_record",
        },
    )

    assert payload["active"] is True
    assert payload["missing_input"]["active"] is True
    assert payload["missing_input"]["candidate_ids"] == ["google_maps"]
    assert payload["missing_input"]["blocked_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["missing_input"]["candidates"][0]["used_in_this_job"] is True
    assert payload["auth"]["active"] is False
    assert payload["quota"]["active"] is True
    assert payload["quota"]["provider_hint"] == "codex"


def test_build_job_self_growing_effectiveness_ignores_mismatched_job_artifact(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    job = _build_job()
    job.job_kind = "followup_backlog"
    job.parent_job_id = "job-parent"
    docs_dir = settings.repository_workspace_path(job.repository, job.app_code) / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "SELF_GROWING_EFFECTIVENESS.json").write_text(
        json.dumps(
            {
                "active": True,
                "job_id": "other-followup-job",
                "status": "improved",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    runtime = DashboardJobRuntime(
        store=_Store(),
        settings=settings,
        get_memory_runtime_store=lambda: None,
        compute_job_resume_state=lambda job, node_runs, runtime_settings: {},
        resolve_channel_log_path=lambda runtime_settings, file_name, channel="debug": runtime_settings.logs_dir / channel / file_name,
    )

    payload = runtime.build_job_self_growing_effectiveness(job)

    assert payload["active"] is False
    assert payload["expected"] is True
    assert payload["mismatched_job_artifact"] is True
    assert payload["artifact_job_id"] == "other-followup-job"
