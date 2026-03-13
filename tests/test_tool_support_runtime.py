"""Tests for tool support runtime extraction."""

from __future__ import annotations

from pathlib import Path

from app.tool_support_runtime import ToolSupportRuntime


class _MemoryStoreStub:
    def __init__(self) -> None:
        self.refreshed_at: str | None = None
        self.last_search_kwargs: dict | None = None

    def refresh_rankings(self, *, as_of: str) -> None:
        self.refreshed_at = as_of

    def search_entries(self, **kwargs):
        self.last_search_kwargs = kwargs
        return [{"id": "mem-1", "summary": "지도 SDK fallback"}]


class _VectorResultStub:
    def to_dict(self) -> dict:
        return {"configured": True, "attempted": True, "items": [{"id": "vec-1"}]}


class _TransportStub:
    def __init__(self) -> None:
        self.last_query_kwargs: dict | None = None

    def query_memory_entries(self, **kwargs):
        self.last_query_kwargs = kwargs
        return _VectorResultStub()


def test_tool_support_runtime_search_memory_entries_refreshes_rankings() -> None:
    store = _MemoryStoreStub()
    runtime = ToolSupportRuntime(
        get_memory_runtime_store=lambda: store,
        utc_now_iso=lambda: "2026-03-13T00:00:00+00:00",
        get_qdrant_shadow_transport=lambda: _TransportStub(),
        repo_context_reader=lambda _path: {},
    )

    entries = runtime.search_memory_entries_for_tool(
        query="google maps",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="mobile",
        workflow_id="wf-app",
        limit=5,
    )

    assert store.refreshed_at == "2026-03-13T00:00:00+00:00"
    assert store.last_search_kwargs == {
        "query": "google maps",
        "repository": "owner/repo",
        "execution_repository": "owner/repo",
        "app_code": "mobile",
        "workflow_id": "wf-app",
        "limit": 5,
    }
    assert entries == [{"id": "mem-1", "summary": "지도 SDK fallback"}]


def test_tool_support_runtime_search_vector_memory_entries_uses_threshold() -> None:
    transport = _TransportStub()
    runtime = ToolSupportRuntime(
        get_memory_runtime_store=lambda: _MemoryStoreStub(),
        utc_now_iso=lambda: "2026-03-13T00:00:00+00:00",
        get_qdrant_shadow_transport=lambda: transport,
        repo_context_reader=lambda _path: {},
    )

    result = runtime.search_vector_memory_entries_for_tool(
        query="maps sdk",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="mobile",
        workflow_id="wf-app",
        limit=3,
    )

    assert transport.last_query_kwargs == {
        "query": "maps sdk",
        "repository": "owner/repo",
        "execution_repository": "owner/repo",
        "app_code": "mobile",
        "workflow_id": "wf-app",
        "limit": 3,
        "score_threshold": 0.15,
    }
    assert result["configured"] is True


def test_tool_support_runtime_builds_local_evidence_fallback_from_spec_and_readme(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    spec_path = repository_path / "_docs" / "SPEC.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# SPEC\n\n지도 화면\n" + ("row\n" * 100), encoding="utf-8")

    runtime = ToolSupportRuntime(
        get_memory_runtime_store=lambda: _MemoryStoreStub(),
        utc_now_iso=lambda: "2026-03-13T00:00:00+00:00",
        get_qdrant_shadow_transport=lambda: _TransportStub(),
        repo_context_reader=lambda _path: {
            "readme_excerpt": "README 지도 설명",
            "stack": ["react-native", "expo"],
        },
    )

    payload = runtime.build_local_evidence_fallback(
        repository_path,
        {"spec": spec_path},
        "google maps integration",
        "search api unavailable",
    )

    assert "fallback_local" in payload["context_text"]
    assert "google maps integration" in payload["context_text"]
    assert "react-native, expo" in payload["context_text"]
    assert "README 지도 설명" in payload["context_text"]
    assert "# SPEC" in payload["context_text"]
