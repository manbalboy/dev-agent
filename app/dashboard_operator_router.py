"""Operator-side admin routers extracted from dashboard route module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dashboard_integration_registry_runtime import DashboardIntegrationRegistryRuntime
from app.dashboard_runtime_input_runtime import DashboardRuntimeInputRuntime
from app.dependencies import get_settings, get_store
from app.store import JobStore


router = APIRouter(tags=["dashboard"])


def _dashboard_module():
    """Import dashboard lazily to preserve existing monkeypatch contracts."""

    import app.dashboard as dashboard

    return dashboard


class PatchRunCreateRequest(BaseModel):
    """Payload for creating one patch execution record from dashboard."""

    refresh: bool = Field(default=False)
    note: str = Field(default="", max_length=300)


class PatchRollbackRequest(BaseModel):
    """Payload for requesting one rollback from dashboard."""

    note: str = Field(default="", max_length=300)


class PatchRestoreRequest(BaseModel):
    """Payload for requesting one backup restore from dashboard."""

    note: str = Field(default="", max_length=300)


class SelfCheckAlertAcknowledgeRequest(BaseModel):
    """Payload for acknowledging one active self-check alert."""

    acted_by: str = Field(default="dashboard", max_length=80)
    note: str = Field(default="", max_length=300)


class MemoryOverrideRequest(BaseModel):
    """Payload for manual memory state override."""

    state: str = Field(default="", max_length=20)
    note: str = Field(default="", max_length=300)


class BacklogCandidateActionRequest(BaseModel):
    """Payload for one backlog candidate state transition."""

    action: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=300)


class RuntimeInputRequestPayload(BaseModel):
    """Payload for one operator-managed runtime input request."""

    repository: str = Field(default="", max_length=200)
    app_code: str = Field(default="", max_length=32)
    job_id: str = Field(default="", max_length=128)
    scope: str = Field(min_length=1, max_length=20)
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    value_type: str = Field(default="text", max_length=20)
    env_var_name: str = Field(default="", max_length=80)
    sensitive: bool = Field(default=False)
    placeholder: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=300)
    requested_by: str = Field(default="operator", max_length=40)


class RuntimeInputDraftRequestPayload(BaseModel):
    """Payload for suggesting one runtime input request draft."""

    repository: str = Field(default="", max_length=200)
    app_code: str = Field(default="", max_length=32)
    job_id: str = Field(default="", max_length=128)
    context_text: str = Field(default="", max_length=2000)


class RuntimeInputProvidePayload(BaseModel):
    """Payload for providing one runtime input value later."""

    value: str = Field(default="", max_length=8000)
    note: str = Field(default="", max_length=300)


class IntegrationRegistryRequestPayload(BaseModel):
    """Payload for one third-party integration registry entry."""

    integration_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=60)
    supported_app_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    required_env_keys: list[str] = Field(default_factory=list)
    optional_env_keys: list[str] = Field(default_factory=list)
    operator_guide_markdown: str = Field(default="", max_length=20000)
    implementation_guide_markdown: str = Field(default="", max_length=20000)
    verification_notes: str = Field(default="", max_length=4000)
    approval_required: bool = Field(default=True)
    enabled: bool = Field(default=True)


class IntegrationApprovalActionPayload(BaseModel):
    """Payload for one operator approval/reject/reset action."""

    action: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=1000)
    acted_by: str = Field(default="operator", max_length=80)


@router.get("/api/admin/patch-status", response_class=JSONResponse)
def admin_patch_status_api(refresh: bool = Query(default=False)) -> JSONResponse:
    """Return current Git-based patch/update availability for the server repo."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_patch_control_runtime().build_patch_status(refresh=bool(refresh)))


@router.get("/api/admin/patch-runs/latest", response_class=JSONResponse)
def admin_patch_run_latest_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return the latest patch run payload for operator progress visibility."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_patch_runtime(store, settings).get_latest_patch_run_payload())


@router.get("/api/admin/patch-updater-status", response_class=JSONResponse)
def admin_patch_updater_status_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return standalone updater service heartbeat/status for patch operations."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_patch_updater_runtime(store, settings).read_status_payload())


@router.get("/api/admin/security-governance", response_class=JSONResponse)
def admin_security_governance_api(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one operator-facing security / TLS governance posture payload."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_security_governance_runtime(settings).build_status())


@router.get("/api/admin/durable-runtime-hygiene", response_class=JSONResponse)
def admin_durable_runtime_hygiene_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one operator-facing durable runtime hygiene audit payload."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_durable_runtime_hygiene_runtime(store, settings).build_hygiene_status())


@router.post("/api/admin/durable-runtime-hygiene/cleanup", response_class=JSONResponse)
def admin_durable_runtime_hygiene_cleanup_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Apply one safe durable runtime hygiene cleanup pass."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_durable_runtime_hygiene_runtime(store, settings).cleanup())


@router.get("/api/admin/durable-runtime-self-check", response_class=JSONResponse)
def admin_durable_runtime_self_check_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return the latest periodic durable runtime self-check report."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_durable_runtime_self_check_runtime(store, settings).read_status())


@router.post("/api/admin/durable-runtime-self-check/run", response_class=JSONResponse)
def admin_durable_runtime_self_check_run_api(
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Run one durable runtime self-check pass and persist the report."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_durable_runtime_self_check_runtime(store, settings).run_check(trigger="manual"))


@router.post("/api/admin/durable-runtime-self-check/alert/acknowledge", response_class=JSONResponse)
def admin_durable_runtime_self_check_alert_acknowledge_api(
    request: SelfCheckAlertAcknowledgeRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Acknowledge the current active periodic self-check alert."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_durable_runtime_self_check_runtime(store, settings).acknowledge_alert(
            acted_by=str(request.acted_by or "").strip() or "dashboard",
            note=str(request.note or "").strip(),
        )
    )


@router.post("/api/admin/patch-runs", response_class=JSONResponse)
def admin_patch_run_create_api(
    request: PatchRunCreateRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create one baseline patch run waiting for updater execution."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_patch_runtime(store, settings).create_patch_run(
        refresh=bool(request.refresh),
        note=str(request.note or ""),
    )
    return JSONResponse(payload)


@router.post("/api/admin/patch-runs/{patch_run_id}/rollback", response_class=JSONResponse)
def admin_patch_run_rollback_api(
    patch_run_id: str,
    request: PatchRollbackRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Request one operator-approved rollback for a failed patch run."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_patch_runtime(store, settings).request_rollback(
        patch_run_id=patch_run_id,
        note=str(request.note or ""),
    )
    return JSONResponse(payload)


@router.post("/api/admin/patch-runs/{patch_run_id}/restore", response_class=JSONResponse)
def admin_patch_run_restore_api(
    patch_run_id: str,
    request: PatchRestoreRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Request one operator-approved backup restore for a failed patch run."""

    dashboard = _dashboard_module()
    payload = dashboard._build_dashboard_patch_runtime(store, settings).request_restore(
        patch_run_id=patch_run_id,
        note=str(request.note or ""),
    )
    return JSONResponse(payload)


@router.get("/api/admin/memory/search", response_class=JSONResponse)
def admin_memory_search_api(
    q: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=20),
    memory_type: str = Query(default="", max_length=40),
    repository: str = Query(default="", max_length=200),
    execution_repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    workflow_id: str = Query(default="", max_length=120),
    limit: int = Query(default=12, ge=1, le=100),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Search memory runtime entries with lightweight filters for admin UI."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_memory_admin_runtime(store, settings).search_entries(
            q=q,
            state=state,
            memory_type=memory_type,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )
    )


@router.get("/api/admin/memory/backlog", response_class=JSONResponse)
def admin_memory_backlog_api(
    q: str = Query(default="", max_length=200),
    state: str = Query(default="", max_length=40),
    priority: str = Query(default="", max_length=4),
    repository: str = Query(default="", max_length=200),
    execution_repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    workflow_id: str = Query(default="", max_length=120),
    limit: int = Query(default=12, ge=1, le=100),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """List memory-backed autonomous backlog candidates for admin review."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_memory_admin_runtime(store, settings).list_backlog_candidates(
            q=q,
            state=state,
            priority=priority,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )
    )


@router.post("/api/admin/memory/backlog/{candidate_id:path}/action", response_class=JSONResponse)
def admin_memory_backlog_action_api(
    candidate_id: str,
    payload: BacklogCandidateActionRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Apply one small operator action to a backlog candidate."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_memory_admin_runtime(store, settings).apply_backlog_action(
            candidate_id=candidate_id,
            action=payload.action,
            note=payload.note,
        )
    )


@router.get("/api/admin/memory/{memory_id:path}", response_class=JSONResponse)
def admin_memory_detail_api(
    memory_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one detailed memory payload including evidence and feedback."""

    dashboard = _dashboard_module()
    return JSONResponse(dashboard._build_dashboard_memory_admin_runtime(store, settings).get_memory_detail(memory_id=memory_id))


@router.post("/api/admin/memory/{memory_id:path}/override", response_class=JSONResponse)
def admin_memory_override_api(
    memory_id: str,
    payload: MemoryOverrideRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Apply or clear one manual memory state override."""

    dashboard = _dashboard_module()
    return JSONResponse(
        dashboard._build_dashboard_memory_admin_runtime(store, settings).override_memory(
            memory_id=memory_id,
            state=payload.state,
            note=payload.note,
        )
    )


@router.get("/api/admin/runtime-inputs", response_class=JSONResponse)
def admin_runtime_inputs_api(
    q: str = Query(default="", max_length=200),
    status: str = Query(default="", max_length=20),
    scope: str = Query(default="", max_length=20),
    repository: str = Query(default="", max_length=200),
    app_code: str = Query(default="", max_length=32),
    job_id: str = Query(default="", max_length=128),
    limit: int = Query(default=20, ge=1, le=100),
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """List operator-managed runtime input requests/values for dashboard admin."""

    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)
    return JSONResponse(
        runtime.list_runtime_inputs(
            q=q,
            status=status,
            scope=scope,
            repository=repository,
            app_code=app_code,
            job_id=job_id,
            limit=limit,
        )
    )


@router.post("/api/admin/runtime-inputs/draft", response_class=JSONResponse)
def admin_runtime_inputs_draft_api(
    payload: RuntimeInputDraftRequestPayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Suggest operator-approval runtime input drafts from job/context text."""

    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)
    return JSONResponse(
        runtime.suggest_runtime_input_drafts(
            repository=payload.repository,
            app_code=payload.app_code,
            job_id=payload.job_id,
            context_text=payload.context_text,
        )
    )


@router.post("/api/admin/runtime-inputs/request", response_class=JSONResponse)
def admin_runtime_inputs_request_api(
    payload: RuntimeInputRequestPayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create one small operator runtime input request."""

    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)
    return JSONResponse(
        runtime.create_runtime_input_request(
            repository=payload.repository,
            app_code=payload.app_code,
            job_id=payload.job_id,
            scope=payload.scope,
            key=payload.key,
            label=payload.label,
            description=payload.description,
            value_type=payload.value_type,
            env_var_name=payload.env_var_name,
            sensitive=payload.sensitive,
            placeholder=payload.placeholder,
            note=payload.note,
            requested_by=payload.requested_by,
        )
    )


@router.post("/api/admin/runtime-inputs/{request_id:path}/provide", response_class=JSONResponse)
def admin_runtime_inputs_provide_api(
    request_id: str,
    payload: RuntimeInputProvidePayload,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Provide or clear one runtime input value."""

    runtime = DashboardRuntimeInputRuntime(store=store, settings=settings)
    return JSONResponse(
        runtime.provide_runtime_input(
            request_id=request_id,
            value=payload.value,
            note=payload.note,
        )
    )


@router.get("/api/admin/integrations", response_class=JSONResponse)
def admin_integrations_api(
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=60),
    app_type: str = Query(default="", max_length=20),
    enabled: str = Query(default="", max_length=10),
    limit: int = Query(default=20, ge=1, le=100),
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """List third-party integration registry entries for dashboard admin."""

    runtime = DashboardIntegrationRegistryRuntime(store=store)
    return JSONResponse(
        runtime.list_entries(
            q=q,
            category=category,
            app_type=app_type,
            enabled=enabled,
            limit=limit,
        )
    )


@router.post("/api/admin/integrations", response_class=JSONResponse)
def admin_integrations_upsert_api(
    payload: IntegrationRegistryRequestPayload,
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Create or update one third-party integration registry entry."""

    runtime = DashboardIntegrationRegistryRuntime(store=store)
    return JSONResponse(
        runtime.save_entry(
            integration_id=payload.integration_id,
            display_name=payload.display_name,
            category=payload.category,
            supported_app_types=payload.supported_app_types,
            tags=payload.tags,
            required_env_keys=payload.required_env_keys,
            optional_env_keys=payload.optional_env_keys,
            operator_guide_markdown=payload.operator_guide_markdown,
            implementation_guide_markdown=payload.implementation_guide_markdown,
            verification_notes=payload.verification_notes,
            approval_required=payload.approval_required,
            enabled=payload.enabled,
        )
    )


@router.post("/api/admin/integrations/{integration_id}/approval", response_class=JSONResponse)
def admin_integrations_approval_action_api(
    integration_id: str,
    payload: IntegrationApprovalActionPayload,
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Apply one operator approval action to an integration registry entry."""

    runtime = DashboardIntegrationRegistryRuntime(store=store)
    return JSONResponse(
        runtime.set_approval_action(
            integration_id=integration_id,
            action=payload.action,
            note=payload.note,
            acted_by=payload.acted_by,
        )
    )
