"""Tests for conversational assistant API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-chat-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=42,
        issue_title="assistant chat test",
        issue_url="https://github.com/owner/repo/issues/42",
        status=JobStatus.FAILED.value,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=2,
        max_attempts=3,
        branch_name="agenthub/default/issue-42",
        pr_url=None,
        error_message="heartbeat stale",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )


def test_assistant_chat_runs_selected_provider_with_history_and_focus_job(app_components, monkeypatch):
    settings, store, app = app_components
    job = _make_job("job-chat-run")
    store.create_job(job)
    (settings.logs_debug_dir / job.log_file).write_text(
        "[2026-03-08T00:00:00Z] [ORCHESTRATOR] running heartbeat stale detected\n",
        encoding="utf-8",
    )

    captured = {"assistant": "", "prompt": ""}

    def fake_run_chat_provider(*, assistant, prompt, templates):
        captured["assistant"] = assistant
        captured["prompt"] = prompt
        return f"{assistant} replied"

    monkeypatch.setattr("app.dashboard._run_assistant_chat_provider", fake_run_chat_provider)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/chat",
        json={
            "assistant": "gemini",
            "message": "원인을 더 좁혀줘",
            "job_id": job.job_id,
            "history": [
                {"role": "user", "content": "최근 실패 요약해줘"},
                {"role": "assistant", "content": "stale heartbeat가 핵심입니다."},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "gemini"
    assert payload["assistant"] == "gemini replied"
    assert payload["diagnosis_trace"]["enabled"] is False
    assert captured["assistant"] == "gemini"
    assert "[대화 이력]" in captured["prompt"]
    assert "assistant: stale heartbeat가 핵심입니다." in captured["prompt"]
    assert "Focused job" in captured["prompt"]
    assert "running heartbeat stale detected" in captured["prompt"]
    assert "[최신 사용자 메시지]" in captured["prompt"]


def test_assistant_chat_rejects_unknown_assistant(app_components):
    _, _, app = app_components
    client = TestClient(app)
    response = client.post(
        "/api/assistant/chat",
        json={"assistant": "unknown", "message": "분석"},
    )
    assert response.status_code == 400
    assert "지원하지 않는 assistant" in response.json()["detail"]


def test_assistant_chat_runs_diagnosis_loop_when_enabled(app_components, monkeypatch, tmp_path: Path):
    settings, store, app = app_components
    job = _make_job("job-chat-diagnosis")
    job.error_message = "heartbeat stale detected"
    job.issue_title = "assistant chat diagnosis"
    store.create_job(job)

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps({"flags": {"assistant_diagnosis_loop": True}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_FEATURE_FLAGS_CONFIG_PATH", feature_flags_path)

    repo_path = settings.repository_workspace_path(job.repository, job.app_code)
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "assistant_notes.txt").write_text(
        "codex implement stage heartbeat stale guidance\n",
        encoding="utf-8",
    )

    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:assistant_chat_stale",
            "memory_type": "failure_pattern",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": "",
            "job_id": job.job_id,
            "title": "assistant heartbeat stale",
            "summary": "heartbeat stale detected during implement stage",
            "score": 1.7,
            "confidence": 0.72,
            "updated_at": "2026-03-12T00:00:00+00:00",
        }
    )

    (settings.logs_debug_dir / job.log_file).write_text(
        "\n".join(
            [
                "[2026-03-08T00:00:00Z] [RUN] codex exec implement",
                "[2026-03-08T00:00:01Z] [STDERR] heartbeat stale detected",
                "[2026-03-08T00:00:02Z] [DONE] exit_code=1 elapsed=2.40s",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    captured = {"prompt": ""}

    def fake_run_chat_provider(*, assistant, prompt, templates):
        del assistant, templates
        captured["prompt"] = prompt
        return "chat diagnosis response"

    monkeypatch.setattr("app.dashboard._run_assistant_chat_provider", fake_run_chat_provider)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/chat",
        json={
            "assistant": "codex",
            "message": "이 실패 원인 더 좁혀줘",
            "job_id": job.job_id,
            "history": [{"role": "user", "content": "이전 로그 요약해줘"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant"] == "chat diagnosis response"
    assert payload["diagnosis_trace"]["enabled"] is True
    assert len(payload["diagnosis_trace"]["tool_runs"]) == 3
    assert Path(payload["diagnosis_trace"]["trace_path"]).exists()
    assert "[도구 진단 컨텍스트]" in captured["prompt"]
    assert "[집중 분석 대상]" in captured["prompt"]
    assert "[log_lookup]" in captured["prompt"]
    assert "[repo_search]" in captured["prompt"]
    assert "[memory_search]" in captured["prompt"]


def test_legacy_codex_chat_route_forwards_to_conversational_endpoint(app_components, monkeypatch):
    _, _, app = app_components
    captured = {"assistant": "", "prompt": ""}

    def fake_run_chat_provider(*, assistant, prompt, templates):
        captured["assistant"] = assistant
        captured["prompt"] = prompt
        return "codex replied"

    monkeypatch.setattr("app.dashboard._run_assistant_chat_provider", fake_run_chat_provider)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/codex-chat",
        json={
            "message": "최근 실패 요약해줘",
            "history": [{"role": "user", "content": "첫 질문"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "codex"
    assert payload["assistant"] == "codex replied"
    assert captured["assistant"] == "codex"
