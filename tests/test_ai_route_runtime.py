from __future__ import annotations

import json
from pathlib import Path

from app.ai_role_routing import AIRoleRouter, default_ai_role_routing_payload
from app.ai_route_runtime import AIRouteRuntime
from app.provider_failure_counter_runtime import record_provider_failure


class _TemplateRunner:
    def __init__(self, available: set[str]) -> None:
        self.available = set(available)

    def has_template(self, template_name: str) -> bool:
        return template_name in self.available


def _write_roles(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "roles": [
                    {
                        "code": "architect",
                        "name": "플래너",
                        "cli": "gemini",
                        "template_key": "planner",
                        "skills": ["repo-reading"],
                        "allowed_tools": ["research_search"],
                        "enabled": True,
                    },
                    {
                        "code": "reviewer",
                        "name": "리뷰어",
                        "cli": "gemini",
                        "template_key": "reviewer",
                        "enabled": True,
                    },
                    {
                        "code": "coder",
                        "name": "코더",
                        "cli": "codex",
                        "template_key": "coder",
                        "enabled": True,
                    },
                ],
                "presets": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_ai_route_runtime_uses_fallback_candidates_for_profile(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)
    routing_path.write_text(json.dumps(default_ai_role_routing_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    profile = {"value": "fallback"}

    runtime = AIRouteRuntime(
        ai_role_router=AIRoleRouter(roles_path=roles_path, routing_path=routing_path),
        command_templates=_TemplateRunner({"planner_fallback", "planner"}),
        get_agent_profile=lambda: profile["value"],
        get_workflow_route_role_overrides=lambda: {},
        append_actor_log=lambda *_args: None,
    )

    assert runtime.template_candidates_for_route("planner")[0] == "planner__gemini_fallback"
    assert runtime.template_for_route("planner") == "planner_fallback"


def test_ai_route_runtime_uses_role_override(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)
    payload = default_ai_role_routing_payload()
    payload["routes"]["documentation"]["role_code"] = "coder"
    routing_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runtime = AIRouteRuntime(
        ai_role_router=AIRoleRouter(roles_path=roles_path, routing_path=routing_path),
        command_templates=_TemplateRunner({"documentation_writer", "documentation_writer__codex"}),
        get_agent_profile=lambda: "primary",
        get_workflow_route_role_overrides=lambda: {"documentation": "coder"},
        append_actor_log=lambda *_args: None,
    )

    route = runtime.resolve_ai_route("documentation")
    assert route.role_code == "coder"
    assert runtime.template_for_route("documentation") == "documentation_writer__codex"


def test_ai_route_runtime_uses_repository_fallback_when_provider_quarantined(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)
    routing_path.write_text(json.dumps(default_ai_role_routing_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    repository_path = tmp_path / "repo"
    logs: list[tuple[str, str]] = []

    for attempt in range(1, 5):
        record_provider_failure(
            repository_path,
            provider_hint="gemini",
            failure_class="provider_timeout",
            stage_family="planning",
            reason_code="provider_timeout",
            reason="request timeout",
            job_id="job-ai-route-runtime",
            attempt=attempt,
        )

    runtime = AIRouteRuntime(
        ai_role_router=AIRoleRouter(roles_path=roles_path, routing_path=routing_path),
        command_templates=_TemplateRunner({"planner", "planner_fallback"}),
        get_agent_profile=lambda: "primary",
        get_workflow_route_role_overrides=lambda: {},
        append_actor_log=lambda _path, actor, message: logs.append((actor, message)),
    )

    resolved = runtime.template_for_route_in_repository("planner", repository_path, tmp_path / "job.log")

    assert resolved == "planner_fallback"
    assert any("Using alternate template 'planner_fallback'" in message for _, message in logs)
