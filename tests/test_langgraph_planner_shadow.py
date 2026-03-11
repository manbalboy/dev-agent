"""Tests for LangGraph planner shadow trace."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.langgraph_planner_shadow import LangGraphPlannerShadowRunner
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.orchestrator import Orchestrator
from app.workflow_resume import build_workflow_artifact_paths


class SequencedPlannerRunner:
    """Return pre-seeded planner outputs for planner shadow tests."""

    def __init__(self, planner_outputs: list[str]) -> None:
        self.planner_outputs = list(planner_outputs)

    def has_template(self, template_name: str) -> bool:
        return str(template_name or "").split("__", 1)[0].rstrip("_fallback") == "planner"

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del template_name, cwd
        output = self.planner_outputs.pop(0) if self.planner_outputs else ""
        Path(variables["plan_path"]).write_text(output, encoding="utf-8")
        log_writer("[SEQUENCED_PLANNER]")
        return CommandResult(
            command="fake planner",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


def _make_job(job_id: str = "job-langgraph-planner-shadow") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="planner shadow test",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-88-shadow",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _complete_plan_text() -> str:
    return "\n".join(
        [
            "# PLAN",
            "",
            "## Task Breakdown",
            "- 핵심 사용자 흐름 구현",
            "- 회귀 테스트 추가",
            "- 운영 로그 점검 경로 정리",
            "",
            "## MVP Scope",
            "### In Scope",
            "- 일정 생성",
            "- 일정 목록",
            "- 오류 상태 표시",
            "### Out-of-Scope",
            "- 소셜 공유",
            "- 멀티테넌시",
            "",
            "## Completion Criteria",
            "- 사용자가 일정 1개를 생성하고 목록에서 확인할 수 있다.",
            "- 실패 상태에서 명시적인 안내 문구가 노출된다.",
            "- 테스트가 핵심 흐름을 덮는다.",
            "",
            "## Risk Test Strategy",
            "- pytest smoke + integration 경로를 우선 추가한다.",
            "- 로그 분석 단계에서 stale heartbeat와 API fallback을 확인한다.",
            "- 회귀 위험이 큰 저장/조회 경로를 먼저 검증한다.",
            "",
            "## Design Intent",
            "- 화면은 명확한 상태 구분을 유지한다.",
            "- 운영자 관점에서 실패 이유와 다음 조치가 바로 보여야 한다.",
            "",
            "## Extensible Architecture",
            "- planner/coder/reviewer 경계는 유지하고 모듈 경계를 분리한다.",
            "- memory/tool runtime은 adapter로 분리해서 확장 포인트를 남긴다.",
            "- workflow node와 tool registry는 설정 기반으로 유지한다.",
            "",
            "## MVP Phases",
            "### Phase 1",
            "- 기본 생성/조회 흐름",
            "### Phase 2",
            "- 테스트 및 운영 로그 정리",
            "### Phase 3",
            "- 후속 개선 backlog 연결",
            "",
            "## Delivery Notes",
            "- 구현 전에 로그/테스트/운영 추적을 먼저 확인한다.",
            "- 각 단계는 작은 단위 작업으로 끊고 바로 검증한다.",
            "",
        ]
    )


def test_langgraph_planner_shadow_runner_replays_rounds() -> None:
    runner = LangGraphPlannerShadowRunner()
    tmp_path = Path("/tmp/agenthub-langgraph-shadow-unit")
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan_path = tmp_path / "PLAN.md"
    quality_path = tmp_path / "PLAN_QUALITY.json"
    plan_path.write_text(_complete_plan_text(), encoding="utf-8")
    quality_path.write_text("{}", encoding="utf-8")

    payload = runner.run(
        rounds=[
            {
                "round": 1,
                "mode": "draft",
                "tool_requests": 1,
                "quality": {
                    "passed": False,
                    "score": 52,
                    "missing_sections": ["completion_criteria"],
                },
            },
            {
                "round": 2,
                "mode": "refine",
                "tool_requests": 0,
                "quality": {
                    "passed": True,
                    "score": 92,
                    "missing_sections": [],
                },
            },
        ],
        max_rounds=3,
        planning_mode="general",
        plan_path=plan_path,
        plan_quality_path=quality_path,
    )

    assert payload["available"] is True
    assert payload["status"] == "completed"
    assert payload["framework"] == "langgraph"
    assert payload["round_count"] == 2
    assert payload["contract_preserved"] is True
    trace = payload["trace"]
    assert trace[0]["node"] == "draft_plan"
    assert any(item["node"] == "refine_plan" for item in trace)
    assert trace[-1]["node"] == "optional_tool_request"


def test_orchestrator_writes_disabled_langgraph_planner_shadow_when_flag_off(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    job = _make_job("job-langgraph-shadow-disabled")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    paths["spec"].write_text("# SPEC\n\n- goal: planner shadow\n", encoding="utf-8")
    paths["review"].write_text("", encoding="utf-8")
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps({"flags": {"langgraph_planner_shadow": False}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, SequencedPlannerRunner([_complete_plan_text()]))
    orchestrator.feature_flags_path = feature_flags_path

    orchestrator._run_planner_graph_mvp(job, repository_path, paths, log_path)

    payload = json.loads(paths["langgraph_planner_shadow"].read_text(encoding="utf-8"))
    assert payload["enabled"] is False
    assert payload["status"] == "disabled"
    assert payload["detail"] == "feature_flag_disabled"


def test_orchestrator_writes_langgraph_planner_shadow_trace_when_enabled(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    job = _make_job("job-langgraph-shadow-enabled")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    paths["spec"].write_text("# SPEC\n\n- goal: planner shadow\n", encoding="utf-8")
    paths["review"].write_text("", encoding="utf-8")
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps({"flags": {"langgraph_planner_shadow": True}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(
        settings,
        store,
        SequencedPlannerRunner(
            [
                "# PLAN\n\n## Task Breakdown\n- first draft only\n",
                _complete_plan_text(),
            ]
        ),
    )
    orchestrator.feature_flags_path = feature_flags_path

    orchestrator._run_planner_graph_mvp(job, repository_path, paths, log_path)

    shadow_payload = json.loads(paths["langgraph_planner_shadow"].read_text(encoding="utf-8"))
    quality_payload = json.loads((repository_path / "_docs" / "PLAN_QUALITY.json").read_text(encoding="utf-8"))

    assert shadow_payload["enabled"] is True
    assert shadow_payload["available"] is True
    assert shadow_payload["status"] == "completed"
    assert shadow_payload["round_count"] == 2
    assert shadow_payload["plan_contract"]["plan_exists"] is True
    assert shadow_payload["plan_contract"]["plan_quality_exists"] is True
    assert any(item["node"] == "refine_plan" for item in shadow_payload["trace"])
    assert quality_payload["final"]["passed"] is True
