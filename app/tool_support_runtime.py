"""Shared helper runtime for tool fallback and scoped memory search."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List


class ToolSupportRuntime:
    """Provide orchestrator-owned helpers used by the shared tool runtime."""

    def __init__(
        self,
        *,
        get_memory_runtime_store: Callable[[], Any],
        utc_now_iso: Callable[[], str],
        get_qdrant_shadow_transport: Callable[[], Any],
        repo_context_reader: Callable[[Path], Dict[str, Any]],
    ) -> None:
        self.get_memory_runtime_store = get_memory_runtime_store
        self.utc_now_iso = utc_now_iso
        self.get_qdrant_shadow_transport = get_qdrant_shadow_transport
        self.repo_context_reader = repo_context_reader

    def search_memory_entries_for_tool(
        self,
        *,
        query: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Expose scoped memory search to the shared tool runtime."""

        runtime_store = self.get_memory_runtime_store()
        runtime_store.refresh_rankings(as_of=self.utc_now_iso())
        return runtime_store.search_entries(
            query=query,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
        )

    def search_vector_memory_entries_for_tool(
        self,
        *,
        query: str,
        repository: str,
        execution_repository: str,
        app_code: str,
        workflow_id: str,
        limit: int,
    ) -> Dict[str, Any]:
        """Expose optional vector-backed memory search for the tool runtime."""

        result = self.get_qdrant_shadow_transport().query_memory_entries(
            query=query,
            repository=repository,
            execution_repository=execution_repository,
            app_code=app_code,
            workflow_id=workflow_id,
            limit=limit,
            score_threshold=0.15,
        )
        return result.to_dict()

    def build_local_evidence_fallback(
        self,
        repository_path: Path,
        paths: Dict[str, Path],
        query: str,
        error_text: str,
    ) -> Dict[str, str]:
        """Create fallback evidence payload when external search is unavailable."""

        repo_context = self.repo_context_reader(repository_path)
        spec_excerpt = ""
        spec_path = paths.get("spec")
        if spec_path and Path(spec_path).exists():
            spec_excerpt = Path(spec_path).read_text(encoding="utf-8", errors="replace")
            spec_excerpt = "\n".join(spec_excerpt.splitlines()[:80]).strip()

        readme_excerpt = str(repo_context.get("readme_excerpt", "")).strip()
        stack = ", ".join(repo_context.get("stack", []) or [])
        context_text = (
            "# SEARCH CONTEXT (Fallback Local Evidence)\n\n"
            f"- query: {query}\n"
            "- mode: fallback_local\n"
            f"- reason: external_search_unavailable ({error_text[:400]})\n"
            f"- detected_stack: {stack or '(none)'}\n\n"
            "## SPEC excerpt\n\n"
            f"{spec_excerpt or '(SPEC excerpt unavailable)'}\n\n"
            "## README excerpt\n\n"
            f"{readme_excerpt or '(README excerpt unavailable)'}\n"
        ).strip() + "\n"
        return {"context_text": context_text}
