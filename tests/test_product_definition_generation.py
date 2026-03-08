"""Tests for product-definition prompt context and refinement behavior."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.orchestrator import Orchestrator
from app.prompt_builder import (
    build_architecture_plan_prompt,
    build_mvp_scope_prompt,
    build_product_brief_prompt,
    build_user_flows_prompt,
)


class SequencedPlannerRunner:
    """Return pre-seeded planner outputs for deterministic refinement tests."""

    def __init__(self, planner_outputs: list[str]) -> None:
        self.planner_outputs = list(planner_outputs)
        self.calls: list[str] = []

    @staticmethod
    def _canonical_template_name(template_name: str) -> str:
        name = str(template_name).strip()
        if name.endswith("_fallback"):
            name = name[: -len("_fallback")]
        if "__" in name:
            name = name.split("__", 1)[0]
        return name

    def has_template(self, template_name: str) -> bool:
        return self._canonical_template_name(template_name) == "planner"

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        self.calls.append(template_name)
        if self._canonical_template_name(template_name) != "planner":
            raise AssertionError(f"unexpected template: {template_name}")
        output = self.planner_outputs.pop(0) if self.planner_outputs else ""
        Path(variables["plan_path"]).write_text(output, encoding="utf-8")
        log_writer(f"[SEQUENCED_PLANNER] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )


def _make_job(job_id: str = "job-product-def") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=77,
        issue_title="반려동물 일정 추적 MVP",
        issue_url="https://github.com/owner/repo/issues/77",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-77-job-product-def",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def test_product_definition_prompts_embed_source_contents(tmp_path: Path):
    spec_path = tmp_path / "SPEC.md"
    product_brief_path = tmp_path / "PRODUCT_BRIEF.md"
    user_flows_path = tmp_path / "USER_FLOWS.md"
    spec_json_path = tmp_path / "SPEC.json"
    mvp_scope_path = tmp_path / "MVP_SCOPE.md"
    architecture_plan_path = tmp_path / "ARCHITECTURE_PLAN.md"

    spec_path.write_text("# SPEC\n\n- Goal: 반려동물 일정 추적 MVP\n- Scope: 알림, 일정 등록\n", encoding="utf-8")
    product_brief_path.write_text("# PRODUCT BRIEF\n\n## Product Goal\n- 일정 누락 방지\n", encoding="utf-8")
    user_flows_path.write_text("# USER FLOWS\n\n## Primary Flow\n1. 일정 등록\n", encoding="utf-8")
    spec_json_path.write_text(
        json.dumps(
            {
                "goal": "반려동물 일정 추적 MVP",
                "scope_in": ["일정 등록", "알림 확인"],
                "scope_out": ["소셜 공유"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    mvp_scope_path.write_text("# MVP SCOPE\n\n## In Scope\n- 일정 등록\n", encoding="utf-8")

    job_id = "job-inline"
    issue_title = "반려동물 일정 추적 MVP"

    product_prompt = build_product_brief_prompt(
        str(spec_path),
        str(tmp_path / "OUT_PRODUCT_BRIEF.md"),
        job_id=job_id,
        issue_title=issue_title,
    )
    assert "Goal: 반려동물 일정 추적 MVP" in product_prompt
    assert f"Job ID: {job_id}" in product_prompt
    assert f"Issue Title: {issue_title}" in product_prompt

    flows_prompt = build_user_flows_prompt(
        str(product_brief_path),
        str(tmp_path / "OUT_USER_FLOWS.md"),
        job_id=job_id,
        issue_title=issue_title,
    )
    assert "일정 누락 방지" in flows_prompt
    assert f"Job ID: {job_id}" in flows_prompt

    mvp_prompt = build_mvp_scope_prompt(
        str(product_brief_path),
        str(user_flows_path),
        str(spec_json_path),
        str(tmp_path / "OUT_MVP_SCOPE.md"),
        job_id=job_id,
        issue_title=issue_title,
    )
    assert '"scope_in": [' in mvp_prompt
    assert "일정 등록" in mvp_prompt
    assert f"Issue Title: {issue_title}" in mvp_prompt

    architecture_prompt = build_architecture_plan_prompt(
        str(mvp_scope_path),
        str(user_flows_path),
        str(architecture_plan_path),
        job_id=job_id,
        issue_title=issue_title,
    )
    assert "# MVP SCOPE" in architecture_prompt
    assert "1. 일정 등록" in architecture_prompt
    assert f"Job ID: {job_id}" in architecture_prompt


def test_product_brief_stage_retries_once_with_refinement_feedback(app_components):
    settings, store, _ = app_components
    job = _make_job("job-product-brief-retry")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = {
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "spec_json": Orchestrator._docs_file(repository_path, "SPEC.json"),
        "product_brief": Orchestrator._docs_file(repository_path, "PRODUCT_BRIEF.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
    }
    paths["spec"].write_text(
        "# SPEC\n\n- Goal: 반려동물 일정 추적 MVP\n- Scope In: 일정 등록, 알림 확인\n",
        encoding="utf-8",
    )
    paths["spec_json"].write_text(
        json.dumps(
            {
                "goal": "반려동물 일정 추적 MVP",
                "scope_in": ["일정 등록", "알림 확인"],
                "scope_out": ["소셜 공유"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    runner = SequencedPlannerRunner(
        [
            "# PRODUCT BRIEF\n\n## Product Goal\n- 일정 추적\n",
            "\n".join(
                [
                    "# PRODUCT BRIEF",
                    "",
                    "## Context Anchor",
                    f"- Job ID: {job.job_id}",
                    f"- Issue Title: {job.issue_title}",
                    "",
                    "## Product Goal",
                    "- 반려동물 일정 누락을 줄이는 MVP를 만든다.",
                    "",
                    "## Problem Statement",
                    "- 보호자가 예방접종과 산책 일정을 자주 놓친다.",
                    "",
                    "## Target Users",
                    "- 1차 사용자: 반려동물 보호자",
                    "- 2차 사용자: 운영자",
                    "",
                    "## Core Value",
                    "- 일정 등록과 알림 확인을 한 번에 처리한다.",
                    "",
                    "## Scope Inputs",
                    "- 일정 등록",
                    "- 알림 확인",
                    "",
                    "## Success Metrics",
                    "- 사용자가 일정 1개를 등록하고 알림 상태를 확인할 수 있다.",
                    "",
                    "## Non-Goals",
                    "- 소셜 공유",
                    "",
                ]
            ),
        ]
    )
    orchestrator = Orchestrator(settings, store, runner)

    orchestrator._stage_idea_to_product_brief(job, repository_path, paths, log_path)

    assert len(runner.calls) == 2
    prompt_text = (repository_path / "_docs" / "PRODUCT_BRIEF_PROMPT.md").read_text(encoding="utf-8")
    assert "반려동물 일정 추적 MVP" in prompt_text
    assert "이전 출력 보정 지시" in prompt_text
    output_text = paths["product_brief"].read_text(encoding="utf-8")
    assert f"Job ID: {job.job_id}" in output_text
    assert f"Issue Title: {job.issue_title}" in output_text


def test_user_flows_stage_fallback_requires_issue_linkage(app_components):
    settings, store, _ = app_components
    job = _make_job("job-user-flows-fallback")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = {
        "product_brief": Orchestrator._docs_file(repository_path, "PRODUCT_BRIEF.md"),
        "user_flows": Orchestrator._docs_file(repository_path, "USER_FLOWS.md"),
        "spec_json": Orchestrator._docs_file(repository_path, "SPEC.json"),
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
    }
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["product_brief"].write_text(
        "\n".join(
            [
                "# PRODUCT BRIEF",
                "",
                "## Context Anchor",
                f"- Job ID: {job.job_id}",
                f"- Issue Title: {job.issue_title}",
                "",
                "## Product Goal",
                "- 반려동물 일정 누락을 줄인다.",
                "",
                "## Target Users",
                "- 반려동물 보호자",
                "",
                "## Core Value",
                "- 일정 등록과 알림 확인을 간단하게 처리한다.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    paths["spec_json"].write_text(
        json.dumps(
            {"scope_in": ["일정 등록", "알림 확인"]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    runner = SequencedPlannerRunner(
        [
            "# USER FLOWS\n\n## Primary Flow\n1. 시작\n",
            "# USER FLOWS\n\n## Primary Flow\n1. 다시 시작\n",
        ]
    )
    orchestrator = Orchestrator(settings, store, runner)

    orchestrator._stage_generate_user_flows(job, repository_path, paths, log_path)

    assert len(runner.calls) == 2
    output_text = paths["user_flows"].read_text(encoding="utf-8")
    assert f"Job ID: {job.job_id}" in output_text
    assert f"Issue Title: {job.issue_title}" in output_text
    assert "일정 등록" in output_text
    assert "UX State Checklist" in output_text
