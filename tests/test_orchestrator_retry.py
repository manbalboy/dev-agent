"""Tests for orchestration retry behavior and stage order."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shlex

from app.command_runner import CommandResult
from app.models import JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.orchestrator import Orchestrator


class FakeTemplateRunner:
    """Fake AI template runner used for deterministic tests."""

    def __init__(self) -> None:
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
        return self._canonical_template_name(template_name) in {
            "planner",
            "coder",
            "reviewer",
            "copilot",
            "escalation",
            "documentation_writer",
            "commit_summary",
            "pr_summary",
        }

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        self.calls.append(template_name)
        canonical = self._canonical_template_name(template_name)

        if canonical == "planner":
            Path(variables["plan_path"]).write_text("# PLAN\n", encoding="utf-8")
        elif canonical == "coder":
            design_path = variables.get("design_path", "")
            if design_path and not Path(design_path).exists():
                Path(design_path).write_text("# DESIGN SYSTEM\n", encoding="utf-8")
        elif canonical == "reviewer":
            Path(variables["review_path"]).write_text("# REVIEW\n- [ ] TODO\n", encoding="utf-8")

        log_writer(f"[FAKE_TEMPLATE] {template_name}")
        return CommandResult(
            command=f"fake {template_name}",
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )



def _make_job(job_id: str = "job-1") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=55,
        issue_title="Retry behavior",
        issue_url="https://github.com/owner/repo/issues/55",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-55-job1",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )



def test_orchestrator_retries_three_times_and_fails(app_components):
    settings, store, _ = app_components
    job = _make_job("job-retry")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    call_count = {"value": 0}

    def always_fail(job_id: str, log_path: Path) -> None:
        call_count["value"] += 1
        raise RuntimeError("simulated stage failure")

    orchestrator._run_single_attempt = always_fail  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert call_count["value"] == 3

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED.value
    assert stored.stage == JobStage.FAILED.value
    assert stored.attempt == 3



def test_long_track_runs_three_full_rounds(app_components):
    settings, store, _ = app_components
    job = _make_job("job-long-rounds")
    job.track = "long"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    call_count = {"value": 0}

    def always_success(job_id: str, log_path: Path) -> None:
        call_count["value"] += 1

    orchestrator._run_single_attempt = always_success  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert call_count["value"] == 3

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.attempt == 3


def test_orchestrator_runs_stages_in_fixed_order(app_components):
    settings, store, _ = app_components
    job = _make_job("job-success")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _job, _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.pr_url == "https://github.com/owner/repo/pull/999"
    repo_path = settings.repository_workspace_path(job.repository, job.app_code)
    pr_body = (repo_path / "_docs" / "PR_BODY.md").read_text(encoding="utf-8")
    assert "## Deployment Preview" in pr_body
    assert "Docker Pod/Container" in pr_body
    summary_md = (repo_path / "_docs" / "CODE_CHANGE_SUMMARY.md").read_text(encoding="utf-8")
    assert "# CODE CHANGE SUMMARY" in summary_md
    assert (repo_path / "_docs" / "PRODUCT_BRIEF.md").exists()
    assert (repo_path / "_docs" / "USER_FLOWS.md").exists()
    assert (repo_path / "_docs" / "MVP_SCOPE.md").exists()
    assert (repo_path / "_docs" / "ARCHITECTURE_PLAN.md").exists()
    assert (repo_path / "_docs" / "SCAFFOLD_PLAN.md").exists()
    assert (repo_path / "_docs" / "BOOTSTRAP_REPORT.json").exists()
    assert (repo_path / "_docs" / "PRODUCT_REVIEW.json").exists()
    assert (repo_path / "_docs" / "REPO_MATURITY.json").exists()
    assert (repo_path / "_docs" / "QUALITY_TREND.json").exists()
    assert (repo_path / "_docs" / "IMPROVEMENT_PLAN.md").exists()
    assert (repo_path / "_docs" / "NEXT_IMPROVEMENT_TASKS.json").exists()
    assert (repo_path / "_docs" / "STAGE_CONTRACTS.md").exists()
    assert (repo_path / "_docs" / "STAGE_CONTRACTS.json").exists()
    assert (repo_path / "_docs" / "PIPELINE_ANALYSIS.md").exists()
    assert (repo_path / "_docs" / "PIPELINE_ANALYSIS.json").exists()
    product_review_payload = json.loads(
        (repo_path / "_docs" / "PRODUCT_REVIEW.json").read_text(encoding="utf-8")
    )
    assert product_review_payload["schema_version"] == "1.1"
    assert "validation" in product_review_payload
    assert product_review_payload["validation"]["passed"] is True
    assert "quality_signals" in product_review_payload
    assert "recommended_next_tasks" in product_review_payload
    assert "artifact_health" in product_review_payload
    assert "category_evidence" in product_review_payload
    assert "evidence_summary" in product_review_payload
    assert "principle_alignment" in product_review_payload
    assert "principle_1_mvp_first" in product_review_payload["principle_alignment"]
    assert "operating_policy" in product_review_payload
    assert "requires_scope_reset" in product_review_payload["operating_policy"]
    next_tasks_payload = json.loads(
        (repo_path / "_docs" / "NEXT_IMPROVEMENT_TASKS.json").read_text(encoding="utf-8")
    )
    assert isinstance(next_tasks_payload.get("tasks"), list)
    improvement_loop_state_payload = json.loads(
        (repo_path / "_docs" / "IMPROVEMENT_LOOP_STATE.json").read_text(encoding="utf-8")
    )
    assert "principle_enforcement" in improvement_loop_state_payload
    assert "requires_quality_focus" in improvement_loop_state_payload["principle_enforcement"]
    repo_maturity_payload = json.loads(
        (repo_path / "_docs" / "REPO_MATURITY.json").read_text(encoding="utf-8")
    )
    assert repo_maturity_payload["level"] in {"bootstrap", "mvp", "usable", "stable", "product_grade"}
    assert isinstance(repo_maturity_payload["score"], int)
    quality_trend_payload = json.loads(
        (repo_path / "_docs" / "QUALITY_TREND.json").read_text(encoding="utf-8")
    )
    assert "trend_direction" in quality_trend_payload
    assert "maturity_level" in quality_trend_payload

    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    stage_lines = [
        line.split("[STAGE] ", 1)[1].strip()
        for line in log_text.splitlines()
        if "[STAGE]" in line
    ]

    assert stage_lines == [
        JobStage.PREPARE_REPO.value,
        JobStage.READ_ISSUE.value,
        JobStage.WRITE_SPEC.value,
        JobStage.IDEA_TO_PRODUCT_BRIEF.value,
        JobStage.GENERATE_USER_FLOWS.value,
        JobStage.DEFINE_MVP_SCOPE.value,
        JobStage.ARCHITECTURE_PLANNING.value,
        JobStage.PROJECT_SCAFFOLDING.value,
        JobStage.PLAN_WITH_GEMINI.value,
        JobStage.DESIGN_WITH_CODEX.value,
        JobStage.IMPLEMENT_WITH_CODEX.value,
        JobStage.IMPLEMENT_WITH_CODEX.value,
        JobStage.SUMMARIZE_CODE_CHANGES.value,
        JobStage.TEST_AFTER_IMPLEMENT.value,
        JobStage.COMMIT_IMPLEMENT.value,
        JobStage.REVIEW_WITH_GEMINI.value,
        JobStage.PRODUCT_REVIEW.value,
        JobStage.IMPROVEMENT_STAGE.value,
        JobStage.FIX_WITH_CODEX.value,
        JobStage.TEST_AFTER_FIX.value,
        JobStage.COMMIT_FIX.value,
        JobStage.DOCUMENTATION_TASK.value,
        JobStage.PUSH_BRANCH.value,
        JobStage.CREATE_PR.value,
        JobStage.FINALIZE.value,
    ]


def test_stage_specific_tester_commands_are_used(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command="echo base",
        test_command_secondary="echo base secondary",
        test_command_implement="echo implement",
        test_command_fix="echo fix",
        test_command_secondary_implement="echo implement secondary",
        test_command_secondary_fix="echo fix secondary",
    )

    job = _make_job("job-stage-commands")
    job.track = "long"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    executed_commands: list[str] = []

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        executed_commands.append(str(command))
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _job, _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert any("echo implement" in cmd for cmd in executed_commands)
    assert any("echo implement secondary" in cmd for cmd in executed_commands)
    assert any("echo fix" in cmd for cmd in executed_commands)
    assert any("echo fix secondary" in cmd for cmd in executed_commands)


def test_workflow_tester_task_node_runs_test_stage(app_components):
    settings, store, _ = app_components
    job = _make_job("job-tester-task")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    workflow = {
        "workflow_id": "test_tester_task",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "tester_task"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    node_runs = store.list_node_runs(job.job_id)
    assert [item.node_type for item in node_runs] == [
        "gh_read_issue",
        "write_spec",
        "tester_task",
    ]
    assert all(item.status == "success" for item in node_runs)
    assert all(item.attempt == 1 for item in node_runs)

    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "[STAGE] test_after_implement" in log_text


def test_failed_workflow_node_is_persisted_in_node_runs(app_components):
    settings, store, _ = app_components
    job = _make_job("job-node-run-failed")
    job.max_attempts = 1
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    def fake_ux_stage(job_obj, repo_path, paths, log_path):
        raise RuntimeError("ux review failed")

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]

    workflow = {
        "workflow_id": "test_failed_node",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "ux_e2e_review", "title": "UX review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED.value

    node_runs = store.list_node_runs(job.job_id)
    assert [item.status for item in node_runs] == ["success", "success", "failed"]
    assert node_runs[-1].node_type == "ux_e2e_review"
    assert node_runs[-1].node_title == "UX review"
    assert node_runs[-1].error_message == "ux review failed"


def test_improvement_stage_uses_operating_policy_for_design_rebaseline(app_components):
    settings, store, _ = app_components
    job = _make_job("job-improvement-policy")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = {
        "schema_version": "1.1",
        "generated_at": utc_now_iso(),
        "job_id": job.job_id,
        "scores": {
            "code_quality": 3,
            "architecture_structure": 2,
            "maintainability": 2,
            "usability": 3,
            "ux_clarity": 3,
            "test_coverage": 3,
            "error_state_handling": 3,
            "empty_state_handling": 3,
            "loading_state_handling": 3,
            "overall": 2.78,
        },
        "score_reasons": {
            "code_quality": "ok",
            "architecture_structure": "warn",
            "maintainability": "warn",
            "usability": "ok",
            "ux_clarity": "ok",
            "test_coverage": "ok",
            "error_state_handling": "ok",
            "empty_state_handling": "ok",
            "loading_state_handling": "ok",
        },
        "findings": [{"category": "architecture_structure", "summary": "warn"}],
        "improvement_candidates": [],
        "priority_summary": {"P0": 0, "P1": 1, "P2": 0, "P3": 0},
        "recommended_next_tasks": [],
        "quality_signals": {
            "todo_items_count": 1,
            "critical_issue_keywords_detected": False,
            "test_report_count": 0,
            "test_failures_count": 0,
            "test_passes_count": 0,
            "has_product_brief": False,
            "has_user_flows": False,
            "has_mvp_scope": False,
            "has_architecture_plan": False,
            "has_ux_review": False,
        },
        "principle_alignment": {
            "principle_2_design_first": {
                "title": "설계 선행 원칙",
                "status": "blocked",
                "summary": "설계 문서 누락",
                "evidence": ["PRODUCT_BRIEF=X", "USER_FLOWS=X"],
                "enforced_by": "product-definition hard gate",
            }
        },
        "operating_policy": {
            "blocked_principles": ["principle_2_design_first"],
            "warning_principles": [],
            "runtime_principles": ["principle_6_no_repeat_same_fix"],
            "requires_design_reset": True,
            "requires_scope_reset": False,
            "requires_quality_focus": False,
        },
        "validation": {"passed": True, "errors": [], "checked_at": utc_now_iso()},
        "quality_gate": {"passed": False, "reason": "overall < 3.0"},
    }

    paths = {
        "product_review": Orchestrator._docs_file(repository_path, "PRODUCT_REVIEW.json"),
        "review_history": Orchestrator._docs_file(repository_path, "REVIEW_HISTORY.json"),
        "improvement_backlog": Orchestrator._docs_file(repository_path, "IMPROVEMENT_BACKLOG.json"),
        "improvement_loop_state": Orchestrator._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"),
        "improvement_plan": Orchestrator._docs_file(repository_path, "IMPROVEMENT_PLAN.md"),
        "next_improvement_tasks": Orchestrator._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
    }
    paths["product_review"].write_text(json.dumps(review_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["review_history"].write_text(
        json.dumps({"entries": [{"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 2.78, "top_issue_ids": ["policy_design_rebaseline"]}]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["improvement_backlog"].write_text(
        json.dumps(
            {
                "generated_at": utc_now_iso(),
                "items": [
                    {
                        "id": "issue_scope_fix",
                        "title": "설계 문서 보강",
                        "priority": "P1",
                        "reason": "설계 문서 누락",
                        "action": "문서 재정렬",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="abc123\n",
            stderr="",
            duration_seconds=0.0,
        )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    loop_state_payload = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    assert loop_state_payload["strategy"] == "design_rebaseline"
    assert loop_state_payload["next_scope_restriction"] == "MVP_redefinition"
    assert loop_state_payload["principle_enforcement"]["requires_design_reset"] is True

    next_tasks_payload = json.loads(paths["next_improvement_tasks"].read_text(encoding="utf-8"))
    assert next_tasks_payload["tasks"][0]["recommended_node_type"] == "gemini_plan"
    assert next_tasks_payload["tasks"][0]["priority"] == "P0"


def test_fix_stage_routes_to_planner_when_strategy_requires_rebaseline(app_components):
    settings, store, _ = app_components
    job = _make_job("job-fix-rebaseline")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = {
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
        "design": Orchestrator._docs_file(repository_path, "DESIGN_SYSTEM.md"),
        "design_tokens": Orchestrator._docs_file(repository_path, "DESIGN_TOKENS.json"),
        "token_handoff": Orchestrator._docs_file(repository_path, "TOKEN_HANDOFF.md"),
        "publish_handoff": Orchestrator._docs_file(repository_path, "PUBLISH_HANDOFF.md"),
        "improvement_plan": Orchestrator._docs_file(repository_path, "IMPROVEMENT_PLAN.md"),
        "improvement_loop_state": Orchestrator._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"),
        "next_improvement_tasks": Orchestrator._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
    }
    for key in ["spec", "plan", "review"]:
        paths[key].write_text(f"# {key.upper()}\n", encoding="utf-8")
    paths["improvement_loop_state"].write_text(
        json.dumps({"strategy": "design_rebaseline"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["next_improvement_tasks"].write_text(
        json.dumps({"scope_restriction": "MVP_redefinition", "tasks": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    planner_calls = {"count": 0}

    def fake_plan_stage(job_obj, repo_path, passed_paths, passed_log_path, planning_mode="general"):
        planner_calls["count"] += 1
        assert planning_mode == "dev_planning"
        assert repo_path == repository_path
        assert passed_paths["next_improvement_tasks"] == paths["next_improvement_tasks"]

    orchestrator._stage_plan_with_gemini = fake_plan_stage  # type: ignore[method-assign]

    orchestrator._stage_fix_with_codex(job, repository_path, paths, log_path)

    assert planner_calls["count"] == 1
    assert fake_runner.calls == []


def test_workflow_node_metadata_controls_planning_mode_and_agent_profile(app_components):
    settings, store, _ = app_components
    job = replace(_make_job("job-node-metadata"), attempt=1)
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = {
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
    }
    for path in paths.values():
        path.write_text("# stub\n", encoding="utf-8")

    workflow = {
        "workflow_id": "wf-node-metadata",
        "entry_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "gemini_plan",
                "title": "메타데이터 기반 플랜",
                "agent_profile": "fallback",
                "planning_mode": "big_picture",
                "notes": "fallback planner note",
            }
        ],
        "edges": [],
    }

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)
    orchestrator._workflow_context_paths = lambda _context: paths  # type: ignore[method-assign]
    orchestrator._commit_markdown_changes_after_stage = lambda *args, **kwargs: None  # type: ignore[method-assign]

    observed: dict[str, str] = {}

    def fake_plan_stage(job_obj, repo_path, passed_paths, passed_log_path, planning_mode="general"):
        observed["planning_mode"] = planning_mode
        observed["agent_profile"] = orchestrator._agent_profile
        assert repo_path == repository_path
        assert passed_paths == paths
        assert passed_log_path == log_path

    orchestrator._stage_plan_with_gemini = fake_plan_stage  # type: ignore[method-assign]
    orchestrator._agent_profile = "primary"

    orchestrator._run_workflow_pipeline(
        job,
        repository_path,
        workflow,
        workflow["nodes"],
        log_path,
    )

    assert observed["planning_mode"] == "big_picture"
    assert observed["agent_profile"] == "fallback"
    assert orchestrator._agent_profile == "primary"

    node_runs = store.list_node_runs(job.job_id)
    assert len(node_runs) == 1
    assert node_runs[0].agent_profile == "fallback"
    assert node_runs[0].status == "success"

    log_text = log_path.read_text(encoding="utf-8")
    assert "Workflow node note: fallback planner note" in log_text
    assert "Workflow node agent profile override: primary -> fallback" in log_text


def test_fix_prompt_uses_next_improvement_tasks_context(app_components):
    settings, store, _ = app_components
    job = _make_job("job-fix-next-tasks")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = {
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
        "design": Orchestrator._docs_file(repository_path, "DESIGN_SYSTEM.md"),
        "design_tokens": Orchestrator._docs_file(repository_path, "DESIGN_TOKENS.json"),
        "token_handoff": Orchestrator._docs_file(repository_path, "TOKEN_HANDOFF.md"),
        "publish_handoff": Orchestrator._docs_file(repository_path, "PUBLISH_HANDOFF.md"),
        "improvement_plan": Orchestrator._docs_file(repository_path, "IMPROVEMENT_PLAN.md"),
        "improvement_loop_state": Orchestrator._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"),
        "next_improvement_tasks": Orchestrator._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
    }
    for key in ["spec", "plan", "review"]:
        paths[key].write_text(f"# {key.upper()}\n", encoding="utf-8")
    paths["improvement_loop_state"].write_text(
        json.dumps({"strategy": "quality_hardening"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["next_improvement_tasks"].write_text(
        json.dumps(
            {
                "scope_restriction": "P1_only",
                "tasks": [
                    {"title": "에러 상태 처리 보강"},
                    {"title": "빈 상태 안내 문구 추가"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(settings, store, fake_runner)

    orchestrator._stage_fix_with_codex(job, repository_path, paths, log_path)

    prompt_text = (repository_path / "_docs" / "CODER_PROMPT_FIX.md").read_text(encoding="utf-8")
    assert "NEXT_IMPROVEMENT_TASKS.json 기반 우선 개선 항목 반영 및 테스트 안정화" in prompt_text
    assert "에러 상태 처리 보강" in prompt_text
    assert "빈 상태 안내 문구 추가" in prompt_text
    assert str(paths["next_improvement_tasks"]) in prompt_text
    assert fake_runner.calls


def test_workflow_ux_e2e_review_node_runs_stage(app_components):
    settings, store, _ = app_components
    job = _make_job("job-ux-review-task")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    called = {"ux": False}

    def fake_ux_stage(job_obj, repo_path, paths, log_path):
        called["ux"] = True
        orchestrator._set_stage(job_obj.job_id, JobStage.UX_E2E_REVIEW, log_path)
        (repo_path / "UX_REVIEW.md").write_text("# UX REVIEW\n", encoding="utf-8")

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]

    workflow = {
        "workflow_id": "test_ux_stage",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "ux_e2e_review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert called["ux"] is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value


def test_test_failure_does_not_abort_workflow(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command="run-tests-fail",
        test_command_secondary="run-tests-fail-secondary",
        test_command_implement="run-tests-fail-implement",
        test_command_fix="run-tests-fail-fix",
        test_command_secondary_implement="run-tests-fail-implement-secondary",
        test_command_secondary_fix="run-tests-fail-fix-secondary",
    )

    job = _make_job("job-test-failure-continue")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr create"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/repo/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if "run-tests-fail" in command:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="1 failed, 0 passed",
                stderr="failing by design",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )
    orchestrator._load_active_workflow = lambda _job, _log_path: None  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value
    assert stored.pr_url == "https://github.com/owner/repo/pull/999"

    repo_path = tuned_settings.repository_workspace_path(job.repository, job.app_code)
    assert (repo_path / "TEST_FAILURE_REASON_TEST_AFTER_IMPLEMENT.md").exists()
    assert (repo_path / "TEST_FAILURE_REASON_TEST_AFTER_FIX.md").exists()


def test_e2e_failure_runs_fix_loop_then_enters_review(app_components):
    settings, store, _ = app_components
    tuned_settings = replace(
        settings,
        test_command_fix="run-e2e-loop",
    )

    job = _make_job("job-e2e-loop")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    e2e_calls = {"count": 0}

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "run-e2e-loop" in command:
            e2e_calls["count"] += 1
            should_fail = e2e_calls["count"] < 3
            return CommandResult(
                command=command,
                exit_code=1 if should_fail else 0,
                stdout="1 failed" if should_fail else "1 passed",
                stderr="loop failure" if should_fail else "",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    fake_runner = FakeTemplateRunner()
    orchestrator = Orchestrator(
        settings=tuned_settings,
        store=store,
        command_templates=fake_runner,
        shell_executor=fake_shell,
    )

    workflow = {
        "workflow_id": "test_fix_loop",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "codex_fix"},
            {"id": "n4", "type": "test_after_fix"},
            {"id": "n5", "type": "gemini_review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
            {"from": "n4", "to": "n5", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert e2e_calls["count"] == 2

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.stage == JobStage.DONE.value

    log_text = (tuned_settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "[RECOVERY_MODE:after_fix_web] recoverable. Running fix + retest once." in log_text
    assert "[RECOVERY_MODE:after_fix_web] recovery attempt failed." in log_text
    assert "[STAGE] review_with_gemini" in log_text


def test_failed_safe_node_resumes_from_failed_node_only(app_components):
    settings, store, _ = app_components
    job = _make_job("job-safe-resume")
    job.max_attempts = 2
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            Path(parts[-1]).mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    ux_calls = {"count": 0}

    def fake_ux_stage(job_obj, repo_path, paths, log_path):
        ux_calls["count"] += 1
        if ux_calls["count"] == 1:
            raise RuntimeError("ux review failed")

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "test_safe_resume",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기"},
            {"id": "n2", "type": "write_spec", "title": "SPEC 작성"},
            {"id": "n3", "type": "ux_e2e_review", "title": "UX review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert ux_calls["count"] == 2

    node_runs = store.list_node_runs(job.job_id)
    assert [
        (item.attempt, item.node_type, item.status)
        for item in node_runs
    ] == [
        (1, "gh_read_issue", "success"),
        (1, "write_spec", "success"),
        (1, "ux_e2e_review", "failed"),
        (2, "ux_e2e_review", "success"),
    ]

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "Workflow resume active:" in log_text
    assert "Workflow resume reuses completed nodes: n1, n2" in log_text


def test_failed_side_effect_node_forces_full_rerun(app_components):
    settings, store, _ = app_components
    job = _make_job("job-side-effect-rerun")
    job.max_attempts = 2
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            Path(parts[-1]).mkdir(parents=True, exist_ok=True)

        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )

        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )

        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    commit_calls = {"count": 0}

    def fake_commit(job_obj, repo_path, stage, log_path, prefix):
        commit_calls["count"] += 1
        if commit_calls["count"] == 1:
            raise RuntimeError("commit failed")

    orchestrator._stage_commit = fake_commit  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "test_side_effect_rerun",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기"},
            {"id": "n2", "type": "write_spec", "title": "SPEC 작성"},
            {"id": "n3", "type": "commit_fix", "title": "최종 커밋"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert commit_calls["count"] == 2

    node_runs = store.list_node_runs(job.job_id)
    assert [
        (item.attempt, item.node_type, item.status)
        for item in node_runs
    ] == [
        (1, "gh_read_issue", "success"),
        (1, "write_spec", "success"),
        (1, "commit_fix", "failed"),
        (2, "gh_read_issue", "success"),
        (2, "write_spec", "success"),
        (2, "commit_fix", "success"),
    ]

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    log_text = (settings.logs_debug_dir / stored.log_file).read_text(encoding="utf-8")
    assert "Workflow resume skipped: failed_on_side_effect_node" in log_text


def test_auto_recovered_job_resumes_on_next_attempt(app_components):
    settings, store, _ = app_components
    job = _make_job("job-auto-recovered-resume")
    job.status = JobStatus.QUEUED.value
    job.stage = JobStage.QUEUED.value
    job.attempt = 1
    job.max_attempts = 3
    job.recovery_status = "auto_recovered"
    job.recovery_reason = "running heartbeat stale detected"
    store.create_job(job)
    store.enqueue_job(job.job_id)

    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="resume-n1",
            job_id=job.job_id,
            workflow_id="test_auto_recovered_resume",
            node_id="n1",
            node_type="gh_read_issue",
            node_title="이슈 읽기",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:01+00:00",
            finished_at="2026-03-08T00:00:02+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="resume-n2",
            job_id=job.job_id,
            workflow_id="test_auto_recovered_resume",
            node_id="n2",
            node_type="write_spec",
            node_title="SPEC 작성",
            status="success",
            attempt=1,
            started_at="2026-03-08T00:00:03+00:00",
            finished_at="2026-03-08T00:00:04+00:00",
        )
    )
    store.upsert_node_run(
        NodeRunRecord(
            node_run_id="resume-n3",
            job_id=job.job_id,
            workflow_id="test_auto_recovered_resume",
            node_id="n3",
            node_type="ux_e2e_review",
            node_title="UX review",
            status="failed",
            attempt=1,
            started_at="2026-03-08T00:00:05+00:00",
            finished_at="2026-03-08T00:00:06+00:00",
            error_message="ux review failed",
        )
    )

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")
        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            Path(parts[-1]).mkdir(parents=True, exist_ok=True)
        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
            }
            return CommandResult(
                command=command,
                exit_code=0,
                stdout=json.dumps(payload),
                stderr="",
                duration_seconds=0.0,
            )
        if "status --porcelain" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=0.0,
            )
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_seconds=0.0,
        )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    ux_calls = {"count": 0}

    def fake_ux_stage(job_obj, repo_path, paths, log_path):
        ux_calls["count"] += 1

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "test_auto_recovered_resume",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기"},
            {"id": "n2", "type": "write_spec", "title": "SPEC 작성"},
            {"id": "n3", "type": "ux_e2e_review", "title": "UX review"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert ux_calls["count"] == 1

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.attempt == 2

    node_runs = store.list_node_runs(job.job_id)
    assert [
        (item.attempt, item.node_type, item.status)
        for item in node_runs
    ] == [
        (1, "gh_read_issue", "success"),
        (1, "write_spec", "success"),
        (1, "ux_e2e_review", "failed"),
        (2, "ux_e2e_review", "success"),
    ]
