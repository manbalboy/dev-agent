from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.agent_cli_runtime import ASSISTANT_PROVIDER_ALIASES, canonical_cli_name
from app.dashboard_assistant_runtime import DashboardAssistantRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-assistant-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=101,
        issue_title="assistant runtime test",
        issue_url="https://github.com/owner/repo/issues/101",
        status=JobStatus.FAILED.value,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/default/issue-101",
        pr_url=None,
        error_message="heartbeat stale",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )


def _build_runtime(
    *,
    store,
    settings,
    diagnosis_trace=None,
    read_templates=None,
    on_chat=None,
    on_log_analysis=None,
) -> DashboardAssistantRuntime:
    return DashboardAssistantRuntime(
        store=store,
        settings=settings,
        primary_assistant_providers=["codex", "gemini"],
        assistant_provider_aliases=ASSISTANT_PROVIDER_ALIASES,
        canonical_cli_name=canonical_cli_name,
        build_focus_job_log_context=lambda job, runtime_settings: f"Focused job: {job.job_id} @ {runtime_settings.allowed_repository}",
        build_agent_observability_context=lambda runtime_store, runtime_settings: (
            f"runtime::{runtime_settings.allowed_repository}::{len(runtime_store.list_jobs())}"
        ),
        run_assistant_diagnosis_loop=(
            diagnosis_trace
            or (lambda **_kwargs: {"enabled": False, "trace_path": "", "tool_runs": [], "context_text": ""})
        ),
        build_assistant_chat_prompt=lambda **kwargs: (
            f"chat::{kwargs['assistant']}::{kwargs['focus_context']}::{kwargs['diagnosis_context']}::{len(kwargs['history'])}"
        ),
        build_log_analysis_prompt=lambda **kwargs: (
            f"log::{kwargs['assistant']}::{kwargs['focus_context']}::{kwargs['diagnosis_context']}"
        ),
        read_command_templates=read_templates or (lambda _path: {"planner": "gemini"}),
        run_assistant_chat_provider=on_chat
        or (lambda *, assistant, prompt, templates: f"{assistant}::{prompt}::{sorted(templates)}"),
        run_log_analyzer=on_log_analysis
        or (lambda *, assistant, prompt, templates: f"{assistant}::{prompt}::{sorted(templates)}"),
    )


def test_dashboard_assistant_runtime_chat_routes_alias_to_canonical_provider(app_components) -> None:
    settings, store, _ = app_components
    job = _make_job("job-assistant-chat")
    store.create_job(job)
    captured: dict[str, object] = {}
    runtime = _build_runtime(
        store=store,
        settings=settings,
        on_chat=lambda *, assistant, prompt, templates: captured.update(
            {"assistant": assistant, "prompt": prompt, "templates": templates}
        )
        or "assistant reply",
    )

    payload = runtime.chat(
        {
            "assistant": "copilot",
            "message": "원인을 더 좁혀줘",
            "job_id": job.job_id,
            "history": [{"role": "user", "content": "이전 요약"}],
        }
    )

    assert payload["ok"] is True
    assert payload["provider"] == "codex"
    assert payload["requested_provider"] == "copilot"
    assert payload["focus_job_id"] == job.job_id
    assert captured["assistant"] == "codex"
    assert "Focused job: job-assistant-chat" in str(captured["prompt"])
    assert "::1" in str(captured["prompt"])
    assert captured["templates"] == {"planner": "gemini"}


def test_dashboard_assistant_runtime_chat_falls_back_to_empty_templates_on_read_error(app_components) -> None:
    settings, store, _ = app_components
    job = _make_job("job-assistant-diagnosis")
    store.create_job(job)
    captured: dict[str, object] = {}
    runtime = _build_runtime(
        store=store,
        settings=settings,
        diagnosis_trace=lambda **_kwargs: {
            "enabled": True,
            "trace_path": str(Path("/tmp/assistant-trace.json")),
            "tool_runs": [{"tool": "log_lookup", "ok": True}],
            "context_text": "diag context",
        },
        read_templates=lambda _path: (_ for _ in ()).throw(HTTPException(status_code=500, detail="missing")),
        on_chat=lambda *, assistant, prompt, templates: captured.update(
            {"assistant": assistant, "prompt": prompt, "templates": templates}
        )
        or "chat diagnosis",
    )

    payload = runtime.chat(
        {
            "assistant": "gemini",
            "message": "진단해줘",
            "job_id": job.job_id,
            "history": [],
        }
    )

    assert payload["assistant"] == "chat diagnosis"
    assert payload["diagnosis_trace"]["enabled"] is True
    assert payload["diagnosis_trace"]["tool_runs"] == [{"tool": "log_lookup", "ok": True}]
    assert "diag context" in str(captured["prompt"])
    assert captured["templates"] == {}


def test_dashboard_assistant_runtime_log_analysis_rejects_unknown_assistant(app_components) -> None:
    settings, store, _ = app_components
    runtime = _build_runtime(store=store, settings=settings)

    with pytest.raises(HTTPException) as exc_info:
        runtime.log_analysis({"assistant": "unknown", "question": "분석"})

    assert exc_info.value.status_code == 400
    assert "지원하지 않는 assistant" in str(exc_info.value.detail)


def test_dashboard_assistant_runtime_log_analysis_requires_existing_focus_job(app_components) -> None:
    settings, store, _ = app_components
    runtime = _build_runtime(store=store, settings=settings)

    with pytest.raises(HTTPException) as exc_info:
        runtime.log_analysis(
            {
                "assistant": "codex",
                "question": "문제점 알려줘",
                "job_id": "missing-job",
            }
        )

    assert exc_info.value.status_code == 404
    assert "job_id를 찾을 수 없습니다" in str(exc_info.value.detail)
