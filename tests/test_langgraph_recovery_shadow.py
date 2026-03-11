"""Tests for LangGraph recovery shadow trace."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_runner import CommandResult
from app.langgraph_recovery_shadow import LangGraphRecoveryShadowRunner
from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.recovery_runtime import RecoveryRuntime


class FakeTemplateRunner:
    """Minimal helper runner for recovery shadow tests."""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        del template_name, variables, cwd
        log_writer("[FAKE_TEMPLATE]")
        return CommandResult(
            command="fake helper",
            exit_code=0,
            stdout=self.stdout,
            stderr="",
            duration_seconds=0.0,
        )


def _make_job(job_id: str = "job-langgraph-recovery-shadow") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=109,
        issue_title="recovery shadow test",
        issue_url="https://github.com/owner/repo/issues/109",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=3,
        branch_name="agenthub/issue-109-recovery-shadow",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(
    tmp_path: Path,
    *,
    stage_results: list[bool],
    feature_flags: dict[str, bool] | None = None,
    helper_stdout: str = "",
):
    repository_path = tmp_path / "repo"
    repository_path.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "job.log"
    log_entries: list[tuple[str, str]] = []
    fix_calls: list[str] = []
    commit_calls: list[str] = []
    runner = FakeTemplateRunner(stdout=helper_stdout)
    results = list(stage_results)
    call_state = {"index": 0}

    def docs_file(base_path: Path, name: str) -> Path:
        target = base_path / "_docs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def stage_run_tests(*, job, repository_path: Path, stage: JobStage, log_path: Path) -> bool:
        del job, log_path
        index = min(call_state["index"], len(results) - 1)
        passed = results[index]
        call_state["index"] += 1
        if not passed:
            (repository_path / f"TEST_FAILURE_REASON_{stage.value.upper()}.md").write_text(
                "# TEST FAILURE REASON\n\n- Reason: test failed\n",
                encoding="utf-8",
            )
        return passed

    runtime = RecoveryRuntime(
        command_templates=runner,
        stage_run_tests=stage_run_tests,
        append_actor_log=lambda log_path, actor, message: log_entries.append((actor, message)),
        stage_fix_with_codex=lambda job, repository_path, paths, log_path: fix_calls.append(job.job_id),
        commit_markdown_changes_after_stage=lambda job, repository_path, stage_name, log_path: commit_calls.append(stage_name),
        is_recovery_mode_enabled=lambda: True,
        find_configured_template_for_route=lambda route: route if route == "codex_helper" else None,
        template_for_route=lambda route: route,
        build_template_variables=lambda job, docs, prompt_path: {"prompt_path": str(prompt_path)},
        docs_file=docs_file,
        actor_log_writer=lambda log_path, actor: lambda message: log_entries.append((actor, message)),
        is_escalation_enabled=lambda: False,
        run_optional_escalation=lambda job_id, log_path, reason: None,
        feature_enabled=lambda flag_name: bool((feature_flags or {}).get(flag_name, False)),
        recovery_shadow_runner=LangGraphRecoveryShadowRunner(),
    )
    return {
        "runtime": runtime,
        "job": _make_job(),
        "repository_path": repository_path,
        "log_path": log_path,
        "logs": log_entries,
        "fix_calls": fix_calls,
        "commit_calls": commit_calls,
    }


def test_langgraph_recovery_shadow_runner_replays_recoverable_session() -> None:
    runner = LangGraphRecoveryShadowRunner()

    payload = runner.run(
        stage=JobStage.TEST_AFTER_IMPLEMENT.value,
        gate_label="after_implement",
        reason="soft gate failure",
        analysis_written=True,
        recoverable=True,
        recovery_attempted=True,
        recovery_succeeded=True,
    )

    assert payload["status"] == "completed"
    assert payload["framework"] == "langgraph"
    assert [item["node"] for item in payload["trace"]] == [
        "analyze_failure",
        "decide_recoverable",
        "fix_once",
        "retest",
    ]


def test_recovery_runtime_writes_disabled_shadow_artifact_when_flag_off(tmp_path: Path) -> None:
    built = _build_runtime(
        tmp_path,
        stage_results=[True],
        feature_flags={"langgraph_recovery_shadow": False},
        helper_stdout="# FAILURE ANALYSIS\n\n- root cause\n",
    )
    (built["repository_path"] / f"TEST_FAILURE_REASON_{JobStage.TEST_AFTER_FIX.value.upper()}.md").write_text(
        "# TEST FAILURE REASON\n\n- Reason: test failed\n",
        encoding="utf-8",
    )

    recovered = built["runtime"].try_recovery_flow(
        job=built["job"],
        repository_path=built["repository_path"],
        paths={},
        log_path=built["log_path"],
        stage=JobStage.TEST_AFTER_FIX,
        gate_label="after_fix",
        reason="soft gate failure",
    )

    assert recovered is True
    payload = json.loads((built["repository_path"] / "_docs" / "LANGGRAPH_RECOVERY_SHADOW.json").read_text(encoding="utf-8"))
    assert payload["enabled"] is False
    assert payload["status"] == "disabled"
    assert payload["detail"] == "feature_flag_disabled"


def test_recovery_runtime_writes_shadow_session_when_flag_on(tmp_path: Path) -> None:
    built = _build_runtime(
        tmp_path,
        stage_results=[True],
        feature_flags={"langgraph_recovery_shadow": True},
        helper_stdout="# FAILURE ANALYSIS\n\n- root cause\n",
    )
    (built["repository_path"] / f"TEST_FAILURE_REASON_{JobStage.TEST_AFTER_FIX.value.upper()}.md").write_text(
        "# TEST FAILURE REASON\n\n- Reason: test failed\n",
        encoding="utf-8",
    )

    recovered = built["runtime"].try_recovery_flow(
        job=built["job"],
        repository_path=built["repository_path"],
        paths={},
        log_path=built["log_path"],
        stage=JobStage.TEST_AFTER_FIX,
        gate_label="after_fix",
        reason="soft gate failure",
    )

    assert recovered is True
    payload = json.loads((built["repository_path"] / "_docs" / "LANGGRAPH_RECOVERY_SHADOW.json").read_text(encoding="utf-8"))
    assert payload["enabled"] is True
    assert payload["status"] == "completed"
    assert payload["session_count"] == 1
    session = payload["sessions"][0]
    assert session["gate_label"] == "after_fix"
    assert session["recoverable"] is True
    assert session["recovery_attempted"] is True
    assert session["recovery_succeeded"] is True
    assert [item["node"] for item in session["trace"]] == [
        "analyze_failure",
        "decide_recoverable",
        "fix_once",
        "retest",
    ]
