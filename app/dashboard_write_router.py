"""Write-oriented dashboard routers extracted from the main dashboard module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dependencies import get_settings, get_store
from app.store import JobStore


router = APIRouter(tags=["dashboard"])


def _dashboard_module():
    """Import dashboard lazily to preserve existing monkeypatch contracts."""

    import app.dashboard as dashboard

    return dashboard


class IssueRegistrationRequest(BaseModel):
    """Payload for manual issue creation from dashboard."""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    app_code: str = Field(default="default", min_length=1, max_length=32)
    track: str = Field(default="enhance", min_length=1, max_length=32)
    keep_branch: bool = Field(default=True)
    branch_name: str = Field(default="", max_length=200)
    role_preset_id: str = Field(default="", max_length=64)
    workflow_id: str = Field(default="", max_length=120)


class AppConfigRequest(BaseModel):
    """Payload for app registration/editing."""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    source_repository: str = Field(default="", max_length=200)
    workflow_id: str = Field(default="", max_length=120)


class AppWorkflowMappingRequest(BaseModel):
    """Payload for binding one app to one saved workflow."""

    workflow_id: str = Field(min_length=1, max_length=120)


class AssistantChatRequest(BaseModel):
    """Payload for dashboard assistant chat."""

    assistant: str = Field(default="codex", min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=8000)
    history: list[dict[str, str]] = Field(default_factory=list)
    job_id: str = Field(default="", max_length=128)


class AssistantLogAnalysisRequest(BaseModel):
    """Payload for one-shot log analysis by selected assistant."""

    assistant: str = Field(default="codex", min_length=1, max_length=20)
    question: str = Field(default="최근 로그의 핵심 문제점을 분석해줘", min_length=1, max_length=8000)
    job_id: str = Field(default="", max_length=128)


@router.get("/api/apps", response_class=JSONResponse)
def list_apps(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return registered app list for dashboard dropdowns."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_app_registry_runtime(settings).list_apps())


@router.post("/api/apps", response_class=JSONResponse)
def upsert_app(
    payload: AppConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create or update one app registration entry."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_app_registry_runtime(settings).upsert_app(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.delete("/api/apps/{app_code}", response_class=JSONResponse)
def delete_app(
    app_code: str,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Delete one app registration entry."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_app_registry_runtime(settings).delete_app(app_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.post("/api/apps/{app_code}/workflow", response_class=JSONResponse)
def map_app_workflow(
    app_code: str,
    payload: AppWorkflowMappingRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Bind one app code to one registered workflow id."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_app_registry_runtime(settings).map_app_workflow(
            app_code,
            payload.workflow_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc)) from exc
    return JSONResponse(response_payload)


@router.post("/api/assistant/codex-chat", response_class=JSONResponse)
def codex_assistant_chat(
    payload: AssistantChatRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Legacy Codex-only chat route kept for compatibility."""

    payload.assistant = "codex"
    return assistant_chat(payload=payload, settings=settings, store=store)


@router.post("/api/assistant/chat", response_class=JSONResponse)
def assistant_chat(
    payload: AssistantChatRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Run one conversational assistant turn with selected provider."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_assistant_runtime(store, settings).chat(payload.model_dump()))


@router.post("/api/assistant/log-analysis", response_class=JSONResponse)
def assistant_log_analysis(
    payload: AssistantLogAnalysisRequest,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Analyze AgentHub logs with one selected assistant CLI."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_assistant_runtime(store, settings).log_analysis(payload.model_dump()))


@router.post("/api/issues/register", response_class=JSONResponse)
def register_issue_and_trigger(
    payload: IssueRegistrationRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create a GitHub issue, label it, and trigger a local job immediately."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_issue_registration_runtime(store, settings).register_issue(payload.model_dump()))
