"""Tests for JSON store and queue behavior."""

from __future__ import annotations

from app.models import JobRecord, JobStage, JobStatus, RuntimeInputRecord, utc_now_iso
from app.store import SQLiteJobStore



def _make_job(job_id: str = "job-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=1,
        issue_title="Sample issue",
        issue_url="https://github.com/owner/repo/issues/1",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-1-job1",
        pr_url=None,
        error_message=None,
        log_file="job-1.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_store_create_update_list_and_queue(app_components):
    _, store, _ = app_components

    first = _make_job("job-1")
    second = _make_job("job-2")

    store.create_job(first)
    store.create_job(second)

    store.enqueue_job("job-1")
    store.enqueue_job("job-2")

    assert store.queue_size() == 2
    assert store.dequeue_job() == "job-1"
    assert store.dequeue_job() == "job-2"
    assert store.dequeue_job() is None

    updated = store.update_job("job-1", status=JobStatus.RUNNING.value)
    assert updated.status == JobStatus.RUNNING.value

    jobs = store.list_jobs()
    assert len(jobs) == 2
    assert {item.job_id for item in jobs} == {"job-1", "job-2"}


def test_sqlite_store_persists_followup_job_contract_fields(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    job = JobRecord(
        job_id="job-followup",
        repository="owner/repo",
        issue_number=1,
        issue_title="[Follow-up] Sample issue",
        issue_url="https://github.com/owner/repo/issues/1",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/default/issue-1",
        pr_url=None,
        error_message=None,
        log_file="job-followup.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        job_kind="followup_backlog",
        parent_job_id="job-parent",
        backlog_candidate_id="candidate-1",
    )

    store.create_job(job)
    loaded = store.get_job("job-followup")

    assert loaded is not None
    assert loaded.job_kind == "followup_backlog"
    assert loaded.parent_job_id == "job-parent"
    assert loaded.backlog_candidate_id == "candidate-1"


def test_store_persists_runtime_input_records(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.db")
    now = utc_now_iso()
    record = RuntimeInputRecord(
        request_id="runtime-input-1",
        repository="owner/repo",
        app_code="default",
        job_id="",
        scope="app",
        key="google_maps_api_key",
        label="Google Maps API Key",
        description="지도 기능 구현에 필요",
        value_type="secret",
        env_var_name="GOOGLE_MAPS_API_KEY",
        sensitive=True,
        status="requested",
        value="",
        placeholder="나중에 입력",
        note="",
        requested_by="operator",
        requested_at=now,
        provided_at=None,
        updated_at=now,
    )

    store.upsert_runtime_input(record)
    loaded = store.get_runtime_input("runtime-input-1")

    assert loaded is not None
    assert loaded.scope == "app"
    assert loaded.key == "google_maps_api_key"
    assert loaded.status == "requested"

    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-1",
            repository="owner/repo",
            app_code="default",
            job_id="",
            scope="app",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 기능 구현에 필요",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="secret-value",
            placeholder="나중에 입력",
            note="provided later",
            requested_by="operator",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )
    listed = store.list_runtime_inputs()

    assert listed[0].status == "provided"
    assert listed[0].env_var_name == "GOOGLE_MAPS_API_KEY"
