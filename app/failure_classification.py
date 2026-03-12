"""Normalized runtime failure classification helpers."""

from __future__ import annotations

from typing import Any, Mapping

from app.models import JobRecord


FAILURE_CLASS_UNKNOWN = "unknown_runtime"

_REASON_CODE_CLASS_MAP = {
    "stale_heartbeat": "stale_heartbeat",
    "workflow_unavailable": "workflow_contract",
    "workflow_contract": "workflow_contract",
    "hard_gate_timeout": "test_failure",
    "provider_quota": "provider_quota",
    "provider_auth": "provider_auth",
    "provider_timeout": "provider_timeout",
    "git_conflict": "git_conflict",
    "test_failure": "test_failure",
    "tool_failure": "tool_failure",
    "unknown_runtime": "unknown_runtime",
}

_STAGE_PROVIDER_HINT_MAP = {
    "plan_with_gemini": "gemini",
    "review_with_gemini": "gemini",
    "design_with_codex": "codex",
    "documentation_with_claude": "codex",
    "copywriter_task": "codex",
    "documentation_task": "codex",
    "implement_with_codex": "codex",
    "fix_with_codex": "codex",
    "summarize_code_changes": "codex",
    "push_branch": "git",
    "create_pr": "github",
    "prepare_repo": "git",
    "read_issue": "github",
    "commit_implement": "git",
    "commit_fix": "git",
    "test_after_implement": "test_runner",
    "test_after_fix": "test_runner",
    "ux_e2e_review": "test_runner",
    "product_review": "gemini",
    "improvement_stage": "runtime",
}

_WORKFLOW_PATTERNS = (
    "workflow unavailable",
    "invalid workflow",
    "missing workflow",
    "unsupported node",
    "failed_node_missing_from_workflow",
    "manual_target_invalid",
    "manual_target_not_found",
    "workflow contract",
)

_GIT_PATTERNS = (
    "non-fast-forward",
    "merge conflict",
    "failed to push",
    "cannot lock ref",
    "would be overwritten",
    "needs merge",
    "rejected",
    "git conflict",
    "gh pr create",
    "gh pr edit",
)

_PROVIDER_QUOTA_PATTERNS = (
    "quota",
    "rate limit",
    "rate-limit",
    "usage limit",
    "capacity reached",
    "too many requests",
    "http 429",
    " 429 ",
    "http 402",
    " 402 ",
)

_PROVIDER_AUTH_PATTERNS = (
    "unauthorized",
    "forbidden",
    "invalid api key",
    "invalid key",
    "missing api key",
    "missing token",
    "authentication failed",
    "auth failed",
    "token expired",
    "http 401",
    "http 403",
    " 401 ",
    " 403 ",
)

_PROVIDER_TIMEOUT_PATTERNS = (
    "timed out",
    "timeout",
    "deadline exceeded",
    "context deadline exceeded",
    "took too long",
    "request timeout",
)

_TEST_PATTERNS = (
    "test failed",
    "tests failed",
    "pytest",
    "playwright",
    "vitest",
    "jest",
    "snapshot mismatch",
    "e2e failed",
)

_TOOL_PATTERNS = (
    "tool_request",
    "tool failed",
    "mcp",
    "search-api",
    "tool runtime",
)

_PROVIDER_AUTH_HINTS = {"gemini", "codex", "github"}
_PROVIDER_QUOTA_HINTS = {"gemini", "codex", "github"}
_PROVIDER_TIMEOUT_HINTS = {"gemini", "codex", "github", "mcp", "tool"}


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def infer_stage_family(*, stage: str = "", source: str = "") -> str:
    """Infer a coarse stage family from stage/source evidence."""

    stage_lower = str(stage or "").strip().lower()
    source_lower = str(source or "").strip().lower()

    if "worker_stale_recovery" in source_lower:
        return "runtime_recovery"
    if stage_lower in {"push_branch", "create_pr", "prepare_repo", "read_issue", "commit_implement", "commit_fix"}:
        return "git_provider"
    if stage_lower.startswith("test_") or stage_lower == "ux_e2e_review":
        return "test"
    if stage_lower in {"review_with_gemini", "product_review"}:
        return "review"
    if stage_lower in {"implement_with_codex", "fix_with_codex"}:
        return "implementation"
    if stage_lower in {
        "plan_with_gemini",
        "architecture_planning",
        "idea_to_product_brief",
        "generate_user_flows",
        "define_mvp_scope",
        "project_scaffolding",
        "write_spec",
    }:
        return "planning"
    if stage_lower in {"design_with_codex", "copywriter_task", "documentation_task", "documentation_with_claude"}:
        return "content"
    if "workflow" in source_lower:
        return "workflow"
    if "tool" in source_lower or "mcp" in source_lower:
        return "tool"
    if "recovery_runtime" in source_lower:
        return "runtime_recovery"
    return "unknown"


def infer_provider_hint(
    *,
    stage: str = "",
    source: str = "",
    reason: str = "",
    error_message: str = "",
    details: Mapping[str, Any] | None = None,
) -> str:
    """Infer which provider/runtime area the failure likely belongs to."""

    stage_lower = str(stage or "").strip().lower()
    source_lower = str(source or "").strip().lower()
    if "worker_stale_recovery" in source_lower:
        return "runtime"
    if stage_lower in _STAGE_PROVIDER_HINT_MAP:
        return _STAGE_PROVIDER_HINT_MAP[stage_lower]

    detail_parts = []
    if details:
        for key in ("provider", "assistant", "route", "tool_name", "tool", "command"):
            value = details.get(key)
            if value:
                detail_parts.append(str(value))
    text = " ".join(
        [
            source_lower,
            str(reason or ""),
            str(error_message or ""),
            " ".join(detail_parts),
        ]
    ).lower()

    if "recovery_runtime" in source_lower:
        return "runtime"
    if "gemini" in text:
        return "gemini"
    if "codex" in text:
        return "codex"
    if "claude" in text or "copilot" in text:
        return "codex"
    if "github" in text or "gh " in text or " gh" in text:
        return "github"
    if "git" in text or "origin/" in text:
        return "git"
    if "mcp" in text:
        return "mcp"
    if "tool" in text or "search-api" in text:
        return "tool"
    return "unknown"


def classify_failure(
    *,
    reason_code: str = "",
    reason: str = "",
    stage: str = "",
    error_message: str = "",
    source: str = "",
    details: Mapping[str, Any] | None = None,
) -> str:
    """Map runtime failure evidence into one normalized failure class."""

    normalized_reason_code = str(reason_code or "").strip().lower()
    if normalized_reason_code in _REASON_CODE_CLASS_MAP:
        return _REASON_CODE_CLASS_MAP[normalized_reason_code]

    stage_lower = str(stage or "").strip().lower()
    source_lower = str(source or "").strip().lower()
    stage_family = infer_stage_family(stage=stage_lower, source=source_lower)
    provider_hint = infer_provider_hint(
        stage=stage_lower,
        source=source_lower,
        reason=reason,
        error_message=error_message,
        details=details,
    )

    details_text = ""
    if details:
        details_text = " ".join(f"{key}={value}" for key, value in details.items())
    text = " ".join(
        [
            normalized_reason_code,
            str(reason or ""),
            str(error_message or ""),
            stage_lower,
            source_lower,
            stage_family,
            provider_hint,
            details_text,
        ]
    ).lower()

    if "stale heartbeat" in text or "heartbeat stale" in text:
        return "stale_heartbeat"
    if _contains_any(text, _WORKFLOW_PATTERNS):
        return "workflow_contract"
    if _contains_any(text, _GIT_PATTERNS):
        return "git_conflict"
    if _contains_any(text, _PROVIDER_QUOTA_PATTERNS) and provider_hint in _PROVIDER_QUOTA_HINTS:
        return "provider_quota"
    if _contains_any(text, _PROVIDER_AUTH_PATTERNS) and provider_hint in _PROVIDER_AUTH_HINTS:
        return "provider_auth"
    if _contains_any(text, _PROVIDER_TIMEOUT_PATTERNS) and provider_hint in _PROVIDER_TIMEOUT_HINTS:
        return "provider_timeout"
    if stage_family == "git_provider" and (
        "push" in text or "pr " in text or "pull request" in text or "git " in text or "origin/" in text
    ):
        return "git_conflict"
    if stage_family == "test" or _contains_any(text, _TEST_PATTERNS):
        return "test_failure"
    if stage_family == "tool" or "tool" in source_lower or _contains_any(text, _TOOL_PATTERNS):
        return "tool_failure"
    return FAILURE_CLASS_UNKNOWN


def build_failure_evidence_summary(
    *,
    reason_code: str = "",
    reason: str = "",
    stage: str = "",
    error_message: str = "",
    source: str = "",
    generated_at: str = "",
    details: Mapping[str, Any] | None = None,
    failure_class: str = "",
) -> dict[str, Any]:
    """Build a normalized failure evidence payload with mapping metadata."""

    stage_family = infer_stage_family(stage=stage, source=source)
    provider_hint = infer_provider_hint(
        stage=stage,
        source=source,
        reason=reason,
        error_message=error_message,
        details=details,
    )
    normalized_failure_class = str(failure_class or "").strip() or classify_failure(
        reason_code=reason_code,
        reason=reason,
        stage=stage,
        error_message=error_message,
        source=source,
        details=details,
    )
    return {
        "failure_class": normalized_failure_class,
        "provider_hint": provider_hint,
        "stage_family": stage_family,
        "reason_code": str(reason_code or "").strip(),
        "reason": str(reason or "").strip(),
        "stage": str(stage or "").strip(),
        "source": str(source or "").strip(),
        "generated_at": str(generated_at or "").strip(),
    }


def classify_runtime_recovery_event(event: Mapping[str, Any]) -> str:
    """Classify one runtime recovery trace event."""

    return classify_failure(
        reason_code=str(event.get("reason_code", "")),
        reason=str(event.get("reason", "")),
        stage=str(event.get("stage", "")),
        source=str(event.get("source", "")),
        details=event.get("details") if isinstance(event.get("details"), Mapping) else None,
    )


def build_failure_classification_summary(
    *,
    job: JobRecord,
    runtime_recovery_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one normalized failure classification summary for dashboard/API use."""

    trace_events = []
    if runtime_recovery_trace and isinstance(runtime_recovery_trace.get("events"), list):
        trace_events = runtime_recovery_trace.get("events", [])
    if trace_events:
        latest = trace_events[-1]
        if isinstance(latest, Mapping):
            evidence = build_failure_evidence_summary(
                reason_code=str(latest.get("reason_code", "")),
                reason=str(latest.get("reason", "")),
                stage=str(latest.get("stage", "")),
                source=str(latest.get("source", "")),
                generated_at=str(latest.get("generated_at", "")),
                details=latest.get("details") if isinstance(latest.get("details"), Mapping) else None,
                failure_class=str(latest.get("failure_class", "")),
            )
            evidence["source"] = "runtime_recovery_trace"
            return evidence

    evidence_text = " ".join(
        [
            str(job.recovery_reason or ""),
            str(job.error_message or ""),
            str(job.recovery_status or ""),
        ]
    ).strip()
    if not evidence_text and str(job.status or "").strip().lower() != "failed":
        return {}
    return build_failure_evidence_summary(
        reason=str(job.recovery_reason or job.error_message or ""),
        stage=str(job.stage or ""),
        error_message=str(job.error_message or ""),
        source="job_record",
    )
