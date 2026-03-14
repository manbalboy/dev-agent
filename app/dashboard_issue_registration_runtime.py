"""Issue registration helper runtime for dashboard APIs."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from fastapi import HTTPException

from app.config import AppSettings
from app.models import JobRecord, JobStage, JobStatus
from app.store import JobStore


class DashboardIssueRegistrationRuntime:
    """Encapsulate issue creation, labeling, and enqueue behavior."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        apps_config_path: Any,
        workflows_config_path: Any,
        roles_config_path: Any,
        ensure_patch_accepting_new_jobs: Callable[[], None],
        normalize_app_code: Callable[[str], str],
        normalize_track: Callable[[str], str],
        detect_title_track: Callable[[str], str],
        normalize_role_code: Callable[[str], str],
        read_registered_apps: Callable[..., List[Dict[str, str]]],
        list_known_workflow_ids: Callable[[Any], Any],
        read_roles_payload: Callable[[Any], Dict[str, Any]],
        run_gh_command: Callable[[List[str], str], str],
        extract_issue_url: Callable[[str], str],
        extract_issue_number: Callable[[str], int],
        ensure_agent_run_label: Callable[[str], None],
        ensure_label: Callable[[str, str, str, str], None],
        find_active_job: Callable[[JobStore, str, int], JobRecord | None],
        resolve_workflow_selection: Callable[..., Any],
        build_branch_name: Callable[..., str],
        build_log_file_name: Callable[[str, str], str],
        utc_now_iso: Callable[[], str],
        uuid_factory: Callable[[], str],
    ) -> None:
        self.store = store
        self.settings = settings
        self.apps_config_path = apps_config_path
        self.workflows_config_path = workflows_config_path
        self.roles_config_path = roles_config_path
        self.ensure_patch_accepting_new_jobs = ensure_patch_accepting_new_jobs
        self.normalize_app_code = normalize_app_code
        self.normalize_track = normalize_track
        self.detect_title_track = detect_title_track
        self.normalize_role_code = normalize_role_code
        self.read_registered_apps = read_registered_apps
        self.list_known_workflow_ids = list_known_workflow_ids
        self.read_roles_payload = read_roles_payload
        self.run_gh_command = run_gh_command
        self.extract_issue_url = extract_issue_url
        self.extract_issue_number = extract_issue_number
        self.ensure_agent_run_label = ensure_agent_run_label
        self.ensure_label = ensure_label
        self.find_active_job = find_active_job
        self.resolve_workflow_selection = resolve_workflow_selection
        self.build_branch_name = build_branch_name
        self.build_log_file_name = build_log_file_name
        self.utc_now_iso = utc_now_iso
        self.uuid_factory = uuid_factory

    def register_issue(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create one GitHub issue, label it, and trigger a local job immediately."""

        self.ensure_patch_accepting_new_jobs()

        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(status_code=400, detail="이슈 제목은 필수입니다.")

        body = str(payload.get("body", "")).strip() or "AgentHub 대시보드에서 등록된 작업 이슈입니다."
        app_code = self.normalize_app_code(str(payload.get("app_code", ""))) or "default"
        track = self.normalize_track(str(payload.get("track", "")))
        keep_branch = bool(payload.get("keep_branch", True))
        requested_branch_name = str(payload.get("branch_name", "") or "").strip()
        role_preset_id = self.normalize_role_code(str(payload.get("role_preset_id", "") or ""))
        requested_workflow_id = str(payload.get("workflow_id", "") or "").strip()

        title_track = self.detect_title_track(title)
        if title_track:
            track = title_track

        repository = self.settings.allowed_repository
        registered_apps = self.read_registered_apps(self.apps_config_path, repository)
        app_entry = next((item for item in registered_apps if item.get("code") == app_code), None)
        if app_entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"등록되지 않은 앱 코드입니다: {app_code}. 설정 메뉴에서 먼저 등록해주세요.",
            )
        source_repository = str(app_entry.get("source_repository", "")).strip()

        if requested_workflow_id:
            known_workflow_ids = set(self.list_known_workflow_ids(self.workflows_config_path))
            if requested_workflow_id not in known_workflow_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"등록되지 않은 workflow_id 입니다: {requested_workflow_id}",
                )

        if role_preset_id:
            roles_payload = self.read_roles_payload(self.roles_config_path)
            presets = roles_payload.get("presets", []) if isinstance(roles_payload, dict) else []
            matched = next(
                (
                    item
                    for item in presets
                    if self.normalize_role_code(str(item.get("preset_id", ""))) == role_preset_id
                ),
                None,
            )
            if matched is None:
                raise HTTPException(status_code=400, detail=f"등록되지 않은 역할 프리셋입니다: {role_preset_id}")
            role_codes = matched.get("role_codes", []) if isinstance(matched, dict) else []
            body = (
                f"{body}\n\n"
                "## ROLE PRESET\n"
                f"- preset_id: `{role_preset_id}`\n"
                f"- roles: {', '.join(f'`{code}`' for code in role_codes) if role_codes else '(none)'}\n"
            )

        create_stdout = self.run_gh_command(
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
        issue_url = self.extract_issue_url(create_stdout)
        issue_number = self.extract_issue_number(issue_url)

        self.ensure_agent_run_label(repository)
        self.ensure_label(repository, f"app:{app_code}", "0052CC", f"AgentHub app namespace ({app_code})")
        self.ensure_label(repository, f"track:{track}", "5319E7", f"AgentHub work type ({track})")

        self.run_gh_command(
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

        existing = self.find_active_job(self.store, repository, issue_number)
        if existing is not None:
            return {
                "accepted": True,
                "created_issue": True,
                "triggered": False,
                "reason": "already_active_job",
                "job_id": existing.job_id,
                "issue_number": issue_number,
                "issue_url": issue_url,
            }

        now = self.utc_now_iso()
        job_id = self.uuid_factory()
        workflow_selection = self.resolve_workflow_selection(
            requested_workflow_id=requested_workflow_id,
            app_code=app_code,
            repository=repository,
            apps_path=self.apps_config_path,
            workflows_path=self.workflows_config_path,
        )
        job = JobRecord(
            job_id=job_id,
            repository=repository,
            issue_number=issue_number,
            issue_title=title,
            issue_url=issue_url,
            status=JobStatus.QUEUED.value,
            stage=JobStage.QUEUED.value,
            attempt=0,
            max_attempts=self.settings.max_retries,
            branch_name=self.build_branch_name(
                app_code,
                issue_number,
                track,
                job_id,
                keep_branch=keep_branch,
                requested_branch_name=requested_branch_name,
            ),
            pr_url=None,
            error_message=None,
            log_file=self.build_log_file_name(app_code, job_id),
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
            app_code=app_code,
            track=track,
            workflow_id=str(getattr(workflow_selection, "workflow_id", "")).strip(),
            source_repository=source_repository,
        )

        self.store.create_job(job)
        self.store.enqueue_job(job_id)

        return {
            "accepted": True,
            "created_issue": True,
            "triggered": True,
            "job_id": job_id,
            "issue_number": issue_number,
            "issue_url": issue_url,
            "app_code": app_code,
            "track": track,
            "workflow_id": str(getattr(workflow_selection, "workflow_id", "")).strip(),
            "workflow_source": str(getattr(workflow_selection, "source", "")).strip(),
            "source_repository": source_repository,
            "keep_branch": keep_branch,
            "role_preset_id": role_preset_id,
        }
