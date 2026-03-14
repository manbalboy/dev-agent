"""GitHub CLI and repository-ref helpers for dashboard-originated actions."""

from __future__ import annotations

import re
import subprocess
from typing import List

from fastapi import HTTPException


_ISSUE_URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/issues/\d+")
_ISSUE_NUMBER_PATTERN = re.compile(r"/issues/(?P<number>\d+)")


class DashboardGithubCliRuntime:
    """Encapsulate small GitHub CLI and repository normalization helpers."""

    @staticmethod
    def normalize_repository_ref(value: str) -> str:
        """Normalize GitHub repository input to owner/repo form."""

        raw = (value or "").strip()
        if not raw:
            return ""
        https_match = re.match(r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", raw, re.IGNORECASE)
        if https_match:
            return f"{https_match.group(1)}/{https_match.group(2)}"
        ssh_match = re.match(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$", raw, re.IGNORECASE)
        if ssh_match:
            return f"{ssh_match.group(1)}/{ssh_match.group(2)}"
        plain_match = re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", raw)
        if plain_match:
            return raw
        return ""

    @staticmethod
    def extract_issue_number(issue_url: str) -> int:
        """Extract issue number from GitHub issue URL."""

        match = _ISSUE_NUMBER_PATTERN.search(issue_url)
        if match is None:
            raise HTTPException(
                status_code=502,
                detail=(
                    "이슈 URL에서 번호를 읽지 못했습니다. "
                    "gh CLI 출력 형식을 확인해주세요."
                ),
            )
        return int(match.group("number"))

    @staticmethod
    def extract_issue_url(stdout: str) -> str:
        """Extract issue URL from gh output text."""

        match = _ISSUE_URL_PATTERN.search(stdout)
        if match is None:
            raise HTTPException(
                status_code=502,
                detail=(
                    "이슈 생성 결과에서 URL을 읽지 못했습니다. "
                    "gh CLI 출력 형식을 확인해주세요."
                ),
            )
        return match.group(0)

    @staticmethod
    def run_gh_command(args: List[str], error_context: str) -> str:
        """Run gh command with consistent error mapping."""

        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            stderr_preview = (process.stderr or "").strip()[:500]
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{error_context} 실패: gh CLI 상태를 확인해주세요. "
                    f"stderr: {stderr_preview or '(no stderr)'}"
                ),
            )
        return process.stdout

    @classmethod
    def ensure_agent_run_label(cls, repository: str) -> None:
        """Ensure `agent:run` label exists in the target repository."""

        cls.ensure_label(
            repository=repository,
            label_name="agent:run",
            color="1D76DB",
            description="Trigger AgentHub worker",
        )

    @staticmethod
    def ensure_label(repository: str, label_name: str, color: str, description: str) -> None:
        """Ensure one GitHub label exists in the target repository."""

        process = subprocess.run(
            [
                "gh",
                "label",
                "create",
                label_name,
                "--repo",
                repository,
                "--color",
                color,
                "--description",
                description,
            ],
            capture_output=True,
            text=True,
        )

        if process.returncode == 0:
            return

        stderr_lower = (process.stderr or "").lower()
        if "already exists" in stderr_lower or "name already exists" in stderr_lower:
            return

        stderr_preview = (process.stderr or "").strip()[:500]
        raise HTTPException(
            status_code=502,
            detail=(
                f"{label_name} 라벨 자동 생성 실패: gh CLI 상태를 확인해주세요. "
                f"stderr: {stderr_preview or '(no stderr)'}"
            ),
        )
