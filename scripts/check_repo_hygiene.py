#!/usr/bin/env python3
"""Lightweight repository hygiene checks for production-readiness basics."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


FORBIDDEN_TRACKED_FILES = (
    ".env",
    ".webhook_secret.txt",
    "config/ai_commands.json",
    "config/apps.json",
    "config/app_ports.json",
)


def _tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_checks(root: Path) -> list[str]:
    errors: list[str] = []
    tracked = _tracked_files(root)

    for relpath in FORBIDDEN_TRACKED_FILES:
        if relpath in tracked:
            errors.append(f"forbidden tracked file present: {relpath}")

    env_example = _read_text(root / ".env.example")
    if "AGENTHUB_CORS_ALLOW_ALL=true" in env_example:
        errors.append(".env.example must not enable allow-all CORS by default")
    if "AGENTHUB_CORS_ORIGINS=*" in env_example:
        errors.append(".env.example must not use wildcard CORS origins by default")
    if "AGENTHUB_PUBLIC_BASE_URL=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_PUBLIC_BASE_URL")
    if "AGENTHUB_ENFORCE_HTTPS=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_ENFORCE_HTTPS")
    if "AGENTHUB_TRUST_X_FORWARDED_PROTO=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_TRUST_X_FORWARDED_PROTO")
    if "AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_URL")
    if "AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_SELF_CHECK_ALERT_CRITICAL_WEBHOOK_URL")
    if "AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_TIMEOUT_SECONDS=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_SELF_CHECK_ALERT_WEBHOOK_TIMEOUT_SECONDS")
    if "AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_SELF_CHECK_ALERT_REPEAT_MINUTES")
    if "AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES=" not in env_example:
        errors.append(".env.example must declare AGENTHUB_SELF_CHECK_ALERT_FAILURE_BACKOFF_MAX_MINUTES")

    ai_commands_example = _read_text(root / "config" / "ai_commands.example.json")
    if "--dangerously-bypass-approvals-and-sandbox" in ai_commands_example:
        errors.append("config/ai_commands.example.json must keep dangerous codex flags opt-in")

    for required in ("SECURITY.md", "CONTRIBUTING.md", ".github/workflows/ci.yml"):
        if not (root / required).exists():
            errors.append(f"required production-readiness file missing: {required}")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = run_checks(root)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1
    print("[OK] repository hygiene checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
