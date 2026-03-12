"""Workspace-scoped provider failure counter and cooldown helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.models import utc_now_iso
from app.workflow_resume import build_workflow_artifact_paths


_TRACKED_PROVIDER_HINTS = {"gemini", "codex", "github", "git", "mcp", "tool"}


def should_track_provider_failure(provider_hint: str) -> bool:
    """Return True when one provider hint should affect outage counters."""

    return str(provider_hint or "").strip().lower() in _TRACKED_PROVIDER_HINTS


def _parse_iso(value: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_provider_failure_counters(repository_path: Path) -> Dict[str, Any]:
    """Read provider failure counter artifact for one workspace."""

    path = build_workflow_artifact_paths(repository_path)["provider_failure_counters"]
    payload = _load_payload(path)
    providers = payload.get("providers", {}) if isinstance(payload.get("providers"), dict) else {}
    return {
        "path": str(path),
        "generated_at": str(payload.get("generated_at", "")).strip(),
        "latest_updated_at": str(payload.get("latest_updated_at", "")).strip(),
        "providers": providers,
    }


def evaluate_workspace_provider_quarantine(
    repository_path: Path,
    *,
    provider_hint: str,
) -> Dict[str, Any]:
    """Evaluate quarantine state for one provider using workspace counters."""

    normalized_provider = str(provider_hint or "").strip().lower()
    payload = read_provider_failure_counters(repository_path)
    snapshot = (
        payload.get("providers", {}).get(normalized_provider, {})
        if isinstance(payload.get("providers"), dict)
        else {}
    )
    quarantine = evaluate_provider_quarantine(
        provider_hint=normalized_provider,
        failure_class=str(snapshot.get("last_failure_class", "")).strip(),
        counter_snapshot=snapshot,
    )
    quarantine["path"] = str(payload.get("path", "")).strip()
    return quarantine


def evaluate_workspace_provider_circuit_breaker(
    repository_path: Path,
    *,
    provider_hint: str,
) -> Dict[str, Any]:
    """Evaluate circuit-breaker state for one provider using workspace counters."""

    normalized_provider = str(provider_hint or "").strip().lower()
    payload = read_provider_failure_counters(repository_path)
    snapshot = (
        payload.get("providers", {}).get(normalized_provider, {})
        if isinstance(payload.get("providers"), dict)
        else {}
    )
    circuit = evaluate_provider_circuit_breaker(
        provider_hint=normalized_provider,
        failure_class=str(snapshot.get("last_failure_class", "")).strip(),
        counter_snapshot=snapshot,
    )
    circuit["path"] = str(payload.get("path", "")).strip()
    return circuit


def record_provider_failure(
    repository_path: Path,
    *,
    provider_hint: str,
    failure_class: str,
    stage_family: str,
    reason_code: str,
    reason: str,
    job_id: str = "",
    attempt: int = 0,
    occurrence_key: str = "",
) -> Dict[str, Any]:
    """Append one provider failure into the workspace counter artifact."""

    normalized_provider = str(provider_hint or "").strip().lower()
    if not should_track_provider_failure(normalized_provider):
        return {}

    now = utc_now_iso()
    path = build_workflow_artifact_paths(repository_path)["provider_failure_counters"]
    payload = _load_payload(path)
    providers = payload.get("providers", {}) if isinstance(payload.get("providers"), dict) else {}
    item = providers.get(normalized_provider, {}) if isinstance(providers.get(normalized_provider), dict) else {}
    recent_failures = item.get("recent_failures", []) if isinstance(item.get("recent_failures"), list) else []
    if recent_failures:
        latest = recent_failures[-1] if isinstance(recent_failures[-1], dict) else {}
        normalized_occurrence_key = str(occurrence_key or "").strip()
        latest_occurrence_key = str(latest.get("occurrence_key", "")).strip()
        if (
            str(latest.get("job_id", "")).strip() == str(job_id or "").strip()
            and int(latest.get("attempt", 0) or 0) == int(attempt or 0)
            and str(latest.get("reason_code", "")).strip() == str(reason_code or "").strip()
            and str(latest.get("stage_family", "")).strip() == str(stage_family or "").strip()
            and latest_occurrence_key == normalized_occurrence_key
        ):
            return item
    recent_failures.append(
        {
            "generated_at": now,
            "failure_class": str(failure_class or "").strip(),
            "stage_family": str(stage_family or "").strip(),
            "reason_code": str(reason_code or "").strip(),
            "job_id": str(job_id or "").strip(),
            "attempt": int(attempt or 0),
            "occurrence_key": str(occurrence_key or "").strip(),
        }
    )
    recent_failures = recent_failures[-10:]
    updated_item = {
        "provider_hint": normalized_provider,
        "total_failures": int(item.get("total_failures", 0) or 0) + 1,
        "recent_failure_count": len(recent_failures),
        "last_failure_class": str(failure_class or "").strip(),
        "last_stage_family": str(stage_family or "").strip(),
        "last_reason_code": str(reason_code or "").strip(),
        "last_reason": str(reason or "").strip(),
        "last_job_id": str(job_id or "").strip(),
        "last_attempt": int(attempt or 0),
        "last_failed_at": now,
        "recent_failures": recent_failures,
    }
    providers[normalized_provider] = updated_item
    result = {
        "generated_at": str(payload.get("generated_at", "")).strip() or now,
        "latest_updated_at": now,
        "providers": providers,
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated_item


def evaluate_provider_cooldown(
    *,
    provider_hint: str,
    failure_class: str,
    counter_snapshot: Dict[str, Any] | None,
    retry_policy: Dict[str, Any] | None,
    now_iso: str | None = None,
) -> Dict[str, Any]:
    """Return cooldown decision from one provider counter snapshot and retry policy."""

    snapshot = counter_snapshot if isinstance(counter_snapshot, dict) else {}
    policy = retry_policy if isinstance(retry_policy, dict) else {}
    normalized_class = str(failure_class or "").strip()
    normalized_provider = str(provider_hint or "").strip().lower()
    recent_failure_count = int(snapshot.get("recent_failure_count", 0) or 0)

    threshold = 0
    if normalized_class in {"provider_quota", "provider_auth"}:
        threshold = 1
    elif normalized_class in {"provider_timeout", "tool_failure"}:
        threshold = 2

    cooldown_seconds = int(policy.get("cooldown_seconds", 0) or 0)
    if threshold <= 0 or cooldown_seconds <= 0 or recent_failure_count < threshold:
        return {
            "active": False,
            "provider_hint": normalized_provider,
            "failure_class": normalized_class,
            "recent_failure_count": recent_failure_count,
            "threshold": threshold,
            "cooldown_seconds": cooldown_seconds,
            "remaining_seconds": 0,
            "last_failed_at": str(snapshot.get("last_failed_at", "")).strip(),
        }

    current_time = _parse_iso(str(now_iso or utc_now_iso()))
    last_failed_at = _parse_iso(str(snapshot.get("last_failed_at", "")).strip())
    if current_time is None or last_failed_at is None:
        return {
            "active": False,
            "provider_hint": normalized_provider,
            "failure_class": normalized_class,
            "recent_failure_count": recent_failure_count,
            "threshold": threshold,
            "cooldown_seconds": cooldown_seconds,
            "remaining_seconds": 0,
            "last_failed_at": str(snapshot.get("last_failed_at", "")).strip(),
        }

    elapsed_seconds = max(0, int((current_time - last_failed_at).total_seconds()))
    remaining_seconds = max(0, cooldown_seconds - elapsed_seconds)
    return {
        "active": remaining_seconds > 0,
        "provider_hint": normalized_provider,
        "failure_class": normalized_class,
        "recent_failure_count": recent_failure_count,
        "threshold": threshold,
        "cooldown_seconds": cooldown_seconds,
        "remaining_seconds": remaining_seconds,
        "last_failed_at": str(snapshot.get("last_failed_at", "")).strip(),
    }


def evaluate_provider_quarantine(
    *,
    provider_hint: str,
    failure_class: str,
    counter_snapshot: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Return quarantine decision from one provider counter snapshot."""

    snapshot = counter_snapshot if isinstance(counter_snapshot, dict) else {}
    normalized_class = str(failure_class or "").strip()
    normalized_provider = str(provider_hint or "").strip().lower()
    recent_failure_count = int(snapshot.get("recent_failure_count", 0) or 0)

    threshold = 0
    if normalized_class in {"provider_timeout", "tool_failure"}:
        threshold = 4

    return {
        "active": threshold > 0 and recent_failure_count >= threshold,
        "provider_hint": normalized_provider,
        "failure_class": normalized_class,
        "recent_failure_count": recent_failure_count,
        "threshold": threshold,
        "last_failed_at": str(snapshot.get("last_failed_at", "")).strip(),
    }


def evaluate_provider_circuit_breaker(
    *,
    provider_hint: str,
    failure_class: str,
    counter_snapshot: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Return circuit-breaker decision from one provider counter snapshot."""

    snapshot = counter_snapshot if isinstance(counter_snapshot, dict) else {}
    normalized_class = str(failure_class or "").strip()
    normalized_provider = str(provider_hint or "").strip().lower()
    recent_failure_count = int(snapshot.get("recent_failure_count", 0) or 0)

    threshold = 0
    if normalized_class in {"provider_timeout", "tool_failure"}:
        threshold = 6

    return {
        "active": threshold > 0 and recent_failure_count >= threshold,
        "provider_hint": normalized_provider,
        "failure_class": normalized_class,
        "recent_failure_count": recent_failure_count,
        "threshold": threshold,
        "last_failed_at": str(snapshot.get("last_failed_at", "")).strip(),
    }


def format_provider_cooldown_reason(cooldown: Dict[str, Any] | None) -> str:
    """Build a compact human-readable reason for one active cooldown."""

    payload = cooldown if isinstance(cooldown, dict) else {}
    provider_hint = str(payload.get("provider_hint", "")).strip() or "provider"
    failure_class = str(payload.get("failure_class", "")).strip() or "unknown_runtime"
    remaining_seconds = int(payload.get("remaining_seconds", 0) or 0)
    recent_failure_count = int(payload.get("recent_failure_count", 0) or 0)
    threshold = int(payload.get("threshold", 0) or 0)
    return (
        f"{provider_hint} cooldown active for {remaining_seconds}s "
        f"after {recent_failure_count}/{threshold} {failure_class} failure(s)"
    )


def format_provider_quarantine_reason(quarantine: Dict[str, Any] | None) -> str:
    """Build a compact human-readable reason for one active provider quarantine."""

    payload = quarantine if isinstance(quarantine, dict) else {}
    provider_hint = str(payload.get("provider_hint", "")).strip() or "provider"
    failure_class = str(payload.get("failure_class", "")).strip() or "unknown_runtime"
    recent_failure_count = int(payload.get("recent_failure_count", 0) or 0)
    threshold = int(payload.get("threshold", 0) or 0)
    return (
        f"{provider_hint} provider quarantined after "
        f"{recent_failure_count}/{threshold} {failure_class} failure(s)"
    )


def format_provider_circuit_breaker_reason(circuit: Dict[str, Any] | None) -> str:
    """Build a compact human-readable reason for one active provider circuit-breaker."""

    payload = circuit if isinstance(circuit, dict) else {}
    provider_hint = str(payload.get("provider_hint", "")).strip() or "provider"
    failure_class = str(payload.get("failure_class", "")).strip() or "unknown_runtime"
    recent_failure_count = int(payload.get("recent_failure_count", 0) or 0)
    threshold = int(payload.get("threshold", 0) or 0)
    return (
        f"{provider_hint} provider circuit open after "
        f"{recent_failure_count}/{threshold} {failure_class} failure(s)"
    )
