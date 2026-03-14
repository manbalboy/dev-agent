from __future__ import annotations

import json

from app.patch_backup_runtime import PatchBackupRuntime


def test_patch_backup_runtime_copies_state_files_and_writes_manifest(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "jobs.json").write_text('{"job-1": {"status": "queued"}}\n', encoding="utf-8")
    (data_dir / "queue.json").write_text('["job-1"]\n', encoding="utf-8")
    sqlite_file = data_dir / "agenthub.db"
    sqlite_file.write_bytes(b"sqlite-state")

    runtime = PatchBackupRuntime(
        backups_dir=data_dir / "patch_backups",
        data_root=data_dir,
        state_files={
            "jobs": data_dir / "jobs.json",
            "queue": data_dir / "queue.json",
            "sqlite": sqlite_file,
            "missing": data_dir / "missing.json",
        },
        utc_now_iso=lambda: "2026-03-14T10:00:00+09:00",
    )

    manifest = runtime.create_backup(
        patch_run_id="patch-1",
        repo_root=tmp_path / "repo",
        branch="master",
        source_commit="11112222",
        target_commit="aaaabbbb",
        reason="before_patch_restart",
    )

    assert manifest["patch_run_id"] == "patch-1"
    assert manifest["file_count"] == 3
    manifest_path = data_dir / "patch_backups" / manifest["backup_id"] / "manifest.json"
    assert manifest_path.exists()
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_payload["reason"] == "before_patch_restart"
    copied_jobs = data_dir / "patch_backups" / manifest["backup_id"] / "jobs.json"
    copied_queue = data_dir / "patch_backups" / manifest["backup_id"] / "queue.json"
    copied_sqlite = data_dir / "patch_backups" / manifest["backup_id"] / "agenthub.db"
    assert copied_jobs.exists()
    assert copied_queue.exists()
    assert copied_sqlite.exists()


def test_patch_backup_runtime_restore_backup_replays_files(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = data_dir / "jobs.json"
    jobs_file.write_text('{"job-1": {"status": "queued"}}\n', encoding="utf-8")

    runtime = PatchBackupRuntime(
        backups_dir=data_dir / "patch_backups",
        data_root=data_dir,
        state_files={"jobs": jobs_file},
        utc_now_iso=lambda: "2026-03-14T10:00:00+09:00",
    )
    manifest = runtime.create_backup(
        patch_run_id="patch-1",
        repo_root=tmp_path / "repo",
        branch="master",
        source_commit="11112222",
        target_commit="aaaabbbb",
        reason="before_patch_restart",
    )

    jobs_file.write_text('{"job-1": {"status": "failed"}}\n', encoding="utf-8")
    payload = runtime.restore_backup(manifest=manifest)

    assert payload["backup_id"] == manifest["backup_id"]
    assert payload["restored_file_count"] == 1
    assert payload["verification"]["ok"] is True
    assert '"queued"' in jobs_file.read_text(encoding="utf-8")


def test_patch_backup_runtime_verify_manifest_detects_missing_backup_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = data_dir / "jobs.json"
    jobs_file.write_text('{"job-1": {"status": "queued"}}\n', encoding="utf-8")

    runtime = PatchBackupRuntime(
        backups_dir=data_dir / "patch_backups",
        data_root=data_dir,
        state_files={"jobs": jobs_file},
        utc_now_iso=lambda: "2026-03-14T10:00:00+09:00",
    )
    manifest = runtime.create_backup(
        patch_run_id="patch-1",
        repo_root=tmp_path / "repo",
        branch="master",
        source_commit="11112222",
        target_commit="aaaabbbb",
        reason="before_patch_restart",
    )

    backup_file = data_dir / "patch_backups" / manifest["backup_id"] / "jobs.json"
    backup_file.unlink()

    payload = runtime.verify_backup_manifest(manifest=manifest)

    assert payload["ok"] is False
    assert payload["missing_files"] == ["jobs.json"]
