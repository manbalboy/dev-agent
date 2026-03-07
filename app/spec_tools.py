"""Specification-stage helper tools for AgentHub orchestration.

These tools make the spec pipeline less brittle by combining:
- deterministic schema checks
- light auto-rewrite for common failures
- repository context extraction
- optional evidence search wrapper
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple


_VAGUE_PATTERNS = [
    "적당",
    "알아서",
    "대충",
    "빠르게",
    "좋게",
    "잘",
]


def issue_reader(
    *,
    issue_title: str,
    issue_body: str,
    issue_url: str,
) -> Dict[str, Any]:
    """Extract normalized issue context for spec generation."""

    lines = _extract_lines(issue_body)
    keywords = _extract_keywords(issue_title, issue_body)
    return {
        "title": issue_title.strip(),
        "url": issue_url.strip(),
        "raw_body": issue_body.strip(),
        "lines": lines,
        "keywords": keywords,
        "line_count": len(lines),
    }


def spec_schema_validator(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate SPEC.json and return score + reject codes."""

    score = 100
    findings: List[Dict[str, str]] = []
    reject_codes: List[str] = []

    required = [
        "goal",
        "scope_in",
        "scope_out",
        "acceptance_criteria",
        "constraints",
        "validation",
    ]
    for key in required:
        if key not in spec:
            findings.append({"code": "MISSING_FIELD", "detail": key})
            reject_codes.append("MISSING_FIELD")
            score -= 20

    goal = str(spec.get("goal", "")).strip()
    if not goal:
        findings.append({"code": "MISSING_GOAL", "detail": "goal is empty"})
        reject_codes.append("MISSING_GOAL")
        score -= 25
    elif _contains_vague(goal):
        findings.append({"code": "VAGUE_GOAL", "detail": goal})
        reject_codes.append("VAGUE_GOAL")
        score -= 12

    scope_in = _to_string_list(spec.get("scope_in", []))
    scope_out = _to_string_list(spec.get("scope_out", []))
    ac = _to_string_list(spec.get("acceptance_criteria", []))

    if not scope_in:
        findings.append({"code": "MISSING_SCOPE_IN", "detail": "scope_in is empty"})
        reject_codes.append("MISSING_SCOPE_IN")
        score -= 20
    if not scope_out:
        findings.append({"code": "MISSING_SCOPE_OUT", "detail": "scope_out is empty"})
        reject_codes.append("MISSING_SCOPE_OUT")
        score -= 15
    if len(ac) < 3:
        findings.append({"code": "MISSING_AC", "detail": f"acceptance_criteria={len(ac)}"})
        reject_codes.append("MISSING_AC")
        score -= 18

    overlap = _overlap_items(scope_in, scope_out)
    if overlap:
        findings.append({"code": "SCOPE_CONFLICT", "detail": ", ".join(overlap[:3])})
        reject_codes.append("SCOPE_CONFLICT")
        score -= 18

    if _contains_vague(" ".join(scope_in[:5])):
        findings.append({"code": "VAGUE_SCOPE", "detail": "scope_in contains vague expressions"})
        reject_codes.append("VAGUE_SCOPE")
        score -= 8

    score = max(0, score)
    passed = score >= 80 and not any(code.startswith("MISSING_") for code in reject_codes)
    return {
        "passed": passed,
        "score": score,
        "reject_codes": sorted(set(reject_codes)),
        "findings": findings,
    }


def spec_rewriter(spec: Dict[str, Any], validation: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Patch common validation failures without rewriting everything."""

    patched = dict(spec)
    actions: List[str] = []
    reject_codes = set(validation.get("reject_codes", []))

    goal = str(patched.get("goal", "")).strip()
    scope_in = _to_string_list(patched.get("scope_in", []))
    scope_out = _to_string_list(patched.get("scope_out", []))
    ac = _to_string_list(patched.get("acceptance_criteria", []))
    constraints = _to_string_list(patched.get("constraints", []))

    if "MISSING_GOAL" in reject_codes or "VAGUE_GOAL" in reject_codes:
        if scope_in:
            patched["goal"] = scope_in[0]
        else:
            patched["goal"] = "핵심 기능 구현 목표 정의 필요"
        actions.append("goal_patched")

    if "MISSING_SCOPE_IN" in reject_codes:
        raw_request = str(patched.get("raw_request", "")).strip()
        lines = _extract_lines(raw_request)
        patched["scope_in"] = lines[:5] if lines else ["요청 본문 기반 구현 범위 정리 필요"]
        actions.append("scope_in_patched")

    if "MISSING_SCOPE_OUT" in reject_codes:
        patched["scope_out"] = [
            "요청 본문에 없는 신규 대기능 추가",
            "전체 구조 재작성",
            "자동 머지/자동 배포",
        ]
        actions.append("scope_out_patched")

    if "MISSING_AC" in reject_codes:
        current_goal = str(patched.get("goal", "")).strip() or "핵심 기능"
        patched["acceptance_criteria"] = [
            f"{current_goal} 동작이 재현 가능해야 한다.",
            "실행/검증 방법이 문서에 포함되어야 한다.",
            "범위(in/out)가 명확히 구분되어야 한다.",
        ]
        actions.append("acceptance_patched")

    if "SCOPE_CONFLICT" in reject_codes:
        in_norm = {_normalize_text(item) for item in _to_string_list(patched.get("scope_in", []))}
        filtered_out = [
            item
            for item in _to_string_list(patched.get("scope_out", []))
            if _normalize_text(item) not in in_norm
        ]
        patched["scope_out"] = filtered_out
        actions.append("scope_conflict_resolved")

    if "priority_order" not in patched:
        patched["priority_order"] = ["SPEC.json", "SPEC.md", "issue_body"]
        actions.append("priority_order_added")

    if not constraints:
        patched["constraints"] = [
            "우선순위 규칙: SPEC.json > SPEC.md > issue 원문",
            "MVP 범위 우선",
        ]
        actions.append("constraints_patched")

    return patched, actions


def repo_context_reader(repository_path: Path) -> Dict[str, Any]:
    """Read lightweight repository context for spec owner decisions."""

    context: Dict[str, Any] = {"exists": repository_path.exists()}
    if not repository_path.exists():
        context["stack"] = []
        context["readme_excerpt"] = ""
        return context

    stack: List[str] = []
    if (repository_path / "package.json").exists():
        stack.append("node")
    if (repository_path / "requirements.txt").exists() or (repository_path / "pyproject.toml").exists():
        stack.append("python")
    if (repository_path / "android").exists() or (repository_path / "ios").exists():
        stack.append("mobile")
    if (repository_path / "next.config.js").exists() or (repository_path / "next.config.ts").exists():
        stack.append("nextjs")

    readme_path = repository_path / "README.md"
    excerpt = ""
    if readme_path.exists():
        try:
            rows = readme_path.read_text(encoding="utf-8", errors="replace").splitlines()
            excerpt = "\n".join(rows[:40]).strip()
        except OSError:
            excerpt = ""

    return {
        "exists": True,
        "stack": stack,
        "readme_excerpt": excerpt,
    }


def risk_policy_checker(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Detect risky intent hints from spec text."""

    text = " ".join(
        [
            str(spec.get("goal", "")),
            " ".join(_to_string_list(spec.get("scope_in", []))),
            str(spec.get("raw_request", "")),
        ]
    ).lower()
    risks = []
    if "rm -rf" in text or "reset --hard" in text:
        risks.append({"level": "high", "code": "DESTRUCTIVE_HINT"})
    if "token" in text or "secret" in text or "credential" in text:
        risks.append({"level": "medium", "code": "SENSITIVE_DATA_RISK"})
    if len(_to_string_list(spec.get("scope_out", []))) == 0:
        risks.append({"level": "medium", "code": "SCOPE_OUT_EMPTY"})
    return {"risks": risks, "risk_count": len(risks)}


def evidence_search(
    *,
    query: str,
    work_dir: Path,
    api_key: str,
) -> Dict[str, Any]:
    """Run optional search tool wrapper and return output paths/status."""

    if not query.strip() or not api_key.strip():
        return {"executed": False, "reason": "query_or_api_key_missing"}
    docs_dir = work_dir / "_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    json_out = docs_dir / "SEARCH_RESULT.json"
    md_out = docs_dir / "SEARCH_CONTEXT.md"
    command = [
        "python3",
        str(Path.cwd() / "scripts" / "search_research_tool.py"),
        "--query",
        query.strip(),
        "--api-key",
        api_key.strip(),
        "--json-out",
        str(json_out),
        "--md-out",
        str(md_out),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "executed": True,
        "exit_code": int(result.returncode),
        "json_out": str(json_out),
        "md_out": str(md_out),
        "stdout": (result.stdout or "")[:500],
        "stderr": (result.stderr or "")[:500],
    }


def diff_snapshot(repository_path: Path, max_files: int = 24, max_bytes: int = 200_000) -> List[Dict[str, Any]]:
    """Capture current changed files as point-in-time snapshot."""

    cmd = ["git", "-C", str(repository_path), "status", "--porcelain"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    snapshots: List[Dict[str, Any]] = []
    seen = set()
    for raw in lines:
        if len(snapshots) >= max_files:
            break
        status = raw[:2].strip()
        path = _parse_porcelain_path(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        abs_path = (repository_path / path).resolve()
        if repository_path.resolve() not in abs_path.parents and abs_path != repository_path.resolve():
            continue
        item: Dict[str, Any] = {
            "path": path,
            "status": status or "??",
            "exists": abs_path.exists() and abs_path.is_file(),
            "truncated": False,
            "binary": False,
            "content": "",
        }
        if not item["exists"]:
            snapshots.append(item)
            continue
        try:
            blob = abs_path.read_bytes()
        except OSError:
            snapshots.append(item)
            continue
        if b"\x00" in blob:
            item["binary"] = True
            snapshots.append(item)
            continue
        if len(blob) > max_bytes:
            blob = blob[:max_bytes]
            item["truncated"] = True
        item["content"] = blob.decode("utf-8", errors="replace")
        snapshots.append(item)
    return snapshots


def _to_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_lines(body: str) -> List[str]:
    body = _sanitize_issue_body(body)
    lines: List[str] = []
    for raw in (body or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        text = re.sub(r"^[-*]\s+", "", text)
        text = re.sub(r"^\d+\.\s+", "", text)
        if text in {"---", "----", "```"}:
            continue
        lines.append(text)
    return lines


def _sanitize_issue_body(body: str) -> str:
    """Remove orchestration metadata blocks from free-form issue body."""

    cleaned: List[str] = []
    in_role_preset = False
    for raw in (body or "").splitlines():
        text = raw.strip()
        if re.match(r"^##\s*ROLE\s*PRESET\s*$", text, flags=re.IGNORECASE):
            in_role_preset = True
            continue
        if re.match(r"^-\s*##\s*ROLE\s*PRESET\s*$", text, flags=re.IGNORECASE):
            in_role_preset = True
            continue

        if in_role_preset:
            if not text:
                continue
            if re.match(r"^-\s*preset_id\s*:", text, flags=re.IGNORECASE):
                continue
            if re.match(r"^-\s*roles\s*:", text, flags=re.IGNORECASE):
                continue
            if text.startswith("## "):
                in_role_preset = False
            else:
                in_role_preset = False

        if re.match(r"^-\s*preset_id\s*:", text, flags=re.IGNORECASE):
            continue
        if re.match(r"^-\s*roles\s*:", text, flags=re.IGNORECASE):
            continue
        cleaned.append(raw)
    return "\n".join(cleaned)


def _extract_keywords(*values: str) -> List[str]:
    corpus = " ".join(values).lower()
    tokens = re.findall(r"[a-z0-9가-힣]{2,}", corpus)
    skip = {"issue", "spec", "json", "md", "and", "the", "요청", "작업"}
    uniq: List[str] = []
    for token in tokens:
        if token in skip:
            continue
        if token not in uniq:
            uniq.append(token)
        if len(uniq) >= 12:
            break
    return uniq


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _overlap_items(one: List[str], two: List[str]) -> List[str]:
    one_map = {_normalize_text(item): item for item in one}
    two_map = {_normalize_text(item): item for item in two}
    keys = [key for key in one_map if key and key in two_map]
    return [one_map[key] for key in keys]


def _contains_vague(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(token in lowered for token in _VAGUE_PATTERNS)


def _parse_porcelain_path(raw_line: str) -> str:
    line = str(raw_line or "").rstrip()
    if len(line) < 4:
        return ""
    payload = line[3:].strip()
    if not payload:
        return ""
    if " -> " in payload:
        payload = payload.split(" -> ", 1)[1].strip()
    return payload
