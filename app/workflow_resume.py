"""Shared helpers for workflow resume decisions and artifact path recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set


RESUME_UNSAFE_NODE_TYPES = {
    "commit_implement",
    "commit_fix",
    "push_branch",
    "create_pr",
}

MANUAL_RESUME_MODES = {
    "full_rerun",
    "resume_failed_node",
    "resume_from_node",
}


def build_workflow_artifact_paths(repository_path: Path) -> Dict[str, Path]:
    """Return the canonical artifact path map used after write_spec."""

    docs_dir = repository_path / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    return {
        "spec": docs_dir / "SPEC.md",
        "spec_json": docs_dir / "SPEC.json",
        "spec_quality": docs_dir / "SPEC_QUALITY.json",
        "plan": docs_dir / "PLAN.md",
        "review": docs_dir / "REVIEW.md",
        "design": docs_dir / "DESIGN_SYSTEM.md",
        "design_tokens": docs_dir / "DESIGN_TOKENS.json",
        "token_handoff": docs_dir / "TOKEN_HANDOFF.md",
        "publish_checklist": docs_dir / "PUBLISH_CHECKLIST.md",
        "publish_handoff": docs_dir / "PUBLISH_HANDOFF.md",
        "copy_plan": docs_dir / "COPYWRITING_PLAN.md",
        "copy_deck": docs_dir / "COPY_DECK.md",
        "documentation_plan": docs_dir / "DOCUMENTATION_PLAN.md",
        "product_brief": docs_dir / "PRODUCT_BRIEF.md",
        "user_flows": docs_dir / "USER_FLOWS.md",
        "mvp_scope": docs_dir / "MVP_SCOPE.md",
        "architecture_plan": docs_dir / "ARCHITECTURE_PLAN.md",
        "scaffold_plan": docs_dir / "SCAFFOLD_PLAN.md",
        "bootstrap_report": docs_dir / "BOOTSTRAP_REPORT.json",
        "product_review": docs_dir / "PRODUCT_REVIEW.json",
        "repo_maturity": docs_dir / "REPO_MATURITY.json",
        "quality_trend": docs_dir / "QUALITY_TREND.json",
        "review_history": docs_dir / "REVIEW_HISTORY.json",
        "improvement_backlog": docs_dir / "IMPROVEMENT_BACKLOG.json",
        "improvement_loop_state": docs_dir / "IMPROVEMENT_LOOP_STATE.json",
        "improvement_plan": docs_dir / "IMPROVEMENT_PLAN.md",
        "next_improvement_tasks": docs_dir / "NEXT_IMPROVEMENT_TASKS.json",
        "memory_log": docs_dir / "MEMORY_LOG.jsonl",
        "decision_history": docs_dir / "DECISION_HISTORY.json",
        "failure_patterns": docs_dir / "FAILURE_PATTERNS.json",
        "conventions": docs_dir / "CONVENTIONS.json",
        "memory_selection": docs_dir / "MEMORY_SELECTION.json",
        "memory_context": docs_dir / "MEMORY_CONTEXT.json",
        "memory_trace": docs_dir / "MEMORY_TRACE.json",
        "memory_feedback": docs_dir / "MEMORY_FEEDBACK.json",
        "memory_rankings": docs_dir / "MEMORY_RANKINGS.json",
        "strategy_shadow_report": docs_dir / "STRATEGY_SHADOW_REPORT.json",
        "stage_contracts": docs_dir / "STAGE_CONTRACTS.md",
        "stage_contracts_json": docs_dir / "STAGE_CONTRACTS.json",
        "pipeline_analysis": docs_dir / "PIPELINE_ANALYSIS.md",
        "pipeline_analysis_json": docs_dir / "PIPELINE_ANALYSIS.json",
        "readme": repository_path / "README.md",
        "copyright": repository_path / "COPYRIGHT.md",
        "development_guide": repository_path / "DEVELOPMENT_GUIDE.md",
        "status": docs_dir / "STATUS.md",
    }


def linearize_workflow_nodes(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return linear execution order from entry node over success/always edges."""

    raw_nodes = workflow.get("nodes", [])
    raw_edges = workflow.get("edges", [])
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return []

    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", "")).strip()
        if node_id:
            nodes_by_id[node_id] = node
    if not nodes_by_id:
        return []

    entry = str(workflow.get("entry_node_id", "")).strip()
    if not entry or entry not in nodes_by_id:
        entry = next(iter(nodes_by_id.keys()))

    adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes_by_id}
    if isinstance(raw_edges, list):
        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("from", "")).strip()
            dst = str(edge.get("to", "")).strip()
            event = str(edge.get("on", "success")).strip()
            if event not in {"success", "always"}:
                continue
            if src in adjacency and dst in nodes_by_id:
                adjacency[src].append(dst)

    reachable: Set[str] = set()
    stack: List[str] = [entry]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        for nxt in adjacency.get(node_id, []):
            if nxt not in reachable:
                stack.append(nxt)

    indegree: Dict[str, int] = {node_id: 0 for node_id in reachable}
    for src, targets in adjacency.items():
        if src not in reachable:
            continue
        for dst in targets:
            if dst in indegree:
                indegree[dst] += 1

    queue: List[str] = sorted([node_id for node_id, degree in indegree.items() if degree == 0])
    ordered_ids: List[str] = []
    while queue:
        current = queue.pop(0)
        ordered_ids.append(current)
        for nxt in adjacency.get(current, []):
            if nxt not in indegree:
                continue
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(ordered_ids) != len(reachable):
        return [nodes_by_id[node_id] for node_id in nodes_by_id]
    return [nodes_by_id[node_id] for node_id in ordered_ids]


def read_improvement_runtime_context(paths: Dict[str, Path]) -> Dict[str, Any]:
    """Read current improvement strategy and next-task summary."""

    loop_state_path = paths.get("improvement_loop_state")
    tasks_path = paths.get("next_improvement_tasks")

    loop_payload: Dict[str, Any] = {}
    tasks_payload: Dict[str, Any] = {}
    if isinstance(loop_state_path, Path) and loop_state_path.exists():
        try:
            loop_payload = json.loads(loop_state_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            loop_payload = {}
    if isinstance(tasks_path, Path) and tasks_path.exists():
        try:
            tasks_payload = json.loads(tasks_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            tasks_payload = {}

    raw_tasks = tasks_payload.get("tasks", []) if isinstance(tasks_payload, dict) else []
    task_titles: List[str] = []
    if isinstance(raw_tasks, list):
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if title:
                task_titles.append(title)

    return {
        "strategy": str(loop_payload.get("strategy", "")).strip() if isinstance(loop_payload, dict) else "",
        "scope_restriction": str(tasks_payload.get("scope_restriction", "")).strip() if isinstance(tasks_payload, dict) else "",
        "task_titles": task_titles,
    }


def compute_workflow_resume_state(
    *,
    workflow_id: str,
    ordered_nodes: List[Dict[str, Any]],
    node_runs: List[Any],
    current_attempt: int,
    strategy: str = "",
    scope_restriction: str = "",
    manual_mode: str = "",
    manual_node_id: str = "",
    manual_note: str = "",
) -> Dict[str, Any]:
    """Return safe resume/full-rerun decision for the current attempt."""

    normalized_attempt = max(1, int(current_attempt or 1))
    base_state: Dict[str, Any] = {
        "enabled": False,
        "mode": "none",
        "reason_code": "no_prior_attempt",
        "reason": "이전 시도 기록이 없어 처음부터 실행합니다.",
        "current_attempt": normalized_attempt,
        "source_attempt": 0,
        "failed_node_id": "",
        "failed_node_type": "",
        "failed_node_title": "",
        "resume_from_node_id": "",
        "resume_from_node_type": "",
        "resume_from_node_title": "",
        "resume_from_index": 0,
        "skipped_nodes": [],
        "override_active": False,
        "override_mode": "",
        "override_note": "",
    }

    normalized_manual_mode = str(manual_mode or "").strip().lower()
    if normalized_manual_mode and normalized_manual_mode not in MANUAL_RESUME_MODES:
        normalized_manual_mode = ""
    normalized_manual_note = str(manual_note or "").strip()

    if normalized_manual_mode == "full_rerun":
        base_state.update(
            {
                "mode": "full_rerun",
                "reason_code": "manual_full_rerun",
                "reason": normalized_manual_note or "운영자가 처음부터 재실행하도록 지정했습니다.",
                "override_active": True,
                "override_mode": normalized_manual_mode,
                "override_note": normalized_manual_note,
            }
        )
        return base_state

    if normalized_attempt <= 1:
        return base_state

    prior_runs = [
        item
        for item in (node_runs or [])
        if int(_read_field(item, "attempt", 0)) < normalized_attempt
    ]
    if not prior_runs:
        return base_state

    latest_attempt = max(int(_read_field(item, "attempt", 0)) for item in prior_runs)
    source_runs = [
        item for item in prior_runs if int(_read_field(item, "attempt", 0)) == latest_attempt
    ]
    source_runs.sort(
        key=lambda item: (
            _read_field(item, "started_at", ""),
            _read_field(item, "node_run_id", ""),
        )
    )
    base_state["source_attempt"] = latest_attempt
    if not source_runs:
        base_state["reason_code"] = "missing_attempt_records"
        base_state["reason"] = "이전 시도 기록이 불완전해 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    normalized_strategy = str(strategy or "").strip().lower()
    normalized_scope = str(scope_restriction or "").strip()
    if normalized_strategy == "design_rebaseline":
        base_state["reason_code"] = "strategy_requires_replan"
        base_state["reason"] = "개선 전략이 설계 재수립을 요구해 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state
    if normalized_scope == "MVP_redefinition":
        base_state["reason_code"] = "scope_requires_replan"
        base_state["reason"] = "MVP 범위 재정의가 필요해 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    current_workflow_id = str(workflow_id or "").strip()
    source_workflow_ids = {
        str(_read_field(item, "workflow_id", "")).strip() for item in source_runs
    }
    source_workflow_ids.discard("")
    if current_workflow_id and source_workflow_ids and source_workflow_ids != {current_workflow_id}:
        base_state["reason_code"] = "workflow_changed"
        base_state["reason"] = "워크플로우 구성이 달라져 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    if not ordered_nodes:
        base_state["reason_code"] = "missing_workflow_nodes"
        base_state["reason"] = "워크플로우 노드가 없어 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    node_index = {
        str(node.get("id", "")).strip(): idx
        for idx, node in enumerate(ordered_nodes)
        if isinstance(node, dict) and str(node.get("id", "")).strip()
    }
    write_spec_index = next(
        (
            idx
            for idx, node in enumerate(ordered_nodes)
            if str(node.get("type", "")).strip() == "write_spec"
        ),
        -1,
    )
    if write_spec_index < 0:
        base_state["reason_code"] = "write_spec_missing"
        base_state["reason"] = "write_spec 노드가 없어 안전 재개를 사용하지 않습니다."
        base_state["mode"] = "full_rerun"
        return base_state

    has_write_spec_success = any(
        str(_read_field(item, "node_type", "")).strip() == "write_spec"
        and str(_read_field(item, "status", "")).strip().lower() == "success"
        for item in source_runs
    )
    if not has_write_spec_success:
        base_state["reason_code"] = "paths_context_missing"
        base_state["reason"] = "SPEC 산출물이 확정되지 않아 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    last_run = source_runs[-1]
    last_status = str(_read_field(last_run, "status", "")).strip().lower()
    if last_status == "success":
        base_state["reason_code"] = "previous_attempt_completed"
        base_state["reason"] = "이전 시도가 완료되어 다음 라운드는 처음부터 실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state
    if last_status not in {"failed", "running"}:
        base_state["reason_code"] = "unsupported_terminal_status"
        base_state["reason"] = "이전 시도 종료 상태를 해석할 수 없어 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    failed_node_id = str(_read_field(last_run, "node_id", "")).strip()
    failed_node_type = str(_read_field(last_run, "node_type", "")).strip()
    failed_node_title = str(_read_field(last_run, "node_title", "")).strip()
    base_state["failed_node_id"] = failed_node_id
    base_state["failed_node_type"] = failed_node_type
    base_state["failed_node_title"] = failed_node_title

    if normalized_manual_mode in {"resume_failed_node", "resume_from_node"}:
        target_node_id = failed_node_id if normalized_manual_mode == "resume_failed_node" else str(manual_node_id or "").strip()
        manual_target = validate_manual_resume_target(
            ordered_nodes=ordered_nodes,
            node_id=target_node_id,
        )
        if manual_target.get("valid"):
            resume_index = int(manual_target.get("node_index", 0) or 0)
            resume_node = ordered_nodes[resume_index]
            skipped_nodes = [
                {
                    "id": str(node.get("id", "")).strip(),
                    "type": str(node.get("type", "")).strip(),
                    "title": str(node.get("title", "")).strip(),
                }
                for node in ordered_nodes[:resume_index]
                if isinstance(node, dict)
            ]
            base_state.update(
                {
                    "enabled": True,
                    "mode": "resume",
                    "reason_code": "manual_resume_from_failed_node"
                    if normalized_manual_mode == "resume_failed_node"
                    else "manual_resume_from_selected_node",
                    "reason": normalized_manual_note
                    or (
                        "운영자가 마지막 실패 노드부터 재개하도록 지정했습니다."
                        if normalized_manual_mode == "resume_failed_node"
                        else "운영자가 선택한 노드부터 재개하도록 지정했습니다."
                    ),
                    "resume_from_node_id": str(resume_node.get("id", "")).strip(),
                    "resume_from_node_type": str(resume_node.get("type", "")).strip(),
                    "resume_from_node_title": str(resume_node.get("title", "")).strip(),
                    "resume_from_index": resume_index,
                    "skipped_nodes": skipped_nodes,
                    "override_active": True,
                    "override_mode": normalized_manual_mode,
                    "override_note": normalized_manual_note,
                }
            )
            return base_state
        base_state.update(
            {
                "mode": "full_rerun",
                "reason_code": str(manual_target.get("reason_code", "manual_target_invalid")),
                "reason": normalized_manual_note
                or str(manual_target.get("reason", "선택한 노드가 안전 재개 대상이 아니어서 전체 재실행합니다.")),
                "override_active": True,
                "override_mode": normalized_manual_mode,
                "override_note": normalized_manual_note,
            }
        )
        return base_state

    failed_index = node_index.get(failed_node_id, -1)
    if failed_index < 0:
        base_state["reason_code"] = "failed_node_missing_from_workflow"
        base_state["reason"] = "실패 노드가 현재 워크플로우에 없어 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state
    if failed_index <= write_spec_index:
        base_state["reason_code"] = "failed_before_paths_ready"
        base_state["reason"] = "설계 산출물 준비 전 실패라 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state
    if failed_node_type in RESUME_UNSAFE_NODE_TYPES:
        base_state["reason_code"] = "failed_on_side_effect_node"
        base_state["reason"] = "부작용이 있는 노드에서 실패해 전체 재실행합니다."
        base_state["mode"] = "full_rerun"
        return base_state

    resume_node = ordered_nodes[failed_index]
    skipped_nodes = [
        {
            "id": str(node.get("id", "")).strip(),
            "type": str(node.get("type", "")).strip(),
            "title": str(node.get("title", "")).strip(),
        }
        for node in ordered_nodes[:failed_index]
        if isinstance(node, dict)
    ]

    base_state.update(
        {
            "enabled": True,
            "mode": "resume",
            "reason_code": "resume_from_failed_node",
            "reason": "이전 시도에서 실패한 노드부터 안전하게 재개합니다.",
            "resume_from_node_id": str(resume_node.get("id", "")).strip(),
            "resume_from_node_type": str(resume_node.get("type", "")).strip(),
            "resume_from_node_title": str(resume_node.get("title", "")).strip(),
            "resume_from_index": failed_index,
            "skipped_nodes": skipped_nodes,
        }
    )
    return base_state


def _read_field(item: Any, name: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def list_manual_resume_candidates(ordered_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return safe manual resume targets after write_spec."""

    write_spec_index = next(
        (
            idx
            for idx, node in enumerate(ordered_nodes)
            if str(node.get("type", "")).strip() == "write_spec"
        ),
        -1,
    )
    if write_spec_index < 0:
        return []

    candidates: List[Dict[str, Any]] = []
    for idx, node in enumerate(ordered_nodes):
        node_id = str(node.get("id", "")).strip()
        node_type = str(node.get("type", "")).strip()
        if not node_id or idx <= write_spec_index or node_type in RESUME_UNSAFE_NODE_TYPES:
            continue
        candidates.append(
            {
                "id": node_id,
                "type": node_type,
                "title": str(node.get("title", "")).strip(),
                "index": idx,
            }
        )
    return candidates


def validate_manual_resume_target(
    *,
    ordered_nodes: List[Dict[str, Any]],
    node_id: str,
) -> Dict[str, Any]:
    """Validate whether one workflow node can be used as a manual resume target."""

    normalized_node_id = str(node_id or "").strip()
    if not normalized_node_id:
        return {
            "valid": False,
            "reason_code": "manual_target_missing",
            "reason": "재개할 노드를 선택해야 합니다.",
        }

    write_spec_index = next(
        (
            idx
            for idx, node in enumerate(ordered_nodes)
            if str(node.get("type", "")).strip() == "write_spec"
        ),
        -1,
    )
    if write_spec_index < 0:
        return {
            "valid": False,
            "reason_code": "write_spec_missing",
            "reason": "write_spec 노드가 없어 수동 재개를 사용할 수 없습니다.",
        }

    for idx, node in enumerate(ordered_nodes):
        current_id = str(node.get("id", "")).strip()
        if current_id != normalized_node_id:
            continue
        node_type = str(node.get("type", "")).strip()
        if idx <= write_spec_index:
            return {
                "valid": False,
                "reason_code": "manual_target_before_paths_ready",
                "reason": "write_spec 이전 노드에서는 수동 재개를 허용하지 않습니다.",
                "node_index": idx,
                "node_type": node_type,
                "node_title": str(node.get("title", "")).strip(),
            }
        if node_type in RESUME_UNSAFE_NODE_TYPES:
            return {
                "valid": False,
                "reason_code": "manual_target_side_effect_node",
                "reason": "부작용이 있는 노드는 수동 재개 대상이 아닙니다. 처음부터 재실행하세요.",
                "node_index": idx,
                "node_type": node_type,
                "node_title": str(node.get("title", "")).strip(),
            }
        return {
            "valid": True,
            "reason_code": "manual_target_valid",
            "reason": "수동 재개 가능한 노드입니다.",
            "node_index": idx,
            "node_type": node_type,
            "node_title": str(node.get("title", "")).strip(),
        }

    return {
        "valid": False,
        "reason_code": "manual_target_not_found",
        "reason": "선택한 노드가 현재 워크플로우에 없습니다.",
    }
