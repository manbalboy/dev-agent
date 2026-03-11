"""Tests for shell command execution helpers."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import run_shell_command


def test_run_shell_command_invokes_heartbeat_callback_while_process_runs(tmp_path: Path) -> None:
    heartbeats: list[str] = []
    logs: list[str] = []

    result = run_shell_command(
        command="python3 -c 'import time; time.sleep(0.18)'",
        cwd=tmp_path,
        log_writer=logs.append,
        heartbeat_callback=lambda: heartbeats.append("tick"),
        heartbeat_interval_seconds=0.05,
    )

    assert result.exit_code == 0
    assert len(heartbeats) >= 2
    assert any(line.startswith("[DONE]") for line in logs)
