"""Tests for workflow selection precedence."""

from __future__ import annotations

import json
from pathlib import Path

from app.workflow_resolution import resolve_workflow_selection


def _write_workflows(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [
                    {"workflow_id": "wf-default", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                    {"workflow_id": "wf-app", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                    {"workflow_id": "wf-job", "entry_node_id": "n1", "nodes": [{"id": "n1", "type": "gh_read_issue"}], "edges": []},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_apps(path: Path, workflow_id: str = "wf-app") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "code": "default",
                    "name": "Default",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                },
                {
                    "code": "web",
                    "name": "Web",
                    "repository": "owner/repo",
                    "workflow_id": workflow_id,
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_workflow_selection_prefers_job_override(tmp_path: Path):
    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflows(workflows_path)
    _write_apps(apps_path)

    selection = resolve_workflow_selection(
        requested_workflow_id="wf-job",
        app_code="web",
        repository="owner/repo",
        apps_path=apps_path,
        workflows_path=workflows_path,
    )

    assert selection.workflow_id == "wf-job"
    assert selection.source == "job"
    assert selection.warning == ""


def test_workflow_selection_uses_app_mapping_when_no_job_override(tmp_path: Path):
    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflows(workflows_path)
    _write_apps(apps_path)

    selection = resolve_workflow_selection(
        requested_workflow_id="",
        app_code="web",
        repository="owner/repo",
        apps_path=apps_path,
        workflows_path=workflows_path,
    )

    assert selection.workflow_id == "wf-app"
    assert selection.source == "app"
    assert selection.warning == ""


def test_workflow_selection_falls_back_to_default_when_app_mapping_is_stale(tmp_path: Path):
    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflows(workflows_path)
    _write_apps(apps_path, workflow_id="wf-stale")

    selection = resolve_workflow_selection(
        requested_workflow_id="",
        app_code="web",
        repository="owner/repo",
        apps_path=apps_path,
        workflows_path=workflows_path,
    )

    assert selection.workflow_id == "wf-default"
    assert selection.source == "default"
    assert "wf-stale" in selection.warning
