"""Tests for the local config bootstrap script."""

from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "setup_local_config.sh"


def _run_setup(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    target_root = tmp_path / "agenthub-root"
    return subprocess.run(
        [
            "bash",
            str(SCRIPT_PATH),
            "--root",
            str(target_root),
            "--repo",
            "owner/repo",
            "--secret",
            "fixed-secret",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_setup_local_config_uses_safe_codex_flags_by_default(tmp_path: Path) -> None:
    result = _run_setup(tmp_path)

    assert result.returncode == 0
    commands_path = tmp_path / "agenthub-root" / "config" / "ai_commands.json"
    env_path = tmp_path / "agenthub-root" / ".env"
    content = commands_path.read_text(encoding="utf-8")
    env_content = env_path.read_text(encoding="utf-8")
    assert "--dangerously-bypass-approvals-and-sandbox" not in content
    assert "exec - -C {work_dir} --color never" in content
    assert "AGENTHUB_SELF_CHECK_STALE_MINUTES=45" in env_content
    assert "AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL=" in env_content
    assert "AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL=" in env_content
    assert "AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_TIMEOUT_SECONDS=10" in env_content
    assert "AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES=180" in env_content
    assert "AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES=720" in env_content
    assert "AGENTHUB_PUBLIC_BASE_URL=http://127.0.0.1:8321" in env_content
    assert "AGENTHUB_ENFORCE_HTTPS=false" in env_content
    assert "AGENTHUB_TRUST_X_FORWARDED_PROTO=false" in env_content
    assert "AGENTHUB_CORS_ALLOW_ALL=false" in env_content
    assert "DANGER_MODE=false" in result.stdout


def test_setup_local_config_supports_explicit_danger_mode(tmp_path: Path) -> None:
    result = _run_setup(tmp_path, "--danger-mode")

    assert result.returncode == 0
    commands_path = tmp_path / "agenthub-root" / "config" / "ai_commands.json"
    content = commands_path.read_text(encoding="utf-8")
    assert "--dangerously-bypass-approvals-and-sandbox" in content
    assert "DANGER_MODE=true" in result.stdout
