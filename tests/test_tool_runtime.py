"""Tests for tool runtime normalization."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.memory.runtime_store import MemoryRuntimeStore
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


def _build_runtime(
    tmp_path: Path,
    *,
    should_fail: bool = False,
    feature_flags: dict[str, bool] | None = None,
    shadow_result=None,
    memory_search_entries=None,
    vector_memory_search_entries=None,
):
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

    class FakeMCPToolClient:
        def call_tool_shadow(self, *, tool_name: str, arguments: dict[str, str]):
            if shadow_result is None:
                raise AssertionError("shadow_result must be provided when shadow is enabled")
            return shadow_result

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
        search_memory_entries=memory_search_entries,
        search_vector_memory_entries=vector_memory_search_entries,
        feature_enabled=lambda flag_name: bool((feature_flags or {}).get(flag_name, False)),
        mcp_tool_client=FakeMCPToolClient(),
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


def test_tool_runtime_records_mcp_shadow_trace_when_enabled(tmp_path: Path) -> None:
    from app.mcp_tool_client import MCPToolCallResult

    runtime, logs = _build_runtime(
        tmp_path,
        should_fail=False,
        feature_flags={"mcp_tools_shadow": True},
        shadow_result=MCPToolCallResult(
            enabled=True,
            available=False,
            ok=False,
            tool="research_search",
            server_command="python -m fake_mcp_server",
            detail="mcp_sdk_not_installed",
        ),
    )
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
    trace_path = repository_path / "_docs" / "MCP_TOOL_SHADOW.jsonl"
    assert trace_path.exists()
    payload = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["tool"] == "research_search"
    assert payload["primary_result"]["mode"] == "search_api"
    assert payload["shadow_result"]["detail"] == "mcp_sdk_not_installed"
    assert any("MCP shadow recorded for tool=research_search" in message for _, message in logs)


def test_tool_runtime_executes_log_lookup_against_debug_and_user_logs(tmp_path: Path) -> None:
    runtime, logs = _build_runtime(tmp_path, should_fail=False)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)

    logs_dir = tmp_path / "logs"
    debug_log_path = logs_dir / "debug" / "job-tool-runtime.log"
    user_log_path = logs_dir / "user" / "job-tool-runtime.log"
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    user_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text(
        "[2026-03-12T10:00:00Z] [CODER] implement started\n"
        "[2026-03-12T10:02:00Z] [ORCHESTRATOR] running heartbeat stale detected after 1803s\n",
        encoding="utf-8",
    )
    user_log_path.write_text(
        "[2026-03-12T10:03:00Z] [TESTER] retry scheduled after stale recovery\n",
        encoding="utf-8",
    )

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=debug_log_path,
        request=ToolRequest(tool="log_lookup", query="heartbeat stale", reason="find failure evidence"),
    )

    assert result.ok is True
    assert result.mode == "log_lookup"
    assert (repository_path / "_docs" / "LOG_LOOKUP_CONTEXT.md").exists()
    assert (repository_path / "_docs" / "LOG_LOOKUP_RESULT.json").exists()
    payload = json.loads((repository_path / "_docs" / "LOG_LOOKUP_RESULT.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["query"] == "heartbeat stale"
    assert payload["match_count"] >= 1
    assert any("heartbeat" in row["excerpt"].lower() for row in payload["matches"])
    assert any("log_lookup captured" in message for _, message in logs)


def test_tool_runtime_log_lookup_falls_back_to_recent_log_excerpt_when_no_match(tmp_path: Path) -> None:
    runtime, _logs = _build_runtime(tmp_path, should_fail=False)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)

    logs_dir = tmp_path / "logs"
    debug_log_path = logs_dir / "debug" / "job-tool-runtime.log"
    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    debug_log_path.write_text(
        "[2026-03-12T10:00:00Z] [CODER] implement started\n"
        "[2026-03-12T10:01:00Z] [TESTER] all smoke checks passed\n",
        encoding="utf-8",
    )

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=debug_log_path,
        request=ToolRequest(tool="log_lookup", query="totally-missing-keyword", reason="inspect latest context"),
    )

    assert result.ok is False
    assert result.mode == "log_lookup"
    payload = json.loads((repository_path / "_docs" / "LOG_LOOKUP_RESULT.json").read_text(encoding="utf-8"))
    assert payload["match_count"] == 1
    assert payload["matches"][0]["matched_keywords"] == []
    assert "all smoke checks passed" in payload["matches"][0]["excerpt"].lower()


def test_tool_runtime_executes_repo_search_for_paths_and_content(tmp_path: Path) -> None:
    runtime, logs = _build_runtime(tmp_path, should_fail=False)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "app").mkdir(parents=True, exist_ok=True)
    (repository_path / "docs").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "worker_runtime.py").write_text(
        "def refresh_heartbeat():\n    return 'heartbeat ok'\n",
        encoding="utf-8",
    )
    (repository_path / "docs" / "operations.md").write_text(
        "Use worker_runtime refresh_heartbeat when stale recovery is detected.\n",
        encoding="utf-8",
    )

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(tool="repo_search", query="worker_runtime heartbeat", reason="find implementation path"),
    )

    assert result.ok is True
    assert result.mode == "repo_search"
    assert (repository_path / "_docs" / "REPO_SEARCH_CONTEXT.md").exists()
    assert (repository_path / "_docs" / "REPO_SEARCH_RESULT.json").exists()
    payload = json.loads((repository_path / "_docs" / "REPO_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["query"] == "worker_runtime heartbeat"
    assert payload["match_count"] >= 2
    assert any(row["kind"] == "path" and row["path"] == "app/worker_runtime.py" for row in payload["matches"])
    assert any(row["kind"] == "content" and "heartbeat" in row["excerpt"].lower() for row in payload["matches"])
    assert any("repo_search captured" in message for _, message in logs)


def test_tool_runtime_repo_search_returns_empty_match_set_when_no_match(tmp_path: Path) -> None:
    runtime, _logs = _build_runtime(tmp_path, should_fail=False)
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "app").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "main.py").write_text("print('hello')\n", encoding="utf-8")

    result = runtime.execute(
        job=_make_job(),
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(tool="repo_search", query="totally-missing-symbol", reason="find source"),
    )

    assert result.ok is False
    assert result.mode == "repo_search"
    payload = json.loads((repository_path / "_docs" / "REPO_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["match_count"] == 0
    assert payload["matches"] == []


def test_tool_runtime_executes_memory_search_against_runtime_store(tmp_path: Path) -> None:
    runtime_store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:stale-heartbeat",
            "memory_type": "failure_pattern",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "job_id": "job-memory-runtime",
            "title": "stale heartbeat during long codex step",
            "summary": "heartbeat stale detected after implement_with_codex during long-running command",
            "score": 2.4,
            "confidence": 0.86,
            "baseline_score": 2.4,
            "baseline_confidence": 0.86,
            "state": "promoted",
            "source_path": "_docs/FAILURE_PATTERNS.json",
            "updated_at": "2026-03-12T00:00:00+00:00",
        }
    )
    runtime_store.refresh_rankings(as_of="2026-03-12T01:00:00+00:00")
    runtime, logs = _build_runtime(
        tmp_path,
        should_fail=False,
        memory_search_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    job = _make_job("job-memory-runtime")
    job.workflow_id = "wf-default"

    result = runtime.execute(
        job=job,
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(
            tool="memory_search",
            query="heartbeat stale",
            reason="find prior failure pattern",
        ),
    )

    assert result.ok is True
    assert result.mode == "memory_search"
    payload = json.loads((repository_path / "_docs" / "MEMORY_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["match_count"] == 1
    assert payload["items"][0]["memory_id"] == "failure_pattern:stale-heartbeat"
    assert any("memory_search captured" in message for _, message in logs)


def test_tool_runtime_memory_search_returns_empty_when_scope_has_no_match(tmp_path: Path) -> None:
    runtime_store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "conv:frontend-theme",
            "memory_type": "convention",
            "repository": "other/repo",
            "execution_repository": "other/repo",
            "app_code": "other",
            "workflow_id": "wf-other",
            "job_id": "job-memory-runtime",
            "title": "theme convention",
            "summary": "uses sand background and serif headings",
            "score": 1.2,
            "confidence": 0.61,
            "baseline_score": 1.2,
            "baseline_confidence": 0.61,
            "state": "active",
            "source_path": "_docs/CONVENTIONS.json",
            "updated_at": "2026-03-12T00:00:00+00:00",
        }
    )
    runtime, _logs = _build_runtime(
        tmp_path,
        should_fail=False,
        memory_search_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)

    result = runtime.execute(
        job=_make_job("job-memory-runtime-empty"),
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(tool="memory_search", query="theme convention", reason="find matching repo memory"),
    )

    assert result.ok is False
    assert result.mode == "memory_search"
    payload = json.loads((repository_path / "_docs" / "MEMORY_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["match_count"] == 0
    assert payload["items"] == []


def test_tool_runtime_prefers_vector_memory_search_when_enabled(tmp_path: Path) -> None:
    runtime_store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:db-fallback",
            "memory_type": "failure_pattern",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "job_id": "job-memory-vector",
            "title": "db stale heartbeat",
            "summary": "db-only stale heartbeat entry",
            "score": 1.0,
            "confidence": 0.6,
            "baseline_score": 1.0,
            "baseline_confidence": 0.6,
            "state": "active",
            "source_path": "_docs/FAILURE_PATTERNS.json",
            "updated_at": "2026-03-12T00:00:00+00:00",
        }
    )
    runtime, logs = _build_runtime(
        tmp_path,
        should_fail=False,
        feature_flags={"vector_memory_retrieval": True},
        memory_search_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
        vector_memory_search_entries=lambda **_kwargs: {
            "configured": True,
            "attempted": True,
            "ok": True,
            "detail": "query_ok",
            "items": [
                {
                    "memory_id": "failure_pattern:vector-hit",
                    "memory_type": "failure_pattern",
                    "state": "promoted",
                    "score": 2.9,
                    "confidence": 0.91,
                    "source_path": "_docs/FAILURE_PATTERNS.json",
                    "title": "vector stale heartbeat",
                    "summary": "vector-backed stale heartbeat match",
                    "vector_score": 0.88,
                }
            ],
        },
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    job = _make_job("job-memory-vector")
    job.workflow_id = "wf-default"

    result = runtime.execute(
        job=job,
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(
            tool="memory_search",
            query="db fallback stale heartbeat",
            reason="find prior failure pattern",
        ),
    )

    assert result.ok is True
    payload = json.loads((repository_path / "_docs" / "MEMORY_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["source"] == "vector"
    assert payload["fallback_used"] is False
    assert payload["vector"]["detail"] == "query_ok"
    assert payload["items"][0]["memory_id"] == "failure_pattern:vector-hit"
    assert any("source=vector" in message for _, message in logs)


def test_tool_runtime_falls_back_to_db_when_vector_memory_search_returns_no_results(tmp_path: Path) -> None:
    runtime_store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:db-hit",
            "memory_type": "failure_pattern",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "wf-default",
            "job_id": "job-memory-vector-fallback",
            "title": "db stale heartbeat",
            "summary": "db fallback stale heartbeat entry",
            "score": 2.4,
            "confidence": 0.86,
            "baseline_score": 2.4,
            "baseline_confidence": 0.86,
            "state": "promoted",
            "source_path": "_docs/FAILURE_PATTERNS.json",
            "updated_at": "2026-03-12T00:00:00+00:00",
        }
    )
    runtime, _logs = _build_runtime(
        tmp_path,
        should_fail=False,
        feature_flags={"vector_memory_retrieval": True},
        memory_search_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
        vector_memory_search_entries=lambda **_kwargs: {
            "configured": True,
            "attempted": True,
            "ok": False,
            "detail": "no_results",
            "items": [],
        },
    )
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    job = _make_job("job-memory-vector-fallback")
    job.workflow_id = "wf-default"

    result = runtime.execute(
        job=job,
        repository_path=repository_path,
        paths={},
        log_path=tmp_path / "job.log",
        request=ToolRequest(
            tool="memory_search",
            query="db fallback stale heartbeat",
            reason="find prior failure pattern",
        ),
    )

    assert result.ok is True
    payload = json.loads((repository_path / "_docs" / "MEMORY_SEARCH_RESULT.json").read_text(encoding="utf-8"))
    assert payload["source"] == "db"
    assert payload["fallback_used"] is True
    assert payload["vector"]["detail"] == "no_results"
    assert payload["items"][0]["memory_id"] == "failure_pattern:db-hit"
