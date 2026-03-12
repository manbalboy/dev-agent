"""Git/GitHub provider execution runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any, Callable, Dict, Optional

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage


class ProviderRuntime:
    """Encapsulate git/github provider execution outside the main orchestrator."""

    def __init__(
        self,
        *,
        settings,
        store,
        run_shell: Callable[..., Any],
        set_stage: Callable[[str, JobStage, Path], None],
        require_job: Callable[[str], JobRecord],
        job_execution_repository: Callable[[JobRecord], str],
        deploy_preview_and_smoke_test: Callable[[JobRecord, Path, Path], Dict[str, str]],
        docs_file: Callable[[Path, str], Path],
        stage_prepare_pr_summary: Callable[[JobRecord, Path, Dict[str, Path], Path], Optional[Path]],
        issue_reference_line: Callable[[JobRecord], str],
        append_preview_section_to_pr_body: Callable[[Path, Dict[str, str]], None],
        append_actor_log: Callable[[Path, str, str], None],
    ) -> None:
        self.settings = settings
        self.store = store
        self.run_shell = run_shell
        self.set_stage = set_stage
        self.require_job = require_job
        self.job_execution_repository = job_execution_repository
        self.deploy_preview_and_smoke_test = deploy_preview_and_smoke_test
        self.docs_file = docs_file
        self.stage_prepare_pr_summary = stage_prepare_pr_summary
        self.issue_reference_line = issue_reference_line
        self.append_preview_section_to_pr_body = append_preview_section_to_pr_body
        self.append_actor_log = append_actor_log

    def stage_push_branch(self, *, job: JobRecord, repository_path: Path, log_path: Path) -> None:
        self.set_stage(job.job_id, JobStage.PUSH_BRANCH, log_path)
        self.push_branch_with_recovery(
            repository_path=repository_path,
            branch_name=job.branch_name,
            log_path=log_path,
            purpose="git push",
        )

    def stage_create_pr(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
    ) -> None:
        self.set_stage(job.job_id, JobStage.CREATE_PR, log_path)
        refreshed_job = self.require_job(job.job_id)
        execution_repository = self.job_execution_repository(refreshed_job)
        preview_info = self.deploy_preview_and_smoke_test(refreshed_job, repository_path, log_path)

        pr_body_path = self.docs_file(repository_path, "PR_BODY.md")
        generated_summary_path = self.stage_prepare_pr_summary(
            refreshed_job,
            repository_path,
            paths,
            log_path,
        )
        if generated_summary_path is not None and generated_summary_path.exists():
            content = generated_summary_path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                pr_body_path.write_text(content + "\n", encoding="utf-8")
            else:
                pr_body_path.write_text(self._default_pr_body(refreshed_job), encoding="utf-8")
        else:
            pr_body_path.write_text(self._default_pr_body(refreshed_job), encoding="utf-8")

        self.append_preview_section_to_pr_body(pr_body_path, preview_info)

        title = f"AgentHub: {refreshed_job.issue_title}"
        create_command = (
            f"gh pr create --repo {shlex.quote(execution_repository)} "
            f"--head {shlex.quote(job.branch_name)} "
            f"--base {shlex.quote(self.settings.default_branch)} "
            f"--title {shlex.quote(title)} "
            f"--body-file {shlex.quote(str(pr_body_path))}"
        )

        create_result = None
        try:
            create_result = self.run_shell(
                command=create_command,
                cwd=repository_path,
                log_path=log_path,
                purpose="create pull request",
            )
        except CommandExecutionError as error:
            if "already exists" not in str(error).lower():
                raise
            self.append_actor_log(
                log_path,
                "GITHUB",
                "PR already exists. Will update body and fetch existing PR URL.",
            )
            self.run_shell(
                command=(
                    f"gh pr edit --repo {shlex.quote(execution_repository)} "
                    f"{shlex.quote(job.branch_name)} "
                    f"--body-file {shlex.quote(str(pr_body_path))}"
                ),
                cwd=repository_path,
                log_path=log_path,
                purpose="update existing pull request body",
            )

        pr_url = self.get_pr_url(job, repository_path, log_path, create_result)
        if pr_url:
            self.store.update_job(job.job_id, pr_url=pr_url)
            return
        raise CommandExecutionError(
            "PR creation appears to have succeeded but URL was not found. "
            "Next action: run `gh pr view <branch> --json url` manually."
        )

    def push_branch_with_recovery(
        self,
        *,
        repository_path: Path,
        branch_name: str,
        log_path: Path,
        purpose: str,
    ) -> None:
        normal_push = (
            f"git -C {shlex.quote(str(repository_path))} push -u origin "
            f"{shlex.quote(branch_name)}"
        )
        try:
            self.run_shell(
                command=normal_push,
                cwd=repository_path,
                log_path=log_path,
                purpose=purpose,
            )
            return
        except CommandExecutionError as error:
            message = str(error).lower()
            if "non-fast-forward" not in message and "failed to push some refs" not in message:
                raise

        self.append_actor_log(
            log_path,
            "GIT",
            "Detected push divergence. Retrying with --force-with-lease for job branch.",
        )
        self.run_shell(
            command=f"git -C {shlex.quote(str(repository_path))} fetch origin",
            cwd=repository_path,
            log_path=log_path,
            purpose="git fetch before force push",
        )
        self.run_shell(
            command=(
                f"git -C {shlex.quote(str(repository_path))} push --force-with-lease "
                f"-u origin {shlex.quote(branch_name)}"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose=f"{purpose} (force-with-lease)",
        )

    def get_pr_url(
        self,
        job: JobRecord,
        repository_path: Path,
        log_path: Path,
        create_result: Optional[object],
    ) -> Optional[str]:
        if create_result is not None:
            for candidate in re.findall(r"https://\S+", getattr(create_result, "stdout", "")):
                if "/pull/" in candidate:
                    return candidate.strip()

        query_result = self.run_shell(
            command=(
                f"gh pr view --repo {shlex.quote(self.job_execution_repository(job))} "
                f"{shlex.quote(job.branch_name)} --json url --jq .url"
            ),
            cwd=repository_path,
            log_path=log_path,
            purpose="read pull request url",
        )
        url = query_result.stdout.strip()
        return url or None

    def _default_pr_body(self, job: JobRecord) -> str:
        return (
            "## Summary\n"
            "- Automated by AgentHub worker\n"
            "- Generated from deterministic stage pipeline\n\n"
            f"{self.issue_reference_line(job)}\n"
        )
