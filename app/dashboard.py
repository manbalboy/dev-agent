"""Dashboard routes for job visibility."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import AppSettings
from app.dependencies import get_settings, get_store
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.store import JobStore
from app.workflow_design import (
    default_workflow_template,
    load_workflows,
    save_workflows,
    schema_payload,
    validate_workflow,
)


router = APIRouter(tags=["dashboard"])
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_LOG_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
_TIMESTAMPED_LINE_PATTERN = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<msg>.*)$")
_ISSUE_URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/issues/\d+")
_ISSUE_NUMBER_PATTERN = re.compile(r"/issues/(?P<number>\d+)")
_APP_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_TRACK_CHOICES = {"new", "enhance", "bug", "long", "ultra"}
_APPS_CONFIG_PATH = Path.cwd() / "config" / "apps.json"
_WORKFLOWS_CONFIG_PATH = Path.cwd() / "config" / "workflows.json"


class IssueRegistrationRequest(BaseModel):
    """Payload for manual issue creation from dashboard."""

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    app_code: str = Field(default="default", min_length=1, max_length=32)
    track: str = Field(default="new", min_length=1, max_length=32)


class AppConfigRequest(BaseModel):
    """Payload for app registration/editing."""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)


class AppWorkflowMappingRequest(BaseModel):
    """Payload for binding one app to one saved workflow."""

    workflow_id: str = Field(min_length=1, max_length=120)


class AgentTemplateConfigRequest(BaseModel):
    """Editable command templates for planner/coder/reviewer."""

    planner: str = Field(min_length=1, max_length=4000)
    coder: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=4000)
    escalation: str = Field(default="", max_length=4000)
    enable_escalation: bool = Field(default=False)


class WorkflowValidateRequest(BaseModel):
    """Payload wrapper for workflow validation."""

    workflow: Dict[str, Any]


class WorkflowSaveRequest(BaseModel):
    """Payload for workflow save/update in phase-1."""

    workflow: Dict[str, Any]
    set_default: bool = Field(default=False)


@router.get("/", response_class=HTMLResponse)
def job_list_page(
    request: Request,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render a simple dashboard table with all jobs."""

    jobs = store.list_jobs()
    return _templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": jobs,
            "apps": _read_registered_apps(_APPS_CONFIG_PATH, settings.allowed_repository),
            "title": "AgentHub Jobs",
        },
    )


@router.get("/api/jobs", response_class=JSONResponse)
def jobs_api(
    store: JobStore = Depends(get_store),
) -> JSONResponse:
    """Return all jobs as JSON for real-time polling on dashboard list."""

    jobs = [job.to_dict() for job in store.list_jobs()]
    summary = {
        "total": len(jobs),
        "queued": sum(1 for item in jobs if item.get("status") == "queued"),
        "running": sum(1 for item in jobs if item.get("status") == "running"),
        "done": sum(1 for item in jobs if item.get("status") == "done"),
        "failed": sum(1 for item in jobs if item.get("status") == "failed"),
    }
    return JSONResponse({"jobs": jobs, "summary": summary})


@router.get("/api/apps", response_class=JSONResponse)
def list_apps(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return registered app list for dashboard dropdowns."""

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    return JSONResponse(
        {
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
            "tracks": sorted(_TRACK_CHOICES),
            "default_workflow_id": default_workflow_id,
        }
    )


@router.post("/api/apps", response_class=JSONResponse)
def upsert_app(
    payload: AppConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create or update one app registration entry."""

    code = _normalize_app_code(payload.code)
    if not code:
        raise HTTPException(status_code=400, detail="앱 코드는 영문/숫자/-/_ 형식이어야 합니다.")
    if code == "default":
        raise HTTPException(status_code=400, detail="default 코드는 예약되어 있습니다.")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="앱 표시명을 입력해주세요.")

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    replaced = False
    updated: List[Dict[str, str]] = []
    for app in apps:
        if app["code"] == "default":
            updated.append(app)
            continue
        if app["code"] == code:
            updated.append(
                {
                    "code": code,
                    "name": name,
                    "repository": settings.allowed_repository,
                    "workflow_id": app.get("workflow_id", default_workflow_id),
                }
            )
            replaced = True
            continue
        updated.append(app)
    if not replaced:
        updated.append(
            {
                "code": code,
                "name": name,
                "repository": settings.allowed_repository,
                "workflow_id": default_workflow_id,
            }
        )

    _write_registered_apps(_APPS_CONFIG_PATH, updated)
    _ensure_label(settings.allowed_repository, f"app:{code}", "0052CC", f"AgentHub app namespace ({code})")
    for track in sorted(_TRACK_CHOICES):
        _ensure_label(settings.allowed_repository, f"track:{track}", "5319E7", f"AgentHub work type ({track})")
    return JSONResponse(
        {
            "saved": True,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.delete("/api/apps/{app_code}", response_class=JSONResponse)
def delete_app(
    app_code: str,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Delete one app registration entry."""

    code = _normalize_app_code(app_code)
    if not code or code == "default":
        raise HTTPException(status_code=400, detail="삭제할 수 없는 앱 코드입니다.")

    default_workflow_id = _read_default_workflow_id(_WORKFLOWS_CONFIG_PATH)
    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    filtered = [app for app in apps if app["code"] != code]
    _write_registered_apps(_APPS_CONFIG_PATH, filtered)
    return JSONResponse(
        {
            "deleted": True,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.post("/api/apps/{app_code}/workflow", response_class=JSONResponse)
def map_app_workflow(
    app_code: str,
    payload: AppWorkflowMappingRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Bind one app code to one registered workflow id."""

    code = _normalize_app_code(app_code)
    if not code:
        raise HTTPException(status_code=400, detail="유효하지 않은 앱 코드입니다.")

    workflows_payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    default_workflow_id = str(workflows_payload.get("default_workflow_id", "")).strip() or default_workflow_template()["workflow_id"]
    workflows = workflows_payload.get("workflows", [])
    known_workflow_ids = {
        str(item.get("workflow_id", "")).strip()
        for item in workflows
        if isinstance(item, dict)
    }

    workflow_id = payload.workflow_id.strip()
    if workflow_id not in known_workflow_ids:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 workflow_id 입니다: {workflow_id}")

    apps = _read_registered_apps(
        _APPS_CONFIG_PATH,
        settings.allowed_repository,
        default_workflow_id=default_workflow_id,
    )
    found = False
    updated: List[Dict[str, str]] = []
    for app in apps:
        if app["code"] == code:
            copied = dict(app)
            copied["workflow_id"] = workflow_id
            updated.append(copied)
            found = True
            continue
        updated.append(app)

    if not found:
        raise HTTPException(status_code=404, detail=f"앱을 찾을 수 없습니다: {code}")

    _write_registered_apps(_APPS_CONFIG_PATH, updated)
    return JSONResponse(
        {
            "saved": True,
            "app_code": code,
            "workflow_id": workflow_id,
            "apps": _read_registered_apps(
                _APPS_CONFIG_PATH,
                settings.allowed_repository,
                default_workflow_id=default_workflow_id,
            ),
        }
    )


@router.get("/api/workflows/schema", response_class=JSONResponse)
def workflow_schema_api() -> JSONResponse:
    """Return workflow node/edge schema metadata for editor UI."""

    return JSONResponse(schema_payload())


@router.get("/api/workflows", response_class=JSONResponse)
def workflows_api() -> JSONResponse:
    """Return saved workflows and current default workflow id."""

    payload = load_workflows(_WORKFLOWS_CONFIG_PATH)
    return JSONResponse(payload)


@router.post("/api/workflows/validate", response_class=JSONResponse)
def validate_workflow_api(
    payload: WorkflowValidateRequest,
) -> JSONResponse:
    """Validate one workflow definition without saving."""

    ok, errors = validate_workflow(payload.workflow)
    return JSONResponse({"ok": ok, "errors": errors})


@router.post("/api/workflows", response_class=JSONResponse)
def save_workflow_api(
    payload: WorkflowSaveRequest,
) -> JSONResponse:
    """Save one workflow definition in phase-1 workflow config."""

    workflow = payload.workflow
    ok, errors = validate_workflow(workflow)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": "workflow validation failed", "errors": errors})

    saved = load_workflows(_WORKFLOWS_CONFIG_PATH)
    workflows = saved.get("workflows", [])
    if not isinstance(workflows, list):
        workflows = []

    workflow_id = str(workflow.get("workflow_id", "")).strip()
    replaced = False
    next_workflows: List[Dict[str, Any]] = []
    for item in workflows:
        if isinstance(item, dict) and str(item.get("workflow_id", "")) == workflow_id:
            next_workflows.append(workflow)
            replaced = True
            continue
        if isinstance(item, dict):
            next_workflows.append(item)
    if not replaced:
        next_workflows.append(workflow)

    saved["workflows"] = next_workflows
    if payload.set_default or not str(saved.get("default_workflow_id", "")).strip():
        saved["default_workflow_id"] = workflow_id
    if saved.get("default_workflow_id") == "":
        saved["default_workflow_id"] = default_workflow_template()["workflow_id"]

    save_workflows(_WORKFLOWS_CONFIG_PATH, saved)
    return JSONResponse({"saved": True, "workflow_id": workflow_id, "default_workflow_id": saved.get("default_workflow_id")})


@router.get("/api/agents/config", response_class=JSONResponse)
def get_agents_config(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return editable command templates for dashboard form."""

    templates = _read_command_templates(settings.command_config)
    return JSONResponse(
        {
            "planner": templates.get("planner", ""),
            "coder": templates.get("coder", ""),
            "reviewer": templates.get("reviewer", ""),
            "escalation": templates.get("escalation", ""),
            "enable_escalation": _read_env_enable_escalation(Path.cwd() / ".env", settings.enable_escalation),
        }
    )


@router.post("/api/agents/config", response_class=JSONResponse)
def update_agents_config(
    payload: AgentTemplateConfigRequest,
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Update planner/coder/reviewer templates in command config file."""

    current = _read_command_templates(settings.command_config)
    current["planner"] = payload.planner.strip()
    current["coder"] = payload.coder.strip()
    current["reviewer"] = payload.reviewer.strip()
    current["escalation"] = payload.escalation.strip()
    _write_command_templates(settings.command_config, current)
    _set_env_value(Path.cwd() / ".env", "AGENTHUB_ENABLE_ESCALATION", "true" if payload.enable_escalation else "false")
    return JSONResponse({"saved": True, "enable_escalation": payload.enable_escalation})


@router.get("/api/agents/check", response_class=JSONResponse)
def check_agent_clis(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Check whether Gemini/Codex/Claude CLIs are executable."""

    templates = _read_command_templates(settings.command_config)
    result = {
        "gemini": _check_one_cli("gemini", templates),
        "codex": _check_one_cli("codex", templates),
        "claude": _check_one_cli("claude", templates),
    }
    return JSONResponse(result)


@router.get("/api/agents/models", response_class=JSONResponse)
def check_agent_models(
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return inferred model settings for Gemini/Codex/Claude."""

    templates = _read_command_templates(settings.command_config)
    result = {
        "gemini": _infer_cli_model("gemini", templates),
        "codex": _infer_cli_model("codex", templates),
        "claude": _infer_cli_model("claude", templates),
    }
    return JSONResponse(result)


@router.post("/api/issues/register", response_class=JSONResponse)
def register_issue_and_trigger(
    payload: IssueRegistrationRequest,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Create a GitHub issue, label it, and trigger a local job immediately."""

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="이슈 제목은 필수입니다.")

    body = payload.body.strip() or "AgentHub 대시보드에서 등록된 작업 이슈입니다."
    app_code = _normalize_app_code(payload.app_code) or "default"
    track = _normalize_track(payload.track)
    title_track = _detect_title_track(title)
    if title_track:
        track = title_track
    repository = settings.allowed_repository
    registered_codes = {
        item["code"] for item in _read_registered_apps(_APPS_CONFIG_PATH, repository)
    }
    if app_code not in registered_codes:
        raise HTTPException(
            status_code=400,
            detail=f"등록되지 않은 앱 코드입니다: {app_code}. 설정 메뉴에서 먼저 등록해주세요.",
        )

    create_stdout = _run_gh_command(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repository,
            "--title",
            title,
            "--body",
            body,
        ],
        error_context="GitHub 이슈 생성",
    )
    issue_url = _extract_issue_url(create_stdout)

    issue_number = _extract_issue_number(issue_url)

    _ensure_agent_run_label(repository)
    _ensure_label(repository, f"app:{app_code}", "0052CC", f"AgentHub app namespace ({app_code})")
    _ensure_label(repository, f"track:{track}", "5319E7", f"AgentHub work type ({track})")

    _run_gh_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repository,
            "--add-label",
            f"agent:run,app:{app_code},track:{track}",
        ],
        error_context="작업 라벨 추가",
    )

    existing = _find_active_job(store, repository, issue_number)
    if existing is not None:
        return JSONResponse(
            {
                "accepted": True,
                "created_issue": True,
                "triggered": False,
                "reason": "already_active_job",
                "job_id": existing.job_id,
                "issue_number": issue_number,
                "issue_url": issue_url,
            }
        )

    now = utc_now_iso()
    job_id = str(uuid.uuid4())
    job = JobRecord(
        job_id=job_id,
        repository=repository,
        issue_number=issue_number,
        issue_title=title,
        issue_url=issue_url,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=settings.max_retries,
        branch_name=_build_branch_name(app_code, issue_number, job_id),
        pr_url=None,
        error_message=None,
        log_file=_build_log_file_name(app_code, job_id),
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
    )

    store.create_job(job)
    store.enqueue_job(job_id)

    return JSONResponse(
        {
            "accepted": True,
            "created_issue": True,
            "triggered": True,
            "job_id": job_id,
            "issue_number": issue_number,
            "issue_url": issue_url,
            "app_code": app_code,
            "track": track,
        }
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(
    job_id: str,
    request: Request,
    store: JobStore = Depends(get_store),
) -> HTMLResponse:
    """Render details and quick links for one job."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return _templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "title": f"Job {job_id}",
        },
    )


@router.get("/api/jobs/{job_id}", response_class=JSONResponse)
def job_detail_api(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Return one job plus parsed log conversation events and agent artifacts."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    log_path = (settings.logs_dir / job.log_file).resolve()
    events = _parse_log_events(log_path) if log_path.exists() else []
    
    workspace_path = settings.repository_workspace_path(job.repository, job.app_code)
    md_files = _read_agent_md_files(workspace_path)
    stage_md_snapshots = _read_stage_md_snapshots(settings.data_dir, job_id)

    return JSONResponse(
        {
            "job": job.to_dict(),
            "events": events,
            "md_files": md_files,
            "stage_md_snapshots": stage_md_snapshots,
            "stop_requested": _stop_signal_path(settings.data_dir, job_id).exists(),
        }
    )


def _read_agent_md_files(workspace_path: Path) -> List[Dict[str, str]]:
    """Read .md files generated by agents in the workspace."""

    if not workspace_path.exists():
        return []

    md_files = []
    # 에이전트가 생성한 주요 MD 파일들을 우선적으로 찾고, 그 외 MD 파일들도 포함한다.
    # 보통 프로젝트 루트에 생성됨.
    for path in workspace_path.glob("*.md"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            md_files.append({
                "name": path.name,
                "content": content
            })
        except Exception:
            continue
    
    # 파일명 순으로 정렬
    md_files.sort(key=lambda x: x["name"])
    return md_files


def _read_stage_md_snapshots(data_dir: Path, job_id: str) -> List[Dict[str, Any]]:
    """Read per-stage markdown snapshots saved by orchestrator."""

    snapshot_root = (data_dir / "md_snapshots" / job_id).resolve()
    base_root = (data_dir / "md_snapshots").resolve()
    if not snapshot_root.exists():
        return []
    if base_root not in snapshot_root.parents and snapshot_root != base_root:
        return []

    snapshots: List[Dict[str, Any]] = []
    for path in sorted(snapshot_root.glob("attempt_*.json")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        snapshots.append(
            {
                "attempt": int(payload.get("attempt", 0) or 0),
                "stage": str(payload.get("stage", "")),
                "created_at": str(payload.get("created_at", "")),
                "changed_files": payload.get("changed_files", []),
                "md_files": payload.get("md_files", []),
            }
        )

    snapshots.sort(key=lambda item: (item.get("attempt", 0), item.get("stage", "")))
    return snapshots


@router.post("/api/jobs/{job_id}/stop", response_class=JSONResponse)
def request_job_stop(
    job_id: str,
    store: JobStore = Depends(get_store),
    settings: AppSettings = Depends(get_settings),
) -> JSONResponse:
    """Request graceful stop for one running ultra job."""

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        raise HTTPException(status_code=400, detail="실행 중 작업에서만 정지 요청할 수 있습니다.")

    path = _stop_signal_path(settings.data_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop\n", encoding="utf-8")
    return JSONResponse({"requested": True, "job_id": job_id, "stop_file": str(path)})


@router.get("/logs/{file_name}", response_class=PlainTextResponse)
def job_log_file(
    file_name: str,
    settings: AppSettings = Depends(get_settings),
) -> PlainTextResponse:
    """Serve one log file as plain text.

    The file name is strictly validated to block path traversal such as `../`.
    """

    if not _LOG_NAME_PATTERN.match(file_name):
        raise HTTPException(
            status_code=400,
            detail="Invalid log file name. Use only letters, numbers, dot, dash, underscore.",
        )

    logs_dir = settings.logs_dir.resolve()
    target_path = (logs_dir / file_name).resolve()

    # Why this check exists:
    # Even after regex validation we still validate final resolved path to defend
    # against symlink tricks or future relaxed naming rules.
    if logs_dir not in target_path.parents and target_path != logs_dir:
        raise HTTPException(status_code=400, detail="Invalid log file path.")

    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {file_name}")

    return PlainTextResponse(target_path.read_text(encoding="utf-8"))


def _parse_log_events(log_path: Path) -> List[Dict[str, str]]:
    """Parse log lines into timeline events with inferred speaker and receiver."""

    events: List[Dict[str, str]] = []
    raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    last_timestamp = ""
    active_target = "shell"

    for raw_line in raw_lines:
        match = _TIMESTAMPED_LINE_PATTERN.match(raw_line)
        if not match:
            if events:
                events[-1]["message"] += "\n" + raw_line
            continue

        timestamp = match.group("ts")
        message = match.group("msg")
        last_timestamp = timestamp

        if message.startswith("[RUN] "):
            command = message.replace("[RUN] ", "", 1)
            active_target = _classify_command_target(command)
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": "agenthub",
                    "receiver": active_target,
                    "kind": "run",
                    "message": command,
                }
            )
            continue

        if message.startswith("[STDOUT]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "stdout",
                    "message": message.replace("[STDOUT]", "", 1).strip() or "(stdout)",
                }
            )
            continue

        if message.startswith("[STDERR]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "stderr",
                    "message": message.replace("[STDERR]", "", 1).strip() or "(stderr)",
                }
            )
            continue

        if message.startswith("[STAGE] "):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": "agenthub",
                    "receiver": "dashboard",
                    "kind": "stage",
                    "message": message.replace("[STAGE] ", "", 1),
                }
            )
            continue

        if message.startswith("[DONE]"):
            events.append(
                {
                    "timestamp": timestamp,
                    "speaker": active_target,
                    "receiver": "agenthub",
                    "kind": "done",
                    "message": message,
                }
            )
            continue

        events.append(
            {
                "timestamp": last_timestamp,
                "speaker": "agenthub",
                "receiver": "dashboard",
                "kind": "info",
                "message": message,
            }
        )

    return events[-300:]


def _classify_command_target(command: str) -> str:
    """Infer command target actor for conversation-style timeline."""

    lowered = command.lower()
    if "plann" in lowered and "gemini" in lowered:
        return "planner"
    if "review" in lowered and "gemini" in lowered:
        return "reviewer"
    if "codex" in lowered:
        return "coder"
    if lowered.startswith("gh "):
        return "github"
    if lowered.startswith("git ") or " git " in lowered:
        return "git"
    return "shell"


def _extract_issue_number(issue_url: str) -> int:
    """Extract issue number from GitHub issue URL."""

    match = _ISSUE_NUMBER_PATTERN.search(issue_url)
    if match is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "이슈 URL에서 번호를 읽지 못했습니다. "
                "gh CLI 출력 형식을 확인해주세요."
            ),
        )
    return int(match.group("number"))


def _extract_issue_url(stdout: str) -> str:
    """Extract issue URL from gh output text."""

    match = _ISSUE_URL_PATTERN.search(stdout)
    if match is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "이슈 생성 결과에서 URL을 읽지 못했습니다. "
                "gh CLI 출력 형식을 확인해주세요."
            ),
        )
    return match.group(0)


def _run_gh_command(args: List[str], error_context: str) -> str:
    """Run gh command with consistent error mapping."""

    process = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        stderr_preview = (process.stderr or "").strip()[:500]
        raise HTTPException(
            status_code=502,
            detail=(
                f"{error_context} 실패: gh CLI 상태를 확인해주세요. "
                f"stderr: {stderr_preview or '(no stderr)'}"
            ),
        )
    return process.stdout


def _read_command_templates(path: Path) -> Dict[str, str]:
    """Read command template JSON file into string dictionary."""

    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 파일이 없습니다: {path}",
        )

    try:
        raw_payload = path.read_text(encoding="utf-8")
        loaded = json.loads(raw_payload)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=500,
            detail=f"명령 템플릿 JSON 파싱 실패: {path}",
        ) from error

    if not isinstance(loaded, dict):
        raise HTTPException(
            status_code=500,
            detail="명령 템플릿 포맷이 올바르지 않습니다. JSON object여야 합니다.",
        )

    templates: Dict[str, str] = {}
    for key, value in loaded.items():
        if isinstance(value, str):
            templates[str(key)] = value
    return templates


def _write_command_templates(path: Path, templates: Dict[str, str]) -> None:
    """Persist command templates as pretty JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(templates, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_env_enable_escalation(env_path: Path, fallback: bool) -> bool:
    """Read AGENTHUB_ENABLE_ESCALATION from .env file if available."""

    if not env_path.exists():
        return fallback

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("AGENTHUB_ENABLE_ESCALATION="):
            continue
        raw_value = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
        return raw_value in {"1", "true", "yes", "on"}
    return fallback


def _set_env_value(env_path: Path, key: str, value: str) -> None:
    """Set or append one KEY=value entry in .env while preserving other lines."""

    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()

    prefix = f"{key}="
    replaced = False
    updated: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith(f"#{prefix}"):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)

    if not replaced:
        updated.append(f"{key}={value}")

    env_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _check_one_cli(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Probe one CLI using template-derived paths then PATH fallback."""

    candidates = _build_cli_probe_candidates(cli_name, templates)
    for args in candidates:
        probe = _run_probe(args)
        if probe["ok"]:
            return {
                "ok": True,
                "command": " ".join(args),
                "output": probe["output"],
            }

    # Return the last failure detail for easier debugging.
    last = _run_probe(candidates[-1])
    return {
        "ok": False,
        "command": " ".join(candidates[-1]),
        "output": last["output"],
    }


def _build_cli_probe_candidates(cli_name: str, templates: Dict[str, str]) -> List[List[str]]:
    """Build probe command candidates from known paths and templates."""

    known: List[List[str]] = []
    template_text = " ".join(templates.values())
    absolute_paths = re.findall(r"(/[^ \t\"']+)", template_text)
    node_paths = [path for path in absolute_paths if path.endswith("/node")]
    cli_paths = [
        path
        for path in absolute_paths
        if path.endswith(f"/{cli_name}") or path.endswith(f"/{cli_name}.js")
    ]

    for path in cli_paths:
        if node_paths and path.startswith("/"):
            known.append([node_paths[0], path, "--version"])
        known.append([path, "--version"])

    # Fallback to PATH
    known.append([cli_name, "--version"])

    deduped: List[List[str]] = []
    seen: set[str] = set()
    for args in known:
        key = " ".join(args)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(args)
    return deduped


def _run_probe(args: List[str]) -> Dict[str, Any]:
    """Run one probe command and capture compact output."""

    try:
        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "timeout"}

    output = (process.stdout or process.stderr or "").strip().splitlines()
    first_line = output[0] if output else ""
    return {"ok": process.returncode == 0, "output": first_line[:240]}


def _infer_cli_model(cli_name: str, templates: Dict[str, str]) -> Dict[str, Any]:
    """Infer model name from command templates first, then environment."""

    from_template = _infer_model_from_templates(cli_name, templates)
    if from_template is not None:
        return {
            "model": from_template["model"],
            "source": from_template["source"],
            "template_key": from_template["template_key"],
        }

    from_env = _infer_model_from_env(cli_name)
    if from_env is not None:
        return {
            "model": from_env["model"],
            "source": from_env["source"],
            "template_key": "",
        }

    from_runtime = _infer_model_from_runtime_files(cli_name)
    if from_runtime is not None:
        return {
            "model": from_runtime["model"],
            "source": from_runtime["source"],
            "template_key": "",
        }

    return {
        "model": "",
        "source": "not_found",
        "template_key": "",
    }


def _infer_model_from_templates(cli_name: str, templates: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Find explicit --model/-m style option in matching template command."""

    for key, command in templates.items():
        lowered = command.lower()
        if cli_name not in lowered:
            continue

        # --model value
        match = re.search(r"(?:--model|-m)\s+([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_flag",
                "template_key": key,
            }

        # key=value styles
        match = re.search(r"(?:model|MODEL)=([^\s\"']+)", command)
        if match:
            return {
                "model": match.group(1),
                "source": "template_assignment",
                "template_key": key,
            }
    return None


def _infer_model_from_env(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from common environment variable names."""

    candidates: Dict[str, List[str]] = {
        "gemini": ["GEMINI_MODEL", "AGENTHUB_GEMINI_MODEL"],
        "codex": ["CODEX_MODEL", "OPENAI_MODEL", "AGENTHUB_CODEX_MODEL"],
        "claude": ["CLAUDE_MODEL", "ANTHROPIC_MODEL", "AGENTHUB_CLAUDE_MODEL"],
    }
    for env_name in candidates.get(cli_name, []):
        value = os.getenv(env_name, "").strip()
        if value:
            return {"model": value, "source": f"env:{env_name}"}
    return None


def _infer_model_from_runtime_files(cli_name: str) -> Optional[Dict[str, str]]:
    """Infer model from the latest local runtime/session files."""

    if cli_name == "gemini":
        candidates = _recent_files(Path("/root/.gemini"), "tmp/**/chats/*.json")
        model = _find_model_in_recent_files(candidates, [r'"model"\s*:\s*"([^"]+)"'])
        if model:
            return {"model": model, "source": "runtime:gemini_chats"}
        return None

    if cli_name == "claude":
        candidates = _recent_files(Path("/root/.claude"), "projects/**/*.jsonl")
        model = _find_model_in_recent_files(candidates, [r'"model"\s*:\s*"([^"]+)"'])
        if model:
            return {"model": model, "source": "runtime:claude_projects"}
        return None

    if cli_name == "codex":
        files: List[Path] = []
        files.extend(_recent_files(Path("/root/.codex"), "history.jsonl", limit=1))
        files.extend(_recent_files(Path("/root/.codex"), "sessions/**/*.jsonl"))
        model = _find_model_in_recent_files(
            files,
            [
                r'"model"\s*:\s*"([^"]+)"',
                r'"model_slug"\s*:\s*"([^"]+)"',
                r'"model_name"\s*:\s*"([^"]+)"',
            ],
        )
        if model:
            return {"model": model, "source": "runtime:codex_sessions"}
        return None

    return None


def _recent_files(base: Path, pattern: str, limit: int = 20) -> List[Path]:
    """Return recent files matching glob pattern, newest first."""

    if not base.exists():
        return []
    matched = [path for path in base.glob(pattern) if path.is_file()]
    matched.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return matched[:limit]


def _find_model_in_recent_files(files: List[Path], regexes: List[str]) -> Optional[str]:
    """Search recent files for model-like fields and return the first match."""

    compiled = [re.compile(pattern) for pattern in regexes]
    for file_path in files:
        text = _read_file_tail(file_path, max_bytes=250_000)
        for regex in compiled:
            matches = regex.findall(text)
            if not matches:
                continue
            # Prefer latest entry by taking the last match in the file tail.
            candidate = str(matches[-1]).strip()
            if candidate:
                return candidate
    return None


def _read_file_tail(path: Path, max_bytes: int) -> str:
    """Read at most `max_bytes` from the end of file as text."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            raw = handle.read()
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _ensure_agent_run_label(repository: str) -> None:
    """Ensure `agent:run` label exists in the target repository."""

    _ensure_label(
        repository=repository,
        label_name="agent:run",
        color="1D76DB",
        description="Trigger AgentHub worker",
    )


def _ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
    """Ensure one GitHub label exists in the target repository."""

    process = subprocess.run(
        [
            "gh",
            "label",
            "create",
            label_name,
            "--repo",
            repository,
            "--color",
            color,
            "--description",
            description,
        ],
        capture_output=True,
        text=True,
    )

    if process.returncode == 0:
        return

    stderr_lower = (process.stderr or "").lower()
    if "already exists" in stderr_lower or "name already exists" in stderr_lower:
        return

    stderr_preview = (process.stderr or "").strip()[:500]
    raise HTTPException(
        status_code=502,
        detail=(
            f"{label_name} 라벨 자동 생성 실패: gh CLI 상태를 확인해주세요. "
            f"stderr: {stderr_preview or '(no stderr)'}"
        ),
    )


def _normalize_app_code(value: str) -> str:
    """Normalize app code for labels and branch/workspace names."""

    lowered = (value or "").strip().lower()
    if not lowered:
        return ""
    if not _APP_CODE_PATTERN.match(lowered):
        return ""
    return lowered


def _normalize_track(value: str) -> str:
    """Normalize track value to one of known choices."""

    lowered = (value or "").strip().lower()
    if lowered in {"ultra", "초장기"}:
        lowered = "ultra"
    if lowered in {"longterm", "long-term", "장기"}:
        lowered = "long"
    if lowered in _TRACK_CHOICES:
        return lowered
    return "new"


def _detect_title_track(title: str) -> str:
    """Detect explicit title marker track override."""

    lowered = (title or "").strip().lower()
    if "[초장기]" in lowered or "[ultra]" in lowered:
        return "ultra"
    if "[장기]" in lowered or "[long]" in lowered:
        return "long"
    return ""


def _build_branch_name(app_code: str, issue_number: int, job_id: str) -> str:
    """Build namespaced branch name for one job."""

    return f"agenthub/{app_code}/issue-{issue_number}-{job_id[:8]}"


def _build_log_file_name(app_code: str, job_id: str) -> str:
    """Build one safe log file name."""

    return f"{app_code}--{job_id}.log"


def _stop_signal_path(data_dir: Path, job_id: str) -> Path:
    """Return stop signal file path for one job."""

    return data_dir / "control" / f"stop_{job_id}.flag"


def _read_registered_apps(
    path: Path,
    repository: str,
    default_workflow_id: str = "",
) -> List[Dict[str, str]]:
    """Read app registration list from JSON file with a default fallback."""

    resolved_default_workflow_id = default_workflow_id.strip() or default_workflow_template()["workflow_id"]
    defaults = [
        {
            "code": "default",
            "name": "Default",
            "repository": repository,
            "workflow_id": resolved_default_workflow_id,
        }
    ]
    if not path.exists():
        return defaults

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return defaults

    if not isinstance(payload, list):
        return defaults

    collected: List[Dict[str, str]] = []
    has_default = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = _normalize_app_code(str(item.get("code", "")))
        if not code:
            continue
        name = str(item.get("name", code)).strip() or code
        app_repository = str(item.get("repository", repository)).strip() or repository
        workflow_id = str(item.get("workflow_id", resolved_default_workflow_id)).strip() or resolved_default_workflow_id
        collected.append(
            {
                "code": code,
                "name": name,
                "repository": app_repository,
                "workflow_id": workflow_id,
            }
        )
        if code == "default":
            has_default = True

    collected.sort(key=lambda one: one["code"])
    if not has_default:
        collected.insert(0, defaults[0])
    return collected


def _write_registered_apps(path: Path, apps: List[Dict[str, str]]) -> None:
    """Persist app list as pretty JSON."""

    dedup: Dict[str, Dict[str, str]] = {}
    for app in apps:
        code = _normalize_app_code(app.get("code", ""))
        if not code:
            continue
        name = str(app.get("name", code)).strip() or code
        repository = str(app.get("repository", "")).strip()
        workflow_id = str(app.get("workflow_id", "")).strip() or default_workflow_template()["workflow_id"]
        dedup[code] = {"code": code, "name": name, "repository": repository, "workflow_id": workflow_id}

    if "default" not in dedup:
        dedup["default"] = {
            "code": "default",
            "name": "Default",
            "repository": "",
            "workflow_id": default_workflow_template()["workflow_id"],
        }

    ordered = [dedup[key] for key in sorted(dedup)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_default_workflow_id(path: Path) -> str:
    """Read default workflow id from workflow config with safe fallback."""

    payload = load_workflows(path)
    default_workflow_id = str(payload.get("default_workflow_id", "")).strip()
    if default_workflow_id:
        return default_workflow_id
    return default_workflow_template()["workflow_id"]


def _find_active_job(
    store: JobStore,
    repository: str,
    issue_number: int,
) -> Optional[JobRecord]:
    """Find an already-active job for the same repository issue."""

    for item in store.list_jobs():
        if item.repository != repository:
            continue
        if item.issue_number != issue_number:
            continue
        if item.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            return item
    return None
