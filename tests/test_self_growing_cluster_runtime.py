from __future__ import annotations

import json
from pathlib import Path

from app.self_growing_cluster_runtime import SelfGrowingClusterRuntime


def test_cluster_runtime_marks_reduced_when_failure_count_drops(tmp_path: Path) -> None:
    runtime = SelfGrowingClusterRuntime()
    failure_patterns_path = tmp_path / "FAILURE_PATTERNS.json"
    failure_patterns_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "pattern_id": "loop_guard:repeated_issue",
                        "count": 2,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = runtime.build_cluster_recurrence(
        backlog_candidate={
            "candidate_id": "failure_pattern_cluster:job-parent:loop_guard_repeated_issue",
            "title": "반복 실패 묶음",
            "payload": {
                "source_kind": "failure_pattern_cluster",
                "pattern_id": "loop_guard_repeated_issue",
                "count": 4,
            },
        },
        failure_patterns_path=failure_patterns_path,
    )

    assert payload["active"] is True
    assert payload["status"] == "reduced"
    assert payload["baseline_count"] == 4
    assert payload["current_count"] == 2
    assert payload["delta_count"] == -2


def test_cluster_runtime_marks_insufficient_when_artifact_missing(tmp_path: Path) -> None:
    runtime = SelfGrowingClusterRuntime()
    payload = runtime.build_cluster_recurrence(
        backlog_candidate={
            "candidate_id": "failure_pattern_cluster:job-parent:loop_guard_repeated_issue",
            "title": "반복 실패 묶음",
            "payload": {
                "source_kind": "failure_pattern_cluster",
                "pattern_id": "loop_guard_repeated_issue",
                "count": 4,
            },
        },
        failure_patterns_path=tmp_path / "FAILURE_PATTERNS.json",
    )

    assert payload["active"] is True
    assert payload["status"] == "insufficient_baseline"
    assert "failure_patterns_artifact" in payload["missing"]
