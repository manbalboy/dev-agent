from __future__ import annotations

import json
from pathlib import Path

from app.models import JobRecord, JobStatus, utc_now_iso
from app.template_artifact_runtime import TemplateArtifactRuntime


def _make_job(job_id: str = "job-template-artifacts") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=55,
        issue_title="template artifact runtime",
        issue_url="https://github.com/owner/repo/issues/55",
        status=JobStatus.RUNNING.value,
        stage="plan_with_gemini",
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/issue-55",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=None,
        app_code="demo",
        source_repository="owner/repo",
    )


def _build_runtime(tmp_path: Path, writes: list[Path], logs: list[str]) -> TemplateArtifactRuntime:
    return TemplateArtifactRuntime(
        docs_file=lambda repository_path, name: repository_path / "_docs" / name,
        job_workspace_path=lambda job: tmp_path / "workspace" / job.app_code,
        job_execution_repository=lambda job: job.source_repository or job.repository,
        write_operator_inputs_artifact=lambda job, path: (
            writes.append(path),
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_text(json.dumps({"job_id": job.job_id}, ensure_ascii=False) + "\n", encoding="utf-8"),
            {"job_id": job.job_id},
        )[-1],
        append_actor_log=lambda _log_path, _actor, message: logs.append(message),
    )


def test_build_template_variables_writes_operator_inputs_artifact(tmp_path: Path) -> None:
    writes: list[Path] = []
    logs: list[str] = []
    runtime = _build_runtime(tmp_path, writes, logs)
    job = _make_job()
    paths = {
        "spec": tmp_path / "repo" / "_docs" / "SPEC.md",
        "plan": tmp_path / "repo" / "_docs" / "PLAN.md",
        "review": tmp_path / "repo" / "_docs" / "REVIEW.md",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# stub\n", encoding="utf-8")
    prompt_path = tmp_path / "repo" / "_docs" / "PROMPT.md"

    variables = runtime.build_template_variables(job, paths, prompt_path)

    assert writes
    assert Path(variables["operator_inputs_path"]).exists()
    assert variables["execution_repository"] == "owner/repo"
    assert variables["prompt_file"] == str(prompt_path)


def test_ensure_design_artifacts_writes_fallback_files(tmp_path: Path) -> None:
    writes: list[Path] = []
    logs: list[str] = []
    runtime = _build_runtime(tmp_path, writes, logs)
    repository_path = tmp_path / "repo"
    paths = {}

    runtime.ensure_design_artifacts(repository_path, paths, tmp_path / "job.log")

    tokens_path = repository_path / "_docs" / "DESIGN_TOKENS.json"
    handoff_path = repository_path / "_docs" / "TOKEN_HANDOFF.md"
    assert tokens_path.exists()
    assert handoff_path.exists()
    assert "Fallback DESIGN_TOKENS.json generated." in logs
    payload = json.loads(tokens_path.read_text(encoding="utf-8"))
    assert payload["meta"]["source"] == "fallback"


def test_ensure_documentation_artifacts_writes_missing_root_docs(tmp_path: Path) -> None:
    writes: list[Path] = []
    logs: list[str] = []
    runtime = _build_runtime(tmp_path, writes, logs)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True)

    runtime.ensure_documentation_artifacts(repository_path, {}, tmp_path / "job.log")

    assert (repository_path / "README.md").exists()
    assert (repository_path / "COPYRIGHT.md").exists()
    assert (repository_path / "DEVELOPMENT_GUIDE.md").exists()
    assert (repository_path / "_docs" / "DOCUMENTATION_PLAN.md").exists()
    assert any("Fallback README.md generated." == message for message in logs)
