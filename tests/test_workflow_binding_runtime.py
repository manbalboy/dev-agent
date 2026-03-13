from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.command_runner import CommandExecutionError
from app.workflow_binding_runtime import WorkflowBindingRuntime


@dataclass
class _Issue:
    title: str


class _FakeRouter:
    def resolve(self, route_name: str, *, role_code_override: str | None = None, preset_id: str | None = None):
        if role_code_override:
            if route_name == "documentation" and role_code_override == "coder":
                return SimpleNamespace(role_code="coder")
            return SimpleNamespace(role_code="")
        if preset_id == "doc-fast" and route_name == "documentation":
            return SimpleNamespace(role_code="tech-writer")
        return SimpleNamespace(role_code="")


def _make_runtime() -> WorkflowBindingRuntime:
    return WorkflowBindingRuntime(
        ai_role_router=_FakeRouter(),
        issue_type=_Issue,
        route_names_map={"documentation_task": ("documentation",)},
    )


def test_workflow_node_agent_profile_uses_node_override() -> None:
    runtime = _make_runtime()

    assert runtime.workflow_node_agent_profile({"agent_profile": "fallback"}, "primary") == "fallback"
    assert runtime.workflow_node_agent_profile({"agent_profile": "unknown"}, "primary") == "primary"


def test_normalize_workflow_binding_id_filters_invalid_chars() -> None:
    assert (
        WorkflowBindingRuntime.normalize_workflow_binding_id("  Doc Fast!@#  ")
        == "docfast"
    )


def test_workflow_node_route_role_overrides_supports_explicit_role_code() -> None:
    runtime = _make_runtime()

    overrides = runtime.workflow_node_route_role_overrides(
        {"type": "documentation_task", "role_code": "coder"},
    )

    assert overrides == {"documentation": "coder"}


def test_workflow_node_route_role_overrides_supports_preset_resolution() -> None:
    runtime = _make_runtime()

    overrides = runtime.workflow_node_route_role_overrides(
        {"type": "documentation_task", "role_preset_id": "doc-fast"},
    )

    assert overrides == {"documentation": "tech-writer"}


def test_workflow_context_issue_requires_typed_issue() -> None:
    runtime = _make_runtime()

    assert runtime.workflow_context_issue({"issue": _Issue(title="x")}).title == "x"
    with pytest.raises(CommandExecutionError):
        runtime.workflow_context_issue({"issue": "bad"})


def test_workflow_context_paths_requires_paths_dict() -> None:
    runtime = _make_runtime()
    paths = {"spec": Path("/tmp/spec.md")}

    assert runtime.workflow_context_paths({"paths": paths}) == paths
    with pytest.raises(CommandExecutionError):
        runtime.workflow_context_paths({"paths": None})
