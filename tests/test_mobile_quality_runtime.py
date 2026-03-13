"""Tests for mobile app quality artifact generation."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.command_runner import CommandResult
from app.mobile_quality_runtime import MobileQualityRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-mobile-quality", *, app_code: str = "food") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=10,
        issue_title="mobile quality test",
        issue_url="https://github.com/owner/repo/issues/10",
        status=JobStatus.RUNNING.value,
        stage=JobStage.TEST_AFTER_IMPLEMENT.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/mobile-quality",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code=app_code,
    )


def test_mobile_quality_runtime_writes_checklist_for_app_targets(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    runtime = MobileQualityRuntime(settings=settings)
    repository_path = tmp_path / "workspace"
    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "SPEC.json").write_text(json.dumps({"app_type": "app"}), encoding="utf-8")

    pid_dir = settings.data_dir / "pids"
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "app_food.json").write_text(
        json.dumps(
            {
                "mode": "expo-android",
                "command": "npx expo run:android",
                "pid": os.getpid(),
                "updated_at": utc_now_iso(),
            }
        ),
        encoding="utf-8",
    )
    (docs_dir / "MOBILE_E2E_RESULT.json").write_text(
        json.dumps(
            {
                "platform": "android",
                "status": "passed",
                "runner": "npm_script",
                "command": "npm run test:e2e:android",
                "target_name": "Pixel 8",
                "target_id": "emulator-5554",
                "notes": "reused already booted android emulator",
            }
        ),
        encoding="utf-8",
    )

    artifact_path = runtime.write_mobile_app_checklist(
        job=_make_job(),
        repository_path=repository_path,
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        test_results=[
            {
                "name": "gpt",
                "result": CommandResult(
                    command="npm test",
                    exit_code=0,
                    stdout="1 passed\n",
                    stderr="",
                    duration_seconds=1.25,
                ),
                "report": repository_path / "TEST_REPORT_TEST_AFTER_IMPLEMENT.md",
            }
        ],
    )

    assert artifact_path == docs_dir / "MOBILE_APP_CHECKLIST.md"
    content = artifact_path.read_text(encoding="utf-8")
    assert "Verification Target: `android_emulator`" in content
    assert "State: `running`" in content
    assert "Command: `npx expo run:android`" in content
    assert "## Mobile E2E Result" in content
    assert "Status: `passed`" in content
    assert "Target ID: `emulator-5554`" in content
    assert "`gpt`: `PASS`" in content
    assert "TEST_REPORT_TEST_AFTER_IMPLEMENT.md" in content


def test_mobile_quality_runtime_skips_non_app_targets(app_components, tmp_path: Path) -> None:
    settings, _, _ = app_components
    runtime = MobileQualityRuntime(settings=settings)
    repository_path = tmp_path / "workspace"
    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "SPEC.json").write_text(json.dumps({"app_type": "web"}), encoding="utf-8")

    artifact_path = runtime.write_mobile_app_checklist(
        job=_make_job(app_code="webapp"),
        repository_path=repository_path,
        stage=JobStage.TEST_AFTER_IMPLEMENT,
        test_results=[],
    )

    assert artifact_path is None
    assert not (docs_dir / "MOBILE_APP_CHECKLIST.md").exists()
