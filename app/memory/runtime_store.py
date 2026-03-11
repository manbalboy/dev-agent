"""SQLite-backed canonical memory runtime store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List

from app.models import utc_now_iso


class MemoryRuntimeStore:
    """Durable canonical store for Phase 3 memory runtime data."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    repository TEXT NOT NULL DEFAULT '',
                    execution_repository TEXT NOT NULL DEFAULT '',
                    app_code TEXT NOT NULL DEFAULT '',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    job_id TEXT NOT NULL DEFAULT '',
                    issue_number INTEGER NOT NULL DEFAULT 0,
                    issue_title TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'active',
                    baseline_score REAL NOT NULL DEFAULT 0.0,
                    baseline_confidence REAL NOT NULL DEFAULT 0.0,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    score REAL NOT NULL DEFAULT 0.0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    retrieval_count INTEGER NOT NULL DEFAULT 0,
                    effectiveness REAL NOT NULL DEFAULT 0.0,
                    staleness_penalty REAL NOT NULL DEFAULT 0.0,
                    positive_count INTEGER NOT NULL DEFAULT 0,
                    negative_count INTEGER NOT NULL DEFAULT 0,
                    neutral_count INTEGER NOT NULL DEFAULT 0,
                    state_reason TEXT NOT NULL DEFAULT '',
                    manual_state_override TEXT NOT NULL DEFAULT '',
                    manual_override_note TEXT NOT NULL DEFAULT '',
                    last_verdict TEXT NOT NULL DEFAULT '',
                    last_routes_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    last_used_at TEXT NOT NULL DEFAULT '',
                    last_feedback_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS memory_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS memory_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    repository TEXT NOT NULL DEFAULT '',
                    execution_repository TEXT NOT NULL DEFAULT '',
                    app_code TEXT NOT NULL DEFAULT '',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    verdict TEXT NOT NULL DEFAULT '',
                    score_delta REAL NOT NULL DEFAULT 0.0,
                    routes_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (memory_id) REFERENCES memory_entries(memory_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS memory_retrieval_runs (
                    run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT '',
                    repository TEXT NOT NULL DEFAULT '',
                    execution_repository TEXT NOT NULL DEFAULT '',
                    app_code TEXT NOT NULL DEFAULT '',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    selection_ids_json TEXT NOT NULL DEFAULT '[]',
                    context_json TEXT NOT NULL DEFAULT '[]',
                    corpus_counts_json TEXT NOT NULL DEFAULT '{}',
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS memory_backlog_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL DEFAULT '',
                    execution_repository TEXT NOT NULL DEFAULT '',
                    app_code TEXT NOT NULL DEFAULT '',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'P2',
                    state TEXT NOT NULL DEFAULT 'candidate',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_memory_entries_lookup
                ON memory_entries (memory_type, repository, app_code, workflow_id, state);

                CREATE INDEX IF NOT EXISTS idx_memory_feedback_job
                ON memory_feedback (job_id, generated_at);

                CREATE INDEX IF NOT EXISTS idx_memory_retrieval_runs_job
                ON memory_retrieval_runs (job_id, route, generated_at);
                """
            )
            self._ensure_column(connection, "memory_entries", "baseline_score", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column(connection, "memory_entries", "baseline_confidence", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column(connection, "memory_entries", "retrieval_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "memory_entries", "effectiveness", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column(connection, "memory_entries", "staleness_penalty", "REAL NOT NULL DEFAULT 0.0")
            self._ensure_column(connection, "memory_entries", "state_reason", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "memory_entries", "manual_state_override", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "memory_entries", "manual_override_note", "TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        existing_columns = {
            str(row["name"]).strip()
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in existing_columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def upsert_entry(self, payload: Dict[str, Any]) -> None:
        memory_id = str(payload.get("memory_id", "")).strip()
        if not memory_id:
            raise ValueError("memory_id is required")

        now = utc_now_iso()
        created_at = str(payload.get("created_at", "")).strip() or str(payload.get("updated_at", "")).strip() or now
        updated_at = str(payload.get("updated_at", "")).strip() or created_at
        score = float(payload.get("score", 0.0) or 0.0)
        confidence = float(payload.get("confidence", 0.0) or 0.0)
        baseline_score = float(payload.get("baseline_score", score) or 0.0)
        baseline_confidence = float(payload.get("baseline_confidence", confidence) or 0.0)
        manual_state_override = self._normalize_override_state(payload.get("manual_state_override", ""))
        manual_override_note = str(payload.get("manual_override_note", "")).strip()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_entries (
                    memory_id, memory_type, repository, execution_repository, app_code,
                    workflow_id, job_id, issue_number, issue_title, source_kind, source_path, title,
                    summary, state, baseline_score, baseline_confidence, confidence, score,
                    usage_count, retrieval_count, effectiveness, staleness_penalty, positive_count,
                    negative_count, neutral_count, state_reason, manual_state_override, manual_override_note,
                    last_verdict, last_routes_json, payload_json, created_at, updated_at, last_used_at, last_feedback_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    memory_type=excluded.memory_type,
                    repository=excluded.repository,
                    execution_repository=excluded.execution_repository,
                    app_code=excluded.app_code,
                    workflow_id=excluded.workflow_id,
                    job_id=excluded.job_id,
                    issue_number=excluded.issue_number,
                    issue_title=excluded.issue_title,
                    source_kind=excluded.source_kind,
                    source_path=excluded.source_path,
                    title=excluded.title,
                    summary=excluded.summary,
                    state=excluded.state,
                    baseline_score=excluded.baseline_score,
                    baseline_confidence=excluded.baseline_confidence,
                    confidence=excluded.confidence,
                    score=excluded.score,
                    usage_count=excluded.usage_count,
                    retrieval_count=excluded.retrieval_count,
                    effectiveness=excluded.effectiveness,
                    staleness_penalty=excluded.staleness_penalty,
                    positive_count=excluded.positive_count,
                    negative_count=excluded.negative_count,
                    neutral_count=excluded.neutral_count,
                    state_reason=excluded.state_reason,
                    manual_state_override=excluded.manual_state_override,
                    manual_override_note=excluded.manual_override_note,
                    last_verdict=excluded.last_verdict,
                    last_routes_json=excluded.last_routes_json,
                    payload_json=excluded.payload_json,
                    created_at=COALESCE(NULLIF(memory_entries.created_at, ''), excluded.created_at),
                    updated_at=excluded.updated_at,
                    last_used_at=excluded.last_used_at,
                    last_feedback_at=excluded.last_feedback_at
                """,
                (
                    memory_id,
                    str(payload.get("memory_type", "")).strip() or "unknown",
                    str(payload.get("repository", "")).strip(),
                    str(payload.get("execution_repository", "")).strip(),
                    str(payload.get("app_code", "")).strip(),
                    str(payload.get("workflow_id", "")).strip(),
                    str(payload.get("job_id", "")).strip(),
                    int(payload.get("issue_number", 0) or 0),
                    str(payload.get("issue_title", "")).strip(),
                    str(payload.get("source_kind", "")).strip(),
                    str(payload.get("source_path", "")).strip(),
                    str(payload.get("title", "")).strip(),
                    str(payload.get("summary", "")).strip(),
                    str(payload.get("state", "")).strip() or "active",
                    baseline_score,
                    baseline_confidence,
                    confidence,
                    score,
                    int(payload.get("usage_count", 0) or 0),
                    int(payload.get("retrieval_count", 0) or 0),
                    float(payload.get("effectiveness", 0.0) or 0.0),
                    float(payload.get("staleness_penalty", 0.0) or 0.0),
                    int(payload.get("positive_count", 0) or 0),
                    int(payload.get("negative_count", 0) or 0),
                    int(payload.get("neutral_count", 0) or 0),
                    str(payload.get("state_reason", "")).strip(),
                    manual_state_override,
                    manual_override_note,
                    str(payload.get("last_verdict", "")).strip(),
                    self._json_dumps(payload.get("last_routes", [])),
                    self._json_dumps(payload.get("payload", {})),
                    created_at,
                    updated_at,
                    str(payload.get("last_used_at", "")).strip(),
                    str(payload.get("last_feedback_at", "")).strip(),
                ),
            )

    def refresh_rankings(self, *, as_of: str | None = None) -> Dict[str, int]:
        """Recompute ranking state from feedback, retrieval effectiveness, and freshness."""

        reference_time = self._parse_timestamp(as_of or utc_now_iso()) or datetime.now(timezone.utc)
        with self._connect() as connection:
            entry_rows = connection.execute("SELECT * FROM memory_entries").fetchall()
            feedback_rows = connection.execute(
                "SELECT memory_id, generated_at, verdict, score_delta, routes_json FROM memory_feedback"
            ).fetchall()
            retrieval_rows = connection.execute(
                "SELECT generated_at, selection_ids_json FROM memory_retrieval_runs WHERE enabled = 1"
            ).fetchall()

            feedback_map: Dict[str, Dict[str, Any]] = {}
            for row in feedback_rows:
                memory_id = str(row["memory_id"] or "").strip()
                if not memory_id:
                    continue
                bucket = feedback_map.setdefault(
                    memory_id,
                    {
                        "feedback_count": 0,
                        "score_total": 0.0,
                        "positive_count": 0,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "last_feedback_at": "",
                        "last_verdict": "",
                        "last_routes": [],
                    },
                )
                generated_at = str(row["generated_at"] or "").strip()
                score_delta = float(row["score_delta"] or 0.0)
                verdict = str(row["verdict"] or "").strip()
                routes = self._loads_json_array(row["routes_json"])
                bucket["feedback_count"] = int(bucket["feedback_count"] or 0) + 1
                bucket["score_total"] = float(bucket["score_total"] or 0.0) + score_delta
                if score_delta > 0:
                    bucket["positive_count"] = int(bucket["positive_count"] or 0) + 1
                elif score_delta < 0:
                    bucket["negative_count"] = int(bucket["negative_count"] or 0) + 1
                else:
                    bucket["neutral_count"] = int(bucket["neutral_count"] or 0) + 1
                if generated_at >= str(bucket["last_feedback_at"] or ""):
                    bucket["last_feedback_at"] = generated_at
                    bucket["last_verdict"] = verdict
                    bucket["last_routes"] = routes

            retrieval_map: Dict[str, Dict[str, Any]] = {}
            for row in retrieval_rows:
                generated_at = str(row["generated_at"] or "").strip()
                selection_ids = self._loads_json_array(row["selection_ids_json"])
                for memory_id in selection_ids:
                    normalized = str(memory_id or "").strip()
                    if not normalized:
                        continue
                    bucket = retrieval_map.setdefault(
                        normalized,
                        {"retrieval_count": 0, "last_used_at": ""},
                    )
                    bucket["retrieval_count"] = int(bucket["retrieval_count"] or 0) + 1
                    if generated_at >= str(bucket["last_used_at"] or ""):
                        bucket["last_used_at"] = generated_at

            state_counts: Dict[str, int] = {}
            for row in entry_rows:
                entry = self._decode_entry(row)
                memory_id = str(entry.get("memory_id", "")).strip()
                if not memory_id:
                    continue
                feedback_stats = feedback_map.get(memory_id, {})
                retrieval_stats = retrieval_map.get(memory_id, {})
                positive_count = int(feedback_stats.get("positive_count", 0) or 0)
                negative_count = int(feedback_stats.get("negative_count", 0) or 0)
                neutral_count = int(feedback_stats.get("neutral_count", 0) or 0)
                feedback_count = int(feedback_stats.get("feedback_count", 0) or 0)
                retrieval_count = int(retrieval_stats.get("retrieval_count", 0) or 0)
                usage_count = max(feedback_count, retrieval_count)
                baseline_score = float(entry.get("baseline_score", 0.0) or 0.0)
                baseline_confidence = float(entry.get("baseline_confidence", 0.0) or 0.0)
                score_total = float(feedback_stats.get("score_total", 0.0) or 0.0)
                base_score = baseline_score if abs(baseline_score) > 0.0 else score_total
                effectiveness = 0.0
                if usage_count > 0:
                    effectiveness = (positive_count - negative_count) / float(usage_count)
                staleness_penalty = self._staleness_penalty(
                    reference_time=reference_time,
                    timestamps=[
                        str(retrieval_stats.get("last_used_at", "")).strip(),
                        str(feedback_stats.get("last_feedback_at", "")).strip(),
                        str(entry.get("last_used_at", "")).strip(),
                        str(entry.get("last_feedback_at", "")).strip(),
                        str(entry.get("updated_at", "")).strip(),
                        str(entry.get("created_at", "")).strip(),
                    ],
                )
                adjusted_score = round(
                    self._clamp_score(base_score + effectiveness * 2.0 - staleness_penalty),
                    3,
                )
                adjusted_confidence = round(
                    max(
                        0.05,
                        min(
                            0.98,
                            max(baseline_confidence, 0.5)
                            + adjusted_score * 0.04
                            + positive_count * 0.02
                            - negative_count * 0.03
                            + min(usage_count, 6) * 0.01
                            - staleness_penalty * 0.06,
                        ),
                    ),
                    3,
                )
                state, state_reason = self._ranking_state(
                    score=adjusted_score,
                    positive_count=positive_count,
                    negative_count=negative_count,
                    effectiveness=effectiveness,
                    staleness_penalty=staleness_penalty,
                    usage_count=usage_count,
                )
                manual_state_override = self._normalize_override_state(entry.get("manual_state_override", ""))
                manual_override_note = str(entry.get("manual_override_note", "")).strip()
                if manual_state_override:
                    state = manual_state_override
                    state_reason = "manual override"
                    if manual_override_note:
                        state_reason = f"{state_reason}: {manual_override_note}"
                state_counts[state] = int(state_counts.get(state, 0) or 0) + 1
                connection.execute(
                    """
                    UPDATE memory_entries
                    SET score = ?,
                        confidence = ?,
                        usage_count = ?,
                        retrieval_count = ?,
                        effectiveness = ?,
                        staleness_penalty = ?,
                        positive_count = ?,
                        negative_count = ?,
                        neutral_count = ?,
                        state = ?,
                        state_reason = ?,
                        last_feedback_at = ?,
                        last_used_at = ?,
                        last_verdict = ?,
                        last_routes_json = ?
                    WHERE memory_id = ?
                    """,
                    (
                        adjusted_score,
                        adjusted_confidence,
                        usage_count,
                        retrieval_count,
                        round(effectiveness, 3),
                        round(staleness_penalty, 3),
                        positive_count,
                        negative_count,
                        neutral_count,
                        state,
                        state_reason,
                        str(feedback_stats.get("last_feedback_at", "")).strip(),
                        str(retrieval_stats.get("last_used_at", "")).strip() or str(entry.get("last_used_at", "")).strip(),
                        str(feedback_stats.get("last_verdict", "")).strip(),
                        self._json_dumps(feedback_stats.get("last_routes", [])),
                        memory_id,
                    ),
                )

        return state_counts

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(-6.0, min(6.0, float(value or 0.0)))

    @staticmethod
    def _ranking_state(
        *,
        score: float,
        positive_count: int,
        negative_count: int,
        effectiveness: float,
        staleness_penalty: float,
        usage_count: int,
    ) -> tuple[str, str]:
        if negative_count >= 3 and score <= -2.5:
            return "banned", "3+ negative feedback with strongly negative score"
        if usage_count >= 4 and effectiveness <= -0.75:
            return "banned", "retrieval effectiveness stayed strongly negative"
        if score >= 3.0 or (positive_count >= 3 and effectiveness >= 0.4):
            if score >= 3.0:
                return "promoted", "high cumulative score"
            return "promoted", "repeated positive feedback and good effectiveness"
        if score < 0.0 or staleness_penalty >= 1.0 or effectiveness <= -0.25:
            if score < 0.0:
                return "decayed", "score dropped below zero"
            if staleness_penalty >= 1.0:
                return "decayed", "staleness penalty applied"
            return "decayed", "retrieval effectiveness fell below threshold"
        return "active", "within normal scoring range"

    @classmethod
    def _staleness_penalty(cls, *, reference_time: datetime, timestamps: List[str]) -> float:
        freshest: datetime | None = None
        for raw in timestamps:
            parsed = cls._parse_timestamp(raw)
            if parsed is None:
                continue
            if freshest is None or parsed > freshest:
                freshest = parsed
        if freshest is None:
            return 0.0
        age_days = max(0.0, (reference_time - freshest).total_seconds() / 86400.0)
        if age_days >= 30:
            return 2.0
        if age_days >= 14:
            return 1.0
        return 0.0

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _loads_json_array(raw: object) -> List[str]:
        if not isinstance(raw, str) or not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [str(item or "").strip() for item in payload if str(item or "").strip()]

    def replace_evidence(self, memory_id: str, evidences: List[Dict[str, Any]]) -> None:
        normalized_memory_id = str(memory_id or "").strip()
        if not normalized_memory_id:
            raise ValueError("memory_id is required")

        with self._connect() as connection:
            connection.execute("DELETE FROM memory_evidence WHERE memory_id = ?", (normalized_memory_id,))
            for index, evidence in enumerate(evidences):
                source_path = str(evidence.get("source_path", "")).strip()
                evidence_type = str(evidence.get("evidence_type", "")).strip() or "reference"
                evidence_id = str(evidence.get("evidence_id", "")).strip() or (
                    f"{normalized_memory_id}:{evidence_type}:{index}:{source_path or 'evidence'}"
                )
                connection.execute(
                    """
                    INSERT INTO memory_evidence (
                        evidence_id, memory_id, evidence_type, source_path,
                        content, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        normalized_memory_id,
                        evidence_type,
                        source_path,
                        str(evidence.get("content", "")).strip(),
                        self._json_dumps(evidence.get("payload", {})),
                        str(evidence.get("created_at", "")).strip() or utc_now_iso(),
                    ),
                )

    def upsert_feedback(self, payload: Dict[str, Any]) -> None:
        feedback_id = str(payload.get("feedback_id", "")).strip()
        if not feedback_id:
            raise ValueError("feedback_id is required")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_feedback (
                    feedback_id, memory_id, job_id, repository, execution_repository,
                    app_code, workflow_id, generated_at, verdict, score_delta,
                    routes_json, evidence_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feedback_id) DO UPDATE SET
                    memory_id=excluded.memory_id,
                    job_id=excluded.job_id,
                    repository=excluded.repository,
                    execution_repository=excluded.execution_repository,
                    app_code=excluded.app_code,
                    workflow_id=excluded.workflow_id,
                    generated_at=excluded.generated_at,
                    verdict=excluded.verdict,
                    score_delta=excluded.score_delta,
                    routes_json=excluded.routes_json,
                    evidence_json=excluded.evidence_json,
                    payload_json=excluded.payload_json
                """,
                (
                    feedback_id,
                    str(payload.get("memory_id", "")).strip(),
                    str(payload.get("job_id", "")).strip(),
                    str(payload.get("repository", "")).strip(),
                    str(payload.get("execution_repository", "")).strip(),
                    str(payload.get("app_code", "")).strip(),
                    str(payload.get("workflow_id", "")).strip(),
                    str(payload.get("generated_at", "")).strip() or utc_now_iso(),
                    str(payload.get("verdict", "")).strip(),
                    float(payload.get("score_delta", 0.0) or 0.0),
                    self._json_dumps(payload.get("routes", [])),
                    self._json_dumps(payload.get("evidence", {})),
                    self._json_dumps(payload.get("payload", {})),
                ),
            )

    def upsert_retrieval_run(self, payload: Dict[str, Any]) -> None:
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("run_id is required")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_retrieval_runs (
                    run_id, job_id, route, repository, execution_repository,
                    app_code, workflow_id, generated_at, enabled,
                    selection_ids_json, context_json, corpus_counts_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    job_id=excluded.job_id,
                    route=excluded.route,
                    repository=excluded.repository,
                    execution_repository=excluded.execution_repository,
                    app_code=excluded.app_code,
                    workflow_id=excluded.workflow_id,
                    generated_at=excluded.generated_at,
                    enabled=excluded.enabled,
                    selection_ids_json=excluded.selection_ids_json,
                    context_json=excluded.context_json,
                    corpus_counts_json=excluded.corpus_counts_json,
                    payload_json=excluded.payload_json
                """,
                (
                    run_id,
                    str(payload.get("job_id", "")).strip(),
                    str(payload.get("route", "")).strip(),
                    str(payload.get("repository", "")).strip(),
                    str(payload.get("execution_repository", "")).strip(),
                    str(payload.get("app_code", "")).strip(),
                    str(payload.get("workflow_id", "")).strip(),
                    str(payload.get("generated_at", "")).strip() or utc_now_iso(),
                    1 if bool(payload.get("enabled", True)) else 0,
                    self._json_dumps(payload.get("selection_ids", [])),
                    self._json_dumps(payload.get("context", [])),
                    self._json_dumps(payload.get("corpus_counts", {})),
                    self._json_dumps(payload.get("payload", {})),
                ),
            )

    def upsert_backlog_candidate(self, payload: Dict[str, Any]) -> None:
        candidate_id = str(payload.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")

        created_at = str(payload.get("created_at", "")).strip() or utc_now_iso()
        updated_at = str(payload.get("updated_at", "")).strip() or created_at
        priority = self._normalize_backlog_priority(payload.get("priority", "")) or "P2"
        state = str(payload.get("state", "")).strip().lower() or "candidate"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_backlog_candidates (
                    candidate_id, repository, execution_repository, app_code,
                    workflow_id, title, summary, priority, state,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    repository=excluded.repository,
                    execution_repository=excluded.execution_repository,
                    app_code=excluded.app_code,
                    workflow_id=excluded.workflow_id,
                    title=excluded.title,
                    summary=excluded.summary,
                    priority=excluded.priority,
                    state=excluded.state,
                    payload_json=excluded.payload_json,
                    created_at=COALESCE(NULLIF(memory_backlog_candidates.created_at, ''), excluded.created_at),
                    updated_at=excluded.updated_at
                """,
                (
                    candidate_id,
                    str(payload.get("repository", "")).strip(),
                    str(payload.get("execution_repository", "")).strip(),
                    str(payload.get("app_code", "")).strip(),
                    str(payload.get("workflow_id", "")).strip(),
                    str(payload.get("title", "")).strip(),
                    str(payload.get("summary", "")).strip(),
                    priority,
                    state,
                    self._json_dumps(payload.get("payload", {})),
                    created_at,
                    updated_at,
                ),
            )

    def get_entry(self, memory_id: str) -> Dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_entries WHERE memory_id = ?",
                (str(memory_id or "").strip(),),
            ).fetchone()
        return self._decode_entry(row) if row is not None else None

    def list_entries(self, *, memory_type: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM memory_entries"
        params: tuple[object, ...] = ()
        if str(memory_type or "").strip():
            query += " WHERE memory_type = ?"
            params = (str(memory_type or "").strip(),)
        query += " ORDER BY memory_type ASC, memory_id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._decode_entry(row) for row in rows]

    def search_entries(
        self,
        *,
        query: str = "",
        state: str = "",
        memory_type: str = "",
        repository: str = "",
        execution_repository: str = "",
        app_code: str = "",
        workflow_id: str = "",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[object] = []
        normalized_query = str(query or "").strip().lower()
        normalized_state = self._normalize_override_state(state) or str(state or "").strip().lower()
        normalized_memory_type = str(memory_type or "").strip().lower()
        normalized_repository = str(repository or "").strip()
        normalized_execution_repository = str(execution_repository or "").strip()
        normalized_app_code = str(app_code or "").strip()
        normalized_workflow_id = str(workflow_id or "").strip()
        normalized_limit = max(1, min(int(limit or 25), 100))

        if normalized_query:
            like_pattern = f"%{normalized_query}%"
            clauses.append(
                "("
                "LOWER(memory_id) LIKE ? OR LOWER(title) LIKE ? OR LOWER(summary) LIKE ? "
                "OR LOWER(source_path) LIKE ? OR LOWER(issue_title) LIKE ?"
                ")"
            )
            params.extend([like_pattern] * 5)
        if normalized_state:
            clauses.append("state = ?")
            params.append(normalized_state)
        if normalized_memory_type:
            clauses.append("memory_type = ?")
            params.append(normalized_memory_type)
        if normalized_repository:
            clauses.append("repository = ?")
            params.append(normalized_repository)
        if normalized_execution_repository:
            clauses.append("execution_repository = ?")
            params.append(normalized_execution_repository)
        if normalized_app_code:
            clauses.append("app_code = ?")
            params.append(normalized_app_code)
        if normalized_workflow_id:
            clauses.append("workflow_id = ?")
            params.append(normalized_workflow_id)

        sql = "SELECT * FROM memory_entries"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += """
            ORDER BY
                CASE state
                    WHEN 'promoted' THEN 0
                    WHEN 'active' THEN 1
                    WHEN 'decayed' THEN 2
                    WHEN 'candidate' THEN 3
                    WHEN 'banned' THEN 4
                    WHEN 'archived' THEN 5
                    ELSE 6
                END,
                score DESC,
                confidence DESC,
                updated_at DESC,
                memory_id ASC
            LIMIT ?
        """
        params.append(normalized_limit)
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._decode_entry(row) for row in rows]

    def set_manual_override(self, memory_id: str, *, state: str = "", note: str = "") -> Dict[str, Any] | None:
        normalized_id = str(memory_id or "").strip()
        if not normalized_id:
            return None
        normalized_state = self._normalize_override_state(state)
        normalized_note = str(note or "").strip()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT memory_id FROM memory_entries WHERE memory_id = ?",
                (normalized_id,),
            ).fetchone()
            if existing is None:
                return None
            connection.execute(
                """
                UPDATE memory_entries
                SET manual_state_override = ?,
                    manual_override_note = ?,
                    updated_at = ?
                WHERE memory_id = ?
                """,
                (
                    normalized_state,
                    normalized_note,
                    utc_now_iso(),
                    normalized_id,
                ),
            )
        self.refresh_rankings(as_of=utc_now_iso())
        return self.get_entry(normalized_id)

    def query_entries_for_retrieval(
        self,
        *,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return retrieval candidates ordered by scope affinity and quality."""

        normalized_repository = str(repository or "").strip()
        normalized_execution_repository = str(execution_repository or "").strip()
        normalized_app_code = str(app_code or "").strip()
        normalized_workflow_id = str(workflow_id or "").strip()
        normalized_limit = max(1, int(limit or 200))

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memory_entries
                WHERE state NOT IN ('banned', 'archived')
                  AND (
                    execution_repository = ?
                    OR repository = ?
                    OR execution_repository = ?
                    OR repository = ?
                  )
                ORDER BY
                    CASE WHEN execution_repository = ? THEN 0 ELSE 1 END,
                    CASE WHEN repository = ? THEN 0 ELSE 1 END,
                    CASE
                        WHEN app_code = ? THEN 0
                        WHEN app_code = '' OR app_code = 'default' THEN 1
                        ELSE 2
                    END,
                    CASE
                        WHEN workflow_id = ? AND workflow_id != '' THEN 0
                        WHEN workflow_id = '' THEN 1
                        ELSE 2
                    END,
                    CASE state
                        WHEN 'promoted' THEN 0
                        WHEN 'active' THEN 1
                        WHEN 'decayed' THEN 2
                        WHEN 'candidate' THEN 3
                        ELSE 4
                    END,
                    score DESC,
                    confidence DESC,
                    usage_count DESC,
                    last_feedback_at DESC,
                    updated_at DESC,
                    memory_id ASC
                LIMIT ?
                """,
                (
                    normalized_execution_repository,
                    normalized_execution_repository,
                    normalized_repository,
                    normalized_repository,
                    normalized_execution_repository,
                    normalized_repository,
                    normalized_app_code,
                    normalized_workflow_id,
                    normalized_limit,
                ),
            ).fetchall()
        return [self._decode_entry(row) for row in rows]

    def list_evidence(self, memory_id: str) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_evidence WHERE memory_id = ? ORDER BY evidence_id ASC",
                (str(memory_id or "").strip(),),
            ).fetchall()
        decoded_rows = [self._decode_row(row, json_fields={"payload_json"}) for row in rows]
        for item in decoded_rows:
            item["payload"] = item.pop("payload_json", {})
        return decoded_rows

    def list_feedback(self, *, memory_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM memory_feedback"
        params: tuple[object, ...] = ()
        if str(memory_id or "").strip():
            query += " WHERE memory_id = ?"
            params = (str(memory_id or "").strip(),)
        query += " ORDER BY generated_at ASC, feedback_id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        decoded_rows = [
            self._decode_row(row, json_fields={"routes_json", "evidence_json", "payload_json"})
            for row in rows
        ]
        for item in decoded_rows:
            item["routes"] = item.pop("routes_json", [])
            item["evidence"] = item.pop("evidence_json", {})
            item["payload"] = item.pop("payload_json", {})
        return decoded_rows

    def list_retrieval_runs(self, *, job_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM memory_retrieval_runs"
        params: tuple[object, ...] = ()
        if str(job_id or "").strip():
            query += " WHERE job_id = ?"
            params = (str(job_id or "").strip(),)
        query += " ORDER BY generated_at ASC, route ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        decoded_rows = [
            self._decode_row(
                row,
                json_fields={"selection_ids_json", "context_json", "corpus_counts_json", "payload_json"},
            )
            for row in rows
        ]
        for item in decoded_rows:
            item["enabled"] = bool(item.get("enabled"))
            item["selection_ids"] = item.pop("selection_ids_json", [])
            item["context"] = item.pop("context_json", [])
            item["corpus_counts"] = item.pop("corpus_counts_json", {})
            item["payload"] = item.pop("payload_json", {})
        return decoded_rows

    def list_backlog_candidates(
        self,
        *,
        query: str = "",
        state: str = "",
        priority: str = "",
        repository: str = "",
        execution_repository: str = "",
        app_code: str = "",
        workflow_id: str = "",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[object] = []
        normalized_query = str(query or "").strip().lower()
        normalized_state = str(state or "").strip().lower()
        normalized_priority = self._normalize_backlog_priority(priority)
        normalized_repository = str(repository or "").strip()
        normalized_execution_repository = str(execution_repository or "").strip()
        normalized_app_code = str(app_code or "").strip()
        normalized_workflow_id = str(workflow_id or "").strip()
        normalized_limit = max(1, min(int(limit or 25), 100))

        if normalized_query:
            like_pattern = f"%{normalized_query}%"
            clauses.append(
                "("
                "LOWER(candidate_id) LIKE ? OR LOWER(title) LIKE ? OR LOWER(summary) LIKE ? "
                "OR LOWER(payload_json) LIKE ?"
                ")"
            )
            params.extend([like_pattern] * 4)
        if normalized_state:
            clauses.append("LOWER(state) = ?")
            params.append(normalized_state)
        if normalized_priority:
            clauses.append("priority = ?")
            params.append(normalized_priority)
        if normalized_repository:
            clauses.append("repository = ?")
            params.append(normalized_repository)
        if normalized_execution_repository:
            clauses.append("execution_repository = ?")
            params.append(normalized_execution_repository)
        if normalized_app_code:
            clauses.append("app_code = ?")
            params.append(normalized_app_code)
        if normalized_workflow_id:
            clauses.append("workflow_id = ?")
            params.append(normalized_workflow_id)

        sql = "SELECT * FROM memory_backlog_candidates"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += """
            ORDER BY
                CASE priority
                    WHEN 'P0' THEN 0
                    WHEN 'P1' THEN 1
                    WHEN 'P2' THEN 2
                    WHEN 'P3' THEN 3
                    ELSE 4
                END,
                CASE LOWER(state)
                    WHEN 'candidate' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'queued' THEN 2
                    WHEN 'done' THEN 3
                    WHEN 'dismissed' THEN 4
                    ELSE 5
                END,
                updated_at DESC,
                candidate_id ASC
            LIMIT ?
        """
        params.append(normalized_limit)
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        decoded_rows = [self._decode_row(row, json_fields={"payload_json"}) for row in rows]
        for item in decoded_rows:
            item["payload"] = item.pop("payload_json", {})
        return decoded_rows

    @staticmethod
    def _json_dumps(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _decode_row(row: sqlite3.Row, *, json_fields: set[str]) -> Dict[str, Any]:
        payload = dict(row)
        list_fields = {"last_routes_json", "routes_json", "selection_ids_json", "context_json"}
        for key in json_fields:
            raw = payload.get(key)
            if not isinstance(raw, str) or not raw:
                payload[key] = [] if key in list_fields else {}
                continue
            try:
                payload[key] = json.loads(raw)
            except json.JSONDecodeError:
                payload[key] = [] if key in list_fields else {}
        return payload

    @classmethod
    def _decode_entry(cls, row: sqlite3.Row) -> Dict[str, Any]:
        payload = cls._decode_row(row, json_fields={"last_routes_json", "payload_json"})
        payload["last_routes"] = payload.pop("last_routes_json", [])
        payload["payload"] = payload.pop("payload_json", {})
        return payload

    @staticmethod
    def _normalize_override_state(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "active", "candidate", "promoted", "decayed", "banned", "archived"}:
            return normalized
        return ""

    @staticmethod
    def _normalize_backlog_priority(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        if normalized in {"", "P0", "P1", "P2", "P3"}:
            return normalized
        return ""
