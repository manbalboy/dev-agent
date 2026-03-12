from __future__ import annotations

from pathlib import Path

import pytest

from app.command_runner import CommandExecutionError
from app.orchestrator import IssueDetails
from app.workflow_node_runtime import WorkflowNodeRuntime


class _Owner:
    def __init__(self) -> None:
        self.called: list[str] = []

    def _workflow_context_issue(self, context):
        return context["issue"]

    def _workflow_context_paths(self, context):
        return context["paths"]

    def _stage_documentation_with_claude(self, job, repository_path, paths, log_path):
        self.called.append("original")


def test_if_label_match_supports_mode_any() -> None:
    owner = _Owner()
    runtime = WorkflowNodeRuntime(owner=owner)
    context = {
        "issue": IssueDetails(
            title="title",
            body="body",
            url="https://example.com/issues/1",
            labels=("agent:run", "mobile"),
        )
    }

    result = runtime.workflow_node_if_label_match(
        job=None,
        repository_path=Path("/tmp/repo"),
        node={"match_labels": "web,mobile", "match_mode": "any"},
        context=context,
        log_path=Path("/tmp/log"),
    )

    assert result["event"] == "success"
    assert "mobile" in result["message"]


def test_loop_until_pass_retries_then_stops() -> None:
    owner = _Owner()
    runtime = WorkflowNodeRuntime(owner=owner)
    context = {
        "last_node_result": {"event": "failure"},
        "loop_counters": {},
    }

    first = runtime.workflow_node_loop_until_pass(
        job=None,
        repository_path=Path("/tmp/repo"),
        node={"id": "n-loop", "loop_max_iterations": 1},
        context=context,
        log_path=Path("/tmp/log"),
    )
    assert first["event"] == "failure"
    assert context["loop_counters"]["n-loop"] == 1

    with pytest.raises(CommandExecutionError):
        runtime.workflow_node_loop_until_pass(
            job=None,
            repository_path=Path("/tmp/repo"),
            node={"id": "n-loop", "loop_max_iterations": 1},
            context=context,
            log_path=Path("/tmp/log"),
        )


def test_runtime_uses_current_owner_stage_method() -> None:
    owner = _Owner()
    runtime = WorkflowNodeRuntime(owner=owner)
    context = {"paths": {"spec": Path("/tmp/repo/_docs/SPEC.md")}}

    def patched_stage(job, repository_path, paths, log_path):
        owner.called.append("patched")

    owner._stage_documentation_with_claude = patched_stage  # type: ignore[method-assign]

    runtime.workflow_node_documentation_task(
        job=None,
        repository_path=Path("/tmp/repo"),
        node={"id": "n-doc"},
        context=context,
        log_path=Path("/tmp/log"),
    )

    assert owner.called == ["patched"]
