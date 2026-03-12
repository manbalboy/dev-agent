from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.workflow_resolution_runtime import WorkflowResolutionRuntime


class _Store:
    def __init__(self, *, node_runs=None) -> None:
        self.node_runs = list(node_runs or [])
        self.updated: list[tuple[str, dict[str, object]]] = []

    def list_node_runs(self, job_id: str):
        del job_id
        return list(self.node_runs)

    def update_job(self, job_id: str, **kwargs) -> None:
        self.updated.append((job_id, kwargs))


def test_load_active_workflow_returns_none_and_logs_validation_failure(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workflows.json").write_text(
        '{\n'
        '  "default_workflow_id": "wf-default",\n'
        '  "workflows": [\n'
        '    {\n'
        '      "workflow_id": "wf-default",\n'
        '      "entry_node_id": "n1",\n'
        '      "nodes": []\n'
        '    }\n'
        '  ]\n'
        '}\n',
        encoding="utf-8",
    )
    (config_dir / "apps.json").write_text('{"apps":[]}\n', encoding="utf-8")

    actor_logs: list[tuple[str, str, str]] = []
    runtime = WorkflowResolutionRuntime(
        store=_Store(),
        append_actor_log=lambda log_path, actor, message: actor_logs.append((str(log_path), actor, message)),
        read_improvement_runtime_context=lambda paths: {},
    )
    job = SimpleNamespace(job_id="job-1", workflow_id="", app_code="app", repository="owner/repo")

    previous_cwd = Path.cwd()
    try:
        import os

        os.chdir(tmp_path)
        selected = runtime.load_active_workflow(job=job, log_path=tmp_path / "job.log")
    finally:
        os.chdir(previous_cwd)

    assert selected is None
    assert any("Workflow validation failed" in message for _, _, message in actor_logs)


def test_resolve_workflow_resume_state_clears_manual_resume_fields(tmp_path: Path) -> None:
    store = _Store(
        node_runs=[
            {
                "attempt": 1,
                "node_id": "n2",
                "node_type": "write_spec",
                "status": "success",
            },
            {
                "attempt": 1,
                "node_id": "n3",
                "node_type": "gemini_plan",
                "status": "failed",
            },
        ]
    )
    runtime = WorkflowResolutionRuntime(
        store=store,
        append_actor_log=lambda log_path, actor, message: None,
        read_improvement_runtime_context=lambda paths: {},
    )
    job = SimpleNamespace(
        job_id="job-2",
        attempt=2,
        manual_resume_mode="resume_from_node",
        manual_resume_node_id="n3",
        manual_resume_note="retry from n3",
    )
    workflow = {"workflow_id": "wf-1"}
    ordered_nodes = [
        {"id": "n1", "type": "gh_read_issue"},
        {"id": "n2", "type": "write_spec"},
        {"id": "n3", "type": "gemini_plan"},
    ]

    result = runtime.resolve_workflow_resume_state(
        job=job,
        repository_path=tmp_path,
        workflow=workflow,
        ordered_nodes=ordered_nodes,
    )

    assert result["mode"] == "resume"
    assert result["resume_from_node_id"] == "n3"
    assert store.updated == [
        (
            "job-2",
            {
                "manual_resume_mode": "",
                "manual_resume_node_id": "",
                "manual_resume_requested_at": None,
                "manual_resume_note": "",
            },
        )
    ]
