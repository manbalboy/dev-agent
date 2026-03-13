"""AI route and template resolution runtime for orchestrator."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.provider_failure_counter_runtime import (
    evaluate_workspace_provider_circuit_breaker,
    evaluate_workspace_provider_quarantine,
)


class AIRouteRuntime:
    """Encapsulate route resolution, template choice, and provider fallback logic."""

    def __init__(
        self,
        *,
        ai_role_router,
        command_templates,
        get_agent_profile: Callable[[], str],
        get_workflow_route_role_overrides: Callable[[], Dict[str, str]],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.ai_role_router = ai_role_router
        self.command_templates = command_templates
        self.get_agent_profile = get_agent_profile
        self.get_workflow_route_role_overrides = get_workflow_route_role_overrides
        self.append_actor_log = append_actor_log

    def resolve_ai_route(self, route_name: str):
        """Resolve one logical route with active workflow-node role overrides."""

        override_role_code = self.get_workflow_route_role_overrides().get(str(route_name or "").strip(), "")
        if override_role_code:
            return self.ai_role_router.resolve(route_name, role_code_override=override_role_code)
        return self.ai_role_router.resolve(route_name)

    def template_candidates_for_route(self, route_name: str) -> List[str]:
        """Return ordered template candidates for one logical AI route."""

        route = self.resolve_ai_route(route_name)
        candidates: List[str] = []
        for base_template in route.template_keys:
            per_provider = ""
            if route.cli:
                per_provider = f"{base_template}__{route.cli}"
            if self.get_agent_profile() == "fallback":
                if per_provider:
                    candidates.append(f"{per_provider}_fallback")
                candidates.append(f"{base_template}_fallback")
            if per_provider:
                candidates.append(per_provider)
            candidates.append(base_template)

        deduped: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def build_route_runtime_context(self, route_name: str) -> str:
        """Describe one route's runtime profile for prompt injection."""

        route = self.resolve_ai_route(route_name)
        lines = [
            f"- route: {route.route_name}",
            f"- role_code: {route.role_code}",
            f"- role_name: {route.role_name}",
            f"- cli: {route.cli or '(unspecified)'}",
        ]
        if route.description:
            lines.append(f"- route_description: {route.description}")
        if route.objective:
            lines.append(f"- objective: {route.objective}")
        if route.inputs:
            lines.append(f"- expected_inputs: {route.inputs}")
        if route.outputs:
            lines.append(f"- expected_outputs: {route.outputs}")
        if route.skills:
            lines.append(f"- attached_skills: {', '.join(route.skills)}")
        if route.allowed_tools:
            lines.append(f"- allowed_tools: {', '.join(route.allowed_tools)}")
        checklist_items = [
            item.strip()
            for item in re.split(r"[\n,]+", route.checklist)
            if item.strip()
        ]
        if checklist_items:
            lines.append("- role_checklist:")
            lines.extend(f"  - {item}" for item in checklist_items[:8])
        elif route.checklist:
            lines.append(f"- role_checklist: {route.checklist}")
        return "\n".join(lines)

    def route_allows_tool(self, route_name: str, tool_name: str) -> bool:
        """Return True when one route may request one tool."""

        normalized_tool = str(tool_name or "").strip().lower()
        if not normalized_tool:
            return False
        route = self.resolve_ai_route(route_name)
        if not route.allowed_tools:
            return normalized_tool == "research_search" if route_name == "planner" else False
        return normalized_tool in route.allowed_tools

    def template_for_route(self, route_name: str) -> str:
        """Resolve one logical AI route to the best available template key."""

        candidates = self.template_candidates_for_route(route_name)
        for candidate in candidates:
            if self.command_templates.has_template(candidate):
                return candidate
        return candidates[0]

    def template_for_route_in_repository(
        self,
        route_name: str,
        repository_path: Path,
        log_path: Path | None = None,
    ) -> str:
        """Resolve one route to a repository-aware template when outages are active."""

        default_template = self.template_for_route(route_name)
        normalized_route = str(route_name or "").strip().lower()
        if normalized_route not in {"planner", "reviewer"}:
            return default_template

        route = self.resolve_ai_route(route_name)
        provider_hint = str(route.cli or "").strip().lower()
        if provider_hint != "gemini":
            return default_template

        circuit_breaker = evaluate_workspace_provider_circuit_breaker(
            repository_path,
            provider_hint=provider_hint,
        )
        quarantine = evaluate_workspace_provider_quarantine(
            repository_path,
            provider_hint=provider_hint,
        )
        if not circuit_breaker.get("active") and not quarantine.get("active"):
            return default_template

        fallback_candidates: List[str] = []
        for base_template in route.template_keys:
            fallback_candidates.append(f"{base_template}_fallback")
            if route.cli:
                fallback_candidates.append(f"{base_template}__{route.cli}_fallback")

        deduped_candidates: List[str] = []
        for candidate in fallback_candidates:
            if candidate and candidate not in deduped_candidates:
                deduped_candidates.append(candidate)

        for candidate in deduped_candidates:
            if self.command_templates.has_template(candidate):
                if log_path is not None:
                    self.append_actor_log(
                        log_path,
                        "ORCHESTRATOR",
                        (
                            f"{provider_hint} provider "
                            f"{'circuit open' if circuit_breaker.get('active') else 'quarantined'} "
                            f"for route '{normalized_route}'. "
                            f"Using alternate template '{candidate}'."
                        ),
                    )
                return candidate
        return default_template

    def find_configured_template_for_route(self, route_name: str) -> Optional[str]:
        """Return the first configured template for one route, if any."""

        for candidate in self.template_candidates_for_route(route_name):
            if self.command_templates.has_template(candidate):
                return candidate
        return None
