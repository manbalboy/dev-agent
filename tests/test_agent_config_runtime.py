from __future__ import annotations

import json
from pathlib import Path

from app.agent_config_runtime import (
    load_agent_template_config,
    read_command_templates,
    update_agent_template_config,
)


def test_read_command_templates_returns_only_string_values(tmp_path: Path) -> None:
    config_path = tmp_path / "ai_commands.json"
    config_path.write_text(
        json.dumps({"planner": "gemini run", "coder": "codex exec", "meta": {"nested": True}}, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = read_command_templates(config_path)

    assert payload == {"planner": "gemini run", "coder": "codex exec"}


def test_load_and_update_agent_template_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "ai_commands.json"
    env_path = tmp_path / ".env"
    config_path.write_text(
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

    loaded = load_agent_template_config(config_path, env_path, enable_escalation_fallback=True)
    assert loaded["planner"] == "gemini old"
    assert loaded["enable_escalation"] is False

    saved = update_agent_template_config(
        config_path,
        env_path,
        planner="gemini new",
        coder="codex new",
        reviewer="gemini review new",
        copilot="codex helper new",
        escalation="codex escalation new",
        enable_escalation=True,
    )

    assert saved == {"saved": True, "enable_escalation": True}

    reloaded = load_agent_template_config(config_path, env_path, enable_escalation_fallback=False)
    assert reloaded["planner"] == "gemini new"
    assert reloaded["coder"] == "codex new"
    assert reloaded["reviewer"] == "gemini review new"
    assert reloaded["copilot"] == "codex helper new"
    assert reloaded["escalation"] == "codex escalation new"
    assert reloaded["enable_escalation"] is True
