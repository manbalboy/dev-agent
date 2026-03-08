"""Standalone worker process for queue consumption."""

from __future__ import annotations

from datetime import datetime, timezone
import sys
import time

from app.command_runner import CommandTemplateRunner
from app.config import AppSettings
from app.models import JobStage, JobStatus
from app.orchestrator import Orchestrator
from app.store import JobStore, create_job_store


def _recover_orphan_queued_jobs(store: JobStore) -> int:
    """Re-enqueue queued jobs when queue became empty unexpectedly."""

    if store.queue_size() != 0:
        return 0

    recovered = 0
    for job in store.list_jobs():
        if job.status == JobStatus.QUEUED.value:
            store.enqueue_job(job.job_id)
            recovered += 1
    return recovered


def _recover_stale_running_jobs(store: JobStore, settings: AppSettings) -> int:
    """Auto-recover running jobs whose heartbeat has gone stale."""

    now = datetime.now(timezone.utc)
    recovered = 0
    for job in store.list_jobs():
        if job.status != JobStatus.RUNNING.value:
            continue
        heartbeat_raw = (job.heartbeat_at or job.updated_at or "").strip()
        if not heartbeat_raw:
            continue
        try:
            heartbeat_at = datetime.fromisoformat(heartbeat_raw)
        except ValueError:
            continue
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
        stale_seconds = (now - heartbeat_at).total_seconds()
        if stale_seconds < settings.worker_stale_running_seconds:
            continue

        next_recovery_count = int(job.recovery_count or 0) + 1
        reason = (
            "running heartbeat stale detected "
            f"after {int(stale_seconds)}s at stage={job.stage} attempt={job.attempt}"
        )
        if next_recovery_count > settings.worker_max_auto_recoveries:
            store.update_job(
                job.job_id,
                status=JobStatus.FAILED.value,
                stage=JobStage.FAILED.value,
                error_message=reason,
                heartbeat_at=heartbeat_raw,
                recovery_status="needs_human",
                recovery_reason=reason,
                recovery_count=next_recovery_count,
                last_recovered_at=now.isoformat(),
                finished_at=now.isoformat(),
            )
            continue

        store.update_job(
            job.job_id,
            status=JobStatus.QUEUED.value,
            stage=JobStatus.QUEUED.value,
            error_message=reason,
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            recovery_status="auto_recovered",
            recovery_reason=reason,
            recovery_count=next_recovery_count,
            last_recovered_at=now.isoformat(),
        )
        store.enqueue_job(job.job_id)
        recovered += 1
    return recovered



def run_worker_forever() -> None:
    """Run a single worker loop forever.

    This process is intentionally separate from API server so one component can be
    restarted without interrupting the other.
    """

    settings = AppSettings.from_env()
    store = create_job_store(settings)
    template_runner = CommandTemplateRunner(settings.command_config)
    orchestrator = Orchestrator(settings, store, template_runner)

    print("[worker] started")
    while True:
        try:
            recovered_running = _recover_stale_running_jobs(store, settings)
            if recovered_running > 0:
                print(f"[worker] auto-recovered {recovered_running} stale running job(s)")
            processed = orchestrator.process_next_job()
            if not processed:
                recovered = _recover_orphan_queued_jobs(store)
                if recovered > 0:
                    print(f"[worker] recovered {recovered} orphan queued job(s)")
                    continue
                time.sleep(settings.worker_poll_seconds)
        except KeyboardInterrupt:
            print("[worker] stopped by keyboard interrupt")
            return
        except Exception as error:  # noqa: BLE001
            # Worker keeps running after unexpected errors so queue consumption does
            # not stop entirely because of one malformed job.
            print(f"[worker] unexpected error: {error}", file=sys.stderr)
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run_worker_forever()
