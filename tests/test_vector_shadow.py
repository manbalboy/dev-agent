"""Tests for Phase 4 vector shadow manifest generation."""

from __future__ import annotations

from app.memory.vector_shadow import build_vector_shadow_manifest


def test_build_vector_shadow_manifest_projects_memory_entries() -> None:
    manifest = build_vector_shadow_manifest(
        entries=[
            {
                "memory_id": "failure_pattern:stale-heartbeat",
                "memory_type": "failure_pattern",
                "state": "promoted",
                "score": 2.8,
                "confidence": 0.91,
                "title": "stale heartbeat during codex run",
                "summary": "heartbeat stale detected during implement_with_codex",
                "source_path": "_docs/FAILURE_PATTERNS.json",
                "payload": {
                    "failure_signature": "running heartbeat stale detected",
                    "chosen_strategy": "test_hardening",
                },
            }
        ],
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
    )

    assert manifest["provider"] == "qdrant"
    assert manifest["mode"] == "shadow_manifest_only"
    assert manifest["candidate_count"] == 1
    candidate = manifest["candidates"][0]
    assert candidate["memory_id"] == "failure_pattern:stale-heartbeat"
    assert candidate["repository"] == "owner/repo"
    assert candidate["workflow_id"] == "wf-default"
    assert "implement_with_codex" in candidate["embedding_text"]


def test_build_vector_shadow_manifest_skips_entries_without_identity() -> None:
    manifest = build_vector_shadow_manifest(
        entries=[{"title": "missing id", "summary": "should skip"}],
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
    )

    assert manifest["candidate_count"] == 0
    assert manifest["candidates"] == []
