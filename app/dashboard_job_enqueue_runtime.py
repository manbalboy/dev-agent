"""Shared job-enqueue helpers used by dashboard issue/follow-up flows."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from app.config import AppSettings
from app.models import JobRecord, JobStage, JobStatus
from app.store import JobStore


_APP_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_TRACK_CHOICES = {"new", "enhance", "bug", "long", "ultra", "ultra10"}


class DashboardJobEnqueueRuntime:
    """Encapsulate branch/log naming and follow-up enqueue helpers."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        apps_config_path: Path,
        workflows_config_path: Path,
        resolve_workflow_selection: Callable[..., Any],
        build_workflow_artifact_paths: Callable[[Path], Dict[str, Path]],
        utc_now_iso: Callable[[], str],
        uuid_factory: Callable[[], str],
    ) -> None:
        self.store = store
        self.settings = settings
        self.apps_config_path = apps_config_path
        self.workflows_config_path = workflows_config_path
        self.resolve_workflow_selection = resolve_workflow_selection
        self.build_workflow_artifact_paths = build_workflow_artifact_paths
        self.utc_now_iso = utc_now_iso
        self.uuid_factory = uuid_factory

    @staticmethod
    def normalize_app_code(value: str) -> str:
        """Normalize app code for labels and branch/workspace names."""

        lowered = (value or "").strip().lower()
        if not lowered:
            return ""
        if not _APP_CODE_PATTERN.match(lowered):
            return ""
        return lowered

    @staticmethod
    def normalize_track(value: str) -> str:
        """Normalize track value to one of known choices."""

        lowered = (value or "").strip().lower()
        if lowered in {"ultra10", "ultra-10", "초초장기"}:
            lowered = "ultra10"
        if lowered in {"ultra", "초장기"}:
            lowered = "ultra"
        if lowered in {"longterm", "long-term", "장기"}:
            lowered = "long"
        if lowered in _TRACK_CHOICES:
            return lowered
        return "enhance"

    @staticmethod
    def detect_title_track(title: str) -> str:
        """Detect explicit title marker track override."""

        lowered = (title or "").strip().lower()
        if "[초초장기]" in lowered or "[ultra10]" in lowered:
            return "ultra10"
        if "[초장기]" in lowered or "[ultra]" in lowered:
            return "ultra"
        if "[장기]" in lowered or "[long]" in lowered:
            return "long"
        return ""

    @staticmethod
    def build_branch_name(
        app_code: str,
        issue_number: int,
        track: str,
        job_id: str,
        keep_branch: bool = True,
        requested_branch_name: str = "",
    ) -> str:
        """Build namespaced branch name for one job."""

        custom = DashboardJobEnqueueRuntime._sanitize_branch_name(requested_branch_name)
        if custom:
            return custom
        if keep_branch:
            return f"agenthub/{app_code}/issue-{issue_number}"
        if track == "enhance":
            return f"agenthub/{app_code}/issue-{issue_number}-enhance"
        return f"agenthub/{app_code}/issue-{issue_number}-{job_id[:8]}"

    @staticmethod
    def build_log_file_name(app_code: str, job_id: str) -> str:
        """Build one safe log file name."""

        return f"{app_code}--{job_id}.log"

    @staticmethod
    def find_active_job(
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

    def queue_followup_job_from_backlog_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        runtime_store: Any,
        note: str,
    ) -> tuple[JobRecord, Path]:
        """Queue one follow-up job that consumes an approved backlog candidate."""

        payload = candidate.get("payload", {}) if isinstance(candidate.get("payload"), dict) else {}
        source_job_id = str(payload.get("job_id", "")).strip()
        source_job = self.store.get_job(source_job_id) if source_job_id else None
        candidate_id = str(candidate.get("candidate_id", "")).strip()

        repository = str(candidate.get("repository", "")).strip() or self.settings.allowed_repository
        execution_repository = (
            str(candidate.get("execution_repository", "")).strip()
            or (str(source_job.source_repository or "").strip() if source_job is not None else "")
            or (str(source_job.repository or "").strip() if source_job is not None else "")
            or repository
        )
        source_repository = execution_repository if execution_repository and execution_repository != repository else ""
        app_code = self.normalize_app_code(
            str(candidate.get("app_code", "")).strip()
            or (source_job.app_code if source_job is not None else "default")
        ) or "default"

        issue_number = int(payload.get("issue_number", 0) or (source_job.issue_number if source_job is not None else 0) or 0)
        if issue_number <= 0:
            raise HTTPException(
                status_code=400,
                detail="현재 follow-up bridge는 기존 GitHub issue에 연결된 backlog 후보만 큐잉할 수 있습니다.",
            )
        issue_url = (
            str(source_job.issue_url or "").strip()
            if source_job is not None
            else f"https://github.com/{repository}/issues/{issue_number}"
        )
        if not issue_url:
            issue_url = f"https://github.com/{repository}/issues/{issue_number}"

        workflow_id = str(candidate.get("workflow_id", "")).strip() or (
            str(source_job.workflow_id or "").strip() if source_job is not None else ""
        )
        if not workflow_id:
            selection = self.resolve_workflow_selection(
                requested_workflow_id="",
                app_code=app_code,
                repository=repository,
                apps_path=self.apps_config_path,
                workflows_path=self.workflows_config_path,
            )
            workflow_id = selection.workflow_id

        track = "enhance"
        if source_job is not None:
            source_track = self.normalize_track(source_job.track)
            if source_track in {"bug", "new", "enhance"}:
                track = source_track

        now = self.utc_now_iso()
        queued_job_id = self.uuid_factory()
        followup_title = f"[Follow-up] {str(candidate.get('title', '')).strip() or f'Issue {issue_number} improvement'}"
        queued_job = JobRecord(
            job_id=queued_job_id,
            repository=repository,
            issue_number=issue_number,
            issue_title=followup_title,
            issue_url=issue_url,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            attempt=0,
            max_attempts=self.settings.max_retries,
            branch_name=self.build_branch_name(
                app_code,
                issue_number,
                track,
                queued_job_id,
                keep_branch=True,
            ),
            pr_url=None,
            error_message=None,
            log_file=self.build_log_file_name(app_code, queued_job_id),
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            app_code=app_code,
            track=track,
            workflow_id=workflow_id,
            source_repository=source_repository,
            job_kind="followup_backlog",
            parent_job_id=source_job_id,
            backlog_candidate_id=candidate_id,
        )
        self.store.create_job(queued_job)
        self.store.enqueue_job(queued_job_id)

        artifact_path = self._write_followup_backlog_artifact(
            candidate=candidate,
            queued_job=queued_job,
            note=note,
            source_job=source_job,
        )
        runtime_store.set_backlog_candidate_state(
            str(candidate.get("candidate_id", "")).strip(),
            state="queued",
            payload_updates={
                "approved_at": str(payload.get("approved_at", "")).strip() or now,
                "queued_at": now,
                "queued_job_id": queued_job_id,
                "queued_job_kind": queued_job.job_kind,
                "queued_job_issue_number": issue_number,
                "queued_job_issue_url": issue_url,
                "parent_job_id": source_job_id,
                "backlog_candidate_id": candidate_id,
                "followup_artifact_path": str(artifact_path),
                "operator_note": note,
                "last_action": "queue",
            },
        )
        return queued_job, artifact_path

    @staticmethod
    def _sanitize_branch_name(value: str) -> str:
        """Best-effort sanitize for user-provided branch names."""

        name = (value or "").strip()
        if not name:
            return ""
        allowed = re.sub(r"[^a-zA-Z0-9/_-]", "-", name)
        collapsed = re.sub(r"/{2,}", "/", allowed).strip("/ ")
        if not collapsed:
            return ""
        return collapsed[:120]

    def _write_followup_backlog_artifact(
        self,
        *,
        candidate: Dict[str, Any],
        queued_job: JobRecord,
        note: str,
        source_job: Optional[JobRecord],
    ) -> Path:
        """Write one explicit follow-up backlog artifact for the next planner round."""

        workspace_repository = str(queued_job.source_repository or queued_job.repository or "").strip()
        workspace_path = self.settings.repository_workspace_path(workspace_repository, queued_job.app_code)
        paths = self.build_workflow_artifact_paths(workspace_path)
        artifact_path = paths["followup_backlog_task"]
        payload = candidate.get("payload", {}) if isinstance(candidate.get("payload"), dict) else {}
        artifact_payload = {
            "generated_at": self.utc_now_iso(),
            "source": "memory_backlog_candidate",
            "job_contract": {
                "kind": "followup_backlog",
                "version": "v1",
                "issue_backed": True,
                "dedicated_followup": True,
            },
            "candidate_id": str(candidate.get("candidate_id", "")).strip(),
            "title": str(candidate.get("title", "")).strip(),
            "summary": str(candidate.get("summary", "")).strip(),
            "priority": str(candidate.get("priority", "P2")).strip() or "P2",
            "state": "queued",
            "queued_job_id": queued_job.job_id,
            "queued_job_kind": str(queued_job.job_kind or "").strip() or "followup_backlog",
            "queued_job_issue_number": queued_job.issue_number,
            "queued_job_issue_url": queued_job.issue_url,
            "workflow_id": queued_job.workflow_id,
            "app_code": queued_job.app_code,
            "track": queued_job.track,
            "parent_job_id": str(queued_job.parent_job_id or "").strip(),
            "backlog_candidate_id": str(queued_job.backlog_candidate_id or "").strip(),
            "recommended_node_type": str(payload.get("recommended_node_type", "")).strip(),
            "recommended_action": (
                str(payload.get("action", "")).strip()
                or str(payload.get("recommended_action", "")).strip()
            ),
            "source_kind": str(payload.get("source_kind", "")).strip(),
            "source_job_id": str(payload.get("job_id", "")).strip(),
            "source_issue_number": int(payload.get("issue_number", queued_job.issue_number) or queued_job.issue_number),
            "source_issue_title": str(payload.get("issue_title", "")).strip()
            or (str(source_job.issue_title or "").strip() if source_job is not None else ""),
            "source_job_kind": str(source_job.job_kind or "").strip() if source_job is not None else "issue",
            "cluster_key": str(payload.get("cluster_key", "")).strip(),
            "operator_note": note,
            "raw_payload": payload,
        }
        artifact_path.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return artifact_path
