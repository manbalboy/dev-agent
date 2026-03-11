"""Tests for multi-assistant log analysis API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.dashboard as dashboard
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-log-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=99,
        issue_title="log analyzer test",
        issue_url="https://github.com/owner/repo/issues/99",
        status=JobStatus.FAILED.value,
        stage=JobStage.TEST_AFTER_FIX.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/default/issue-99",
        pr_url=None,
        error_message="test failure",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
    )


def test_log_analysis_runs_selected_assistant(app_components, monkeypatch):
    settings, store, app = app_components
    job = _make_job("job-log-run")
    store.create_job(job)
    (settings.logs_debug_dir / job.log_file).write_text(
        "[2026-03-08T00:00:00Z] [TESTER] failed assertion\n",
        encoding="utf-8",
    )

    captured = {"assistant": "", "prompt": ""}

    def fake_run_log_analyzer(*, assistant, prompt, templates):
        captured["assistant"] = assistant
        captured["prompt"] = prompt
        return f"{assistant} analyzed"

    monkeypatch.setattr("app.dashboard._run_log_analyzer", fake_run_log_analyzer)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={
            "assistant": "gemini",
            "question": "문제점 알려줘",
            "job_id": job.job_id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "gemini"
    assert payload["assistant"] == "gemini analyzed"
    assert payload["diagnosis_trace"]["enabled"] is False
    assert captured["assistant"] == "gemini"
    assert "Focused job" in captured["prompt"]
    assert "failed assertion" in captured["prompt"]


def test_log_analysis_rejects_unknown_assistant(app_components):
    _, _, app = app_components
    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={"assistant": "unknown", "question": "분석"},
    )
    assert response.status_code == 400
    assert "지원하지 않는 assistant" in response.json()["detail"]


def test_log_analysis_requires_existing_job_for_focus(app_components):
    _, _, app = app_components
    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={
            "assistant": "codex",
            "question": "분석",
            "job_id": "missing-job-id",
        },
    )
    assert response.status_code == 404
    assert "job_id를 찾을 수 없습니다" in response.json()["detail"]


def test_log_analysis_routes_copilot_requests_to_codex(app_components, monkeypatch):
    _, _, app = app_components

    captured = {"codex_called": 0, "copilot_called": 0}

    def fake_codex(prompt, templates):
        captured["codex_called"] += 1
        assert "로그 분석 도우미(codex)" in prompt.lower()
        return "codex routed response"

    def fake_copilot(prompt, templates):
        captured["copilot_called"] += 1
        return "copilot direct response"

    monkeypatch.setattr("app.dashboard._run_codex_log_analysis", fake_codex)
    monkeypatch.setattr("app.dashboard._run_copilot_log_analysis", fake_copilot)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={"assistant": "copilot", "question": "최근 실패 원인 분석"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "codex"
    assert payload["requested_provider"] == "copilot"
    assert payload["assistant"] == "codex routed response"
    assert captured["codex_called"] == 1
    assert captured["copilot_called"] == 0


def test_log_analysis_routes_claude_requests_to_codex(app_components, monkeypatch):
    _, _, app = app_components

    captured = {"codex_called": 0, "claude_called": 0}

    def fake_codex(prompt, templates):
        captured["codex_called"] += 1
        return "codex routed response"

    def fake_claude(prompt, templates):
        captured["claude_called"] += 1
        return "claude direct response"

    monkeypatch.setattr("app.dashboard._run_codex_log_analysis", fake_codex)
    monkeypatch.setattr("app.dashboard._run_claude_log_analysis", fake_claude)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={"assistant": "claude", "question": "최근 실패 원인 분석"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider"] == "codex"
    assert payload["requested_provider"] == "claude"
    assert payload["assistant"] == "codex routed response"
    assert captured["codex_called"] == 1
    assert captured["claude_called"] == 0


def test_log_analysis_runs_tool_diagnosis_loop_when_enabled(app_components, monkeypatch, tmp_path: Path):
    settings, store, app = app_components
    job = _make_job("job-log-diagnosis")
    job.error_message = "heartbeat stale detected"
    job.issue_title = "heartbeat stale diagnosis"
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
    (repo_path / "heartbeat_notes.txt").write_text(
        "heartbeat stale detected during codex implement stage\n",
        encoding="utf-8",
    )

    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:heartbeat_stale",
            "memory_type": "failure_pattern",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": "",
            "job_id": job.job_id,
            "title": "heartbeat stale",
            "summary": "heartbeat stale detected during implement stage",
            "score": 1.8,
            "confidence": 0.7,
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
    (settings.logs_user_dir / job.log_file).write_text(
        "[2026-03-08T00:00:00Z] visible user log line\n",
        encoding="utf-8",
    )

    captured = {"prompt": ""}

    def fake_run_log_analyzer(*, assistant, prompt, templates):
        del assistant, templates
        captured["prompt"] = prompt
        return "diagnosis response"

    monkeypatch.setattr("app.dashboard._run_log_analyzer", fake_run_log_analyzer)

    client = TestClient(app)
    response = client.post(
        "/api/assistant/log-analysis",
        json={
            "assistant": "codex",
            "question": "최근 실패 원인 분석",
            "job_id": job.job_id,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnosis_trace"]["enabled"] is True
    assert len(payload["diagnosis_trace"]["tool_runs"]) == 3
    assert Path(payload["diagnosis_trace"]["trace_path"]).exists()
    assert "[도구 진단 컨텍스트]" in captured["prompt"]
    assert "[log_lookup]" in captured["prompt"]
    assert "[repo_search]" in captured["prompt"]
    assert "[memory_search]" in captured["prompt"]
