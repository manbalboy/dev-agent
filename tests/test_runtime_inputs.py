"""Unit tests for runtime input draft suggestions."""

from __future__ import annotations

from app.models import IntegrationRegistryRecord, RuntimeInputRecord, utc_now_iso
from app.runtime_inputs import resolve_runtime_inputs, suggest_runtime_input_drafts


def test_suggest_runtime_input_drafts_matches_google_maps_keywords() -> None:
    drafts = suggest_runtime_input_drafts(
        context_text="Google Maps 지도와 places 검색 기능을 붙여야 해서 키가 필요함",
        repository="owner/repo",
        app_code="maps",
        job_id="job-maps",
    )

    assert drafts
    top = drafts[0]
    assert top["key"] == "google_maps_api_key"
    assert top["scope"] == "job"
    assert top["source"] == "matched"
    assert "google maps" in top["matched_keywords"]


def test_suggest_runtime_input_drafts_returns_featured_templates_without_context() -> None:
    drafts = suggest_runtime_input_drafts(
        context_text="",
        repository="owner/repo",
        app_code="billing",
        job_id="",
    )

    assert [item["key"] for item in drafts[:3]] == [
        "google_maps_api_key",
        "stripe_secret_key",
        "supabase_url",
    ]


def test_resolve_runtime_inputs_blocks_env_for_pending_integration() -> None:
    now = utc_now_iso()
    resolved = resolve_runtime_inputs(
        [
            RuntimeInputRecord(
                request_id="ri-1",
                repository="owner/repo",
                app_code="app",
                job_id="",
                scope="repository",
                key="google_maps_api_key",
                label="Google Maps API Key",
                description="지도",
                value_type="secret",
                env_var_name="GOOGLE_MAPS_API_KEY",
                sensitive=True,
                status="provided",
                value="secret-value",
                requested_at=now,
                updated_at=now,
            )
        ],
        repository="owner/repo",
        app_code="app",
        job_id="job-1",
        integration_registry_entries=[
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
                approval_status="pending",
            )
        ],
    )

    assert resolved["environment"] == {}
    assert resolved["blocked_environment"]["GOOGLE_MAPS_API_KEY"]
    assert resolved["blocked"][0]["bridge_allowed"] is False
    assert "승인" in resolved["blocked"][0]["bridge_reason"]


def test_resolve_runtime_inputs_allows_generic_env_without_registry_binding() -> None:
    now = utc_now_iso()
    resolved = resolve_runtime_inputs(
        [
            RuntimeInputRecord(
                request_id="ri-1",
                repository="owner/repo",
                app_code="app",
                job_id="",
                scope="repository",
                key="custom_feature_flag",
                label="Custom Feature Flag",
                description="generic",
                value_type="text",
                env_var_name="CUSTOM_FEATURE_FLAG",
                sensitive=False,
                status="provided",
                value="enabled",
                requested_at=now,
                updated_at=now,
            )
        ],
        repository="owner/repo",
        app_code="app",
        job_id="job-1",
        integration_registry_entries=[],
    )

    assert resolved["environment"] == {"CUSTOM_FEATURE_FLAG": "enabled"}
    assert resolved["blocked"] == []
