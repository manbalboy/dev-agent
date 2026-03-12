from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "workspace_app.sh"


def _make_stub_command(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"$(basename \"$0\") $*\" >> \"$CALLS_FILE\"\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _build_root(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "agenthub-root"
    workspace = root / "workspaces" / "maps" / "owner__repo"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "node_modules").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "logs" / "apps").mkdir(parents=True, exist_ok=True)
    (root / "data" / "pids").mkdir(parents=True, exist_ok=True)
    (root / "config" / "app_ports.json").write_text("{}\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    _make_stub_command(fake_bin / "npm")
    _make_stub_command(fake_bin / "npx")

    calls_file = tmp_path / "calls.log"
    env = os.environ.copy()
    env["AGENTHUB_ROOT_DIR"] = str(root)
    env["AGENTHUB_WORKSPACE_DIR"] = str(root / "workspaces")
    env["AGENTHUB_ALLOWED_REPOSITORY"] = "owner/repo"
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CALLS_FILE"] = str(calls_file)
    return root, env


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )


def test_workspace_app_script_web_mode_start_status_stop(tmp_path: Path) -> None:
    root, env = _build_root(tmp_path)

    try:
        start = _run(env, "start", "--app", "maps", "--repo", "owner/repo", "--mode", "web")
        assert start.returncode == 0, start.stderr
        assert "mode=web" in start.stdout
        assert "port=" in start.stdout
        assert "Command: exec npm start" in start.stdout

        status = _run(env, "status", "--app", "maps")
        assert status.returncode == 0
        assert "RUNNING app=maps" in status.stdout
        assert "mode=web" in status.stdout
        assert "COMMAND exec npm start" in status.stdout

        meta_path = root / "data" / "pids" / "app_maps.json"
        assert meta_path.exists() is True
        assert '"mode": "web"' in meta_path.read_text(encoding="utf-8")

        calls_text = (tmp_path / "calls.log").read_text(encoding="utf-8")
        assert "npm start" in calls_text
    finally:
        stop = _run(env, "stop", "--app", "maps")
        assert stop.returncode == 0


def test_workspace_app_script_supports_expo_android_mode(tmp_path: Path) -> None:
    root, env = _build_root(tmp_path)

    try:
        start = _run(env, "start", "--app", "maps", "--repo", "owner/repo", "--mode", "expo-android")
        assert start.returncode == 0, start.stderr
        assert "mode=expo-android" in start.stdout
        assert "Command: exec npx expo start --android" in start.stdout
        assert "URL:" not in start.stdout

        status = _run(env, "status", "--app", "maps")
        assert status.returncode == 0
        assert "RUNNING app=maps" in status.stdout
        assert "mode=expo-android" in status.stdout
        assert "COMMAND exec npx expo start --android" in status.stdout
        assert "port=(unassigned)" in status.stdout

        meta_path = root / "data" / "pids" / "app_maps.json"
        assert meta_path.exists() is True
        meta_text = meta_path.read_text(encoding="utf-8")
        assert '"mode": "expo-android"' in meta_text
        assert '"port": ""' in meta_text

        calls_text = (tmp_path / "calls.log").read_text(encoding="utf-8")
        assert "npx expo start --android" in calls_text
    finally:
        stop = _run(env, "stop", "--app", "maps")
        assert stop.returncode == 0
