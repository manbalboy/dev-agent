"""Tests for Phase 3 memory runtime store + artifact ingest."""

from __future__ import annotations

import json
from pathlib import Path

from app.memory.runtime_ingest import ingest_memory_runtime_artifacts
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.workflow_resume import build_workflow_artifact_paths


def _make_job(job_id: str) -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=42,
        issue_title="Improve memory runtime",
        issue_url="https://github.com/owner/repo/issues/42",
        status=JobStatus.DONE.value,
        stage=JobStage.DONE.value,
        attempt=1,
        max_attempts=3,
        branch_name="agenthub/issue-42-memory-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
        app_code="web",
        workflow_id="wf-memory",
    )


def test_memory_runtime_store_upserts_entries_and_decodes_json(tmp_path: Path) -> None:
    store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")

    store.upsert_entry(
        {
            "memory_id": "episodic_job_summary:job-1",
            "memory_type": "episodic",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "job_id": "job-1",
            "issue_number": 42,
            "issue_title": "Improve memory runtime",
            "source_kind": "artifact_memory_log",
            "source_path": "/tmp/_docs/MEMORY_LOG.jsonl",
            "title": "Job 42 episodic summary",
            "summary": "Improve memory runtime | strategy=test_hardening | overall=4.20",
            "state": "promoted",
            "confidence": 0.91,
            "score": 4.0,
            "usage_count": 3,
            "positive_count": 2,
            "negative_count": 0,
            "neutral_count": 1,
            "last_verdict": "promote",
            "last_routes": ["planner", "coder"],
            "payload": {"signals": {"overall": 4.2}},
            "created_at": "2026-03-10T00:00:00+00:00",
            "updated_at": "2026-03-10T00:00:00+00:00",
            "last_used_at": "2026-03-10T00:00:00+00:00",
            "last_feedback_at": "2026-03-10T00:00:00+00:00",
        }
    )

    payload = store.get_entry("episodic_job_summary:job-1")

    assert payload is not None
    assert payload["state"] == "promoted"
    assert payload["last_routes"] == ["planner", "coder"]
    assert payload["payload"]["signals"]["overall"] == 4.2


def test_memory_runtime_ingest_populates_entries_feedback_and_retrieval_runs(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    job = _make_job("job-memory-runtime")
    store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")

    paths["memory_log"].write_text(
        json.dumps(
            {
                "memory_id": f"episodic_job_summary:{job.job_id}",
                "memory_type": "episodic",
                "job_id": job.job_id,
                "repository": job.repository,
                "execution_repository": job.repository,
                "app_code": job.app_code,
                "workflow_id": job.workflow_id,
                "issue_number": job.issue_number,
                "issue_title": job.issue_title,
                "generated_at": "2026-03-10T01:00:00+00:00",
                "signals": {
                    "strategy": "test_hardening",
                    "overall": 4.6,
                    "maturity_level": "growing",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["decision_history"].write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "decision_id": f"improvement_strategy:{job.job_id}",
                        "job_id": job.job_id,
                        "repository": job.repository,
                        "execution_repository": job.repository,
                        "app_code": job.app_code,
                        "workflow_id": job.workflow_id,
                        "generated_at": "2026-03-10T01:00:00+00:00",
                        "decision_type": "improvement_strategy",
                        "chosen_strategy": "test_hardening",
                        "strategy_focus": "quality_gates",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["failure_patterns"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:00:00+00:00",
                "items": [
                    {
                        "pattern_id": "persistent_low:test_coverage",
                        "pattern_type": "persistent_low",
                        "category": "test_coverage",
                        "trigger": "trend_persistent_low",
                        "count": 3,
                        "first_seen_at": "2026-03-01T00:00:00+00:00",
                        "last_seen_at": "2026-03-10T01:00:00+00:00",
                        "recommended_actions": ["추가 회귀 테스트 작성"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["conventions"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:00:00+00:00",
                "job_id": job.job_id,
                "repository": job.repository,
                "detected_stack": ["python", "pytest"],
                "rules": [
                    {
                        "id": "conv_pytest_file_pattern",
                        "type": "testing",
                        "rule": "Python tests follow test_*.py naming under tests/",
                        "evidence_paths": ["tests/test_memory_runtime_store.py", "tests/test_node_runs_api.py"],
                        "confidence": 0.78,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["memory_feedback"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:05:00+00:00",
                "entries": [
                    {
                        "feedback_id": f"episodic_job_summary:{job.job_id}:{job.job_id}",
                        "memory_id": f"episodic_job_summary:{job.job_id}",
                        "memory_kind": "episodic",
                        "job_id": job.job_id,
                        "repository": job.repository,
                        "app_code": job.app_code,
                        "generated_at": "2026-03-10T01:05:00+00:00",
                        "routes": ["planner", "coder"],
                        "verdict": "promote",
                        "score_delta": 2,
                        "evidence": {"quality_gate_passed": True},
                    },
                    {
                        "feedback_id": f"conv_pytest_file_pattern:{job.job_id}",
                        "memory_id": "conv_pytest_file_pattern",
                        "memory_kind": "convention",
                        "job_id": job.job_id,
                        "repository": job.repository,
                        "app_code": job.app_code,
                        "generated_at": "2026-03-10T01:05:00+00:00",
                        "routes": ["reviewer"],
                        "verdict": "keep",
                        "score_delta": 0,
                        "evidence": {"quality_gate_passed": True},
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["memory_rankings"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:05:00+00:00",
                "items": [
                    {
                        "memory_id": f"episodic_job_summary:{job.job_id}",
                        "memory_kind": "episodic",
                        "score": 4.0,
                        "usage_count": 3,
                        "positive_count": 2,
                        "negative_count": 0,
                        "neutral_count": 1,
                        "confidence": 0.91,
                        "state": "promoted",
                        "last_feedback_at": "2026-03-10T01:05:00+00:00",
                    },
                    {
                        "memory_id": f"improvement_strategy:{job.job_id}",
                        "memory_kind": "decision",
                        "score": 1.0,
                        "usage_count": 1,
                        "positive_count": 1,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "confidence": 0.63,
                        "state": "active",
                        "last_feedback_at": "2026-03-10T01:05:00+00:00",
                    },
                    {
                        "memory_id": "persistent_low:test_coverage",
                        "memory_kind": "failure_pattern",
                        "score": -1.0,
                        "usage_count": 2,
                        "positive_count": 0,
                        "negative_count": 1,
                        "neutral_count": 1,
                        "confidence": 0.42,
                        "state": "decayed",
                        "last_feedback_at": "2026-03-10T01:05:00+00:00",
                    },
                    {
                        "memory_id": "conv_pytest_file_pattern",
                        "memory_kind": "convention",
                        "score": 3.0,
                        "usage_count": 2,
                        "positive_count": 2,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "confidence": 0.88,
                        "state": "promoted",
                        "last_feedback_at": "2026-03-10T01:05:00+00:00",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["memory_selection"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:04:00+00:00",
                "job_id": job.job_id,
                "corpus_counts": {
                    "episodic": 1,
                    "decisions": 1,
                    "failure_patterns": 1,
                    "conventions": 1,
                },
                "planner_context": [f"episodic_job_summary:{job.job_id}", "conv_pytest_file_pattern"],
                "reviewer_context": ["persistent_low:test_coverage", "conv_pytest_file_pattern"],
                "coder_context": [f"improvement_strategy:{job.job_id}", f"episodic_job_summary:{job.job_id}"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["memory_context"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-10T01:04:00+00:00",
                "job_id": job.job_id,
                "repository": job.repository,
                "planner_context": [
                    {"kind": "episodic", "id": f"episodic_job_summary:{job.job_id}", "summary": "strategy=test_hardening"},
                    {"kind": "convention", "id": "conv_pytest_file_pattern", "summary": "Python tests follow test_*.py naming under tests/"},
                ],
                "reviewer_context": [
                    {"kind": "failure_pattern", "id": "persistent_low:test_coverage", "summary": "trend_persistent_low"},
                    {"kind": "convention", "id": "conv_pytest_file_pattern", "summary": "Python tests follow test_*.py naming under tests/"},
                ],
                "coder_context": [
                    {"kind": "decision", "id": f"improvement_strategy:{job.job_id}", "summary": "test_hardening"},
                    {"kind": "episodic", "id": f"episodic_job_summary:{job.job_id}", "summary": "strategy=test_hardening"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    counts = ingest_memory_runtime_artifacts(
        store,
        job=job,
        execution_repository=job.repository,
        paths=paths,
    )

    assert counts == {
        "entries": 4,
        "evidence": 2,
        "feedback": 2,
        "retrieval_runs": 3,
        "backlog_candidates": 0,
    }

    entry_map = {item["memory_id"]: item for item in store.list_entries()}
    assert set(entry_map) == {
        f"episodic_job_summary:{job.job_id}",
        f"improvement_strategy:{job.job_id}",
        "persistent_low:test_coverage",
        "conv_pytest_file_pattern",
    }
    assert entry_map[f"episodic_job_summary:{job.job_id}"]["state"] == "promoted"
    assert entry_map[f"episodic_job_summary:{job.job_id}"]["last_verdict"] == "promote"
    assert entry_map["persistent_low:test_coverage"]["state"] == "decayed"
    assert entry_map["conv_pytest_file_pattern"]["confidence"] >= 0.88
    assert entry_map["conv_pytest_file_pattern"]["state"] == "promoted"

    evidence_rows = store.list_evidence("conv_pytest_file_pattern")
    assert [item["source_path"] for item in evidence_rows] == [
        "tests/test_memory_runtime_store.py",
        "tests/test_node_runs_api.py",
    ]

    feedback_rows = store.list_feedback(memory_id=f"episodic_job_summary:{job.job_id}")
    assert len(feedback_rows) == 1
    assert feedback_rows[0]["routes"] == ["planner", "coder"]

    retrieval_runs = {item["route"]: item for item in store.list_retrieval_runs(job_id=job.job_id)}
    assert set(retrieval_runs) == {"planner", "reviewer", "coder"}
    assert retrieval_runs["planner"]["enabled"] is True
    assert retrieval_runs["planner"]["selection_ids"] == [
        f"episodic_job_summary:{job.job_id}",
        "conv_pytest_file_pattern",
    ]
    assert retrieval_runs["reviewer"]["context"][0]["id"] == "persistent_low:test_coverage"


def test_memory_runtime_ingest_populates_backlog_candidates_from_improvement_artifacts(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    job = _make_job("job-memory-backlog")
    store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")

    paths["improvement_backlog"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-11T01:00:00+00:00",
                "items": [
                    {
                        "id": "tests_regression",
                        "title": "회귀 테스트 보강",
                        "priority": "P1",
                        "reason": "테스트 저점이 반복됨",
                        "action": "핵심 흐름 회귀 테스트를 추가",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["next_improvement_tasks"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-11T01:01:00+00:00",
                "strategy": "test_hardening",
                "scope_restriction": "P1_only",
                "tasks": [
                    {
                        "task_id": "next_1",
                        "source_issue_id": "tests_regression",
                        "title": "핵심 회귀 테스트 작성",
                        "priority": "P1",
                        "reason": "품질 게이트를 안정화해야 함",
                        "action": "테스트 리포트 기준으로 빠진 시나리오를 추가",
                        "selected_by_strategy": "test_hardening",
                        "recommended_node_type": "coder_fix_from_test_report",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["quality_trend"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-11T01:02:00+00:00",
                "trend_direction": "stable",
                "delta_from_previous": 0.0,
                "review_round_count": 3,
                "persistent_low_categories": ["test_coverage"],
                "stagnant_categories": ["maintainability"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["strategy_shadow_report"].write_text(
        json.dumps(
            {
                "generated_at": "2026-03-11T01:03:00+00:00",
                "shadow_strategy": "feature_expansion",
                "decision_mode": "memory_divergence",
                "diverged": True,
                "confidence": 0.81,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    counts = ingest_memory_runtime_artifacts(
        store,
        job=job,
        execution_repository=job.repository,
        paths=paths,
    )

    assert counts["backlog_candidates"] == 5
    candidates = store.list_backlog_candidates(repository=job.repository, limit=10)
    assert [item["priority"] for item in candidates] == ["P1", "P1", "P1", "P1", "P2"]
    candidate_map = {item["candidate_id"]: item for item in candidates}
    assert candidate_map["improvement_backlog:job-memory-backlog:tests_regression"]["payload"]["source_kind"] == "improvement_backlog"
    assert candidate_map["next_improvement_task:job-memory-backlog:next_1"]["payload"]["recommended_node_type"] == "coder_fix_from_test_report"
    assert candidate_map["quality_trend_persistent_low:job-memory-backlog:test_coverage"]["payload"]["category"] == "test_coverage"
    assert candidate_map["quality_trend_stagnant:job-memory-backlog:maintainability"]["payload"]["category"] == "maintainability"
    assert candidate_map["strategy_shadow:job-memory-backlog:feature_expansion"]["payload"]["diverged"] is True


def test_memory_runtime_store_refresh_rankings_applies_staleness_and_effectiveness(tmp_path: Path) -> None:
    store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")

    store.upsert_entry(
        {
            "memory_id": "mem-promoted",
            "memory_type": "episodic",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "job_id": "job-1",
            "title": "promoted",
            "summary": "good memory",
            "baseline_score": 2.5,
            "baseline_confidence": 0.7,
            "score": 2.5,
            "confidence": 0.7,
            "updated_at": "2026-03-09T00:00:00+00:00",
        }
    )
    store.upsert_entry(
        {
            "memory_id": "mem-stale",
            "memory_type": "convention",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "job_id": "job-2",
            "title": "stale",
            "summary": "old convention",
            "baseline_score": 0.5,
            "baseline_confidence": 0.6,
            "score": 0.5,
            "confidence": 0.6,
            "updated_at": "2026-02-01T00:00:00+00:00",
        }
    )
    store.upsert_entry(
        {
            "memory_id": "mem-banned",
            "memory_type": "failure_pattern",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "job_id": "job-3",
            "title": "bad",
            "summary": "bad pattern",
            "baseline_score": -1.5,
            "baseline_confidence": 0.4,
            "score": -1.5,
            "confidence": 0.4,
            "updated_at": "2026-03-01T00:00:00+00:00",
        }
    )

    store.upsert_feedback(
        {
            "feedback_id": "fb-promoted",
            "memory_id": "mem-promoted",
            "job_id": "job-1",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "generated_at": "2026-03-10T00:00:00+00:00",
            "verdict": "promote",
            "score_delta": 2,
            "routes": ["planner"],
            "evidence": {"quality_gate_passed": True},
            "payload": {},
        }
    )
    for index in range(3):
        store.upsert_feedback(
            {
                "feedback_id": f"fb-banned-{index}",
                "memory_id": "mem-banned",
                "job_id": f"job-bad-{index}",
                "repository": "owner/repo",
                "execution_repository": "owner/repo",
                "app_code": "web",
                "workflow_id": "wf-memory",
                "generated_at": f"2026-03-0{index + 1}T00:00:00+00:00",
                "verdict": "decay",
                "score_delta": -2,
                "routes": ["reviewer"],
                "evidence": {"quality_gate_passed": False},
                "payload": {},
            }
        )

    store.upsert_retrieval_run(
        {
            "run_id": "run-1",
            "job_id": "job-1",
            "route": "planner",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "generated_at": "2026-03-10T00:00:00+00:00",
            "enabled": True,
            "selection_ids": ["mem-promoted", "mem-banned"],
            "context": [],
            "corpus_counts": {},
            "payload": {},
        }
    )
    store.upsert_retrieval_run(
        {
            "run_id": "run-2",
            "job_id": "job-2",
            "route": "reviewer",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "generated_at": "2026-03-10T12:00:00+00:00",
            "enabled": True,
            "selection_ids": ["mem-promoted", "mem-banned", "mem-banned", "mem-banned"],
            "context": [],
            "corpus_counts": {},
            "payload": {},
        }
    )

    state_counts = store.refresh_rankings(as_of="2026-03-11T00:00:00+00:00")
    entry_map = {item["memory_id"]: item for item in store.list_entries()}

    assert state_counts["promoted"] >= 1
    assert state_counts["decayed"] >= 1
    assert state_counts["banned"] >= 1

    assert entry_map["mem-promoted"]["state"] == "promoted"
    assert entry_map["mem-promoted"]["retrieval_count"] == 2
    assert entry_map["mem-promoted"]["effectiveness"] == 0.5

    assert entry_map["mem-stale"]["state"] == "decayed"
    assert entry_map["mem-stale"]["staleness_penalty"] == 2.0

    assert entry_map["mem-banned"]["state"] == "banned"
    assert entry_map["mem-banned"]["negative_count"] == 3
    assert entry_map["mem-banned"]["retrieval_count"] == 4


def test_memory_runtime_store_manual_override_persists_across_refresh(tmp_path: Path) -> None:
    store = MemoryRuntimeStore(tmp_path / "memory" / "memory_runtime.db")

    store.upsert_entry(
        {
            "memory_id": "mem-manual",
            "memory_type": "episodic",
            "repository": "owner/repo",
            "execution_repository": "owner/repo",
            "app_code": "web",
            "workflow_id": "wf-memory",
            "job_id": "job-manual",
            "title": "manual memory",
            "summary": "needs manual ban",
            "baseline_score": 3.2,
            "baseline_confidence": 0.8,
            "score": 3.2,
            "confidence": 0.8,
            "updated_at": "2026-03-10T00:00:00+00:00",
        }
    )
    store.upsert_feedback(
        {
            "feedback_id": "fb-manual",
            "memory_id": "mem-manual",
            "job_id": "job-manual",
            "generated_at": "2026-03-10T01:00:00+00:00",
            "verdict": "promote",
            "score_delta": 2.0,
            "routes": ["planner"],
        }
    )

    baseline_counts = store.refresh_rankings(as_of="2026-03-11T00:00:00+00:00")
    assert baseline_counts["promoted"] >= 1
    assert store.get_entry("mem-manual")["state"] == "promoted"

    updated = store.set_manual_override("mem-manual", state="banned", note="regression during review")
    assert updated is not None
    assert updated["state"] == "banned"
    assert updated["manual_state_override"] == "banned"
    assert updated["manual_override_note"] == "regression during review"
    assert updated["state_reason"] == "manual override: regression during review"

    counts = store.refresh_rankings(as_of="2026-03-12T00:00:00+00:00")
    assert counts["banned"] >= 1
    refreshed = store.get_entry("mem-manual")
    assert refreshed is not None
    assert refreshed["state"] == "banned"
    assert refreshed["manual_state_override"] == "banned"
    assert refreshed["state_reason"] == "manual override: regression during review"

    cleared = store.set_manual_override("mem-manual", state="", note="")
    assert cleared is not None
    assert cleared["manual_state_override"] == ""
    assert cleared["state"] == "promoted"
    assert cleared["state_reason"] == "high cumulative score"
