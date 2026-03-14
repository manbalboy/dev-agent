from __future__ import annotations

import json
from pathlib import Path

from app.dashboard import _TIMESTAMPED_LINE_PATTERN, _classify_command_target
from app.dashboard_assistant_diagnosis_runtime import DashboardAssistantDiagnosisRuntime
from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.workflow_resume import build_workflow_artifact_paths


def _make_job(job_id: str, *, status: str = JobStatus.FAILED.value) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title=f"Issue {job_id}",
        issue_url="https://github.com/owner/repo/issues/77",
        status=status,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/default/{job_id}",
        pr_url=None,
        error_message="heartbeat stale",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code="default",
        source_repository="owner/repo",
    )


def _build_runtime(settings, feature_flags_path: Path) -> DashboardAssistantDiagnosisRuntime:
    artifact_runtime = DashboardJobArtifactRuntime(
        settings=settings,
        timestamped_line_pattern=_TIMESTAMPED_LINE_PATTERN,
        classify_command_target=_classify_command_target,
    )
    return DashboardAssistantDiagnosisRuntime(
        settings=settings,
        feature_flags_config_path=feature_flags_path,
        artifact_runtime=artifact_runtime,
        get_memory_runtime_store=lambda current_settings: MemoryRuntimeStore(
            current_settings.resolved_memory_dir / "memory_runtime.db"
        ),
        read_feature_flags=lambda path: json.loads(path.read_text(encoding="utf-8")).get("flags", {})
        if path.exists()
        else {},
        build_workflow_artifact_paths=build_workflow_artifact_paths,
        utc_now_iso=utc_now_iso,
    )


def test_dashboard_assistant_diagnosis_runtime_builds_observability_context(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    feature_flags_path = tmp_path / "feature_flags.json"
    runtime = _build_runtime(settings, feature_flags_path)

    failed_job = _make_job("job-diagnosis-failed", status=JobStatus.FAILED.value)
    running_job = _make_job("job-diagnosis-running", status=JobStatus.RUNNING.value)
    running_job.error_message = ""
    store.create_job(failed_job)
    store.create_job(running_job)
    (settings.logs_debug_dir / failed_job.log_file).write_text(
        "[2026-03-14T01:00:00Z] [STDERR] heartbeat stale detected\n",
        encoding="utf-8",
    )

    context_text = runtime.build_agent_observability_context(store)

    assert "Job summary: total=2" in context_text
    assert "Recent failed jobs:" in context_text
    assert "log_tail(job-diagnosis-failed.log)" in context_text
    assert "heartbeat stale detected" in context_text


def test_dashboard_assistant_diagnosis_runtime_runs_tool_loop_and_writes_trace(
    app_components,
    tmp_path: Path,
) -> None:
    settings, store, _ = app_components
    feature_flags_path = tmp_path / "feature_flags.json"
    feature_flags_path.write_text(
        json.dumps({"flags": {"assistant_diagnosis_loop": True}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime = _build_runtime(settings, feature_flags_path)

    job = _make_job("job-diagnosis-loop")
    job.issue_title = "assistant diagnosis runtime"
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "assistant_notes.txt").write_text(
        "codex implement stage heartbeat stale guidance\n",
        encoding="utf-8",
    )
    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:assistant_runtime",
            "memory_type": "failure_pattern",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": "",
            "job_id": job.job_id,
            "title": "assistant runtime heartbeat stale",
            "summary": "heartbeat stale detected during implement stage",
            "score": 1.6,
            "confidence": 0.73,
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

    payload = runtime.run_assistant_diagnosis_loop(
        job=job,
        question="최근 실패 원인 분석",
        assistant_scope="chat",
    )

    assert payload["enabled"] is True
    assert payload["assistant_scope"] == "chat"
    assert len(payload["tool_runs"]) == 3
    assert Path(payload["trace_path"]).exists()
    assert "[log_lookup]" in payload["context_text"]
    assert "[repo_search]" in payload["context_text"]
    assert "[memory_search]" in payload["context_text"]
