"""Class-aware retry policy selection helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Normalized retry policy for one failure class."""

    failure_class: str
    retry_budget: int
    recovery_path: str
    cooldown_seconds: int
    needs_human_recommended: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_retry_policy(
    *,
    failure_class: str,
    provider_hint: str = "",
    stage_family: str = "",
    default_retry_budget: int = 3,
) -> RetryPolicy:
    """Resolve a minimal class-aware retry policy."""

    normalized_class = str(failure_class or "").strip() or "unknown_runtime"
    normalized_provider = str(provider_hint or "").strip()
    normalized_stage_family = str(stage_family or "").strip()
    budget = max(1, int(default_retry_budget or 1))

    if normalized_class in {"provider_quota", "provider_auth", "workflow_contract"}:
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=1,
            recovery_path="needs_human_candidate",
            cooldown_seconds=900 if normalized_class == "provider_quota" else 300 if normalized_class == "provider_auth" else 0,
            needs_human_recommended=True,
        )
    if normalized_class == "provider_timeout":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=min(budget, 2),
            recovery_path="short_retry",
            cooldown_seconds=120,
            needs_human_recommended=False,
        )
    if normalized_class == "git_conflict":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=1,
            recovery_path="git_recovery",
            cooldown_seconds=0,
            needs_human_recommended=False,
        )
    if normalized_class == "tool_failure":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=min(budget, 2),
            recovery_path="tool_retry",
            cooldown_seconds=120,
            needs_human_recommended=False,
        )
    if normalized_class == "test_failure" or normalized_stage_family == "test":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=budget,
            recovery_path="fix_loop",
            cooldown_seconds=0,
            needs_human_recommended=False,
        )
    if normalized_class == "stale_heartbeat":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=1,
            recovery_path="worker_requeue",
            cooldown_seconds=0,
            needs_human_recommended=False,
        )
    if normalized_provider in {"github", "git"} and normalized_stage_family == "git_provider":
        return RetryPolicy(
            failure_class=normalized_class,
            retry_budget=1,
            recovery_path="git_recovery",
            cooldown_seconds=0,
            needs_human_recommended=False,
        )
    return RetryPolicy(
        failure_class=normalized_class,
        retry_budget=budget,
        recovery_path="standard_retry",
        cooldown_seconds=0,
        needs_human_recommended=False,
    )


def should_retry_attempt(*, attempt: int, retry_budget: int) -> bool:
    """Return True when one more retry is allowed under the selected budget."""

    return int(attempt or 0) < max(1, int(retry_budget or 1))
