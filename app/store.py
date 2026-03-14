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
from app.models import (
    IntegrationRegistryRecord,
    JobRecord,
    NodeRunRecord,
    PatchRunRecord,
    RuntimeInputRecord,
    utc_now_iso,
)


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

    @abstractmethod
    def upsert_node_run(self, node_run: NodeRunRecord) -> None:
        """Insert or update one workflow node execution record."""

    @abstractmethod
    def list_node_runs(self, job_id: str) -> List[NodeRunRecord]:
        """Return workflow node execution records for one job."""

    @abstractmethod
    def upsert_runtime_input(self, runtime_input: RuntimeInputRecord) -> None:
        """Insert or update one operator runtime input request/value."""

    @abstractmethod
    def get_runtime_input(self, request_id: str) -> Optional[RuntimeInputRecord]:
        """Fetch one runtime input record by ID."""

    @abstractmethod
    def list_runtime_inputs(self) -> List[RuntimeInputRecord]:
        """Return runtime input records sorted by newest first."""

    @abstractmethod
    def upsert_integration_registry_entry(self, entry: IntegrationRegistryRecord) -> None:
        """Insert or update one third-party integration registry entry."""

    @abstractmethod
    def get_integration_registry_entry(self, integration_id: str) -> Optional[IntegrationRegistryRecord]:
        """Fetch one integration registry entry by ID."""

    @abstractmethod
    def list_integration_registry_entries(self) -> List[IntegrationRegistryRecord]:
        """Return integration registry entries sorted by newest first."""

    @abstractmethod
    def upsert_patch_run(self, patch_run: PatchRunRecord) -> None:
        """Insert or update one patch/update run state."""

    @abstractmethod
    def get_patch_run(self, patch_run_id: str) -> Optional[PatchRunRecord]:
        """Fetch one patch run record by ID."""

    @abstractmethod
    def list_patch_runs(self) -> List[PatchRunRecord]:
        """Return patch runs sorted by newest first."""


class JsonJobStore(JobStore):
    """JSON-backed JobStore implementation with file locking.

    File locking is essential because API and worker run in separate processes and
    may update the same files at the same time.
    """

    def __init__(
        self,
        jobs_file: Path,
        queue_file: Path,
        node_runs_file: Path | None = None,
        runtime_inputs_file: Path | None = None,
        integrations_file: Path | None = None,
        patch_runs_file: Path | None = None,
    ) -> None:
        self.jobs_file = jobs_file
        self.queue_file = queue_file
        self.node_runs_file = node_runs_file or jobs_file.parent / "node_runs.json"
        self.runtime_inputs_file = runtime_inputs_file or jobs_file.parent / "runtime_inputs.json"
        self.integrations_file = integrations_file or jobs_file.parent / "integrations.json"
        self.patch_runs_file = patch_runs_file or jobs_file.parent / "patch_runs.json"

        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.node_runs_file.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_inputs_file.parent.mkdir(parents=True, exist_ok=True)
        self.integrations_file.parent.mkdir(parents=True, exist_ok=True)
        self.patch_runs_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.jobs_file.exists():
            self._write_json_atomic(self.jobs_file, {})
        if not self.queue_file.exists():
            self._write_json_atomic(self.queue_file, [])
        if not self.node_runs_file.exists():
            self._write_json_atomic(self.node_runs_file, {})
        if not self.runtime_inputs_file.exists():
            self._write_json_atomic(self.runtime_inputs_file, {})
        if not self.integrations_file.exists():
            self._write_json_atomic(self.integrations_file, {})
        if not self.patch_runs_file.exists():
            self._write_json_atomic(self.patch_runs_file, {})

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

    def upsert_node_run(self, node_run: NodeRunRecord) -> None:
        """Insert or update one node run record."""

        with self._locked_json(self.node_runs_file, default={}) as node_runs_data:
            node_runs = self._ensure_node_run_map(node_runs_data)
            node_runs[node_run.node_run_id] = node_run.to_dict()

    def list_node_runs(self, job_id: str) -> List[NodeRunRecord]:
        """Return node runs sorted by attempt/start time."""

        with self._locked_json(self.node_runs_file, default={}) as node_runs_data:
            node_runs = self._ensure_node_run_map(node_runs_data)
            records = [
                NodeRunRecord.from_dict(item)
                for item in node_runs.values()
                if str(item.get("job_id", "")) == job_id
            ]

        records.sort(
            key=lambda record: (
                record.attempt,
                record.started_at,
                record.node_run_id,
            )
        )
        return records

    def upsert_runtime_input(self, runtime_input: RuntimeInputRecord) -> None:
        """Insert or update one runtime input record."""

        with self._locked_json(self.runtime_inputs_file, default={}) as runtime_inputs_data:
            runtime_inputs = self._ensure_runtime_input_map(runtime_inputs_data)
            runtime_inputs[runtime_input.request_id] = runtime_input.to_dict()

    def get_runtime_input(self, request_id: str) -> Optional[RuntimeInputRecord]:
        """Return one runtime input by ID, or None if it does not exist."""

        with self._locked_json(self.runtime_inputs_file, default={}) as runtime_inputs_data:
            runtime_inputs = self._ensure_runtime_input_map(runtime_inputs_data)
            payload = runtime_inputs.get(request_id)
            if payload is None:
                return None
            return RuntimeInputRecord.from_dict(payload)

    def list_runtime_inputs(self) -> List[RuntimeInputRecord]:
        """List runtime inputs sorted by newest first."""

        with self._locked_json(self.runtime_inputs_file, default={}) as runtime_inputs_data:
            runtime_inputs = self._ensure_runtime_input_map(runtime_inputs_data)
            records = [RuntimeInputRecord.from_dict(item) for item in runtime_inputs.values()]

        records.sort(
            key=lambda record: (
                record.updated_at or record.provided_at or record.requested_at,
                record.request_id,
            ),
            reverse=True,
        )
        return records

    def upsert_integration_registry_entry(self, entry: IntegrationRegistryRecord) -> None:
        """Insert or update one integration registry entry."""

        with self._locked_json(self.integrations_file, default={}) as integrations_data:
            integrations = self._ensure_integration_map(integrations_data)
            integrations[entry.integration_id] = entry.to_dict()

    def get_integration_registry_entry(self, integration_id: str) -> Optional[IntegrationRegistryRecord]:
        """Return one integration registry entry by ID, or None if it does not exist."""

        with self._locked_json(self.integrations_file, default={}) as integrations_data:
            integrations = self._ensure_integration_map(integrations_data)
            payload = integrations.get(integration_id)
            if payload is None:
                return None
            return IntegrationRegistryRecord.from_dict(payload)

    def list_integration_registry_entries(self) -> List[IntegrationRegistryRecord]:
        """List integration registry entries sorted by newest first."""

        with self._locked_json(self.integrations_file, default={}) as integrations_data:
            integrations = self._ensure_integration_map(integrations_data)
            records = [IntegrationRegistryRecord.from_dict(item) for item in integrations.values()]

        records.sort(
            key=lambda record: (
                record.updated_at or record.created_at,
                record.integration_id,
            ),
            reverse=True,
        )
        return records

    def upsert_patch_run(self, patch_run: PatchRunRecord) -> None:
        """Insert or update one patch run record."""

        with self._locked_json(self.patch_runs_file, default={}) as patch_runs_data:
            patch_runs = self._ensure_patch_run_map(patch_runs_data)
            patch_runs[patch_run.patch_run_id] = patch_run.to_dict()

    def get_patch_run(self, patch_run_id: str) -> Optional[PatchRunRecord]:
        """Return one patch run by ID, or None if it does not exist."""

        with self._locked_json(self.patch_runs_file, default={}) as patch_runs_data:
            patch_runs = self._ensure_patch_run_map(patch_runs_data)
            payload = patch_runs.get(patch_run_id)
            if payload is None:
                return None
            return PatchRunRecord.from_dict(payload)

    def list_patch_runs(self) -> List[PatchRunRecord]:
        """List patch runs sorted by newest first."""

        with self._locked_json(self.patch_runs_file, default={}) as patch_runs_data:
            patch_runs = self._ensure_patch_run_map(patch_runs_data)
            records = [PatchRunRecord.from_dict(item) for item in patch_runs.values()]

        records.sort(
            key=lambda record: (
                record.updated_at or record.requested_at,
                record.patch_run_id,
            ),
            reverse=True,
        )
        return records

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

    @staticmethod
    def _ensure_node_run_map(raw_payload: object) -> Dict[str, Dict[str, object]]:
        """Validate that node-runs file content is a dictionary."""

        if isinstance(raw_payload, dict):
            return raw_payload
        return {}

    @staticmethod
    def _ensure_runtime_input_map(raw_payload: object) -> Dict[str, Dict[str, object]]:
        """Validate that runtime-input file content is a dictionary."""

        if isinstance(raw_payload, dict):
            return raw_payload
        return {}

    @staticmethod
    def _ensure_integration_map(raw_payload: object) -> Dict[str, Dict[str, object]]:
        """Validate that integration-registry file content is a dictionary."""

        if isinstance(raw_payload, dict):
            return raw_payload
        return {}

    @staticmethod
    def _ensure_patch_run_map(raw_payload: object) -> Dict[str, Dict[str, object]]:
        """Validate that patch-runs file content is a dictionary."""

        if isinstance(raw_payload, dict):
            return raw_payload
        return {}


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
                    finished_at, app_code, track, workflow_id, source_repository, heartbeat_at,
                    recovery_status, recovery_reason, recovery_count, last_recovered_at,
                    manual_resume_mode, manual_resume_node_id, manual_resume_requested_at,
                    manual_resume_note, job_kind, parent_job_id, backlog_candidate_id
                )
                VALUES (
                    :job_id, :repository, :issue_number, :issue_title, :issue_url,
                    :status, :stage, :attempt, :max_attempts, :branch_name, :pr_url,
                    :error_message, :log_file, :created_at, :updated_at, :started_at,
                    :finished_at, :app_code, :track, :workflow_id, :source_repository, :heartbeat_at,
                    :recovery_status, :recovery_reason, :recovery_count, :last_recovered_at,
                    :manual_resume_mode, :manual_resume_node_id, :manual_resume_requested_at,
                    :manual_resume_note, :job_kind, :parent_job_id, :backlog_candidate_id
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
                    track=:track,
                    workflow_id=:workflow_id,
                    source_repository=:source_repository,
                    heartbeat_at=:heartbeat_at,
                    recovery_status=:recovery_status,
                    recovery_reason=:recovery_reason,
                    recovery_count=:recovery_count,
                    last_recovered_at=:last_recovered_at,
                    manual_resume_mode=:manual_resume_mode,
                    manual_resume_node_id=:manual_resume_node_id,
                    manual_resume_requested_at=:manual_resume_requested_at,
                    manual_resume_note=:manual_resume_note,
                    job_kind=:job_kind,
                    parent_job_id=:parent_job_id,
                    backlog_candidate_id=:backlog_candidate_id
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

    def upsert_node_run(self, node_run: NodeRunRecord) -> None:
        payload = node_run.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO node_runs (
                    node_run_id, job_id, workflow_id, node_id, node_type,
                    node_title, status, attempt, started_at, finished_at,
                    error_message, agent_profile
                )
                VALUES (
                    :node_run_id, :job_id, :workflow_id, :node_id, :node_type,
                    :node_title, :status, :attempt, :started_at, :finished_at,
                    :error_message, :agent_profile
                )
                ON CONFLICT(node_run_id) DO UPDATE SET
                    job_id=excluded.job_id,
                    workflow_id=excluded.workflow_id,
                    node_id=excluded.node_id,
                    node_type=excluded.node_type,
                    node_title=excluded.node_title,
                    status=excluded.status,
                    attempt=excluded.attempt,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    error_message=excluded.error_message,
                    agent_profile=excluded.agent_profile
                """,
                payload,
            )

    def list_node_runs(self, job_id: str) -> List[NodeRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM node_runs
                WHERE job_id = ?
                ORDER BY attempt ASC, started_at ASC, node_run_id ASC
                """,
                (job_id,),
            ).fetchall()
            return [self._row_to_node_run(row) for row in rows]

    def upsert_runtime_input(self, runtime_input: RuntimeInputRecord) -> None:
        payload = runtime_input.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_inputs (
                    request_id, repository, app_code, job_id, scope, key,
                    label, description, value_type, env_var_name, sensitive,
                    status, value, placeholder, note, requested_by,
                    requested_at, provided_at, updated_at
                )
                VALUES (
                    :request_id, :repository, :app_code, :job_id, :scope, :key,
                    :label, :description, :value_type, :env_var_name, :sensitive,
                    :status, :value, :placeholder, :note, :requested_by,
                    :requested_at, :provided_at, :updated_at
                )
                ON CONFLICT(request_id) DO UPDATE SET
                    repository=excluded.repository,
                    app_code=excluded.app_code,
                    job_id=excluded.job_id,
                    scope=excluded.scope,
                    key=excluded.key,
                    label=excluded.label,
                    description=excluded.description,
                    value_type=excluded.value_type,
                    env_var_name=excluded.env_var_name,
                    sensitive=excluded.sensitive,
                    status=excluded.status,
                    value=excluded.value,
                    placeholder=excluded.placeholder,
                    note=excluded.note,
                    requested_by=excluded.requested_by,
                    requested_at=excluded.requested_at,
                    provided_at=excluded.provided_at,
                    updated_at=excluded.updated_at
                """,
                payload,
            )

    def get_runtime_input(self, request_id: str) -> Optional[RuntimeInputRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_inputs WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_runtime_input(row)

    def list_runtime_inputs(self) -> List[RuntimeInputRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runtime_inputs
                ORDER BY updated_at DESC, request_id DESC
                """
            ).fetchall()
            return [self._row_to_runtime_input(row) for row in rows]

    def upsert_integration_registry_entry(self, entry: IntegrationRegistryRecord) -> None:
        payload = entry.to_dict()
        payload["supported_app_types"] = json.dumps(entry.supported_app_types)
        payload["tags"] = json.dumps(entry.tags)
        payload["required_env_keys"] = json.dumps(entry.required_env_keys)
        payload["optional_env_keys"] = json.dumps(entry.optional_env_keys)
        payload["approval_required"] = int(bool(entry.approval_required))
        payload["enabled"] = int(bool(entry.enabled))
        payload["approval_status"] = str(entry.approval_status or "")
        payload["approval_note"] = str(entry.approval_note or "")
        payload["approval_updated_at"] = str(entry.approval_updated_at or "")
        payload["approval_updated_by"] = str(entry.approval_updated_by or "operator")
        payload["approval_trail_json"] = json.dumps(entry.approval_trail or [], ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_registry (
                    integration_id, display_name, category, supported_app_types, tags,
                    required_env_keys, optional_env_keys, operator_guide_markdown,
                    implementation_guide_markdown, verification_notes,
                    approval_required, enabled, created_at, updated_at,
                    approval_status, approval_note, approval_updated_at, approval_updated_by, approval_trail_json
                )
                VALUES (
                    :integration_id, :display_name, :category, :supported_app_types, :tags,
                    :required_env_keys, :optional_env_keys, :operator_guide_markdown,
                    :implementation_guide_markdown, :verification_notes,
                    :approval_required, :enabled, :created_at, :updated_at,
                    :approval_status, :approval_note, :approval_updated_at, :approval_updated_by, :approval_trail_json
                )
                ON CONFLICT(integration_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    category=excluded.category,
                    supported_app_types=excluded.supported_app_types,
                    tags=excluded.tags,
                    required_env_keys=excluded.required_env_keys,
                    optional_env_keys=excluded.optional_env_keys,
                    operator_guide_markdown=excluded.operator_guide_markdown,
                    implementation_guide_markdown=excluded.implementation_guide_markdown,
                    verification_notes=excluded.verification_notes,
                    approval_required=excluded.approval_required,
                    enabled=excluded.enabled,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    approval_status=excluded.approval_status,
                    approval_note=excluded.approval_note,
                    approval_updated_at=excluded.approval_updated_at,
                    approval_updated_by=excluded.approval_updated_by,
                    approval_trail_json=excluded.approval_trail_json
                """,
                payload,
            )

    def get_integration_registry_entry(self, integration_id: str) -> Optional[IntegrationRegistryRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_registry WHERE integration_id = ?",
                (integration_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_integration_registry_entry(row)

    def list_integration_registry_entries(self) -> List[IntegrationRegistryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM integration_registry
                ORDER BY updated_at DESC, integration_id DESC
                """
            ).fetchall()
            return [self._row_to_integration_registry_entry(row) for row in rows]

    def upsert_patch_run(self, patch_run: PatchRunRecord) -> None:
        payload = patch_run.to_dict()
        payload["details_json"] = json.dumps(patch_run.details or {}, ensure_ascii=False)
        payload["refresh_used"] = int(bool(patch_run.refresh_used))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO patch_runs (
                    patch_run_id, status, repo_root, branch, upstream_ref,
                    source_commit, target_commit, current_step_key, current_step_label,
                    current_step_index, total_steps, progress_percent, message,
                    requested_by, requested_at, updated_at, refresh_used, note, details_json
                )
                VALUES (
                    :patch_run_id, :status, :repo_root, :branch, :upstream_ref,
                    :source_commit, :target_commit, :current_step_key, :current_step_label,
                    :current_step_index, :total_steps, :progress_percent, :message,
                    :requested_by, :requested_at, :updated_at, :refresh_used, :note, :details_json
                )
                ON CONFLICT(patch_run_id) DO UPDATE SET
                    status=excluded.status,
                    repo_root=excluded.repo_root,
                    branch=excluded.branch,
                    upstream_ref=excluded.upstream_ref,
                    source_commit=excluded.source_commit,
                    target_commit=excluded.target_commit,
                    current_step_key=excluded.current_step_key,
                    current_step_label=excluded.current_step_label,
                    current_step_index=excluded.current_step_index,
                    total_steps=excluded.total_steps,
                    progress_percent=excluded.progress_percent,
                    message=excluded.message,
                    requested_by=excluded.requested_by,
                    requested_at=excluded.requested_at,
                    updated_at=excluded.updated_at,
                    refresh_used=excluded.refresh_used,
                    note=excluded.note,
                    details_json=excluded.details_json
                """,
                payload,
            )

    def get_patch_run(self, patch_run_id: str) -> Optional[PatchRunRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM patch_runs WHERE patch_run_id = ?",
                (patch_run_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_patch_run(row)

    def list_patch_runs(self) -> List[PatchRunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM patch_runs
                ORDER BY updated_at DESC, patch_run_id DESC
                """
            ).fetchall()
            return [self._row_to_patch_run(row) for row in rows]

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
                    track TEXT NOT NULL DEFAULT 'enhance',
                    workflow_id TEXT NOT NULL DEFAULT '',
                    source_repository TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT,
                    recovery_status TEXT NOT NULL DEFAULT '',
                    recovery_reason TEXT NOT NULL DEFAULT '',
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    last_recovered_at TEXT,
                    manual_resume_mode TEXT NOT NULL DEFAULT '',
                    manual_resume_node_id TEXT NOT NULL DEFAULT '',
                    manual_resume_requested_at TEXT,
                    manual_resume_note TEXT NOT NULL DEFAULT '',
                    job_kind TEXT NOT NULL DEFAULT '',
                    parent_job_id TEXT NOT NULL DEFAULT '',
                    backlog_candidate_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "workflow_id" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN workflow_id TEXT NOT NULL DEFAULT ''"
                )
            if "source_repository" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN source_repository TEXT NOT NULL DEFAULT ''"
                )
            if "heartbeat_at" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT")
            if "recovery_status" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN recovery_status TEXT NOT NULL DEFAULT ''"
                )
            if "recovery_reason" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN recovery_reason TEXT NOT NULL DEFAULT ''"
                )
            if "recovery_count" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_recovered_at" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN last_recovered_at TEXT")
            if "manual_resume_mode" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN manual_resume_mode TEXT NOT NULL DEFAULT ''"
                )
            if "manual_resume_node_id" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN manual_resume_node_id TEXT NOT NULL DEFAULT ''"
                )
            if "manual_resume_requested_at" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN manual_resume_requested_at TEXT")
            if "manual_resume_note" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN manual_resume_note TEXT NOT NULL DEFAULT ''"
                )
            if "job_kind" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN job_kind TEXT NOT NULL DEFAULT ''"
                )
            if "parent_job_id" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN parent_job_id TEXT NOT NULL DEFAULT ''"
                )
            if "backlog_candidate_id" not in columns:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN backlog_candidate_id TEXT NOT NULL DEFAULT ''"
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
                """
                CREATE TABLE IF NOT EXISTS node_runs (
                    node_run_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    node_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    node_title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_message TEXT,
                    agent_profile TEXT NOT NULL DEFAULT 'primary'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_runs_job_started ON node_runs(job_id, started_at ASC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_inputs (
                    request_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    app_code TEXT NOT NULL DEFAULT '',
                    job_id TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT 'repository',
                    key TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    value_type TEXT NOT NULL DEFAULT 'text',
                    env_var_name TEXT NOT NULL DEFAULT '',
                    sensitive INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'requested',
                    value TEXT NOT NULL DEFAULT '',
                    placeholder TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL DEFAULT 'operator',
                    requested_at TEXT NOT NULL DEFAULT '',
                    provided_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_inputs_updated ON runtime_inputs(updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_inputs_scope ON runtime_inputs(repository, app_code, job_id, scope)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS integration_registry (
                    integration_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    supported_app_types TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    required_env_keys TEXT NOT NULL DEFAULT '[]',
                    optional_env_keys TEXT NOT NULL DEFAULT '[]',
                    operator_guide_markdown TEXT NOT NULL DEFAULT '',
                    implementation_guide_markdown TEXT NOT NULL DEFAULT '',
                    verification_notes TEXT NOT NULL DEFAULT '',
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    approval_status TEXT NOT NULL DEFAULT '',
                    approval_note TEXT NOT NULL DEFAULT '',
                    approval_updated_at TEXT NOT NULL DEFAULT '',
                    approval_updated_by TEXT NOT NULL DEFAULT 'operator',
                    approval_trail_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_integration_registry_updated ON integration_registry(updated_at DESC)"
            )
            integration_columns = {
                str(row["name"] or "")
                for row in conn.execute("PRAGMA table_info(integration_registry)").fetchall()
            }
            if "approval_status" not in integration_columns:
                conn.execute("ALTER TABLE integration_registry ADD COLUMN approval_status TEXT NOT NULL DEFAULT ''")
            if "approval_note" not in integration_columns:
                conn.execute("ALTER TABLE integration_registry ADD COLUMN approval_note TEXT NOT NULL DEFAULT ''")
            if "approval_updated_at" not in integration_columns:
                conn.execute("ALTER TABLE integration_registry ADD COLUMN approval_updated_at TEXT NOT NULL DEFAULT ''")
            if "approval_updated_by" not in integration_columns:
                conn.execute("ALTER TABLE integration_registry ADD COLUMN approval_updated_by TEXT NOT NULL DEFAULT 'operator'")
            if "approval_trail_json" not in integration_columns:
                conn.execute("ALTER TABLE integration_registry ADD COLUMN approval_trail_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS patch_runs (
                    patch_run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    repo_root TEXT NOT NULL,
                    branch TEXT NOT NULL DEFAULT '',
                    upstream_ref TEXT NOT NULL DEFAULT '',
                    source_commit TEXT NOT NULL DEFAULT '',
                    target_commit TEXT NOT NULL DEFAULT '',
                    current_step_key TEXT NOT NULL DEFAULT '',
                    current_step_label TEXT NOT NULL DEFAULT '',
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL DEFAULT 'operator',
                    requested_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    refresh_used INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patch_runs_updated ON patch_runs(updated_at DESC)"
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
            workflow_id=str(row["workflow_id"] or ""),
            source_repository=str(row["source_repository"] or ""),
            heartbeat_at=row["heartbeat_at"],
            recovery_status=str(row["recovery_status"] or ""),
            recovery_reason=str(row["recovery_reason"] or ""),
            recovery_count=int(row["recovery_count"] or 0),
            last_recovered_at=row["last_recovered_at"],
            manual_resume_mode=str(row["manual_resume_mode"] or ""),
            manual_resume_node_id=str(row["manual_resume_node_id"] or ""),
            manual_resume_requested_at=row["manual_resume_requested_at"],
            manual_resume_note=str(row["manual_resume_note"] or ""),
            job_kind=str(row["job_kind"] or ""),
            parent_job_id=str(row["parent_job_id"] or ""),
            backlog_candidate_id=str(row["backlog_candidate_id"] or ""),
        )

    @staticmethod
    def _row_to_node_run(row: sqlite3.Row) -> NodeRunRecord:
        return NodeRunRecord(
            node_run_id=str(row["node_run_id"]),
            job_id=str(row["job_id"]),
            workflow_id=str(row["workflow_id"] or ""),
            node_id=str(row["node_id"]),
            node_type=str(row["node_type"]),
            node_title=str(row["node_title"] or ""),
            status=str(row["status"]),
            attempt=int(row["attempt"]),
            started_at=str(row["started_at"]),
            finished_at=row["finished_at"],
            error_message=row["error_message"],
            agent_profile=str(row["agent_profile"] or "primary"),
        )

    @staticmethod
    def _row_to_runtime_input(row: sqlite3.Row) -> RuntimeInputRecord:
        return RuntimeInputRecord(
            request_id=str(row["request_id"]),
            repository=str(row["repository"]),
            app_code=str(row["app_code"] or ""),
            job_id=str(row["job_id"] or ""),
            scope=str(row["scope"] or "repository"),
            key=str(row["key"]),
            label=str(row["label"] or ""),
            description=str(row["description"] or ""),
            value_type=str(row["value_type"] or "text"),
            env_var_name=str(row["env_var_name"] or ""),
            sensitive=bool(row["sensitive"]),
            status=str(row["status"] or "requested"),
            value=str(row["value"] or ""),
            placeholder=str(row["placeholder"] or ""),
            note=str(row["note"] or ""),
            requested_by=str(row["requested_by"] or "operator"),
            requested_at=str(row["requested_at"] or ""),
            provided_at=row["provided_at"],
            updated_at=str(row["updated_at"] or ""),
        )

    @staticmethod
    def _row_to_integration_registry_entry(row: sqlite3.Row) -> IntegrationRegistryRecord:
        def _load_string_list(value: object) -> List[str]:
            try:
                payload = json.loads(str(value or "[]"))
            except json.JSONDecodeError:
                payload = []
            if not isinstance(payload, list):
                return []
            return [str(item).strip() for item in payload if str(item).strip()]

        def _load_trail(value: object) -> List[dict]:
            try:
                payload = json.loads(str(value or "[]"))
            except json.JSONDecodeError:
                payload = []
            if not isinstance(payload, list):
                return []
            trail: List[dict] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                trail.append({str(key): item[key] for key in item.keys()})
            return trail

        return IntegrationRegistryRecord(
            integration_id=str(row["integration_id"]),
            display_name=str(row["display_name"] or ""),
            category=str(row["category"] or ""),
            supported_app_types=_load_string_list(row["supported_app_types"]),
            tags=_load_string_list(row["tags"]),
            required_env_keys=_load_string_list(row["required_env_keys"]),
            optional_env_keys=_load_string_list(row["optional_env_keys"]),
            operator_guide_markdown=str(row["operator_guide_markdown"] or ""),
            implementation_guide_markdown=str(row["implementation_guide_markdown"] or ""),
            verification_notes=str(row["verification_notes"] or ""),
            approval_required=bool(row["approval_required"]),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            approval_status=str(row["approval_status"] or ""),
            approval_note=str(row["approval_note"] or ""),
            approval_updated_at=str(row["approval_updated_at"] or ""),
            approval_updated_by=str(row["approval_updated_by"] or "operator"),
            approval_trail=_load_trail(row["approval_trail_json"]),
        )

    @staticmethod
    def _row_to_patch_run(row: sqlite3.Row) -> PatchRunRecord:
        try:
            details = json.loads(str(row["details_json"] or "{}"))
        except json.JSONDecodeError:
            details = {}
        if not isinstance(details, dict):
            details = {}
        return PatchRunRecord(
            patch_run_id=str(row["patch_run_id"]),
            status=str(row["status"] or ""),
            repo_root=str(row["repo_root"] or ""),
            branch=str(row["branch"] or ""),
            upstream_ref=str(row["upstream_ref"] or ""),
            source_commit=str(row["source_commit"] or ""),
            target_commit=str(row["target_commit"] or ""),
            current_step_key=str(row["current_step_key"] or ""),
            current_step_label=str(row["current_step_label"] or ""),
            current_step_index=int(row["current_step_index"] or 0),
            total_steps=int(row["total_steps"] or 0),
            progress_percent=int(row["progress_percent"] or 0),
            message=str(row["message"] or ""),
            requested_by=str(row["requested_by"] or "operator"),
            requested_at=str(row["requested_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            refresh_used=bool(row["refresh_used"]),
            note=str(row["note"] or ""),
            details=details,
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
                for node_run in json_store.list_node_runs(job.job_id):
                    sqlite_store.upsert_node_run(node_run)
            for runtime_input in json_store.list_runtime_inputs():
                sqlite_store.upsert_runtime_input(runtime_input)
            for entry in json_store.list_integration_registry_entries():
                sqlite_store.upsert_integration_registry_entry(entry)
            for patch_run in json_store.list_patch_runs():
                sqlite_store.upsert_patch_run(patch_run)
            queue_payload = JsonJobStore._read_json(settings.queue_file, default=[])
            for queued in JsonJobStore._ensure_queue_list(queue_payload):
                sqlite_store.enqueue_job(queued)

        return sqlite_store

    return JsonJobStore(settings.jobs_file, settings.queue_file)
