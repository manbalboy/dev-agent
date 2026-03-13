from __future__ import annotations

from pathlib import Path

from app.job_mode_runtime import JobModeRuntime
from app.models import JobRecord


def _job(*, track: str | None = None, title: str = "제목") -> JobRecord:
    return JobRecord(
        job_id="job-1",
        repository="manbalboy/dev-agent",
        issue_number=1,
        issue_title=title,
        issue_url="https://example.com/issues/1",
        status="queued",
        stage="queued",
        attempt=0,
        max_attempts=3,
        pr_url=None,
        error_message=None,
        log_file="job-1.log",
        created_at="2026-03-13T00:00:00Z",
        updated_at="2026-03-13T00:00:00Z",
        started_at=None,
        finished_at=None,
        branch_name="agenthub/test",
        track=track or "enhance",
    )


def test_job_mode_runtime_reads_escalation_toggle_from_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text('AGENTHUB_ENABLE_ESCALATION="true"\n', encoding="utf-8")

    runtime = JobModeRuntime(
        default_enable_escalation=False,
        env_path=env_path,
        environ={},
    )

    assert runtime.is_escalation_enabled() is True


def test_job_mode_runtime_reads_recovery_toggle_from_environment() -> None:
    runtime = JobModeRuntime(
        default_enable_escalation=False,
        environ={"AGENTHUB_RECOVERY_MODE": "off"},
    )

    assert runtime.is_recovery_mode_enabled() is False


def test_job_mode_runtime_detects_long_ultra_tracks() -> None:
    assert JobModeRuntime.is_long_track(_job(track="long")) is True
    assert JobModeRuntime.is_long_track(_job(title="[장기] 작업")) is True
    assert JobModeRuntime.is_ultra_track(_job(track="ultra")) is True
    assert JobModeRuntime.is_ultra_track(_job(title="[초장기] 작업")) is True
    assert JobModeRuntime.is_ultra10_track(_job(track="ultra10")) is True
    assert JobModeRuntime.is_ultra10_track(_job(title="[초초장기] 작업")) is True
