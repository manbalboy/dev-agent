from __future__ import annotations

from subprocess import CompletedProcess

import pytest
from fastapi import HTTPException

import app.dashboard_github_cli_runtime as github_runtime
from app.dashboard_github_cli_runtime import DashboardGithubCliRuntime


def test_dashboard_github_cli_runtime_normalizes_repository_ref() -> None:
    assert DashboardGithubCliRuntime.normalize_repository_ref("https://github.com/manbalboy/Food.git") == "manbalboy/Food"
    assert DashboardGithubCliRuntime.normalize_repository_ref("git@github.com:manbalboy/Food.git") == "manbalboy/Food"
    assert DashboardGithubCliRuntime.normalize_repository_ref("manbalboy/Food") == "manbalboy/Food"
    assert DashboardGithubCliRuntime.normalize_repository_ref("not a repo") == ""


def test_dashboard_github_cli_runtime_extracts_issue_url_and_number() -> None:
    stdout = "created: https://github.com/owner/repo/issues/501"

    issue_url = DashboardGithubCliRuntime.extract_issue_url(stdout)
    issue_number = DashboardGithubCliRuntime.extract_issue_number(issue_url)

    assert issue_url == "https://github.com/owner/repo/issues/501"
    assert issue_number == 501


def test_dashboard_github_cli_runtime_maps_gh_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        github_runtime.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args=args[0], returncode=1, stdout="", stderr="bad auth"),
    )

    with pytest.raises(HTTPException) as exc_info:
        DashboardGithubCliRuntime.run_gh_command(["gh", "issue", "create"], "GitHub 이슈 생성")

    assert exc_info.value.status_code == 502
    assert "bad auth" in str(exc_info.value.detail)


def test_dashboard_github_cli_runtime_accepts_existing_label(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        return CompletedProcess(args=args, returncode=1, stdout="", stderr="name already exists")

    monkeypatch.setattr(github_runtime.subprocess, "run", fake_run)

    DashboardGithubCliRuntime.ensure_agent_run_label("owner/repo")

    assert calls[0][:4] == ("gh", "label", "create", "agent:run")
