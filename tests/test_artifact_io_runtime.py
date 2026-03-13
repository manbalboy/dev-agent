"""Unit tests for shared artifact I/O helpers."""

from __future__ import annotations

import json

from app.artifact_io_runtime import ArtifactIoRuntime


def test_artifact_io_runtime_upsert_json_history_entries_keeps_order_and_limit(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps({"entries": [{"id": "a", "value": 1}, {"id": "b", "value": 2}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    ArtifactIoRuntime.upsert_json_history_entries(
        path,
        [{"id": "b", "value": 22}, {"id": "c", "value": 3}],
        key_field="id",
        root_key="entries",
        max_entries=2,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entries"] == [{"id": "b", "value": 22}, {"id": "c", "value": 3}]


def test_artifact_io_runtime_upsert_jsonl_entries_replaces_existing_key(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id":"a","value":1}\n{"id":"b","value":2}\n', encoding="utf-8")

    ArtifactIoRuntime.upsert_jsonl_entries(
        path,
        [{"id": "b", "value": 22}, {"id": "c", "value": 3}],
        key_field="id",
    )

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == [{"id": "a", "value": 1}, {"id": "b", "value": 22}, {"id": "c", "value": 3}]


def test_artifact_io_runtime_extract_review_todo_items_and_stable_issue_id():
    items = ArtifactIoRuntime.extract_review_todo_items(
        """
        # REVIEW
        - [ ] 첫 번째 할 일
        * [ ] 두 번째 할 일
        - [x] 완료된 항목
        """
    )

    assert items == ["첫 번째 할 일", "두 번째 할 일"]
    assert ArtifactIoRuntime.stable_issue_id("  Same Issue ") == ArtifactIoRuntime.stable_issue_id("same   issue")
