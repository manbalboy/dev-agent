from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

from app.config import AppSettings
from app.durable_runtime_hygiene import DurableRuntimeHygieneRuntime
from app.models import JobRecord, PatchRunRecord
from app.store import JsonJobStore, SQLiteJobStore


def _make_settings(tmp_path: Path, *, store_backend: str) -> AppSettings:
    command_config = tmp_path / "ai_commands.json"
    command_config.write_text("{}\n", encoding="utf-8")
    settings = AppSettings(
        webhook_secret="test-secret",
        allowed_repository="owner/repo",
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspaces",
        max_retries=3,
        test_command="echo test",
        test_command_secondary="echo test",
        test_command_implement="echo test implement",
        test_command_fix="echo test fix",
        test_command_secondary_implement="echo test implement secondary",
        test_command_secondary_fix="echo test fix secondary",
        tester_primary_name="gpt",
        tester_secondary_name="gemini",
        command_config=command_config,
        worker_poll_seconds=1,
        worker_stale_running_seconds=600,
        worker_max_auto_recoveries=2,
        default_branch="main",
        enable_escalation=False,
        enable_stage_md_commits=True,
        api_port=8321,
        store_backend=store_backend,
        sqlite_file=tmp_path / "data" / "agenthub.db",
        durable_retention_days=7,
        docker_preview_enabled=False,
    )
    settings.ensure_directories()
    return settings


def _make_job(job_id: str, *, status: str, stage: str = "queued") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_title=job_id,
        issue_url="https://github.com/owner/repo/issues/1",
        status=status,
        stage=stage,
        attempt=1,
        max_attempts=3,
        branch_name=f"agenthub/{job_id}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at="2026-03-10T00:00:00+00:00",
        updated_at="2026-03-10T00:00:00+00:00",
        started_at=None,
        finished_at=None,
        app_code="alpha",
        track="enhance",
    )


def _make_patch_run(
    patch_run_id: str,
    *,
    status: str,
    backup_id: str,
    backup_dir: Path,
) -> PatchRunRecord:
    return PatchRunRecord(
        patch_run_id=patch_run_id,
        status=status,
        repo_root=str(backup_dir.parent.parent),
        branch="main",
        upstream_ref="origin/main",
        source_commit="1111",
        target_commit="2222",
        current_step_key="verify_health",
        current_step_label="상태 확인",
        current_step_index=6,
        total_steps=6,
        progress_percent=100,
        message=status,
        requested_by="operator",
        requested_at="2026-03-10T00:00:00+00:00",
        updated_at="2026-03-10T00:00:00+00:00",
        details={
            "backup_manifest": {
                "backup_id": backup_id,
                "backup_dir": str(backup_dir),
                "manifest_path": str(backup_dir / "manifest.json"),
            }
        },
    )


def _write_manifest(backup_dir: Path, backup_id: str) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "manifest.json").write_text(
        json.dumps({"backup_id": backup_id, "backup_dir": str(backup_dir)}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _set_age_days(path: Path, *, days: int) -> None:
    timestamp = 1_700_000_000 - (days * 86400)
    os.utime(path, (timestamp, timestamp))


def test_durable_runtime_hygiene_runtime_audits_and_cleans_json_runtime(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, store_backend="json")
    store = JsonJobStore(settings.jobs_file, settings.queue_file)
    store.create_job(_make_job("job-queued", status="queued"))
    store.create_job(_make_job("job-running", status="running", stage="implement_with_codex"))
    store.create_job(_make_job("job-done", status="done", stage="done"))
    settings.queue_file.write_text(
        json.dumps(["job-queued", "job-queued", "job-running", "job-done", "job-missing"]) + "\n",
        encoding="utf-8",
    )

    tracked_workspace = settings.repository_workspace_path("owner/repo", "alpha")
    tracked_workspace.mkdir(parents=True, exist_ok=True)
    invalid_workspace = tracked_workspace.parent / "owner__repo__invalid_20260301"
    invalid_workspace.mkdir(parents=True, exist_ok=True)
    unmanaged_workspace = tracked_workspace.parent / "orphan__repo"
    unmanaged_workspace.mkdir(parents=True, exist_ok=True)
    _set_age_days(invalid_workspace, days=9)
    _set_age_days(unmanaged_workspace, days=9)

    success_backup = settings.patch_backups_dir / "backup-success"
    failed_backup = settings.patch_backups_dir / "backup-failed"
    orphan_backup = settings.patch_backups_dir / "backup-orphan"
    _write_manifest(success_backup, "backup-success")
    _write_manifest(failed_backup, "backup-failed")
    _write_manifest(orphan_backup, "backup-orphan")
    _set_age_days(success_backup, days=9)
    _set_age_days(failed_backup, days=9)
    _set_age_days(orphan_backup, days=9)

    store.upsert_patch_run(
        _make_patch_run(
            "patch-success",
            status="done",
            backup_id="backup-success",
            backup_dir=success_backup,
        )
    )
    store.upsert_patch_run(
        _make_patch_run(
            "patch-failed",
            status="failed",
            backup_id="backup-failed",
            backup_dir=failed_backup,
        )
    )

    settings.patch_lock_file.write_text(
        json.dumps(
            {
                "active": True,
                "patch_run_id": "patch-stale",
                "status": "draining",
                "message": "stale lock",
                "updated_at": "2026-03-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime = DurableRuntimeHygieneRuntime(
        store=store,
        settings=settings,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        report_file=settings.durable_runtime_hygiene_report_file,
    )

    status = runtime.build_hygiene_status()

    assert status["summary"]["cleanup_candidate_count"] == 8
    assert status["summary"]["queue_cleanup_candidate_count"] == 4
    assert status["summary"]["workspace_warning_count"] == 2
    assert status["patch_lock"]["stale_active_lock"] is True
    assert len(status["patch_backups"]["cleanup_candidates"]) == 2
    assert len(status["workspaces"]["invalid_workspace_cleanup_candidates"]) == 1

    cleanup = runtime.cleanup()

    assert cleanup["cleanup_applied"] is True
    assert len(cleanup["cleanup"]["removed_patch_backups"]) == 2
    assert len(cleanup["cleanup"]["removed_invalid_workspaces"]) == 1
    assert len(cleanup["cleanup"]["queue_pruned_entries"]) == 4
    assert cleanup["cleanup"]["patch_lock_cleared"] is True
    assert success_backup.exists() is False
    assert orphan_backup.exists() is False
    assert failed_backup.exists() is True
    assert invalid_workspace.exists() is False
    assert json.loads(settings.queue_file.read_text(encoding="utf-8")) == ["job-queued"]
    assert json.loads(settings.patch_lock_file.read_text(encoding="utf-8"))["active"] is False
    assert settings.durable_runtime_hygiene_report_file.exists() is True


def test_durable_runtime_hygiene_runtime_rewrites_sqlite_queue_cleanup(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, store_backend="sqlite")
    store = SQLiteJobStore(settings.sqlite_file)
    store.create_job(_make_job("job-queued", status="queued"))
    store.create_job(_make_job("job-done", status="done", stage="done"))
    store.enqueue_job("job-queued")
    store.enqueue_job("job-queued")
    store.enqueue_job("job-done")
    store.enqueue_job("job-missing")

    runtime = DurableRuntimeHygieneRuntime(
        store=store,
        settings=settings,
        utc_now_iso=lambda: "2026-03-14T00:00:00+00:00",
        report_file=settings.durable_runtime_hygiene_report_file,
    )

    status = runtime.build_hygiene_status()
    assert status["summary"]["queue_cleanup_candidate_count"] == 3

    cleanup = runtime.cleanup()
    assert len(cleanup["cleanup"]["queue_pruned_entries"]) == 3

    with sqlite3.connect(settings.sqlite_file) as conn:
        remaining = [row[0] for row in conn.execute("SELECT job_id FROM queue ORDER BY id ASC").fetchall()]
    assert remaining == ["job-queued"]
