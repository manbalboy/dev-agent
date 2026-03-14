"""FastAPI application entrypoint for AgentHub."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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
    if resolved_settings.cors_allow_all:
        allow_origins = ["*"]
    else:
        allow_origins = [
            origin.strip()
            for origin in resolved_settings.cors_origins.split(",")
            if origin.strip()
        ] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False if "*" in allow_origins else True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def enforce_https_middleware(request: Request, call_next):
        """Optionally reject non-HTTPS requests outside local health checks."""

        if not resolved_settings.enforce_https or request.url.path == "/healthz":
            return await call_next(request)

        scheme = str(request.url.scheme or "").lower()
        if resolved_settings.trust_x_forwarded_proto:
            forwarded = str(request.headers.get("x-forwarded-proto", "") or "").split(",")[0].strip().lower()
            if forwarded:
                scheme = forwarded
        if scheme != "https":
            return JSONResponse(
                {
                    "status": "https_required",
                    "detail": "HTTPS is required for this endpoint.",
                },
                status_code=426,
            )
        return await call_next(request)

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
