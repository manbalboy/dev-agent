"""Assistant chat and log-analysis helper runtime for dashboard APIs."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Sequence

from fastapi import HTTPException

from app.config import AppSettings
from app.store import JobStore


class DashboardAssistantRuntime:
    """Encapsulate conversational assistant dashboard behavior."""

    def __init__(
        self,
        *,
        store: JobStore,
        settings: AppSettings,
        primary_assistant_providers: Sequence[str],
        assistant_provider_aliases: Mapping[str, str],
        canonical_cli_name: Callable[[str], str],
        build_focus_job_log_context: Callable[..., str],
        build_agent_observability_context: Callable[..., str],
        run_assistant_diagnosis_loop: Callable[..., Dict[str, Any]],
        build_assistant_chat_prompt: Callable[..., str],
        build_log_analysis_prompt: Callable[..., str],
        read_command_templates: Callable[[Any], Dict[str, str]],
        run_assistant_chat_provider: Callable[..., str],
        run_log_analyzer: Callable[..., str],
    ) -> None:
        self.store = store
        self.settings = settings
        self.primary_assistant_providers = {
            str(item or "").strip().lower()
            for item in primary_assistant_providers
            if str(item or "").strip()
        }
        self.assistant_provider_aliases = {
            str(key or "").strip().lower(): str(value or "").strip().lower()
            for key, value in assistant_provider_aliases.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        self._canonical_cli_name = canonical_cli_name
        self._build_focus_job_log_context = build_focus_job_log_context
        self._build_agent_observability_context = build_agent_observability_context
        self._run_assistant_diagnosis_loop = run_assistant_diagnosis_loop
        self._build_assistant_chat_prompt = build_assistant_chat_prompt
        self._build_log_analysis_prompt = build_log_analysis_prompt
        self._read_command_templates = read_command_templates
        self._run_assistant_chat_provider = run_assistant_chat_provider
        self._run_log_analyzer = run_log_analyzer

    def chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run one conversational assistant turn with selected provider."""

        requested_assistant, assistant = self._resolve_assistant(payload.get("assistant", ""))
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raise HTTPException(status_code=400, detail="메시지를 입력해주세요.")

        focus_job_id = str(payload.get("job_id", "")).strip()
        focus_context, diagnosis_trace = self._build_focus_context(
            focus_job_id=focus_job_id,
            question=raw_message,
            assistant_scope="chat",
        )
        runtime_context = self._build_agent_observability_context(self.store, self.settings)
        prompt = self._build_assistant_chat_prompt(
            assistant=assistant,
            message=raw_message,
            history=self._normalize_history(payload.get("history")),
            runtime_context=runtime_context,
            focus_context=focus_context,
            diagnosis_context=str(diagnosis_trace.get("context_text", "")).strip(),
        )
        output_text = self._run_assistant_chat_provider(
            assistant=assistant,
            prompt=prompt,
            templates=self._read_templates(),
        )
        return {
            "ok": True,
            "assistant": output_text,
            "provider": assistant,
            "requested_provider": requested_assistant,
            "focus_job_id": focus_job_id,
            "diagnosis_trace": self._serialize_diagnosis_trace(diagnosis_trace),
        }

    def log_analysis(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze AgentHub logs with one selected assistant CLI."""

        requested_assistant, assistant = self._resolve_assistant(payload.get("assistant", ""))
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=400, detail="question은 비어 있을 수 없습니다.")

        focus_job_id = str(payload.get("job_id", "")).strip()
        focus_context, diagnosis_trace = self._build_focus_context(
            focus_job_id=focus_job_id,
            question=question,
            assistant_scope="log_analysis",
        )
        runtime_context = self._build_agent_observability_context(self.store, self.settings)
        prompt = self._build_log_analysis_prompt(
            assistant=assistant,
            question=question,
            runtime_context=runtime_context,
            focus_context=focus_context,
            diagnosis_context=str(diagnosis_trace.get("context_text", "")).strip(),
        )
        analysis = self._run_log_analyzer(
            assistant=assistant,
            prompt=prompt,
            templates=self._read_templates(),
        )
        return {
            "ok": True,
            "assistant": analysis,
            "provider": assistant,
            "requested_provider": requested_assistant,
            "focus_job_id": focus_job_id,
            "diagnosis_trace": self._serialize_diagnosis_trace(diagnosis_trace),
        }

    def _resolve_assistant(self, requested_assistant: Any) -> tuple[str, str]:
        requested_provider = str(requested_assistant or "").strip().lower()
        allowed = self.primary_assistant_providers | set(self.assistant_provider_aliases)
        if requested_provider not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"지원하지 않는 assistant 입니다: {requested_provider}. "
                    f"공식 지원: {', '.join(sorted(self.primary_assistant_providers))}. "
                    f"호환 별칭: {', '.join(sorted(self.assistant_provider_aliases))}"
                ),
            )
        return requested_provider, self._canonical_cli_name(requested_provider)

    def _build_focus_context(
        self,
        *,
        focus_job_id: str,
        question: str,
        assistant_scope: str,
    ) -> tuple[str, Dict[str, Any]]:
        if not focus_job_id:
            return "", {"enabled": False, "tool_runs": []}

        job = self.store.get_job(focus_job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job_id를 찾을 수 없습니다: {focus_job_id}")
        return (
            self._build_focus_job_log_context(job, self.settings),
            self._run_assistant_diagnosis_loop(
                job=job,
                question=question,
                settings=self.settings,
                assistant_scope=assistant_scope,
            ),
        )

    def _read_templates(self) -> Dict[str, str]:
        try:
            return self._read_command_templates(self.settings.command_config)
        except HTTPException:
            return {}

    @staticmethod
    def _normalize_history(history: Any) -> list[Dict[str, str]]:
        if not isinstance(history, list):
            return []
        normalized: list[Dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "role": str(item.get("role", "")).strip(),
                    "content": str(item.get("content", "")).strip(),
                }
            )
        return normalized

    @staticmethod
    def _serialize_diagnosis_trace(diagnosis_trace: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "enabled": bool(diagnosis_trace.get("enabled")),
            "trace_path": str(diagnosis_trace.get("trace_path", "")).strip(),
            "tool_runs": diagnosis_trace.get("tool_runs", []),
        }
