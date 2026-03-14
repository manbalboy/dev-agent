from __future__ import annotations

import json
from pathlib import Path

from app.dashboard import _TIMESTAMPED_LINE_PATTERN, _classify_command_target
from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _build_runtime(settings) -> DashboardJobArtifactRuntime:
    return DashboardJobArtifactRuntime(
        settings=settings,
        timestamped_line_pattern=_TIMESTAMPED_LINE_PATTERN,
        classify_command_target=_classify_command_target,
    )


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="Artifact runtime",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.FAILED.value,
        stage=JobStage.IMPLEMENT_WITH_CODEX.value,
        attempt=2,
        max_attempts=3,
        branch_name=f"agenthub/test/{job_id}",
        pr_url=None,
        error_message="stderr failure",
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )


def test_dashboard_job_artifact_runtime_reads_agent_md_files_and_stage_snapshots(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(settings)
    workspace = settings.repository_workspace_path("owner/repo", "default")
    docs_dir = workspace / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (workspace / "STATUS.md").write_text("status", encoding="utf-8")
    (docs_dir / "PLAN.md").write_text("plan", encoding="utf-8")

    snapshot_dir = settings.data_dir / "md_snapshots" / "job-artifacts"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "attempt_02.json").write_text(
        json.dumps(
            {
                "attempt": 2,
                "stage": "implement",
                "created_at": "2026-03-14T00:00:00+00:00",
                "changed_files": ["app.py"],
                "changed_files_all": ["app.py", "README.md"],
                "md_files": ["STATUS.md"],
                "file_snapshots": [],
            }
        ),
        encoding="utf-8",
    )

    md_files = runtime.read_agent_md_files(workspace)
    snapshots = runtime.read_stage_md_snapshots("job-artifacts")

    assert [item["name"] for item in md_files] == ["STATUS.md", "_docs/PLAN.md"]
    assert snapshots[0]["attempt"] == 2
    assert snapshots[0]["stage"] == "implement"


def test_dashboard_job_artifact_runtime_resolves_legacy_debug_log_path(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(settings)
    legacy_log = settings.logs_dir / "sample.log"
    legacy_log.parent.mkdir(parents=True, exist_ok=True)
    legacy_log.write_text("legacy\n", encoding="utf-8")

    resolved = runtime.resolve_channel_log_path("sample.log", channel="debug")

    assert resolved == legacy_log.resolve()


def test_dashboard_job_artifact_runtime_parses_log_events_with_tail_cap(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(settings)
    log_path = settings.logs_dir / "debug" / "artifact.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [f"[2026-03-14T00:00:{index:02d}+00:00] line {index}" for index in range(295)]
            + [
                "[2026-03-14T00:05:00+00:00] [RUN] codex exec --json",
                "[2026-03-14T00:05:01+00:00] [STDOUT] hello",
                "[2026-03-14T00:05:02+00:00] [STDERR] failed",
                "[2026-03-14T00:05:03+00:00] [STAGE] implement_with_codex",
                "[2026-03-14T00:05:04+00:00] [DONE] exit_code=1",
                "[2026-03-14T00:05:05+00:00] trailing info",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    events = runtime.parse_log_events(log_path)

    assert len(events) == 300
    assert any(item["kind"] == "stdout" for item in events)
    assert any(item["kind"] == "stderr" for item in events)
    assert any(item["kind"] == "done" for item in events)


def test_dashboard_job_artifact_runtime_builds_focus_job_log_context(app_components) -> None:
    settings, _, _ = app_components
    runtime = _build_runtime(settings)
    job = _make_job("job-artifact-focus")
    debug_log = settings.logs_dir / "debug" / job.log_file
    user_log = settings.logs_dir / "user" / job.log_file
    debug_log.parent.mkdir(parents=True, exist_ok=True)
    user_log.parent.mkdir(parents=True, exist_ok=True)
    debug_log.write_text("debug-line-1\ndebug-line-2\n", encoding="utf-8")
    user_log.write_text("user-line-1\n", encoding="utf-8")

    context = runtime.build_focus_job_log_context(job)

    assert "Focused job:" in context
    assert "debug log tail" in context
    assert "user log tail" in context
    assert "debug-line-2" in context
    assert "user-line-1" in context
