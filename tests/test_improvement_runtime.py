"""Tests for improvement runtime extraction."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.improvement_runtime import ImprovementRuntime
from app.models import JobRecord, JobStatus, utc_now_iso


def _make_job(job_id: str = "job-improvement-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=52,
        issue_title="모바일 추천 흐름 안정화",
        issue_url="https://github.com/owner/repo/issues/52",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-52-improvement-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(logs: list[str], callback_log: list[str]) -> ImprovementRuntime:
    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def read_json_file(path: Path | None) -> dict:
        if path is None or not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def execute_shell_command(*args, **kwargs):
        return CommandResult(
            command=str(kwargs.get("command", "")),
            exit_code=0,
            stdout="abc123\n",
            stderr="",
            duration_seconds=0.0,
        )

    return ImprovementRuntime(
        set_stage=lambda *args, **kwargs: None,
        docs_file=docs_file,
        read_json_file=read_json_file,
        execute_shell_command=execute_shell_command,
        actor_log_writer=lambda *args, **kwargs: (lambda message: None),
        append_actor_log=lambda log_path, actor, message: logs.append(f"{actor}:{message}"),
        write_structured_memory_artifacts=lambda **kwargs: callback_log.append("structured"),
        write_memory_retrieval_artifacts=lambda **kwargs: callback_log.append("retrieval"),
        write_strategy_shadow_report=lambda **kwargs: callback_log.append("shadow"),
        ingest_memory_runtime_artifacts=lambda **kwargs: callback_log.append("ingest"),
        build_improvement_strategy_inputs=lambda **kwargs: ImprovementRuntime.build_improvement_strategy_inputs(**kwargs),
        select_improvement_strategy=lambda **kwargs: ImprovementRuntime.select_improvement_strategy(**kwargs),
        select_next_improvement_items=lambda **kwargs: ImprovementRuntime.select_next_improvement_items(**kwargs),
    )


def test_improvement_runtime_builds_strategy_inputs_with_test_gap() -> None:
    payload = ImprovementRuntime.build_improvement_strategy_inputs(
        review_payload={
            "scores": {
                "overall": 3.1,
                "test_coverage": 2,
                "usability": 3,
                "ux_clarity": 3,
                "error_state_handling": 3,
                "empty_state_handling": 3,
                "loading_state_handling": 3,
            },
            "artifact_health": {"tests": {"test_file_count": 0, "report_count": 0}},
            "quality_gate": {"passed": False},
        },
        maturity_payload={"level": "mvp"},
        trend_payload={"trend_direction": "stable", "review_round_count": 2},
        categories_below=["test_coverage"],
    )

    assert payload["has_test_gap"] is True
    assert payload["quality_gate_passed"] is False
    assert payload["maturity_level"] == "mvp"


def test_improvement_runtime_stage_writes_design_rebaseline_plan(tmp_path: Path) -> None:
    logs: list[str] = []
    callback_log: list[str] = []
    runtime = _build_runtime(logs, callback_log)
    job = _make_job()
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "job.log"

    paths = {
        "product_review": docs_dir / "PRODUCT_REVIEW.json",
        "review_history": docs_dir / "REVIEW_HISTORY.json",
        "improvement_backlog": docs_dir / "IMPROVEMENT_BACKLOG.json",
        "repo_maturity": docs_dir / "REPO_MATURITY.json",
        "quality_trend": docs_dir / "QUALITY_TREND.json",
        "improvement_loop_state": docs_dir / "IMPROVEMENT_LOOP_STATE.json",
        "next_improvement_tasks": docs_dir / "NEXT_IMPROVEMENT_TASKS.json",
        "improvement_plan": docs_dir / "IMPROVEMENT_PLAN.md",
    }
    paths["product_review"].write_text(
        json.dumps(
            {
                "scores": {"overall": 3.0},
                "quality_gate": {"passed": False, "categories_below_threshold": ["code_quality"]},
                "artifact_health": {},
                "operating_policy": {"requires_design_reset": True},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["review_history"].write_text(
        json.dumps({"entries": [{"overall": 3.1, "top_issue_ids": ["design"]}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["improvement_backlog"].write_text(
        json.dumps({"items": [{"id": "design-1", "priority": "P0", "title": "설계 재정렬"}]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    paths["repo_maturity"].write_text(json.dumps({"level": "mvp"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["quality_trend"].write_text(
        json.dumps({"trend_direction": "stable", "review_round_count": 1}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    runtime.stage_improvement_stage(
        job=job,
        repository_path=repository_path,
        paths=paths,
        log_path=log_path,
    )

    loop_state = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    next_tasks = json.loads(paths["next_improvement_tasks"].read_text(encoding="utf-8"))
    plan_text = paths["improvement_plan"].read_text(encoding="utf-8")

    assert loop_state["strategy"] == "design_rebaseline"
    assert next_tasks["tasks"][0]["recommended_node_type"] == "gemini_plan"
    assert "## Strategy Change Reasons" in plan_text
    assert callback_log == ["structured", "retrieval", "shadow", "ingest"]
    assert any("IMPROVEMENT_PLAN.md 생성 완료" in item for item in logs)
