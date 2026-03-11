"""Tests for semantic embedding provider adapters."""

from __future__ import annotations

import httpx
import json

from app.memory.embedding_provider import (
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
)


def test_hash_embedding_provider_returns_normalized_vectors() -> None:
    provider = HashEmbeddingProvider(vector_size=16)

    result = provider.embed_many(["heartbeat stale detected", "repo search result"])

    assert result.configured is True
    assert result.ok is True
    assert result.provider == "hash"
    assert result.model == "hash-shadow-v1"
    assert len(result.vectors) == 2
    assert len(result.vectors[0]) == 16


def test_openai_embedding_provider_returns_not_configured_without_api_key() -> None:
    provider = OpenAIEmbeddingProvider(api_key="", vector_size=8)

    result = provider.embed_many(["hello"])

    assert result.configured is False
    assert result.ok is False
    assert result.detail == "api_key_missing"


def test_openai_embedding_provider_parses_embedding_response() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]},
                    {"index": 1, "embedding": [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]},
                ]
            },
        )

    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        vector_size=8,
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.openai.com/v1",
            timeout=5.0,
        ),
    )

    result = provider.embed_many(["first text", "second text"])

    assert result.configured is True
    assert result.ok is True
    assert result.provider == "openai"
    assert result.model == "text-embedding-3-small"
    assert result.vectors == [
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1],
    ]
    assert captured[0]["model"] == "text-embedding-3-small"
    assert captured[0]["dimensions"] == 8
