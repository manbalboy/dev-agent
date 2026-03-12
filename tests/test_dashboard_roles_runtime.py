"""Tests for dashboard role/preset runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.dashboard_roles_runtime import DashboardRolesRuntime, read_roles_payload


def test_dashboard_roles_runtime_upserts_role_with_normalized_tags(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    roles_path.write_text("{\"roles\": [], \"presets\": []}\n", encoding="utf-8")

    payload = DashboardRolesRuntime().upsert_role(
        roles_config_path=roles_path,
        payload={
            "code": "Planner",
            "name": "Planner",
            "cli": "gemini",
            "template_key": "planner",
            "skills": ["repo-reading", "mvp-planning", "repo-reading"],
            "allowed_tools": ["research_search", "research_search"],
            "enabled": True,
        },
    )

    assert payload["saved"] is True
    role = next(item for item in payload["roles"] if item["code"] == "planner")
    assert role["skills"] == ["repo-reading", "mvp-planning"]
    assert role["allowed_tools"] == ["research_search"]

    persisted = json.loads(roles_path.read_text(encoding="utf-8"))
    role = next(item for item in persisted["roles"] if item["code"] == "planner")
    assert role["skills"] == ["repo-reading", "mvp-planning"]
    assert role["allowed_tools"] == ["research_search"]


def test_dashboard_roles_runtime_delete_role_unlinks_presets(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    roles_path.write_text(
        json.dumps(
            {
                "roles": [
                    {"code": "planner", "name": "Planner", "cli": "gemini", "template_key": "planner", "enabled": True},
                    {"code": "coder", "name": "Coder", "cli": "codex", "template_key": "coder", "enabled": True},
                ],
                "presets": [
                    {"preset_id": "core", "name": "Core", "role_codes": ["planner", "coder"]},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = DashboardRolesRuntime().delete_role(roles_config_path=roles_path, role_code="planner")

    assert payload["deleted"] is True
    assert {item["code"] for item in payload["roles"]} == {"coder"}
    assert payload["presets"][0]["role_codes"] == ["coder"]

    persisted = read_roles_payload(roles_path)
    assert {item["code"] for item in persisted["roles"]} == {"coder"}
    assert persisted["presets"][0]["role_codes"] == ["coder"]


def test_dashboard_roles_runtime_upserts_preset_with_known_roles_only(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    roles_path.write_text(
        json.dumps(
            {
                "roles": [
                    {"code": "planner", "name": "Planner", "cli": "gemini", "template_key": "planner", "enabled": True},
                    {"code": "coder", "name": "Coder", "cli": "codex", "template_key": "coder", "enabled": True},
                ],
                "presets": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = DashboardRolesRuntime().upsert_role_preset(
        roles_config_path=roles_path,
        payload={
            "preset_id": "core-team",
            "name": "Core Team",
            "description": "main preset",
            "role_codes": ["planner", "unknown", "coder", "planner"],
        },
    )

    assert payload["saved"] is True
    preset = next(item for item in payload["presets"] if item["preset_id"] == "core-team")
    assert preset["role_codes"] == ["planner", "coder"]
