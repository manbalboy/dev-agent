from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.agent_config_runtime import load_agent_template_config, update_agent_template_config
from app.dashboard_settings_runtime import DashboardSettingsRuntime
from app.feature_flags import feature_flags_payload, write_feature_flags
from app.workflow_design import default_workflow_template, load_workflows, save_workflows, schema_payload, validate_workflow


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


def _build_runtime(tmp_path: Path) -> tuple[DashboardSettingsRuntime, Path, Path, Path, Path]:
    workflows_path = tmp_path / "config" / "workflows.json"
    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    command_config_path = tmp_path / "ai_commands.json"
    env_path = tmp_path / ".env"
    command_config_path.write_text(
        json.dumps(
            {
                "planner": "gemini old",
                "coder": "codex old",
                "reviewer": "gemini review",
                "copilot": "codex helper",
                "escalation": "codex escalation",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    env_path.write_text("AGENTHUB_ENABLE_ESCALATION=false\n", encoding="utf-8")
    runtime = DashboardSettingsRuntime(
        workflows_config_path=workflows_path,
        feature_flags_config_path=feature_flags_path,
        command_config_path=command_config_path,
        env_path=env_path,
        enable_escalation_fallback=False,
        schema_payload=schema_payload,
        load_workflows=load_workflows,
        save_workflows=save_workflows,
        validate_workflow=validate_workflow,
        default_workflow_template=default_workflow_template,
        feature_flags_payload=feature_flags_payload,
        write_feature_flags=write_feature_flags,
        load_agent_template_config=load_agent_template_config,
        update_agent_template_config=update_agent_template_config,
        collect_agent_cli_status=lambda path: {"config_path": str(path)},
        collect_agent_model_status=lambda path: {"config_path": str(path)},
    )
    return runtime, workflows_path, feature_flags_path, command_config_path, env_path


def test_workflow_schema_returns_phase_one_metadata(tmp_path: Path) -> None:
    runtime, _, _, _, _ = _build_runtime(tmp_path)

    payload = runtime.workflow_schema()

    assert payload["phase"] == "phase-1"
    assert "gh_read_issue" in payload["node_types"]
    assert payload["supported_edge_events"] == ["success", "failure", "always"]


def test_save_workflow_persists_definition_and_default(tmp_path: Path) -> None:
    runtime, workflows_path, _, _, _ = _build_runtime(tmp_path)
    _write_workflow_catalog(workflows_path)

    payload = runtime.save_workflow(
        {
            "workflow_id": "wf-product-loop",
            "name": "Product Loop",
            "version": 3,
            "entry_node_id": "n1",
            "nodes": [
                {"id": "n1", "type": "gh_read_issue"},
                {"id": "n2", "type": "write_spec", "role_code": "coder"},
            ],
            "edges": [{"from": "n1", "to": "n2", "on": "success"}],
        },
        set_default=True,
    )

    assert payload == {
        "saved": True,
        "workflow_id": "wf-product-loop",
        "default_workflow_id": "wf-product-loop",
    }
    saved = json.loads(workflows_path.read_text(encoding="utf-8"))
    assert saved["default_workflow_id"] == "wf-product-loop"
    stored = next(item for item in saved["workflows"] if item["workflow_id"] == "wf-product-loop")
    assert stored["nodes"][1]["role_code"] == "coder"


def test_save_workflow_rejects_invalid_definition(tmp_path: Path) -> None:
    runtime, _, _, _, _ = _build_runtime(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        runtime.save_workflow({"workflow_id": "wf-bad", "nodes": [], "edges": []}, set_default=False)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["message"] == "workflow validation failed"
    assert "nodes must be a non-empty list" in exc_info.value.detail["errors"]


def test_set_default_workflow_updates_catalog(tmp_path: Path) -> None:
    runtime, workflows_path, _, _, _ = _build_runtime(tmp_path)
    _write_workflow_catalog(workflows_path)

    payload = runtime.set_default_workflow("wf-special")

    assert payload == {"saved": True, "default_workflow_id": "wf-special"}
    saved = json.loads(workflows_path.read_text(encoding="utf-8"))
    assert saved["default_workflow_id"] == "wf-special"


def test_feature_flags_round_trip_persists_values(tmp_path: Path) -> None:
    runtime, _, feature_flags_path, _, _ = _build_runtime(tmp_path)

    initial = runtime.get_feature_flags()
    assert initial["flags"]["memory_logging"] is True

    payload = runtime.save_feature_flags(
        {
            "memory_logging": True,
            "memory_retrieval": False,
            "convention_extraction": True,
            "memory_scoring": False,
            "strategy_shadow": False,
            "vector_memory_shadow": True,
            "vector_memory_retrieval": True,
            "langgraph_planner_shadow": True,
            "langgraph_recovery_shadow": True,
        }
    )

    assert payload["saved"] is True
    assert payload["flags"]["memory_retrieval"] is False
    assert payload["flags"]["vector_memory_shadow"] is True
    saved = json.loads(feature_flags_path.read_text(encoding="utf-8"))
    assert saved["flags"]["strategy_shadow"] is False


def test_agent_config_round_trip_updates_templates_and_env(tmp_path: Path) -> None:
    runtime, _, _, command_config_path, env_path = _build_runtime(tmp_path)

    initial = runtime.get_agent_config()
    assert initial["planner"] == "gemini old"
    assert initial["enable_escalation"] is False

    payload = runtime.update_agent_config(
        {
            "planner": "gemini new",
            "coder": "codex new",
            "reviewer": "gemini review new",
            "copilot": "codex helper new",
            "escalation": "codex escalation new",
            "enable_escalation": True,
        }
    )

    assert payload == {"saved": True, "enable_escalation": True}
    saved_templates = json.loads(command_config_path.read_text(encoding="utf-8"))
    assert saved_templates["planner"] == "gemini new"
    assert saved_templates["coder"] == "codex new"
    assert "AGENTHUB_ENABLE_ESCALATION=true" in env_path.read_text(encoding="utf-8")

    updated = runtime.get_agent_config()
    assert updated["reviewer"] == "gemini review new"
    assert updated["copilot"] == "codex helper new"
    assert updated["escalation"] == "codex escalation new"
    assert updated["enable_escalation"] is True
