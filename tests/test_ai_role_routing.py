"""Tests for configurable AI role routing."""

from __future__ import annotations

import json
from pathlib import Path

from app.ai_role_routing import AIRoleRouter, default_ai_role_routing_payload
from app.command_runner import CommandResult
from app.orchestrator import Orchestrator


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
                        "skills": ["repo-reading", "mvp-planning"],
                        "allowed_tools": ["research_search"],
                        "enabled": True,
                    },
                    {
                        "code": "reviewer",
                        "name": "리뷰어",
                        "cli": "gemini",
                        "template_key": "reviewer",
                        "skills": ["quality-review"],
                        "enabled": True,
                    },
                    {
                        "code": "coder",
                        "name": "코더",
                        "cli": "codex",
                        "template_key": "coder",
                        "skills": ["implementation", "refactor"],
                        "enabled": True,
                    },
                    {"code": "designer", "name": "디자이너", "cli": "codex", "template_key": "coder", "enabled": True},
                    {"code": "publisher", "name": "퍼블리셔", "cli": "codex", "template_key": "coder", "enabled": True},
                    {"code": "copywriter", "name": "카피라이터", "cli": "codex", "template_key": "coder", "enabled": True},
                    {"code": "tech-writer", "name": "기술 문서 작성가", "cli": "codex", "template_key": "documentation_writer", "enabled": True},
                    {"code": "escalation-helper", "name": "에스컬레이션", "cli": "codex", "template_key": "escalation", "enabled": True},
                    {"code": "orchestration-helper", "name": "오케스트레이션", "cli": "codex", "template_key": "copilot", "enabled": True},
                    {"code": "research-agent", "name": "리서치", "cli": "python3", "template_key": "research_search", "enabled": True},
                    {"code": "refactor-specialist", "name": "리팩토링", "cli": "codex", "template_key": "coder", "enabled": True},
                ],
                "presets": [
                    {"preset_id": "doc-fast", "name": "문서 빠른 처리", "role_codes": ["coder"]},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_default_ai_role_router_matches_primary_strategy(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    _write_roles(roles_path)

    router = AIRoleRouter(roles_path=roles_path, routing_path=tmp_path / "missing-routing.json")

    planner = router.resolve("planner")
    reviewer = router.resolve("reviewer")
    coder = router.resolve("coder")
    documentation = router.resolve("documentation")

    assert planner.role_code == "architect"
    assert planner.cli == "gemini"
    assert planner.template_keys == ("planner",)
    assert planner.skills == ("repo-reading", "mvp-planning")
    assert planner.allowed_tools == ("research_search",)

    assert reviewer.role_code == "reviewer"
    assert reviewer.cli == "gemini"
    assert reviewer.template_keys == ("reviewer",)
    assert reviewer.skills == ("quality-review",)

    assert coder.role_code == "coder"
    assert coder.cli == "codex"
    assert coder.template_keys == ("coder",)
    assert coder.skills == ("implementation", "refactor")

    assert documentation.role_code == "tech-writer"
    assert documentation.cli == "codex"
    assert documentation.template_keys[0] == "documentation_writer"


def test_ai_role_router_allows_route_provider_swap_without_code_change(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)

    payload = default_ai_role_routing_payload()
    payload["routes"]["planner"]["role_code"] = "refactor-specialist"
    routing_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    router = AIRoleRouter(roles_path=roles_path, routing_path=routing_path)
    planner = router.resolve("planner")

    assert planner.role_code == "refactor-specialist"
    assert planner.cli == "codex"
    assert planner.template_keys == ("planner",)


def test_ai_role_router_can_bind_route_from_role_preset(tmp_path: Path) -> None:
    roles_path = tmp_path / "roles.json"
    _write_roles(roles_path)

    router = AIRoleRouter(roles_path=roles_path, routing_path=tmp_path / "missing-routing.json")
    documentation = router.resolve("documentation", preset_id="doc-fast")

    assert documentation.role_code == "coder"
    assert documentation.cli == "codex"
    assert documentation.template_keys[0] == "documentation_writer"


class _TemplateProbeRunner:
    """Minimal template runner used to inspect chosen template keys."""

    def __init__(self, available: set[str]) -> None:
        self.available = set(available)

    def has_template(self, template_name: str) -> bool:
        return template_name in self.available

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


def test_orchestrator_prefers_provider_specific_template_variant(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)

    payload = default_ai_role_routing_payload()
    payload["routes"]["planner"]["role_code"] = "refactor-specialist"
    routing_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    router = AIRoleRouter(roles_path=roles_path, routing_path=routing_path)
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=_TemplateProbeRunner({"planner__codex", "planner", "coder__codex", "coder"}),
        ai_role_router=router,
    )

    assert orchestrator._template_for_route("planner") == "planner__codex"
    assert orchestrator._template_for_route("coder") == "coder__codex"


def test_orchestrator_builds_route_runtime_context_with_skills_and_tools(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    roles_path = tmp_path / "roles.json"
    routing_path = tmp_path / "ai_role_routing.json"
    _write_roles(roles_path)
    routing_path.write_text(json.dumps(default_ai_role_routing_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=_TemplateProbeRunner({"planner", "coder", "reviewer"}),
        ai_role_router=AIRoleRouter(roles_path=roles_path, routing_path=routing_path),
    )

    planner_context = orchestrator._build_route_runtime_context("planner")

    assert "attached_skills: repo-reading, mvp-planning" in planner_context
    assert "allowed_tools: research_search" in planner_context
    assert orchestrator._route_allows_tool("planner", "research_search") is True
    assert orchestrator._route_allows_tool("planner", "unknown_tool") is False
