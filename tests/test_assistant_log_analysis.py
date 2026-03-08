"""Tests for multi-assistant log analysis API."""

from __future__ import annotations

from fastapi.testclient import TestClient

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
