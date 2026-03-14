"""Patch/update status helpers for operator-facing dashboard controls."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class PatchControlRuntime:
    """Encapsulate Git-based patch detection for the current deployment repo."""

    def __init__(
        self,
        *,
        repo_root: Path,
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.repo_root = repo_root
        self.utc_now_iso = utc_now_iso

    def build_patch_status(self, *, refresh: bool = False) -> Dict[str, Any]:
        """Return one patch/update status payload for dashboard use."""

        if not (self.repo_root / ".git").exists():
            return {
                "status": "unavailable",
                "repo_root": str(self.repo_root),
                "refresh_attempted": bool(refresh),
                "checked_at": self.utc_now_iso(),
                "message": "Git 저장소가 아니어서 패치 상태를 확인할 수 없습니다.",
                "update_available": False,
            }

        try:
            branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            current_commit = self._run_git(["rev-parse", "HEAD"])
            current_subject = self._run_git(["log", "--format=%s", "-n", "1", "HEAD"])
            working_tree_dirty = bool(self._run_git(["status", "--porcelain"]))
            upstream_ref = self._resolve_upstream_ref(branch)
            if not upstream_ref:
                return {
                    "status": "unavailable",
                    "repo_root": str(self.repo_root),
                    "current_branch": branch,
                    "current_commit": current_commit,
                    "current_short_commit": current_commit[:8],
                    "current_subject": current_subject,
                    "working_tree_dirty": working_tree_dirty,
                    "refresh_attempted": bool(refresh),
                    "checked_at": self.utc_now_iso(),
                    "message": "추적 중인 원격 브랜치를 찾지 못해 패치 상태를 확인할 수 없습니다.",
                    "update_available": False,
                }

            fetch_error = ""
            if refresh:
                try:
                    self._run_git(["fetch", "--quiet", "origin"], check=True)
                except subprocess.CalledProcessError as exc:
                    fetch_error = self._command_error_message(exc)

            upstream_commit = self._run_git(["rev-parse", upstream_ref])
            upstream_subject = self._run_git(["log", "--format=%s", "-n", "1", upstream_ref])
            behind_count = self._safe_int(self._run_git(["rev-list", "--count", f"HEAD..{upstream_ref}"]))
            ahead_count = self._safe_int(self._run_git(["rev-list", "--count", f"{upstream_ref}..HEAD"]))
            update_available = behind_count > 0
            state = "update_available" if update_available else "up_to_date"
            message = (
                "패치가 있습니다. 진행하시겠습니까?"
                if update_available
                else "현재 배포 코드는 원격 기준 최신 상태입니다."
            )
            if fetch_error:
                message = f"{message} 원격 fetch 경고: {fetch_error}"

            return {
                "status": state,
                "repo_root": str(self.repo_root),
                "current_branch": branch,
                "upstream_ref": upstream_ref,
                "current_commit": current_commit,
                "current_short_commit": current_commit[:8],
                "current_subject": current_subject,
                "upstream_commit": upstream_commit,
                "upstream_short_commit": upstream_commit[:8],
                "upstream_subject": upstream_subject,
                "behind_count": behind_count,
                "ahead_count": ahead_count,
                "working_tree_dirty": working_tree_dirty,
                "refresh_attempted": bool(refresh),
                "fetch_error": fetch_error,
                "checked_at": self.utc_now_iso(),
                "update_available": update_available,
                "message": message,
            }
        except subprocess.CalledProcessError as exc:
            return {
                "status": "error",
                "repo_root": str(self.repo_root),
                "refresh_attempted": bool(refresh),
                "checked_at": self.utc_now_iso(),
                "message": self._command_error_message(exc),
                "update_available": False,
            }

    def _resolve_upstream_ref(self, branch: str) -> Optional[str]:
        try:
            upstream_ref = self._run_git(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                check=True,
            )
            if upstream_ref:
                return upstream_ref
        except subprocess.CalledProcessError:
            pass

        candidates = []
        if branch and branch != "HEAD":
            candidates.append(f"origin/{branch}")
        candidates.extend(["origin/master", "origin/main"])
        for ref_name in candidates:
            if self._ref_exists(ref_name):
                return ref_name
        return None

    def _ref_exists(self, ref_name: str) -> bool:
        try:
            self._run_git(["rev-parse", ref_name], check=True)
        except subprocess.CalledProcessError:
            return False
        return True

    def _run_git(self, args: List[str], *, check: bool = True) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=check,
            timeout=30,
        )
        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        return stdout or stderr

    @staticmethod
    def _safe_int(value: str) -> int:
        try:
            return int(str(value or "").strip())
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _command_error_message(exc: subprocess.CalledProcessError) -> str:
        stderr = str(getattr(exc, "stderr", "") or "").strip()
        stdout = str(getattr(exc, "stdout", "") or "").strip()
        if stderr:
            return stderr
        if stdout:
            return stdout
        return f"git 명령이 exit code {getattr(exc, 'returncode', 1)} 로 종료되었습니다."
