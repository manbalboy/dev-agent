from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.assistant_runtime as assistant_runtime


def test_build_assistant_chat_prompt_includes_history_and_context() -> None:
    prompt = assistant_runtime.build_assistant_chat_prompt(
        assistant="codex",
        message="원인 좁혀줘",
        history=[
            {"role": "user", "content": "최근 실패 요약"},
            {"role": "assistant", "content": "stale heartbeat가 보입니다."},
        ],
        runtime_context="Job summary: total=3",
        focus_context="Focused job: job-1",
        diagnosis_context="[log_lookup]\nheartbeat stale",
    )

    assert "AgentHub 운영 AI 도우미(codex)" in prompt
    assert "[대화 이력]" in prompt
    assert "assistant: stale heartbeat가 보입니다." in prompt
    assert "[런타임 컨텍스트]" in prompt
    assert "[집중 분석 대상]" in prompt
    assert "[도구 진단 컨텍스트]" in prompt
    assert "[최신 사용자 메시지]" in prompt


def test_run_log_analyzer_dispatches_to_selected_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"codex": 0, "gemini": 0}

    def fake_codex(prompt: str, templates: dict[str, str]) -> str:
        del prompt, templates
        captured["codex"] += 1
        return "codex analyzed"

    def fake_gemini(prompt: str, templates: dict[str, str]) -> str:
        del prompt, templates
        captured["gemini"] += 1
        return "gemini analyzed"

    monkeypatch.setattr(assistant_runtime, "run_codex_log_analysis", fake_codex)
    monkeypatch.setattr(assistant_runtime, "run_gemini_log_analysis", fake_gemini)

    assert assistant_runtime.run_log_analyzer(assistant="codex", prompt="p", templates={}) == "codex analyzed"
    assert assistant_runtime.run_log_analyzer(assistant="gemini", prompt="p", templates={}) == "gemini analyzed"
    assert captured == {"codex": 1, "gemini": 1}


def test_run_assistant_chat_provider_rejects_unknown_provider() -> None:
    with pytest.raises(HTTPException) as error:
        assistant_runtime.run_assistant_chat_provider(
            assistant="unknown",
            prompt="hello",
            templates={},
        )

    assert "지원하지 않는 assistant" in str(error.value)
