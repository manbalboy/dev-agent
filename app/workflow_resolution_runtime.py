"""Workflow loading and resume-resolution runtime for orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.workflow_design import validate_workflow
from app.workflow_resolution import load_workflow_catalog, resolve_workflow_selection
from app.workflow_resume import (
    build_workflow_artifact_paths,
    compute_workflow_resume_state,
    linearize_workflow_nodes,
)


class WorkflowResolutionRuntime:
    """Encapsulate workflow loading and resume state resolution."""

    def __init__(
        self,
        *,
        store,
        append_actor_log,
        read_improvement_runtime_context,
    ) -> None:
        self.store = store
        self.append_actor_log = append_actor_log
        self.read_improvement_runtime_context = read_improvement_runtime_context

    def resolve_workflow_resume_state(
        self,
        *,
        job,
        repository_path: Path,
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        improvement_runtime = self.read_improvement_runtime_context(
            build_workflow_artifact_paths(repository_path)
        )
        resume_state = compute_workflow_resume_state(
            workflow_id=str(workflow.get("workflow_id", "")).strip(),
            ordered_nodes=ordered_nodes,
            node_runs=self.store.list_node_runs(job.job_id),
            current_attempt=max(1, int(job.attempt or 1)),
            strategy=str(improvement_runtime.get("strategy", "")).strip(),
            scope_restriction=str(improvement_runtime.get("scope_restriction", "")).strip(),
            manual_mode=str(job.manual_resume_mode or "").strip(),
            manual_node_id=str(job.manual_resume_node_id or "").strip(),
            manual_note=str(job.manual_resume_note or "").strip(),
        )
        if str(job.manual_resume_mode or "").strip():
            self.store.update_job(
                job.job_id,
                manual_resume_mode="",
                manual_resume_node_id="",
                manual_resume_requested_at=None,
                manual_resume_note="",
            )
        return resume_state

    def load_active_workflow(self, *, job, log_path: Path) -> Optional[Dict[str, Any]]:
        workflow_path = Path.cwd() / "config" / "workflows.json"
        apps_path = Path.cwd() / "config" / "apps.json"
        try:
            default_id, workflows_by_id = load_workflow_catalog(workflow_path)
            if not default_id or not workflows_by_id:
                return None
            selection = resolve_workflow_selection(
                requested_workflow_id=job.workflow_id,
                app_code=job.app_code,
                repository=job.repository,
                apps_path=apps_path,
                workflows_path=workflow_path,
            )
            selected = workflows_by_id.get(selection.workflow_id)
            if selection.warning:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow resolution warning: {selection.warning}",
                )
            if selected is None and selection.workflow_id != default_id:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Resolved workflow '{selection.workflow_id}' missing. Falling back to default '{default_id}'.",
                )
                selected = workflows_by_id.get(default_id)
            if not isinstance(selected, dict):
                return None
            ok, errors = validate_workflow(selected)
            if not ok:
                self.append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    "Workflow validation failed; fallback to fixed pipeline: "
                    + "; ".join(errors),
                )
                return None
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow resolved: source={selection.source}, workflow_id={selected.get('workflow_id', default_id)}",
            )
            return selected
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow load failed; fallback to fixed pipeline: {error}",
            )
            return None

    @staticmethod
    def linearize_workflow_nodes(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        return linearize_workflow_nodes(workflow)
