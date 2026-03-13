"""Tests for structured memory runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import JobRecord, JobStatus, utc_now_iso
from app.structured_memory_runtime import StructuredMemoryRuntime


def _make_job(job_id: str = "job-structured-memory-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=91,
        issue_title="structured memory runtime 정리",
        issue_url="https://github.com/owner/repo/issues/91",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-91-structured-memory-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
        app_code="default",
    )


def _build_runtime(
    tmp_path: Path,
    *,
    quality_calls: list[dict] | None = None,
    flags: dict[str, bool] | None = None,
) -> StructuredMemoryRuntime:
    quality_log = quality_calls if quality_calls is not None else []
    feature_flags = flags or {"memory_logging": True, "convention_extraction": True, "memory_scoring": True}

    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def upsert_jsonl_entries(path: Path, entries: list[dict], *, key_field: str) -> None:
        current: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    current.append(json.loads(line))
        merged: dict[str, dict] = {}
        ordered: list[str] = []
        for item in current + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered.append(item_id)
            merged[item_id] = item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(merged[item_id], ensure_ascii=False) for item_id in ordered) + "\n", encoding="utf-8")

    def upsert_json_history_entries(path: Path, entries: list[dict], *, key_field: str, root_key: str, max_entries: int) -> None:
        payload = read_json_file(path)
        current = payload.get(root_key, []) if isinstance(payload, dict) else []
        if not isinstance(current, list):
            current = []
        merged: dict[str, dict] = {}
        ordered: list[str] = []
        for item in current + list(entries):
            item_id = str(item.get(key_field, "")).strip()
            if not item_id:
                continue
            if item_id not in merged:
                ordered.append(item_id)
            merged[item_id] = item
        if max_entries > 0 and len(ordered) > max_entries:
            ordered = ordered[-max_entries:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({root_key: [merged[item_id] for item_id in ordered]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_json_artifact(path: Path | None, payload: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json_file(path: Path | None) -> dict:
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def read_text_file(path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    return StructuredMemoryRuntime(
        feature_enabled=lambda flag: bool(feature_flags.get(flag, False)),
        docs_file=docs_file,
        job_execution_repository=lambda job: job.source_repository or job.repository,
        upsert_jsonl_entries=upsert_jsonl_entries,
        upsert_json_history_entries=upsert_json_history_entries,
        write_json_artifact=write_json_artifact,
        write_memory_quality_artifacts=lambda **kwargs: quality_log.append(kwargs),
        read_json_file=read_json_file,
        read_text_file=read_text_file,
    )


def test_structured_memory_runtime_writes_artifacts_and_calls_quality_runtime(tmp_path: Path) -> None:
    quality_calls: list[dict] = []
    runtime = _build_runtime(tmp_path, quality_calls=quality_calls)
    job = _make_job()
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    paths = {
        "memory_log": repository_path / "_docs" / "MEMORY_LOG.jsonl",
        "decision_history": repository_path / "_docs" / "DECISION_HISTORY.json",
        "failure_patterns": repository_path / "_docs" / "FAILURE_PATTERNS.json",
        "conventions": repository_path / "_docs" / "CONVENTIONS.json",
        "memory_feedback": repository_path / "_docs" / "MEMORY_FEEDBACK.json",
        "memory_rankings": repository_path / "_docs" / "MEMORY_RANKINGS.json",
        "product_review": repository_path / "_docs" / "PRODUCT_REVIEW.json",
        "review_history": repository_path / "_docs" / "REVIEW_HISTORY.json",
        "repo_maturity": repository_path / "_docs" / "REPO_MATURITY.json",
        "quality_trend": repository_path / "_docs" / "QUALITY_TREND.json",
        "improvement_loop_state": repository_path / "_docs" / "IMPROVEMENT_LOOP_STATE.json",
        "next_improvement_tasks": repository_path / "_docs" / "NEXT_IMPROVEMENT_TASKS.json",
    }
    review_payload = {"quality_gate": {"passed": True, "categories_below_threshold": ["test_coverage"]}, "scores": {"overall": 4.1}}
    maturity_payload = {"level": "usable", "progression": "up"}
    trend_payload = {"trend_direction": "up", "delta_from_previous": 0.2, "persistent_low_categories": ["test_coverage"], "stagnant_categories": []}
    loop_state = {
        "generated_at": "2026-03-13T10:00:00+00:00",
        "strategy": "test_hardening",
        "strategy_focus": "testing",
        "next_scope_restriction": "normal",
        "categories_below_threshold": ["test_coverage"],
        "strategy_inputs": {"has_test_gap": True},
        "strategy_change_reasons": ["test coverage too low"],
    }
    next_tasks_payload = {"tasks": [{"source_issue_id": "t1", "title": "테스트 보강"}]}

    runtime.write_structured_memory_artifacts(
        job=job,
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        maturity_payload=maturity_payload,
        trend_payload=trend_payload,
        loop_state=loop_state,
        next_tasks_payload=next_tasks_payload,
    )

    memory_lines = [json.loads(line) for line in paths["memory_log"].read_text(encoding="utf-8").splitlines() if line.strip()]
    decision_payload = json.loads(paths["decision_history"].read_text(encoding="utf-8"))
    failure_payload = json.loads(paths["failure_patterns"].read_text(encoding="utf-8"))

    assert memory_lines[0]["signals"]["strategy"] == "test_hardening"
    assert decision_payload["entries"][0]["chosen_strategy"] == "test_hardening"
    assert {item["pattern_id"] for item in failure_payload["items"]} >= {"low_category:test_coverage", "persistent_low:test_coverage"}
    assert len(quality_calls) == 1
    assert quality_calls[0]["current_memory_ids"] == [
        f"episodic_job_summary:{job.job_id}",
        f"improvement_strategy:{job.job_id}",
    ]


def test_structured_memory_runtime_extracts_repo_conventions(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    job = _make_job("job-structured-memory-conventions")
    repository_path = tmp_path / "repo"
    (repository_path / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "components").mkdir(parents=True, exist_ok=True)
    (repository_path / "app").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "layout.tsx").write_text("export default function Layout() { return null }\n", encoding="utf-8")
    (repository_path / "app" / "components" / "Button.tsx").write_text("export function Button() { return <button /> }\n", encoding="utf-8")
    (repository_path / "tests" / "e2e" / "smoke.test.ts").write_text("test('smoke', async () => {})\n", encoding="utf-8")
    (repository_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "15", "react": "19", "tailwindcss": "4"},
                "devDependencies": {"@playwright/test": "1.55.0", "typescript": "5.9.0", "vitest": "3.2.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repository_path / "README.md").write_text("# Example\n", encoding="utf-8")
    conventions_path = repository_path / "_docs" / "CONVENTIONS.json"

    runtime.write_conventions_artifact(
        repository_path=repository_path,
        conventions_path=conventions_path,
        job=job,
        generated_at="2026-03-13T10:10:00+00:00",
    )

    payload = json.loads(conventions_path.read_text(encoding="utf-8"))
    rule_ids = {item["id"] for item in payload["rules"]}

    assert "nextjs" in payload["detected_stack"]
    assert "react" in payload["detected_stack"]
    assert "tailwindcss" in payload["detected_stack"]
    assert "playwright" in payload["detected_stack"]
    assert "typescript" in payload["detected_stack"]
    assert "conv_nextjs" in rule_ids
    assert "conv_tailwindcss" in rule_ids
    assert "conv_playwright" in rule_ids
    assert "conv_typescript" in rule_ids
    assert "conv_next_app_router" in rule_ids
    assert "conv_component_tsx" in rule_ids
    assert "conv_tests_e2e_dir" in rule_ids
    assert "conv_js_test_pattern" in rule_ids
