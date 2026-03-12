from __future__ import annotations

from app.dead_letter_policy import build_dead_letter_summary


def test_build_dead_letter_summary_includes_actions_and_flags() -> None:
    summary = build_dead_letter_summary(
        failure_class="test_failure",
        provider_hint="test_runner",
        stage_family="test",
        reason_code="dead_letter",
        reason="dead-letter after retry budget exhausted: snapshot mismatch",
        source="job_failure_runtime",
        generated_at="2026-03-12T00:00:00+00:00",
        details={"upstream_recovery_status": ""},
    )

    assert summary["active"] is True
    assert summary["failure_class"] == "test_failure"
    assert summary["manual_resume_recommended"] is True
    assert summary["retry_from_scratch_recommended"] is True
    assert summary["recommended_actions"]
