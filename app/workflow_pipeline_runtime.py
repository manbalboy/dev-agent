"""Workflow pipeline dispatch/runtime bookkeeping for orchestrator."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.command_runner import CommandExecutionError
from app.models import JobRecord, JobStage, NodeRunRecord, utc_now_iso
from app.workflow_registry import WORKFLOW_NODE_SKIP_AUTO_COMMIT
from app.workflow_resume import build_workflow_artifact_paths


class WorkflowPipelineRuntime:
    """Encapsulate workflow pipeline dispatch outside the main orchestrator."""

    def __init__(self, *, owner: Any) -> None:
        self.owner = owner

    def run_workflow_pipeline(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
        log_path: Path,
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        context: Dict[str, Any] = {
            "issue": None,
            "paths": None,
            "last_node_result": None,
            "results": {},
            "loop_counters": {},
        }
        current_node_id = str(workflow.get("entry_node_id", "")).strip()
        if not current_node_id:
            current_node_id = str(ordered_nodes[0].get("id", "")).strip() if ordered_nodes else ""

        if isinstance(resume_state, dict) and resume_state.get("enabled"):
            current_node_id = str(resume_state.get("resume_from_node_id", "")).strip() or current_node_id
            context["paths"] = build_workflow_artifact_paths(repository_path)
            skipped_nodes = resume_state.get("skipped_nodes", [])
            if isinstance(skipped_nodes, list) and skipped_nodes:
                skipped_labels = ", ".join(
                    str(item.get("id", "")).strip() or str(item.get("type", "")).strip()
                    for item in skipped_nodes
                    if isinstance(item, dict)
                )
                self.owner._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow resume reuses completed nodes: {skipped_labels}",
                )

        nodes_by_id, edges_by_source = self.build_workflow_runtime_maps(workflow, ordered_nodes)
        if not current_node_id:
            raise CommandExecutionError("Workflow has no entry node to execute.")

        step_limit = max(64, len(nodes_by_id) * 8)
        step_count = 0
        while current_node_id:
            step_count += 1
            if step_count > step_limit:
                raise CommandExecutionError(
                    f"Workflow exceeded step limit ({step_limit}). "
                    "Next action: inspect loop edges and loop_until_pass settings."
                )

            node = nodes_by_id.get(current_node_id)
            if not isinstance(node, dict):
                raise CommandExecutionError(f"Workflow node not found during execution: {current_node_id}")

            node_id = str(node.get("id", ""))
            node_type = str(node.get("type", ""))
            node_notes = str(node.get("notes", "")).strip()
            previous_agent_profile = self.owner._agent_profile
            previous_route_role_overrides = dict(self.owner._workflow_route_role_overrides)
            effective_agent_profile = self.owner._workflow_node_agent_profile(node)
            node_route_role_overrides = self.owner._workflow_node_route_role_overrides(node)
            self.owner._append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"Workflow node start: {node_id} ({node_type})",
            )
            if node_notes:
                self.owner._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node note: {node_notes}",
                )
            if effective_agent_profile != previous_agent_profile:
                self.owner._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node agent profile override: {previous_agent_profile} -> {effective_agent_profile}",
                )
            self.owner._agent_profile = effective_agent_profile
            self.owner._workflow_route_role_overrides = node_route_role_overrides
            if node_route_role_overrides:
                binding_items = ", ".join(
                    f"{route_name}->{role_code}"
                    for route_name, role_code in sorted(node_route_role_overrides.items())
                )
                self.owner._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow node role binding: {binding_items}",
                )

            executor = self.owner._resolve_workflow_node_executor(node_type)
            if executor is None:
                self.owner._agent_profile = previous_agent_profile
                self.owner._workflow_route_role_overrides = previous_route_role_overrides
                raise CommandExecutionError(f"Unsupported workflow node type: {node_type}")
            node_run: NodeRunRecord | None = None
            node_event = "success"
            node_status = "success"
            node_error_message: str | None = None
            node_message = ""
            node_exception: Exception | None = None
            try:
                node_run = self.start_node_run(job, workflow, node)
                result = executor(
                    job=job,
                    repository_path=repository_path,
                    node=node,
                    context=context,
                    log_path=log_path,
                )
                normalized_result = self.normalize_workflow_node_result(result)
                node_event = normalized_result["event"]
                node_status = normalized_result["status"]
                node_error_message = normalized_result.get("error_message")
                node_message = normalized_result.get("message", "")
            except Exception as error:
                node_event = "failure"
                node_status = "failed"
                node_error_message = str(error)
                node_exception = error
            finally:
                self.owner._agent_profile = previous_agent_profile
                self.owner._workflow_route_role_overrides = previous_route_role_overrides
            if node_run is not None:
                self.finish_node_run(
                    node_run,
                    status=node_status,
                    error_message=node_error_message,
                )

            if node_message:
                self.owner._append_actor_log(log_path, "ORCHESTRATOR", node_message)

            context["last_node_result"] = {
                "node_id": node_id,
                "node_type": node_type,
                "node_title": str(node.get("title", "")).strip(),
                "event": node_event,
                "status": node_status,
                "error_message": node_error_message or "",
            }
            self.record_workflow_node_result(
                context=context,
                node=node,
                node_run=node_run,
                event=node_event,
                status=node_status,
                error_message=node_error_message,
                route_role_overrides=node_route_role_overrides,
            )

            if node_status == "success" and node_type not in WORKFLOW_NODE_SKIP_AUTO_COMMIT:
                self.owner._commit_markdown_changes_after_stage(job, repository_path, node_type, log_path)

            next_node_id = self.resolve_next_workflow_node_id(
                edges_by_source=edges_by_source,
                node_id=node_id,
                event=node_event,
            )
            if next_node_id:
                self.owner._append_actor_log(
                    log_path,
                    "ORCHESTRATOR",
                    f"Workflow edge selected: {node_id} --{node_event}--> {next_node_id}",
                )
                current_node_id = next_node_id
                continue

            if node_event == "failure":
                if node_exception is not None:
                    raise node_exception
                raise CommandExecutionError(
                    f"Workflow node {node_id} produced failure event without failure edge."
                )

            current_node_id = ""

        self.owner._set_stage(job.job_id, JobStage.FINALIZE, log_path)

    def start_node_run(
        self,
        job: JobRecord,
        workflow: Dict[str, Any],
        node: Dict[str, Any],
    ) -> NodeRunRecord:
        node_run = NodeRunRecord(
            node_run_id=uuid4().hex,
            job_id=job.job_id,
            workflow_id=str(workflow.get("workflow_id", "")).strip(),
            node_id=str(node.get("id", "")).strip(),
            node_type=str(node.get("type", "")).strip(),
            node_title=str(node.get("title", "")).strip(),
            status="running",
            attempt=max(1, int(job.attempt or 0)),
            started_at=utc_now_iso(),
            agent_profile=self.owner._agent_profile,
        )
        self.owner.store.upsert_node_run(node_run)
        return node_run

    def finish_node_run(
        self,
        node_run: NodeRunRecord,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        updated = replace(
            node_run,
            status=status,
            finished_at=utc_now_iso(),
            error_message=error_message,
        )
        self.owner.store.upsert_node_run(updated)

    @staticmethod
    def build_workflow_runtime_maps(
        workflow: Dict[str, Any],
        ordered_nodes: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
        raw_nodes = workflow.get("nodes", [])
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        source_nodes = raw_nodes if isinstance(raw_nodes, list) and raw_nodes else ordered_nodes
        for node in source_nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", "")).strip()
            if node_id:
                nodes_by_id[node_id] = node

        edges_by_source: Dict[str, List[Dict[str, str]]] = {}
        raw_edges = workflow.get("edges", [])
        if isinstance(raw_edges, list):
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    continue
                src = str(edge.get("from", "")).strip()
                dst = str(edge.get("to", "")).strip()
                event = str(edge.get("on", "success")).strip().lower() or "success"
                if not src or not dst:
                    continue
                edges_by_source.setdefault(src, []).append({"to": dst, "on": event})

        return nodes_by_id, edges_by_source

    @staticmethod
    def normalize_workflow_node_result(result: Any) -> Dict[str, str]:
        if result is None:
            return {"event": "success", "status": "success", "message": "", "error_message": ""}
        if isinstance(result, str):
            event = result.strip().lower() or "success"
            return {"event": event, "status": "success", "message": "", "error_message": ""}
        if isinstance(result, dict):
            event = str(result.get("event", "success")).strip().lower() or "success"
            status = str(result.get("status", "success")).strip().lower() or "success"
            message = str(result.get("message", "")).strip()
            error_message = str(result.get("error_message", "")).strip()
            return {
                "event": event,
                "status": status,
                "message": message,
                "error_message": error_message,
            }
        return {"event": "success", "status": "success", "message": "", "error_message": ""}

    @staticmethod
    def resolve_next_workflow_node_id(
        *,
        edges_by_source: Dict[str, List[Dict[str, str]]],
        node_id: str,
        event: str,
    ) -> str:
        outgoing = edges_by_source.get(node_id, [])
        normalized_event = str(event or "success").strip().lower() or "success"
        for edge in outgoing:
            if str(edge.get("on", "")).strip().lower() == normalized_event:
                return str(edge.get("to", "")).strip()
        for edge in outgoing:
            if str(edge.get("on", "")).strip().lower() == "always":
                return str(edge.get("to", "")).strip()
        return ""

    def record_workflow_node_result(
        self,
        *,
        context: Dict[str, Any],
        node: Dict[str, Any],
        node_run: NodeRunRecord | None,
        event: str,
        status: str,
        error_message: str | None,
        route_role_overrides: Optional[Dict[str, str]] = None,
    ) -> None:
        results = context.setdefault("results", {})
        if not isinstance(results, dict):
            results = {}
            context["results"] = results

        artifact_info = self.workflow_result_artifact_info(context)
        result_payload = {
            "node_id": str(node.get("id", "")).strip(),
            "node_type": str(node.get("type", "")).strip(),
            "node_title": str(node.get("title", "")).strip(),
            "event": str(event or "success").strip().lower() or "success",
            "status": str(status or "success").strip().lower() or "success",
            "error_message": str(error_message or "").strip(),
            "attempt": int(getattr(node_run, "attempt", 0) or 0),
            "started_at": str(getattr(node_run, "started_at", "") or ""),
            "finished_at": str(getattr(node_run, "finished_at", "") or ""),
            "agent_profile": str(getattr(node_run, "agent_profile", self.owner._agent_profile) or self.owner._agent_profile),
            "role_code": str(node.get("role_code", "")).strip(),
            "role_preset_id": str(node.get("role_preset_id", "")).strip(),
            "route_role_overrides": dict(route_role_overrides or {}),
            "artifact_keys": artifact_info["keys"],
            "artifacts": artifact_info["paths"],
        }
        results[result_payload["node_id"]] = result_payload

    @staticmethod
    def workflow_result_artifact_info(context: Dict[str, Any]) -> Dict[str, List[str]]:
        paths = context.get("paths")
        if not isinstance(paths, dict):
            return {"keys": [], "paths": []}

        artifact_keys: List[str] = []
        artifact_paths: List[str] = []
        for key, raw_path in paths.items():
            if not isinstance(raw_path, Path) or not raw_path.exists():
                continue
            artifact_keys.append(str(key))
            artifact_paths.append(str(raw_path))
        artifact_keys.sort()
        artifact_paths.sort()
        return {"keys": artifact_keys, "paths": artifact_paths}
