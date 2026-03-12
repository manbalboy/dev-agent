"""Tests for product-definition runtime extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStatus, utc_now_iso
from app.product_definition_runtime import ProductDefinitionRuntime


def _make_job(job_id: str = "job-product-runtime") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=51,
        issue_title="반려동물 일정 추적 MVP",
        issue_url="https://github.com/owner/repo/issues/51",
        status=JobStatus.QUEUED.value,
        stage="queued",
        attempt=0,
        max_attempts=2,
        branch_name="agenthub/issue-51-product-runtime",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_runtime(logs: list[str]) -> ProductDefinitionRuntime:
    def docs_file(repository_path: Path, name: str) -> Path:
        path = repository_path / "_docs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    class _FakeTemplates:
        def run_template(self, *args, **kwargs):  # pragma: no cover - not used here
            raise AssertionError("run_template should not be called in this test")

    return ProductDefinitionRuntime(
        command_templates=_FakeTemplates(),
        set_stage=lambda *args, **kwargs: None,
        docs_file=docs_file,
        build_template_variables=lambda *args, **kwargs: {},
        actor_log_writer=lambda *args, **kwargs: (lambda message: None),
        template_for_route=lambda route_name: str(route_name),
        append_actor_log=lambda log_path, actor, message: logs.append(f"{actor}:{message}"),
    )


def test_product_definition_runtime_builds_bootstrap_report_for_greenfield(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    spec_json = repository_path / "_docs" / "SPEC.json"
    spec_json.parent.mkdir(parents=True, exist_ok=True)
    spec_json.write_text('{"app_type":"app"}\n', encoding="utf-8")

    payload = ProductDefinitionRuntime.build_bootstrap_report(
        repository_path=repository_path,
        spec_json_path=spec_json,
        repo_context={"exists": True, "stack": [], "readme_excerpt": ""},
    )

    assert payload["repository_state"] == "greenfield"
    assert payload["bootstrap_mode"] == "create"
    assert payload["app_type"] == "app"


def test_product_definition_runtime_hard_gate_reports_missing_sections(tmp_path: Path) -> None:
    logs: list[str] = []
    runtime = _build_runtime(logs)
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "product_brief": docs_dir / "PRODUCT_BRIEF.md",
        "user_flows": docs_dir / "USER_FLOWS.md",
        "mvp_scope": docs_dir / "MVP_SCOPE.md",
        "architecture_plan": docs_dir / "ARCHITECTURE_PLAN.md",
        "scaffold_plan": docs_dir / "SCAFFOLD_PLAN.md",
    }
    paths["product_brief"].write_text(
        "# PRODUCT BRIEF\n## Product Goal\n- goal\n## Target Users\n- users\n## Success Metrics\n- metrics\n",
        encoding="utf-8",
    )
    paths["user_flows"].write_text("# USER FLOWS\n## Primary Flow\n- only primary\n", encoding="utf-8")
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
        runtime.ensure_product_definition_ready(paths, tmp_path / "job.log")

    assert "USER_FLOWS.md" in str(exc.value)
    assert "ux_state_checklist" in str(exc.value)
    assert any("Product-definition hard gate blocked implementation" in line for line in logs)


def test_product_definition_runtime_architecture_fallback_writes_contract(tmp_path: Path) -> None:
    output_path = tmp_path / "ARCHITECTURE_PLAN.md"

    ProductDefinitionRuntime.write_architecture_plan_fallback(_make_job(), {}, output_path)

    content = output_path.read_text(encoding="utf-8")
    assert "## Layer Structure" in content
    assert "## Quality Gates" in content
    assert "## Loop Safety Rules" in content
