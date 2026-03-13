"""Tests for memory quality runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.memory_quality_runtime import MemoryQualityRuntime
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-memory-quality-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="메모리 quality runtime 정리",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-88-memory-quality-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
    )


def _build_runtime(tmp_path: Path) -> MemoryQualityRuntime:
    def read_json_file(path: Path | None) -> dict:
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def upsert_json_history_entries(path: Path, entries: list[dict], *, key_field: str, root_key: str, max_entries: int) -> None:
        payload = read_json_file(path)
        current_entries = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(current_entries, list):
            current_entries = []
        merged: dict[str, dict] = {}
        ordered_keys: list[str] = []
        for item in current_entries + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered_keys.append(item_id)
            merged[item_id] = item
        if max_entries > 0 and len(ordered_keys) > max_entries:
            ordered_keys = ordered_keys[-max_entries:]
        path.write_text(
            json.dumps({root_key: [merged[item_id] for item_id in ordered_keys]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return MemoryQualityRuntime(
        read_json_file=read_json_file,
        upsert_json_history_entries=upsert_json_history_entries,
        job_execution_repository=lambda job: job.source_repository or job.repository,
    )


def test_memory_quality_runtime_writes_feedback_and_rankings(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    job = _make_job()
    selection_path = tmp_path / "MEMORY_SELECTION.json"
    selection_path.write_text(
        json.dumps(
            {
                "planner_context": ["episodic_job_summary:job-memory-quality-runtime"],
                "reviewer_context": ["low_category:test_coverage"],
                "coder_context": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    feedback_path = tmp_path / "MEMORY_FEEDBACK.json"
    rankings_path = tmp_path / "MEMORY_RANKINGS.json"

    runtime.write_memory_quality_artifacts(
        job=job,
        paths={"memory_selection": selection_path},
        review_payload={"quality_gate": {"passed": True}},
        trend_payload={"delta_from_previous": 0.4, "trend_direction": "up"},
        loop_state={"quality_regression_detected": False, "score_stagnation_detected": False, "repeated_issue_limit_hit": False},
        generated_at="2026-03-13T09:00:00+00:00",
        current_memory_ids=["improvement_strategy:job-memory-quality-runtime"],
        memory_feedback_path=feedback_path,
        memory_rankings_path=rankings_path,
    )

    feedback_payload = json.loads(feedback_path.read_text(encoding="utf-8"))
    feedback_map = {item["memory_id"]: item for item in feedback_payload["entries"]}
    rankings_payload = json.loads(rankings_path.read_text(encoding="utf-8"))
    ranking_map = {item["memory_id"]: item for item in rankings_payload["items"]}

    assert feedback_map["episodic_job_summary:job-memory-quality-runtime"]["routes"] == ["planner"]
    assert feedback_map["low_category:test_coverage"]["memory_kind"] == "failure_pattern"
    assert feedback_map["improvement_strategy:job-memory-quality-runtime"]["routes"] == ["generated"]
    assert all(item["verdict"] == "promote" for item in feedback_payload["entries"])
    assert ranking_map["improvement_strategy:job-memory-quality-runtime"]["state"] in {"active", "promoted"}
    assert ranking_map["low_category:test_coverage"]["score"] >= 2.0


def test_memory_quality_runtime_builds_decay_outcome_for_regression() -> None:
    payload = MemoryQualityRuntime.build_memory_feedback_outcome(
        review_payload={"quality_gate": {"passed": False}},
        trend_payload={"delta_from_previous": -0.3, "trend_direction": "down"},
        loop_state={"quality_regression_detected": True, "score_stagnation_detected": False, "repeated_issue_limit_hit": False},
    )

    assert payload["verdict"] == "decay"
    assert payload["score_delta"] == -2
    assert payload["evidence"]["quality_regression_detected"] is True
