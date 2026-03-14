from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.dashboard_compat_runtime import DashboardCompatRuntime


def test_dashboard_compat_runtime_dispatches_log_analyzer_with_injected_provider() -> None:
    captured = {"codex": 0, "gemini": 0}

    def fake_codex(prompt: str, templates: dict[str, str]) -> str:
        del prompt, templates
        captured["codex"] += 1
        return "codex analyzed"

    def fake_gemini(prompt: str, templates: dict[str, str]) -> str:
        del prompt, templates
        captured["gemini"] += 1
        return "gemini analyzed"

    assert (
        DashboardCompatRuntime.run_log_analyzer(
            assistant="codex",
            prompt="prompt",
            templates={},
            run_codex_log_analysis=fake_codex,
            run_gemini_log_analysis=fake_gemini,
        )
        == "codex analyzed"
    )
    assert (
        DashboardCompatRuntime.run_log_analyzer(
            assistant="gemini",
            prompt="prompt",
            templates={},
            run_codex_log_analysis=fake_codex,
            run_gemini_log_analysis=fake_gemini,
        )
        == "gemini analyzed"
    )
    assert captured == {"codex": 1, "gemini": 1}


def test_dashboard_compat_runtime_rejects_unknown_chat_provider() -> None:
    with pytest.raises(HTTPException) as error:
        DashboardCompatRuntime.run_assistant_chat_provider(
            assistant="unknown",
            prompt="hello",
            templates={},
            run_codex_chat_completion=lambda prompt, templates: "codex",
            run_gemini_chat_completion=lambda prompt, templates: "gemini",
        )

    assert "지원하지 않는 assistant" in str(error.value.detail)


def test_dashboard_compat_runtime_reads_and_writes_registered_apps(tmp_path: Path) -> None:
    apps_path = tmp_path / "config" / "apps.json"
    workflows_path = tmp_path / "config" / "workflows.json"
    workflows_path.parent.mkdir(parents=True, exist_ok=True)
    workflows_path.write_text(
        json.dumps(
            {
                "default_workflow_id": "wf-default",
                "workflows": [{"workflow_id": "wf-default"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    DashboardCompatRuntime.write_registered_apps(
        apps_path,
        [
            {
                "code": "food",
                "name": "Food",
                "repository": "owner/repo",
                "workflow_id": "wf-default",
                "source_repository": "manbalboy/Food",
            }
        ],
    )

    apps = DashboardCompatRuntime.read_registered_apps(
        apps_path,
        "owner/repo",
        default_workflow_id=DashboardCompatRuntime.read_default_workflow_id(workflows_path),
    )

    assert [item["code"] for item in apps] == ["default", "food"]
    assert next(item for item in apps if item["code"] == "food")["source_repository"] == "manbalboy/Food"
