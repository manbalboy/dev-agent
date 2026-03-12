"""Workspace/repository preparation runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import shutil
import time

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage


class WorkspaceRepositoryRuntime:
    """Encapsulate execution repository and workspace preparation helpers."""

    def __init__(
        self,
        *,
        settings,
        set_stage,
        append_log,
        run_shell,
        ref_exists,
    ) -> None:
        self.settings = settings
        self.set_stage = set_stage
        self.append_log = append_log
        self.run_shell = run_shell
        self.ref_exists = ref_exists

    @staticmethod
    def job_execution_repository(job: JobRecord) -> str:
        """Return repository used for clone/build/push for one job."""

        source_repository = str(job.source_repository or "").strip()
        return source_repository or str(job.repository or "").strip()

    def job_workspace_path(self, job: JobRecord) -> Path:
        """Resolve workspace path using execution repository."""

        return self.settings.repository_workspace_path(self.job_execution_repository(job), job.app_code)

    def issue_reference_line(self, job: JobRecord) -> str:
        """Return PR-safe issue reference text."""

        if self.job_execution_repository(job) != str(job.repository or "").strip():
            return f"Tracking issue: {job.issue_url}"
        return f"Closes #{job.issue_number}"

    def stage_prepare_repo(self, job: JobRecord, log_path: Path) -> Path:
        """Prepare and align workspace git repository for one job."""

        self.set_stage(job.job_id, JobStage.PREPARE_REPO, log_path)
        repository_path = self.job_workspace_path(job)
        execution_repository = self.job_execution_repository(job)

        if not execution_repository:
            raise CommandExecutionError(
                "No execution repository is configured for this job. "
                "Set app.source_repository or job.source_repository before running."
            )

        if not repository_path.exists():
            self.clone_repository_to_workspace(execution_repository, repository_path, log_path)
        elif not self.workspace_has_git_metadata(repository_path):
            backup_path = self.backup_invalid_workspace(repository_path, log_path)
            self.append_log(
                log_path,
                f"Workspace existed without git metadata. Backed up to {backup_path} and recloning.",
            )
            self.clone_repository_to_workspace(execution_repository, repository_path, log_path)
        else:
            current_origin = self.read_workspace_origin_repository(repository_path, log_path)
            if current_origin and current_origin != execution_repository:
                backup_path = self.backup_invalid_workspace(repository_path, log_path)
                self.append_log(
                    log_path,
                    f"Workspace origin mismatch ({current_origin} != {execution_repository}). "
                    f"Backed up to {backup_path} and recloning.",
                )
                self.clone_repository_to_workspace(execution_repository, repository_path, log_path)
            else:
                self.append_log(log_path, f"Repository already exists at {repository_path}")

        self.run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} fetch origin",
            cwd=repository_path,
            log_path=log_path,
            purpose="git fetch",
        )

        default_remote_ref = f"origin/{self.settings.default_branch}"
        job_remote_ref = f"origin/{job.branch_name}"
        remote_ref = default_remote_ref

        if self.ref_exists(repository_path, job_remote_ref, log_path):
            remote_ref = job_remote_ref

        self.append_log(log_path, f"Branch base selected: {remote_ref}")
        checkout_command = (
            f"git -C {shlex.quote(str(repository_path))} checkout -B "
            f"{shlex.quote(job.branch_name)} {shlex.quote(remote_ref)}"
        )

        try:
            self.run_shell(
                command=checkout_command,
                cwd=repository_path,
                log_path=log_path,
                purpose="branch checkout",
            )
        except CommandExecutionError:
            self.append_log(
                log_path,
                "Default branch checkout failed. Falling back to local branch creation.",
            )
            self.run_shell(
                command=(
                    f"git -C {shlex.quote(str(repository_path))} checkout -B "
                    f"{shlex.quote(job.branch_name)}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="fallback branch checkout",
            )

        self.ensure_workspace_git_excludes(repository_path, log_path)
        return repository_path

    def clone_repository_to_workspace(self, execution_repository: str, repository_path: Path, log_path: Path) -> None:
        """Clone the configured execution repository into the job workspace."""

        self.run_shell(
            command=f"gh repo clone {shlex.quote(execution_repository)} {shlex.quote(str(repository_path))}",
            cwd=self.settings.workspace_dir,
            log_path=log_path,
            purpose="repository clone",
        )

    @staticmethod
    def workspace_has_git_metadata(repository_path: Path) -> bool:
        """Return True when the workspace already contains git metadata."""

        return (repository_path / ".git").exists()

    def read_workspace_origin_repository(self, repository_path: Path, log_path: Path) -> str:
        """Return normalized `owner/repo` from workspace origin remote if available."""

        try:
            result = self.run_shell(
                command=f"git -C {shlex.quote(str(repository_path))} remote get-url origin",
                cwd=repository_path,
                log_path=log_path,
                purpose="git remote origin",
            )
        except CommandExecutionError:
            return ""
        return self.normalize_repository_ref(str(result.stdout or "").strip())

    @staticmethod
    def normalize_repository_ref(value: str) -> str:
        """Normalize GitHub repository references to `owner/repo`."""

        normalized = (value or "").strip()
        if not normalized:
            return ""
        normalized = normalized.removesuffix(".git")
        https_match = re.match(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)$", normalized)
        if https_match:
            return f"{https_match.group('owner')}/{https_match.group('repo')}"
        ssh_match = re.match(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+)$", normalized)
        if ssh_match:
            return f"{ssh_match.group('owner')}/{ssh_match.group('repo')}"
        simple_match = re.match(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$", normalized)
        if simple_match:
            return f"{simple_match.group('owner')}/{simple_match.group('repo')}"
        return normalized

    def backup_invalid_workspace(self, repository_path: Path, log_path: Path) -> Path:
        """Move aside a non-git workspace so clone can recreate it safely."""

        parent = repository_path.parent
        base_name = repository_path.name
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        candidate = parent / f"{base_name}__invalid_{timestamp}"
        suffix = 1
        while candidate.exists():
            suffix += 1
            candidate = parent / f"{base_name}__invalid_{timestamp}_{suffix}"
        shutil.move(str(repository_path), str(candidate))
        self.append_log(log_path, f"Moved invalid workspace to backup path: {candidate}")
        return candidate

    def ensure_workspace_git_excludes(self, repository_path: Path, log_path: Path) -> None:
        """Apply one shared workspace ignore file to each cloned repository."""

        shared_ignore = self.settings.workspace_dir / ".agenthub-global.gitignore"
        patterns = [
            "node_modules/",
            "**/node_modules/",
            ".venv/",
            "**/.venv/",
            "__pycache__/",
            "**/__pycache__/",
            "*.pyc",
            ".pytest_cache/",
            "**/.pytest_cache/",
            ".mypy_cache/",
            "**/.mypy_cache/",
            ".next/",
            "**/.next/",
            ".turbo/",
            "**/.turbo/",
            "dist/",
            "**/dist/",
            "build/",
            "**/build/",
            ".DS_Store",
            "*.log",
        ]
        desired = "\n".join(patterns).rstrip() + "\n"
        current = ""
        if shared_ignore.exists():
            current = shared_ignore.read_text(encoding="utf-8", errors="replace")
        if current != desired:
            shared_ignore.parent.mkdir(parents=True, exist_ok=True)
            shared_ignore.write_text(desired, encoding="utf-8")
            self.append_log(log_path, f"Workspace shared ignore updated: {shared_ignore}")

        self.run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} "
                f"config --local core.excludesfile {shlex.quote(str(shared_ignore))}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="set workspace shared git excludes",
        )
