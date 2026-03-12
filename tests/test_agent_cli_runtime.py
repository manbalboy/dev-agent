from __future__ import annotations

from app.agent_cli_runtime import canonical_cli_name, infer_cli_model


def test_canonical_cli_name_maps_legacy_aliases() -> None:
    assert canonical_cli_name("claude") == "codex"
    assert canonical_cli_name("copilot") == "codex"
    assert canonical_cli_name("gemini") == "gemini"


def test_infer_cli_model_prefers_template_and_reports_danger() -> None:
    payload = infer_cli_model(
        "codex",
        {
            "coder": "cat {prompt_file} | codex exec - --dangerously-bypass-approvals-and-sandbox -C {work_dir} --color never --model gpt-5-codex",
        },
    )

    assert payload["model"] == "gpt-5-codex"
    assert payload["source"] == "template_flag"
    assert payload["template_key"] == "coder"
    assert payload["danger_mode"] is True
    assert payload["danger_template_keys"] == ["coder"]


def test_infer_cli_model_uses_environment_when_template_missing(monkeypatch) -> None:
    monkeypatch.setenv("AGENTHUB_GEMINI_MODEL", "gemini-3.1-pro-preview")

    payload = infer_cli_model("gemini", {})

    assert payload["model"] == "gemini-3.1-pro-preview"
    assert payload["source"] == "env:AGENTHUB_GEMINI_MODEL"
    assert payload["template_key"] == ""
    assert payload["danger_mode"] is False
    assert payload["danger_template_keys"] == []
