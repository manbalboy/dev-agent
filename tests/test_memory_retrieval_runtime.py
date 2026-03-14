"""Tests for memory retrieval runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.memory_retrieval_runtime import MemoryRetrievalRuntime
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-memory-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=71,
        issue_title="메모리 retrieval 품질 정리",
        issue_url="https://github.com/owner/repo/issues/71",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-71-memory-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
    )


class _TransportResult:
    configured = False
    attempted = False
    ok = False
    detail = ""

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "attempted": self.attempted,
            "ok": self.ok,
            "detail": self.detail,
        }


class _QueryResult:
    def __init__(self, *, items: list[dict] | None = None, detail: str = "query_ok") -> None:
        self.configured = True
        self.attempted = True
        self.ok = bool(items)
        self.detail = detail if items else "no_results"
        self.items = items or []
        self.item_count = len(self.items)

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "attempted": self.attempted,
            "ok": self.ok,
            "detail": self.detail,
            "item_count": self.item_count,
            "items": list(self.items),
        }


class _TransportStub:
    def __init__(self, *, query_items: list[dict] | None = None) -> None:
        self.query_items = query_items or []
        self.last_query_kwargs: dict | None = None

    def sync_manifest(self, _manifest: dict) -> _TransportResult:
        return _TransportResult()

    def query_memory_entries(self, **kwargs) -> _QueryResult:
        self.last_query_kwargs = kwargs
        return _QueryResult(items=self.query_items)


class _MemoryStoreStub:
    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries
        self.refreshed_at: str | None = None

    def refresh_rankings(self, *, as_of: str) -> None:
        self.refreshed_at = as_of

    def query_entries_for_retrieval(self, **_kwargs):
        return list(self.entries)


def _build_runtime(
    read_payloads: dict[Path, dict] | None = None,
    *,
    feature_flags: dict[str, bool] | None = None,
    runtime_entries: list[dict] | None = None,
    query_items: list[dict] | None = None,
) -> MemoryRetrievalRuntime:
    payloads = read_payloads or {}
    flags = feature_flags or {}
    store = _MemoryStoreStub(runtime_entries or [])
    transport = _TransportStub(query_items=query_items)

    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json_artifact(path: Path | None, payload: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json_file(path: Path | None) -> dict:
        if path is None:
            return {}
        if path in payloads:
            return payloads[path]
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    return MemoryRetrievalRuntime(
        feature_enabled=lambda flag: bool(flags.get(flag, False)),
        docs_file=docs_file,
        write_json_artifact=write_json_artifact,
        job_execution_repository=lambda job: job.source_repository or job.repository,
        get_memory_runtime_store=lambda: store,
        read_json_file=read_json_file,
        append_actor_log=lambda *_args, **_kwargs: None,
        get_qdrant_shadow_transport=lambda: transport,
    )


def test_memory_retrieval_runtime_reads_json_history_entries(tmp_path: Path) -> None:
    history_path = tmp_path / "DECISION_HISTORY.json"
    runtime = _build_runtime(
        {
            history_path: {
                "entries": [
                    {"decision_id": "d-1", "chosen_strategy": "stabilization"},
                    "skip-me",
                    {"decision_id": "d-2", "chosen_strategy": "test_hardening"},
                ]
            }
        }
    )

    entries = runtime.read_json_history_entries(history_path)

    assert entries == [
        {"decision_id": "d-1", "chosen_strategy": "stabilization"},
        {"decision_id": "d-2", "chosen_strategy": "test_hardening"},
    ]


def test_memory_retrieval_runtime_strategy_shadow_payload_diverges_with_memory_signal(tmp_path: Path) -> None:
    runtime = _build_runtime()
    job = _make_job()

    payload = runtime.build_strategy_shadow_report_payload(
        job=job,
        context_payload={
            "planner_context": [
                {
                    "id": "fp-1",
                    "kind": "failure_pattern",
                    "summary": "test gaps keep recurring",
                    "category": "test_coverage",
                },
                {
                    "id": "fp-2",
                    "kind": "failure_pattern",
                    "summary": "score_stagnation on tests",
                    "category": "test_coverage",
                },
            ],
            "reviewer_context": [],
            "coder_context": [],
        },
        rankings_map={
            "fp-1": {"state": "active", "score": 7.0, "confidence": 0.9, "usage_count": 3},
            "fp-2": {"state": "active", "score": 6.5, "confidence": 0.9, "usage_count": 2},
        },
        strategy_inputs={
            "maturity_level": "usable",
            "maturity_progression": "stable",
            "quality_trend_direction": "stable",
            "quality_gate_passed": False,
            "persistent_low_categories": ["test_coverage"],
            "stagnant_categories": [],
        },
        selected_strategy="feature_expansion",
        selected_focus="feature",
    )

    assert payload["enabled"] is True
    assert payload["selected_strategy"] == "feature_expansion"
    assert payload["shadow_strategy"] == "test_hardening"
    assert payload["diverged"] is True
    assert payload["decision_mode"] == "memory_divergence"
    assert payload["repository"] == "owner/repo"


def test_memory_retrieval_runtime_vector_candidates_are_injected_into_route_context(tmp_path: Path) -> None:
    runtime_entries = [
        {
            "memory_id": "decision-1",
            "memory_type": "decision",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "",
            "score": 3.2,
            "confidence": 0.88,
            "usage_count": 2,
            "state": "active",
            "payload": {
                "decision_id": "decision-1",
                "chosen_strategy": "test_hardening",
                "strategy_focus": "testing",
                "change_reasons": ["recurring test regression"],
                "selected_task_titles": ["테스트 추가"],
            },
        },
        {
            "memory_id": "conv-1",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "default",
            "workflow_id": "",
            "score": 2.1,
            "confidence": 0.8,
            "usage_count": 1,
            "state": "active",
            "payload": {
                "id": "conv-1",
                "type": "test_pattern",
                "rule": "pytest regression coverage required",
                "confidence": 0.8,
                "evidence_paths": ["tests/"],
            },
        },
    ]
    runtime = _build_runtime(
        feature_flags={
            "memory_retrieval": True,
            "vector_memory_retrieval": True,
        },
        runtime_entries=runtime_entries,
        query_items=[
            {
                "memory_id": "decision-1",
                "vector_score": 0.92,
            }
        ],
    )
    job = _make_job("job-vector-context")
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = {
        "memory_selection": repository_path / "_docs" / "MEMORY_SELECTION.json",
        "memory_context": repository_path / "_docs" / "MEMORY_CONTEXT.json",
        "memory_trace": repository_path / "_docs" / "MEMORY_TRACE.json",
        "vector_shadow_index": repository_path / "_docs" / "VECTOR_SHADOW_INDEX.json",
    }

    runtime.write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    context_payload = json.loads(paths["memory_context"].read_text(encoding="utf-8"))
    trace_payload = json.loads(paths["memory_trace"].read_text(encoding="utf-8"))
    selection_payload = json.loads(paths["memory_selection"].read_text(encoding="utf-8"))

    assert context_payload["planner_context"][0]["id"] == "decision-1"
    assert context_payload["planner_context"][0]["retrieval_source"] == "vector"
    assert context_payload["planner_context"][0]["vector_score"] == 0.92
    assert trace_payload["routes"]["planner"]["source_counts"]["vector"] >= 1
    assert trace_payload["routes"]["planner"]["vector_selected_count"] >= 1
    assert trace_payload["vector_routes"]["planner"]["used_in_context"] is True
    assert "decision-1" in selection_payload["vector_routes"]["planner"]["selected_ids"]
