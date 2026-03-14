"""Dashboard HTML shell and plain-text log view helpers."""

from __future__ import annotations

from pathlib import Path
import re

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.store import JobStore


class DashboardViewRuntime:
    """Encapsulate dashboard HTML shell rendering and log file serving."""

    _LOG_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")

    def __init__(
        self,
        *,
        store: JobStore | None,
        templates: Jinja2Templates,
        artifact_runtime: DashboardJobArtifactRuntime,
    ) -> None:
        self.store = store
        self.templates = templates
        self.artifact_runtime = artifact_runtime

    def render_dashboard_shell(self, request: Request) -> HTMLResponse:
        """Render the top-level dashboard shell."""

        return self.templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "AgentHub Jobs",
            },
        )

    def render_job_detail_page(self, request: Request, job_id: str) -> HTMLResponse:
        """Render details and quick links for one job."""

        if self.store is None:
            raise HTTPException(status_code=500, detail="dashboard view runtime store is not configured")
        job = self.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

        return self.templates.TemplateResponse(
            request,
            "job_detail.html",
            {
                "job": job,
                "title": f"Job {job_id}",
            },
        )

    def read_log_file(self, *, file_name: str, channel: str = "debug") -> str:
        """Read one validated dashboard log file as UTF-8 text."""

        if not self._LOG_NAME_PATTERN.match(file_name):
            raise HTTPException(
                status_code=400,
                detail="Invalid log file name. Use only letters, numbers, dot, dash, underscore.",
            )
        target_path = self.artifact_runtime.resolve_channel_log_path(file_name, channel=channel)
        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"Log file not found: {file_name}")
        return target_path.read_text(encoding="utf-8")

    def log_file_response(self, *, file_name: str, channel: str = "debug") -> PlainTextResponse:
        """Return one plain-text log response for dashboard log links."""

        return PlainTextResponse(self.read_log_file(file_name=file_name, channel=channel))

    @classmethod
    def stop_signal_path(cls, data_dir: Path, job_id: str) -> Path:
        """Return stop signal file path for one job."""

        return data_dir / "control" / f"stop_{job_id}.flag"
