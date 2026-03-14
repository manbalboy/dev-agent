"""Compatibility helpers retained as thin runtime boundaries for dashboard routes."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List

from fastapi import HTTPException

import app.assistant_runtime as assistant_runtime
from app.dashboard_github_cli_runtime import DashboardGithubCliRuntime
from app.workflow_resolution import (
    read_default_workflow_id as _shared_read_default_workflow_id,
    read_registered_apps as _shared_read_registered_apps,
    write_registered_apps as _shared_write_registered_apps,
)


class DashboardCompatRuntime:
    """Collect thin helper implementations kept for dashboard compatibility."""

    @staticmethod
    def extract_issue_number(issue_url: str) -> int:
        """Extract issue number from GitHub issue URL."""

        return DashboardGithubCliRuntime.extract_issue_number(issue_url)

    @staticmethod
    def extract_issue_url(stdout: str) -> str:
        """Extract issue URL from gh output text."""

        return DashboardGithubCliRuntime.extract_issue_url(stdout)

    @staticmethod
    def run_gh_command(args: List[str], error_context: str) -> str:
        """Run gh command with consistent error mapping."""

        return DashboardGithubCliRuntime.run_gh_command(args, error_context)

    @staticmethod
    def run_log_analyzer(
        *,
        assistant: str,
        prompt: str,
        templates: Dict[str, str],
        run_codex_log_analysis: Callable[[str, Dict[str, str]], str],
        run_gemini_log_analysis: Callable[[str, Dict[str, str]], str],
    ) -> str:
        """Dispatch one log-analysis request while preserving wrapper monkeypatch points."""

        if assistant == "codex":
            return run_codex_log_analysis(prompt, templates)
        if assistant == "gemini":
            return run_gemini_log_analysis(prompt, templates)
        raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")

    @staticmethod
    def run_assistant_chat_provider(
        *,
        assistant: str,
        prompt: str,
        templates: Dict[str, str],
        run_codex_chat_completion: Callable[[str, Dict[str, str]], str],
        run_gemini_chat_completion: Callable[[str, Dict[str, str]], str],
    ) -> str:
        """Dispatch one chat request while preserving wrapper monkeypatch points."""

        if assistant == "codex":
            return run_codex_chat_completion(prompt, templates)
        if assistant == "gemini":
            return run_gemini_chat_completion(prompt, templates)
        raise HTTPException(status_code=400, detail=f"지원하지 않는 assistant: {assistant}")

    @staticmethod
    def run_codex_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Codex chat execution to shared assistant runtime."""

        return assistant_runtime.run_codex_chat_completion(prompt, templates)

    @staticmethod
    def run_gemini_chat_completion(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Gemini chat execution to shared assistant runtime."""

        return assistant_runtime.run_gemini_chat_completion(prompt, templates)

    @staticmethod
    def run_codex_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Codex log-analysis execution to shared assistant runtime."""

        return assistant_runtime.run_codex_log_analysis(prompt, templates)

    @staticmethod
    def run_gemini_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Gemini log-analysis execution to shared assistant runtime."""

        return assistant_runtime.run_gemini_log_analysis(prompt, templates)

    @staticmethod
    def run_claude_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Claude alias to shared assistant runtime compatibility path."""

        return assistant_runtime.run_claude_log_analysis(prompt, templates)

    @staticmethod
    def run_copilot_log_analysis(prompt: str, templates: Dict[str, str]) -> str:
        """Forward Copilot alias to shared assistant runtime compatibility path."""

        return assistant_runtime.run_copilot_log_analysis(prompt, templates)

    @staticmethod
    def ensure_agent_run_label(repository: str) -> None:
        """Ensure `agent:run` label exists in the target repository."""

        DashboardGithubCliRuntime.ensure_agent_run_label(repository)

    @staticmethod
    def ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
        """Ensure one GitHub label exists in the target repository."""

        DashboardGithubCliRuntime.ensure_label(repository, label_name, color, description)

    @staticmethod
    def normalize_repository_ref(value: str) -> str:
        """Normalize GitHub repository input to owner/repo form."""

        return DashboardGithubCliRuntime.normalize_repository_ref(value)

    @staticmethod
    def read_registered_apps(
        path: Path,
        repository: str,
        default_workflow_id: str = "",
    ) -> List[Dict[str, str]]:
        """Read app registration list from JSON file with a default fallback."""

        return _shared_read_registered_apps(path, repository, default_workflow_id=default_workflow_id)

    @staticmethod
    def write_registered_apps(path: Path, apps: List[Dict[str, str]]) -> None:
        """Persist app list as pretty JSON."""

        _shared_write_registered_apps(path, apps)

    @staticmethod
    def read_default_workflow_id(path: Path) -> str:
        """Read default workflow id from workflow config with safe fallback."""

        return _shared_read_default_workflow_id(path)
