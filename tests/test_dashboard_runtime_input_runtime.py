from __future__ import annotations

from pathlib import Path

from app.config import AppSettings
from app.dashboard_runtime_input_runtime import DashboardRuntimeInputRuntime
from app.models import JobRecord, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore


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


def _make_job(job_id: str = "job-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_url="https://github.com/owner/repo/issues/77",
        issue_title="Google Maps 화면 구현",
        branch_name="agenthub/app/issue-77",
        attempt=1,
        max_attempts=3,
        log_file=f"{job_id}.log",
        pr_url="",
        app_code="maps",
        status="queued",
        stage="queued",
        error_message="",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        source_repository="owner/repo",
    )


def test_serialize_runtime_input_masks_secret_value(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    runtime = DashboardRuntimeInputRuntime(store=SQLiteJobStore(tmp_path / "jobs.db"), settings=settings)
    record = RuntimeInputRecord(
        request_id="runtime-input-1",
        repository="owner/repo",
        app_code="maps",
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

    payload = runtime.serialize_runtime_input(record)

    assert payload["status"] == "provided"
    assert payload["has_value"] is True
    assert payload["value"] == ""
    assert payload["display_value"] != ""
    assert payload["display_value"] != "secret-value"


def test_create_runtime_input_request_uses_job_context_and_normalizes_requested_by(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = SQLiteJobStore(tmp_path / "jobs.db")
    job = _make_job("job-maps")
    store.create_job(job)
    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)

    payload = runtime.create_runtime_input_request(
        repository="",
        app_code="",
        job_id=job.job_id,
        scope="job",
        key="google_maps_api_key",
        label="",
        description="지도 기능 구현에 필요",
        value_type="secret",
        env_var_name="",
        sensitive=False,
        placeholder="나중에 입력",
        note="assistant detected",
        requested_by="assistant_draft",
    )

    item = payload["item"]
    stored = store.get_runtime_input(item["request_id"])

    assert item["repository"] == "owner/repo"
    assert item["app_code"] == "maps"
    assert item["job_id"] == job.job_id
    assert item["scope"] == "job"
    assert item["requested_by"] == "assistant_draft"
    assert item["env_var_name"] == "GOOGLE_MAPS_API_KEY"
    assert stored is not None
    assert stored.status == "requested"


def test_provide_runtime_input_updates_status_and_timestamps(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)
    now = utc_now_iso()
    record = RuntimeInputRecord(
        request_id="runtime-input-2",
        repository="owner/repo",
        app_code="maps",
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
        requested_at=now,
        updated_at=now,
    )
    store.upsert_runtime_input(record)

    payload = runtime.provide_runtime_input(
        request_id="runtime-input-2",
        value="dataset-v1",
        note="operator provided",
    )

    item = payload["item"]
    stored = store.get_runtime_input("runtime-input-2")

    assert item["status"] == "provided"
    assert item["value"] == "dataset-v1"
    assert item["provided_at"] != ""
    assert stored is not None
    assert stored.status == "provided"
    assert stored.value == "dataset-v1"
