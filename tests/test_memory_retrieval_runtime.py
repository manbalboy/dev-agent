"""Tests for memory retrieval runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.memory_retrieval_runtime import MemoryRetrievalRuntime
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-memory-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=71,
        issue_title="메모리 retrieval 품질 정리",
        issue_url="https://github.com/owner/repo/issues/71",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-71-memory-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
    )


class _TransportResult:
    configured = False
    attempted = False
    ok = False
    detail = ""

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "attempted": self.attempted,
            "ok": self.ok,
            "detail": self.detail,
        }


class _TransportStub:
    def sync_manifest(self, _manifest: dict) -> _TransportResult:
        return _TransportResult()


def _build_runtime(read_payloads: dict[Path, dict] | None = None) -> MemoryRetrievalRuntime:
    payloads = read_payloads or {}

    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json_artifact(path: Path | None, payload: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json_file(path: Path | None) -> dict:
        if path is None:
            return {}
        if path in payloads:
            return payloads[path]
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    return MemoryRetrievalRuntime(
        feature_enabled=lambda _flag: False,
        docs_file=docs_file,
        write_json_artifact=write_json_artifact,
        job_execution_repository=lambda job: job.source_repository or job.repository,
        get_memory_runtime_store=lambda: None,
        read_json_file=read_json_file,
        append_actor_log=lambda *_args, **_kwargs: None,
        get_qdrant_shadow_transport=lambda: _TransportStub(),
    )


def test_memory_retrieval_runtime_reads_json_history_entries(tmp_path: Path) -> None:
    history_path = tmp_path / "DECISION_HISTORY.json"
    runtime = _build_runtime(
        {
            history_path: {
                "entries": [
                    {"decision_id": "d-1", "chosen_strategy": "stabilization"},
                    "skip-me",
                    {"decision_id": "d-2", "chosen_strategy": "test_hardening"},
                ]
            }
        }
    )

    entries = runtime.read_json_history_entries(history_path)

    assert entries == [
        {"decision_id": "d-1", "chosen_strategy": "stabilization"},
        {"decision_id": "d-2", "chosen_strategy": "test_hardening"},
    ]


def test_memory_retrieval_runtime_strategy_shadow_payload_diverges_with_memory_signal(tmp_path: Path) -> None:
    runtime = _build_runtime()
    job = _make_job()

    payload = runtime.build_strategy_shadow_report_payload(
        job=job,
        context_payload={
            "planner_context": [
                {
                    "id": "fp-1",
                    "kind": "failure_pattern",
                    "summary": "test gaps keep recurring",
                    "category": "test_coverage",
                },
                {
                    "id": "fp-2",
                    "kind": "failure_pattern",
                    "summary": "score_stagnation on tests",
                    "category": "test_coverage",
                },
            ],
            "reviewer_context": [],
            "coder_context": [],
        },
        rankings_map={
            "fp-1": {"state": "active", "score": 7.0, "confidence": 0.9, "usage_count": 3},
            "fp-2": {"state": "active", "score": 6.5, "confidence": 0.9, "usage_count": 2},
        },
        strategy_inputs={
            "maturity_level": "usable",
            "maturity_progression": "stable",
            "quality_trend_direction": "stable",
            "quality_gate_passed": False,
            "persistent_low_categories": ["test_coverage"],
            "stagnant_categories": [],
        },
        selected_strategy="feature_expansion",
        selected_focus="feature",
    )

    assert payload["enabled"] is True
    assert payload["selected_strategy"] == "feature_expansion"
    assert payload["shadow_strategy"] == "test_hardening"
    assert payload["diverged"] is True
    assert payload["decision_mode"] == "memory_divergence"
    assert payload["repository"] == "owner/repo"
