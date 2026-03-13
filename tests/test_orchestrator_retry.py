"""Tests for orchestration retry behavior and stage order."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import pytest
import shlex

from app.ai_role_routing import AIRoleRouter, default_ai_role_routing_payload
from app.command_runner import CommandResult
from app.command_runner import CommandExecutionError
from app.memory.runtime_store import MemoryRuntimeStore
from app.models import IntegrationRegistryRecord, JobRecord, JobStage, JobStatus, NodeRunRecord, utc_now_iso
from app.orchestrator import IssueDetails, Orchestrator
from app.models import RuntimeInputRecord
from app.workflow_resume import build_workflow_artifact_paths


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
            "codex_helper",
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


def test_orchestrator_passes_heartbeat_hooks_to_shell_executor(app_components, tmp_path: Path) -> None:
    settings, store, _ = app_components
    captured: dict[str, object] = {}

    def fake_shell(
        command,
        cwd,
        log_writer,
        check,
        command_purpose,
        heartbeat_callback=None,
        heartbeat_interval_seconds=None,
    ):
        captured["command"] = command
        captured["heartbeat_callback"] = heartbeat_callback
        captured["heartbeat_interval_seconds"] = heartbeat_interval_seconds
        log_writer("[DONE] exit_code=0 elapsed=0.00s")
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

    orchestrator._run_shell(
        command="echo heartbeat",
        cwd=tmp_path,
        log_path=tmp_path / "heartbeat.log",
        purpose="heartbeat smoke",
    )

    assert captured["command"] == "echo heartbeat"
    assert callable(captured["heartbeat_callback"])
    assert captured["heartbeat_interval_seconds"] == 10.0


def test_orchestrator_resolves_runtime_inputs_into_env_and_prompt_safe_artifact(app_components) -> None:
    settings, store, _ = app_components
    job = _make_job("job-runtime-input-orchestrator")
    store.create_job(job)
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-secret",
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            scope="job",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 기능 구현용",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="secret-value-123",
            placeholder="",
            note="provided",
            requested_by="operator",
            requested_at=utc_now_iso(),
            provided_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
    )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
    )
    orchestrator._set_active_runtime_input_environment(job)

    assert orchestrator.command_templates.extra_env["GOOGLE_MAPS_API_KEY"] == "secret-value-123"

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    variables = orchestrator._build_template_variables(
        job,
        paths,
        repository_path / "_docs" / "PROMPT.md",
    )

    artifact_path = Path(variables["operator_inputs_path"])
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["available_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["resolved_inputs"][0]["env_var_name"] == "GOOGLE_MAPS_API_KEY"
    assert payload["resolved_inputs"][0]["value"] == ""
    assert payload["resolved_inputs"][0]["display_value"] != ""


def test_orchestrator_blocks_runtime_input_env_when_integration_not_approved(app_components) -> None:
    settings, store, _ = app_components
    job = _make_job("job-runtime-input-blocked")
    job.app_code = "maps"
    store.create_job(job)
    now = utc_now_iso()
    store.upsert_runtime_input(
        RuntimeInputRecord(
            request_id="runtime-input-secret-blocked",
            repository=job.repository,
            app_code=job.app_code,
            job_id=job.job_id,
            scope="job",
            key="google_maps_api_key",
            label="Google Maps API Key",
            description="지도 기능 구현용",
            value_type="secret",
            env_var_name="GOOGLE_MAPS_API_KEY",
            sensitive=True,
            status="provided",
            value="secret-value-123",
            placeholder="",
            note="provided",
            requested_by="operator",
            requested_at=now,
            provided_at=now,
            updated_at=now,
        )
    )
    store.upsert_integration_registry_entry(
        IntegrationRegistryRecord(
            integration_id="google_maps",
            display_name="Google Maps",
            category="mapping",
            supported_app_types=["web", "app"],
            tags=["maps"],
            required_env_keys=["GOOGLE_MAPS_API_KEY"],
            optional_env_keys=[],
            operator_guide_markdown="",
            implementation_guide_markdown="",
            verification_notes="",
            approval_required=True,
            enabled=True,
            created_at=now,
            updated_at=now,
            approval_status="pending",
        )
    )

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
    )
    orchestrator._set_active_runtime_input_environment(job)

    assert orchestrator.command_templates.extra_env == {}

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = build_workflow_artifact_paths(repository_path)
    variables = orchestrator._build_template_variables(
        job,
        paths,
        repository_path / "_docs" / "PROMPT.md",
    )

    artifact_path = Path(variables["operator_inputs_path"])
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["available_env_vars"] == []
    assert payload["blocked_env_vars"] == ["GOOGLE_MAPS_API_KEY"]
    assert payload["blocked_inputs"][0]["bridge_allowed"] is False


def _improvement_paths(repository_path: Path) -> dict[str, Path]:
    return {
        "product_review": Orchestrator._docs_file(repository_path, "PRODUCT_REVIEW.json"),
        "review_history": Orchestrator._docs_file(repository_path, "REVIEW_HISTORY.json"),
        "improvement_backlog": Orchestrator._docs_file(repository_path, "IMPROVEMENT_BACKLOG.json"),
        "improvement_loop_state": Orchestrator._docs_file(repository_path, "IMPROVEMENT_LOOP_STATE.json"),
        "improvement_plan": Orchestrator._docs_file(repository_path, "IMPROVEMENT_PLAN.md"),
        "next_improvement_tasks": Orchestrator._docs_file(repository_path, "NEXT_IMPROVEMENT_TASKS.json"),
        "repo_maturity": Orchestrator._docs_file(repository_path, "REPO_MATURITY.json"),
        "quality_trend": Orchestrator._docs_file(repository_path, "QUALITY_TREND.json"),
        "memory_log": Orchestrator._docs_file(repository_path, "MEMORY_LOG.jsonl"),
        "decision_history": Orchestrator._docs_file(repository_path, "DECISION_HISTORY.json"),
        "failure_patterns": Orchestrator._docs_file(repository_path, "FAILURE_PATTERNS.json"),
        "conventions": Orchestrator._docs_file(repository_path, "CONVENTIONS.json"),
        "memory_selection": Orchestrator._docs_file(repository_path, "MEMORY_SELECTION.json"),
        "memory_context": Orchestrator._docs_file(repository_path, "MEMORY_CONTEXT.json"),
        "memory_trace": Orchestrator._docs_file(repository_path, "MEMORY_TRACE.json"),
        "vector_shadow_index": Orchestrator._docs_file(repository_path, "VECTOR_SHADOW_INDEX.json"),
        "memory_feedback": Orchestrator._docs_file(repository_path, "MEMORY_FEEDBACK.json"),
        "memory_rankings": Orchestrator._docs_file(repository_path, "MEMORY_RANKINGS.json"),
        "strategy_shadow_report": Orchestrator._docs_file(repository_path, "STRATEGY_SHADOW_REPORT.json"),
    }


def _base_review_payload(job_id: str, *, overall: float = 3.4) -> dict:
    return {
        "schema_version": "1.1",
        "generated_at": utc_now_iso(),
        "job_id": job_id,
        "scores": {
            "code_quality": 4,
            "architecture_structure": 4,
            "maintainability": 4,
            "usability": 4,
            "ux_clarity": 4,
            "test_coverage": 4,
            "error_state_handling": 4,
            "empty_state_handling": 4,
            "loading_state_handling": 4,
            "overall": overall,
        },
        "score_reasons": {
            "code_quality": "ok",
            "architecture_structure": "ok",
            "maintainability": "ok",
            "usability": "ok",
            "ux_clarity": "ok",
            "test_coverage": "ok",
            "error_state_handling": "ok",
            "empty_state_handling": "ok",
            "loading_state_handling": "ok",
        },
        "findings": [],
        "improvement_candidates": [],
        "priority_summary": {"P0": 0, "P1": 1, "P2": 1, "P3": 0},
        "recommended_next_tasks": [],
        "artifact_health": {
            "tests": {"test_file_count": 2, "report_count": 1},
        },
        "quality_signals": {
            "todo_items_count": 0,
            "critical_issue_keywords_detected": False,
            "test_report_count": 1,
            "test_failures_count": 0,
            "test_passes_count": 5,
            "has_product_brief": True,
            "has_user_flows": True,
            "has_mvp_scope": True,
            "has_architecture_plan": True,
            "has_ux_review": True,
        },
        "principle_alignment": {},
        "operating_policy": {
            "blocked_principles": [],
            "warning_principles": [],
            "runtime_principles": ["principle_6_no_repeat_same_fix"],
            "requires_design_reset": False,
            "requires_scope_reset": False,
            "requires_quality_focus": False,
        },
        "validation": {"passed": True, "errors": [], "checked_at": utc_now_iso()},
        "quality_gate": {"passed": True, "categories_below_threshold": []},
    }


def _write_improvement_stage_inputs(
    *,
    repository_path: Path,
    paths: dict[str, Path],
    review_payload: dict,
    history_entries: list[dict],
    backlog_items: list[dict],
    maturity_payload: dict,
    trend_payload: dict,
) -> None:
    paths["product_review"].write_text(json.dumps(review_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["review_history"].write_text(
        json.dumps({"entries": history_entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["improvement_backlog"].write_text(
        json.dumps({"generated_at": utc_now_iso(), "items": backlog_items}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["repo_maturity"].write_text(json.dumps(maturity_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["quality_trend"].write_text(json.dumps(trend_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



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
    review_history_payload = json.loads(
        (repo_path / "_docs" / "REVIEW_HISTORY.json").read_text(encoding="utf-8")
    )
    assert isinstance(review_history_payload.get("entries"), list)
    assert isinstance(review_history_payload["entries"][-1].get("scores"), dict)
    assert "test_coverage" in review_history_payload["entries"][-1]["scores"]
    quality_trend_payload = json.loads(
        (repo_path / "_docs" / "QUALITY_TREND.json").read_text(encoding="utf-8")
    )
    assert "trend_direction" in quality_trend_payload
    assert "maturity_level" in quality_trend_payload
    assert "category_deltas" in quality_trend_payload
    assert "persistent_low_categories" in quality_trend_payload
    assert "stagnant_categories" in quality_trend_payload

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


def test_orchestrator_uses_source_repository_for_clone_and_pr(app_components):
    settings, store, _ = app_components
    job = _make_job("job-source-repo")
    job.source_repository = "manbalboy/Food"
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
                stdout="https://github.com/manbalboy/Food/pull/999\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh pr view"):
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/manbalboy/Food/pull/999\n",
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
    assert any(cmd.startswith("gh repo clone manbalboy/Food ") for cmd in executed_commands)
    assert any("gh issue view 55 --repo owner/repo" in cmd for cmd in executed_commands)
    assert any("gh pr create --repo manbalboy/Food " in cmd for cmd in executed_commands)
    repo_path = settings.repository_workspace_path(job.source_repository, job.app_code)
    pr_body = (repo_path / "_docs" / "PR_BODY.md").read_text(encoding="utf-8")
    assert "Tracking issue: https://github.com/owner/repo/issues/55" in pr_body


def test_prepare_repo_reclones_when_workspace_exists_without_git_metadata(app_components):
    settings, store, _ = app_components
    job = _make_job("job-prepare-reclone")
    job.repository = "owner/hub"
    job.source_repository = "owner/source-repo"
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.source_repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "README.tmp").write_text("orphan workspace\n", encoding="utf-8")
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    executed_commands: list[str] = []

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        executed_commands.append(str(command))
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(parents=True, exist_ok=True)

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

    prepared_path = orchestrator._stage_prepare_repo(job, log_path)

    assert prepared_path == repository_path
    assert (prepared_path / ".git").exists()
    assert not (prepared_path / "README.tmp").exists()

    backup_dirs = list(repository_path.parent.glob(f"{repository_path.name}__invalid_*"))
    assert len(backup_dirs) == 1
    assert (backup_dirs[0] / "README.tmp").exists()
    assert any(
        command.startswith("gh repo clone owner/source-repo ")
        for command in executed_commands
    )
    assert any("git -C" in command and "fetch origin" in command for command in executed_commands)


def test_prepare_repo_reclones_when_workspace_origin_mismatches_execution_repository(app_components):
    settings, store, _ = app_components
    job = _make_job("job-prepare-origin-mismatch")
    job.repository = "owner/hub"
    job.source_repository = "owner/source-repo"
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.source_repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / ".git").mkdir(parents=True, exist_ok=True)
    (repository_path / "README.tmp").write_text("wrong repo workspace\n", encoding="utf-8")
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    executed_commands: list[str] = []

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        executed_commands.append(str(command))
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")

        if "remote get-url origin" in command:
            return CommandResult(
                command=command,
                exit_code=0,
                stdout="https://github.com/owner/wrong-repo.git\n",
                stderr="",
                duration_seconds=0.0,
            )

        if command.startswith("gh repo clone"):
            parts = shlex.split(command)
            target = Path(parts[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(parents=True, exist_ok=True)

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

    prepared_path = orchestrator._stage_prepare_repo(job, log_path)

    assert prepared_path == repository_path
    assert (prepared_path / ".git").exists()
    assert not (prepared_path / "README.tmp").exists()
    backup_dirs = list(repository_path.parent.glob(f"{repository_path.name}__invalid_*"))
    assert len(backup_dirs) == 1
    assert (backup_dirs[0] / "README.tmp").exists()
    assert any("remote get-url origin" in command for command in executed_commands)
    assert any(command.startswith("gh repo clone owner/source-repo ") for command in executed_commands)


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


def test_improvement_stage_selects_feature_expansion_from_maturity_and_trend(app_components):
    settings, store, _ = app_components
    job = _make_job("job-improvement-feature-expansion")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=4.1)
    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.75},
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 4.1},
        ],
        backlog_items=[
            {
                "id": "feature_quick_filter",
                "title": "추천 결과 빠른 필터 추가",
                "priority": "P1",
                "reason": "추천 정확도를 높이는 사용자 가치 확장",
                "action": "카테고리/기분 기준 빠른 필터를 추가하고 핵심 흐름 테스트를 보강",
            }
        ],
        maturity_payload={
            "level": "stable",
            "previous_level": "usable",
            "progression": "up",
            "score": 88,
            "quality_gate_passed": True,
        },
        trend_payload={
            "trend_direction": "improving",
            "delta_from_previous": 0.35,
            "review_round_count": 4,
            "maturity_progression": "up",
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    loop_state_payload = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    assert loop_state_payload["strategy"] == "feature_expansion"
    assert loop_state_payload["strategy_focus"] == "feature"
    assert loop_state_payload["next_scope_restriction"] == "normal"

    next_tasks_payload = json.loads(paths["next_improvement_tasks"].read_text(encoding="utf-8"))
    assert next_tasks_payload["strategy"] == "feature_expansion"
    assert next_tasks_payload["tasks"][0]["title"] == "추천 결과 빠른 필터 추가"
    assert next_tasks_payload["tasks"][0]["selected_by_strategy"] == "feature_expansion"


def test_improvement_stage_selects_test_hardening_for_test_gap(app_components):
    settings, store, _ = app_components
    job = _make_job("job-improvement-test-hardening")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.3)
    review_payload["scores"]["test_coverage"] = 2
    review_payload["quality_gate"] = {"passed": False, "categories_below_threshold": ["test_coverage"]}
    review_payload["artifact_health"] = {"tests": {"test_file_count": 0, "report_count": 0}}

    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.25},
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.3},
        ],
        backlog_items=[
            {
                "id": "tests_playwright",
                "title": "Playwright 회귀 테스트 추가",
                "priority": "P1",
                "reason": "핵심 추천 흐름을 보호하는 자동 테스트가 부족함",
                "action": "메뉴 추천 완료 흐름 기준 E2E 테스트를 추가",
            }
        ],
        maturity_payload={
            "level": "mvp",
            "previous_level": "mvp",
            "progression": "unchanged",
            "score": 58,
            "quality_gate_passed": False,
        },
        trend_payload={
            "trend_direction": "stable",
            "delta_from_previous": 0.05,
            "review_round_count": 3,
            "maturity_progression": "unchanged",
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    loop_state_payload = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    assert loop_state_payload["strategy"] == "test_hardening"
    assert loop_state_payload["strategy_focus"] == "testing"
    assert loop_state_payload["next_scope_restriction"] == "P1_only"

    next_tasks_payload = json.loads(paths["next_improvement_tasks"].read_text(encoding="utf-8"))
    assert next_tasks_payload["strategy"] == "test_hardening"
    assert next_tasks_payload["tasks"][0]["title"] == "Playwright 회귀 테스트 추가"
    assert next_tasks_payload["tasks"][0]["selected_by_strategy"] == "test_hardening"


def test_improvement_stage_selects_ux_clarity_improvement_for_state_handling_gap(app_components):
    settings, store, _ = app_components
    job = _make_job("job-improvement-ux")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.45)
    review_payload["scores"]["ux_clarity"] = 2
    review_payload["scores"]["empty_state_handling"] = 2
    review_payload["quality_gate"] = {
        "passed": False,
        "categories_below_threshold": ["ux_clarity", "empty_state_handling"],
    }

    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.3},
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.45},
        ],
        backlog_items=[
            {
                "id": "ux_empty_copy",
                "title": "빈 상태 안내 문구와 CTA 개선",
                "priority": "P1",
                "reason": "추천 결과가 없을 때 다음 행동이 불명확함",
                "action": "empty/loading/error 상태 메시지와 CTA를 정리",
            }
        ],
        maturity_payload={
            "level": "usable",
            "previous_level": "mvp",
            "progression": "up",
            "score": 72,
            "quality_gate_passed": False,
        },
        trend_payload={
            "trend_direction": "improving",
            "delta_from_previous": 0.15,
            "review_round_count": 2,
            "maturity_progression": "up",
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    loop_state_payload = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    assert loop_state_payload["strategy"] == "ux_clarity_improvement"
    assert loop_state_payload["strategy_focus"] == "ux"
    assert loop_state_payload["next_scope_restriction"] == "P1_only"

    next_tasks_payload = json.loads(paths["next_improvement_tasks"].read_text(encoding="utf-8"))
    assert next_tasks_payload["strategy"] == "ux_clarity_improvement"
    assert next_tasks_payload["tasks"][0]["title"] == "빈 상태 안내 문구와 CTA 개선"
    assert next_tasks_payload["tasks"][0]["selected_by_strategy"] == "ux_clarity_improvement"


def test_improvement_stage_uses_persistent_low_category_trend_for_strategy(app_components):
    settings, store, _ = app_components
    job = _make_job("job-improvement-persistent-low")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.6)
    review_payload["scores"]["test_coverage"] = 3
    review_payload["quality_gate"] = {"passed": True, "categories_below_threshold": []}
    review_payload["artifact_health"] = {"tests": {"test_file_count": 1, "report_count": 1}}

    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {
                "generated_at": utc_now_iso(),
                "job_id": job.job_id,
                "overall": 3.2,
                "scores": {"test_coverage": 1, "ux_clarity": 3},
            },
            {
                "generated_at": utc_now_iso(),
                "job_id": job.job_id,
                "overall": 3.4,
                "scores": {"test_coverage": 1, "ux_clarity": 3},
            },
            {
                "generated_at": utc_now_iso(),
                "job_id": job.job_id,
                "overall": 3.6,
                "scores": {"test_coverage": 1, "ux_clarity": 4},
            },
        ],
        backlog_items=[
            {
                "id": "tests_regression",
                "title": "추천 플로우 회귀 테스트 보강",
                "priority": "P1",
                "reason": "테스트 저점이 장기 지속됨",
                "action": "핵심 추천 흐름을 보호하는 회귀 테스트를 추가",
            }
        ],
        maturity_payload={
            "level": "usable",
            "previous_level": "usable",
            "progression": "unchanged",
            "score": 74,
            "quality_gate_passed": True,
        },
        trend_payload={
            "trend_direction": "improving",
            "delta_from_previous": 0.2,
            "review_round_count": 3,
            "maturity_progression": "unchanged",
            "persistent_low_categories": ["test_coverage"],
            "stagnant_categories": ["test_coverage"],
            "category_deltas": {"test_coverage": 0},
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    loop_state_payload = json.loads(paths["improvement_loop_state"].read_text(encoding="utf-8"))
    assert loop_state_payload["strategy"] == "test_hardening"
    assert loop_state_payload["strategy_inputs"]["persistent_low_categories"] == ["test_coverage"]
    assert loop_state_payload["strategy_inputs"]["stagnant_categories"] == ["test_coverage"]


def test_improvement_stage_writes_structured_memory_artifacts(app_components):
    settings, store, _ = app_components
    job = _make_job("job-memory-structured")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "tests").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "components").mkdir(parents=True, exist_ok=True)
    (repository_path / "package.json").write_text('{"name":"memory-app"}\n', encoding="utf-8")
    (repository_path / "README.md").write_text("# App\n", encoding="utf-8")

    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.3)
    review_payload["scores"]["test_coverage"] = 2
    review_payload["quality_gate"] = {"passed": False, "categories_below_threshold": ["test_coverage"]}
    review_payload["artifact_health"] = {"tests": {"test_file_count": 0, "report_count": 0}}

    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {
                "generated_at": utc_now_iso(),
                "job_id": job.job_id,
                "overall": 3.1,
                "scores": {"test_coverage": 1, "ux_clarity": 3},
            },
            {
                "generated_at": utc_now_iso(),
                "job_id": job.job_id,
                "overall": 3.3,
                "scores": {"test_coverage": 1, "ux_clarity": 3},
            },
        ],
        backlog_items=[
            {
                "id": "tests_regression",
                "title": "추천 플로우 회귀 테스트 보강",
                "priority": "P1",
                "reason": "테스트 저점이 지속됨",
                "action": "회귀 테스트를 추가",
            }
        ],
        maturity_payload={
            "level": "mvp",
            "previous_level": "mvp",
            "progression": "unchanged",
            "score": 61,
            "quality_gate_passed": False,
        },
        trend_payload={
            "trend_direction": "stable",
            "delta_from_previous": 0.2,
            "review_round_count": 2,
            "maturity_progression": "unchanged",
            "persistent_low_categories": ["test_coverage"],
            "stagnant_categories": ["test_coverage"],
            "category_deltas": {"test_coverage": 0},
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    memory_lines = [
        json.loads(line)
        for line in paths["memory_log"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(memory_lines) == 1
    assert memory_lines[0]["memory_type"] == "episodic"
    assert memory_lines[0]["signals"]["strategy"] == "test_hardening"
    assert memory_lines[0]["signals"]["persistent_low_categories"] == ["test_coverage"]

    decision_payload = json.loads(paths["decision_history"].read_text(encoding="utf-8"))
    assert decision_payload["entries"][0]["decision_type"] == "improvement_strategy"
    assert decision_payload["entries"][0]["chosen_strategy"] == "test_hardening"

    patterns_payload = json.loads(paths["failure_patterns"].read_text(encoding="utf-8"))
    pattern_ids = {item["pattern_id"] for item in patterns_payload["items"]}
    assert "low_category:test_coverage" in pattern_ids
    assert "persistent_low:test_coverage" in pattern_ids

    conventions_payload = json.loads(paths["conventions"].read_text(encoding="utf-8"))
    rule_ids = {item["id"] for item in conventions_payload["rules"]}
    assert "conv_tests_dir" in rule_ids
    assert "conv_app_components" in rule_ids
    assert "conv_node_runtime" in rule_ids

    feedback_payload = json.loads(paths["memory_feedback"].read_text(encoding="utf-8"))
    feedback_ids = {item["memory_id"] for item in feedback_payload["entries"]}
    assert f"episodic_job_summary:{job.job_id}" in feedback_ids
    assert f"improvement_strategy:{job.job_id}" in feedback_ids
    assert all(item["verdict"] == "promote" for item in feedback_payload["entries"])

    rankings_payload = json.loads(paths["memory_rankings"].read_text(encoding="utf-8"))
    ranking_map = {item["memory_id"]: item for item in rankings_payload["items"]}
    assert ranking_map[f"episodic_job_summary:{job.job_id}"]["state"] in {"active", "promoted"}
    assert ranking_map[f"improvement_strategy:{job.job_id}"]["score"] >= 1.0

    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    backlog_candidates = runtime_store.list_backlog_candidates(repository=job.repository, limit=10)
    backlog_map = {item["candidate_id"]: item for item in backlog_candidates}
    assert "improvement_backlog:job-memory-structured:tests_regression" in backlog_map
    assert "quality_trend_persistent_low:job-memory-structured:test_coverage" in backlog_map
    assert backlog_map["quality_trend_persistent_low:job-memory-structured:test_coverage"]["priority"] == "P1"
    assert backlog_map["next_improvement_task:job-memory-structured:next_1"]["payload"]["selected_by_strategy"] == "test_hardening"


def test_improvement_stage_extracts_richer_repo_conventions(app_components):
    settings, store, _ = app_components
    job = _make_job("job-memory-conventions-rich")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    (repository_path / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "components").mkdir(parents=True, exist_ok=True)
    (repository_path / "app").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "layout.tsx").write_text("export default function Layout() { return null }\n", encoding="utf-8")
    (repository_path / "app" / "components" / "Button.tsx").write_text(
        "export function Button() { return <button /> }\n",
        encoding="utf-8",
    )
    (repository_path / "tests" / "e2e" / "smoke.test.ts").write_text(
        "test('smoke', async () => {})\n",
        encoding="utf-8",
    )
    (repository_path / "package.json").write_text(
        json.dumps(
            {
                "name": "food-random",
                "dependencies": {
                    "next": "15.0.0",
                    "react": "19.0.0",
                    "tailwindcss": "4.0.0",
                    "framer-motion": "12.0.0",
                    "lucide-react": "0.500.0",
                },
                "devDependencies": {
                    "@playwright/test": "1.55.0",
                    "typescript": "5.9.0",
                    "vitest": "3.2.0",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repository_path / "README.md").write_text("# Food Random\n", encoding="utf-8")

    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.8)
    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.6, "scores": {"ux_clarity": 3}},
        ],
        backlog_items=[],
        maturity_payload={
            "level": "usable",
            "previous_level": "mvp",
            "progression": "up",
            "score": 74,
            "quality_gate_passed": True,
        },
        trend_payload={
            "trend_direction": "up",
            "delta_from_previous": 0.2,
            "review_round_count": 1,
            "maturity_progression": "up",
            "persistent_low_categories": [],
            "stagnant_categories": [],
            "category_deltas": {"ux_clarity": 1},
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    conventions_payload = json.loads(paths["conventions"].read_text(encoding="utf-8"))
    rule_ids = {item["id"] for item in conventions_payload["rules"]}

    assert "nextjs" in conventions_payload["detected_stack"]
    assert "react" in conventions_payload["detected_stack"]
    assert "tailwindcss" in conventions_payload["detected_stack"]
    assert "playwright" in conventions_payload["detected_stack"]
    assert "typescript" in conventions_payload["detected_stack"]
    assert "conv_nextjs" in rule_ids
    assert "conv_tailwindcss" in rule_ids
    assert "conv_playwright" in rule_ids
    assert "conv_typescript" in rule_ids
    assert "conv_next_app_router" in rule_ids
    assert "conv_component_tsx" in rule_ids
    assert "conv_tests_e2e_dir" in rule_ids
    assert "conv_js_test_pattern" in rule_ids


def test_memory_retrieval_artifacts_build_route_specific_context(app_components):
    settings, store, _ = app_components
    job = _make_job("job-memory-retrieval")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    (repository_path / "tests").mkdir(parents=True, exist_ok=True)
    (repository_path / "app" / "components").mkdir(parents=True, exist_ok=True)
    (repository_path / "package.json").write_text('{"name":"retrieval-app"}\n', encoding="utf-8")
    (repository_path / "README.md").write_text("# App\n", encoding="utf-8")

    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    review_payload = _base_review_payload(job.job_id, overall=3.3)
    review_payload["scores"]["test_coverage"] = 2
    review_payload["quality_gate"] = {"passed": False, "categories_below_threshold": ["test_coverage"]}
    review_payload["artifact_health"] = {"tests": {"test_file_count": 0, "report_count": 0}}

    paths = _improvement_paths(repository_path)
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=[
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.0, "scores": {"test_coverage": 1}},
            {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.3, "scores": {"test_coverage": 1}},
        ],
        backlog_items=[
            {
                "id": "tests_regression",
                "title": "추천 플로우 회귀 테스트 보강",
                "priority": "P1",
                "reason": "테스트 저점이 지속됨",
                "action": "회귀 테스트를 추가",
            }
        ],
        maturity_payload={
            "level": "mvp",
            "previous_level": "mvp",
            "progression": "unchanged",
            "score": 61,
            "quality_gate_passed": False,
        },
        trend_payload={
            "trend_direction": "stable",
            "delta_from_previous": 0.2,
            "review_round_count": 2,
            "maturity_progression": "unchanged",
            "persistent_low_categories": ["test_coverage"],
            "stagnant_categories": ["test_coverage"],
            "category_deltas": {"test_coverage": 0},
        },
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)
    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    selection_payload = json.loads(paths["memory_selection"].read_text(encoding="utf-8"))
    context_payload = json.loads(paths["memory_context"].read_text(encoding="utf-8"))
    trace_payload = json.loads(paths["memory_trace"].read_text(encoding="utf-8"))

    assert selection_payload["corpus_counts"]["episodic"] == 1
    assert selection_payload["corpus_counts"]["decisions"] == 1
    assert selection_payload["corpus_counts"]["failure_patterns"] >= 2
    assert selection_payload["corpus_counts"]["conventions"] >= 3
    assert selection_payload["source"] == "db"
    assert selection_payload["planner_context"]
    assert selection_payload["reviewer_context"]
    assert selection_payload["coder_context"]
    assert any(item["kind"] == "decision" for item in context_payload["coder_context"])
    assert any(item["kind"] == "failure_pattern" for item in context_payload["reviewer_context"])
    assert any(item["kind"] == "convention" for item in context_payload["planner_context"])
    assert trace_payload["source"] == "db"
    assert trace_payload["fallback_used"] is False
    assert trace_payload["routes"]["planner"]["selected_count"] >= 1
    assert any(item["kind"] == "convention" for item in trace_payload["routes"]["planner"]["selected_items"])


def test_memory_retrieval_writes_disabled_vector_shadow_manifest_by_default(app_components):
    settings, store, _ = app_components
    job = _make_job("job-vector-shadow-disabled")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _improvement_paths(repository_path)

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    payload = json.loads(paths["vector_shadow_index"].read_text(encoding="utf-8"))
    assert payload["enabled"] is False
    assert payload["status"] == "disabled"
    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []
    assert payload["transport"]["configured"] is False
    assert payload["transport"]["detail"] == "not_configured"


def test_memory_retrieval_writes_vector_shadow_manifest_when_enabled(app_components, tmp_path: Path):
    settings, store, _ = app_components
    job = _make_job("job-vector-shadow-enabled")
    job.workflow_id = "wf-default"
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _improvement_paths(repository_path)

    runtime_store = MemoryRuntimeStore(settings.resolved_memory_dir / "memory_runtime.db")
    runtime_store.upsert_entry(
        {
            "memory_id": "failure_pattern:job-vector-shadow-enabled:stale-heartbeat",
            "memory_type": "failure_pattern",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": job.workflow_id,
            "job_id": job.job_id,
            "title": "stale heartbeat during codex run",
            "summary": "heartbeat stale detected during implement_with_codex",
            "source_path": "_docs/FAILURE_PATTERNS.json",
            "score": 2.4,
            "confidence": 0.86,
            "baseline_score": 2.4,
            "baseline_confidence": 0.86,
            "state": "promoted",
            "updated_at": "2026-03-12T02:00:00+00:00",
        }
    )

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps(
            {
                "flags": {
                    "memory_logging": True,
                    "memory_retrieval": True,
                    "convention_extraction": True,
                    "memory_scoring": True,
                    "strategy_shadow": True,
                    "vector_memory_shadow": True,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator.feature_flags_path = feature_flags_path
    orchestrator._qdrant_shadow_transport = type(
        "FakeShadowTransport",
        (),
        {
            "sync_manifest": staticmethod(
                lambda manifest: type(
                    "TransportResult",
                    (),
                    {
                        "to_dict": lambda self: {
                            "configured": True,
                            "attempted": True,
                            "ok": True,
                            "detail": "upsert_ok",
                            "collection": "agenthub_memory_shadow",
                            "point_count": len(manifest.get("candidates", [])),
                            "vector_size": 64,
                            "collection_status_code": 200,
                            "upsert_status_code": 200,
                        },
                        "ok": True,
                        "attempted": True,
                        "configured": True,
                    },
                )()
            )
        },
    )()
    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    payload = json.loads(paths["vector_shadow_index"].read_text(encoding="utf-8"))
    assert payload["enabled"] is True
    assert payload["status"] == "transported"
    assert payload["provider"] == "qdrant"
    assert payload["candidate_count"] >= 1
    assert payload["candidates"][0]["memory_id"].startswith("failure_pattern:")
    assert payload["transport"]["ok"] is True
    assert payload["transport"]["detail"] == "upsert_ok"


def test_memory_retrieval_skips_banned_memory_entries(app_components):
    settings, store, _ = app_components
    job = _make_job("job-memory-retrieval-banned")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _improvement_paths(repository_path)

    paths["memory_log"].write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "memory_id": "episodic_job_summary:good",
                        "memory_type": "episodic",
                        "generated_at": "2026-03-10T10:00:00Z",
                        "signals": {"strategy": "stabilization", "overall": 3.8, "maturity_level": "usable"},
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "memory_id": "episodic_job_summary:banned",
                        "memory_type": "episodic",
                        "generated_at": "2026-03-10T11:00:00Z",
                        "signals": {"strategy": "feature_expansion", "overall": 4.2, "maturity_level": "stable"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths["decision_history"].write_text(json.dumps({"entries": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["failure_patterns"].write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conventions"].write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["memory_rankings"].write_text(
        json.dumps(
            {
                "items": [
                    {"memory_id": "episodic_job_summary:good", "score": 2.0, "confidence": 0.8, "usage_count": 2, "state": "promoted"},
                    {"memory_id": "episodic_job_summary:banned", "score": -5.0, "confidence": 0.1, "usage_count": 3, "state": "banned"},
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    context_payload = json.loads(paths["memory_context"].read_text(encoding="utf-8"))
    trace_payload = json.loads(paths["memory_trace"].read_text(encoding="utf-8"))
    planner_ids = [item["id"] for item in context_payload["planner_context"]]
    assert "episodic_job_summary:good" in planner_ids
    assert "episodic_job_summary:banned" not in planner_ids
    assert trace_payload["source"] == "file"
    assert trace_payload["fallback_used"] is True


def test_memory_retrieval_prefers_runtime_db_over_file_artifacts(app_components):
    settings, store, _ = app_components
    job = _make_job("job-memory-retrieval-db")
    job.app_code = "web"
    job.workflow_id = "wf-memory"
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _improvement_paths(repository_path)

    # If retrieval falls back to files, this ID would leak into planner context.
    paths["memory_log"].write_text(
        json.dumps(
            {
                "memory_id": "episodic_job_summary:file-only",
                "memory_type": "episodic",
                "generated_at": "2026-03-10T09:00:00Z",
                "signals": {"strategy": "feature_expansion", "overall": 9.9, "maturity_level": "fake"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["decision_history"].write_text(json.dumps({"entries": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["failure_patterns"].write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conventions"].write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["memory_rankings"].write_text(
        json.dumps(
            {
                "items": [
                    {"memory_id": "episodic_job_summary:file-only", "score": 5.0, "confidence": 0.99, "usage_count": 9, "state": "promoted"},
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    runtime_store = orchestrator._get_memory_runtime_store()
    runtime_store.upsert_entry(
        {
            "memory_id": f"episodic_job_summary:{job.job_id}",
            "memory_type": "episodic",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": job.workflow_id,
            "job_id": job.job_id,
            "issue_number": job.issue_number,
            "issue_title": job.issue_title,
            "source_kind": "artifact_memory_log",
            "source_path": str(paths["memory_log"]),
            "title": "runtime episodic",
            "summary": "strategy=test_hardening",
            "state": "promoted",
            "confidence": 0.92,
            "score": 4.0,
            "usage_count": 3,
            "payload": {
                "memory_id": f"episodic_job_summary:{job.job_id}",
                "memory_type": "episodic",
                "generated_at": "2026-03-10T10:00:00Z",
                "signals": {"strategy": "test_hardening", "overall": 4.4, "maturity_level": "usable"},
            },
            "updated_at": "2026-03-10T10:00:00Z",
        }
    )
    runtime_store.upsert_entry(
        {
            "memory_id": f"improvement_strategy:{job.job_id}",
            "memory_type": "decision",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": job.workflow_id,
            "job_id": job.job_id,
            "issue_number": job.issue_number,
            "issue_title": job.issue_title,
            "source_kind": "artifact_decision_history",
            "source_path": str(paths["decision_history"]),
            "title": "improvement_strategy",
            "summary": "test_hardening",
            "state": "active",
            "confidence": 0.71,
            "score": 1.0,
            "usage_count": 1,
            "payload": {
                "decision_id": f"improvement_strategy:{job.job_id}",
                "generated_at": "2026-03-10T10:00:00Z",
                "chosen_strategy": "test_hardening",
                "strategy_focus": "testing",
            },
            "updated_at": "2026-03-10T10:00:00Z",
        }
    )
    runtime_store.upsert_entry(
        {
            "memory_id": "persistent_low:test_coverage",
            "memory_type": "failure_pattern",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": job.workflow_id,
            "job_id": "job-older",
            "issue_number": 11,
            "issue_title": "older issue",
            "source_kind": "artifact_failure_patterns",
            "source_path": str(paths["failure_patterns"]),
            "title": "persistent_low",
            "summary": "test_coverage",
            "state": "decayed",
            "confidence": 0.43,
            "score": -1.0,
            "usage_count": 2,
            "payload": {
                "pattern_id": "persistent_low:test_coverage",
                "pattern_type": "persistent_low",
                "category": "test_coverage",
                "trigger": "trend_persistent_low",
                "count": 3,
                "recommended_actions": ["추가 회귀 테스트 작성"],
            },
            "updated_at": "2026-03-10T10:00:00Z",
        }
    )
    runtime_store.upsert_entry(
        {
            "memory_id": "conv_pytest_file_pattern",
            "memory_type": "convention",
            "repository": job.repository,
            "execution_repository": job.repository,
            "app_code": job.app_code,
            "workflow_id": job.workflow_id,
            "job_id": "job-older",
            "issue_number": 11,
            "issue_title": "older issue",
            "source_kind": "artifact_conventions",
            "source_path": str(paths["conventions"]),
            "title": "testing",
            "summary": "Python tests follow test_*.py naming under tests/",
            "state": "promoted",
            "confidence": 0.88,
            "score": 3.0,
            "usage_count": 2,
            "payload": {
                "id": "conv_pytest_file_pattern",
                "type": "testing",
                "rule": "Python tests follow test_*.py naming under tests/",
                "confidence": 0.88,
                "evidence_paths": ["tests/test_orchestrator_retry.py"],
            },
            "updated_at": "2026-03-10T10:00:00Z",
        }
    )

    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    selection_payload = json.loads(paths["memory_selection"].read_text(encoding="utf-8"))
    context_payload = json.loads(paths["memory_context"].read_text(encoding="utf-8"))
    trace_payload = json.loads(paths["memory_trace"].read_text(encoding="utf-8"))

    assert selection_payload["source"] == "db"
    assert context_payload["source"] == "db"
    assert trace_payload["source"] == "db"
    assert trace_payload["fallback_used"] is False
    assert selection_payload["corpus_counts"]["episodic"] == 1
    assert "episodic_job_summary:file-only" not in selection_payload["planner_context"]
    assert any(item["id"] == f"episodic_job_summary:{job.job_id}" for item in context_payload["planner_context"])
    assert any(item["id"] == f"improvement_strategy:{job.job_id}" for item in context_payload["coder_context"])
    assert any(item["id"] == "persistent_low:test_coverage" for item in context_payload["reviewer_context"])
    assert any(item["id"] == "conv_pytest_file_pattern" for item in context_payload["planner_context"])
    assert trace_payload["routes"]["coder"]["selected_count"] >= 2


def test_planner_prompt_includes_memory_context_snapshot(app_components):
    settings, store, _ = app_components
    job = _make_job("job-planner-memory-prompt")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = _improvement_paths(repository_path)
    paths["spec"] = Orchestrator._docs_file(repository_path, "SPEC.md")
    paths["plan"] = Orchestrator._docs_file(repository_path, "PLAN.md")
    paths["review"] = Orchestrator._docs_file(repository_path, "REVIEW.md")
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n", encoding="utf-8")
    paths["memory_log"].write_text(
        json.dumps(
            {
                "memory_id": f"episodic_job_summary:{job.job_id}",
                "memory_type": "episodic",
                "generated_at": utc_now_iso(),
                "signals": {
                    "strategy": "test_hardening",
                    "overall": 3.3,
                    "maturity_level": "mvp",
                    "persistent_low_categories": ["test_coverage"],
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
                        "generated_at": utc_now_iso(),
                        "chosen_strategy": "test_hardening",
                        "strategy_focus": "testing",
                        "change_reasons": ["테스트 저점이 지속됨"],
                        "selected_task_titles": ["회귀 테스트 보강"],
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
                "items": [
                    {
                        "pattern_id": "persistent_low:test_coverage",
                        "pattern_type": "persistent_low",
                        "category": "test_coverage",
                        "trigger": "trend_persistent_low",
                        "count": 3,
                        "recommended_actions": ["회귀 테스트 보강"],
                    }
                ]
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
                "rules": [
                    {
                        "id": "conv_tests_dir",
                        "type": "filesystem",
                        "rule": "Tests live under tests/",
                        "evidence_paths": ["tests"],
                        "confidence": 0.74,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._run_planner_legacy_one_shot(job, repository_path, paths, log_path)

    prompt_text = (repository_path / "_docs" / "PLANNER_PROMPT.md").read_text(encoding="utf-8")
    assert "Memory Selection" in prompt_text
    assert "Memory Context" in prompt_text
    assert "strategy=test_hardening" in prompt_text


def test_planner_prompt_includes_followup_backlog_task_snapshot(app_components):
    settings, store, _ = app_components
    job = _make_job("job-planner-followup-prompt")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = _improvement_paths(repository_path)
    paths["spec"] = Orchestrator._docs_file(repository_path, "SPEC.md")
    paths["plan"] = Orchestrator._docs_file(repository_path, "PLAN.md")
    paths["review"] = Orchestrator._docs_file(repository_path, "REVIEW.md")
    paths["spec"].write_text("# SPEC\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n", encoding="utf-8")
    paths["followup_backlog_task"] = Orchestrator._docs_file(repository_path, "FOLLOWUP_BACKLOG_TASK.json")
    paths["followup_backlog_task"].write_text(
        json.dumps(
            {
                "candidate_id": "next_improvement_task:job-planner-followup-prompt:next_1",
                "title": "회귀 테스트 보강",
                "summary": "실패 재현 케이스를 먼저 고정한다",
                "recommended_node_type": "coder_fix_from_test_report",
                "recommended_action": "regression test를 먼저 추가한다",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._run_planner_legacy_one_shot(job, repository_path, paths, log_path)

    prompt_text = (repository_path / "_docs" / "PLANNER_PROMPT.md").read_text(encoding="utf-8")
    assert "Follow-up Backlog Task" in prompt_text
    assert "회귀 테스트 보강" in prompt_text
    assert "coder_fix_from_test_report" in prompt_text


def test_improvement_stage_writes_strategy_shadow_report_with_memory_divergence(app_components):
    settings, store, _ = app_components
    job = _make_job("job-strategy-shadow")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = _improvement_paths(repository_path)
    review_payload = _base_review_payload(job.job_id, overall=3.7)
    history_entries = [
        {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.5, "top_issue_ids": ["stability"]},
        {"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.7, "top_issue_ids": ["stability"]},
    ]
    backlog_items = [{"id": "feat-1", "priority": "P2", "title": "다음 기능 후보", "reason": "사용자 가치 개선"}]
    maturity_payload = {"level": "usable", "score": 76, "progression": "up"}
    trend_payload = {
        "trend_direction": "stable",
        "delta_from_previous": 0.0,
        "review_round_count": 2,
        "persistent_low_categories": [],
        "stagnant_categories": [],
        "category_deltas": {},
        "maturity_progression": "unchanged",
    }
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=history_entries,
        backlog_items=backlog_items,
        maturity_payload=maturity_payload,
        trend_payload=trend_payload,
    )
    paths["memory_log"].write_text(
        json.dumps(
            {
                "memory_id": "episodic_job_summary:stable-feature",
                "memory_type": "episodic",
                "generated_at": utc_now_iso(),
                "signals": {
                    "strategy": "feature_expansion",
                    "overall": 4.3,
                    "maturity_level": "stable",
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
                        "decision_id": "improvement_strategy:stable-feature",
                        "generated_at": utc_now_iso(),
                        "chosen_strategy": "feature_expansion",
                        "strategy_focus": "feature",
                        "change_reasons": ["상승 추세에서 기능 확장을 선택"],
                        "selected_task_titles": ["검색 필터 확장"],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["failure_patterns"].write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conventions"].write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["memory_rankings"].write_text(
        json.dumps(
            {
                "items": [
                    {
                        "memory_id": "episodic_job_summary:stable-feature",
                        "score": 5.0,
                        "confidence": 0.92,
                        "usage_count": 4,
                        "state": "promoted",
                    },
                    {
                        "memory_id": "improvement_strategy:stable-feature",
                        "score": 4.0,
                        "confidence": 0.9,
                        "usage_count": 3,
                        "state": "promoted",
                    },
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    shadow_payload = json.loads(paths["strategy_shadow_report"].read_text(encoding="utf-8"))
    assert shadow_payload["selected_strategy"] == "normal_iterative_improvement"
    assert shadow_payload["shadow_strategy"] == "feature_expansion"
    assert shadow_payload["diverged"] is True
    assert shadow_payload["decision_mode"] == "memory_divergence"
    assert any(item["recommended_strategy"] == "feature_expansion" for item in shadow_payload["evidence"])


def test_improvement_stage_keeps_protected_strategy_in_shadow_mode(app_components):
    settings, store, _ = app_components
    job = _make_job("job-strategy-shadow-locked")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = _improvement_paths(repository_path)
    review_payload = _base_review_payload(job.job_id, overall=3.2)
    review_payload["operating_policy"]["requires_design_reset"] = True
    history_entries = [{"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.0, "top_issue_ids": ["design"]}]
    backlog_items = [{"id": "design-1", "priority": "P0", "title": "설계 문서 재정렬", "reason": "설계 계약 위반"}]
    maturity_payload = {"level": "mvp", "score": 60, "progression": "unchanged"}
    trend_payload = {
        "trend_direction": "stable",
        "delta_from_previous": 0.0,
        "review_round_count": 1,
        "persistent_low_categories": [],
        "stagnant_categories": [],
        "category_deltas": {},
        "maturity_progression": "unchanged",
    }
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=history_entries,
        backlog_items=backlog_items,
        maturity_payload=maturity_payload,
        trend_payload=trend_payload,
    )
    paths["memory_log"].write_text(
        json.dumps(
            {
                "memory_id": "episodic_job_summary:feature-memory",
                "memory_type": "episodic",
                "generated_at": utc_now_iso(),
                "signals": {"strategy": "feature_expansion", "overall": 4.1, "maturity_level": "stable"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["decision_history"].write_text(json.dumps({"entries": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["failure_patterns"].write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conventions"].write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["memory_rankings"].write_text(
        json.dumps(
            {
                "items": [
                    {
                        "memory_id": "episodic_job_summary:feature-memory",
                        "score": 5.0,
                        "confidence": 0.95,
                        "usage_count": 5,
                        "state": "promoted",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    shadow_payload = json.loads(paths["strategy_shadow_report"].read_text(encoding="utf-8"))
    assert shadow_payload["selected_strategy"] == "design_rebaseline"
    assert shadow_payload["shadow_strategy"] == "design_rebaseline"
    assert shadow_payload["diverged"] is False
    assert shadow_payload["decision_mode"] == "locked_by_guardrail"


def test_memory_retrieval_flag_writes_disabled_context_payload(app_components, tmp_path: Path):
    settings, store, _ = app_components
    job = _make_job("job-memory-retrieval-disabled")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _improvement_paths(repository_path)

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps(
            {
                "flags": {
                    "memory_logging": True,
                    "memory_retrieval": False,
                    "convention_extraction": True,
                    "memory_scoring": True,
                    "strategy_shadow": True,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator.feature_flags_path = feature_flags_path
    orchestrator._write_memory_retrieval_artifacts(job=job, repository_path=repository_path, paths=paths)

    selection_payload = json.loads(paths["memory_selection"].read_text(encoding="utf-8"))
    context_payload = json.loads(paths["memory_context"].read_text(encoding="utf-8"))
    trace_payload = json.loads(paths["memory_trace"].read_text(encoding="utf-8"))
    assert selection_payload["enabled"] is False
    assert selection_payload["planner_context"] == []
    assert context_payload["enabled"] is False
    assert context_payload["coder_context"] == []
    assert trace_payload["enabled"] is False
    assert trace_payload["source"] == "disabled"


def test_improvement_stage_writes_disabled_strategy_shadow_when_flag_off(app_components, tmp_path: Path):
    settings, store, _ = app_components
    job = _make_job("job-strategy-shadow-disabled")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    paths = _improvement_paths(repository_path)
    review_payload = _base_review_payload(job.job_id, overall=3.4)
    history_entries = [{"generated_at": utc_now_iso(), "job_id": job.job_id, "overall": 3.1, "top_issue_ids": ["qa"]}]
    backlog_items = [{"id": "qa-1", "priority": "P1", "title": "테스트 보강", "reason": "품질 강화"}]
    maturity_payload = {"level": "usable", "score": 71, "progression": "up"}
    trend_payload = {
        "trend_direction": "improving",
        "delta_from_previous": 0.3,
        "review_round_count": 1,
        "persistent_low_categories": [],
        "stagnant_categories": [],
        "category_deltas": {},
        "maturity_progression": "up",
    }
    _write_improvement_stage_inputs(
        repository_path=repository_path,
        paths=paths,
        review_payload=review_payload,
        history_entries=history_entries,
        backlog_items=backlog_items,
        maturity_payload=maturity_payload,
        trend_payload=trend_payload,
    )

    feature_flags_path = tmp_path / "config" / "feature_flags.json"
    feature_flags_path.parent.mkdir(parents=True, exist_ok=True)
    feature_flags_path.write_text(
        json.dumps(
            {
                "flags": {
                    "memory_logging": True,
                    "memory_retrieval": True,
                    "convention_extraction": True,
                    "memory_scoring": True,
                    "strategy_shadow": False,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator.feature_flags_path = feature_flags_path
    orchestrator._stage_improvement_stage(job, repository_path, paths, log_path)

    shadow_payload = json.loads(paths["strategy_shadow_report"].read_text(encoding="utf-8"))
    assert shadow_payload["enabled"] is False
    assert shadow_payload["decision_mode"] == "disabled"
    assert shadow_payload["shadow_strategy"] == ""


def test_quality_trend_snapshot_tracks_category_level_history():
    maturity_snapshot = {
        "level": "usable",
        "previous_level": "mvp",
        "progression": "up",
    }
    history_entries = [
        {
            "generated_at": utc_now_iso(),
            "job_id": "job-trend",
            "overall": 3.0,
            "scores": {"test_coverage": 1, "ux_clarity": 2, "code_quality": 3},
        },
        {
            "generated_at": utc_now_iso(),
            "job_id": "job-trend",
            "overall": 3.2,
            "scores": {"test_coverage": 1, "ux_clarity": 2, "code_quality": 3},
        },
        {
            "generated_at": utc_now_iso(),
            "job_id": "job-trend",
            "overall": 3.4,
            "scores": {"test_coverage": 1, "ux_clarity": 3, "code_quality": 4},
        },
    ]

    snapshot = Orchestrator._build_quality_trend_snapshot(
        job_id="job-trend",
        history_entries=history_entries,
        maturity_snapshot=maturity_snapshot,
    )

    assert snapshot["trend_direction"] == "improving"
    assert snapshot["category_latest_scores"]["test_coverage"] == 1
    assert snapshot["category_deltas"]["test_coverage"] == 0
    assert snapshot["category_deltas"]["ux_clarity"] == 1
    assert snapshot["category_trend_direction"]["ux_clarity"] == "improving"
    assert "test_coverage" in snapshot["persistent_low_categories"]
    assert "test_coverage" in snapshot["stagnant_categories"]
    assert "ux_clarity" not in snapshot["persistent_low_categories"]


def test_repo_maturity_snapshot_calculates_levels_from_evidence():
    base_scores = {
        "code_quality": 4,
        "architecture_structure": 4,
        "maintainability": 4,
        "usability": 4,
        "ux_clarity": 4,
        "test_coverage": 4,
        "error_state_handling": 4,
        "empty_state_handling": 4,
        "loading_state_handling": 4,
    }

    bootstrap = Orchestrator._build_repo_maturity_snapshot(
        job_id="job-maturity-bootstrap",
        scores=base_scores,
        overall=2.1,
        artifact_health={
            "docs": {"generated_count": 2},
            "repo": {"source_file_count": 0},
            "tests": {"test_file_count": 0, "report_count": 0},
        },
        quality_gate={"passed": False, "categories_below_threshold": ["test_coverage"]},
        principle_alignment={},
        previous_level="bootstrap",
    )
    assert bootstrap["level"] == "bootstrap"
    assert bootstrap["progression"] == "unchanged"

    stable = Orchestrator._build_repo_maturity_snapshot(
        job_id="job-maturity-stable",
        scores=base_scores,
        overall=3.9,
        artifact_health={
            "docs": {"generated_count": 7},
            "repo": {"source_file_count": 8},
            "tests": {"test_file_count": 2, "report_count": 1},
        },
        quality_gate={"passed": True, "categories_below_threshold": []},
        principle_alignment={},
        previous_level="usable",
    )
    assert stable["level"] == "stable"
    assert stable["progression"] == "up"

    product_grade = Orchestrator._build_repo_maturity_snapshot(
        job_id="job-maturity-product",
        scores=base_scores,
        overall=4.6,
        artifact_health={
            "docs": {"generated_count": 8},
            "repo": {"source_file_count": 12},
            "tests": {"test_file_count": 3, "report_count": 2},
        },
        quality_gate={"passed": True, "categories_below_threshold": []},
        principle_alignment={},
        previous_level="stable",
    )
    assert product_grade["level"] == "product_grade"
    assert product_grade["progression"] == "up"


def test_product_definition_hard_gate_isolates_only_missing_artifact_sections(app_components, tmp_path: Path):
    settings, store, _ = app_components
    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    log_path = tmp_path / "hard-gate.log"
    log_path.write_text("", encoding="utf-8")

    paths = {
        "product_brief": tmp_path / "PRODUCT_BRIEF.md",
        "user_flows": tmp_path / "USER_FLOWS.md",
        "mvp_scope": tmp_path / "MVP_SCOPE.md",
        "architecture_plan": tmp_path / "ARCHITECTURE_PLAN.md",
        "scaffold_plan": tmp_path / "SCAFFOLD_PLAN.md",
    }
    paths["product_brief"].write_text(
        "# PRODUCT BRIEF\n## Product Goal\n- goal\n## Target Users\n- users\n## Success Metrics\n- metrics\n",
        encoding="utf-8",
    )
    paths["user_flows"].write_text(
        "# USER FLOWS\n## Primary Flow\n- step 1\n",
        encoding="utf-8",
    )
    paths["mvp_scope"].write_text(
        "# MVP SCOPE\n## In Scope\n- a\n## Out of Scope\n- b\n## Acceptance Gates\n- gate\n",
        encoding="utf-8",
    )
    paths["architecture_plan"].write_text(
        "# ARCHITECTURE PLAN\n## Component Boundaries\n- boundary\n## Quality Gates\n- gate\n## Loop Safety Rules\n- stagnation\n",
        encoding="utf-8",
    )
    paths["scaffold_plan"].write_text(
        "# SCAFFOLD PLAN\n## Repository State\n- greenfield\n## Bootstrap Mode\n- create\n## Verification Checklist\n- check\n",
        encoding="utf-8",
    )

    with pytest.raises(CommandExecutionError) as exc:
        orchestrator._ensure_product_definition_ready(paths, log_path)

    message = str(exc.value)
    assert "USER_FLOWS.md" in message
    assert "ux_state_checklist" in message
    assert "PRODUCT_BRIEF.md" not in message
    assert "MVP_SCOPE.md" not in message
    assert "ARCHITECTURE_PLAN.md" not in message
    assert "SCAFFOLD_PLAN.md" not in message


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


def test_workflow_node_role_preset_binds_documentation_route(app_components, tmp_path: Path):
    settings, store, _ = app_components
    job = replace(_make_job("job-node-role-preset"), attempt=1)
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    roles_path = tmp_path / "roles.json"
    roles_path.write_text(
        json.dumps(
            {
                "roles": [
                    {"code": "tech-writer", "name": "기술 문서 작성가", "cli": "codex", "template_key": "documentation_writer", "enabled": True},
                    {"code": "coder", "name": "코더", "cli": "codex", "template_key": "coder", "enabled": True},
                ],
                "presets": [
                    {"preset_id": "doc-fast", "name": "문서 빠른 처리", "role_codes": ["coder"]},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    routing_path = tmp_path / "ai_role_routing.json"
    routing_path.write_text(json.dumps(default_ai_role_routing_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    workflow = {
        "workflow_id": "wf-node-role-preset",
        "entry_node_id": "n1",
        "nodes": [
            {
                "id": "n1",
                "type": "documentation_task",
                "title": "문서화",
                "role_preset_id": "doc-fast",
            }
        ],
        "edges": [],
    }

    orchestrator = Orchestrator(
        settings,
        store,
        FakeTemplateRunner(),
        ai_role_router=AIRoleRouter(roles_path=roles_path, routing_path=routing_path),
    )
    orchestrator._commit_markdown_changes_after_stage = lambda *args, **kwargs: None  # type: ignore[method-assign]

    observed: dict[str, str] = {}

    def fake_documentation_stage(job_obj, repo_path, passed_paths, passed_log_path):
        observed["route_context"] = orchestrator._build_route_runtime_context("documentation")
        observed["template_name"] = orchestrator._template_for_route("documentation")
        assert repo_path == repository_path
        assert passed_log_path == log_path

    orchestrator._workflow_context_paths = lambda _context: {  # type: ignore[method-assign]
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
    }
    orchestrator._stage_documentation_with_claude = fake_documentation_stage  # type: ignore[method-assign]

    orchestrator._run_workflow_pipeline(
        job,
        repository_path,
        workflow,
        workflow["nodes"],
        log_path,
    )

    assert "role_code: coder" in observed["route_context"]
    assert observed["template_name"] == "documentation_writer__codex"
    log_text = log_path.read_text(encoding="utf-8")
    assert "Workflow node role binding: documentation->coder" in log_text


def test_workflow_context_results_accumulate_previous_node_outputs(app_components):
    settings, store, _ = app_components
    job = _make_job("job-workflow-context-results")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    workflow = {
        "workflow_id": "wf-context-results",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "Issue"},
            {"id": "n2", "type": "write_spec", "title": "Spec"},
            {"id": "n3", "type": "tester_task", "title": "Assert context"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
        ],
    }

    orchestrator = Orchestrator(settings, store, FakeTemplateRunner())
    orchestrator._commit_markdown_changes_after_stage = lambda *args, **kwargs: None  # type: ignore[method-assign]
    orchestrator._stage_read_issue = lambda *_args, **_kwargs: IssueDetails(  # type: ignore[method-assign]
        title="Issue title",
        body="Issue body",
        url="https://github.com/owner/repo/issues/55",
        labels=("mobile",),
    )

    def fake_write_spec(*_args, **_kwargs):
        paths = {
            "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
            "spec_json": Orchestrator._docs_file(repository_path, "SPEC.json"),
        }
        paths["spec"].write_text("# SPEC\n", encoding="utf-8")
        paths["spec_json"].write_text('{"app_type":"web"}\n', encoding="utf-8")
        return paths

    observed: dict[str, object] = {}

    def fake_tester_task(*, job, repository_path, node, context, log_path):
        results = context.get("results")
        assert isinstance(results, dict)
        assert results["n1"]["node_type"] == "gh_read_issue"
        assert results["n1"]["artifacts"] == []
        assert results["n2"]["node_type"] == "write_spec"
        assert results["n2"]["artifact_keys"] == ["spec", "spec_json"]
        assert len(results["n2"]["artifacts"]) == 2
        observed["results"] = results
        return None

    orchestrator._stage_write_spec = fake_write_spec  # type: ignore[method-assign]
    orchestrator._workflow_node_tester_task = fake_tester_task  # type: ignore[method-assign]

    orchestrator._run_workflow_pipeline(
        job,
        repository_path,
        workflow,
        workflow["nodes"],
        log_path,
    )

    assert "results" in observed


def test_workflow_failure_edge_routes_without_new_attempt(app_components):
    settings, store, _ = app_components
    job = _make_job("job-workflow-failure-edge")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")
        if command.startswith("gh repo clone"):
            Path(shlex.split(command)[-1]).mkdir(parents=True, exist_ok=True)
        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
                "labels": [],
            }
            return CommandResult(command=command, exit_code=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.0)
        if "status --porcelain" in command:
            return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    called = {"documentation": 0}

    def fake_ux_stage(job_obj, repo_path, paths, passed_log_path):
        raise RuntimeError("ux review failed")

    def fake_documentation(job_obj, repo_path, paths, passed_log_path):
        called["documentation"] += 1
        (repo_path / "README.md").write_text("# docs\n", encoding="utf-8")

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]
    orchestrator._stage_documentation_with_claude = fake_documentation  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "wf-failure-edge",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "ux_e2e_review"},
            {"id": "n4", "type": "documentation_task"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "failure"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert called["documentation"] == 1

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.attempt == 1

    node_runs = store.list_node_runs(job.job_id)
    assert [(item.node_type, item.status) for item in node_runs] == [
        ("gh_read_issue", "success"),
        ("write_spec", "success"),
        ("ux_e2e_review", "failed"),
        ("documentation_task", "success"),
    ]


def test_workflow_if_label_match_branches_on_issue_labels(app_components):
    settings, store, _ = app_components
    job = _make_job("job-workflow-if-label")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")
        if command.startswith("gh repo clone"):
            Path(shlex.split(command)[-1]).mkdir(parents=True, exist_ok=True)
        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
                "labels": [{"name": "mobile"}, {"name": "food"}],
            }
            return CommandResult(command=command, exit_code=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.0)
        if "status --porcelain" in command:
            return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    observed = {"planner": 0, "documentation": 0}

    def fake_plan_stage(job_obj, repo_path, paths, passed_log_path, planning_mode="general"):
        observed["planner"] += 1

    def fake_documentation(job_obj, repo_path, paths, passed_log_path):
        observed["documentation"] += 1

    orchestrator._stage_plan_with_gemini = fake_plan_stage  # type: ignore[method-assign]
    orchestrator._stage_documentation_with_claude = fake_documentation  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "wf-if-label",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "if_label_match", "match_labels": "mobile,web", "match_mode": "any"},
            {"id": "n4", "type": "gemini_plan", "title": "큰틀 플랜"},
            {"id": "n5", "type": "documentation_task"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
            {"from": "n3", "to": "n5", "on": "failure"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert observed["planner"] == 1
    assert observed["documentation"] == 0

    log_text = (settings.logs_debug_dir / job.log_file).read_text(encoding="utf-8")
    assert "if_label_match evaluated" in log_text
    assert "Workflow edge selected: n3 --success--> n4" in log_text


def test_workflow_loop_until_pass_retries_within_same_attempt(app_components):
    settings, store, _ = app_components
    job = _make_job("job-workflow-loop")
    store.create_job(job)
    store.enqueue_job(job.job_id)

    def fake_shell(command, cwd, log_writer, check, command_purpose):
        log_writer(f"[FAKE_SHELL] {command_purpose}: {command}")
        if command.startswith("gh repo clone"):
            Path(shlex.split(command)[-1]).mkdir(parents=True, exist_ok=True)
        if command.startswith("gh issue view"):
            payload = {
                "title": "Fetched issue title",
                "body": "Issue body from fake gh",
                "url": "https://github.com/owner/repo/issues/55",
                "labels": [],
            }
            return CommandResult(command=command, exit_code=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.0)
        if "status --porcelain" in command:
            return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)
        return CommandResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    orchestrator = Orchestrator(
        settings=settings,
        store=store,
        command_templates=FakeTemplateRunner(),
        shell_executor=fake_shell,
    )

    calls = {"ux": 0, "documentation": 0}

    def fake_ux_stage(job_obj, repo_path, paths, passed_log_path):
        calls["ux"] += 1
        if calls["ux"] < 3:
            raise RuntimeError("ux review failed")

    def fake_documentation(job_obj, repo_path, paths, passed_log_path):
        calls["documentation"] += 1

    orchestrator._stage_ux_e2e_review = fake_ux_stage  # type: ignore[method-assign]
    orchestrator._stage_documentation_with_claude = fake_documentation  # type: ignore[method-assign]
    workflow = {
        "workflow_id": "wf-loop-until-pass",
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue"},
            {"id": "n2", "type": "write_spec"},
            {"id": "n3", "type": "ux_e2e_review"},
            {"id": "n4", "type": "loop_until_pass", "loop_max_iterations": 2},
            {"id": "n5", "type": "documentation_task"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
            {"from": "n3", "to": "n4", "on": "failure"},
            {"from": "n4", "to": "n3", "on": "failure"},
            {"from": "n4", "to": "n5", "on": "success"},
        ],
    }
    orchestrator._load_active_workflow = lambda _job, _log_path: workflow  # type: ignore[method-assign]

    processed = orchestrator.process_next_job()
    assert processed is True
    assert calls["ux"] == 3
    assert calls["documentation"] == 1

    stored = store.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.DONE.value
    assert stored.attempt == 1

    log_text = (settings.logs_debug_dir / job.log_file).read_text(encoding="utf-8")
    assert "loop_until_pass retry 1/2" in log_text
    assert "loop_until_pass retry 2/2" in log_text
    assert "loop_until_pass exit: previous node succeeded" in log_text


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
