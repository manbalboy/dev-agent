from __future__ import annotations

from pathlib import Path

from app.models import JobRecord
from app.store import JobStore


class JobControlRuntime:
    """Own small job control helpers used across orchestration loops."""

    def __init__(self, *, store: JobStore, data_dir: Path) -> None:
        self.store = store
        self.data_dir = data_dir

    def stop_signal_path(self, job_id: str) -> Path:
        """Return path of stop signal file for one job."""

        return self.data_dir / "control" / f"stop_{job_id}.flag"

    def is_stop_requested(self, job_id: str) -> bool:
        """Check whether user requested graceful stop for this job."""

        return self.stop_signal_path(job_id).exists()

    def clear_stop_requested(self, job_id: str) -> None:
        """Remove stop signal file after graceful termination."""

        path = self.stop_signal_path(job_id)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def normalize_agent_profile(profile: str) -> str:
        """Normalize active AI profile string."""

        return str(profile or "primary").strip() or "primary"

    def require_job(self, job_id: str) -> JobRecord:
        """Return job or raise a clear error."""

        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        return job
