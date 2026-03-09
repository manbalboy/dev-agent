"""Tests for workflow settings API and manual issue workflow override."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard


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


def _write_apps(path: Path) -> None:
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
                    "code": "web",
                    "name": "Web",
                    "repository": "owner/repo",
                    "workflow_id": "wf-default",
                    "source_repository": "",
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_set_default_workflow_api_updates_catalog(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    _write_workflow_catalog(workflows_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)

    response = client.post("/api/workflows/default", json={"workflow_id": "wf-special"})

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"saved": True, "default_workflow_id": "wf-special"}

    saved = json.loads(workflows_path.read_text(encoding="utf-8"))
    assert saved["default_workflow_id"] == "wf-special"


def test_workflow_schema_api_returns_supported_node_types(app_components):
    _, _, app = app_components
    client = TestClient(app)

    response = client.get("/api/workflows/schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "phase-1"
    assert "node_types" in payload
    assert "gh_read_issue" in payload["node_types"]
    assert "product_review" in payload["node_types"]
    assert payload["supported_edge_events"] == ["success", "failure", "always"]
    assert payload["node_agent_profiles"] == ["auto", "primary", "fallback"]
    assert payload["node_planning_modes"] == ["auto", "general", "big_picture", "dev_planning"]
    assert payload["node_match_modes"] == ["any", "all", "none"]


def test_save_workflow_api_persists_new_definition(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    _write_workflow_catalog(workflows_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)

    response = client.post(
        "/api/workflows",
        json={
            "workflow": {
                "workflow_id": "wf-product-loop",
                "name": "Product Loop",
                "description": "제품형 개발 루프",
                "version": 3,
                "entry_node_id": "n1",
                "nodes": [
                    {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기", "notes": "read first"},
                    {"id": "n2", "type": "write_spec", "title": "SPEC 작성", "agent_profile": "fallback", "notes": "spec note"},
                    {"id": "n3", "type": "gemini_plan", "title": "플랜", "planning_mode": "dev_planning"},
                ],
                "edges": [
                    {"from": "n1", "to": "n2", "on": "success"},
                    {"from": "n2", "to": "n3", "on": "success"},
                ],
            },
            "set_default": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["workflow_id"] == "wf-product-loop"
    assert payload["default_workflow_id"] == "wf-product-loop"

    saved = json.loads(workflows_path.read_text(encoding="utf-8"))
    assert saved["default_workflow_id"] == "wf-product-loop"
    stored = next(item for item in saved["workflows"] if item["workflow_id"] == "wf-product-loop")
    assert stored["name"] == "Product Loop"
    assert stored["entry_node_id"] == "n1"
    assert stored["nodes"][1]["agent_profile"] == "fallback"
    assert stored["nodes"][1]["notes"] == "spec note"
    assert stored["nodes"][2]["planning_mode"] == "dev_planning"


def test_validate_workflow_api_allows_cycle_when_loop_until_pass_exists(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    _write_workflow_catalog(workflows_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)

    response = client.post(
        "/api/workflows/validate",
        json={
            "workflow": {
                "workflow_id": "wf-loop-control",
                "name": "Loop Control",
                "version": 1,
                "entry_node_id": "n1",
                "nodes": [
                    {"id": "n1", "type": "gh_read_issue"},
                    {"id": "n2", "type": "write_spec"},
                    {"id": "n3", "type": "ux_e2e_review"},
                    {"id": "n4", "type": "loop_until_pass", "loop_max_iterations": 2},
                ],
                "edges": [
                    {"from": "n1", "to": "n2", "on": "success"},
                    {"from": "n2", "to": "n3", "on": "success"},
                    {"from": "n3", "to": "n4", "on": "success"},
                    {"from": "n3", "to": "n4", "on": "failure"},
                    {"from": "n4", "to": "n3", "on": "failure"},
                ],
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["errors"] == []


def test_issue_register_stores_requested_workflow_override(app_components, monkeypatch, tmp_path: Path):
    _, store, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflow_catalog(workflows_path)
    _write_apps(apps_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)
    monkeypatch.setattr(dashboard, "_APPS_CONFIG_PATH", apps_path)

    def fake_run_gh_command(args, error_context):
        if "create" in args:
            return "https://github.com/owner/repo/issues/501"
        return ""

    monkeypatch.setattr(dashboard, "_run_gh_command", fake_run_gh_command)
    monkeypatch.setattr(dashboard, "_ensure_agent_run_label", lambda repository: None)
    monkeypatch.setattr(dashboard, "_ensure_label", lambda repository, label_name, color, description: None)

    response = client.post(
        "/api/issues/register",
        json={
            "title": "Workflow override issue",
            "body": "Run with special workflow",
            "app_code": "web",
            "track": "enhance",
            "workflow_id": "wf-special",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "wf-special"
    assert payload["workflow_source"] == "job"

    stored = store.get_job(payload["job_id"])
    assert stored is not None
    assert stored.workflow_id == "wf-special"


def test_upsert_app_persists_normalized_source_repository(app_components, monkeypatch, tmp_path: Path):
    _, _, app = app_components
    client = TestClient(app)

    apps_path = tmp_path / "config" / "apps.json"
    workflows_path = tmp_path / "config" / "workflows.json"
    _write_workflow_catalog(workflows_path)
    _write_apps(apps_path)
    monkeypatch.setattr(dashboard, "_APPS_CONFIG_PATH", apps_path)
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)
    monkeypatch.setattr(dashboard, "_ensure_label", lambda repository, label_name, color, description: None)

    response = client.post(
        "/api/apps",
        json={
            "code": "food",
            "name": "Food",
            "source_repository": "https://github.com/manbalboy/Food.git",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    saved = next(item for item in payload["apps"] if item["code"] == "food")
    assert saved["source_repository"] == "manbalboy/Food"


def test_issue_register_stores_app_source_repository_on_job(app_components, monkeypatch, tmp_path: Path):
    _, store, app = app_components
    client = TestClient(app)

    workflows_path = tmp_path / "config" / "workflows.json"
    apps_path = tmp_path / "config" / "apps.json"
    _write_workflow_catalog(workflows_path)
    apps_path.parent.mkdir(parents=True, exist_ok=True)
    apps_path.write_text(
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
                    "source_repository": "manbalboy/Food",
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_WORKFLOWS_CONFIG_PATH", workflows_path)
    monkeypatch.setattr(dashboard, "_APPS_CONFIG_PATH", apps_path)

    def fake_run_gh_command(args, error_context):
        if "create" in args:
            return "https://github.com/owner/repo/issues/777"
        return ""

    monkeypatch.setattr(dashboard, "_run_gh_command", fake_run_gh_command)
    monkeypatch.setattr(dashboard, "_ensure_agent_run_label", lambda repository: None)
    monkeypatch.setattr(dashboard, "_ensure_label", lambda repository, label_name, color, description: None)

    response = client.post(
        "/api/issues/register",
        json={
            "title": "Use external source repository",
            "body": "Build from Food repo",
            "app_code": "food",
            "track": "enhance",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    stored = store.get_job(payload["job_id"])
    assert stored is not None
    assert stored.source_repository == "manbalboy/Food"
