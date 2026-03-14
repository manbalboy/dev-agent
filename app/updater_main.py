"""Standalone updater service entrypoint."""

from __future__ import annotations

import sys
import time

from app.config import AppSettings
from app.models import utc_now_iso
from app.patch_backup_runtime import PatchBackupRuntime
from app.patch_health_runtime import PatchHealthRuntime
from app.patch_rollback_runtime import PatchRollbackRuntime
from app.patch_service_runtime import PatchServiceRuntime
from app.patch_updater_runtime import PatchUpdaterRuntime
from app.store import create_job_store


def run_updater_forever() -> None:
    """Run the updater loop forever with lightweight heartbeat/status updates."""

    settings = AppSettings.from_env()
    store = create_job_store(settings)
    patch_service_runtime = PatchServiceRuntime(
        store=store,
        patch_lock_file=settings.patch_lock_file,
        api_service_name=settings.patch_api_service_name,
        worker_service_name=settings.patch_worker_service_name,
        utc_now_iso=utc_now_iso,
    )
    patch_health_runtime = PatchHealthRuntime(
        store=store,
        patch_service_runtime=patch_service_runtime,
        api_health_url=f"http://127.0.0.1:{settings.api_port}/healthz",
        updater_status_file=settings.patch_updater_status_file,
        updater_service_name=settings.patch_updater_service_name,
        utc_now_iso=utc_now_iso,
    )
    patch_rollback_runtime = PatchRollbackRuntime(
        utc_now_iso=utc_now_iso,
    )
    patch_backup_runtime = PatchBackupRuntime(
        backups_dir=settings.patch_backups_dir,
        data_root=settings.data_dir,
        state_files={
            "jobs": settings.jobs_file,
            "queue": settings.queue_file,
            "node_runs": settings.data_dir / "node_runs.json",
            "runtime_inputs": settings.data_dir / "runtime_inputs.json",
            "integrations": settings.data_dir / "integrations.json",
            "patch_runs": settings.data_dir / "patch_runs.json",
            "sqlite": settings.sqlite_file,
        },
        utc_now_iso=utc_now_iso,
    )
    runtime = PatchUpdaterRuntime(
        store=store,
        status_file=settings.patch_updater_status_file,
        service_name=settings.patch_updater_service_name,
        utc_now_iso=utc_now_iso,
        patch_service_runtime=patch_service_runtime,
        patch_health_runtime=patch_health_runtime,
        patch_rollback_runtime=patch_rollback_runtime,
        patch_backup_runtime=patch_backup_runtime,
    )

    print("[updater] started")
    while True:
        try:
            result = runtime.run_once()
            status_payload = result.get("status") or {}
            active_patch_run_id = str(status_payload.get("active_patch_run_id") or "").strip()
            if active_patch_run_id:
                print(f"[updater] tracking patch run {active_patch_run_id}")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # pragma: no cover - defensive loop
            print(f"[updater] unexpected error: {exc}", file=sys.stderr)
        time.sleep(settings.patch_updater_poll_seconds)


def main() -> None:
    """CLI entrypoint for the updater service."""

    run_updater_forever()


if __name__ == "__main__":
    main()
