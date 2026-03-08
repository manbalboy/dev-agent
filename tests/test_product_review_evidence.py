"""Tests for evidence-backed product review state signals."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import JobRecord, JobStage, JobStatus, utc_now_iso
from app.orchestrator import Orchestrator


class DummyTemplateRunner:
    """Minimal template runner for stages that do not invoke AI templates."""

    def has_template(self, template_name: str) -> bool:
        return False

    def run_template(self, template_name: str, variables: dict[str, str], cwd: Path, log_writer):
        raise AssertionError(f"unexpected template execution: {template_name}")


def _make_job(job_id: str = "job-product-review") -> JobRecord:
    now = utc_now_iso()
    return JobRecord(
        job_id=job_id,
        repository="owner/repo",
        issue_number=88,
        issue_title="상태 처리 검증",
        issue_url="https://github.com/owner/repo/issues/88",
        status=JobStatus.QUEUED.value,
        stage=JobStage.QUEUED.value,
        attempt=0,
        max_attempts=2,
        branch_name=f"agenthub/issue-88-{job_id}",
        pr_url=None,
        error_message=None,
        log_file=f"{job_id}.log",
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


def _build_paths(repository_path: Path) -> dict[str, Path]:
    return {
        "spec": Orchestrator._docs_file(repository_path, "SPEC.md"),
        "spec_json": Orchestrator._docs_file(repository_path, "SPEC.json"),
        "product_brief": Orchestrator._docs_file(repository_path, "PRODUCT_BRIEF.md"),
        "user_flows": Orchestrator._docs_file(repository_path, "USER_FLOWS.md"),
        "mvp_scope": Orchestrator._docs_file(repository_path, "MVP_SCOPE.md"),
        "architecture_plan": Orchestrator._docs_file(repository_path, "ARCHITECTURE_PLAN.md"),
        "scaffold_plan": Orchestrator._docs_file(repository_path, "SCAFFOLD_PLAN.md"),
        "plan": Orchestrator._docs_file(repository_path, "PLAN.md"),
        "review": Orchestrator._docs_file(repository_path, "REVIEW.md"),
        "product_review": Orchestrator._docs_file(repository_path, "PRODUCT_REVIEW.json"),
    }


def _write_minimum_review_docs(paths: dict[str, Path], *, job: JobRecord) -> None:
    paths["spec"].write_text("# SPEC\n\n- Goal: 상태 처리를 검증한다.\n", encoding="utf-8")
    paths["spec_json"].write_text(
        json.dumps({"goal": "상태 처리 검증"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
                "- 상태 표시를 검증한다.",
            ]
        ),
        encoding="utf-8",
    )
    paths["user_flows"].write_text(
        "# USER FLOWS\n\n## Primary Flow\n1. 페이지 진입\n2. 결과 확인\n",
        encoding="utf-8",
    )
    paths["mvp_scope"].write_text(
        "# MVP SCOPE\n\n## In Scope\n- 결과 확인\n\n## Out Of Scope\n- 외부 공유\n",
        encoding="utf-8",
    )
    paths["architecture_plan"].write_text(
        "# ARCHITECTURE PLAN\n\n## Layers\n- UI\n\n## Quality Gates\n- smoke test\n",
        encoding="utf-8",
    )
    paths["scaffold_plan"].write_text(
        "# SCAFFOLD PLAN\n\n## Verification Checklist\n- [ ] start\n",
        encoding="utf-8",
    )
    paths["plan"].write_text("# PLAN\n\n- 작은 범위만 구현한다.\n", encoding="utf-8")
    paths["review"].write_text("# REVIEW\n- [ ] TODO: message polish\n", encoding="utf-8")


def test_product_review_state_signals_ignore_backend_keyword_noise(app_components):
    settings, store, _ = app_components
    job = _make_job("job-review-backend-noise")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _build_paths(repository_path)
    _write_minimum_review_docs(paths, job=job)

    backend_dir = repository_path / "src"
    backend_dir.mkdir(parents=True, exist_ok=True)
    (backend_dir / "service.py").write_text(
        "\n".join(
            [
                "def run_service():",
                "    try:",
                "        raise ValueError('error')",
                "    except Exception as error:",
                "        print('error', error)",
            ]
        ),
        encoding="utf-8",
    )

    orchestrator = Orchestrator(settings, store, DummyTemplateRunner())
    evidence = orchestrator._collect_product_review_evidence(
        repository_path=repository_path,
        paths=paths,
        spec_text=paths["spec"].read_text(encoding="utf-8"),
        plan_text=paths["plan"].read_text(encoding="utf-8"),
        review_text=paths["review"].read_text(encoding="utf-8"),
        ux_review_text="",
        test_report_paths=[],
        todo_items=["message polish"],
    )

    source_summary = evidence["source_summary"]
    state_signals = evidence["state_signals"]

    assert source_summary["analyzed_source_file_count"] >= 1
    assert source_summary["analyzed_ui_file_count"] == 0
    assert state_signals["error"]["source_hits"] == 0
    assert state_signals["empty"]["source_hits"] == 0
    assert state_signals["loading"]["source_hits"] == 0


def test_product_review_state_score_uses_boolean_ui_presence(app_components):
    settings, store, _ = app_components
    job = _make_job("job-review-ui-signal")
    store.create_job(job)

    repository_path = settings.repository_workspace_path(job.repository, job.app_code)
    repository_path.mkdir(parents=True, exist_ok=True)
    paths = _build_paths(repository_path)
    _write_minimum_review_docs(paths, job=job)

    ui_dir = repository_path / "src" / "components"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "ErrorPanel.tsx").write_text(
        "\n".join(
            [
                "export function ErrorPanel() {",
                "  const message = 'error error error failed retry alert toast fallback';",
                "  return <div role='alert'>{message}</div>;",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    log_path = settings.logs_debug_dir / job.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    orchestrator = Orchestrator(settings, store, DummyTemplateRunner())
    orchestrator._stage_product_review(job, repository_path, paths, log_path)

    payload = json.loads(paths["product_review"].read_text(encoding="utf-8"))

    assert payload["scores"]["error_state_handling"] == 2
    assert payload["category_evidence"]["error_state_handling"]["source_hits"] == 1
    assert payload["category_evidence"]["error_state_handling"]["doc_hits"] == 0
    assert payload["evidence_summary"]["state_signal_totals"]["error"] == 1

