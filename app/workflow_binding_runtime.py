"""Workflow binding/context helpers extracted from orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.command_runner import CommandExecutionError


class WorkflowBindingRuntime:
    """Encapsulate workflow node binding and context guard helpers."""

    def __init__(
        self,
        *,
        ai_role_router: Any,
        issue_type: type[Any],
        route_names_map: Dict[str, tuple[str, ...]],
    ) -> None:
        self.ai_role_router = ai_role_router
        self.issue_type = issue_type
        self.route_names_map = route_names_map

    @staticmethod
    def workflow_node_agent_profile(node: Dict[str, Any], current_agent_profile: str) -> str:
        """Return the effective agent profile for one workflow node."""

        requested = str(node.get("agent_profile", "")).strip().lower()
        if requested in {"primary", "fallback"}:
            return requested
        return current_agent_profile

    @staticmethod
    def normalize_workflow_binding_id(value: str, *, max_length: int = 64) -> str:
        """Normalize one workflow role-binding identifier."""

        lowered = str(value or "").strip().lower()
        filtered = "".join(ch for ch in lowered if ch.isalnum() or ch in {"-", "_"})
        return filtered[:max_length]

    def workflow_node_route_names(self, node: Dict[str, Any]) -> tuple[str, ...]:
        """Return logical AI routes affected by one workflow node."""

        node_type = str(node.get("type", "")).strip()
        return self.route_names_map.get(node_type, ())

    def workflow_node_route_role_overrides(self, node: Dict[str, Any]) -> Dict[str, str]:
        """Resolve route->role overrides requested by one workflow node."""

        route_names = self.workflow_node_route_names(node)
        if not route_names:
            return {}

        explicit_role_code = self.normalize_workflow_binding_id(str(node.get("role_code", "")))
        preset_id = self.normalize_workflow_binding_id(str(node.get("role_preset_id", "")))
        if not explicit_role_code and not preset_id:
            return {}

        overrides: Dict[str, str] = {}
        for route_name in route_names:
            if explicit_role_code:
                resolved = self.ai_role_router.resolve(
                    route_name,
                    role_code_override=explicit_role_code,
                )
                if getattr(resolved, "role_code", "") == explicit_role_code:
                    overrides[route_name] = resolved.role_code
                continue

            resolved = self.ai_role_router.resolve(route_name, preset_id=preset_id)
            if getattr(resolved, "role_code", ""):
                overrides[route_name] = resolved.role_code
        return overrides

    def workflow_context_issue(self, context: Dict[str, Any]) -> Any:
        """Return typed workflow issue context or raise a contract error."""

        issue = context.get("issue")
        if not isinstance(issue, self.issue_type):
            raise CommandExecutionError("Workflow requires issue context before write_spec.")
        return issue

    @staticmethod
    def workflow_context_paths(context: Dict[str, Any]) -> Dict[str, Path]:
        """Return typed workflow path context or raise a contract error."""

        paths = context.get("paths")
        if not isinstance(paths, dict):
            raise CommandExecutionError("Workflow requires paths context before AI/test/git stages.")
        return paths
