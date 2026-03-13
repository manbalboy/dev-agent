from __future__ import annotations

import json
from pathlib import Path

from app.design_governance_runtime import DesignGovernanceRuntime


def _make_runtime(tmp_path: Path):
    logs: list[tuple[str, str, str]] = []

    def docs_file(repository_path: Path, name: str) -> Path:
        docs_dir = repository_path / "_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / name

    def sha256_file(path: Path | None) -> str:
        if path is None or not path.exists():
            return ""
        return f"sha::{path.read_text(encoding='utf-8')}"

    def append_actor_log(path: Path, actor: str, message: str) -> None:
        logs.append((str(path), actor, message))

    runtime = DesignGovernanceRuntime(
        docs_file=docs_file,
        sha256_file=sha256_file,
        append_actor_log=append_actor_log,
    )
    return runtime, logs


def test_design_lock_round_trip(tmp_path: Path) -> None:
    runtime, logs = _make_runtime(tmp_path)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    log_path = tmp_path / "job.log"
    paths = {
        "spec": repository_path / "_docs" / "SPEC.md",
        "plan": repository_path / "_docs" / "PLAN.md",
        "design": repository_path / "_docs" / "DESIGN_SYSTEM.md",
    }
    paths["spec"].parent.mkdir(parents=True, exist_ok=True)
    paths["spec"].write_text("spec-a", encoding="utf-8")
    paths["plan"].write_text("plan-a", encoding="utf-8")
    paths["design"].write_text("design-a", encoding="utf-8")

    assert runtime.is_design_system_locked(repository_path, paths) is False

    runtime.lock_design_system_decision(repository_path, paths, log_path)

    assert runtime.is_design_system_locked(repository_path, paths) is True
    assert logs[-1][1:] == ("ORCHESTRATOR", "Design-system decision locked at _docs/DECISIONS.json")


def test_design_lock_breaks_when_plan_changes(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    log_path = tmp_path / "job.log"
    paths = {
        "spec": repository_path / "_docs" / "SPEC.md",
        "plan": repository_path / "_docs" / "PLAN.md",
        "design": repository_path / "_docs" / "DESIGN_SYSTEM.md",
    }
    paths["spec"].parent.mkdir(parents=True, exist_ok=True)
    paths["spec"].write_text("spec-a", encoding="utf-8")
    paths["plan"].write_text("plan-a", encoding="utf-8")
    paths["design"].write_text("design-a", encoding="utf-8")

    runtime.lock_design_system_decision(repository_path, paths, log_path)
    paths["plan"].write_text("plan-b", encoding="utf-8")

    assert runtime.is_design_system_locked(repository_path, paths) is False


def test_read_decisions_payload_ignores_invalid_json(tmp_path: Path) -> None:
    runtime, _ = _make_runtime(tmp_path)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    decisions_path = repository_path / "_docs" / "DECISIONS.json"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text("{invalid", encoding="utf-8")

    assert runtime.read_decisions_payload(repository_path) == {}


def test_write_stage_contracts_and_pipeline_analysis_docs(tmp_path: Path) -> None:
    stage_md = tmp_path / "STAGE_CONTRACTS.md"
    stage_json = tmp_path / "STAGE_CONTRACTS.json"
    pipeline_md = tmp_path / "PIPELINE_ANALYSIS.md"
    pipeline_json = tmp_path / "PIPELINE_ANALYSIS.json"

    DesignGovernanceRuntime.write_stage_contracts_doc(stage_md, stage_json)
    DesignGovernanceRuntime.write_pipeline_analysis_doc(pipeline_md, pipeline_json)

    stage_payload = json.loads(stage_json.read_text(encoding="utf-8"))
    pipeline_payload = json.loads(pipeline_json.read_text(encoding="utf-8"))

    assert stage_payload["schema_version"] == "1.0"
    assert any(stage["name"] == "product_review" for stage in stage_payload["stages"])
    assert "PIPELINE ANALYSIS" in pipeline_md.read_text(encoding="utf-8")
    assert "product_review" in pipeline_payload["current_pipeline"]
