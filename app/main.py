"""FastAPI application entrypoint for AgentHub."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppSettings
from app.dashboard import router as dashboard_router
from app.github_webhook import router as webhook_router
from app.store import JobStore, create_job_store



def create_app(
    settings: AppSettings | None = None,
    store: JobStore | None = None,
) -> FastAPI:
    """Application factory used by both production and tests."""

    resolved_settings = settings or AppSettings.from_env()
    resolved_settings.ensure_directories()

    resolved_store = store or create_job_store(resolved_settings)

    app = FastAPI(title="AgentHub", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.store = resolved_store

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(webhook_router)
    app.include_router(dashboard_router)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Minimal health check endpoint."""

        return JSONResponse(
            {
                "status": "ok",
                "allowed_repository": resolved_settings.allowed_repository,
                "api_port": resolved_settings.api_port,
            }
        )

    return app


app = create_app()
