"""Optional Qdrant shadow transport for Phase 4 vector indexing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Callable, Dict, List

import httpx

from app.memory.embedding_provider import (
    EmbeddingBatchResult,
    build_embedding_provider_from_env,
)


@dataclass(frozen=True)
class QdrantShadowTransportResult:
    """Result of one optional Qdrant shadow sync."""

    configured: bool
    attempted: bool
    ok: bool
    detail: str
    collection: str
    point_count: int
    vector_size: int
    embedding_provider: str = ""
    embedding_model: str = ""
    collection_status_code: int = 0
    upsert_status_code: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QdrantMemorySearchResult:
    """Result of one optional vector-backed memory search."""

    configured: bool
    attempted: bool
    ok: bool
    detail: str
    collection: str
    item_count: int
    limit: int
    score_threshold: float
    embedding_provider: str = ""
    embedding_model: str = ""
    status_code: int = 0
    items: List[Dict[str, Any]] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QdrantShadowTransport:
    """Best-effort shadow transport that never affects primary retrieval."""

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        collection: str = "",
        vector_size: int = 64,
        timeout_seconds: float = 5.0,
        embedding_provider=None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.collection = str(collection or "").strip() or "agenthub_memory_shadow"
        self.vector_size = max(8, min(int(vector_size or 64), 4096))
        self.timeout_seconds = max(1.0, float(timeout_seconds or 5.0))
        self.embedding_provider = embedding_provider or build_embedding_provider_from_env(vector_size=self.vector_size)
        self._client_factory = client_factory or self._default_client_factory

    @classmethod
    def from_env(cls) -> "QdrantShadowTransport":
        """Build shadow transport from environment variables."""

        return cls(
            base_url=os.getenv("AGENTHUB_QDRANT_SHADOW_URL", ""),
            api_key=os.getenv("AGENTHUB_QDRANT_SHADOW_API_KEY", ""),
            collection=os.getenv("AGENTHUB_QDRANT_SHADOW_COLLECTION", ""),
            vector_size=int(os.getenv("AGENTHUB_QDRANT_SHADOW_VECTOR_SIZE", "64") or 64),
            timeout_seconds=float(os.getenv("AGENTHUB_QDRANT_SHADOW_TIMEOUT_SECONDS", "5") or 5),
        )

    def sync_manifest(self, manifest: Dict[str, Any]) -> QdrantShadowTransportResult:
        """Sync one vector shadow manifest to Qdrant when configured."""

        candidates = manifest.get("candidates", []) if isinstance(manifest, dict) else []
        point_count = len(candidates) if isinstance(candidates, list) else 0
        if not self.base_url:
            return QdrantShadowTransportResult(
                configured=False,
                attempted=False,
                ok=False,
                detail="not_configured",
                collection=self.collection,
                point_count=point_count,
                vector_size=self.vector_size,
                embedding_provider=self._embedding_provider_name(),
                embedding_model=self._embedding_provider_model(),
            )
        if point_count == 0:
            return QdrantShadowTransportResult(
                configured=True,
                attempted=False,
                ok=True,
                detail="no_candidates",
                collection=self.collection,
                point_count=0,
                vector_size=self.vector_size,
                embedding_provider=self._embedding_provider_name(),
                embedding_model=self._embedding_provider_model(),
            )

        embedding_result = self.embedding_provider.embed_many(
            [str(item.get("embedding_text", "")).strip() for item in candidates if isinstance(item, dict)]
        )
        if not embedding_result.configured:
            return QdrantShadowTransportResult(
                configured=True,
                attempted=False,
                ok=False,
                detail=f"embedding_not_configured:{embedding_result.detail}",
                collection=self.collection,
                point_count=point_count,
                vector_size=self.vector_size,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
            )
        if not embedding_result.ok:
            return QdrantShadowTransportResult(
                configured=True,
                attempted=False,
                ok=False,
                detail=f"embedding_failed:{embedding_result.detail}",
                collection=self.collection,
                point_count=point_count,
                vector_size=self.vector_size,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
            )

        try:
            with self._client_factory() as client:
                collection_response = client.put(
                    f"/collections/{self.collection}",
                    json={"vectors": {"size": self.vector_size, "distance": "Cosine"}},
                )
                collection_ok = 200 <= collection_response.status_code < 300 or collection_response.status_code == 409
                if not collection_ok:
                    return QdrantShadowTransportResult(
                        configured=True,
                        attempted=True,
                        ok=False,
                        detail="collection_create_failed",
                        collection=self.collection,
                        point_count=point_count,
                        vector_size=self.vector_size,
                        embedding_provider=embedding_result.provider,
                        embedding_model=embedding_result.model,
                        collection_status_code=collection_response.status_code,
                    )

                points = self._points_from_candidates(candidates, embedding_result)
                upsert_response = client.put(
                    f"/collections/{self.collection}/points",
                    params={"wait": "true"},
                    json={"points": points},
                )
                if 200 <= upsert_response.status_code < 300:
                    return QdrantShadowTransportResult(
                        configured=True,
                        attempted=True,
                        ok=True,
                        detail="upsert_ok",
                        collection=self.collection,
                        point_count=len(points),
                        vector_size=self.vector_size,
                        embedding_provider=embedding_result.provider,
                        embedding_model=embedding_result.model,
                        collection_status_code=collection_response.status_code,
                        upsert_status_code=upsert_response.status_code,
                    )
                return QdrantShadowTransportResult(
                    configured=True,
                    attempted=True,
                    ok=False,
                    detail="upsert_failed",
                    collection=self.collection,
                    point_count=len(points),
                    vector_size=self.vector_size,
                    embedding_provider=embedding_result.provider,
                    embedding_model=embedding_result.model,
                    collection_status_code=collection_response.status_code,
                    upsert_status_code=upsert_response.status_code,
                )
        except Exception as error:  # noqa: BLE001
            return QdrantShadowTransportResult(
                configured=True,
                attempted=True,
                ok=False,
                detail=f"transport_error: {error}",
                collection=self.collection,
                point_count=point_count,
                vector_size=self.vector_size,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
            )

    def query_memory_entries(
        self,
        *,
        query: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int = 8,
        score_threshold: float = 0.15,
    ) -> QdrantMemorySearchResult:
        """Query vector shadow collection for scoped memory entries."""

        normalized_query = str(query or "").strip()
        normalized_limit = max(1, min(int(limit or 8), 25))
        normalized_threshold = max(0.0, min(float(score_threshold or 0.15), 1.0))
        if not self.base_url:
            return QdrantMemorySearchResult(
                configured=False,
                attempted=False,
                ok=False,
                detail="not_configured",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=self._embedding_provider_name(),
                embedding_model=self._embedding_provider_model(),
                items=[],
            )
        if not normalized_query:
            return QdrantMemorySearchResult(
                configured=True,
                attempted=False,
                ok=False,
                detail="empty_query",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=self._embedding_provider_name(),
                embedding_model=self._embedding_provider_model(),
                items=[],
            )

        embedding_result = self.embedding_provider.embed_many([normalized_query])
        if not embedding_result.configured:
            return QdrantMemorySearchResult(
                configured=True,
                attempted=False,
                ok=False,
                detail=f"embedding_not_configured:{embedding_result.detail}",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
                items=[],
            )
        if not embedding_result.ok or not embedding_result.vectors:
            return QdrantMemorySearchResult(
                configured=True,
                attempted=False,
                ok=False,
                detail=f"embedding_failed:{embedding_result.detail}",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
                items=[],
            )

        payload: Dict[str, Any] = {
            "query": embedding_result.vectors[0],
            "limit": normalized_limit,
            "score_threshold": normalized_threshold,
            "with_payload": True,
            "with_vector": False,
        }
        filter_payload = self._memory_search_filter(
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
        )
        if filter_payload:
            payload["filter"] = filter_payload

        try:
            with self._client_factory() as client:
                response = client.post(
                    f"/collections/{self.collection}/points/query",
                    json=payload,
                )
        except Exception as error:  # noqa: BLE001
            return QdrantMemorySearchResult(
                configured=True,
                attempted=True,
                ok=False,
                detail=f"transport_error: {error}",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
                items=[],
            )

        if response.status_code < 200 or response.status_code >= 300:
            return QdrantMemorySearchResult(
                configured=True,
                attempted=True,
                ok=False,
                detail="query_failed",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
                status_code=response.status_code,
                items=[],
            )

        try:
            response_payload = response.json()
            items = self._memory_items_from_query_payload(response_payload)
        except Exception as error:  # noqa: BLE001
            return QdrantMemorySearchResult(
                configured=True,
                attempted=True,
                ok=False,
                detail=f"response_parse_error: {error}",
                collection=self.collection,
                item_count=0,
                limit=normalized_limit,
                score_threshold=normalized_threshold,
                embedding_provider=embedding_result.provider,
                embedding_model=embedding_result.model,
                status_code=response.status_code,
                items=[],
            )

        return QdrantMemorySearchResult(
            configured=True,
            attempted=True,
            ok=bool(items),
            detail="query_ok" if items else "no_results",
            collection=self.collection,
            item_count=len(items),
            limit=normalized_limit,
            score_threshold=normalized_threshold,
            embedding_provider=embedding_result.provider,
            embedding_model=embedding_result.model,
            status_code=response.status_code,
            items=items,
        )

    def _default_client_factory(self) -> httpx.Client:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["api-key"] = self.api_key
        return httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_seconds,
        )

    def _points_from_candidates(
        self,
        candidates: List[Dict[str, Any]],
        embedding_result: EmbeddingBatchResult,
    ) -> List[Dict[str, Any]]:
        points: List[Dict[str, Any]] = []
        filtered_candidates = [item for item in candidates if isinstance(item, dict)]
        for candidate, vector in zip(filtered_candidates, embedding_result.vectors):
            payload = {
                key: value
                for key, value in candidate.items()
                if key not in {"embedding_text"}
            }
            points.append(
                {
                    "id": str(candidate.get("point_id", "")).strip(),
                    "vector": vector,
                    "payload": payload,
                }
            )
        return points

    @staticmethod
    def _memory_search_filter(
        *,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
    ) -> Dict[str, Any]:
        must: List[Dict[str, Any]] = []
        normalized_execution_repository = str(execution_repository or "").strip()
        normalized_repository = str(repository or "").strip()
        normalized_app_code = str(app_code or "").strip()
        normalized_workflow_id = str(workflow_id or "").strip()
        if normalized_execution_repository:
            must.append(
                {"key": "execution_repository", "match": {"value": normalized_execution_repository}}
            )
        elif normalized_repository:
            must.append({"key": "repository", "match": {"value": normalized_repository}})
        if normalized_repository:
            must.append({"key": "repository", "match": {"value": normalized_repository}})
        if normalized_app_code:
            must.append({"key": "app_code", "match": {"value": normalized_app_code}})
        if normalized_workflow_id:
            must.append({"key": "workflow_id", "match": {"value": normalized_workflow_id}})
        return {"must": must} if must else {}

    @staticmethod
    def _memory_items_from_query_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        result = payload.get("result", []) if isinstance(payload, dict) else []
        points = result.get("points", []) if isinstance(result, dict) else result
        if not isinstance(points, list):
            return []

        items: List[Dict[str, Any]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            memory_payload = point.get("payload", {})
            if not isinstance(memory_payload, dict):
                continue
            item = dict(memory_payload)
            item["vector_score"] = float(point.get("score", 0.0) or 0.0)
            items.append(item)
        return items

    def _embedding_provider_name(self) -> str:
        return str(getattr(self.embedding_provider, "__class__", type(self.embedding_provider)).__name__).replace(
            "EmbeddingProvider",
            "",
        ).lower() or "unknown"

    def _embedding_provider_model(self) -> str:
        return str(getattr(self.embedding_provider, "model", "") or getattr(self.embedding_provider, "vector_size", ""))
