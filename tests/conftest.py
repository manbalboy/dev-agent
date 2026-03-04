"""Shared pytest fixtures for AgentHub tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

# Ensure app.main module import does not fail in tests because of missing env.
_BOOTSTRAP_DIR = Path(tempfile.gettempdir()) / "agenthub_bootstrap"
_BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
_BOOTSTRAP_CONFIG = _BOOTSTRAP_DIR / "ai_commands.json"
if not _BOOTSTRAP_CONFIG.exists():
    _BOOTSTRAP_CONFIG.write_text("{}\n", encoding="utf-8")

os.environ.setdefault("AGENTHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("AGENTHUB_ALLOWED_REPOSITORY", "owner/repo")
os.environ.setdefault("AGENTHUB_DATA_DIR", str(_BOOTSTRAP_DIR / "data"))
os.environ.setdefault("AGENTHUB_WORKSPACE_DIR", str(_BOOTSTRAP_DIR / "workspaces"))
os.environ.setdefault("AGENTHUB_COMMAND_CONFIG", str(_BOOTSTRAP_CONFIG))
os.environ.setdefault("AGENTHUB_MAX_RETRIES", "3")
os.environ.setdefault("AGENTHUB_TEST_COMMAND", "echo test")
os.environ.setdefault("AGENTHUB_DOCKER_PREVIEW_ENABLED", "false")

from app.config import AppSettings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.store import JsonJobStore  # noqa: E402


@pytest.fixture
def app_components(tmp_path: Path):
    """Create isolated settings/store/app tuple for each test."""

    data_dir = tmp_path / "data"
    workspace_dir = tmp_path / "workspaces"
    command_config = tmp_path / "ai_commands.json"
    command_config.write_text("{}\n", encoding="utf-8")

    settings = AppSettings(
        webhook_secret="test-secret",
        allowed_repository="owner/repo",
        data_dir=data_dir,
        workspace_dir=workspace_dir,
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
        default_branch="main",
        enable_escalation=False,
        enable_stage_md_commits=True,
        api_port=8321,
        store_backend="json",
        sqlite_file=data_dir / "agenthub.db",
        docker_preview_enabled=False,
    )
    settings.ensure_directories()

    store = JsonJobStore(settings.jobs_file, settings.queue_file)
    app = create_app(settings=settings, store=store)

    return settings, store, app
