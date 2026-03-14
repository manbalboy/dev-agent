"""Rollback helpers for patch updater flows."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List


class PatchRollbackRuntime:
    """Execute one baseline rollback to a known source commit."""

    def __init__(
        self,
        *,
        utc_now_iso: Callable[[], str],
        git_timeout_seconds: int = 60,
    ) -> None:
        self.utc_now_iso = utc_now_iso
        self.git_timeout_seconds = git_timeout_seconds

    def rollback_to_commit(
        self,
        *,
        repo_root: str | Path,
        branch: str,
        target_commit: str,
    ) -> Dict[str, Any]:
        """Move the deployment repo back to one known commit."""

        repo_path = Path(repo_root).resolve()
        if not (repo_path / ".git").exists():
            raise RuntimeError(f"Git 저장소가 아닙니다: {repo_path}")
        normalized_target = str(target_commit or "").strip()
        if not normalized_target:
            raise RuntimeError("롤백 대상 commit이 비어 있습니다.")

        current_commit = self._run_git(repo_path, ["rev-parse", "HEAD"])
        dirty = bool(self._run_git(repo_path, ["status", "--porcelain"]))
        if dirty:
            raise RuntimeError("로컬 변경 사항이 있어 자동 롤백을 진행할 수 없습니다.")

        self._run_git(repo_path, ["rev-parse", "--verify", normalized_target])
        operations: List[Dict[str, str]] = [
            {"action": "verify", "value": normalized_target},
        ]

        normalized_branch = str(branch or "").strip()
        if normalized_branch and normalized_branch != "HEAD":
            self._run_git(repo_path, ["checkout", "-B", normalized_branch, normalized_target])
            operations.append({"action": "checkout_branch", "value": normalized_branch})
        else:
            self._run_git(repo_path, ["checkout", "--detach", normalized_target])
            operations.append({"action": "checkout_detached", "value": normalized_target})

        resulting_commit = self._run_git(repo_path, ["rev-parse", "HEAD"])
        return {
            "repo_root": str(repo_path),
            "branch": normalized_branch,
            "source_commit_before": current_commit,
            "target_commit": normalized_target,
            "resulting_commit": resulting_commit,
            "completed_at": self.utc_now_iso(),
            "operations": operations,
        }

    def _run_git(self, repo_root: Path, args: List[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            timeout=self.git_timeout_seconds,
            check=True,
        )
        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        return stdout or stderr
