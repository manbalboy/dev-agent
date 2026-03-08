"""Workflow-node design helpers for phase-1 DAG configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


SUPPORTED_NODE_TYPES: Dict[str, Dict[str, Any]] = {
    "gh_read_issue": {"label": "GitHub 이슈 읽기", "kind": "io"},
    "agent_task": {"label": "Agent 작업", "kind": "ai"},
    "planner_task": {"label": "플래너 역할 작업", "kind": "ai"},
    "designer_task": {"label": "디자이너 역할 작업", "kind": "ai"},
    "coder_task": {"label": "코더 역할 작업", "kind": "ai"},
    "tester_task": {"label": "테스터 역할 작업", "kind": "qa"},
    "reviewer_task": {"label": "리뷰어 역할 작업", "kind": "ai"},
    "escalator_task": {"label": "중재자 역할 작업", "kind": "ai"},
    "if_label_match": {"label": "라벨 조건 분기(IF)", "kind": "control"},
    "loop_until_pass": {"label": "반복 루프(Loop)", "kind": "control"},
    "write_spec": {"label": "SPEC 작성", "kind": "transform"},
    "gemini_plan": {"label": "Gemini 계획", "kind": "ai"},
    "publisher_task": {"label": "퍼블리셔 작업", "kind": "ai"},
    "copywriter_task": {"label": "카피라이터 작업", "kind": "ai"},
    "documentation_task": {"label": "기술 문서 작성", "kind": "ai"},
    "codex_implement": {"label": "Codex 구현", "kind": "ai"},
    "code_change_summary": {"label": "코드 변경 요약", "kind": "transform"},
    "test_after_implement": {"label": "테스트(구현 후)", "kind": "qa"},
    "tester_run_e2e": {"label": "테스터 E2E/타입별 검증", "kind": "qa"},
    "ux_e2e_review": {"label": "UX E2E 검수", "kind": "qa"},
    "tester_retest_e2e": {"label": "테스터 E2E/타입별 재검증", "kind": "qa"},
    "coder_fix_from_test_report": {"label": "코더 테스트 리포트 기반 수정", "kind": "ai"},
    "commit_implement": {"label": "커밋(구현)", "kind": "git"},
    "gemini_review": {"label": "Gemini 리뷰", "kind": "ai"},
    "claude_escalation": {"label": "Claude 에스컬레이션", "kind": "ai"},
    "codex_fix": {"label": "Codex 수정", "kind": "ai"},
    "test_after_fix": {"label": "테스트(수정 후)", "kind": "qa"},
    "test_after_fix_final": {"label": "테스트(최종 수정 후)", "kind": "qa"},
    "commit_fix": {"label": "커밋(수정)", "kind": "git"},
    "push_branch": {"label": "브랜치 푸시", "kind": "git"},
    "create_pr": {"label": "PR 생성", "kind": "git"},
}


def default_workflow_template() -> Dict[str, Any]:
    """Return a default workflow template equivalent to fixed orchestration."""

    return {
        "workflow_id": "default_design_first_v4",
        "name": "Default Design First V4",
        "description": "기본 확장 플로우: 큰틀 플랜 -> 디자인 기획 -> 퍼블리싱 -> 카피라이팅 -> 개발기획 -> 코딩 -> PR 전 문서화",
        "version": 4,
        "entry_node_id": "n1",
        "nodes": [
            {"id": "n1", "type": "gh_read_issue", "title": "이슈 읽기"},
            {"id": "n2", "type": "write_spec", "title": "SPEC 작성"},
            {"id": "n3", "type": "gemini_plan", "title": "큰틀 플랜"},
            {"id": "n4", "type": "designer_task", "title": "디자인 시스템 기획"},
            {"id": "n5", "type": "publisher_task", "title": "퍼블리싱(디자인 시스템 반영)"},
            {"id": "n6", "type": "copywriter_task", "title": "카피라이팅(고객 문구 기획/작성)"},
            {"id": "n7", "type": "gemini_plan", "title": "개발 기획(기술/라이브러리 확정)"},
            {"id": "n8", "type": "codex_implement", "title": "코딩(기능 구현)"},
            {"id": "n9", "type": "code_change_summary", "title": "코드 변경 요약"},
            {"id": "n10", "type": "test_after_implement", "title": "1차 기능 테스트"},
            {"id": "n11", "type": "tester_run_e2e", "title": "1차 E2E/타입별 테스트"},
            {"id": "n12", "type": "ux_e2e_review", "title": "UX E2E 검수(PC/모바일 스샷)"},
            {"id": "n13", "type": "coder_fix_from_test_report", "title": "UX/E2E 실패 우선 수정"},
            {"id": "n14", "type": "tester_run_e2e", "title": "수정 + E2E/타입별 루프(최대 3회)"},
            {"id": "n15", "type": "gemini_review", "title": "리뷰어 점검"},
            {"id": "n16", "type": "gemini_plan", "title": "리뷰 반영 고도화 플랜"},
            {"id": "n17", "type": "coder_fix_from_test_report", "title": "고도화 반영 구현"},
            {"id": "n18", "type": "tester_retest_e2e", "title": "고도화 후 E2E/타입별 재테스트"},
            {"id": "n19", "type": "gemini_review", "title": "최종 리뷰 게이트"},
            {"id": "n20", "type": "commit_fix", "title": "최종 커밋"},
            {"id": "n21", "type": "documentation_task", "title": "기술 문서 작성(README/저작권/개발가이드)"},
            {"id": "n22", "type": "push_branch", "title": "브랜치 푸시"},
            {"id": "n23", "type": "create_pr", "title": "PR 생성"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "on": "success"},
            {"from": "n2", "to": "n3", "on": "success"},
            {"from": "n3", "to": "n4", "on": "success"},
            {"from": "n4", "to": "n5", "on": "success"},
            {"from": "n5", "to": "n6", "on": "success"},
            {"from": "n6", "to": "n7", "on": "success"},
            {"from": "n7", "to": "n8", "on": "success"},
            {"from": "n8", "to": "n9", "on": "success"},
            {"from": "n9", "to": "n10", "on": "success"},
            {"from": "n10", "to": "n11", "on": "success"},
            {"from": "n11", "to": "n12", "on": "success"},
            {"from": "n12", "to": "n13", "on": "success"},
            {"from": "n13", "to": "n14", "on": "success"},
            {"from": "n14", "to": "n15", "on": "success"},
            {"from": "n15", "to": "n16", "on": "success"},
            {"from": "n16", "to": "n17", "on": "success"},
            {"from": "n17", "to": "n18", "on": "success"},
            {"from": "n18", "to": "n19", "on": "success"},
            {"from": "n19", "to": "n20", "on": "success"},
            {"from": "n20", "to": "n21", "on": "success"},
            {"from": "n21", "to": "n22", "on": "success"},
            {"from": "n22", "to": "n23", "on": "success"},
        ],
    }


def schema_payload() -> Dict[str, Any]:
    """Return phase-1 schema metadata for dashboard/editor."""

    return {
        "phase": "phase-1",
        "supported_edge_events": ["success", "failure", "always"],
        "node_types": SUPPORTED_NODE_TYPES,
        "notes": [
            "phase-1은 저장/검증 중심이며, 실행엔진 전환은 다음 단계에서 연결",
            "노드 type은 사전 정의 목록만 허용",
            "if_label_match/loop_until_pass 노드는 분기/반복 설계용",
        ],
    }


def load_workflows(path: Path) -> Dict[str, Any]:
    """Load workflow config from JSON with safe fallback."""

    if not path.exists():
        defaults = {"default_workflow_id": "default_design_first_v4", "workflows": [default_workflow_template()]}
        save_workflows(path, defaults)
        return defaults

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}

    if not isinstance(loaded, dict):
        loaded = {}
    workflows = loaded.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        loaded["workflows"] = [default_workflow_template()]
    if not isinstance(loaded.get("default_workflow_id"), str):
        loaded["default_workflow_id"] = "default_design_first_v4"
    return loaded


def save_workflows(path: Path, payload: Dict[str, Any]) -> None:
    """Persist workflow config JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_workflow(workflow: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate phase-1 workflow definition and return errors."""

    errors: List[str] = []
    workflow_id = str(workflow.get("workflow_id", "")).strip()
    if not workflow_id:
        errors.append("workflow_id is required")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        return False, errors

    edges = workflow.get("edges")
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        return False, errors

    node_ids: List[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("node must be object")
            continue
        node_id = str(node.get("id", "")).strip()
        node_type = str(node.get("type", "")).strip()
        if not node_id:
            errors.append("node.id is required")
            continue
        node_ids.append(node_id)
        if node_type not in SUPPORTED_NODE_TYPES:
            errors.append(f"unsupported node.type: {node_type}")

    duplicate_ids = {nid for nid in node_ids if node_ids.count(nid) > 1}
    for duplicate in sorted(duplicate_ids):
        errors.append(f"duplicate node.id: {duplicate}")

    node_id_set = set(node_ids)
    graph: Dict[str, List[str]] = {nid: [] for nid in node_id_set}
    indegree: Dict[str, int] = {nid: 0 for nid in node_id_set}

    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("edge must be object")
            continue
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        event = str(edge.get("on", "success")).strip()
        if src not in node_id_set:
            errors.append(f"edge.from not found: {src}")
            continue
        if dst not in node_id_set:
            errors.append(f"edge.to not found: {dst}")
            continue
        if event not in {"success", "failure", "always"}:
            errors.append(f"unsupported edge event: {event}")
            continue
        graph[src].append(dst)
        indegree[dst] += 1

    entry_node_id = str(workflow.get("entry_node_id", "")).strip()
    if entry_node_id and entry_node_id not in node_id_set:
        errors.append(f"entry_node_id not found: {entry_node_id}")

    if node_id_set:
        queue = [nid for nid, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            current = queue.pop(0)
            visited += 1
            for nxt in graph.get(current, []):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if visited != len(node_id_set):
            errors.append("workflow graph has cycle(s)")

    return len(errors) == 0, errors
