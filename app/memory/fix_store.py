"""Fix History Vector Store — zero-dependency cross-job learning.

Stores (problem, diff_summary, score_delta) triplets and retrieves
the most similar past fixes using TF-IDF cosine similarity (stdlib only).

Usage:
    store = FixStore(memory_dir)
    store.upsert(job_id, problem, diff_summary, score_delta)
    similar = store.search(query_text, top_k=3)

When MEMORY_ENABLED is false, use NoOpFixStore which is a drop-in replacement.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


_STORE_FILENAME = "fix_history.json"
_MAX_ENTRIES = 500
_MAX_TEXT_LEN = 800


def _tokenize(text: str) -> list[str]:
    """Lower-case word tokenizer — no external deps needed."""
    return re.findall(r"[a-zA-Z가-힣]+", (text or "").lower())


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    total = len(tokens) or 1
    return {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    shared = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in a.values())) or 1.0
    norm_b = math.sqrt(sum(v * v for v in b.values())) or 1.0
    return dot / (norm_a * norm_b)


def _build_idf(entries: list[dict[str, Any]]) -> dict[str, float]:
    """Compute IDF over all stored texts."""
    n = len(entries) or 1
    df: Counter[str] = Counter()
    for entry in entries:
        tokens = set(_tokenize(entry.get("text", "")))
        df.update(tokens)
    return {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}


class FixStore:
    """Persistent fix triplet store backed by a JSON file."""

    def __init__(self, memory_dir: Path) -> None:
        self._path = memory_dir / _STORE_FILENAME
        memory_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data.get("entries", [])
        except Exception:
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self._path.write_text(
            json.dumps({"entries": entries[-_MAX_ENTRIES:]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def upsert(
        self,
        job_id: str,
        problem: str,
        diff_summary: str,
        score_delta: float,
    ) -> None:
        """Store a fix triplet. Replaces existing entry for the same job_id."""
        entries = self._load()
        entries = [e for e in entries if e.get("job_id") != job_id]
        text = f"{problem[:_MAX_TEXT_LEN]} {diff_summary[:_MAX_TEXT_LEN]}"
        entries.append(
            {
                "job_id": job_id,
                "problem": problem[:_MAX_TEXT_LEN],
                "diff_summary": diff_summary[:_MAX_TEXT_LEN],
                "score_delta": score_delta,
                "text": text,
            }
        )
        self._save(entries)

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Return top-k most similar past fixes for a given query."""
        entries = self._load()
        if not entries:
            return []
        idf = _build_idf(entries)
        query_tokens = _tokenize(query)
        query_vec = _tfidf_vector(query_tokens, idf)
        if not query_vec:
            return []
        scored = []
        for entry in entries:
            entry_tokens = _tokenize(entry.get("text", ""))
            entry_vec = _tfidf_vector(entry_tokens, idf)
            sim = _cosine(query_vec, entry_vec)
            scored.append((sim, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k] if _ > 0.0]


class NoOpFixStore:
    """Drop-in replacement used when MEMORY_ENABLED=false."""

    def upsert(self, job_id: str, problem: str, diff_summary: str, score_delta: float) -> None:
        pass

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        return []
