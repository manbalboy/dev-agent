"""Tests for optional Qdrant shadow transport."""

from __future__ import annotations

import json
import httpx

from app.memory.qdrant_shadow import QdrantShadowTransport


def test_qdrant_shadow_transport_returns_not_configured_without_url() -> None:
    transport = QdrantShadowTransport(base_url="")

    result = transport.sync_manifest({"candidates": [{"point_id": "1"}]})

    assert result.configured is False
    assert result.attempted is False
    assert result.ok is False
    assert result.detail == "not_configured"
    assert result.embedding_provider == "hash"
    assert result.embedding_model == "hash-shadow-v1"


def test_qdrant_shadow_transport_upserts_manifest_when_configured() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/collections/agenthub_memory_shadow":
            return httpx.Response(200, json={"result": True, "status": "ok"})
        if request.url.path == "/collections/agenthub_memory_shadow/points":
            payload = request.read().decode("utf-8")
            assert "\"points\"" in payload
            return httpx.Response(200, json={"result": {"status": "acknowledged"}, "status": "ok"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = QdrantShadowTransport(
        base_url="http://qdrant.test",
        collection="agenthub_memory_shadow",
        vector_size=32,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://qdrant.test",
            timeout=5.0,
        ),
    )

    result = transport.sync_manifest(
        {
            "candidates": [
                {
                    "point_id": "123e4567-e89b-12d3-a456-426614174000",
                    "memory_id": "failure_pattern:stale-heartbeat",
                    "memory_type": "failure_pattern",
                    "state": "promoted",
                    "score": 2.2,
                    "confidence": 0.88,
                    "repository": "owner/repo",
                    "execution_repository": "owner/repo",
                    "app_code": "default",
                    "workflow_id": "wf-default",
                    "source_path": "_docs/FAILURE_PATTERNS.json",
                    "title": "stale heartbeat",
                    "summary": "stale heartbeat detected",
                    "embedding_text": "failure_pattern stale heartbeat detected",
                }
            ]
        }
    )

    assert result.configured is True
    assert result.attempted is True
    assert result.ok is True
    assert result.detail == "upsert_ok"
    assert result.point_count == 1
    assert result.embedding_provider == "hash"
    assert result.embedding_model == "hash-shadow-v1"
    assert requests == [
        ("PUT", "/collections/agenthub_memory_shadow"),
        ("PUT", "/collections/agenthub_memory_shadow/points"),
    ]


def test_qdrant_shadow_transport_reports_upsert_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/agenthub_memory_shadow":
            return httpx.Response(200, json={"result": True, "status": "ok"})
        if request.url.path == "/collections/agenthub_memory_shadow/points":
            return httpx.Response(500, json={"status": "error"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = QdrantShadowTransport(
        base_url="http://qdrant.test",
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://qdrant.test",
            timeout=5.0,
        ),
    )

    result = transport.sync_manifest(
        {
            "candidates": [
                {
                    "point_id": "123e4567-e89b-12d3-a456-426614174000",
                    "embedding_text": "candidate",
                }
            ]
        }
    )

    assert result.configured is True
    assert result.attempted is True
    assert result.ok is False
    assert result.detail == "upsert_failed"
    assert result.upsert_status_code == 500
    assert result.embedding_provider == "hash"


def test_qdrant_shadow_transport_queries_memory_entries() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/collections/agenthub_memory_shadow/points/query":
            payload = json.loads(request.read().decode("utf-8"))
            assert payload["score_threshold"] == 0.2
            assert payload["filter"]["must"][0]["key"] == "execution_repository"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {
                                "id": "123e4567-e89b-12d3-a456-426614174000",
                                "score": 0.91,
                                "payload": {
                                    "memory_id": "failure_pattern:stale-heartbeat",
                                    "memory_type": "failure_pattern",
                                    "state": "promoted",
                                    "score": 2.2,
                                    "confidence": 0.88,
                                    "repository": "owner/repo",
                                    "execution_repository": "owner/repo",
                                    "app_code": "default",
                                    "workflow_id": "wf-default",
                                    "source_path": "_docs/FAILURE_PATTERNS.json",
                                    "title": "stale heartbeat",
                                    "summary": "stale heartbeat detected",
                                },
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = QdrantShadowTransport(
        base_url="http://qdrant.test",
        collection="agenthub_memory_shadow",
        vector_size=32,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://qdrant.test",
            timeout=5.0,
        ),
    )

    result = transport.query_memory_entries(
        query="heartbeat stale",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
        limit=5,
        score_threshold=0.2,
    )

    assert result.configured is True
    assert result.attempted is True
    assert result.ok is True
    assert result.detail == "query_ok"
    assert result.item_count == 1
    assert result.items is not None
    assert result.items[0]["memory_id"] == "failure_pattern:stale-heartbeat"
    assert result.items[0]["vector_score"] == 0.91
    assert result.embedding_provider == "hash"
    assert requests == [("POST", "/collections/agenthub_memory_shadow/points/query")]


def test_qdrant_shadow_transport_reports_query_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/collections/agenthub_memory_shadow/points/query":
            return httpx.Response(500, json={"status": "error"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = QdrantShadowTransport(
        base_url="http://qdrant.test",
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://qdrant.test",
            timeout=5.0,
        ),
    )

    result = transport.query_memory_entries(
        query="heartbeat stale",
        repository="owner/repo",
        execution_repository="owner/repo",
        app_code="default",
        workflow_id="wf-default",
    )

    assert result.configured is True
    assert result.attempted is True
    assert result.ok is False
    assert result.detail == "query_failed"
    assert result.status_code == 500
