"""Helpers for Phase 4 vector shadow payload generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
import uuid
from typing import Any, Dict, List


@dataclass(frozen=True)
class VectorShadowCandidate:
    """One candidate payload prepared for future Qdrant shadow transport."""

    point_id: str
    memory_id: str
    memory_type: str
    state: str
    score: float
    confidence: float
    repository: str
    execution_repository: str
    app_code: str
    workflow_id: str
    source_path: str
    title: str
    summary: str
    embedding_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_vector_shadow_manifest(
    *,
    entries: List[Dict[str, Any]],
    repository: str,
    execution_repository: str,
    app_code: str,
    workflow_id: str,
    limit: int = 24,
) -> Dict[str, Any]:
    """Project memory runtime entries into deterministic vector shadow candidates."""

    candidates: List[Dict[str, Any]] = []
    for entry in entries[: max(1, int(limit or 24))]:
        candidate = _candidate_from_entry(
            entry=entry,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
        )
        if candidate is None:
            continue
        candidates.append(candidate.to_dict())

    return {
        "provider": "qdrant",
        "mode": "shadow_manifest_only",
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _candidate_from_entry(
    *,
    entry: Dict[str, Any],
    repository: str,
    execution_repository: str,
    app_code: str,
    workflow_id: str,
) -> VectorShadowCandidate | None:
    memory_id = str(entry.get("memory_id", "")).strip()
    if not memory_id:
        return None
    title = str(entry.get("title", "")).strip()
    summary = str(entry.get("summary", "")).strip()
    source_path = str(entry.get("source_path", "")).strip()
    embedding_text = _embedding_text(entry)
    if not embedding_text:
        return None

    digest = hashlib.sha1(memory_id.encode("utf-8")).digest()
    point_id = str(uuid.UUID(bytes=digest[:16]))
    return VectorShadowCandidate(
        point_id=point_id,
        memory_id=memory_id,
        memory_type=str(entry.get("memory_type", "")).strip(),
        state=str(entry.get("state", "")).strip() or "active",
        score=float(entry.get("score", 0.0) or 0.0),
        confidence=float(entry.get("confidence", 0.0) or 0.0),
        repository=repository,
        execution_repository=execution_repository,
        app_code=app_code,
        workflow_id=workflow_id,
        source_path=source_path,
        title=title,
        summary=summary,
        embedding_text=embedding_text[:4000],
    )


def _embedding_text(entry: Dict[str, Any]) -> str:
    parts = [
        str(entry.get("memory_type", "")).strip(),
        str(entry.get("title", "")).strip(),
        str(entry.get("summary", "")).strip(),
        str(entry.get("source_path", "")).strip(),
    ]
    payload = entry.get("payload")
    if isinstance(payload, dict):
        parts.extend(
            [
                str(payload.get("decision_type", "")).strip(),
                str(payload.get("chosen_strategy", "")).strip(),
                str(payload.get("failure_signature", "")).strip(),
                str(payload.get("rule", "")).strip(),
            ]
        )
    text = "\n".join(part for part in parts if part)
    return text.strip()


def embed_text_hash(text: str, *, size: int = 64) -> List[float]:
    """Return deterministic hash embedding for shadow transport only."""

    normalized_size = max(8, min(int(size or 64), 4096))
    vector = [0.0] * normalized_size
    tokens = [token.strip().lower() for token in text.split() if token.strip()]
    if not tokens:
        return vector

    for token in tokens:
        token_digest = hashlib.sha1(token.encode("utf-8")).digest()
        bucket = int.from_bytes(token_digest[:4], "big") % normalized_size
        sign = 1.0 if token_digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (token_digest[5] / 255.0)
        vector[bucket] += sign * weight

    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [round(value / norm, 6) for value in vector]
