from __future__ import annotations

from pathlib import Path

from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.models import IntegrationRegistryRecord, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore


def test_save_entry_normalizes_fields_and_preserves_created_at(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = DashboardIntegrationRegistryRuntime(store=store)

    first = runtime.save_entry(
        integration_id="Google Maps",
        display_name="Google Maps",
        category="Mapping",
        supported_app_types=["web", "app", "web"],
        tags=["maps", "Maps", "places"],
        required_env_keys=["google_maps_api_key"],
        optional_env_keys=["google_maps_map_id"],
        operator_guide_markdown="운영자 가이드",
        implementation_guide_markdown="구현 가이드",
        verification_notes="지도 로딩 확인",
        approval_required=True,
        enabled=True,
    )
    created_at = first["item"]["created_at"]

    second = runtime.save_entry(
        integration_id="google_maps",
        display_name="Google Maps Platform",
        category="mapping",
        supported_app_types=["app"],
        tags=["maps"],
        required_env_keys=["GOOGLE_MAPS_API_KEY"],
        optional_env_keys=[],
        operator_guide_markdown="업데이트 가이드",
        implementation_guide_markdown="업데이트 구현 가이드",
        verification_notes="",
        approval_required=False,
        enabled=True,
    )

    assert first["item"]["integration_id"] == "google_maps"
    assert first["item"]["supported_app_types"] == ["web", "app"]
    assert first["item"]["required_env_keys"] == ["GOOGLE_MAPS_API_KEY"]
    assert second["item"]["created_at"] == created_at
    assert second["item"]["display_name"] == "Google Maps Platform"
    assert second["item"]["approval_required"] is False


def test_list_entries_filters_by_query_and_app_type(tmp_path: Path) -> None:
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
            operator_guide_markdown="",
            implementation_guide_markdown="",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="stripe",
            display_name="Stripe",
            category="payments",
            supported_app_types=["web", "api"],
            tags=["payments"],
            required_env_keys=["STRIPE_SECRET_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="",
            implementation_guide_markdown="",
            verification_notes="",
            approval_required=True,
            enabled=False,
            created_at=now,
            updated_at=now,
        )
    )
    runtime = DashboardIntegrationRegistryRuntime(store=store)

    filtered = runtime.list_entries(q="maps", category="mapping", app_type="app", enabled="true", limit=20)

    assert filtered["count"] == 1
    assert filtered["items"][0]["integration_id"] == "google_maps"


def test_list_entries_includes_required_runtime_input_link_summary(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY", "GOOGLE_MAPS_MAP_ID"],
            optional_env_keys=[],
            operator_guide_markdown="",
            implementation_guide_markdown="",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-gmaps-key",
            repository="owner/repo",
            app_code="default",
            job_id="",
            scope="repository",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 API 키",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="masked-secret",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )
    runtime = DashboardIntegrationRegistryRuntime(store=store)

    payload = runtime.list_entries(q="", category="", app_type="", enabled="", limit=20)

    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["required_input_summary"] == {
        "total": 2,
        "provided": 1,
        "requested": 0,
        "missing": 1,
    }
    assert item["input_readiness_status"] == "input_required"
    assert "운영자 입력이 필요합니다" in item["input_readiness_reason"]
    assert item["required_input_links"][0]["env_var_name"] == "GOOGLE_MAPS_API_KEY"
    assert item["required_input_links"][0]["status"] == "provided"
    assert item["required_input_links"][0]["latest_request"]["label"] == "Google Maps API Key"
    assert item["required_input_links"][1]["env_var_name"] == "GOOGLE_MAPS_MAP_ID"
    assert item["required_input_links"][1]["status"] == "missing"


def test_set_approval_action_updates_status_and_reason(tmp_path: Path) -> None:
    store = SQLiteJobStore(tmp_path / "jobs.db")
    runtime = DashboardIntegrationRegistryRuntime(store=store)
    created = runtime.save_entry(
        integration_id="google_maps",
        display_name="Google Maps",
        category="mapping",
        supported_app_types=["web"],
        tags=["maps"],
        required_env_keys=["GOOGLE_MAPS_API_KEY"],
        optional_env_keys=[],
        operator_guide_markdown="",
        implementation_guide_markdown="",
        verification_notes="",
        approval_required=True,
        enabled=True,
    )

    rejected = runtime.set_approval_action(
        integration_id="google_maps",
        action="reject",
        note="지금은 지도 기능을 넣지 않습니다.",
        acted_by="dashboard_operator",
    )
    rejected_item = rejected["item"]
    assert created["item"]["approval_status"] == "pending"
    assert rejected_item["approval_status"] == "rejected"
    assert rejected_item["input_readiness_status"] == "approval_rejected"
    assert "지도 기능을 넣지 않습니다" in rejected_item["input_readiness_reason"]
    assert rejected_item["approval_updated_by"] == "dashboard_operator"
    assert rejected_item["approval_trail_count"] == 1
    assert rejected_item["approval_trail"][0]["action"] == "reject"
    assert rejected_item["approval_trail"][0]["previous_status"] == "pending"

    approved = runtime.set_approval_action(
        integration_id="google_maps",
        action="approve",
        note="도입 승인",
        acted_by="dashboard_operator",
    )
    approved_item = approved["item"]
    assert approved_item["approval_status"] == "approved"
    assert approved_item["input_readiness_status"] == "input_required"
    assert approved_item["approval_trail_count"] == 2
    assert approved_item["approval_trail"][0]["action"] == "approve"
    assert approved_item["approval_trail"][1]["action"] == "reject"
