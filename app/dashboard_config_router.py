"""Config-oriented dashboard routers extracted from the main dashboard module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dependencies import get_settings


router = APIRouter(tags=["dashboard"])


def _dashboard_module():
    """Import dashboard lazily to preserve existing monkeypatch contracts."""

    import app.dashboard as dashboard

    return dashboard


class RoleConfigRequest(BaseModel):
    """Payload for one role definition."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    objective: str = Field(default="", max_length=400)
    cli: str = Field(default="", max_length=40)
    template_key: str = Field(default="", max_length=80)
    inputs: str = Field(default="", max_length=400)
    outputs: str = Field(default="", max_length=400)
    checklist: str = Field(default="", max_length=1000)
    skills: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = Field(default=True)


class RolePresetRequest(BaseModel):
    """Payload for one role-combination preset."""

    preset_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    role_codes: list[str] = Field(default_factory=list)


class AgentTemplateConfigRequest(BaseModel):
    """Editable command templates for planner/coder/reviewer."""

    planner: str = Field(min_length=1, max_length=4000)
    coder: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=4000)
    copilot: str = Field(default="", max_length=4000)
    escalation: str = Field(default="", max_length=4000)
    enable_escalation: bool = Field(default=False)


class WorkflowValidateRequest(BaseModel):
    """Payload wrapper for workflow validation."""

    workflow: dict


class WorkflowSaveRequest(BaseModel):
    """Payload for workflow save/update in phase-1."""

    workflow: dict
    set_default: bool = Field(default=False)


class WorkflowDefaultRequest(BaseModel):
    """Payload for setting default workflow id."""

    workflow_id: str = Field(min_length=1, max_length=120)


class FeatureFlagsRequest(BaseModel):
    """Payload for adaptive feature flag updates."""

    flags: dict[str, bool] = Field(default_factory=dict)


@router.get("/api/roles", response_class=JSONResponse)
def roles_api() -> JSONResponse:
    """Return role definitions and role-combination presets."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_roles_runtime().list_roles(roles_config_path=dashboard._ROLES_CONFIG_PATH)
    return JSONResponse(payload)


@router.post("/api/roles", response_class=JSONResponse)
def upsert_role(payload: RoleConfigRequest) -> JSONResponse:
    """Create or update one role definition."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_roles_runtime().upsert_role(
            roles_config_path=dashboard._ROLES_CONFIG_PATH,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.delete("/api/roles/{role_code}", response_class=JSONResponse)
def delete_role(role_code: str) -> JSONResponse:
    """Delete one role and unlink it from presets."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_roles_runtime().delete_role(
            roles_config_path=dashboard._ROLES_CONFIG_PATH,
            role_code=role_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.post("/api/role-presets", response_class=JSONResponse)
def upsert_role_preset(payload: RolePresetRequest) -> JSONResponse:
    """Create or update one role-combination preset."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_roles_runtime().upsert_role_preset(
            roles_config_path=dashboard._ROLES_CONFIG_PATH,
            payload=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.delete("/api/role-presets/{preset_id}", response_class=JSONResponse)
def delete_role_preset(preset_id: str) -> JSONResponse:
    """Delete one role-combination preset."""

    dashboard = _dashboard_module()
    try:
        response_payload = dashboard._build_dashboard_roles_runtime().delete_role_preset(
            roles_config_path=dashboard._ROLES_CONFIG_PATH,
            preset_id=preset_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(response_payload)


@router.get("/api/workflows/schema", response_class=JSONResponse)
def workflow_schema_api(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return workflow node/edge schema metadata for editor UI."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).workflow_schema())


@router.get("/api/workflows", response_class=JSONResponse)
def workflows_api(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return saved workflows and current default workflow id."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).list_workflows())


@router.post("/api/workflows/validate", response_class=JSONResponse)
def validate_workflow_api(
    payload: WorkflowValidateRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Validate one workflow definition without saving."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).validate_workflow(payload.workflow))


@router.post("/api/workflows", response_class=JSONResponse)
def save_workflow_api(
    payload: WorkflowSaveRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Save one workflow definition in phase-1 workflow config."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_settings_runtime(settings).save_workflow(
            payload.workflow,
            set_default=payload.set_default,
        )
    )


@router.post("/api/workflows/default", response_class=JSONResponse)
def set_default_workflow_api(
    payload: WorkflowDefaultRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Set one registered workflow as default."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).set_default_workflow(payload.workflow_id))


@router.get("/api/feature-flags", response_class=JSONResponse)
def get_feature_flags_api(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return adaptive feature flags for settings/admin UI."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).get_feature_flags())


@router.post("/api/feature-flags", response_class=JSONResponse)
def save_feature_flags_api(
    payload: FeatureFlagsRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Persist adaptive feature flags."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).save_feature_flags(payload.flags))


@router.get("/api/agents/config", response_class=JSONResponse)
def get_agents_config(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return editable command templates for dashboard form."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).get_agent_config())


@router.post("/api/agents/config", response_class=JSONResponse)
def update_agents_config(
    payload: AgentTemplateConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Update planner/coder/reviewer templates in command config file."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).update_agent_config(payload.model_dump()))


@router.get("/api/agents/check", response_class=JSONResponse)
def check_agent_clis(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Check whether Gemini/Codex CLIs are executable."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).get_agent_cli_status())


@router.get("/api/agents/models", response_class=JSONResponse)
def check_agent_models(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return inferred model settings for Gemini/Codex."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_settings_runtime(settings).get_agent_model_status())
