from __future__ import annotations

import json
from pathlib import Path

import app.dashboard as dashboard
from app.dashboard_job_enqueue_runtime import DashboardJobEnqueueRuntime
from app.dashboard_issue_registration_runtime import DashboardIssueRegistrationRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _write_workflow_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [
                    {
                        "workflow_id": "wf-default",
                        "name": "Default",
                        "version": 1,
                        "entry_node_id": "n1",
                        "nodes": [{"id": "n1", "type": "gh_read_issue"}],
                        "edges": [],
                    },
                    {
                        "workflow_id": "wf-special",
                        "name": "Special",
                        "version": 2,
                        "entry_node_id": "n1",
                        "nodes": [{"id": "n1", "type": "gh_read_issue"}],
                        "edges": [],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_apps(path: Path, *, source_repository: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "code": "default",
                    "name": "Default",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                    "source_repository": "",
                },
                {
                    "code": "food",
                    "name": "Food",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                    "source_repository": source_repository,
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _make_job(job_id: str, *, issue_number: int, status: str = JobStatus.QUEUED.value) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=issue_number,
        issue_title="existing issue",
        issue_url=f"https://github.com/owner/repo/issues/{issue_number}",
        status=status,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name=f"agenthub/default/issue-{issue_number}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
        track="enhance",
        workflow_id="wf-default",
    )


def _build_runtime(app_components, tmp_path: Path, gh_calls: list[tuple], ensure_patch=None) -> DashboardIssueRegistrationRuntime:
    settings, store, _ = app_components
    apps_path = tmp_path / "config" / "apps.json"
    workflows_path = tmp_path / "config" / "workflows.json"
    roles_path = tmp_path / "config" / "roles.json"
    _write_workflow_catalog(workflows_path)
    _write_apps(apps_path, source_repository="manbalboy/Food")

    def fake_run_gh_command(args, error_context):
        gh_calls.append((tuple(args), error_context))
        if "create" in args:
            return "https://github.com/owner/repo/issues/501"
        return ""

    return DashboardIssueRegistrationRuntime(
        store=store,
        settings=settings,
        apps_config_path=apps_path,
        workflows_config_path=workflows_path,
        roles_config_path=roles_path,
        ensure_patch_accepting_new_jobs=ensure_patch or (lambda: None),
        normalize_app_code=DashboardJobEnqueueRuntime.normalize_app_code,
        normalize_track=DashboardJobEnqueueRuntime.normalize_track,
        detect_title_track=DashboardJobEnqueueRuntime.detect_title_track,
        normalize_role_code=dashboard.normalize_role_code,
        read_registered_apps=dashboard._read_registered_apps,
        list_known_workflow_ids=dashboard.list_known_workflow_ids,
        read_roles_payload=dashboard.read_roles_payload,
        run_gh_command=fake_run_gh_command,
        extract_issue_url=dashboard._extract_issue_url,
        extract_issue_number=dashboard._extract_issue_number,
        ensure_agent_run_label=lambda repository: gh_calls.append((("ensure_agent_run_label", repository), "")),
        ensure_label=lambda repository, label_name, color, description: gh_calls.append(
            (("ensure_label", repository, label_name, color, description), "")
        ),
        find_active_job=DashboardJobEnqueueRuntime.find_active_job,
        resolve_workflow_selection=dashboard.resolve_workflow_selection,
        build_branch_name=DashboardJobEnqueueRuntime.build_branch_name,
        build_log_file_name=DashboardJobEnqueueRuntime.build_log_file_name,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        uuid_factory=lambda: "job-uuid-1",
    )


def test_dashboard_issue_registration_runtime_stores_workflow_override_and_source_repository(
    app_components, tmp_path: Path
) -> None:
    gh_calls: list[tuple] = []
    runtime = _build_runtime(app_components, tmp_path, gh_calls)
    _, store, _ = app_components

    payload = runtime.register_issue(
        {
            "title": "Workflow override issue",
            "body": "Run with special workflow",
            "app_code": "food",
            "track": "enhance",
            "workflow_id": "wf-special",
        }
    )

    assert payload["accepted"] is True
    assert payload["triggered"] is True
    assert payload["workflow_id"] == "wf-special"
    assert payload["workflow_source"] == "job"
    assert payload["source_repository"] == "manbalboy/Food"

    stored = store.get_job(payload["job_id"])
    assert stored is not None
    assert stored.workflow_id == "wf-special"
    assert stored.source_repository == "manbalboy/Food"


def test_dashboard_issue_registration_runtime_returns_existing_active_job(app_components, tmp_path: Path) -> None:
    gh_calls: list[tuple] = []
    runtime = _build_runtime(app_components, tmp_path, gh_calls)
    _, store, _ = app_components
    existing = _make_job("job-existing", issue_number=501, status=JobStatus.RUNNING.value)
    store.create_job(existing)

    payload = runtime.register_issue(
        {
            "title": "Existing issue",
            "body": "Already active",
            "app_code": "food",
            "track": "enhance",
        }
    )

    assert payload["accepted"] is True
    assert payload["triggered"] is False
    assert payload["reason"] == "already_active_job"
    assert payload["job_id"] == "job-existing"


def test_dashboard_issue_registration_runtime_appends_role_preset_to_issue_body_and_detects_title_track(
    app_components, tmp_path: Path
) -> None:
    gh_calls: list[tuple] = []
    runtime = _build_runtime(app_components, tmp_path, gh_calls)

    payload = runtime.register_issue(
        {
            "title": "[long] Role preset issue",
            "body": "Need preset",
            "app_code": "food",
            "track": "enhance",
            "role_preset_id": "default-dev",
        }
    )

    assert payload["accepted"] is True
    assert payload["track"] == "long"
    assert payload["role_preset_id"] == "default-dev"

    create_call = next(args for args, _context in gh_calls if args[:3] == ("gh", "issue", "create"))
    body_arg = create_call[create_call.index("--body") + 1]
    assert "## ROLE PRESET" in body_arg
    assert "preset_id: `default-dev`" in body_arg
    assert "`architect`" in body_arg
