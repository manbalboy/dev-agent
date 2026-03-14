from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from app.store import JsonJobStore


def _build_app(tmp_path: Path, *, enforce_https: bool, trust_x_forwarded_proto: bool):
    command_config = tmp_path / "ai_commands.json"
    command_config.write_text("{}\n", encoding="utf-8")
    settings = AppSettings(
        webhook_secret="0123456789abcdef0123456789abcdef",
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
        store_backend="json",
        sqlite_file=tmp_path / "data" / "agenthub.db",
        public_base_url="https://agenthub.example.com",
        enforce_https=enforce_https,
        trust_x_forwarded_proto=trust_x_forwarded_proto,
        cors_allow_all=False,
        cors_origins="https://agenthub.example.com",
        docker_preview_enabled=False,
    )
    settings.ensure_directories()
    store = JsonJobStore(settings.jobs_file, settings.queue_file)
    return create_app(settings=settings, store=store)


def test_https_enforcement_rejects_non_https_requests_but_keeps_healthz(tmp_path: Path) -> None:
    client = TestClient(_build_app(tmp_path, enforce_https=True, trust_x_forwarded_proto=False))

    response = client.get("/api/admin/security-governance")
    healthz = client.get("/healthz")

    assert response.status_code == 426
    assert response.json()["status"] == "https_required"
    assert healthz.status_code == 200
    assert healthz.json()["status"] == "ok"


def test_https_enforcement_accepts_trusted_forwarded_proto_header(tmp_path: Path) -> None:
    client = TestClient(_build_app(tmp_path, enforce_https=True, trust_x_forwarded_proto=True))

    response = client.get(
        "/api/admin/security-governance",
        headers={"X-Forwarded-Proto": "https"},
    )

    assert response.status_code == 200
    assert response.json()["transport"]["https_enforced"] is True
