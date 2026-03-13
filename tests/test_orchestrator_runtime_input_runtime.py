from __future__ import annotations

import json
from pathlib import Path

from app.models import IntegrationRegistryRecord, JobRecord, JobStatus, RuntimeInputRecord, utc_now_iso
from app.orchestrator_runtime_input_runtime import OrchestratorRuntimeInputRuntime
from app.runtime_inputs import normalize_env_var_name, resolve_runtime_inputs
from app.store import SQLiteJobStore


def _make_job(job_id: str = "job-runtime-input-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=21,
        issue_title="runtime input runtime",
        issue_url="https://github.com/owner/repo/issues/21",
        status=JobStatus.RUNNING.value,
        stage="plan_with_gemini",
        attempt=1,
        max_attempts=2,
        branch_name="agenthub/issue-21",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code="maps",
        source_repository="owner/repo",
    )


def _build_runtime(store: SQLiteJobStore) -> OrchestratorRuntimeInputRuntime:
    return OrchestratorRuntimeInputRuntime(
        store=store,
        resolve_runtime_inputs=resolve_runtime_inputs,
        normalize_env_var_name=normalize_env_var_name,
        utc_now_iso=utc_now_iso,
    )


def test_resolve_runtime_inputs_for_job_returns_blocked_environment(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
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
            operator_guide_markdown="guide",
            implementation_guide_markdown="impl",
            verification_notes="verify",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="pending",
            approval_note="",
            approval_updated_at=now,
            approval_updated_by="operator",
            approval_trail=[],
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="gmaps",
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
            value="secret",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )

    payload = _build_runtime(store).resolve_runtime_inputs_for_job(_make_job())

    assert payload["blocked_environment"]["GOOGLE_MAPS_API_KEY"]
    assert payload["environment"] == {}


def test_build_active_runtime_input_environment_normalizes_keys(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="plain",
            repository="owner/repo",
            app_code="maps",
            job_id="",
            scope="repository",
            key="maps key",
            label="Maps Key",
            description="maps",
            value_type="secret",
            env_var_name="maps key",
            sensitive=True,
            status="provided",
            value="secret",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )

    env = _build_runtime(store).build_active_runtime_input_environment(_make_job())

    assert env == {"MAPS_KEY": "secret"}


def test_write_operator_inputs_artifact_persists_prompt_safe_payload(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="supabase-url",
            repository="owner/repo",
            app_code="maps",
            job_id="",
            scope="repository",
            key="supabase_url",
            label="Supabase URL",
            description="url",
            value_type="text",
            env_var_name="SUPABASE_URL",
            sensitive=False,
            status="provided",
            value="https://example.supabase.co",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )
    artifact_path = tmp_path / "_docs" / "OPERATOR_INPUTS.json"

    payload = _build_runtime(store).write_operator_inputs_artifact(_make_job(), artifact_path)

    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["job_id"] == "job-runtime-input-runtime"
    assert saved["available_env_vars"] == ["SUPABASE_URL"]
