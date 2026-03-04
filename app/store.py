"""Storage layer for AgentHub jobs.

Supports both JSON files and SQLite through one shared JobStore interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Dict, Iterator, List, Optional

import fcntl

from app.config import AppSettings
from app.models import JobRecord, utc_now_iso


class JobStore(ABC):
    """Abstract interface for job state + queue persistence."""

    @abstractmethod
    def create_job(self, job: JobRecord) -> None:
        """Persist a new job record."""

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Fetch one job record by ID."""

    @abstractmethod
    def list_jobs(self) -> List[JobRecord]:
        """Return all jobs sorted by creation time descending."""

    @abstractmethod
    def update_job(self, job_id: str, **changes: object) -> JobRecord:
        """Apply partial updates and return the updated job."""

    @abstractmethod
    def enqueue_job(self, job_id: str) -> None:
        """Push a job ID into the FIFO queue."""

    @abstractmethod
    def dequeue_job(self) -> Optional[str]:
        """Pop the next job ID from the FIFO queue."""

    @abstractmethod
    def queue_size(self) -> int:
        """Return the number of queued jobs."""


class JsonJobStore(JobStore):
    """JSON-backed JobStore implementation with file locking.

    File locking is essential because API and worker run in separate processes and
    may update the same files at the same time.
    """

    def __init__(self, jobs_file: Path, queue_file: Path) -> None:
        self.jobs_file = jobs_file
        self.queue_file = queue_file

        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.jobs_file.exists():
            self._write_json_atomic(self.jobs_file, {})
        if not self.queue_file.exists():
            self._write_json_atomic(self.queue_file, [])

    def create_job(self, job: JobRecord) -> None:
        """Insert a new job record.

        Raises:
            ValueError: If a job with the same ID already exists.
        """

        with self._locked_json(self.jobs_file, default={}) as jobs_data:
            jobs = self._ensure_job_map(jobs_data)
            if job.job_id in jobs:
                raise ValueError(f"Job already exists: {job.job_id}")
            jobs[job.job_id] = job.to_dict()

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Return a job by ID, or None if it does not exist."""

        with self._locked_json(self.jobs_file, default={}) as jobs_data:
            jobs = self._ensure_job_map(jobs_data)
            payload = jobs.get(job_id)
            if payload is None:
                return None
            return JobRecord.from_dict(payload)

    def list_jobs(self) -> List[JobRecord]:
        """List jobs sorted by newest first."""

        with self._locked_json(self.jobs_file, default={}) as jobs_data:
            jobs = self._ensure_job_map(jobs_data)
            records = [JobRecord.from_dict(item) for item in jobs.values()]

        records.sort(key=lambda record: record.created_at, reverse=True)
        return records

    def update_job(self, job_id: str, **changes: object) -> JobRecord:
        """Update a stored job record and return it.

        `updated_at` is automatically refreshed unless explicitly provided.
        """

        with self._locked_json(self.jobs_file, default={}) as jobs_data:
            jobs = self._ensure_job_map(jobs_data)
            payload = jobs.get(job_id)
            if payload is None:
                raise KeyError(f"Job not found: {job_id}")

            mutable = dict(payload)
            mutable.update(changes)
            mutable.setdefault("updated_at", utc_now_iso())
            if "updated_at" not in changes:
                mutable["updated_at"] = utc_now_iso()

            updated = JobRecord.from_dict(mutable)
            jobs[job_id] = updated.to_dict()
            return updated

    def enqueue_job(self, job_id: str) -> None:
        """Append a job ID to queue tail."""

        with self._locked_json(self.queue_file, default=[]) as queue_data:
            queue = self._ensure_queue_list(queue_data)
            queue.append(job_id)

    def dequeue_job(self) -> Optional[str]:
        """Pop the next job ID from queue head."""

        with self._locked_json(self.queue_file, default=[]) as queue_data:
            queue = self._ensure_queue_list(queue_data)
            if not queue:
                return None
            return queue.pop(0)

    def queue_size(self) -> int:
        """Current queue length."""

        with self._locked_json(self.queue_file, default=[]) as queue_data:
            queue = self._ensure_queue_list(queue_data)
            return len(queue)

    @contextmanager
    def _locked_json(self, file_path: Path, default: object) -> Iterator[object]:
        """Lock a JSON file, load data, then save data on exit.

        Using one lock per file keeps the implementation simple and prevents
        concurrent write corruption between API and worker processes.
        """

        lock_path = file_path.with_suffix(file_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            current = self._read_json(file_path, default)
            yield current
            self._write_json_atomic(file_path, current)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_json(file_path: Path, default: object) -> object:
        """Read JSON safely, returning default when file is missing/empty."""

        if not file_path.exists():
            return default

        raw_text = file_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return default

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            # We keep this fallback lenient for MVP durability. If a file is
            # corrupted, we still allow service boot and return an empty
            # structure instead of crashing hard.
            return default

    @staticmethod
    def _write_json_atomic(file_path: Path, payload: object) -> None:
        """Write JSON atomically to avoid partially written files."""

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=file_path.parent
        ) as temp_file:
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_name = temp_file.name

        Path(temp_name).replace(file_path)

    @staticmethod
    def _ensure_job_map(raw_payload: object) -> Dict[str, Dict[str, object]]:
        """Validate that jobs file content is a dictionary."""

        if isinstance(raw_payload, dict):
            return raw_payload
        return {}

    @staticmethod
    def _ensure_queue_list(raw_payload: object) -> List[str]:
        """Validate that queue file content is a string list."""

        if not isinstance(raw_payload, list):
            return []

        # IMPORTANT:
        # We must return the same list object (not a copied list), because queue
        # operations mutate this object inside `_locked_json`, and `_locked_json`
        # persists only the yielded root object.
        for index, item in enumerate(raw_payload):
            if not isinstance(item, str):
                raw_payload[index] = str(item)
        return raw_payload


class SQLiteJobStore(JobStore):
    """SQLite-backed JobStore implementation."""

    def __init__(self, db_file: Path) -> None:
        self.db_file = db_file
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def create_job(self, job: JobRecord) -> None:
        payload = job.to_dict()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, repository, issue_number, issue_title, issue_url,
                        status, stage, attempt, max_attempts, branch_name, pr_url,
                        error_message, log_file, created_at, updated_at, started_at,
                        finished_at, app_code, track
                    )
                    VALUES (
                        :job_id, :repository, :issue_number, :issue_title, :issue_url,
                        :status, :stage, :attempt, :max_attempts, :branch_name, :pr_url,
                        :error_message, :log_file, :created_at, :updated_at, :started_at,
                        :finished_at, :app_code, :track
                    )
                    """,
                    payload,
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Job already exists: {job.job_id}") from error

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_job(row)

    def list_jobs(self) -> List[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def update_job(self, job_id: str, **changes: object) -> JobRecord:
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"Job not found: {job_id}")

        mutable = current.to_dict()
        mutable.update(changes)
        mutable.setdefault("updated_at", utc_now_iso())
        if "updated_at" not in changes:
            mutable["updated_at"] = utc_now_iso()

        updated = JobRecord.from_dict(mutable)
        payload = updated.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET repository=:repository,
                    issue_number=:issue_number,
                    issue_title=:issue_title,
                    issue_url=:issue_url,
                    status=:status,
                    stage=:stage,
                    attempt=:attempt,
                    max_attempts=:max_attempts,
                    branch_name=:branch_name,
                    pr_url=:pr_url,
                    error_message=:error_message,
                    log_file=:log_file,
                    created_at=:created_at,
                    updated_at=:updated_at,
                    started_at=:started_at,
                    finished_at=:finished_at,
                    app_code=:app_code,
                    track=:track
                WHERE job_id=:job_id
                """,
                payload,
            )
        return updated

    def enqueue_job(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO queue (job_id) VALUES (?)", (job_id,))

    def dequeue_job(self) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, job_id FROM queue ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
            return str(row["job_id"])

    def queue_size(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM queue").fetchone()
            return int(row["count"]) if row is not None else 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    issue_title TEXT NOT NULL,
                    issue_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    branch_name TEXT NOT NULL,
                    pr_url TEXT,
                    error_message TEXT,
                    log_file TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    app_code TEXT NOT NULL DEFAULT 'default',
                    track TEXT NOT NULL DEFAULT 'new'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)"
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=str(row["job_id"]),
            repository=str(row["repository"]),
            issue_number=int(row["issue_number"]),
            issue_title=str(row["issue_title"]),
            issue_url=str(row["issue_url"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            branch_name=str(row["branch_name"]),
            pr_url=row["pr_url"],
            error_message=row["error_message"],
            log_file=str(row["log_file"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            app_code=str(row["app_code"]),
            track=str(row["track"]),
        )


def create_job_store(settings: AppSettings) -> JobStore:
    """Create store backend based on environment settings."""

    if settings.store_backend == "sqlite":
        sqlite_store = SQLiteJobStore(settings.sqlite_file)
        if sqlite_store.list_jobs():
            return sqlite_store

        # One-time bootstrap from existing JSON files for smoother migration.
        if settings.jobs_file.exists() or settings.queue_file.exists():
            json_store = JsonJobStore(settings.jobs_file, settings.queue_file)
            for job in json_store.list_jobs():
                try:
                    sqlite_store.create_job(job)
                except ValueError:
                    continue
            queue_payload = JsonJobStore._read_json(settings.queue_file, default=[])
            for queued in JsonJobStore._ensure_queue_list(queue_payload):
                sqlite_store.enqueue_job(queued)

        return sqlite_store

    return JsonJobStore(settings.jobs_file, settings.queue_file)
