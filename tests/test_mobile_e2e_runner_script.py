from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "mobile_e2e_runner.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_mobile_e2e_runner_android_reuses_booted_emulator_and_writes_artifact(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "calls.log"

    _write_executable(
        fake_bin / "adb",
        "#!/usr/bin/env bash\n"
        "echo \"adb $*\" >> \"$CALLS_FILE\"\n"
        "if [ \"$1\" = \"devices\" ]; then\n"
        "  printf 'List of devices attached\\nemulator-5554\\tdevice\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"wait-for-device\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"shell\" ] && [ \"$2\" = \"getprop\" ]; then\n"
        "  printf '1\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "emulator",
        "#!/usr/bin/env bash\n"
        "echo \"emulator $*\" >> \"$CALLS_FILE\"\n"
        "if [ \"$1\" = \"-list-avds\" ]; then\n"
        "  printf 'Pixel_8_API_34\\n'\n"
        "fi\n"
        "exit 0\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CALLS_FILE"] = str(calls_file)
    env["AGENTHUB_MOBILE_E2E_COMMAND_ANDROID"] = "printf 'android e2e ok\\n'"

    result = _run(tmp_path, env, "--platform", "android")

    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "_docs" / "MOBILE_E2E_RESULT.json").read_text(encoding="utf-8"))
    assert payload["platform"] == "android"
    assert payload["status"] == "passed"
    assert payload["runner"] == "custom_command"
    assert payload["target_id"] == "emulator-5554"
    assert payload["booted"] is True
    assert "reused already booted android emulator" in payload["notes"]


def test_mobile_e2e_runner_ios_boots_simulator_and_uses_npm_script(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "calls.log"
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test:e2e:ios": "echo ios-e2e"}}),
        encoding="utf-8",
    )

    _write_executable(
        fake_bin / "xcrun",
        "#!/usr/bin/env bash\n"
        "echo \"xcrun $*\" >> \"$CALLS_FILE\"\n"
        "if [ \"$1\" = \"simctl\" ] && [ \"$2\" = \"list\" ] && [ \"$3\" = \"devices\" ] && [ \"$4\" = \"booted\" ] && [ \"$5\" = \"-j\" ]; then\n"
        "  printf '{\"devices\": {\"iOS 18.0\": []}}\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"simctl\" ] && [ \"$2\" = \"list\" ] && [ \"$3\" = \"devices\" ] && [ \"$4\" = \"available\" ] && [ \"$5\" = \"-j\" ]; then\n"
        "  printf '{\"devices\": {\"iOS 18.0\": [{\"name\": \"iPhone 16\", \"udid\": \"SIM-123\", \"isAvailable\": true}]}}\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "open",
        "#!/usr/bin/env bash\n"
        "echo \"open $*\" >> \"$CALLS_FILE\"\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "npm",
        "#!/usr/bin/env bash\n"
        "echo \"npm $*\" >> \"$CALLS_FILE\"\n"
        "printf 'ios e2e ok\\n'\n"
        "exit 0\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CALLS_FILE"] = str(calls_file)

    result = _run(tmp_path, env, "--platform", "ios")

    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "_docs" / "MOBILE_E2E_RESULT.json").read_text(encoding="utf-8"))
    assert payload["platform"] == "ios"
    assert payload["status"] == "passed"
    assert payload["runner"] == "npm_script"
    assert payload["target_name"] == "iPhone 16"
    assert payload["target_id"] == "SIM-123"
    calls_text = calls_file.read_text(encoding="utf-8")
    assert "xcrun simctl boot SIM-123" in calls_text
    assert "npm run test:e2e:ios" in calls_text
