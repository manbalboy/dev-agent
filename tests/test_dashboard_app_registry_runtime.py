from __future__ import annotations

import json
from pathlib import Path

import app.dashboard as dashboard
from app.dashboard_app_registry_runtime import DashboardAppRegistryRuntime
from app.dashboard_job_enqueue_runtime import DashboardJobEnqueueRuntime
from app.workflow_design import default_workflow_template, load_workflows


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
                        "version": 1,
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


def _build_runtime(tmp_path: Path, ensure_calls: list[tuple[str, str, str, str]]) -> DashboardAppRegistryRuntime:
    apps_path = tmp_path / "config" / "apps.json"
    workflows_path = tmp_path / "config" / "workflows.json"
    _write_apps(apps_path)
    _write_workflow_catalog(workflows_path)
    return DashboardAppRegistryRuntime(
        allowed_repository="owner/repo",
        track_choices=sorted(dashboard._TRACK_CHOICES),
        read_registered_apps=dashboard._read_registered_apps,
        write_registered_apps=dashboard._write_registered_apps,
        read_default_workflow_id=dashboard._read_default_workflow_id,
        load_workflows=load_workflows,
        default_workflow_template=default_workflow_template,
        normalize_app_code=DashboardJobEnqueueRuntime.normalize_app_code,
        normalize_repository_ref=dashboard._normalize_repository_ref,
        ensure_label=lambda repository, label_name, color, description: ensure_calls.append(
            (repository, label_name, color, description)
        ),
        apps_config_path=apps_path,
        workflows_config_path=workflows_path,
    )


def test_dashboard_app_registry_runtime_lists_apps_with_default_workflow(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, [])

    payload = runtime.list_apps()

    assert payload["default_workflow_id"] == "wf-default"
    assert [item["code"] for item in payload["apps"]] == ["default", "web"]
    assert "enhance" in payload["tracks"]


def test_dashboard_app_registry_runtime_upserts_app_and_creates_labels(tmp_path: Path) -> None:
    ensure_calls: list[tuple[str, str, str, str]] = []
    runtime = _build_runtime(tmp_path, ensure_calls)

    payload = runtime.upsert_app(
        {
            "code": "food",
            "name": "Food",
            "source_repository": "https://github.com/manbalboy/Food.git",
            "workflow_id": "wf-special",
        }
    )

    saved = next(item for item in payload["apps"] if item["code"] == "food")
    assert saved["source_repository"] == "manbalboy/Food"
    assert saved["workflow_id"] == "wf-special"
    label_names = [item[1] for item in ensure_calls]
    assert "app:food" in label_names
    assert "track:enhance" in label_names


def test_dashboard_app_registry_runtime_deletes_non_default_app(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, [])

    payload = runtime.delete_app("web")

    assert payload["deleted"] is True
    assert [item["code"] for item in payload["apps"]] == ["default"]


def test_dashboard_app_registry_runtime_maps_workflow_for_existing_app(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, [])

    payload = runtime.map_app_workflow("web", "wf-special")

    mapped = next(item for item in payload["apps"] if item["code"] == "web")
    assert payload["saved"] is True
    assert payload["workflow_id"] == "wf-special"
    assert mapped["workflow_id"] == "wf-special"


def test_dashboard_app_registry_runtime_raises_when_mapping_missing_app(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path, [])

    try:
        runtime.map_app_workflow("missing", "wf-special")
    except KeyError as exc:
        assert exc.args[0] == "앱을 찾을 수 없습니다: missing"
    else:
        raise AssertionError("expected KeyError")
