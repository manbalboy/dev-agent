"""Tests for implement/coder stage runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.command_runner import CommandResult
from app.implement_runtime import ImplementRuntime
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-implement-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=93,
        issue_title="implement runtime test",
        issue_url="https://github.com/owner/repo/issues/93",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-93-implement-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class _FakeTemplateRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del cwd
        self.calls.append((template_name, dict(variables)))
        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


def test_stage_implement_with_codex_writes_prompt_and_runs_coder(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    docs_path = repository_path / "_docs"
    docs_path.mkdir(parents=True)
    stage_events: list[tuple[str, str, str]] = []
    runner = _FakeTemplateRunner()

    runtime = ImplementRuntime(
        command_templates=runner,
        set_stage=lambda job_id, stage, log_path: stage_events.append((str(log_path), job_id, stage.value)),
        ensure_product_definition_ready=lambda paths, log_path: stage_events.append((str(log_path), "ensure", str(paths["plan"]))),
        write_memory_retrieval_artifacts=lambda **kwargs: stage_events.append((str(kwargs["repository_path"]), "memory", "written")),
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
        build_route_runtime_context=lambda route: f"{route}-context",
        build_template_variables=lambda job, paths, prompt_path: {
            "repository": job.repository,
            "issue_number": str(job.issue_number),
            "issue_title": job.issue_title,
            "issue_url": job.issue_url,
            "branch_name": job.branch_name,
            "work_dir": str(prompt_path.parent.parent),
            "prompt_file": str(prompt_path),
            "plan_path": str(paths.get("plan", "")),
            "review_path": str(paths.get("review", "")),
        },
        actor_log_writer=lambda log_path, actor: (lambda message: stage_events.append((str(log_path), actor, message))),
        template_for_route=lambda route: {"coder": "coder"}.get(route, route),
    )

    paths = {
        "plan": docs_path / "PLAN.md",
        "review": docs_path / "REVIEW.md",
        "design": docs_path / "DESIGN_SYSTEM.md",
    }
    paths["plan"].write_text("# PLAN\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n", encoding="utf-8")

    runtime.stage_implement_with_codex(
        job=_make_job(),
        repository_path=repository_path,
        paths=paths,
        log_path=tmp_path / "job.log",
    )

    prompt_path = docs_path / "CODER_PROMPT_IMPLEMENT.md"
    assert prompt_path.exists()
    assert "PLAN.md 기반 MVP 구현" in prompt_path.read_text(encoding="utf-8")
    assert [call[0] for call in runner.calls] == ["coder"]
