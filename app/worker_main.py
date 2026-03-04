"""Standalone worker process for queue consumption."""

from __future__ import annotations

import sys
import time

from app.command_runner import CommandTemplateRunner
from app.config import AppSettings
from app.models import JobStatus
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
