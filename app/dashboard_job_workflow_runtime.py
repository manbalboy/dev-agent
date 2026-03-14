"""Workflow-resolution read helper runtime for dashboard job routes."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Tuple

from app.models import JobRecord, JobStatus


class DashboardJobWorkflowRuntime:
    """Encapsulate workflow resolution, resume state, and manual retry helpers."""

    def __init__(
        self,
        *,
        apps_config_path: Path,
        workflows_config_path: Path,
        load_workflows: Callable[[Path], Dict[str, Any]],
        default_workflow_template: Callable[[], Dict[str, Any]],
        resolve_workflow_selection: Callable[..., Any],
        validate_workflow: Callable[[Dict[str, Any]], Tuple[bool, List[str]]],
        linearize_workflow_nodes: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
        job_workspace_path: Callable[[JobRecord], Path],
        build_workflow_artifact_paths: Callable[[Path], Dict[str, Path]],
        read_improvement_runtime_context: Callable[[Dict[str, Path]], Dict[str, Any]],
        compute_workflow_resume_state: Callable[..., Dict[str, Any]],
        list_manual_resume_candidates: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    ) -> None:
        self.apps_config_path = apps_config_path
        self.workflows_config_path = workflows_config_path
        self.load_workflows = load_workflows
        self.default_workflow_template = default_workflow_template
        self.resolve_workflow_selection = resolve_workflow_selection
        self.validate_workflow = validate_workflow
        self.linearize_workflow_nodes = linearize_workflow_nodes
        self.job_workspace_path = job_workspace_path
        self.build_workflow_artifact_paths = build_workflow_artifact_paths
        self.read_improvement_runtime_context = read_improvement_runtime_context
        self.compute_workflow_resume_state = compute_workflow_resume_state
        self.list_manual_resume_candidates = list_manual_resume_candidates

    def resolve_job_workflow_runtime(
        self,
        job: JobRecord,
    ) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
        """Resolve one job to workflow metadata plus a validated workflow definition."""

        default_id, workflows_by_id = self._load_workflows_catalog()
        requested_workflow_id = str(job.workflow_id or "").strip()
        selection = self.resolve_workflow_selection(
            requested_workflow_id=requested_workflow_id,
            app_code=job.app_code,
            repository=job.repository,
            apps_path=self.apps_config_path,
            workflows_path=self.workflows_config_path,
        )
        selected = workflows_by_id.get(selection.workflow_id)
        if selected is None and selection.workflow_id != default_id:
            selected = workflows_by_id.get(default_id)

        raw_workflow = selected if isinstance(selected, dict) else {}
        definition_valid = False
        validation_errors: List[str] = []
        ordered_nodes: List[Dict[str, Any]] = []
        nodes_payload: List[Dict[str, Any]] = []
        if raw_workflow:
            definition_valid, validation_errors = self.validate_workflow(raw_workflow)
            if definition_valid:
                ordered_nodes = self.linearize_workflow_nodes(raw_workflow)
        raw_nodes = raw_workflow.get("nodes", []) if isinstance(raw_workflow.get("nodes"), list) else []
        node_source = ordered_nodes if ordered_nodes else [item for item in raw_nodes if isinstance(item, dict)]
        nodes_payload = [
            {
                "id": str(node.get("id", "")).strip(),
                "type": str(node.get("type", "")).strip(),
                "title": str(node.get("title", "")).strip(),
            }
            for node in node_source
        ]

        resolved_workflow_id = str(raw_workflow.get("workflow_id", "")).strip()
        runtime = {
            "requested_workflow_id": requested_workflow_id,
            "resolved_workflow_id": resolved_workflow_id,
            "workflow_name": str(raw_workflow.get("name", "")).strip(),
            "entry_node_id": str(raw_workflow.get("entry_node_id", "")).strip(),
            "default_workflow_id": default_id,
            "resolution_source": str(selection.source or "").strip(),
            "selection_warning": str(selection.warning or "").strip(),
            "definition_available": bool(raw_workflow),
            "definition_valid": definition_valid,
            "validation_errors": validation_errors,
            "uses_fixed_pipeline": not bool(raw_workflow) or not definition_valid,
            "nodes": nodes_payload,
        }
        return runtime, raw_workflow if definition_valid else {}, ordered_nodes

    def resolve_job_workflow_definition(self, job: JobRecord) -> tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
        """Resolve one job to the active workflow definition and ordered nodes."""

        runtime, workflow, ordered_nodes = self.resolve_job_workflow_runtime(job)
        return str(runtime.get("resolved_workflow_id", "")).strip(), workflow, ordered_nodes

    @staticmethod
    def extract_workflow_fallback_events(events: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Extract workflow resolution and fallback signals from parsed debug events."""

        fallback_events: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            message = str(event.get("message", "")).strip()
            if not message:
                continue

            payload: Dict[str, Any] | None = None
            warning_match = re.match(r"^Workflow resolution warning:\s*(.+)$", message)
            if warning_match:
                payload = {
                    "kind": "resolution_warning",
                    "severity": "warn",
                    "title": "선택 경고",
                    "message": str(warning_match.group(1)).strip(),
                    "uses_fixed_pipeline": False,
                }

            default_match = re.match(
                r"^Resolved workflow '([^']+)' missing\. Falling back to default '([^']+)'\.$",
                message,
            )
            if default_match:
                payload = {
                    "kind": "default_fallback",
                    "severity": "warn",
                    "title": "기본 workflow로 전환",
                    "message": (
                        f"등록되지 않은 workflow '{default_match.group(1)}' 대신 "
                        f"'{default_match.group(2)}'를 사용했습니다."
                    ),
                    "uses_fixed_pipeline": False,
                }

            validation_match = re.match(
                r"^Workflow validation failed; fallback to fixed pipeline:\s*(.+)$",
                message,
            )
            if validation_match:
                payload = {
                    "kind": "validation_failure",
                    "severity": "error",
                    "title": "Workflow validation 실패",
                    "message": str(validation_match.group(1)).strip(),
                    "uses_fixed_pipeline": True,
                }

            load_match = re.match(
                r"^Workflow load failed; fallback to fixed pipeline:\s*(.+)$",
                message,
            )
            if load_match:
                payload = {
                    "kind": "load_failure",
                    "severity": "error",
                    "title": "Workflow 로드 실패",
                    "message": str(load_match.group(1)).strip(),
                    "uses_fixed_pipeline": True,
                }

            if payload is None:
                continue

            dedupe_key = (str(payload.get("kind", "")), str(payload.get("message", "")))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            payload["timestamp"] = str(event.get("timestamp", "")).strip()
            fallback_events.append(payload)
        return fallback_events

    def compute_job_resume_payload(
        self,
        job: JobRecord,
        node_runs: List[Any],
    ) -> Dict[str, Any]:
        """Predict resume mode for the current or next execution attempt."""

        if job.status == JobStatus.DONE.value:
            return {
                "enabled": False,
                "mode": "none",
                "reason_code": "job_completed",
                "reason": "작업이 완료되어 재개 대상이 아닙니다.",
                "current_attempt": int(job.attempt or 0),
                "source_attempt": int(job.attempt or 0),
                "failed_node_id": "",
                "failed_node_type": "",
                "failed_node_title": "",
                "resume_from_node_id": "",
                "resume_from_node_type": "",
                "resume_from_node_title": "",
                "resume_from_index": 0,
                "skipped_nodes": [],
            }

        workflow_id, workflow, ordered_nodes = self.resolve_job_workflow_definition(job)
        if not workflow:
            return {
                "enabled": False,
                "mode": "none",
                "reason_code": "workflow_unavailable",
                "reason": "워크플로우를 찾지 못해 재개 전략을 계산할 수 없습니다.",
                "current_attempt": int(job.attempt or 0),
                "source_attempt": 0,
                "failed_node_id": "",
                "failed_node_type": "",
                "failed_node_title": "",
                "resume_from_node_id": "",
                "resume_from_node_type": "",
                "resume_from_node_title": "",
                "resume_from_index": 0,
                "skipped_nodes": [],
            }

        workspace_path = self.job_workspace_path(job)
        improvement_runtime = self.read_improvement_runtime_context(
            self.build_workflow_artifact_paths(workspace_path)
        )
        prospective_attempt = max(1, int(job.attempt or 0))
        if job.status in {JobStatus.FAILED.value, JobStatus.QUEUED.value}:
            prospective_attempt = max(1, prospective_attempt + 1)

        return self.compute_workflow_resume_state(
            workflow_id=workflow_id,
            ordered_nodes=ordered_nodes,
            node_runs=node_runs,
            current_attempt=prospective_attempt,
            strategy=str(improvement_runtime.get("strategy", "")).strip(),
            scope_restriction=str(improvement_runtime.get("scope_restriction", "")).strip(),
            manual_mode=str(job.manual_resume_mode or "").strip(),
            manual_node_id=str(job.manual_resume_node_id or "").strip(),
            manual_note=str(job.manual_resume_note or "").strip(),
        )

    def build_manual_retry_options(
        self,
        job: JobRecord,
        *,
        node_runs: List[Any],
    ) -> Dict[str, Any]:
        """Return dashboard-safe manual resume and rerun options for one job."""

        workflow_id, _, ordered_nodes = self.resolve_job_workflow_definition(job)
        resume_state = self.compute_job_resume_payload(job, node_runs)
        safe_nodes = self.list_manual_resume_candidates(ordered_nodes)
        failed_node_id = str(resume_state.get("failed_node_id", "")).strip()
        can_resume_failed = any(str(item.get("id", "")).strip() == failed_node_id for item in safe_nodes)
        return {
            "workflow_id": workflow_id,
            "safe_nodes": safe_nodes,
            "can_manual_retry": job.status not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value},
            "can_resume_failed_node": can_resume_failed,
            "failed_node_id": failed_node_id,
            "default_mode": "resume_failed_node" if can_resume_failed else "full_rerun",
        }

    def _load_workflows_catalog(self) -> tuple[str, Dict[str, Dict[str, Any]]]:
        """Read workflow catalog with a dashboard-safe fallback."""

        payload = self.load_workflows(self.workflows_config_path)
        default_workflow_id = str(payload.get("default_workflow_id", "")).strip()
        if not default_workflow_id:
            default_workflow_id = self.default_workflow_template()["workflow_id"]

        workflows_by_id: Dict[str, Dict[str, Any]] = {}
        raw_workflows = payload.get("workflows", [])
        if isinstance(raw_workflows, list):
            for item in raw_workflows:
                if not isinstance(item, dict):
                    continue
                workflow_id = str(item.get("workflow_id", "")).strip()
                if workflow_id:
                    workflows_by_id[workflow_id] = item
        return default_workflow_id, workflows_by_id
