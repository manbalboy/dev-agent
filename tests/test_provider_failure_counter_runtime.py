from __future__ import annotations

import json
from pathlib import Path

from app.provider_failure_counter_runtime import (
    evaluate_provider_circuit_breaker,
    evaluate_provider_cooldown,
    evaluate_provider_quarantine,
    evaluate_workspace_provider_circuit_breaker,
    evaluate_workspace_provider_quarantine,
    format_provider_circuit_breaker_reason,
    format_provider_cooldown_reason,
    format_provider_quarantine_reason,
    read_provider_failure_counters,
    record_provider_failure,
    should_track_provider_failure,
)


def test_should_track_provider_failure_limits_to_provider_like_hints() -> None:
    assert should_track_provider_failure("codex") is True
    assert should_track_provider_failure("gemini") is True
    assert should_track_provider_failure("runtime") is False
    assert should_track_provider_failure("test_runner") is False


def test_record_provider_failure_accumulates_recent_and_total_counts(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    for attempt in range(1, 4):
        record_provider_failure(
            repository_path,
            provider_hint="codex",
            failure_class="provider_quota",
            stage_family="implementation",
            reason_code="provider_quota",
            reason="402 quota exceeded",
            job_id="job-counter",
            attempt=attempt,
        )

    payload = read_provider_failure_counters(repository_path)
    codex = payload["providers"]["codex"]
    assert codex["total_failures"] == 3
    assert codex["recent_failure_count"] == 3
    assert codex["last_failure_class"] == "provider_quota"
    assert codex["last_attempt"] == 3
    assert len(codex["recent_failures"]) == 3


def test_record_provider_failure_skips_unknown_provider(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    result = record_provider_failure(
        repository_path,
        provider_hint="unknown",
        failure_class="unknown_runtime",
        stage_family="unknown",
        reason_code="unknown_runtime",
        reason="unknown",
    )

    assert result == {}
    path = repository_path / "_docs" / "PROVIDER_FAILURE_COUNTERS.json"
    assert not path.exists()


def test_record_provider_failure_dedupes_same_job_attempt_reason(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    first = record_provider_failure(
        repository_path,
        provider_hint="codex",
        failure_class="provider_timeout",
        stage_family="implementation",
        reason_code="provider_timeout",
        reason="request timeout",
        job_id="job-dedupe",
        attempt=2,
    )
    second = record_provider_failure(
        repository_path,
        provider_hint="codex",
        failure_class="provider_timeout",
        stage_family="implementation",
        reason_code="provider_timeout",
        reason="request timeout again",
        job_id="job-dedupe",
        attempt=2,
    )

    payload = read_provider_failure_counters(repository_path)
    codex = payload["providers"]["codex"]
    assert codex["total_failures"] == 1
    assert codex["recent_failure_count"] == 1
    assert first["last_attempt"] == 2
    assert second["last_attempt"] == 2


def test_evaluate_provider_cooldown_activates_for_repeated_timeout() -> None:
    cooldown = evaluate_provider_cooldown(
        provider_hint="gemini",
        failure_class="provider_timeout",
        counter_snapshot={
            "recent_failure_count": 2,
            "last_failed_at": "2026-03-12T00:00:00+00:00",
        },
        retry_policy={"cooldown_seconds": 120},
        now_iso="2026-03-12T00:00:30+00:00",
    )

    assert cooldown["active"] is True
    assert cooldown["remaining_seconds"] == 90
    assert cooldown["threshold"] == 2
    assert "gemini cooldown active" in format_provider_cooldown_reason(cooldown)


def test_evaluate_provider_quarantine_activates_for_provider_burst() -> None:
    quarantine = evaluate_provider_quarantine(
        provider_hint="codex",
        failure_class="provider_timeout",
        counter_snapshot={
            "recent_failure_count": 4,
            "last_failed_at": "2026-03-12T00:00:00+00:00",
        },
    )

    assert quarantine["active"] is True
    assert quarantine["threshold"] == 4
    assert "codex provider quarantined" in format_provider_quarantine_reason(quarantine)


def test_evaluate_workspace_provider_quarantine_reads_latest_workspace_counter(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    for attempt in range(1, 5):
        record_provider_failure(
            repository_path,
            provider_hint="gemini",
            failure_class="provider_timeout",
            stage_family="planning",
            reason_code="provider_timeout",
            reason="request timeout",
            job_id="job-gemini-burst",
            attempt=attempt,
        )

    quarantine = evaluate_workspace_provider_quarantine(
        repository_path,
        provider_hint="gemini",
    )

    assert quarantine["active"] is True
    assert quarantine["provider_hint"] == "gemini"
    assert quarantine["failure_class"] == "provider_timeout"
    assert quarantine["path"].endswith("PROVIDER_FAILURE_COUNTERS.json")


def test_evaluate_provider_circuit_breaker_activates_after_extended_burst() -> None:
    circuit = evaluate_provider_circuit_breaker(
        provider_hint="codex",
        failure_class="provider_timeout",
        counter_snapshot={
            "recent_failure_count": 6,
            "last_failed_at": "2026-03-12T00:00:00+00:00",
        },
    )

    assert circuit["active"] is True
    assert circuit["threshold"] == 6
    assert "codex provider circuit open" in format_provider_circuit_breaker_reason(circuit)


def test_evaluate_workspace_provider_circuit_breaker_reads_latest_workspace_counter(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    for attempt in range(1, 7):
        record_provider_failure(
            repository_path,
            provider_hint="gemini",
            failure_class="provider_timeout",
            stage_family="planning",
            reason_code="provider_timeout",
            reason="request timeout",
            job_id="job-gemini-circuit",
            attempt=attempt,
        )

    circuit = evaluate_workspace_provider_circuit_breaker(
        repository_path,
        provider_hint="gemini",
    )

    assert circuit["active"] is True
    assert circuit["provider_hint"] == "gemini"
    assert circuit["failure_class"] == "provider_timeout"
    assert circuit["path"].endswith("PROVIDER_FAILURE_COUNTERS.json")
