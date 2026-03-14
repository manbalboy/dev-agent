"""Assistant diagnosis loop and observability context runtime for dashboard routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.config import AppSettings
from app.dashboard_job_artifact_runtime import DashboardJobArtifactRuntime
from app.dashboard_job_runtime import DashboardJobRuntime
from app.models import JobRecord, JobStatus
from app.store import JobStore
from app.tool_runtime import ToolRequest, ToolRuntime


class DashboardAssistantDiagnosisRuntime:
    """Encapsulate diagnosis trace generation and assistant runtime context."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        feature_flags_config_path: Path,
        artifact_runtime: DashboardJobArtifactRuntime,
        get_memory_runtime_store: Callable[[AppSettings], Any],
        read_feature_flags: Callable[[Path], Dict[str, Any]],
        build_workflow_artifact_paths: Callable[[Path], Dict[str, Path]],
        utc_now_iso: Callable[[], str],
    ) -> None:
        self.settings = settings
        self.feature_flags_config_path = feature_flags_config_path
        self.artifact_runtime = artifact_runtime
        self.get_memory_runtime_store = get_memory_runtime_store
        self.read_feature_flags = read_feature_flags
        self.build_workflow_artifact_paths = build_workflow_artifact_paths
        self.utc_now_iso = utc_now_iso

    def build_agent_observability_context(self, store: JobStore) -> str:
        """Build compact runtime context for diagnosis-focused assistant responses."""

        jobs = store.list_jobs()
        if not jobs:
            return "No jobs found."

        sorted_jobs = sorted(jobs, key=lambda item: item.updated_at or "", reverse=True)
        queued = sum(1 for item in jobs if item.status == JobStatus.QUEUED.value)
        running = sum(1 for item in jobs if item.status == JobStatus.RUNNING.value)
        done = sum(1 for item in jobs if item.status == JobStatus.DONE.value)
        failed = sum(1 for item in jobs if item.status == JobStatus.FAILED.value)

        lines: List[str] = []
        lines.append(f"Job summary: total={len(jobs)}, queued={queued}, running={running}, done={done}, failed={failed}")

        recent_running = [item for item in sorted_jobs if item.status == JobStatus.RUNNING.value][:3]
        if recent_running:
            lines.append("Running jobs:")
            for item in recent_running:
                lines.append(
                    f"- {item.job_id} app={item.app_code} track={item.track} "
                    f"stage={item.stage} attempt={item.attempt}/{item.max_attempts} updated={item.updated_at}"
                )

        recent_failed = [item for item in sorted_jobs if item.status == JobStatus.FAILED.value][:3]
        if recent_failed:
            lines.append("Recent failed jobs:")
            for item in recent_failed:
                lines.append(
                    f"- {item.job_id} app={item.app_code} track={item.track} "
                    f"stage={item.stage} error={item.error_message or '-'} updated={item.updated_at}"
                )
                log_path = self.artifact_runtime.resolve_channel_log_path(item.log_file, channel="debug")
                if log_path.exists():
                    lines.append(f"  log_tail({item.log_file}):")
                    lines.extend(
                        [f"    {row}" for row in DashboardJobArtifactRuntime.tail_text_lines(log_path, max_lines=16)]
                    )

        recent_any = sorted_jobs[:3]
        lines.append("Recent jobs:")
        for item in recent_any:
            lines.append(
                f"- {item.job_id} status={item.status} stage={item.stage} "
                f"app={item.app_code} track={item.track} updated={item.updated_at}"
            )

        text = "\n".join(lines).strip()
        if len(text) > 14000:
            return text[:14000] + "\n...(truncated)"
        return text

    @staticmethod
    def assistant_tool_docs_file(repository_path: Path, name: str) -> Path:
        """Return assistant diagnosis tool artifact path under one workspace."""

        docs_dir = repository_path / "_docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / f"ASSISTANT_{name}"

    def derive_assistant_diagnosis_queries(
        self,
        *,
        job: JobRecord,
        question: str,
    ) -> Dict[str, str]:
        """Build compact diagnosis-oriented queries for the internal tool loop."""

        debug_log_path = self.artifact_runtime.resolve_channel_log_path(job.log_file, channel="debug")
        events = self.artifact_runtime.parse_log_events(debug_log_path) if debug_log_path.exists() else []
        latest_command = ""
        latest_error = ""
        for event in reversed(events):
            kind = str(event.get("kind", "")).strip().lower()
            if not latest_command and kind == "run":
                latest_command = str(event.get("message", "")).strip()
            if not latest_error and kind in {"stderr", "done"}:
                latest_error = str(event.get("message", "")).strip()
            if latest_command and latest_error:
                break

        def _collapse(*parts: str) -> str:
            ordered: List[str] = []
            for raw in parts:
                value = str(raw or "").strip()
                if not value or value in ordered:
                    continue
                ordered.append(value)
            return " ".join(ordered)[:240]

        base_error = str(job.error_message or "").strip()
        base_stage = str(job.stage or "").strip()
        issue_title = str(job.issue_title or "").strip()
        return {
            "log_lookup": _collapse(question, base_error, base_stage, latest_error, latest_command),
            "repo_search": _collapse(base_stage, base_error, latest_command, issue_title),
            "memory_search": _collapse(base_error, base_stage, issue_title, latest_error),
        }

    def build_assistant_diagnosis_runtime(self) -> ToolRuntime:
        """Build one minimal internal tool runtime for assistant diagnosis loops."""

        runtime_store = self.get_memory_runtime_store(self.settings)
        return ToolRuntime(
            command_templates=None,
            docs_file=self.assistant_tool_docs_file,
            build_template_variables=lambda *_args, **_kwargs: {},
            template_for_route=lambda route_name: route_name,
            actor_log_writer=lambda *_args, **_kwargs: None,
            append_actor_log=lambda *_args, **_kwargs: None,
            build_local_evidence_fallback=lambda *_args, **_kwargs: {"context_text": ""},
            search_memory_entries=lambda **kwargs: runtime_store.search_entries(**kwargs),
            search_vector_memory_entries=lambda **_kwargs: {},
            feature_enabled=lambda _flag_name: False,
        )

    def run_assistant_diagnosis_loop(
        self,
        *,
        job: JobRecord,
        question: str,
        assistant_scope: str = "log_analysis",
    ) -> Dict[str, Any]:
        """Run one small internal tool diagnosis loop and write a trace artifact."""

        feature_flags = self.read_feature_flags(self.feature_flags_config_path)
        if not bool(feature_flags.get("assistant_diagnosis_loop")):
            return {"enabled": False, "tool_runs": []}

        repository_path = self.settings.repository_workspace_path(DashboardJobRuntime.job_execution_repository(job), job.app_code)
        repository_path.mkdir(parents=True, exist_ok=True)
        paths = self.build_workflow_artifact_paths(repository_path)
        log_path = self.artifact_runtime.resolve_channel_log_path(job.log_file, channel="debug")
        runtime = self.build_assistant_diagnosis_runtime()
        queries = self.derive_assistant_diagnosis_queries(job=job, question=question)
        tool_runs: List[Dict[str, Any]] = []
        context_sections: List[str] = []

        for tool_name in ("log_lookup", "repo_search", "memory_search"):
            query = str(queries.get(tool_name, "")).strip()
            if not query:
                continue
            request = ToolRequest(tool=tool_name, query=query, reason="assistant diagnosis loop")
            try:
                result = runtime.execute(
                    job=job,
                    repository_path=repository_path,
                    paths=paths,
                    log_path=log_path,
                    request=request,
                )
                tool_runs.append(
                    {
                        "tool": tool_name,
                        "query": query,
                        "ok": result.ok,
                        "mode": result.mode,
                        "context_path": result.context_path,
                        "result_path": result.result_path,
                        "error": result.error,
                    }
                )
                if result.context_text:
                    context_sections.append(f"[{tool_name}]\n{result.context_text.strip()}")
            except Exception as error:  # noqa: BLE001
                tool_runs.append(
                    {
                        "tool": tool_name,
                        "query": query,
                        "ok": False,
                        "mode": "error",
                        "context_path": "",
                        "result_path": "",
                        "error": str(error),
                    }
                )

        combined_context = "\n\n".join(section for section in context_sections if section).strip()
        trace_payload = {
            "generated_at": self.utc_now_iso(),
            "enabled": True,
            "job_id": job.job_id,
            "assistant_scope": str(assistant_scope or "log_analysis").strip() or "log_analysis",
            "question": question,
            "tool_runs": tool_runs,
            "combined_context_length": len(combined_context),
        }
        trace_path = repository_path / "_docs" / "ASSISTANT_DIAGNOSIS_TRACE.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            **trace_payload,
            "trace_path": str(trace_path),
            "context_text": combined_context[:20_000],
        }
