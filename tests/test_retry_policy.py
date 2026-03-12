from __future__ import annotations

from app.retry_policy import resolve_retry_policy, should_retry_attempt


def test_resolve_retry_policy_fast_fails_provider_quota() -> None:
    policy = resolve_retry_policy(
        failure_class="provider_quota",
        provider_hint="codex",
        stage_family="implementation",
        default_retry_budget=3,
    )

    assert policy.retry_budget == 1
    assert policy.recovery_path == "needs_human_candidate"
    assert policy.needs_human_recommended is True
    assert policy.cooldown_seconds == 900


def test_resolve_retry_policy_short_retries_provider_timeout() -> None:
    policy = resolve_retry_policy(
        failure_class="provider_timeout",
        provider_hint="gemini",
        stage_family="planning",
        default_retry_budget=5,
    )

    assert policy.retry_budget == 2
    assert policy.recovery_path == "short_retry"
    assert policy.needs_human_recommended is False
    assert policy.cooldown_seconds == 120


def test_resolve_retry_policy_keeps_auth_as_needs_human_with_short_cooldown() -> None:
    policy = resolve_retry_policy(
        failure_class="provider_auth",
        provider_hint="codex",
        stage_family="implementation",
        default_retry_budget=3,
    )

    assert policy.retry_budget == 1
    assert policy.recovery_path == "needs_human_candidate"
    assert policy.needs_human_recommended is True
    assert policy.cooldown_seconds == 300


def test_resolve_retry_policy_preserves_fix_loop_for_test_failures() -> None:
    policy = resolve_retry_policy(
        failure_class="test_failure",
        provider_hint="test_runner",
        stage_family="test",
        default_retry_budget=4,
    )

    assert policy.retry_budget == 4
    assert policy.recovery_path == "fix_loop"


def test_should_retry_attempt_uses_budget_boundary() -> None:
    assert should_retry_attempt(attempt=1, retry_budget=2) is True
    assert should_retry_attempt(attempt=2, retry_budget=2) is False
