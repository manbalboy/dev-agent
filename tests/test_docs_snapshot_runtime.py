from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.docs_snapshot_runtime import DocsSnapshotRuntime


def _docs_file(repository_path: Path, name: str) -> Path:
    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return docs_dir / name


def test_snapshot_plan_variant_writes_mode_specific_file(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    plan_path = _docs_file(repository_path, "PLAN.md")
    plan_path.write_text("# plan\n", encoding="utf-8")
    actor_logs: list[tuple[str, str, str]] = []
    runtime = DocsSnapshotRuntime(
        settings=SimpleNamespace(data_dir=tmp_path / "data", enable_stage_md_commits=True),
        run_shell=lambda **kwargs: SimpleNamespace(stdout=""),
        docs_file=_docs_file,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        prepare_commit_summary_with_ai=lambda **kwargs: "",
    )

    runtime.snapshot_plan_variant(
        repository_path=repository_path,
        paths={"plan": plan_path},
        planning_mode="dev_planning",
        log_path=tmp_path / "job.log",
    )

    assert _docs_file(repository_path, "PLAN_DEV.md").read_text(encoding="utf-8") == "# plan\n"
    assert any("Plan snapshot saved: PLAN_DEV.md" in message for _, _, message in actor_logs)


def test_commit_markdown_changes_after_stage_writes_snapshot_and_commits(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    (repository_path / "README.md").write_text("# readme\n", encoding="utf-8")
    (repository_path / "src").mkdir()
    (repository_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _docs_file(repository_path, "SPEC.md").write_text("# spec\n", encoding="utf-8")

    commands: list[str] = []
    actor_logs: list[tuple[str, str, str]] = []

    def run_shell(*, command: str, cwd: Path, log_path: Path, purpose: str):
        del cwd, log_path
        commands.append(command)
        if purpose.startswith("git status all changes"):
            return SimpleNamespace(stdout=" M README.md\n M src/app.py\n")
        if purpose.startswith("git status md changes"):
            return SimpleNamespace(stdout=" M README.md\n")
        return SimpleNamespace(stdout="")

    runtime = DocsSnapshotRuntime(
        settings=SimpleNamespace(data_dir=tmp_path / "data", enable_stage_md_commits=True),
        run_shell=run_shell,
        docs_file=_docs_file,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        prepare_commit_summary_with_ai=lambda **kwargs: "문서 정리",
    )

    runtime.commit_markdown_changes_after_stage(
        job=SimpleNamespace(job_id="job-1", attempt=2, issue_number=17),
        repository_path=repository_path,
        stage_name="write_spec",
        log_path=tmp_path / "job.log",
    )

    snapshot_path = tmp_path / "data" / "md_snapshots" / "job-1" / "attempt_2_write_spec.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["stage"] == "write_spec"
    assert any(item["path"] == "README.md" for item in payload["md_files"])
    assert any(item["path"] == "_docs/SPEC.md" for item in payload["md_files"])
    assert any(item["path"] == "src/app.py" for item in payload["file_snapshots"])
    assert any("add --" in command for command in commands)
    assert any("commit -m 'docs(stage): 문서 정리'" in command for command in commands)
    assert any("Markdown snapshot committed" in message for _, _, message in actor_logs)


def test_commit_markdown_changes_after_stage_skips_prompt_only_docs(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    _docs_file(repository_path, "PLANNER_PROMPT.md").write_text("# prompt\n", encoding="utf-8")

    commands: list[str] = []
    actor_logs: list[tuple[str, str, str]] = []

    def run_shell(*, command: str, cwd: Path, log_path: Path, purpose: str):
        del cwd, log_path
        commands.append(command)
        if purpose.startswith("git status all changes"):
            return SimpleNamespace(stdout=" M _docs/PLANNER_PROMPT.md\n")
        if purpose.startswith("git status md changes"):
            return SimpleNamespace(stdout=" M _docs/PLANNER_PROMPT.md\n")
        return SimpleNamespace(stdout="")

    runtime = DocsSnapshotRuntime(
        settings=SimpleNamespace(data_dir=tmp_path / "data", enable_stage_md_commits=True),
        run_shell=run_shell,
        docs_file=_docs_file,
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        prepare_commit_summary_with_ai=lambda **kwargs: "",
    )

    runtime.commit_markdown_changes_after_stage(
        job=SimpleNamespace(job_id="job-2", attempt=1, issue_number=4),
        repository_path=repository_path,
        stage_name="plan_with_gemini",
        log_path=tmp_path / "job.log",
    )

    assert not any(" add -- " in command or command.endswith(" add -- ':(glob)**/*.md'") for command in commands)
    assert any("Skipped markdown commit" in message for _, _, message in actor_logs)
