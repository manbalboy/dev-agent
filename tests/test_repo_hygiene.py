from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.check_repo_hygiene import run_checks


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
    return repo


def test_repo_hygiene_passes_for_current_repository() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert run_checks(repo_root) == []


def test_repo_hygiene_detects_tracked_secret_and_unsafe_examples(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".env.example").write_text(
        "AGENTHUB_CORS_ALLOW_ALL=true\nAGENTHUB_CORS_ORIGINS=*\n",
        encoding="utf-8",
    )
    (repo / ".webhook_secret.txt").write_text("secret\n", encoding="utf-8")
    (repo / "config").mkdir()
    (repo / "config" / "ai_commands.example.json").write_text(
        '{"coder": "codex exec - --dangerously-bypass-approvals-and-sandbox"}\n',
        encoding="utf-8",
    )
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env.example", ".webhook_secret.txt", "config/ai_commands.example.json", ".github/workflows/ci.yml"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

    errors = run_checks(repo)

    assert "forbidden tracked file present: .webhook_secret.txt" in errors
    assert ".env.example must not enable allow-all CORS by default" in errors
    assert ".env.example must not use wildcard CORS origins by default" in errors
    assert "config/ai_commands.example.json must keep dangerous codex flags opt-in" in errors
    assert "required production-readiness file missing: SECURITY.md" in errors
    assert "required production-readiness file missing: CONTRIBUTING.md" in errors
