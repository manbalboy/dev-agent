"""Tool request parsing and execution helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional

from app.mcp_tool_client import MCPToolClient
from app.models import JobRecord


@dataclass(frozen=True)
class ToolRequest:
    """Normalized tool request emitted by one agent route."""

    tool: str
    query: str
    reason: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Normalized tool execution result."""

    ok: bool
    mode: str
    context_path: str
    result_path: str
    context_text: str
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ToolRuntime:
    """Shared registry-backed tool runtime."""

    def __init__(
        self,
        *,
        command_templates,
        docs_file: Callable[[Path, str], Path],
        build_template_variables,
        template_for_route: Callable[[str], str],
        actor_log_writer,
        append_actor_log: Callable[[Path, str, str], None],
        build_local_evidence_fallback,
        feature_enabled: Callable[[str], bool] | None = None,
        mcp_tool_client: MCPToolClient | None = None,
    ) -> None:
        self.command_templates = command_templates
        self.docs_file = docs_file
        self.build_template_variables = build_template_variables
        self.template_for_route = template_for_route
        self.actor_log_writer = actor_log_writer
        self.append_actor_log = append_actor_log
        self.build_local_evidence_fallback = build_local_evidence_fallback
        self.feature_enabled = feature_enabled or (lambda _flag_name: False)
        self.mcp_tool_client = mcp_tool_client or MCPToolClient()
        self._handlers: Dict[str, Callable[..., ToolResult]] = {
            "research_search": self._execute_research_search,
        }

    @staticmethod
    def parse_planner_tool_request(plan_text: str) -> Optional[ToolRequest]:
        """Parse planner TOOL_REQUEST block into a normalized request."""

        text = str(plan_text or "").strip()
        if not text:
            return None
        block_match = re.search(
            r"\[TOOL_REQUEST\](.*?)\[/TOOL_REQUEST\]",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        payload = block_match.group(1) if block_match else text

        tool_match = re.search(
            r"^\s*tool\s*:\s*([a-zA-Z0-9_\-]+)\s*$",
            payload,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        query_match = re.search(
            r"^\s*query\s*:\s*(.+?)\s*$",
            payload,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        reason_match = re.search(
            r"^\s*reason\s*:\s*(.+?)\s*$",
            payload,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if not tool_match or not query_match:
            return None

        tool = tool_match.group(1).strip().lower()
        query = query_match.group(1).strip()
        reason = reason_match.group(1).strip() if reason_match else ""
        if tool != "research_search" or not query:
            return None
        return ToolRequest(tool=tool, query=query[:240], reason=reason[:240])

    def execute(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        request: ToolRequest,
    ) -> ToolResult:
        """Execute one normalized tool request via registry."""

        handler = self._handlers.get(request.tool)
        if handler is None:
            raise ValueError(f"unsupported tool: {request.tool}")
        result = handler(
            job=job,
            repository_path=repository_path,
            paths=paths,
            log_path=log_path,
            request=request,
        )
        self._run_shadow_if_enabled(
            log_path=log_path,
            repository_path=repository_path,
            request=request,
            primary_result=result,
        )
        return result

    @staticmethod
    def build_planner_tool_context_addendum(*, request: ToolRequest, result: ToolResult) -> str:
        """Build prompt addendum after tool execution."""

        return (
            "\n\n[Tool response context]\n"
            f"- requested_tool: {request.tool}\n"
            f"- query: {request.query}\n"
            f"- mode: {result.mode}\n"
            f"- context_file: {result.context_path}\n"
            "- 아래 근거를 반영해 TOOL_REQUEST가 아닌 최종 PLAN.md 본문을 작성하세요.\n\n"
            f"{result.context_text}\n"
        )

    def _execute_research_search(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        request: ToolRequest,
    ) -> ToolResult:
        """Execute the legacy research_search path via the new registry."""

        search_context_path = self.docs_file(repository_path, "SEARCH_CONTEXT.md")
        search_result_path = self.docs_file(repository_path, "SEARCH_RESULT.json")
        prompt_path = self.docs_file(repository_path, "PLANNER_TOOL_REQUEST.md")
        prompt_path.write_text(
            (
                "# Planner Tool Request\n\n"
                f"- tool: {request.tool}\n"
                f"- query: {request.query}\n"
                f"- reason: {request.reason}\n"
            ),
            encoding="utf-8",
        )

        variables = self.build_template_variables(job, paths, prompt_path)
        variables["query"] = request.query
        try:
            self.command_templates.run_template(
                template_name=self.template_for_route(request.tool),
                variables=variables,
                cwd=repository_path,
                log_writer=self.actor_log_writer(log_path, "PLANNER"),
            )
            legacy_context_path = repository_path / "SEARCH_CONTEXT.md"
            legacy_result_path = repository_path / "SEARCH_RESULT.json"
            if not search_context_path.exists() and legacy_context_path.exists():
                search_context_path.write_text(
                    legacy_context_path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            if not search_result_path.exists() and legacy_result_path.exists():
                search_result_path.write_text(
                    legacy_result_path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )

            context_text = ""
            if search_context_path.exists():
                context_text = search_context_path.read_text(encoding="utf-8", errors="replace").strip()
            if not context_text:
                context_text = "검색 도구가 실행되었지만 SEARCH_CONTEXT.md 본문이 비어 있습니다."
            return ToolResult(
                ok=True,
                mode="search_api",
                context_path=str(search_context_path),
                result_path=str(search_result_path),
                context_text=context_text[:20_000],
            )
        except Exception as error:  # noqa: BLE001
            self.append_actor_log(
                log_path,
                "ORCHESTRATOR",
                f"research_search failed. Fallback to local evidence pack: {error}",
            )
            fallback = self.build_local_evidence_fallback(
                repository_path,
                paths,
                request.query,
                str(error),
            )
            search_context_path.write_text(fallback["context_text"], encoding="utf-8")
            search_result_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "mode": "fallback_local",
                        "query": request.query,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return ToolResult(
                ok=False,
                mode="fallback_local",
                context_path=str(search_context_path),
                result_path=str(search_result_path),
                context_text=str(fallback.get("context_text", "")).strip()[:20_000],
                error=str(error),
            )

    def _run_shadow_if_enabled(
        self,
        *,
        log_path: Path,
        repository_path: Path,
        request: ToolRequest,
        primary_result: ToolResult,
    ) -> None:
        """Run MCP shadow adapter without affecting the primary tool result."""

        if not self.feature_enabled("mcp_tools_shadow"):
            return
        shadow_result = self.mcp_tool_client.call_tool_shadow(
            tool_name=request.tool,
            arguments={"query": request.query, "reason": request.reason},
        )
        trace_path = self.docs_file(repository_path, "MCP_TOOL_SHADOW.jsonl")
        payload = {
            "tool": request.tool,
            "query": request.query,
            "reason": request.reason,
            "primary_result": primary_result.to_dict(),
            "shadow_result": shadow_result.to_dict(),
        }
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"MCP shadow recorded for tool={request.tool} detail={shadow_result.detail}",
        )
