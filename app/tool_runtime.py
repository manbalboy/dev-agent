"""Tool request parsing and execution helpers."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional

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
        search_memory_entries: Callable[..., List[Dict[str, Any]]] | None = None,
        search_vector_memory_entries: Callable[..., Dict[str, Any]] | None = None,
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
        self.search_memory_entries = search_memory_entries or (lambda **_kwargs: [])
        self.search_vector_memory_entries = search_vector_memory_entries or (lambda **_kwargs: {})
        self.feature_enabled = feature_enabled or (lambda _flag_name: False)
        self.mcp_tool_client = mcp_tool_client or MCPToolClient()
        self._handlers: Dict[str, Callable[..., ToolResult]] = {
            "log_lookup": self._execute_log_lookup,
            "memory_search": self._execute_memory_search,
            "repo_search": self._execute_repo_search,
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

    def _execute_log_lookup(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        request: ToolRequest,
    ) -> ToolResult:
        """Search the current job logs and write a compact evidence pack."""

        del paths

        context_path = self.docs_file(repository_path, "LOG_LOOKUP_CONTEXT.md")
        result_path = self.docs_file(repository_path, "LOG_LOOKUP_RESULT.json")

        keywords = self._extract_log_lookup_keywords(request.query)
        match_limit = 12
        scanned_files: List[str] = []
        matches: List[Dict[str, Any]] = []
        fallback_excerpt: Dict[str, Any] | None = None

        for candidate_path in self._candidate_log_paths(log_path):
            if not candidate_path.exists():
                continue
            scanned_files.append(str(candidate_path))
            raw_lines = candidate_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if fallback_excerpt is None and raw_lines:
                fallback_excerpt = {
                    "channel": candidate_path.parent.name,
                    "line_number": max(1, len(raw_lines) - 5),
                    "matched_keywords": [],
                    "line": raw_lines[-1][:500],
                    "excerpt": "\n".join(raw_lines[-6:]).strip()[:1200],
                }
            matches.extend(
                self._collect_log_matches(
                    lines=raw_lines,
                    keywords=keywords,
                    channel=candidate_path.parent.name,
                    limit=match_limit - len(matches),
                )
            )
            if len(matches) >= match_limit:
                break
        if not matches and fallback_excerpt is not None:
            matches.append(fallback_excerpt)
        has_keyword_matches = any(match.get("matched_keywords") for match in matches)

        context_text = self._build_log_lookup_context(
            job=job,
            query=request.query,
            keywords=keywords,
            scanned_files=scanned_files,
            matches=matches,
        )
        context_path.write_text(context_text, encoding="utf-8")
        result_payload = {
            "ok": has_keyword_matches,
            "mode": "log_lookup",
            "query": request.query,
            "reason": request.reason,
            "job_id": job.job_id,
            "log_file": job.log_file,
            "keywords": keywords,
            "scanned_files": scanned_files,
            "match_count": len(matches),
            "matches": matches,
        }
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"log_lookup captured {len(matches)} match(es) for query={request.query!r}",
        )
        return ToolResult(
            ok=has_keyword_matches,
            mode="log_lookup",
            context_path=str(context_path),
            result_path=str(result_path),
            context_text=context_text[:20_000],
        )

    def _execute_repo_search(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        request: ToolRequest,
    ) -> ToolResult:
        """Search repository paths and file contents for compact local evidence."""

        del paths

        context_path = self.docs_file(repository_path, "REPO_SEARCH_CONTEXT.md")
        result_path = self.docs_file(repository_path, "REPO_SEARCH_RESULT.json")

        keywords = self._extract_repo_search_keywords(request.query)
        files_scanned = 0
        matches: List[Dict[str, Any]] = []

        for candidate_path in self._candidate_repo_files(repository_path):
            files_scanned += 1
            relative_path = str(candidate_path.relative_to(repository_path))
            relative_lower = relative_path.lower()
            path_hits = [keyword for keyword in keywords if keyword in relative_lower]
            if path_hits:
                matches.append(
                    {
                        "kind": "path",
                        "path": relative_path,
                        "line_number": None,
                        "matched_keywords": path_hits,
                        "excerpt": relative_path,
                    }
                )
            if len(matches) >= 12:
                break

            raw_text = candidate_path.read_text(encoding="utf-8", errors="replace")
            file_matches = self._collect_repo_content_matches(
                file_text=raw_text,
                relative_path=relative_path,
                keywords=keywords,
                limit=12 - len(matches),
            )
            matches.extend(file_matches)
            if len(matches) >= 12:
                break

        has_keyword_matches = any(match.get("matched_keywords") for match in matches)
        context_text = self._build_repo_search_context(
            job=job,
            query=request.query,
            keywords=keywords,
            files_scanned=files_scanned,
            matches=matches,
        )
        context_path.write_text(context_text, encoding="utf-8")
        result_payload = {
            "ok": has_keyword_matches,
            "mode": "repo_search",
            "query": request.query,
            "reason": request.reason,
            "job_id": job.job_id,
            "repository": job.repository,
            "files_scanned": files_scanned,
            "keywords": keywords,
            "match_count": len(matches),
            "matches": matches,
        }
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"repo_search captured {len(matches)} match(es) for query={request.query!r}",
        )
        return ToolResult(
            ok=has_keyword_matches,
            mode="repo_search",
            context_path=str(context_path),
            result_path=str(result_path),
            context_text=context_text[:20_000],
        )

    def _execute_memory_search(
        self,
        *,
        job: JobRecord,
        repository_path: Path,
        paths: Dict[str, Path],
        log_path: Path,
        request: ToolRequest,
    ) -> ToolResult:
        """Search canonical memory runtime entries for the current repo/app/workflow."""

        del paths

        context_path = self.docs_file(repository_path, "MEMORY_SEARCH_CONTEXT.md")
        result_path = self.docs_file(repository_path, "MEMORY_SEARCH_RESULT.json")

        execution_repository = str(job.source_repository or job.repository or "").strip()
        vector_enabled = bool(self.feature_enabled("vector_memory_retrieval"))
        vector_result: Dict[str, Any] = {}
        if vector_enabled:
            vector_result = self.search_vector_memory_entries(
                query=request.query,
                repository=str(job.repository or "").strip(),
                execution_repository=execution_repository,
                app_code=str(job.app_code or "").strip(),
                workflow_id=str(job.workflow_id or "").strip(),
                limit=8,
            )
        vector_items = vector_result.get("items", []) if isinstance(vector_result, dict) else []
        items: List[Dict[str, Any]]
        source = "vector"
        fallback_used = False
        if vector_enabled and isinstance(vector_items, list) and vector_items:
            items = [item for item in vector_items if isinstance(item, dict)]
        else:
            items = self.search_memory_entries(
                query=request.query,
                repository=str(job.repository or "").strip(),
                execution_repository=execution_repository,
                app_code=str(job.app_code or "").strip(),
                workflow_id=str(job.workflow_id or "").strip(),
                limit=8,
            )
            source = "db"
            fallback_used = vector_enabled
        context_text = self._build_memory_search_context(
            job=job,
            query=request.query,
            execution_repository=execution_repository,
            items=items,
            source=source,
        )
        context_path.write_text(context_text, encoding="utf-8")
        result_payload = {
            "ok": bool(items),
            "mode": "memory_search",
            "query": request.query,
            "reason": request.reason,
            "job_id": job.job_id,
            "repository": str(job.repository or "").strip(),
            "execution_repository": execution_repository,
            "app_code": str(job.app_code or "").strip(),
            "workflow_id": str(job.workflow_id or "").strip(),
            "source": source,
            "fallback_used": fallback_used,
            "match_count": len(items),
            "items": items,
            "vector": vector_result if isinstance(vector_result, dict) else {},
        }
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_actor_log(
            log_path,
            "ORCHESTRATOR",
            f"memory_search captured {len(items)} match(es) for query={request.query!r} source={source}",
        )
        return ToolResult(
            ok=bool(items),
            mode="memory_search",
            context_path=str(context_path),
            result_path=str(result_path),
            context_text=context_text[:20_000],
        )

    @staticmethod
    def _extract_search_keywords(query: str) -> List[str]:
        tokens = [
            token.strip().lower()
            for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or ""))
            if len(token.strip()) >= 3
        ]
        if not tokens:
            return []
        ordered: List[str] = []
        for token, _count in Counter(tokens).most_common(8):
            if token not in ordered:
                ordered.append(token)
        return ordered

    @staticmethod
    def _extract_log_lookup_keywords(query: str) -> List[str]:
        return ToolRuntime._extract_search_keywords(query)

    @staticmethod
    def _extract_repo_search_keywords(query: str) -> List[str]:
        return ToolRuntime._extract_search_keywords(query)

    @staticmethod
    def _candidate_log_paths(log_path: Path) -> List[Path]:
        candidates: List[Path] = []
        if log_path.exists():
            candidates.append(log_path)
        parent_name = log_path.parent.name
        if parent_name == "debug":
            candidates.append(log_path.parent.parent / "user" / log_path.name)
            candidates.append(log_path.parent.parent / log_path.name)
        elif parent_name == "user":
            candidates.append(log_path.parent.parent / "debug" / log_path.name)
            candidates.append(log_path.parent.parent / log_path.name)
        deduped: List[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            normalized = candidate.resolve() if candidate.exists() else candidate
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(candidate)
        return deduped

    @staticmethod
    def _collect_log_matches(
        *,
        lines: List[str],
        keywords: List[str],
        channel: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        lowered_keywords = [keyword.lower() for keyword in keywords if keyword]
        matches: List[Dict[str, Any]] = []
        for index, line in enumerate(lines):
            haystack = line.lower()
            if lowered_keywords:
                if not any(keyword in haystack for keyword in lowered_keywords):
                    continue
                matched_keywords = [keyword for keyword in lowered_keywords if keyword in haystack]
            else:
                matched_keywords = []
            excerpt_start = max(0, index - 1)
            excerpt_end = min(len(lines), index + 2)
            excerpt = "\n".join(lines[excerpt_start:excerpt_end]).strip()
            matches.append(
                {
                    "channel": channel,
                    "line_number": index + 1,
                    "matched_keywords": matched_keywords,
                    "line": line[:500],
                    "excerpt": excerpt[:1200],
                }
            )
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _candidate_repo_files(repository_path: Path) -> List[Path]:
        ignored_dir_names = {
            ".git",
            ".venv",
            ".pytest_cache",
            "_docs",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            "coverage",
            ".next",
            ".turbo",
        }
        ignored_suffixes = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".ico",
            ".pdf",
            ".zip",
            ".gz",
            ".tar",
            ".woff",
            ".woff2",
            ".ttf",
            ".otf",
            ".mp3",
            ".mp4",
            ".mov",
            ".lock",
        }

        candidates: List[Path] = []
        for candidate in repository_path.rglob("*"):
            if not candidate.is_file():
                continue
            if any(part in ignored_dir_names for part in candidate.relative_to(repository_path).parts[:-1]):
                continue
            if candidate.suffix.lower() in ignored_suffixes:
                continue
            try:
                if candidate.stat().st_size > 256_000:
                    continue
            except OSError:
                continue
            candidates.append(candidate)
            if len(candidates) >= 400:
                break
        return candidates

    @staticmethod
    def _collect_repo_content_matches(
        *,
        file_text: str,
        relative_path: str,
        keywords: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        lines = file_text.splitlines()
        matches: List[Dict[str, Any]] = []
        for index, line in enumerate(lines):
            haystack = line.lower()
            matched_keywords = [keyword for keyword in keywords if keyword in haystack]
            if not matched_keywords:
                continue
            excerpt_start = max(0, index - 1)
            excerpt_end = min(len(lines), index + 2)
            excerpt = "\n".join(lines[excerpt_start:excerpt_end]).strip()
            matches.append(
                {
                    "kind": "content",
                    "path": relative_path,
                    "line_number": index + 1,
                    "matched_keywords": matched_keywords,
                    "excerpt": excerpt[:1200],
                }
            )
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _build_log_lookup_context(
        *,
        job: JobRecord,
        query: str,
        keywords: List[str],
        scanned_files: List[str],
        matches: List[Dict[str, Any]],
    ) -> str:
        lines = [
            "# LOG LOOKUP CONTEXT",
            "",
            f"- job_id: {job.job_id}",
            f"- log_file: {job.log_file}",
            f"- query: {query}",
            f"- keywords: {', '.join(keywords) if keywords else '(none)'}",
            f"- scanned_files: {', '.join(scanned_files) if scanned_files else '(none)'}",
            f"- match_count: {len(matches)}",
            "",
        ]
        if not matches:
            lines.extend(
                [
                    "검색 키워드와 맞는 로그 라인을 찾지 못했습니다.",
                    "필요하면 query를 더 구체적으로 바꾸거나 stage/actor 이름을 포함해 다시 요청하세요.",
                ]
            )
            return "\n".join(lines).strip() + "\n"

        lines.append("## Matches")
        for index, match in enumerate(matches[:12], start=1):
            matched_keywords = ", ".join(match.get("matched_keywords", []) or []) or "(none)"
            lines.extend(
                [
                    f"### Match {index}",
                    f"- channel: {match.get('channel', '')}",
                    f"- line_number: {match.get('line_number', '')}",
                    f"- matched_keywords: {matched_keywords}",
                    "```text",
                    str(match.get("excerpt", "")).strip(),
                    "```",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _build_repo_search_context(
        *,
        job: JobRecord,
        query: str,
        keywords: List[str],
        files_scanned: int,
        matches: List[Dict[str, Any]],
    ) -> str:
        lines = [
            "# REPO SEARCH CONTEXT",
            "",
            f"- job_id: {job.job_id}",
            f"- repository: {job.repository}",
            f"- query: {query}",
            f"- keywords: {', '.join(keywords) if keywords else '(none)'}",
            f"- files_scanned: {files_scanned}",
            f"- match_count: {len(matches)}",
            "",
        ]
        if not matches:
            lines.extend(
                [
                    "검색 키워드와 맞는 repo path/content를 찾지 못했습니다.",
                    "필요하면 파일명, 디렉터리명, 함수명, stage 이름을 더 구체적으로 넣어 다시 요청하세요.",
                ]
            )
            return "\n".join(lines).strip() + "\n"

        lines.append("## Matches")
        for index, match in enumerate(matches[:12], start=1):
            matched_keywords = ", ".join(match.get("matched_keywords", []) or []) or "(none)"
            lines.extend(
                [
                    f"### Match {index}",
                    f"- kind: {match.get('kind', '')}",
                    f"- path: {match.get('path', '')}",
                    f"- line_number: {match.get('line_number', '') or '(path match)'}",
                    f"- matched_keywords: {matched_keywords}",
                    "```text",
                    str(match.get("excerpt", "")).strip(),
                    "```",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _build_memory_search_context(
        *,
        job: JobRecord,
        query: str,
        execution_repository: str,
        items: List[Dict[str, Any]],
        source: str,
    ) -> str:
        lines = [
            "# MEMORY SEARCH CONTEXT",
            "",
            f"- job_id: {job.job_id}",
            f"- repository: {job.repository}",
            f"- execution_repository: {execution_repository}",
            f"- app_code: {job.app_code}",
            f"- workflow_id: {job.workflow_id}",
            f"- query: {query}",
            f"- source: {source}",
            f"- match_count: {len(items)}",
            "",
        ]
        if not items:
            lines.extend(
                [
                    "현재 repo/app/workflow 범위에서 query와 맞는 memory entry를 찾지 못했습니다.",
                    "필요하면 issue signature, convention name, failure pattern, strategy 이름을 더 구체적으로 넣어 다시 요청하세요.",
                ]
            )
            return "\n".join(lines).strip() + "\n"

        lines.append("## Matches")
        for index, item in enumerate(items[:8], start=1):
            lines.extend(
                [
                    f"### Match {index}",
                    f"- memory_id: {item.get('memory_id', '')}",
                    f"- memory_type: {item.get('memory_type', '')}",
                    f"- state: {item.get('state', '')}",
                    f"- score: {item.get('score', 0)}",
                    f"- confidence: {item.get('confidence', 0)}",
                    f"- source_path: {item.get('source_path', '')}",
                    f"- title: {item.get('title', '')}",
                    "```text",
                    str(item.get("summary", "")).strip()[:1200],
                    "```",
                ]
            )
        return "\n".join(lines).strip() + "\n"

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
