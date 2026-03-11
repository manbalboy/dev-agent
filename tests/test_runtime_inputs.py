"""Unit tests for runtime input draft suggestions."""

from __future__ import annotations

from app.runtime_inputs import suggest_runtime_input_drafts


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
