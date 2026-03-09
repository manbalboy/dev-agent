"""GitHub webhook endpoint for issue-label based job creation."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any, Dict
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import AppSettings
from app.dependencies import get_settings, get_store
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.store import JobStore
from app.workflow_resolution import (
    list_known_workflow_ids,
    read_registered_apps,
    resolve_workflow_selection,
)


router = APIRouter(tags=["webhook"])
_APPS_CONFIG_PATH = Path.cwd() / "config" / "apps.json"
_WORKFLOWS_CONFIG_PATH = Path.cwd() / "config" / "workflows.json"



def verify_github_signature(secret: str, payload: bytes, signature_header: str) -> bool:
    """Validate GitHub `X-Hub-Signature-256` header with HMAC SHA256."""

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


@router.post("/webhooks/github")
async def receive_github_issue_webhook(
    request: Request,
    settings: AppSettings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> Dict[str, Any]:
    """Handle GitHub `issues` webhook and enqueue jobs on `agent:run` labels."""

    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    if not verify_github_signature(settings.webhook_secret, raw_body, signature_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Webhook signature verification failed. "
                "Next action: verify AGENTHUB_WEBHOOK_SECRET matches GitHub webhook setting."
            ),
        )

    event_name = request.headers.get("X-GitHub-Event", "")
    if event_name != "issues":
        return {"accepted": False, "reason": "ignored_event"}

    payload = await request.json()

    action = payload.get("action")
    label_name = ((payload.get("label") or {}).get("name") or "").strip()
    repository_name = ((payload.get("repository") or {}).get("full_name") or "").strip()

    if repository_name != settings.allowed_repository:
        return {
            "accepted": False,
            "reason": "repository_not_allowed",
            "repository": repository_name,
        }

    if action != "labeled" or label_name != "agent:run":
        return {"accepted": False, "reason": "label_condition_not_met"}

    issue = payload.get("issue") or {}
    issue_number = int(issue.get("number", 0))
    issue_title = str(issue.get("title", "(untitled issue)"))
    issue_url = str(issue.get("html_url", ""))
    labels = issue.get("labels") or []
    app_code = _extract_prefixed_label(labels, "app:", default="default")
    track = _normalize_track(_extract_prefixed_label(labels, "track:", default="enhance"))
    requested_workflow_id = _extract_prefixed_label(labels, "workflow:", default="")
    title_track = _detect_title_track(issue_title)
    if title_track:
        track = title_track
    if requested_workflow_id:
        known_workflow_ids = list_known_workflow_ids(_WORKFLOWS_CONFIG_PATH)
        if requested_workflow_id not in known_workflow_ids:
            return {
                "accepted": False,
                "reason": "invalid_workflow_id",
                "workflow_id": requested_workflow_id,
            }

    existing = _find_active_job(store, repository_name, issue_number)
    if existing is not None:
        return {
            "accepted": True,
            "reason": "already_active_job",
            "job_id": existing.job_id,
            "status": existing.status,
            "stage": existing.stage,
        }

    now = utc_now_iso()
    job_id = str(uuid.uuid4())
    branch_name = _build_branch_name(app_code, issue_number, track, job_id)
    log_file = f"{app_code}--{job_id}.log"
    workflow_selection = resolve_workflow_selection(
        requested_workflow_id=requested_workflow_id,
        app_code=app_code,
        repository=repository_name,
        apps_path=_APPS_CONFIG_PATH,
        workflows_path=_WORKFLOWS_CONFIG_PATH,
    )
    registered_apps = read_registered_apps(_APPS_CONFIG_PATH, repository_name)
    app_entry = next((item for item in registered_apps if item.get("code") == app_code), None)
    source_repository = str((app_entry or {}).get("source_repository", "")).strip()

    job = JobRecord(
        job_id=job_id,
        repository=repository_name,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=issue_url,
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=settings.max_retries,
        branch_name=branch_name,
        pr_url=None,
        error_message=None,
        log_file=log_file,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code=app_code,
        track=track,
        workflow_id=workflow_selection.workflow_id,
        source_repository=source_repository,
    )

    store.create_job(job)
    store.enqueue_job(job_id)

    return {
        "accepted": True,
        "job_id": job_id,
        "status": job.status,
        "stage": job.stage,
        "app_code": app_code,
        "track": track,
        "workflow_id": workflow_selection.workflow_id,
        "workflow_source": workflow_selection.source,
        "source_repository": source_repository,
    }


def _find_active_job(
    store: JobStore,
    repository: str,
    issue_number: int,
) -> JobRecord | None:
    """Find an already-active job for the same repository issue."""

    for item in store.list_jobs():
        if item.repository != repository:
            continue
        if item.issue_number != issue_number:
            continue
        if item.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            return item
    return None


def _extract_prefixed_label(labels: Any, prefix: str, default: str) -> str:
    """Extract first label name with a specific prefix."""

    if not isinstance(labels, list):
        return default
    lowered_prefix = prefix.lower()
    for item in labels:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip().lower()
        if not name.startswith(lowered_prefix):
            continue
        suffix = name[len(lowered_prefix):].strip()
        if suffix:
            return suffix
    return default


def _detect_title_track(title: str) -> str:
    """Detect explicit title marker track override."""

    lowered = (title or "").strip().lower()
    if "[초장기]" in lowered or "[ultra]" in lowered:
        return "ultra"
    if "[장기]" in lowered or "[long]" in lowered:
        return "long"
    return ""


def _normalize_track(value: str) -> str:
    """Normalize track label from webhook payload."""

    lowered = (value or "").strip().lower()
    if lowered in {"ultra", "초장기"}:
        return "ultra"
    if lowered in {"long", "장기", "longterm", "long-term"}:
        return "long"
    if lowered in {"new", "enhance", "bug"}:
        return lowered
    return "enhance"


def _build_branch_name(app_code: str, issue_number: int, track: str, job_id: str) -> str:
    """Build branch name for one job.

    Long-horizon tracks intentionally reuse stable issue branches so follow-up
    runs continue from previous commits.
    """

    if track in {"long", "ultra"}:
        return f"agenthub/{app_code}/issue-{issue_number}"
    if track == "enhance":
        return f"agenthub/{app_code}/issue-{issue_number}-enhance"
    return f"agenthub/{app_code}/issue-{issue_number}-{job_id[:8]}"
