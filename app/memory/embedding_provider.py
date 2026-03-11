"""Embedding provider adapters for Phase 4 vector shadow rollout."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Callable, Dict, List

import httpx

from app.memory.vector_shadow import embed_text_hash


@dataclass(frozen=True)
class EmbeddingBatchResult:
    """Result of one embedding batch request."""

    configured: bool
    ok: bool
    provider: str
    model: str
    vector_size: int
    detail: str
    vectors: List[List[float]]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class HashEmbeddingProvider:
    """Deterministic local fallback embedding provider."""

    def __init__(self, *, vector_size: int = 64) -> None:
        self.model = "hash-shadow-v1"
        self.vector_size = max(8, min(int(vector_size or 64), 4096))

    def embed_many(self, texts: List[str]) -> EmbeddingBatchResult:
        vectors = [embed_text_hash(str(text or ""), size=self.vector_size) for text in texts]
        return EmbeddingBatchResult(
            configured=True,
            ok=True,
            provider="hash",
            model=self.model,
            vector_size=self.vector_size,
            detail="ok",
            vectors=vectors,
        )


class OpenAIEmbeddingProvider:
    """Optional OpenAI embeddings adapter for semantic shadow vectors."""

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "text-embedding-3-small",
        vector_size: int = 64,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 10.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or "text-embedding-3-small"
        self.vector_size = max(8, min(int(vector_size or 64), 3072))
        self.base_url = str(base_url or "").strip().rstrip("/") or "https://api.openai.com/v1"
        self.timeout_seconds = max(1.0, float(timeout_seconds or 10.0))
        self._client_factory = client_factory or self._default_client_factory

    def embed_many(self, texts: List[str]) -> EmbeddingBatchResult:
        if not self.api_key:
            return EmbeddingBatchResult(
                configured=False,
                ok=False,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail="api_key_missing",
                vectors=[],
            )
        cleaned = [str(text or "").strip() for text in texts]
        if not cleaned:
            return EmbeddingBatchResult(
                configured=True,
                ok=True,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail="no_inputs",
                vectors=[],
            )

        try:
            with self._client_factory() as client:
                response = client.post(
                    "/embeddings",
                    json={
                        "model": self.model,
                        "input": cleaned,
                        "encoding_format": "float",
                        "dimensions": self.vector_size,
                    },
                )
        except Exception as error:  # noqa: BLE001
            return EmbeddingBatchResult(
                configured=True,
                ok=False,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail=f"request_error: {error}",
                vectors=[],
            )

        if response.status_code < 200 or response.status_code >= 300:
            return EmbeddingBatchResult(
                configured=True,
                ok=False,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail=f"http_{response.status_code}",
                vectors=[],
            )

        try:
            payload = response.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            rows = sorted(
                [item for item in data if isinstance(item, dict)],
                key=lambda item: int(item.get("index", 0) or 0),
            )
            vectors = [
                [float(value) for value in (row.get("embedding", []) or [])][: self.vector_size]
                for row in rows
            ]
        except Exception as error:  # noqa: BLE001
            return EmbeddingBatchResult(
                configured=True,
                ok=False,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail=f"response_parse_error: {error}",
                vectors=[],
            )

        if len(vectors) != len(cleaned):
            return EmbeddingBatchResult(
                configured=True,
                ok=False,
                provider="openai",
                model=self.model,
                vector_size=self.vector_size,
                detail="embedding_count_mismatch",
                vectors=vectors,
            )

        return EmbeddingBatchResult(
            configured=True,
            ok=True,
            provider="openai",
            model=self.model,
            vector_size=self.vector_size,
            detail="ok",
            vectors=vectors,
        )

    def _default_client_factory(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )


def build_embedding_provider_from_env(*, vector_size: int) -> object:
    """Build the configured embedding provider with safe defaults."""

    provider = str(os.getenv("AGENTHUB_VECTOR_EMBEDDING_PROVIDER", "hash") or "hash").strip().lower()
    if provider == "openai":
        return OpenAIEmbeddingProvider(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("AGENTHUB_VECTOR_EMBEDDING_MODEL", "text-embedding-3-small"),
            vector_size=vector_size,
            base_url=os.getenv("AGENTHUB_VECTOR_EMBEDDING_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(os.getenv("AGENTHUB_VECTOR_EMBEDDING_TIMEOUT_SECONDS", "10") or 10),
        )
    return HashEmbeddingProvider(vector_size=vector_size)
