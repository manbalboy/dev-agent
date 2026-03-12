from __future__ import annotations

import json
from pathlib import Path

from app.config import AppSettings
from app.models import JobRecord, utc_now_iso
from app.runtime_recovery_trace import append_runtime_recovery_trace, append_runtime_recovery_trace_for_job


def _build_settings(tmp_path: Path) -> AppSettings:
    data_dir = tmp_path / "data"
    workspace_dir = tmp_path / "workspaces"
    command_config = tmp_path / "ai_commands.json"
    command_config.write_text("{}\n", encoding="utf-8")
    settings = AppSettings(
        webhook_secret="test-secret",
        allowed_repository="owner/repo",
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        max_retries=3,
        test_command="echo test",
        test_command_secondary="echo test",
        test_command_implement="echo test implement",
        test_command_fix="echo test fix",
        test_command_secondary_implement="echo test implement secondary",
        test_command_secondary_fix="echo test fix secondary",
        tester_primary_name="gpt",
        tester_secondary_name="gemini",
        command_config=command_config,
        worker_poll_seconds=1,
        worker_stale_running_seconds=600,
        worker_max_auto_recoveries=2,
        default_branch="main",
        enable_escalation=False,
        enable_stage_md_commits=True,
        api_port=8321,
        store_backend="json",
        sqlite_file=data_dir / "agenthub.db",
        docker_preview_enabled=False,
    )
    settings.ensure_directories()
    return settings


def _make_job(job_id: str = "job-trace") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_title="trace",
        issue_url="https://github.com/owner/repo/issues/1",
        status="running",
        stage="implement_with_codex",
        attempt=2,
        max_attempts=3,
        branch_name="agenthub/default/issue-1",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code="default",
        source_repository="owner/repo",
    )


def test_append_runtime_recovery_trace_rolls_latest_events(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    for index in range(22):
        append_runtime_recovery_trace(
            repository_path,
            source="worker_stale_recovery",
            reason_code="stale_heartbeat",
            reason=f"stale {index}",
            decision="requeue",
            stage="implement_with_codex",
            job_id="job-trace",
            attempt=1,
            recovery_status="auto_recovered",
            recovery_count=index + 1,
            details={"stale_seconds": 1800 + index},
        )

    payload = json.loads((repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json").read_text(encoding="utf-8"))

    assert payload["event_count"] == 22
    assert len(payload["events"]) == 20
    assert payload["events"][0]["reason"] == "stale 2"
    assert payload["events"][-1]["reason"] == "stale 21"
    assert payload["events"][-1]["failure_class"] == "stale_heartbeat"
    assert payload["events"][-1]["provider_hint"] == "runtime"
    assert payload["events"][-1]["stage_family"] == "runtime_recovery"


def test_append_runtime_recovery_trace_for_job_uses_workspace_contract(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    job = _make_job("job-trace-contract")

    append_runtime_recovery_trace_for_job(
        settings,
        job,
        source="worker_stale_recovery",
        reason_code="stale_heartbeat",
        reason="heartbeat stale detected",
        decision="needs_human",
        recovery_status="needs_human",
        recovery_count=3,
        details={"stale_seconds": 2400},
    )

    trace_path = settings.repository_workspace_path("owner/repo", "default") / "_docs" / "RUNTIME_RECOVERY_TRACE.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))

    assert payload["event_count"] == 1
    assert payload["events"][0]["job_id"] == job.job_id
    assert payload["events"][0]["decision"] == "needs_human"
    assert payload["events"][0]["reason_code"] == "stale_heartbeat"
    assert payload["events"][0]["failure_class"] == "stale_heartbeat"
    assert payload["events"][0]["provider_hint"] == "runtime"
    assert payload["events"][0]["stage_family"] == "runtime_recovery"
    assert payload["events"][0]["needs_human_summary"]["active"] is True
    assert payload["events"][0]["needs_human_summary"]["failure_class"] == "stale_heartbeat"


def test_append_runtime_recovery_trace_builds_dead_letter_summary(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"

    append_runtime_recovery_trace(
        repository_path,
        source="job_failure_runtime",
        reason_code="dead_letter",
        reason="dead-letter after retry budget exhausted: snapshot mismatch",
        decision="dead_letter",
        stage="test_after_fix",
        job_id="job-dead-letter",
        attempt=3,
        recovery_status="dead_letter",
        recovery_count=0,
        details={"upstream_recovery_status": ""},
    )

    payload = json.loads((repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json").read_text(encoding="utf-8"))
    assert payload["events"][0]["decision"] == "dead_letter"
    assert payload["events"][0]["recovery_status"] == "dead_letter"
    assert payload["events"][0]["dead_letter_summary"]["active"] is True
    assert payload["events"][0]["dead_letter_summary"]["failure_class"] == "test_failure"


def test_append_runtime_recovery_trace_builds_provider_circuit_needs_human_summary(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"

    append_runtime_recovery_trace(
        repository_path,
        source="job_failure_runtime",
        reason_code="provider_timeout",
        reason="gemini provider circuit open after 6/6 provider_timeout failure(s)",
        decision="provider_circuit_open",
        stage="plan_with_gemini",
        job_id="job-provider-circuit",
        attempt=2,
        recovery_status="provider_circuit_open",
        recovery_count=0,
        details={
            "retry_policy": {
                "failure_class": "provider_timeout",
                "retry_budget": 2,
                "recovery_path": "provider_circuit_breaker",
                "cooldown_seconds": 120,
                "needs_human_recommended": False,
            }
        },
    )

    payload = json.loads((repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json").read_text(encoding="utf-8"))
    assert payload["events"][0]["decision"] == "provider_circuit_open"
    assert payload["events"][0]["recovery_status"] == "provider_circuit_open"
    assert payload["events"][0]["needs_human_summary"]["active"] is True
    assert payload["events"][0]["needs_human_summary"]["recovery_path"] == "provider_circuit_breaker"
    assert payload["events"][0]["needs_human_summary"]["failure_class"] == "provider_timeout"


def test_append_runtime_recovery_trace_builds_requeue_reason_summary(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"

    append_runtime_recovery_trace(
        repository_path,
        source="worker_stale_recovery",
        reason_code="stale_heartbeat",
        reason="running heartbeat stale detected after 1800s",
        decision="requeue",
        stage="implement_with_codex",
        job_id="job-requeue-trace",
        attempt=2,
        recovery_status="auto_recovered",
        recovery_count=1,
        details={"stale_seconds": 1800},
    )

    payload = json.loads((repository_path / "_docs" / "RUNTIME_RECOVERY_TRACE.json").read_text(encoding="utf-8"))
    summary = payload["events"][0]["requeue_reason_summary"]
    assert summary["active"] is True
    assert summary["source"] == "worker_stale_recovery"
    assert summary["trigger"] == "worker_restart_or_stale_recovery"
    assert summary["recovery_status"] == "auto_recovered"
