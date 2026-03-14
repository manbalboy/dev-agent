"""Job/read-oriented dashboard routers extracted from the main dashboard module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dependencies import get_settings, get_store
from app.store import JobStore


router = APIRouter(tags=["dashboard"])
_DEFAULT_DASHBOARD_PAGE_SIZE = 20
_MAX_DASHBOARD_PAGE_SIZE = 100


def _dashboard_module():
    """Import dashboard lazily to preserve existing builder/helper contracts."""

    import app.dashboard as dashboard

    return dashboard


class WorkflowManualRetryRequest(BaseModel):
    """Payload for manual workflow rerun/resume from dashboard."""

    mode: str = Field(min_length=1, max_length=40)
    node_id: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)


class DeadLetterRetryRequest(BaseModel):
    """Payload for requeueing one dead-lettered job from dashboard."""

    note: str = Field(default="", max_length=300)


@router.get("/", response_class=HTMLResponse)
def job_list_page(
    request: Request,
    settings: AppSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render dashboard shell."""

    dashboard = _dashboard_module()
    return dashboard._build_dashboard_view_runtime(None, settings).render_dashboard_shell(request)


@router.get("/api/jobs", response_class=JSONResponse)
def jobs_api(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_DEFAULT_DASHBOARD_PAGE_SIZE, ge=1, le=_MAX_DASHBOARD_PAGE_SIZE),
    status: str = Query(default="", max_length=32),
    track: str = Query(default="", max_length=32),
    app_code: str = Query(default="", max_length=32),
    stage: str = Query(default="", max_length=64),
    recovery_status: str = Query(default="", max_length=64),
    strategy: str = Query(default="", max_length=64),
    q: str = Query(default="", max_length=200),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return filtered/paginated jobs as JSON for dashboard polling."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_job_list_runtime(store, settings).list_jobs_payload(
            page=page,
            page_size=page_size,
            status=status,
            track=track,
            app_code=app_code,
            stage=stage,
            recovery_status=recovery_status,
            strategy=strategy,
            q=q,
        )
    )


@router.get("/api/admin/metrics", response_class=JSONResponse)
def admin_metrics_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return read-only admin metrics for dashboard management view."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_admin_metrics_runtime(store, settings).build_admin_metrics())


@router.get("/api/jobs/options", response_class=JSONResponse)
def job_options_api(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return compact job options for combobox-style selectors."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_job_list_runtime(store, settings).get_job_options_payload(q=q, limit=limit))


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(
    job_id: str,
    request: Request,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render details and quick links for one job."""

    dashboard = _dashboard_module()
    return dashboard._build_dashboard_view_runtime(store, settings).render_job_detail_page(request, job_id)


@router.get("/api/jobs/{job_id}", response_class=JSONResponse)
def job_detail_api(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one job plus parsed log conversation events and agent artifacts."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_job_detail_runtime(store, settings).get_job_detail_payload(job_id))


@router.get("/api/jobs/{job_id}/node-runs", response_class=JSONResponse)
def job_node_runs_api(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return persisted workflow node execution records for one job."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_job_detail_runtime(store, settings).get_job_node_runs_payload(job_id))


@router.post("/api/jobs/{job_id}/stop", response_class=JSONResponse)
def request_job_stop(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Request graceful stop for one running ultra job."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_job_action_runtime(store, settings).request_job_stop(job_id)
    return JSONResponse(payload)


@router.post("/api/jobs/{job_id}/requeue", response_class=JSONResponse)
def requeue_job(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Requeue one failed job from dashboard."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_job_action_runtime(store, settings).requeue_job(job_id)
    return JSONResponse(payload)


@router.post("/api/jobs/{job_id}/dead-letter/retry", response_class=JSONResponse)
def retry_dead_letter_job(
    job_id: str,
    payload: DeadLetterRetryRequest | None = None,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Requeue one dead-lettered job with an explicit operator action trace."""

    dashboard = _dashboard_module()
    response_payload = dashboard._build_dashboard_job_action_runtime(store, settings).retry_dead_letter_job(
        job_id,
        note=str(payload.note or "").strip() if payload else "",
    )
    return JSONResponse(response_payload)


@router.post("/api/jobs/{job_id}/workflow/manual-retry", response_class=JSONResponse)
def manual_retry_workflow_job(
    job_id: str,
    payload: WorkflowManualRetryRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Queue one failed/completed job with an explicit manual rerun/resume policy."""

    dashboard = _dashboard_module()
    response_payload = dashboard._build_dashboard_job_action_runtime(store, settings).manual_retry_workflow_job(
        job_id,
        mode=payload.mode,
        node_id=payload.node_id,
        note=payload.note,
    )
    return JSONResponse(response_payload)


@router.post("/api/jobs/requeue-failed", response_class=JSONResponse)
def requeue_failed_jobs(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Requeue all failed jobs in one action."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_job_action_runtime(store, settings).requeue_failed_jobs()
    return JSONResponse(payload)


@router.get("/logs/{file_name}", response_class=PlainTextResponse)
def job_log_file(
    file_name: str,
    channel: str = Query(default="debug"),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> PlainTextResponse:
    """Serve one log file as plain text."""

    dashboard = _dashboard_module()
    return dashboard._build_dashboard_view_runtime(store, settings).log_file_response(
        file_name=file_name,
        channel=channel,
    )
