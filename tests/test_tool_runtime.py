"""Tests for tool runtime normalization."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.tool_runtime import ToolRequest, ToolRuntime


def _make_job(job_id: str = "job-tool-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=55,
        issue_title="tool runtime test",
        issue_url="https://github.com/owner/repo/issues/55",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-55-tool-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(tmp_path: Path, *, should_fail: bool = False):
    logs: list[tuple[str, str]] = []

    class FakeTemplateRunner:
        def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
            if should_fail:
                raise RuntimeError("search api unavailable")
            (cwd / "SEARCH_CONTEXT.md").write_text("# SEARCH CONTEXT\n\n- result\n", encoding="utf-8")
            (cwd / "SEARCH_RESULT.json").write_text(
                json.dumps({"ok": True, "items": [{"title": "doc"}]}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log_writer(f"[FAKE_TEMPLATE] {template_name}")
            return CommandResult(
                command=f"fake {template_name}",
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

    def docs_file(base_path: Path, name: str) -> Path:
        target = base_path / "_docs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    runtime = ToolRuntime(
        command_templates=FakeTemplateRunner(),
        docs_file=docs_file,
        build_template_variables=lambda job, paths, prompt_path: {"prompt_path": str(prompt_path)},
        template_for_route=lambda route_name: route_name,
        actor_log_writer=lambda log_path, actor: lambda message: logs.append((actor, message)),
        append_actor_log=lambda log_path, actor, message: logs.append((actor, message)),
        build_local_evidence_fallback=lambda repository_path, paths, query, error_text: {
            "context_text": (
                "# SEARCH CONTEXT (Fallback Local Evidence)\n\n"
                f"- query: {query}\n"
                f"- reason: {error_text}\n"
            )
        },
    )
    return runtime, logs


def test_tool_runtime_parses_planner_tool_request_block() -> None:
    request = ToolRuntime.parse_planner_tool_request(
        """
        [TOOL_REQUEST]
        tool: research_search
        query: latest vite preview behavior
        reason: need external docs
        [/TOOL_REQUEST]
        """
    )

    assert request == ToolRequest(
        tool="research_search",
        query="latest vite preview behavior",
        reason="need external docs",
    )
    assert ToolRuntime.parse_planner_tool_request("tool: repo_search\nquery: foo") is None


def test_tool_runtime_executes_research_search_and_copies_legacy_outputs(tmp_path: Path) -> None:
    runtime, logs = _build_runtime(tmp_path, should_fail=False)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(tool="research_search", query="agent memory", reason="need evidence"),
    )

    assert result.ok is True
    assert result.mode == "search_api"
    assert (repository_path / "_docs" / "SEARCH_CONTEXT.md").exists()
    assert (repository_path / "_docs" / "SEARCH_RESULT.json").exists()
    assert any(actor == "PLANNER" for actor, _ in logs)


def test_tool_runtime_falls_back_to_local_context_when_search_fails(tmp_path: Path) -> None:
    runtime, logs = _build_runtime(tmp_path, should_fail=True)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(tool="research_search", query="agent memory", reason="need evidence"),
    )

    assert result.ok is False
    assert result.mode == "fallback_local"
    assert "search api unavailable" in result.error
    assert (repository_path / "_docs" / "SEARCH_CONTEXT.md").exists()
    assert (repository_path / "_docs" / "SEARCH_RESULT.json").exists()
    assert any("Fallback to local evidence pack" in message for _, message in logs)
