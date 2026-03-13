"""Tests for the repository-aware test runner shell script."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_agenthub_tests.sh"


def _run_script(tmp_path: Path, mode: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    repo_venv = SCRIPT_PATH.parents[1] / ".venv"
    python_bin_dir = str((repo_venv / "bin").resolve())
    env["PATH"] = python_bin_dir + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(repo_venv.resolve())
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), mode],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_run_agenthub_tests_uses_pythonpath_for_python_projects(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_pythonpath.py").write_text(
        "\n".join(
            [
                "import os",
                "from app import VALUE",
                "",
                "def test_pythonpath_is_seeded():",
                "    assert VALUE == 7",
                "    assert os.environ.get('PYTHONPATH', '').startswith('.')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_script(tmp_path, "implement")

    assert result.returncode == 0
    assert "running pytest" in result.stdout
    assert "1 passed" in result.stdout


def test_run_agenthub_tests_skips_when_only_tests_directory_exists(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "example.spec.ts").write_text("describe('noop', () => {})\n", encoding="utf-8")

    result = _run_script(tmp_path, "auto")

    assert result.returncode == 0
    assert "skipping" in result.stdout
    assert "running pytest" not in result.stdout


def test_run_agenthub_tests_supports_explicit_mobile_e2e_modes(tmp_path: Path) -> None:
    runner = tmp_path / "fake-mobile-runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'mobile-runner %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    result = _run_script(
        tmp_path,
        "mobile-e2e-android",
        extra_env={"AGENTHUB_MOBILE_E2E_RUNNER": str(runner)},
    )

    assert result.returncode == 0
    assert "running mobile e2e (android)" in result.stdout
    assert "mobile-runner --platform android" in result.stdout


def test_run_agenthub_tests_prefers_mobile_e2e_when_mobile_scripts_exist(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test:e2e:android":"echo android-e2e","test":"echo unit"}}\n',
        encoding="utf-8",
    )
    runner = tmp_path / "fake-mobile-runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'mobile-runner %s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)

    result = _run_script(
        tmp_path,
        "e2e",
        extra_env={"AGENTHUB_MOBILE_E2E_RUNNER": str(runner)},
    )

    assert result.returncode == 0
    assert "running mobile e2e (android)" in result.stdout
    assert "mobile-runner --platform android" in result.stdout
