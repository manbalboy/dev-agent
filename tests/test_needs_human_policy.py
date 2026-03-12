from __future__ import annotations

from app.needs_human_policy import build_needs_human_summary


def test_build_needs_human_summary_provider_quota_includes_actions_and_cooldown() -> None:
    summary = build_needs_human_summary(
        failure_class="provider_quota",
        provider_hint="codex",
        stage_family="implementation",
        reason_code="provider_quota",
        reason="402 You have no quota remaining",
        recovery_path="needs_human_candidate",
        source="job_failure_runtime",
        details={
            "effective_retry_budget": 1,
            "retry_policy": {
                "recovery_path": "needs_human_candidate",
                "retry_budget": 1,
                "cooldown_seconds": 900,
            }
        },
    )

    assert summary["active"] is True
    assert summary["failure_class"] == "provider_quota"
    assert summary["cooldown_seconds"] == 900
    assert summary["manual_resume_recommended"] is True
    assert any("quota" in item.lower() for item in summary["recommended_actions"])


def test_build_needs_human_summary_unknown_runtime_falls_back_to_generic_text() -> None:
    summary = build_needs_human_summary(
        failure_class="",
        provider_hint="",
        stage_family="",
        reason_code="",
        reason="unexpected runtime stop",
        recovery_path="manual_handoff",
        source="job_record",
        details={},
    )

    assert summary["failure_class"] == "unknown_runtime"
    assert summary["title"] == "운영자 확인 필요"
    assert summary["recovery_path"] == "manual_handoff"
